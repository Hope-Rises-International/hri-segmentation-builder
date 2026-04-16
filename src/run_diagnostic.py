"""Pipeline orchestrator: Phases 1-4 (SF pull → RFM → waterfall → suppression → fitting → ask strings → appeal codes)."""

import logging
import sys
import time

from salesforce_client import (
    connect_salesforce, fetch_accounts, fetch_opportunities,
    fetch_opportunities_cbnc, probe_sustainer_field,
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


def run_diagnostic() -> dict:
    """Execute the full pipeline: SF → RFM → lifecycle → CBNC → waterfall → suppression → fitting."""
    timings = {}

    # --- Step 1: Connect to Salesforce ---
    logger.info("=" * 60)
    logger.info("SEGMENTATION BUILDER — Phase 1 + 2 + 3")
    logger.info("=" * 60)

    t0 = time.time()
    sf = connect_salesforce()
    timings["sf_connect"] = round(time.time() - t0, 1)
    logger.info(f"Salesforce connected ({timings['sf_connect']}s)")

    sustainer_field_exists = probe_sustainer_field(sf)

    # --- Step 2-4: Fetch data ---
    t0 = time.time()
    accounts_df = fetch_accounts(sf)
    timings["pass1"] = round(time.time() - t0, 1)

    t0 = time.time()
    opps_df = fetch_opportunities(sf)
    timings["pass2"] = round(time.time() - t0, 1)

    t0 = time.time()
    cbnc_opps_df = fetch_opportunities_cbnc(sf)
    timings["pass3"] = round(time.time() - t0, 1)

    # --- Step 5-7: Compute RFM, lifecycle, CBNC ---
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    timings["rfm"] = round(time.time() - t0, 1)

    t0 = time.time()
    lifecycle = compute_lifecycle(accounts_df)
    timings["lifecycle"] = round(time.time() - t0, 1)

    t0 = time.time()
    cbnc_ids = detect_cbnc(cbnc_opps_df)
    timings["cbnc"] = round(time.time() - t0, 1)

    # --- Step 8: Waterfall assignment ---
    logger.info("=" * 60)
    logger.info("PHASE 2: WATERFALL ASSIGNMENT")
    logger.info("=" * 60)
    t0 = time.time()
    waterfall_result = run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids)
    timings["waterfall"] = round(time.time() - t0, 1)

    # --- Step 9: Connect to Sheets, read MIC ---
    logger.info("=" * 60)
    logger.info("PHASE 3: SUPPRESSION + BUDGET FITTING")
    logger.info("=" * 60)

    gc = get_sheets_client()

    logger.info("Reading MIC Campaign Calendar...")
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

    # --- Step 10: Tier 2 suppression ---
    t0 = time.time()
    waterfall_result, tier2_log = apply_tier2_suppression(
        waterfall_result, accounts_df, campaign_type=campaign_type
    )
    timings["tier2"] = round(time.time() - t0, 1)

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
        waterfall_result, target_qty, segment_summary
    )
    timings["budget_fit"] = round(time.time() - t0, 1)

    # --- Step 14: Phase 4 — Ask Strings + Appeal Codes ---
    logger.info("=" * 60)
    logger.info("PHASE 4: ASK STRINGS + APPEAL CODES")
    logger.info("=" * 60)

    t0 = time.time()
    ask_df = compute_ask_strings(waterfall_result, accounts_df)
    reply_tiers = classify_reply_copy_tier(waterfall_result, accounts_df)
    timings["ask_strings"] = round(time.time() - t0, 1)

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

    # --- Step 15: Suppression audit log ---
    audit_log = build_suppression_audit_log(waterfall_result, tier2_log)

    # Upload audit log CSV to Drive
    logger.info("Uploading suppression audit log to Drive...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_csv = audit_log.to_csv(index=False)
    try:
        audit_url = upload_csv_to_drive(
            gc, f"suppression_audit_{timestamp}.csv", audit_csv
        )
    except Exception as e:
        audit_url = f"FAILED: {e}"
        logger.error(f"Audit log upload failed: {e}")

    # --- Step 15: Write Draft tab ---
    logger.info("Writing segment summary to Draft tab...")
    try:
        write_draft_tab(gc, segment_summary)
        draft_status = f"OK — {len(segment_summary)} segment rows"
    except Exception as e:
        draft_status = f"FAILED: {e}"
        logger.error(f"Draft tab write failed: {e}")

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
        {"Metric": "Total Opportunities 10yr (Pass 3)", "Value": len(cbnc_opps_df)},
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
    sheet_url = write_diagnostic(gc, tabs)

    # --- Step 18: Print results ---
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

    logger.info(f"\nAudit log: {audit_url}")
    logger.info(f"Diagnostic: {sheet_url}")

    return {
        "gate_results": gate_results.to_dict(orient="records"),
        "segment_summary": segment_summary.to_dict(orient="records"),
        "suppression_summary": suppression_summary_df.to_dict(orient="records"),
        "fit_info": fit_info,
        "sheet_url": sheet_url,
        "audit_url": audit_url,
        "timings": timings,
        "counts": {
            "accounts": len(accounts_df),
            "tier1_suppressed": int(tier1_suppressed),
            "tier2_suppressed": int(tier2_suppressed),
            "tier2_pct": round(tier2_pct, 1),
            "total_mailable": total_mailable,
            "fitted": fit_info["fitted"],
            "trimmed": fit_info["trimmed"],
        },
    }


if __name__ == "__main__":
    result = run_diagnostic()
    all_pass = all(g["Status"] == "PASS" for g in result["gate_results"] if g["Gate"] != "RFM Distribution Check")
    sys.exit(0 if all_pass else 1)
