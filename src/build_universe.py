"""Phase 1 of Dynamic Scenario Editor: /build-universe endpoint.

Runs waterfall + suppression + baseline rollup for a campaign, then returns
the universe dataset as JSON to the browser. NO budget fitting, NO output
files, NO Drive/Sheets writes beyond the Universe tab persistence.

The browser receives ~70K rows × ~15 fields (~10-15 MB JSON) and becomes
the scenario editing engine — instant recalculation on every edit.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime

import pandas as pd
import numpy as np

from bq_reader import check_cache_freshness, fetch_accounts_from_bq
from salesforce_client import (
    connect_salesforce, fetch_accounts, fetch_opportunities,
    fetch_opportunities_cbnc, probe_sustainer_field,
)
from rfm_engine import compute_rfm
from lifecycle import compute_lifecycle
from cbnc import detect_cbnc
from waterfall_engine import run_waterfall, build_segment_summary
from suppression_engine import apply_tier2_suppression
from sheets_client import get_sheets_client, read_campaign_calendar
from baseline_rollup import build_baseline_rollup
from historical_baseline import fetch_baseline_for_type
from config import MIC_SHEET_ID

logger = logging.getLogger(__name__)

UNIVERSE_TAB = "Universe"

# Fields exported to the browser per donor. Kept minimal to stay under 15MB.
UNIVERSE_FIELDS = [
    "account_id",      # SF Account ID
    "constituent_id",  # Donor ID (9-digit)
    "segment_code",    # HRI segment (AH01, ML01, etc.)
    "rfm_score",       # Weighted RFM score (for intra-segment ranking)
    "rfm_code",        # R1F1M1 composite
    "months_since_last_gift",
    "cumulative_giving",
    "avg_gift_5yr",
    "last_gift_amount",
    "gifts_12mo",
    "lifecycle_stage",
    "suppression_reason",   # Tier 1/2 flag (empty if clean)
    "is_cbnc",
    "has_dm_gift_500",
]


def build_universe(toggles=None, baseline_appeal_code=None,
                   baseline_type=None, campaign_config=None):
    """Run waterfall + suppression + baseline → universe dataset.

    Args:
        toggles: waterfall/suppression toggle overrides.
        baseline_appeal_code: specific prior campaign as baseline (legacy path).
        baseline_type: campaign-type baseline from sf_cache.historical_baseline
            (e.g., "Shipping", "Tax Receipt", "Overall"). Preferred over
            baseline_appeal_code when both are supplied — the type-based
            baseline is the new default.
        campaign_config: dict with campaign_name, appeal_code, target_qty, etc.

    Returns:
        dict with:
            - donors: list of donor dicts (~70K)
            - segments: per-segment aggregates (qty, hist_rr, hist_avg_gift, cpp,
              plus baseline_confidence per segment)
            - campaign: campaign metadata
            - baseline: mode + identifier + per-segment confidence
            - meta: timings, counts, source
    """
    timings = {}
    t_start = time.time()
    campaign_config = campaign_config or {}

    def _elapsed():
        return round(time.time() - t_start, 1)

    logger.info("=" * 60)
    logger.info("BUILD UNIVERSE — scenario editor Phase 1")
    logger.info("=" * 60)

    # --- Step 1: Load accounts (BQ cache preferred) ---
    t0 = time.time()
    cache_fresh, _, _ = check_cache_freshness()
    if cache_fresh:
        data_source = "bigquery"
        accounts_df = fetch_accounts_from_bq()
        if "is_cbnc" in accounts_df.columns:
            cbnc_ids = set(accounts_df.loc[accounts_df["is_cbnc"] == True, "Id"])
        else:
            cbnc_ids = set()
        opps_df = pd.DataFrame()
    else:
        data_source = "salesforce_live"
        logger.info("BQ cache stale — falling back to live SF (~14 min)")
        sf = connect_salesforce()
        probe_sustainer_field(sf)
        accounts_df = fetch_accounts(sf)
        opps_df = fetch_opportunities(sf)
        cbnc_opps_df = fetch_opportunities_cbnc(sf)
        cbnc_ids = detect_cbnc(cbnc_opps_df)
    timings["load_data"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Loaded {len(accounts_df):,} accounts from {data_source}")

    # --- Step 2: RFM + lifecycle ---
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    timings["rfm"] = round(time.time() - t0, 1)

    t0 = time.time()
    lifecycle = compute_lifecycle(accounts_df)
    timings["lifecycle"] = round(time.time() - t0, 1)

    # --- Step 3: Waterfall assignment ---
    t0 = time.time()
    waterfall_result = run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids, toggles=toggles)
    timings["waterfall"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Waterfall: {len(waterfall_result):,} rows")

    # --- Step 4: Tier 2 suppression ---
    campaign_type = campaign_config.get("campaign_type", "Appeal") or "Appeal"
    t0 = time.time()
    waterfall_result, tier2_log = apply_tier2_suppression(
        waterfall_result, accounts_df, campaign_type=campaign_type
    )
    timings["suppression"] = round(time.time() - t0, 1)

    # --- Step 5: Build segment summary (used for per-segment aggregates) ---
    segment_summary = build_segment_summary(waterfall_result)

    # --- Step 6: Baseline → per-segment historical economics ---
    # Two modes:
    #   (a) baseline_type — multi-campaign average from historical_baseline (default)
    #   (b) baseline_appeal_code — single-campaign rollup from Segment Actuals (legacy)
    # If both are supplied, baseline_type wins.
    gc = get_sheets_client()
    baseline_by_segment = {}
    baseline_info = {"mode": "none"}
    if baseline_type:
        t0 = time.time()
        try:
            rows = fetch_baseline_for_type(baseline_type)
        except Exception as e:
            logger.warning(f"  fetch_baseline_for_type failed: {e}")
            rows = {}
        timings["baseline"] = round(time.time() - t0, 1)
        for seg_code, r in rows.items():
            baseline_by_segment[seg_code] = {
                "response_rate":          float(r["response_rate"]),
                "avg_gift":               float(r["avg_gift"]),
                "confidence":             r["confidence"],
                "net_revenue_per_contact": float(r["response_rate"]) * float(r["avg_gift"]),
            }
        baseline_info = {
            "mode":     "campaign_type",
            "type":     baseline_type,
            "segments": len(baseline_by_segment),
        }
        logger.info(f"[{_elapsed()}s] Baseline (type={baseline_type}): "
                    f"{len(baseline_by_segment)} segments")
    elif baseline_appeal_code:
        t0 = time.time()
        baseline_df = build_baseline_rollup(gc, baseline_appeal_code)
        timings["baseline"] = round(time.time() - t0, 1)
        if not baseline_df.empty:
            for _, r in baseline_df.iterrows():
                baseline_by_segment[r["hri_segment"]] = {
                    "response_rate":           float(r["response_rate"]),
                    "avg_gift":                float(r["avg_gift"]),
                    "confidence":              "specific",
                    "net_revenue_per_contact": float(r["avg_gift"]) * float(r["response_rate"]),
                }
        baseline_info = {
            "mode":          "specific_campaign",
            "appeal_code":   baseline_appeal_code,
            "segments":      len(baseline_by_segment),
        }
        logger.info(f"[{_elapsed()}s] Baseline (campaign={baseline_appeal_code}): "
                    f"{len(baseline_by_segment)} segments")

    # --- Step 7: CPP from campaign config ---
    cpp = 0.48
    if campaign_config.get("budget_qty_mailed") and campaign_config.get("budget_cost"):
        cpp = float(campaign_config["budget_cost"]) / float(campaign_config["budget_qty_mailed"])

    # --- Step 8: Per-segment aggregates with baseline economics ---
    segment_aggregates = []
    for _, seg_row in segment_summary.iterrows():
        code = seg_row["Segment Code"]
        qty = int(seg_row["Quantity"])
        bl = baseline_by_segment.get(code, {})
        rr = bl.get("response_rate", 0)
        avg_gift = bl.get("avg_gift", 0)
        total_cost = qty * cpp
        proj_gross = qty * rr * avg_gift
        proj_net = proj_gross - total_cost
        segment_aggregates.append({
            "segment_code": code,
            "segment_name": seg_row.get("Segment Name", code),
            "quantity": qty,
            "cpp": round(cpp, 4),
            "total_cost": round(total_cost, 2),
            "hist_response_rate": round(rr, 4),
            "hist_avg_gift": round(avg_gift, 2),
            "baseline_confidence": bl.get("confidence", ""),
            "proj_gross_revenue": round(proj_gross, 2),
            "proj_net_revenue": round(proj_net, 2),
            "net_per_contact": round(rr * avg_gift - cpp, 4),  # For greedy solver
            "roi": round(proj_gross / total_cost, 2) if total_cost > 0 else 0,
        })

    # --- Step 9: Build per-donor universe ---
    # Only include assigned (non-suppressed) donors. Browser filters on segment.
    t0 = time.time()
    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ].copy()

    # Merge account fields efficiently
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Vectorized field mapping (same pattern as output_files.py)
    aids = assigned["account_id"]
    universe_df = pd.DataFrame({
        "account_id": aids,
        "constituent_id": aids.map(accts.get("Constituent_Id__c", pd.Series(dtype=str))).fillna(""),
        "segment_code": assigned["segment_code"].values,
        "rfm_score": assigned["RFM_weighted_score"].values,
        "rfm_code": assigned["RFM_code"].values,
        "months_since_last_gift": assigned.get("months_since_last_gift", pd.Series(0, index=assigned.index)).fillna(0).values,
        "cumulative_giving": assigned["cumulative_giving"].values,
        "avg_gift_5yr": aids.map(accts.get("npo02__AverageAmount__c", pd.Series(dtype=float))).fillna(0).values,
        "last_gift_amount": aids.map(accts.get("npo02__LastOppAmount__c", pd.Series(dtype=float))).fillna(0).values,
        "gifts_12mo": aids.map(accts.get("Gifts_in_L12M__c", pd.Series(dtype=float))).fillna(0).values,
        "lifecycle_stage": assigned["lifecycle_stage"].values,
        "is_cbnc": aids.map(accts.get("is_cbnc", pd.Series(False, dtype=bool))).fillna(False).values,
        "has_dm_gift_500": aids.map(accts.get("has_dm_gift_500", pd.Series(False, dtype=bool))).fillna(False).values,
    })

    # Attach per-donor baseline economics (segment-level values, per-donor for scenario solver)
    universe_df["hist_response_rate"] = universe_df["segment_code"].map(
        {k: v["response_rate"] for k, v in baseline_by_segment.items()}
    ).fillna(0)
    universe_df["hist_avg_gift"] = universe_df["segment_code"].map(
        {k: v["avg_gift"] for k, v in baseline_by_segment.items()}
    ).fillna(0)

    timings["build_universe_df"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Universe built: {len(universe_df):,} donors")

    # --- Step 10: Convert to JSON-safe dicts ---
    # Replace NaN with None, ensure native Python types
    t0 = time.time()
    universe_df = universe_df.replace({np.nan: None})
    # Round floats to reduce payload size
    for col in ["rfm_score", "months_since_last_gift", "cumulative_giving",
                "avg_gift_5yr", "last_gift_amount", "gifts_12mo",
                "hist_response_rate", "hist_avg_gift"]:
        if col in universe_df.columns:
            universe_df[col] = pd.to_numeric(universe_df[col], errors="coerce").round(2)

    donors_list = universe_df.to_dict(orient="records")
    timings["serialize"] = round(time.time() - t0, 1)

    # --- Step 11: Write to MIC Universe tab (persistence) ---
    # Do NOT write 70K rows to sheets — just write segment aggregates summary as a snapshot.
    # Per-donor universe stays in the browser / regenerated on demand.
    t0 = time.time()
    try:
        _write_universe_tab(gc, segment_aggregates, campaign_config, baseline_appeal_code)
        universe_tab_status = "ok"
    except Exception as e:
        logger.warning(f"  Universe tab write failed: {e}")
        universe_tab_status = f"skipped: {e}"
    timings["universe_tab"] = round(time.time() - t0, 1)

    total_time = round(time.time() - t_start, 1)
    logger.info("=" * 60)
    logger.info(f"BUILD UNIVERSE COMPLETE — {total_time}s")
    logger.info(f"  Donors: {len(donors_list):,}")
    logger.info(f"  Segments: {len(segment_aggregates)}")
    logger.info(f"  Payload estimate: ~{len(donors_list) * 250 // 1024 // 1024}MB")
    logger.info("=" * 60)

    return {
        "donors": donors_list,
        "segments": segment_aggregates,
        "campaign": campaign_config,
        "baseline_appeal_code": baseline_appeal_code or "",
        "baseline_type": baseline_type or "",
        "baseline": baseline_info,
        "meta": {
            "data_source": data_source,
            "cpp": round(cpp, 4),
            "total_donors": len(donors_list),
            "total_segments": len(segment_aggregates),
            "timings": timings,
            "total_seconds": total_time,
            "generated_at": datetime.utcnow().isoformat(),
            "universe_tab": universe_tab_status,
        },
    }


def _write_universe_tab(gc, segment_aggregates, campaign_config, baseline_appeal_code):
    """Write segment-level universe snapshot to MIC Universe tab."""
    sh = gc.open_by_key(MIC_SHEET_ID)
    try:
        ws = sh.worksheet(UNIVERSE_TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(title=UNIVERSE_TAB, rows="200", cols="20")

    campaign_code = campaign_config.get("appeal_code", "")
    headers = [
        "Segment Code", "Segment Name", "Qty", "CPP", "Total Cost",
        "Hist RR", "Hist Avg Gift", "Proj Gross Rev", "Proj Net Rev",
        "Net/Contact", "ROI", "Campaign", "Baseline", "Generated",
    ]
    ts = datetime.utcnow().isoformat()
    rows = [headers]
    for s in segment_aggregates:
        rows.append([
            s["segment_code"], s["segment_name"], s["quantity"],
            s["cpp"], s["total_cost"], s["hist_response_rate"],
            s["hist_avg_gift"], s["proj_gross_revenue"], s["proj_net_revenue"],
            s["net_per_contact"], s["roi"],
            campaign_code, baseline_appeal_code or "", ts,
        ])
    # Resize if needed
    if ws.col_count < len(headers):
        ws.resize(rows=max(ws.row_count, len(rows) + 10), cols=len(headers))
    ws.update(range_name="A1", values=rows)
    logger.info(f"  Universe tab written: {len(segment_aggregates)} segment rows")
