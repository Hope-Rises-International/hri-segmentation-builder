"""Appeal code generation per spec Section 9.

Two formats:
- 9-character: TYYMCPSS0 — goes to printer/cager via scanline
- 15-character: [Program][FY][Campaign][Segment][Package][Test] — internal only

ALM Scanline (21 chars, two literal spaces):
    "<DonorID:9> <CampaignAppealCode:9> <CheckDigit:1>"

Check digit algorithm — operates on the 18-char DonorID+AppealCode
concatenation (no spaces, no CD yet):
  1. Treat the 18 characters as 18 individual values.
  2. Replace alpha chars per CHECK_DIGIT_CONVERSION; numerics keep value.
  3. Assign alternating weights 1,2,1,2,... (1-indexed positions).
  4. Multiply each value by its weight (18 products).
  5. For each product: if > 9, subtract 9; else keep.
  6. Sum the 18 step-5 values.
  7. CD = (10 - (sum mod 10)) mod 10
"""

from __future__ import annotations
import logging

import pandas as pd
import numpy as np

from config import SEGMENT_CODES, fy_label_for_date, get_package_code

logger = logging.getLogger(__name__)

# ALM check-digit conversion table — alpha → numeric.
# Numerics keep their integer value; any other char is an error.
CHECK_DIGIT_CONVERSION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8, "I": 9,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "O": 6, "P": 7, "Q": 8, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}


def compute_check_digit(scanline_18: str) -> int:
    """Compute the ALM check digit for an 18-char DonorID+AppealCode string.

    See module docstring for the 7-step spec. Caller must pass exactly 18
    alphanumeric characters; any other length is a bug, raise.
    """
    if len(scanline_18) != 18:
        raise ValueError(f"check digit input must be 18 chars, got {len(scanline_18)}: {scanline_18!r}")
    total = 0
    for i, ch in enumerate(scanline_18):
        if ch.isdigit():
            value = int(ch)
        else:
            up = ch.upper()
            if up not in CHECK_DIGIT_CONVERSION:
                raise ValueError(f"unsupported check-digit char {ch!r} at pos {i+1} in {scanline_18!r}")
            value = CHECK_DIGIT_CONVERSION[up]
        weight = 1 if (i % 2 == 0) else 2   # positions 1,3,5,... get weight 1
        product = value * weight
        if product > 9:
            product -= 9
        total += product
    return (10 - (total % 10)) % 10


def format_scanline(donor_id_9: str, appeal_code_9: str) -> str:
    """Return the full 21-char ALM scanline:
        '<DonorID> <CampaignAppealCode> <CheckDigit>'
    """
    s18 = f"{donor_id_9}{appeal_code_9}"
    cd = compute_check_digit(s18)
    return f"{donor_id_9} {appeal_code_9} {cd}"


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

    # Include quantity_reduced records (they go to Matchback) but not budget_trimmed
    # (pass-2 trim or operator exclude — those are dropped entirely)
    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
        & (~waterfall_result.get("budget_trimmed", pd.Series(False)))
    ].copy()
    # Pass the quantity_reduced flag through so output_files can filter Printer
    if "quantity_reduced" in waterfall_result.columns:
        assigned["quantity_reduced"] = waterfall_result.loc[assigned.index, "quantity_reduced"].fillna(False)
    else:
        assigned["quantity_reduced"] = False

    results = []
    for _, row in assigned.iterrows():
        acct_id = row["account_id"]
        seg_code = row["segment_code"]

        # 9-char appeal code: <CampaignPrefix:5><SegmentCode:4>.
        # Positions 1-5 are campaign-level (e.g. "A2651"); positions 6-9
        # are the HRI segment (AH01, CS01, etc) so Aegis can attribute
        # returned gifts to a segment from the scanline alone, without
        # joining to Matchback. Spec §9 calls for segment in pos 6-9 —
        # earlier implementation zero-padded as "A26510000" and dropped
        # the segment signal.
        campaign_prefix_5 = (campaign_appeal_code or "")[:5].ljust(5, "0")
        appeal_9 = f"{campaign_prefix_5}{seg_code}"

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

        # Scanline: full 21-char ALM format with check digit.
        # Flag records with missing Constituent_Id (these will collide on
        # the scanline-9 prefix even though the CD/appeal differ).
        scanline = format_scanline(donor_id_9, appeal_9)
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
            "quantity_reduced": bool(row.get("quantity_reduced", False)),
        })

    df = pd.DataFrame(results)

    # Validate uniqueness
    if len(df) > 0:
        # 9-char codes are now segment-aware: <campaign:5><segment:4>.
        # Distinct count should equal the segment count (1 per segment per
        # campaign), not 1 per campaign as in the old zero-padded format.
        unique_9 = df["appeal_code_9"].nunique()
        expected_9 = df["segment_code"].nunique()
        if unique_9 == expected_9:
            logger.info(f"  9-char appeal codes: {unique_9} unique (one per segment, expected {expected_9})")
        else:
            logger.warning(f"  WARNING: 9-char code count {unique_9} != expected segment count {expected_9}")

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

    # 9-char codes are now segment-encoded (<campaign:5><segment:4>).
    # Distinct count must equal distinct segment count.
    unique_9 = codes_df["appeal_code_9"].nunique()
    expected_9 = codes_df["segment_code"].nunique()
    rows.append({
        "Check": "9-char Appeal Codes Unique (one per segment)",
        "Status": "PASS" if unique_9 == expected_9 else "FAIL",
        "Detail": f"{unique_9} unique 9-char codes (expected {expected_9} = distinct segments)",
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

    # Scanline format: 21 chars total — '<DonorID:9> <AppealCode:9> <CD:1>'
    bad_length = (codes_df["scanline"].str.len() != 21).sum()
    rows.append({
        "Check": "Scanline Length (21 chars, ALM format)",
        "Status": "PASS" if bad_length == 0 else "FAIL",
        "Detail": f"{bad_length} scanlines with wrong length" if bad_length else "All 21 chars",
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
