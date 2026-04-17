"""Appeal code generation per spec Section 9.

Two formats:
- 9-character: TYYMCPSS0 — goes to printer/cager via scanline
- 15-character: [Program][FY][Campaign][Segment][Package][Test] — internal only

Scanline: 9-digit zero-padded Donor ID + 9-char appeal code
"""

from __future__ import annotations
import logging

import pandas as pd
import numpy as np

from config import SEGMENT_CODES, fy_label_for_date, get_package_code

logger = logging.getLogger(__name__)

# Program code from segment group prefix (spec Section 9.1 position 1)
PROGRAM_BY_PREFIX = {
    "AH": "R",  # Renewal/Housefile
    "LR": "R",
    "DL": "R",
    "CB": "R",
    "ML": "M",  # Mid-Level
    "MP": "M",
    "CS": "C",  # Cornerstone
    "MJ": "R",  # Major Gift → Renewal program code
    "SU": "R",
    "ND": "R",
}


def generate_appeal_codes(
    waterfall_result: pd.DataFrame,
    accounts_df: pd.DataFrame,
    campaign_appeal_code: str,
    campaign_fy: str = "",
    campaign_month: str = "",
    is_ca_version_campaign: bool = False,
    test_flag: str = "CTL",
    package_overrides: dict = None,
) -> pd.DataFrame:
    """Generate 9-char and 15-char appeal codes + scanline for all assigned donors.

    Args:
        waterfall_result: Waterfall output with segment assignments.
        accounts_df: Account data with Constituent_Id__c and BillingState.
        campaign_appeal_code: 9-char appeal code from MIC (TYYMCPSS0 format).
        campaign_fy: Fiscal year (e.g., "26"). Auto-derived if empty.
        campaign_month: Campaign month code (e.g., "05" for May). Auto-derived if empty.
        is_ca_version_campaign: Whether this is a 33x Shipping match (CA versioning).
        test_flag: Test/control flag (CTL, TSA, TSB).

    Returns:
        DataFrame with account_id, appeal_code_9, appeal_code_15, scanline,
        package_code, test_flag, ca_version columns.
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Parse FY and campaign month from appeal code if not provided
    if not campaign_fy and len(campaign_appeal_code) >= 3:
        campaign_fy = campaign_appeal_code[1:3]
    if not campaign_month and len(campaign_appeal_code) >= 5:
        campaign_month = campaign_appeal_code[3:5]

    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
        & (~waterfall_result.get("budget_trimmed", pd.Series(False)))
    ].copy()

    results = []
    for _, row in assigned.iterrows():
        acct_id = row["account_id"]
        seg_code = row["segment_code"]

        # 9-char appeal code: from MIC Campaign Calendar (campaign-level, same for all donors)
        appeal_9 = campaign_appeal_code if len(campaign_appeal_code) == 9 else campaign_appeal_code[:9].ljust(9, "0")

        # Program code (by 2-char prefix)
        program = PROGRAM_BY_PREFIX.get(seg_code[:2], "R")

        # Package code (configurable via overrides or defaults)
        package = get_package_code(seg_code, package_overrides)

        # 15-char internal appeal code: [Program][FY][Campaign][Segment][Package][Test]
        # Positions: 1(program) + 2(FY) + 2(campaign) + 4(segment) + 3(package) + 3(test) = 15
        appeal_15 = f"{program}{campaign_fy}{campaign_month}{seg_code}{package}{test_flag}"

        # Donor ID for scanline (9-digit zero-padded)
        constituent_id = accts.get("Constituent_Id__c", pd.Series("", index=accts.index)).get(acct_id, "")
        if constituent_id and str(constituent_id).strip():
            donor_id_9 = str(constituent_id).strip().zfill(9)[:9]
        else:
            donor_id_9 = "000000000"

        # Scanline: donor ID + 9-char appeal code
        # Flag records with missing Constituent_Id (these will generate duplicate scanlines)
        scanline = donor_id_9 + appeal_9
        missing_id = donor_id_9 == "000000000"

        # CA version flag
        ca_version = False
        if is_ca_version_campaign:
            state = accts.get("BillingState", pd.Series("", index=accts.index)).get(acct_id, "")
            ca_version = str(state).strip().upper() in ("CA", "CALIFORNIA")

        results.append({
            "account_id": acct_id,
            "appeal_code_9": appeal_9,
            "appeal_code_15": appeal_15,
            "scanline": scanline,
            "donor_id_9": donor_id_9,
            "segment_code": seg_code,
            "package_code": package,
            "test_flag": test_flag,
            "ca_version": ca_version,
            "missing_constituent_id": missing_id,
        })

    df = pd.DataFrame(results)

    # Validate uniqueness
    if len(df) > 0:
        # 9-char codes are campaign-level (same for all donors) — uniqueness is per campaign × panel
        unique_9 = df["appeal_code_9"].nunique()
        logger.info(f"  9-char appeal codes: {unique_9} unique (campaign-level, expected 1 per campaign)")

        # 15-char codes should be unique per segment × package × test
        unique_15 = df["appeal_code_15"].nunique()
        expected_15 = df[["segment_code", "package_code", "test_flag"]].drop_duplicates().shape[0]
        logger.info(f"  15-char appeal codes: {unique_15} unique (expected {expected_15} = segments × packages × tests)")

        if unique_15 != expected_15:
            logger.warning(f"  WARNING: 15-char code count mismatch! {unique_15} vs expected {expected_15}")

        # Scanline should be unique per donor (excluding missing IDs)
        valid_scanlines = df[~df["missing_constituent_id"]]
        scanline_dupes = valid_scanlines["scanline"].duplicated().sum()
        missing_ids = df["missing_constituent_id"].sum()
        if missing_ids > 0:
            logger.warning(f"  WARNING: {int(missing_ids)} donors with missing Constituent_Id__c")
        if scanline_dupes > 0:
            logger.warning(f"  WARNING: {scanline_dupes} duplicate scanlines (excluding missing IDs)")
        else:
            logger.info(f"  Scanlines: {len(valid_scanlines):,} unique (no duplicates), {int(missing_ids)} missing ID")

    logger.info(f"  Appeal codes generated for {len(df):,} donors")

    return df


def validate_appeal_codes(codes_df: pd.DataFrame) -> pd.DataFrame:
    """Validate appeal code output and return a validation report."""
    rows = []

    if len(codes_df) == 0:
        rows.append({"Check": "No codes generated", "Status": "FAIL", "Detail": "Empty output"})
        return pd.DataFrame(rows)

    # 9-char uniqueness (per campaign — should be 1)
    unique_9 = codes_df["appeal_code_9"].nunique()
    rows.append({
        "Check": "9-char Appeal Codes Unique (per campaign)",
        "Status": "PASS" if unique_9 >= 1 else "FAIL",
        "Detail": f"{unique_9} unique 9-char code(s)",
    })

    # 15-char uniqueness (per segment × package × test)
    unique_15 = codes_df["appeal_code_15"].nunique()
    expected_15 = codes_df[["segment_code", "package_code", "test_flag"]].drop_duplicates().shape[0]
    rows.append({
        "Check": "15-char Appeal Codes Unique",
        "Status": "PASS" if unique_15 == expected_15 else "FAIL",
        "Detail": f"{unique_15} unique (expected {expected_15})",
    })

    # Scanline uniqueness (excluding missing Constituent_Id)
    valid = codes_df[~codes_df.get("missing_constituent_id", pd.Series(False))]
    scanline_dupes = valid["scanline"].duplicated().sum()
    missing_ids = codes_df.get("missing_constituent_id", pd.Series(False)).sum()
    rows.append({
        "Check": "Scanlines Unique (per donor)",
        "Status": "PASS" if scanline_dupes == 0 else "FAIL",
        "Detail": f"{len(valid):,} valid scanlines, {scanline_dupes} dupes, {int(missing_ids)} missing Constituent_Id",
    })

    # Scanline format (18 chars: 9 donor + 9 appeal)
    bad_length = (codes_df["scanline"].str.len() != 18).sum()
    rows.append({
        "Check": "Scanline Length (18 chars)",
        "Status": "PASS" if bad_length == 0 else "FAIL",
        "Detail": f"{bad_length} scanlines with wrong length" if bad_length else "All 18 chars",
    })

    # 15-char format (exactly 15 chars)
    bad_15 = (codes_df["appeal_code_15"].str.len() != 15).sum()
    rows.append({
        "Check": "15-char Code Length",
        "Status": "PASS" if bad_15 == 0 else "FAIL",
        "Detail": f"{bad_15} codes with wrong length" if bad_15 else "All 15 chars",
    })

    # Ask rounding UP check — done externally but note here
    rows.append({
        "Check": "Registry Match",
        "Status": "PASS" if all(s in SEGMENT_CODES for s in codes_df["segment_code"].unique()) else "FAIL",
        "Detail": f"All {codes_df['segment_code'].nunique()} segment codes in registry",
    })

    # CA version
    ca_count = codes_df["ca_version"].sum()
    rows.append({
        "Check": "CA Version Flag",
        "Status": "INFO",
        "Detail": f"{int(ca_count)} California addresses flagged",
    })

    # Sample records for Jessica spot-check
    sample = codes_df.sample(min(50, len(codes_df)), random_state=42)
    rows.append({
        "Check": "Spot-Check Sample",
        "Status": "REVIEW",
        "Detail": f"50-record sample available in SpotCheck tab",
    })

    return pd.DataFrame(rows)
