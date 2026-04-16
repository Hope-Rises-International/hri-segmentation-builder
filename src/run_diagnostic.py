"""Phase 1 diagnostic orchestrator: wires SF pull, RFM, MIC, and diagnostic output."""

import logging
import sys
import time

from salesforce_client import connect_salesforce, fetch_accounts, fetch_opportunities, probe_sustainer_field
from sheets_client import get_sheets_client, read_campaign_calendar, ensure_draft_tab, write_diagnostic
from rfm_engine import compute_rfm
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
    """Execute the full Phase 1 diagnostic pipeline.

    Returns dict with gate_results and sheet_url.
    """
    timings = {}

    # --- Step 1: Connect to Salesforce ---
    logger.info("=" * 60)
    logger.info("PHASE 1 DIAGNOSTIC — Segmentation Builder")
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

    # --- Step 4: Pass 2 — Fetch opportunities ---
    t0 = time.time()
    opps_df = fetch_opportunities(sf)
    timings["pass2_opps"] = round(time.time() - t0, 1)

    # --- Step 5: Compute RFM ---
    t0 = time.time()
    rfm_df = compute_rfm(accounts_df, opps_df)
    timings["rfm_compute"] = round(time.time() - t0, 1)
    logger.info(f"RFM computed ({timings['rfm_compute']}s)")

    # --- Step 6: Connect to Sheets, read MIC, create Draft tab ---
    logger.info("-" * 40)
    logger.info("Connecting to Google Sheets...")
    gc = get_sheets_client()

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

    logger.info("Creating Draft tab on MIC...")
    try:
        ensure_draft_tab(gc)
        draft_status = "OK — header written"
    except Exception as e:
        draft_status = f"FAILED: {e}"
        logger.error(f"Draft tab creation failed: {e}")

    # --- Step 7: Build all diagnostic outputs ---
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

    # Metadata tab
    outside_window = int(rfm_df["_outside_window"].sum()) if "_outside_window" in rfm_df.columns else 0
    metadata = pd.DataFrame([
        {"Metric": "Run Timestamp", "Value": pd.Timestamp.now().isoformat()},
        {"Metric": "Total Accounts (Pass 1)", "Value": len(accounts_df)},
        {"Metric": "Total Opportunities (Pass 2)", "Value": len(opps_df)},
        {"Metric": "Accounts Outside 5yr Window", "Value": outside_window},
        {"Metric": "MIC Campaign Calendar", "Value": mic_status},
        {"Metric": "MIC Draft Tab", "Value": draft_status},
        {"Metric": "SF Connect Time (s)", "Value": timings.get("sf_connect", "")},
        {"Metric": "Pass 1 Time (s)", "Value": timings.get("pass1_accounts", "")},
        {"Metric": "Pass 2 Time (s)", "Value": timings.get("pass2_opps", "")},
        {"Metric": "RFM Compute Time (s)", "Value": timings.get("rfm_compute", "")},
    ])

    # --- Step 8: Write diagnostic sheet ---
    logger.info("Writing diagnostic sheet...")
    tabs = {
        "RFM_RxF": rfm_rf,
        "RFM_RxM": rfm_rm,
        "RFM_Summary": rfm_summary,
        "HPC_MRC": hpc_mrc,
        "Sustainers": sustainer_summary,
        "Sustainer_SpotCheck": sustainer_spot,
        "Staff_Manager": staff_mgr,
        "Cornerstone": cornerstone,
        "Gate_Results": gate_results,
        "Metadata": metadata,
    }
    sheet_url = write_diagnostic(gc, tabs)

    # --- Step 9: Print gate results ---
    logger.info("=" * 60)
    logger.info("GATE CRITERIA RESULTS")
    logger.info("=" * 60)
    for _, row in gate_results.iterrows():
        status_icon = "PASS" if row["Status"] == "PASS" else "** " + row["Status"] + " **"
        logger.info(f"  [{status_icon}] {row['Gate']}: {row['Detail']}")
    logger.info(f"\nDiagnostic sheet: {sheet_url}")

    return {
        "gate_results": gate_results.to_dict(orient="records"),
        "sheet_url": sheet_url,
        "timings": timings,
        "counts": {
            "accounts": len(accounts_df),
            "opportunities": len(opps_df),
            "outside_window": outside_window,
        },
    }


if __name__ == "__main__":
    result = run_diagnostic()
    all_pass = all(g["Status"] == "PASS" for g in result["gate_results"] if g["Gate"] != "RFM Distribution Check")
    sys.exit(0 if all_pass else 1)
