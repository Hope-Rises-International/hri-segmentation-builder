"""Phase 3 of Dynamic Scenario Editor: /approve-scenario endpoint.

Accepts a scenario definition from the browser (segment_overrides + target),
runs the full finalization pipeline (re-using the existing waterfall +
suppression + baseline + budget fit + ask strings + appeal codes + output
files + MIC writes) with the operator's scenario applied.

Per architect Option B: re-run the waterfall deterministically on cached
BQ data and apply the scenario's segment_overrides. The browser does NOT
send the universe back — it only sends the scenario parameters.

Updates campaign status in MIC Campaign Calendar to "Approved" on success.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime

import pandas as pd

from bq_reader import check_cache_freshness, fetch_accounts_from_bq
from salesforce_client import (
    connect_salesforce, fetch_accounts, fetch_opportunities,
    fetch_opportunities_cbnc, probe_sustainer_field,
)
from rfm_engine import compute_rfm
from lifecycle import compute_lifecycle
from cbnc import detect_cbnc
from waterfall_engine import run_waterfall, build_segment_summary, build_suppression_summary
from suppression_engine import (
    apply_tier2_suppression, apply_segment_level_suppression,
    build_suppression_audit_log,
)
from budget_fitting import fit_to_budget
from ask_strings import compute_ask_strings, classify_reply_copy_tier
from appeal_codes import generate_appeal_codes, validate_appeal_codes
from output_files import generate_output_files
from mic_writeback import PipelineWriteRecovery
from baseline_rollup import build_baseline_rollup, apply_baseline_to_summary
from sheets_client import get_sheets_client, read_campaign_calendar
from config import MIC_SHEET_ID, MIC_CAMPAIGN_CALENDAR_TAB

logger = logging.getLogger(__name__)


def approve_scenario(
    campaign_config: dict,
    scenario: dict,
    toggles: dict = None,
    baseline_appeal_code: str = None,
) -> dict:
    """Apply a scenario to the universe and generate final outputs.

    Args:
        campaign_config: {campaign_name, appeal_code, budget_qty_mailed,
                          budget_cost, campaign_type, lane}.
        scenario: {segments: [{code, include, percent}, ...],
                   target_type, target_value, name}.
        toggles: waterfall/suppression toggle overrides.
        baseline_appeal_code: prior campaign for baseline economics.

    Returns:
        dict with status, file URLs, campaign status, counts.
    """
    timings = {}
    t_start = time.time()
    def _elapsed():
        return round(time.time() - t_start, 1)

    logger.info("=" * 60)
    logger.info(f"APPROVE SCENARIO — {scenario.get('name', 'unnamed')}")
    logger.info(f"  Campaign: {campaign_config.get('appeal_code', 'N/A')}")
    logger.info(f"  Target: {scenario.get('target_type')} = {scenario.get('target_value')}")
    logger.info("=" * 60)

    # Convert scenario.segments → segment_overrides dict
    segment_overrides = {}
    for s in scenario.get("segments", []):
        code = s.get("code")
        if not code:
            continue
        include = s.get("include", True)
        percent = s.get("percent", 100)
        # Only include in overrides if non-default
        if not include or percent < 100:
            segment_overrides[code] = {
                "include": include,
                "percent_include": percent if include else 0,
            }
    logger.info(f"  Segment overrides: {len(segment_overrides)} segments")

    # --- Step 1: Load accounts (BQ preferred) ---
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
        sf = connect_salesforce()
        probe_sustainer_field(sf)
        accounts_df = fetch_accounts(sf)
        opps_df = fetch_opportunities(sf)
        cbnc_opps_df = fetch_opportunities_cbnc(sf)
        cbnc_ids = detect_cbnc(cbnc_opps_df)
    timings["load_data"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Loaded {len(accounts_df):,} accounts from {data_source}")

    # --- Step 2: RFM + lifecycle + waterfall ---
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    lifecycle = compute_lifecycle(accounts_df)
    waterfall_result = run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids, toggles=toggles)
    timings["segmentation"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Segmentation complete")

    # --- Step 3: Tier 2 suppression + initial segment summary ---
    campaign_type = campaign_config.get("campaign_type", "Appeal") or "Appeal"
    t0 = time.time()
    waterfall_result, tier2_log = apply_tier2_suppression(
        waterfall_result, accounts_df, campaign_type=campaign_type
    )
    segment_summary = build_segment_summary(waterfall_result)
    suppression_summary_df = build_suppression_summary(waterfall_result)
    timings["suppression"] = round(time.time() - t0, 1)

    # --- Step 4: CPP + target ---
    cpp = 0.48
    if campaign_config.get("budget_qty_mailed") and campaign_config.get("budget_cost"):
        cpp = float(campaign_config["budget_cost"]) / float(campaign_config["budget_qty_mailed"])
    target_qty = int(campaign_config.get("budget_qty_mailed") or 35000)

    # --- Step 5: Segment-level economic suppression (always active: break-even, RR floor) ---
    segment_summary = apply_segment_level_suppression(segment_summary, cpp)

    # --- Step 6: Budget fit with scenario overrides ---
    t0 = time.time()
    waterfall_result, segment_summary, fit_info = fit_to_budget(
        waterfall_result, target_qty, segment_summary,
        segment_overrides=segment_overrides,
    )
    timings["budget_fit"] = round(time.time() - t0, 1)

    # --- Step 7: CPP and Total Cost always populate ---
    fit_col = "Budget Fit" if "Budget Fit" in segment_summary.columns else "Quantity"
    segment_summary["CPP"] = cpp
    segment_summary["Total Cost"] = segment_summary[fit_col].apply(
        lambda q: round(float(q) * cpp, 2) if q and str(q).replace('.', '').isdigit() else ""
    )

    # --- Step 8: Apply baseline economics if selected ---
    gc = get_sheets_client()
    if baseline_appeal_code:
        t0 = time.time()
        baseline_df = build_baseline_rollup(gc, baseline_appeal_code)
        segment_summary = apply_baseline_to_summary(segment_summary, baseline_df, cpp)
        timings["baseline"] = round(time.time() - t0, 1)

    # --- Step 9: Ask strings + appeal codes ---
    t0 = time.time()
    ask_df = compute_ask_strings(waterfall_result, accounts_df)
    reply_tiers = classify_reply_copy_tier(waterfall_result, accounts_df)
    campaign_appeal_code = campaign_config.get("appeal_code", "") or "R2631TYRE"
    if len(campaign_appeal_code) < 5:
        campaign_appeal_code = "R2631TYRE"
    codes_df = generate_appeal_codes(
        waterfall_result, accounts_df,
        campaign_appeal_code=campaign_appeal_code,
    )
    timings["codes"] = round(time.time() - t0, 1)

    # --- Step 10: Output files ---
    t0 = time.time()
    output = generate_output_files(
        waterfall_result, accounts_df, ask_df, reply_tiers, codes_df,
        campaign_code=campaign_config.get("appeal_code", "SCENARIO") or "SCENARIO",
        lane=campaign_config.get("lane", "Housefile") or "Housefile",
        holdout_pct=5.0,
    )
    timings["output_files"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Output files: printer={output['printer_count']:,}, "
                f"matchback={output['matchback_count']:,}")

    # --- Step 11: Suppression audit log ---
    audit_log = build_suppression_audit_log(waterfall_result, tier2_log)
    audit_csv = audit_log.to_csv(index=False)

    # --- Step 12: Pipeline Write Recovery (Drive + Sheets) ---
    t0 = time.time()
    pipeline = PipelineWriteRecovery()
    write_status = pipeline.execute_writes(
        gc,
        printer_csv=output["printer_csv"],
        matchback_csv=output["matchback_csv"],
        suppression_audit_csv=audit_csv,
        segment_summary=segment_summary,
        campaign_code=campaign_config.get("appeal_code", "SCENARIO") or "SCENARIO",
        campaign_appeal_code=campaign_appeal_code,
        lane=campaign_config.get("lane", "Housefile") or "Housefile",
        exceptions_csv=output.get("exceptions_csv", ""),
    )
    timings["writes"] = round(time.time() - t0, 1)

    # --- Step 13: Update campaign status in MIC Campaign Calendar → Approved ---
    t0 = time.time()
    status_result = _update_campaign_status(
        gc, campaign_config.get("appeal_code", ""), "Approved"
    )
    timings["status_update"] = round(time.time() - t0, 1)

    total_time = round(time.time() - t_start, 1)
    logger.info("=" * 60)
    logger.info(f"APPROVE COMPLETE — {total_time}s")
    logger.info(f"  Scenario: {scenario.get('name', 'unnamed')}")
    logger.info(f"  Printer rows: {output['printer_count']:,}")
    logger.info(f"  Matchback rows: {output['matchback_count']:,}")
    logger.info(f"  Campaign status: {status_result.get('status')}")
    logger.info("=" * 60)

    return {
        "status": "success",
        "scenario_name": scenario.get("name", "unnamed"),
        "campaign_appeal_code": campaign_config.get("appeal_code", ""),
        "campaign_status": status_result.get("status"),
        "drive_urls": write_status.get("drive_urls", {}),
        "counts": {
            "printer_rows": int(output["printer_count"]),
            "matchback_rows": int(output["matchback_count"]),
            "suppression_rows": int(output["suppression_count"]),
            "holdout": int(output["holdout_count"]),
            "excluded": int(output["excluded_count"]),
            "fitted": int(fit_info["fitted"]),
            "trimmed": int(fit_info["trimmed"]),
        },
        "segment_summary_rows": len(segment_summary),
        "write_status": {k: v for k, v in write_status.items() if k != "drive_urls"},
        "timings": timings,
        "total_seconds": total_time,
    }


def _update_campaign_status(gc, appeal_code: str, new_status: str) -> dict:
    """Update the status column in MIC Campaign Calendar for the given appeal code."""
    if not appeal_code:
        return {"status": "skipped", "reason": "no appeal_code"}
    try:
        sh = gc.open_by_key(MIC_SHEET_ID)
        ws = sh.worksheet(MIC_CAMPAIGN_CALENDAR_TAB)
        headers = ws.row_values(1)
        if "appeal_code" not in headers or "status" not in headers:
            return {"status": "skipped", "reason": "columns not found"}
        appeal_col = headers.index("appeal_code") + 1
        status_col = headers.index("status") + 1

        # Find row matching appeal code
        appeal_values = ws.col_values(appeal_col)
        for i, v in enumerate(appeal_values):
            if str(v).strip() == str(appeal_code).strip():
                ws.update_cell(i + 1, status_col, new_status)
                logger.info(f"  Campaign status set to '{new_status}' (row {i+1})")
                return {"status": new_status, "row": i + 1}
        return {"status": "not_found", "reason": f"appeal_code {appeal_code} not in Calendar"}
    except Exception as e:
        logger.warning(f"  Campaign status update failed: {e}")
        return {"status": "error", "reason": str(e)}
