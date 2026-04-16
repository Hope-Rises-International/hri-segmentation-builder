"""Pipeline orchestrator: Phase 1 diagnostic + Phase 2 waterfall assignment."""

import logging
import sys
import time

from salesforce_client import (
    connect_salesforce, fetch_accounts, fetch_opportunities,
    fetch_opportunities_cbnc, probe_sustainer_field,
)
from sheets_client import get_sheets_client, read_campaign_calendar, ensure_draft_tab, write_diagnostic, write_draft_tab
from rfm_engine import compute_rfm
from lifecycle import compute_lifecycle
from cbnc import detect_cbnc
from waterfall_engine import run_waterfall, build_segment_summary, build_suppression_summary
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_diagnostic() -> dict:
    """Execute the full pipeline: SF pull → RFM → lifecycle → CBNC → waterfall → output.

    Returns dict with gate_results, waterfall summary, and sheet_url.
    """
    timings = {}

    # --- Step 1: Connect to Salesforce ---
    logger.info("=" * 60)
    logger.info("SEGMENTATION BUILDER — Phase 1 + Phase 2")
    logger.info("=" * 60)

    t0 = time.time()
    sf = connect_salesforce()
    timings["sf_connect"] = round(time.time() - t0, 1)
    logger.info(f"Salesforce connected ({timings['sf_connect']}s)")

    # --- Step 2: Probe npsp__Sustainer__c ---
    sustainer_field_exists = probe_sustainer_field(sf)
    logger.info(f"npsp__Sustainer__c exists: {sustainer_field_exists}")

    # --- Step 3: Pass 1 — Fetch accounts ---
    t0 = time.time()
    accounts_df = fetch_accounts(sf)
    timings["pass1_accounts"] = round(time.time() - t0, 1)

    # --- Step 4: Pass 2 — Fetch opportunities (5-year for RFM) ---
    t0 = time.time()
    opps_df = fetch_opportunities(sf)
    timings["pass2_opps"] = round(time.time() - t0, 1)

    # --- Step 5: Pass 3 — Fetch opportunities (10-year for CBNC) ---
    t0 = time.time()
    cbnc_opps_df = fetch_opportunities_cbnc(sf)
    timings["pass3_cbnc"] = round(time.time() - t0, 1)

    # --- Step 6: Compute RFM ---
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    timings["rfm_compute"] = round(time.time() - t0, 1)
    logger.info(f"RFM computed ({timings['rfm_compute']}s)")

    # --- Step 7: Compute Lifecycle ---
    t0 = time.time()
    lifecycle = compute_lifecycle(accounts_df)
    timings["lifecycle"] = round(time.time() - t0, 1)

    # --- Step 8: Detect CBNC ---
    t0 = time.time()
    cbnc_ids = detect_cbnc(cbnc_opps_df)
    timings["cbnc"] = round(time.time() - t0, 1)

    # --- Step 9: Run Waterfall ---
    logger.info("=" * 60)
    logger.info("PHASE 2: WATERFALL ASSIGNMENT")
    logger.info("=" * 60)
    t0 = time.time()
    waterfall_result = run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids)
    timings["waterfall"] = round(time.time() - t0, 1)

    # Build segment summary for Draft tab
    segment_summary = build_segment_summary(waterfall_result)
    suppression_summary = build_suppression_summary(waterfall_result)

    # --- Step 10: Connect to Sheets ---
    logger.info("-" * 40)
    logger.info("Connecting to Google Sheets...")
    gc = get_sheets_client()

    # Read MIC Campaign Calendar (connectivity check)
    logger.info("Reading MIC Campaign Calendar...")
    t0 = time.time()
    try:
        mic_df = read_campaign_calendar(gc)
        mic_status = f"OK — {len(mic_df)} rows"
    except Exception as e:
        mic_status = f"FAILED: {e}"
        logger.error(f"MIC read failed: {e}")
        mic_df = pd.DataFrame()
    timings["mic_read"] = round(time.time() - t0, 1)

    # Write segment summary to Draft tab
    logger.info("Writing segment summary to Draft tab...")
    try:
        write_draft_tab(gc, segment_summary)
        draft_status = f"OK — {len(segment_summary)} segment rows"
    except Exception as e:
        draft_status = f"FAILED: {e}"
        logger.error(f"Draft tab write failed: {e}")

    # --- Step 11: Build diagnostic outputs ---
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

    # Cornerstone R-bucket gap diagnostic (noted in Phase 1 review)
    cs_ids = accounts_df.set_index("Id").index[
        accounts_df.set_index("Id").get("Cornerstone_Partner__c", pd.Series(False)) == True
    ]
    cs_rfm = rfm_df.loc[rfm_df.index.isin(cs_ids)]
    cs_r_dist = cs_rfm["R_bucket"].value_counts().sort_index()
    cs_note = "NOTE: Cornerstone R2/R5 gap is a flag-population issue upstream, not a segmentation issue."
    cornerstone_detail = pd.DataFrame({
        "R_Bucket": cs_r_dist.index,
        "Count": cs_r_dist.values,
    })
    cornerstone_detail.loc[len(cornerstone_detail)] = {"R_Bucket": "---", "Count": cs_note}

    # Metadata
    outside_window = int(rfm_df["_outside_window"].sum()) if "_outside_window" in rfm_df.columns else 0
    metadata = pd.DataFrame([
        {"Metric": "Run Timestamp", "Value": pd.Timestamp.now().isoformat()},
        {"Metric": "Total Accounts (Pass 1)", "Value": len(accounts_df)},
        {"Metric": "Total Opportunities 5yr (Pass 2)", "Value": len(opps_df)},
        {"Metric": "Total Opportunities 10yr (Pass 3)", "Value": len(cbnc_opps_df)},
        {"Metric": "Accounts Outside 5yr Window", "Value": outside_window},
        {"Metric": "CBNC Donors Detected", "Value": len(cbnc_ids)},
        {"Metric": "MIC Campaign Calendar", "Value": mic_status},
        {"Metric": "MIC Draft Tab", "Value": draft_status},
        {"Metric": "SF Connect Time (s)", "Value": timings.get("sf_connect", "")},
        {"Metric": "Pass 1 Time (s)", "Value": timings.get("pass1_accounts", "")},
        {"Metric": "Pass 2 Time (s)", "Value": timings.get("pass2_opps", "")},
        {"Metric": "Pass 3 CBNC Time (s)", "Value": timings.get("pass3_cbnc", "")},
        {"Metric": "RFM Compute Time (s)", "Value": timings.get("rfm_compute", "")},
        {"Metric": "Lifecycle Time (s)", "Value": timings.get("lifecycle", "")},
        {"Metric": "CBNC Detect Time (s)", "Value": timings.get("cbnc", "")},
        {"Metric": "Waterfall Time (s)", "Value": timings.get("waterfall", "")},
    ])

    # --- Step 12: Write diagnostic ---
    logger.info("Writing diagnostic output...")
    tabs = {
        "Segment_Summary": segment_summary,
        "Suppression_Summary": suppression_summary,
        "RFM_RxF": rfm_rf,
        "RFM_RxM": rfm_rm,
        "RFM_Summary": rfm_summary,
        "HPC_MRC": hpc_mrc,
        "Sustainers": sustainer_summary,
        "Sustainer_SpotCheck": sustainer_spot,
        "Staff_Manager": staff_mgr,
        "Cornerstone": cornerstone,
        "Cornerstone_RFM": cornerstone_detail,
        "Gate_Results": gate_results,
        "Metadata": metadata,
    }
    sheet_url = write_diagnostic(gc, tabs)

    # --- Step 13: Print results ---
    logger.info("=" * 60)
    logger.info("GATE CRITERIA RESULTS")
    logger.info("=" * 60)
    for _, row in gate_results.iterrows():
        status_icon = "PASS" if row["Status"] == "PASS" else "** " + row["Status"] + " **"
        logger.info(f"  [{status_icon}] {row['Gate']}: {row['Detail']}")

    logger.info("=" * 60)
    logger.info("SEGMENT SUMMARY")
    logger.info("=" * 60)
    total_mailable = 0
    for _, row in segment_summary.iterrows():
        logger.info(f"  {row['Segment Code']:6s} {row['Segment Name']:45s} {int(row['Quantity']):>8,}")
        total_mailable += int(row["Quantity"])
    logger.info(f"  {'':6s} {'TOTAL MAILABLE':45s} {total_mailable:>8,}")

    logger.info(f"\nSuppression summary:")
    for _, row in suppression_summary.iterrows():
        logger.info(f"  {row['Suppression Rule']:40s} {int(row['Count']):>8,}")

    logger.info(f"\nDiagnostic output: {sheet_url}")

    return {
        "gate_results": gate_results.to_dict(orient="records"),
        "segment_summary": segment_summary.to_dict(orient="records"),
        "suppression_summary": suppression_summary.to_dict(orient="records"),
        "sheet_url": sheet_url,
        "timings": timings,
        "counts": {
            "accounts": len(accounts_df),
            "opportunities_5yr": len(opps_df),
            "opportunities_10yr": len(cbnc_opps_df),
            "cbnc_donors": len(cbnc_ids),
            "outside_window": outside_window,
            "total_mailable": total_mailable,
        },
    }


if __name__ == "__main__":
    result = run_diagnostic()
    all_pass = all(g["Status"] == "PASS" for g in result["gate_results"] if g["Gate"] != "RFM Distribution Check")
    sys.exit(0 if all_pass else 1)
