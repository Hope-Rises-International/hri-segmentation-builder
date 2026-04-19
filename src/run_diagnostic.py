"""Pipeline orchestrator: Phases 1-5 (SF pull → RFM → waterfall → suppression → fitting → ask/codes → output files)."""

import logging
import sys
import time

from salesforce_client import (
    connect_salesforce, fetch_accounts, fetch_opportunities,
    fetch_opportunities_cbnc, probe_sustainer_field,
)
from bq_reader import (
    check_cache_freshness,
    fetch_accounts_from_bq,
)
from sheets_client import (
    get_sheets_client, read_campaign_calendar, write_diagnostic,
    write_draft_tab, upload_csv_to_drive,
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
from diagnostic import (
    build_rfm_crosstab_rf,
    build_rfm_crosstab_rm,
    build_rfm_summary,
    build_hpc_mrc_diagnostic,
    build_sustainer_diagnostic,
    build_staff_manager_diagnostic,
    build_cornerstone_diagnostic,
    evaluate_gate_criteria,
)

import pandas as pd
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _pick_campaign_from_mic(mic_df: pd.DataFrame):
    """Pick a real campaign from the MIC for budget fitting demo.

    Selects the most recent DM housefile campaign with a budget_qty_mailed.
    """
    if mic_df.empty:
        return None

    dm = mic_df[
        (mic_df.get("channel", pd.Series("")).str.contains("Direct Mail", case=False, na=False))
        & (mic_df.get("budget_qty_mailed", pd.Series(0)).apply(
            lambda x: pd.to_numeric(x, errors="coerce")
        ) > 0)
    ]

    if dm.empty:
        # Try any row with a budget qty
        dm = mic_df[mic_df.get("budget_qty_mailed", pd.Series(0)).apply(
            lambda x: pd.to_numeric(x, errors="coerce")
        ) > 0]

    if dm.empty:
        return None

    # Take the last row (most recent)
    row = dm.iloc[-1]
    budget_qty = int(pd.to_numeric(row.get("budget_qty_mailed", 0), errors="coerce") or 0)
    budget_cost = float(pd.to_numeric(row.get("budget_cost", 0), errors="coerce") or 0)
    cpp = budget_cost / budget_qty if budget_qty > 0 else 0.48  # default CPP

    return {
        "campaign_name": row.get("campaign_name", "Unknown"),
        "appeal_code": row.get("appeal_code", ""),
        "budget_qty_mailed": budget_qty,
        "budget_cost": budget_cost,
        "cpp": round(cpp, 4),
        "campaign_type": row.get("campaign_type", "Appeal") or "Appeal",
    }


def run_diagnostic(toggles=None, baseline_appeal_code=None, segment_overrides=None) -> dict:
    """Execute the full pipeline. Reads from BQ cache when fresh, falls back to live SF.

    Args:
        toggles: Optional dict of waterfall/suppression toggle overrides from the UI.
                 If None, uses DEFAULT_TOGGLES.
        baseline_appeal_code: Optional appeal code of a prior campaign to use as
                              performance baseline for economics columns.
        segment_overrides: Optional per-segment operator overrides:
                          {segment_code: {'include': bool, 'percent_include': int}}.
    """
    timings = {}
    pipeline_start = time.time()

    def _elapsed():
        return round(time.time() - pipeline_start, 1)

    logger.info("=" * 60)
    logger.info("SEGMENTATION BUILDER PIPELINE")
    logger.info("=" * 60)

    # --- Step 1: Check BQ cache freshness ---
    t0 = time.time()
    cache_fresh, cache_age_hours, cache_timestamp = check_cache_freshness()
    timings["cache_check"] = round(time.time() - t0, 1)

    data_source = "unknown"
    sustainer_field_exists = True  # Known from prior runs

    if cache_fresh:
        # --- BQ path: read from cache (fast) ---
        # accounts table has pre-computed is_cbnc and has_dm_gift_500 flags.
        # No raw opportunity queries needed.
        data_source = "bigquery"
        logger.info(f"Using BQ cache (age: {cache_age_hours}h)")

        t0 = time.time()
        accounts_df = fetch_accounts_from_bq()
        timings["pass1"] = round(time.time() - t0, 1)

        # BQ path: CBNC IDs come from pre-computed is_cbnc column
        if "is_cbnc" in accounts_df.columns:
            cbnc_ids = set(accounts_df.loc[accounts_df["is_cbnc"] == True, "Id"])
            logger.info(f"  CBNC from BQ flag: {len(cbnc_ids):,} donors")
        else:
            cbnc_ids = set()
            logger.warning("  is_cbnc column missing from BQ cache")

        # BQ path: no raw opps needed — RFM uses Account rollup fields only
        opps_df = pd.DataFrame()
        timings["pass2"] = 0
        timings["pass3"] = 0
    else:
        # --- SF fallback: live queries (slow, ~14 min) ---
        data_source = "salesforce_live"
        logger.info(f"BQ cache stale or missing — falling back to live Salesforce queries")

        t0 = time.time()
        sf = connect_salesforce()
        timings["sf_connect"] = round(time.time() - t0, 1)
        logger.info(f"Salesforce connected ({timings['sf_connect']}s)")

        sustainer_field_exists = probe_sustainer_field(sf)

        t0 = time.time()
        accounts_df = fetch_accounts(sf)
        timings["pass1"] = round(time.time() - t0, 1)

        t0 = time.time()
        opps_df = fetch_opportunities(sf)
        timings["pass2"] = round(time.time() - t0, 1)

        t0 = time.time()
        cbnc_opps_df = fetch_opportunities_cbnc(sf)
        timings["pass3"] = round(time.time() - t0, 1)

        # Compute CBNC from raw opps (SF path)
        t0 = time.time()
        cbnc_ids = detect_cbnc(cbnc_opps_df)
        timings["cbnc"] = round(time.time() - t0, 1)
        del cbnc_opps_df

    logger.info(f"[{_elapsed()}s] Data loaded. Source: {data_source} | Accounts: {len(accounts_df):,} | "
                f"Opps: {len(opps_df):,} | CBNC donors: {len(cbnc_ids):,}")

    # --- Compute RFM, lifecycle ---
    logger.info(f"[{_elapsed()}s] Computing RFM...")
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    timings["rfm"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] RFM done ({timings['rfm']}s)")

    logger.info(f"[{_elapsed()}s] Computing lifecycle...")
    t0 = time.time()
    lifecycle = compute_lifecycle(accounts_df)
    timings["lifecycle"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Lifecycle done ({timings['lifecycle']}s)")

    # --- Waterfall assignment ---
    logger.info(f"[{_elapsed()}s] Running waterfall...")
    t0 = time.time()
    waterfall_result = run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids, toggles=toggles)
    timings["waterfall"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Waterfall done ({timings['waterfall']}s)")

    # --- Step 9: Connect to Sheets, read MIC ---
    logger.info("=" * 60)
    logger.info("PHASE 3: SUPPRESSION + BUDGET FITTING")
    logger.info("=" * 60)

    logger.info(f"[{_elapsed()}s] Connecting to Sheets...")
    gc = get_sheets_client()

    logger.info(f"[{_elapsed()}s] Reading MIC Campaign Calendar...")
    try:
        mic_df = read_campaign_calendar(gc)
        mic_status = f"OK — {len(mic_df)} rows"
    except Exception as e:
        mic_status = f"FAILED: {e}"
        logger.error(f"MIC read failed: {e}")
        mic_df = pd.DataFrame()

    # Pick a real campaign for budget fitting
    campaign = _pick_campaign_from_mic(mic_df)
    if campaign:
        logger.info(f"  Using campaign: {campaign['campaign_name']} "
                    f"(target: {campaign['budget_qty_mailed']:,}, CPP: ${campaign['cpp']:.2f})")
        campaign_type = campaign["campaign_type"]
        target_qty = campaign["budget_qty_mailed"]
        cpp = campaign["cpp"]
    else:
        logger.info("  No DM campaign found in MIC — using defaults for demo")
        campaign_type = "Appeal"
        target_qty = 35000
        cpp = 0.48

    # --- Tier 2 suppression ---
    logger.info(f"[{_elapsed()}s] Applying Tier 2 suppression...")
    t0 = time.time()
    waterfall_result, tier2_log = apply_tier2_suppression(
        waterfall_result, accounts_df, campaign_type=campaign_type
    )
    timings["tier2"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Tier 2 done ({timings['tier2']}s)")

    # --- Step 11: Build segment summary (post-Tier 2) ---
    segment_summary = build_segment_summary(waterfall_result)
    suppression_summary_df = build_suppression_summary(waterfall_result)

    # --- Step 12: Segment-level suppression (economic gates) ---
    # Note: without historical performance data, economic columns are blank.
    # Break-even and response rate floor won't fire until Phase 7/8 when
    # Campaign_Segment__c actuals are available. The logic is in place.
    t0 = time.time()
    segment_summary = apply_segment_level_suppression(segment_summary, cpp)
    timings["seg_suppression"] = round(time.time() - t0, 1)

    # --- Step 13: Budget-target fitting ---
    t0 = time.time()
    waterfall_result, segment_summary, fit_info = fit_to_budget(
        waterfall_result, target_qty, segment_summary,
        segment_overrides=segment_overrides,
    )
    timings["budget_fit"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Budget fit done ({timings['budget_fit']}s)")

    # --- Populate economics columns (CPP + Total Cost always; baseline-dependent cols when available) ---
    fit_col = "Budget Fit" if "Budget Fit" in segment_summary.columns else "Quantity"
    segment_summary["CPP"] = cpp
    segment_summary["Total Cost"] = segment_summary[fit_col].apply(
        lambda q: round(float(q) * cpp, 2) if q and str(q).replace('.','').isdigit() else ""
    )
    # Apply baseline economics if a baseline campaign was selected
    if baseline_appeal_code:
        logger.info(f"[{_elapsed()}s] Applying baseline from {baseline_appeal_code}...")
        from baseline_rollup import build_baseline_rollup, apply_baseline_to_summary
        baseline_df = build_baseline_rollup(gc, baseline_appeal_code)
        segment_summary = apply_baseline_to_summary(segment_summary, baseline_df, cpp)
    else:
        logger.info(f"[{_elapsed()}s] No baseline selected — economics history columns empty")
    logger.info(f"[{_elapsed()}s] Economics columns populated (CPP=${cpp:.2f})")

    # --- Ask Strings + Appeal Codes ---
    logger.info(f"[{_elapsed()}s] Computing ask strings...")
    t0 = time.time()
    ask_df = compute_ask_strings(waterfall_result, accounts_df)
    reply_tiers = classify_reply_copy_tier(waterfall_result, accounts_df)
    timings["ask_strings"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Ask strings done ({timings['ask_strings']}s)")

    # Appeal codes
    campaign_appeal_code = campaign.get("appeal_code", "") if campaign else ""
    if not campaign_appeal_code or len(campaign_appeal_code) < 5:
        campaign_appeal_code = "R2631TYRE"  # Fallback demo code
        logger.info(f"  No valid appeal code in MIC — using demo: {campaign_appeal_code}")

    t0 = time.time()
    codes_df = generate_appeal_codes(
        waterfall_result, accounts_df,
        campaign_appeal_code=campaign_appeal_code,
    )
    timings["appeal_codes"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Appeal codes done ({timings['appeal_codes']}s)")

    # Validate
    code_validation = validate_appeal_codes(codes_df)

    # Ask rounding validation: below $100 must be multiple of $5, at/above $100 must be multiple of $25
    rounding_ok = True
    if len(ask_df) > 0:
        for col in ["ask1", "ask2", "ask3"]:
            vals = ask_df[col].dropna()
            low = vals[vals < 100]
            high = vals[vals >= 100]
            bad_low = low[(low % 5 != 0) & (low > 0)]
            bad_high = high[(high % 25 != 0) & (high > 0)]
            if len(bad_low) > 0 or len(bad_high) > 0:
                rounding_ok = False
                if len(bad_low) > 0:
                    logger.warning(f"  {col}: {len(bad_low)} values <$100 not rounded to $5")
                if len(bad_high) > 0:
                    logger.warning(f"  {col}: {len(bad_high)} values >=$100 not rounded to $25")
        logger.info(f"  Ask rounding validation: {'PASS' if rounding_ok else 'FAIL'}")

    # --- Output Files ---
    logger.info(f"[{_elapsed()}s] Generating output files...")
    t0 = time.time()
    output = generate_output_files(
        waterfall_result, accounts_df, ask_df, reply_tiers, codes_df,
        campaign_code=campaign.get("appeal_code", "DIAG") if campaign else "DIAG",
        lane=campaign.get("lane", "Housefile") if campaign else "Housefile",
        holdout_pct=5.0,  # 5% holdout per spec
    )
    timings["output_files"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Output files done ({timings['output_files']}s)")

    for w in output["warnings"]:
        logger.warning(f"  {w}")

    # ZIP preservation validation
    printer_df = output["printer_df"]
    zip_ok = True
    if len(printer_df) > 0:
        zips = printer_df["ZIP"]
        non_empty_zips = zips[zips != ""]
        short_zips = non_empty_zips[non_empty_zips.str.len() < 5]
        if len(short_zips) > 0:
            zip_ok = False
            logger.warning(f"  ZIP validation FAIL: {len(short_zips)} ZIPs < 5 chars")
        else:
            logger.info(f"  ZIP validation PASS: {len(non_empty_zips):,} ZIPs, all >= 5 chars")

    # Verify no 15-char code in Printer File
    printer_has_15char = "InternalAppealCode" in printer_df.columns
    if printer_has_15char:
        logger.warning("  FAIL: InternalAppealCode column present in Printer File!")

    # Verify Matchback has 15-char code
    matchback_df = output["matchback_df"]
    matchback_has_15char = "InternalAppealCode" in matchback_df.columns and matchback_df["InternalAppealCode"].notna().any()

    # Suppression audit log
    audit_log = build_suppression_audit_log(waterfall_result, tier2_log)
    audit_csv = audit_log.to_csv(index=False)

    # --- Pipeline Write Recovery (Drive → Sheets → SF) ---
    logger.info(f"[{_elapsed()}s] Writing pipeline outputs (Drive → Sheets)...")
    t0 = time.time()
    pipeline = PipelineWriteRecovery()
    write_status = pipeline.execute_writes(
        gc,
        printer_csv=output["printer_csv"],
        matchback_csv=output["matchback_csv"],
        suppression_audit_csv=audit_csv,
        segment_summary=segment_summary,
        campaign_code=campaign.get("appeal_code", "DIAG") if campaign else "DIAG",
        campaign_appeal_code=campaign_appeal_code,
        lane=campaign.get("lane", "Housefile") if campaign else "Housefile",
        exceptions_csv=output.get("exceptions_csv", ""),
    )

    timings["pipeline_write"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Pipeline writes done ({timings['pipeline_write']}s)")

    drive_urls = write_status.get("drive_urls", {})
    audit_url = drive_urls.get("audit", "N/A")
    printer_url = drive_urls.get("printer", "N/A")
    matchback_url = drive_urls.get("matchback", "N/A")
    draft_status = "OK — via pipeline write" if write_status.get("sheets_write") == "success" else "FAILED"

    # Idempotency check: re-running approve_projection for same campaign_id
    # should replace (not duplicate) Segment Detail rows — tested by the upsert logic
    # in approve_projection which filters existing_df by campaign_id before appending.
    logger.info(f"  Pipeline write status: Drive={write_status.get('drive_write')}, "
                f"Sheets={write_status.get('sheets_write')}, "
                f"SF={write_status.get('salesforce_write')}")

    # --- Step 16: Build diagnostic outputs ---
    logger.info("-" * 40)
    logger.info("Building diagnostic outputs...")

    rfm_rf = build_rfm_crosstab_rf(rfm_df)
    rfm_rm = build_rfm_crosstab_rm(rfm_df)
    rfm_summary = build_rfm_summary(rfm_df)
    hpc_mrc = build_hpc_mrc_diagnostic(accounts_df)
    sustainer_summary, sustainer_spot = build_sustainer_diagnostic(accounts_df, sustainer_field_exists)
    staff_mgr = build_staff_manager_diagnostic(accounts_df)
    cornerstone = build_cornerstone_diagnostic(accounts_df, rfm_df)
    gate_results = evaluate_gate_criteria(accounts_df, rfm_df, sustainer_field_exists)

    # Cornerstone R-bucket gap note
    cs_ids = accounts_df.set_index("Id").index[
        accounts_df.set_index("Id").get("Cornerstone_Partner__c", pd.Series(False)) == True
    ]
    cs_rfm = rfm_df.loc[rfm_df.index.isin(cs_ids)]
    cs_r_dist = cs_rfm["R_bucket"].value_counts().sort_index()
    cornerstone_detail = pd.DataFrame({
        "R_Bucket": cs_r_dist.index,
        "Count": cs_r_dist.values,
    })
    cornerstone_detail.loc[len(cornerstone_detail)] = {
        "R_Bucket": "---",
        "Count": "NOTE: CS R2/R5 gap is upstream flag-population issue",
    }

    # Suppression totals for gate check
    pre_suppression = len(waterfall_result[waterfall_result["segment_code"] != ""])
    # Tier 1 suppressed = already excluded from waterfall assignments
    tier1_suppressed = (waterfall_result["suppression_reason"].str.startswith("Tier1")).sum()
    tier2_suppressed = (waterfall_result["suppression_reason"].str.startswith("Tier2")).sum()
    total_assigned_post_suppression = (
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ).sum()
    # "Pre-suppression universe" = assigned before Tier 2
    pre_tier2 = total_assigned_post_suppression + tier2_suppressed
    tier2_pct = (tier2_suppressed / pre_tier2 * 100) if pre_tier2 > 0 else 0

    outside_window = int(rfm_df["_outside_window"].sum()) if "_outside_window" in rfm_df.columns else 0

    # Metadata
    metadata = pd.DataFrame([
        {"Metric": "Run Timestamp", "Value": pd.Timestamp.now().isoformat()},
        {"Metric": "Total Accounts (Pass 1)", "Value": len(accounts_df)},
        {"Metric": "Total Opportunities 5yr (Pass 2)", "Value": len(opps_df)},
        {"Metric": "Total Opportunities 10yr (Pass 3)", "Value": "N/A (BQ cache)" if data_source == "bigquery" else len(cbnc_opps_df)},
        {"Metric": "CBNC Donors Detected", "Value": len(cbnc_ids)},
        {"Metric": "Tier 1 Suppressed", "Value": tier1_suppressed},
        {"Metric": "Tier 2 Suppressed", "Value": tier2_suppressed},
        {"Metric": f"Tier 2 % of Pre-Suppression", "Value": f"{tier2_pct:.1f}%"},
        {"Metric": "Total Assigned (post-suppression)", "Value": total_assigned_post_suppression},
        {"Metric": "Budget Target", "Value": target_qty},
        {"Metric": f"Budget Fit Pass", "Value": fit_info["pass"]},
        {"Metric": "Fitted Quantity", "Value": fit_info["fitted"]},
        {"Metric": "Trimmed", "Value": fit_info["trimmed"]},
        {"Metric": "Gap", "Value": fit_info.get("gap", 0)},
        {"Metric": "Campaign", "Value": campaign["campaign_name"] if campaign else "Default"},
        {"Metric": "CPP", "Value": f"${cpp:.2f}"},
        {"Metric": "Audit Log", "Value": audit_url},
        {"Metric": "MIC Status", "Value": mic_status},
        {"Metric": "Draft Tab", "Value": draft_status},
        {"Metric": "Ask Strings Computed", "Value": len(ask_df)},
        {"Metric": "Appeal Codes Generated", "Value": len(codes_df)},
        {"Metric": "Ask Rounding Valid", "Value": "PASS" if rounding_ok else "FAIL"},
        {"Metric": "Printer File Rows", "Value": output["printer_count"]},
        {"Metric": "Matchback File Rows", "Value": output["matchback_count"]},
        {"Metric": "Housefile Suppression Rows", "Value": output["suppression_count"]},
        {"Metric": "Holdout Count", "Value": output["holdout_count"]},
        {"Metric": "Excluded (DQ)", "Value": output.get("excluded_count", 0)},
        {"Metric": "Excluded Missing ID", "Value": output.get("excluded_missing", 0)},
        {"Metric": "Excluded Duplicate ID", "Value": output.get("excluded_duplicate", 0)},
        {"Metric": "ZIP Preservation", "Value": "PASS" if zip_ok else "FAIL"},
        {"Metric": "Printer File URL", "Value": printer_url},
        {"Metric": "Matchback File URL", "Value": matchback_url},
        {"Metric": "Exceptions File URL", "Value": drive_urls.get("exceptions", "N/A")},
        {"Metric": "Pipeline Drive Write", "Value": write_status.get("drive_write", "N/A")},
        {"Metric": "Pipeline Sheets Write", "Value": write_status.get("sheets_write", "N/A")},
        {"Metric": "Pipeline SF Write", "Value": write_status.get("salesforce_write", "N/A")},
    ])

    # Budget fit detail
    fit_detail = pd.DataFrame([
        {"Metric": "Full Universe", "Value": fit_info["full_universe"]},
        {"Metric": "Target", "Value": fit_info["target"]},
        {"Metric": "Pass Used", "Value": fit_info["pass"]},
        {"Metric": "Fitted Total", "Value": fit_info["fitted"]},
        {"Metric": "Total Trimmed", "Value": fit_info["trimmed"]},
        {"Metric": "Gap", "Value": fit_info.get("gap", 0)},
    ])
    if "trimmed_by_segment" in fit_info:
        for seg, count in fit_info["trimmed_by_segment"].items():
            fit_detail.loc[len(fit_detail)] = {"Metric": f"Trimmed: {seg}", "Value": count}

    # --- Step 17: Write diagnostic ---
    logger.info("Writing diagnostic output...")
    tabs = {
        "Segment_Summary": segment_summary,
        "Budget_Fit": fit_detail,
        "Suppression_Summary": suppression_summary_df,
        "RFM_RxF": rfm_rf,
        "RFM_RxM": rfm_rm,
        "RFM_Summary": rfm_summary,
        "HPC_MRC": hpc_mrc,
        "Sustainers": sustainer_summary,
        "Sustainer_SpotCheck": sustainer_spot,
        "Staff_Manager": staff_mgr,
        "Cornerstone": cornerstone,
        "Cornerstone_RFM": cornerstone_detail,
        "Appeal_Validation": code_validation,
        "SpotCheck": codes_df.sample(min(50, len(codes_df)), random_state=42) if len(codes_df) > 0 else pd.DataFrame(),
        "Gate_Results": gate_results,
        "Metadata": metadata,
    }
    logger.info(f"[{_elapsed()}s] Writing diagnostic sheet...")
    t0 = time.time()
    sheet_url = write_diagnostic(gc, tabs)
    timings["diagnostic_sheet"] = round(time.time() - t0, 1)
    logger.info(f"[{_elapsed()}s] Diagnostic sheet done ({timings['diagnostic_sheet']}s)")

    logger.info(f"[{_elapsed()}s] PIPELINE COMPLETE. Total: {_elapsed()}s")

    # --- Print results ---
    logger.info("=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)

    for _, row in gate_results.iterrows():
        status_icon = "PASS" if row["Status"] == "PASS" else "** " + row["Status"] + " **"
        logger.info(f"  [{status_icon}] {row['Gate']}: {row['Detail']}")

    logger.info("")
    logger.info("SEGMENT SUMMARY (post-suppression, post-fit):")
    total_mailable = 0
    for _, row in segment_summary.iterrows():
        qty = int(row.get("Budget Fit", row["Quantity"])) if "Budget Fit" in row else int(row["Quantity"])
        status = row.get("Status", "")
        logger.info(f"  {row['Segment Code']:6s} {row['Segment Name']:45s} {qty:>8,}  {status}")
        if status not in ("Below Budget Line",):
            total_mailable += qty
    logger.info(f"  {'':6s} {'TOTAL MAILABLE':45s} {total_mailable:>8,}")

    logger.info(f"\nSuppression:")
    for _, row in suppression_summary_df.iterrows():
        logger.info(f"  {row['Suppression Rule']:40s} {int(row['Count']):>8,}")

    logger.info(f"\nBudget fit: {fit_info['pass']} — "
                f"universe {fit_info['full_universe']:,} → fitted {fit_info['fitted']:,} "
                f"(target {target_qty:,}, trimmed {fit_info['trimmed']:,})")

    logger.info(f"Tier 2 suppression: {tier2_suppressed:,} ({tier2_pct:.1f}% of pre-suppression universe)")

    logger.info(f"\nPhase 4:")
    logger.info(f"  Ask strings: {len(ask_df):,} computed")
    logger.info(f"  Appeal codes: {len(codes_df):,} generated")
    logger.info(f"  Ask rounding: {'PASS' if rounding_ok else 'FAIL'}")
    for _, row in code_validation.iterrows():
        logger.info(f"  [{row['Status']}] {row['Check']}: {row['Detail']}")

    logger.info(f"\nPhase 5:")
    logger.info(f"  Printer File: {output['printer_count']:,} rows")
    logger.info(f"  Matchback File: {output['matchback_count']:,} rows")
    logger.info(f"  Housefile Suppression: {output['suppression_count']:,} rows")
    logger.info(f"  Holdout: {output['holdout_count']:,} donors excluded")
    excluded_count = output.get("excluded_count", 0)
    if excluded_count > 0:
        logger.info(f"  DQ Exclusions: {excluded_count:,} records "
                    f"({output.get('excluded_missing', 0):,} missing ID, "
                    f"{output.get('excluded_duplicate', 0):,} duplicate ID)")
        logger.info(f"  Exceptions file: {drive_urls.get('exceptions', 'N/A')}")
    logger.info(f"  ZIP preservation: {'PASS' if zip_ok else 'FAIL'}")
    logger.info(f"  15-char in Printer File: {'FAIL' if printer_has_15char else 'PASS (absent)'}")
    logger.info(f"  15-char in Matchback File: {'PASS' if matchback_has_15char else 'FAIL (missing)'}")
    logger.info(f"  Pipeline: Drive={write_status.get('drive_write')}, "
                f"Sheets={write_status.get('sheets_write')}, SF={write_status.get('salesforce_write')}")
    logger.info(f"  Printer: {printer_url}")
    logger.info(f"  Matchback: {matchback_url}")
    logger.info(f"  Audit log: {audit_url}")
    logger.info(f"  Diagnostic: {sheet_url}")

    return {
        "gate_results": gate_results.to_dict(orient="records"),
        "segment_summary": segment_summary.to_dict(orient="records"),
        "suppression_summary": suppression_summary_df.to_dict(orient="records"),
        "fit_info": fit_info,
        "sheet_url": sheet_url,
        "audit_url": audit_url,
        "printer_url": printer_url,
        "matchback_url": matchback_url,
        "write_status": write_status,
        "timings": timings,
        "counts": {
            "accounts": len(accounts_df),
            "tier1_suppressed": int(tier1_suppressed),
            "tier2_suppressed": int(tier2_suppressed),
            "tier2_pct": round(tier2_pct, 1),
            "total_mailable": total_mailable,
            "fitted": fit_info["fitted"],
            "trimmed": fit_info["trimmed"],
            "printer_rows": output["printer_count"],
            "matchback_rows": output["matchback_count"],
            "suppression_rows": output["suppression_count"],
            "holdout": output["holdout_count"],
            "zip_ok": zip_ok,
        },
    }


if __name__ == "__main__":
    result = run_diagnostic()
    all_pass = all(g["Status"] == "PASS" for g in result["gate_results"] if g["Gate"] != "RFM Distribution Check")
    sys.exit(0 if all_pass else 1)
