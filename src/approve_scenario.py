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
from historical_baseline import fetch_baseline_for_type
from sheets_client import get_sheets_client, read_campaign_calendar
from config import (
    MIC_SHEET_ID, MIC_CAMPAIGN_CALENDAR_TAB,
    DEFAULT_TOGGLES, TOGGLE_PREFIX_RULES, validate_campaign_selection,
)

logger = logging.getLogger(__name__)


def approve_scenario(
    campaign_config: dict,
    scenario: dict,
    toggles: dict = None,
    baseline_appeal_code: str = None,
    baseline_type: str = None,
    selected_campaigns: list = None,
    nuclear: bool = False,
) -> dict:
    """Apply a scenario to the universe and generate final outputs.

    Args:
        campaign_config: {campaign_name, appeal_code, budget_qty_mailed,
                          budget_cost, campaign_type, lane}. Used as the
                          primary campaign for budget/CPP/lane purposes.
        scenario: {segments: [{code, include, percent}, ...],
                   target_type, target_value, name}.
        toggles: waterfall/suppression toggle overrides.
        baseline_appeal_code: prior campaign for baseline economics.
        selected_campaigns: list of campaign dicts (Item C). When more
            than one campaign is selected, donors route to the campaign
            whose prefix matches their cohort, and one Print + one
            Matchback file pair is emitted per campaign. The
            `campaign_config` campaign is always added to this list if
            missing.
        nuclear: when True (Item D), force all GROUP_EXCLUDE toggles ON,
            skip Tier 2 + segment-level rule-based suppressions, skip
            holdout, and write a nuclear_run audit log to Drive
            alongside the standard outputs. Tier 1 hard suppressions
            still apply; cohort routing is preserved.

    Returns:
        dict with status, file URLs, campaign status, counts.
    """
    timings = {}
    t_start = time.time()
    def _elapsed():
        return round(time.time() - t_start, 1)

    # --- Normalize the campaign list (Item C) ---
    # Always include campaign_config in the working list. De-dup by
    # appeal_code; preserve operator order otherwise.
    selected_campaigns = list(selected_campaigns or [])
    primary_code = (campaign_config.get("appeal_code") or "").strip()
    if primary_code:
        if not any((c.get("appeal_code") or "").strip() == primary_code
                   for c in selected_campaigns):
            selected_campaigns.insert(0, campaign_config)
    seen = set()
    deduped = []
    for c in selected_campaigns:
        ac = (c.get("appeal_code") or "").strip()
        if not ac or ac in seen:
            continue
        seen.add(ac)
        deduped.append(c)
    selected_campaigns = deduped

    # --- Nuclear toggle override (Item D) ---
    # Capture the operator's pre-Nuclear toggle state for the audit log
    # before forcing GROUP_EXCLUDE keys ON.
    pre_nuclear_toggles = dict(toggles or DEFAULT_TOGGLES)
    if nuclear:
        # GROUP toggles forced ON, regardless of operator setting.
        # RFM toggles left alone (they're skip-only, so honoring the
        # operator's choice keeps the semantics clean — Nuclear is
        # about including more donors, not changing routing rules).
        forced = dict(pre_nuclear_toggles)
        for tk in ["cornerstone", "sustainer", "new_donor",
                   "major_gift", "mid_level", "mid_level_prospect"]:
            forced[tk] = True
        # Tier 2/3 + recent-gift + freq cap suppressed too — flipped
        # to OFF so the suppression engine skips them. Holdout is no
        # longer a toggle (v3.4) — Nuclear zeroes the per-segment
        # values below so no donors are held out.
        for tk in ["newsletter_only", "match_only", "no_name_sharing",
                   "xmas_catalog_cap", "xmas_easter_cap",
                   "recent_gift_window", "frequency_cap"]:
            forced[tk] = False
        toggles = forced
        logger.info("  NUCLEAR MODE: GROUP toggles forced ON; "
                    "Tier 2/3 + recent-gift + freq cap + holdout bypassed.")

    # --- Pre-run validation: cohort prefix must be present (Item C) ---
    val_errors = validate_campaign_selection(
        toggles or DEFAULT_TOGGLES, selected_campaigns
    )
    if val_errors:
        return {
            "status": "validation_error",
            "errors": val_errors,
            "scenario_name": scenario.get("name", "unnamed"),
        }

    logger.info("=" * 60)
    logger.info(f"APPROVE SCENARIO — {scenario.get('name', 'unnamed')}")
    logger.info(f"  Primary campaign: {primary_code or 'N/A'}")
    if len(selected_campaigns) > 1:
        logger.info(f"  Multi-campaign run: {[c.get('appeal_code') for c in selected_campaigns]}")
    if nuclear:
        logger.info(f"  Nuclear: ON")
    logger.info(f"  Target: {scenario.get('target_type')} = {scenario.get('target_value')}")
    logger.info("=" * 60)

    # Convert scenario.segments → segment_overrides dict (for budget
    # fitting) and a separate holdout_pct_by_segment map (for v3.4
    # per-segment holdout sampling).
    segment_overrides = {}
    holdout_pct_by_segment = {}
    for s in scenario.get("segments", []):
        code = s.get("code")
        if not code:
            continue
        include = s.get("include", True)
        percent = s.get("percent", 100)
        # Holdout %: integer 0–5; default 5. Coerce / clamp to be safe;
        # the UI also enforces, but defense in depth.
        try:
            holdout_pct = int(s.get("holdout_pct", 5))
        except (TypeError, ValueError):
            holdout_pct = 5
        holdout_pct = max(0, min(5, holdout_pct))
        holdout_pct_by_segment[code] = holdout_pct
        # Only include in budget overrides if non-default include/percent
        if not include or percent < 100:
            segment_overrides[code] = {
                "include": include,
                "percent_include": percent if include else 0,
            }
    logger.info(f"  Segment overrides: {len(segment_overrides)} segments")
    logger.info(f"  Holdout by segment: {holdout_pct_by_segment}")

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
    # Nuclear mode (Item D) skips Tier 2 entirely so donors flagged
    # Newsletter-Only / Match-Only / etc. are included anyway. The
    # operator gets a Nuclear-specific audit log instead of the
    # standard suppression log.
    #
    # For the Nuclear audit log we want a "what would Tier 2 have
    # removed" delta — measured by simulating Tier 2 on a copy of
    # waterfall_result without applying it to the real working frame.
    # This is the "donors added by Nuclear" number Bill expects.
    campaign_type = campaign_config.get("campaign_type", "Appeal") or "Appeal"
    pre_t2_count = (
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ).sum()
    t0 = time.time()
    if nuclear:
        tier2_log = []
        # Simulate Tier 2 on a copy to compute the delta the operator
        # would have lost without Nuclear. Don't mutate the real
        # working frame.
        try:
            sim_wf, _ = apply_tier2_suppression(
                waterfall_result.copy(), accounts_df, campaign_type=campaign_type
            )
            sim_assigned = (
                (sim_wf["segment_code"] != "")
                & (sim_wf["suppression_reason"] == "")
            ).sum()
            nuclear_delta = int(pre_t2_count - sim_assigned)
        except Exception as e:
            logger.warning(f"  Nuclear Tier 2 simulation failed: {e}")
            nuclear_delta = 0
        logger.info(f"  Tier 2 suppression: SKIPPED (Nuclear mode); "
                    f"delta vs. non-Nuclear = +{nuclear_delta:,} donors")
    else:
        waterfall_result, tier2_log = apply_tier2_suppression(
            waterfall_result, accounts_df, campaign_type=campaign_type
        )
        nuclear_delta = 0
    segment_summary = build_segment_summary(waterfall_result)
    suppression_summary_df = build_suppression_summary(waterfall_result)
    timings["suppression"] = round(time.time() - t0, 1)
    post_t2_count = (
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ).sum()

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
    # baseline_type takes priority over baseline_appeal_code.
    gc = get_sheets_client()
    if baseline_type:
        t0 = time.time()
        rows = fetch_baseline_for_type(baseline_type)
        baseline_df = pd.DataFrame([
            {"hri_segment": seg,
             "response_rate": r["response_rate"],
             "avg_gift":      r["avg_gift"]}
            for seg, r in rows.items()
        ])
        segment_summary = apply_baseline_to_summary(segment_summary, baseline_df, cpp)
        timings["baseline"] = round(time.time() - t0, 1)
    elif baseline_appeal_code:
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
    multi_mode = len(selected_campaigns) > 1
    codes_df = generate_appeal_codes(
        waterfall_result, accounts_df,
        campaign_appeal_code=campaign_appeal_code if not multi_mode else None,
        selected_campaigns=selected_campaigns if multi_mode else None,
        # v3.4.1: pass campaign metadata so the engine can detect
        # Shipping campaigns and route CA donors to the CA1 panel.
        campaign_name=campaign_config.get("campaign_name", ""),
        campaign_lane=campaign_config.get("lane", ""),
        is_followup=bool(campaign_config.get("is_followup", False)),
    )
    timings["codes"] = round(time.time() - t0, 1)

    # --- Step 10: Output files ---
    # Nuclear mode skips holdout per Item D. Single-campaign legacy
    # path passes selected_campaigns=None so output_files keeps the
    # one-pair shape; multi-campaign passes the full list and the
    # generator emits one Print + one Matchback per campaign.
    #
    # v3.4: per-segment holdout. Build the segment→pct map from the
    # scenario; under Nuclear, zero everything so no donors are held
    # out (Nuclear is rule-bypass + cast-wide). When the scenario
    # didn't provide a row for a given segment, the output_files
    # generator falls back to its built-in default (5%).
    if nuclear:
        runtime_holdout_by_segment = {code: 0 for code in holdout_pct_by_segment}
    else:
        runtime_holdout_by_segment = holdout_pct_by_segment
    t0 = time.time()
    output = generate_output_files(
        waterfall_result, accounts_df, ask_df, reply_tiers, codes_df,
        campaign_code=campaign_config.get("appeal_code", "SCENARIO") or "SCENARIO",
        lane=campaign_config.get("lane", "Housefile") or "Housefile",
        holdout_pct=0.0 if nuclear else 5.0,
        selected_campaigns=selected_campaigns if multi_mode else None,
        holdout_pct_by_segment=runtime_holdout_by_segment,
    )
    timings["output_files"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Output files: printer={output['printer_count']:,}, "
                f"matchback={output['matchback_count']:,}")

    # --- Step 11: Suppression audit log ---
    audit_log = build_suppression_audit_log(waterfall_result, tier2_log)
    audit_csv = audit_log.to_csv(index=False)

    # --- Step 11b: Nuclear audit log (Item D) ---
    # When Nuclear is ON, write a separate file that captures: who
    # ran it, the toggle states (operator's settings BEFORE forcing,
    # plus the forced state used for the run), pre/post universe
    # counts, the campaign list, and the output filenames. This file
    # lives alongside the standard outputs in Drive.
    nuclear_log_csv = ""
    if nuclear:
        operator = ""
        try:
            # Apps Script proxies the user's email through to Cloud
            # Run via the OIDC token's `email` claim, but Cloud Run
            # functions don't surface that through functions_framework.
            # The UI also POSTs `operator` in the payload.
            operator = (campaign_config.get("operator")
                        or scenario.get("operator", "")
                        or "")
        except Exception:
            pass

        per_campaign_counts = {}
        for ac, p in (output.get("per_campaign") or {}).items():
            per_campaign_counts[ac] = {
                "printer_rows":   p.get("printer_count", 0),
                "matchback_rows": p.get("matchback_count", 0),
            }

        nuclear_lines = [
            "field,value",
            f"timestamp,{datetime.now().isoformat()}",
            f"operator,{operator}",
            f"primary_campaign,{primary_code}",
            f"campaigns,\"{', '.join(c.get('appeal_code','') for c in selected_campaigns)}\"",
            # pre/post compare same step — Tier 2 was skipped so they're equal.
            # The meaningful number is the simulated delta: how many donors
            # WOULD have been removed by Tier 2 without Nuclear.
            f"assigned_donors,{int(post_t2_count)}",
            f"would_have_lost_to_tier2,{int(nuclear_delta)}",
            f"delta_added_by_nuclear,{int(nuclear_delta)}",
        ]
        # Operator-set toggles (pre-Nuclear)
        for k in sorted(pre_nuclear_toggles.keys()):
            nuclear_lines.append(f"toggle_pre_nuclear.{k},{pre_nuclear_toggles[k]}")
        # Forced toggles (used at run time)
        for k in sorted(toggles.keys()):
            nuclear_lines.append(f"toggle_runtime.{k},{toggles[k]}")
        # Per-campaign output counts
        for ac, counts in per_campaign_counts.items():
            nuclear_lines.append(f"output.{ac}.printer_rows,{counts['printer_rows']}")
            nuclear_lines.append(f"output.{ac}.matchback_rows,{counts['matchback_rows']}")
        nuclear_log_csv = "\n".join(nuclear_lines) + "\n"

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
        per_campaign=output.get("per_campaign") if multi_mode else None,
        nuclear_log_csv=nuclear_log_csv,
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
