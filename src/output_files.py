"""Output file generation per spec Section 10.

Produces:
1. Printer File CSV — for VeraData/lettershop (no 15-char codes)
2. Internal Matchback File CSV — for HRI (all codes + analyst fields)
3. Housefile Suppression File CSV — for agency merge/purge

Plus holdout logic (5% random sample from suppressed segments when toggle ON).
"""

from __future__ import annotations
import logging
import random

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Printer File columns (spec Section 10.1)
PRINTER_COLUMNS = [
    "DonorID", "CampaignAppealCode", "Scanline", "PackageCode",
    "Addressee", "Salutation", "FirstName", "LastName",
    "Address1", "Address2", "City", "State", "ZIP", "Country",
    "AskAmount1", "AskAmount2", "AskAmount3", "AskAmountLabel",
    "ReplyCopyTier", "LastGiftAmount", "LastGiftDate",
    "CurrentFYGiving", "PriorFYGiving", "CAVersion",
]

# Matchback File columns (spec Section 10.2)
MATCHBACK_COLUMNS = [
    "DonorID", "CampaignAppealCode", "InternalAppealCode",
    "SegmentCode", "SegmentName", "PackageCode", "TestFlag",
    "Addressee", "Salutation", "FirstName", "LastName",
    "Address1", "Address2", "City", "State", "ZIP", "Country",
    "AskAmount1", "AskAmount2", "AskAmount3", "AskAmountLabel",
    "ReplyCopyTier", "LastGiftAmount", "LastGiftDate",
    "CurrentFYGiving", "PriorFYGiving",
    "CumulativeGiving", "LifecycleStage", "CAVersion",
    "CornerstoneFlag", "Email", "SustainerFlag",
    "GiftCount12Mo", "RFMScore",
]


def _get_acct_field(accts, acct_id, field, default=""):
    """Safely get a field value from the accounts DataFrame."""
    try:
        val = accts.at[acct_id, field]
        if pd.isna(val) or val is None:
            return default
        return val
    except (KeyError, ValueError):
        return default


def _format_zip(zip_val) -> str:
    """Preserve ZIP leading zeros, handle ZIP+4."""
    if pd.isna(zip_val) or zip_val is None:
        return ""
    z = str(zip_val).strip()
    # Remove .0 from float conversion
    if z.endswith(".0"):
        z = z[:-2]
    # Zero-pad 5-digit ZIPs
    if z.isdigit() and len(z) < 5:
        z = z.zfill(5)
    return z


def generate_output_files(
    waterfall_result: pd.DataFrame,
    accounts_df: pd.DataFrame,
    ask_df: pd.DataFrame,
    reply_tiers: pd.Series,
    codes_df: pd.DataFrame,
    campaign_code: str = "DIAG",
    lane: str = "Housefile",
    holdout_pct: float = 0.0,
    holdout_seed: int = 42,
) -> dict:
    """Generate Printer File, Matchback File, and Housefile Suppression File.

    Args:
        waterfall_result: Full waterfall output.
        accounts_df: Account data.
        ask_df: Ask string data (account_id, ask1, ask2, ask3, ask_label, ask_basis).
        reply_tiers: Reply copy tier series indexed by account_id.
        codes_df: Appeal code data from generate_appeal_codes().
        campaign_code: Campaign code for filenames.
        lane: Lane for filenames.
        holdout_pct: Holdout percentage (0 = no holdout).
        holdout_seed: Random seed for holdout selection.

    Returns:
        dict with "printer_csv", "matchback_csv", "suppression_csv" (string content),
        "printer_count", "matchback_count", "suppression_count",
        "holdout_count", "warnings".
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Merge codes_df with waterfall for the mailable universe
    mailable = codes_df[~codes_df.get("missing_constituent_id", pd.Series(False))].copy()

    # Apply holdout if requested
    holdout_ids = set()
    if holdout_pct > 0 and len(mailable) > 0:
        n_holdout = max(1, int(len(mailable) * holdout_pct / 100))
        random.seed(holdout_seed)
        holdout_ids = set(random.sample(list(mailable["account_id"]), min(n_holdout, len(mailable))))
        logger.info(f"  Holdout: {len(holdout_ids):,} donors ({holdout_pct}% of {len(mailable):,})")

    # Index ask_df and reply_tiers for fast lookup
    ask_lookup = {}
    if len(ask_df) > 0:
        for _, row in ask_df.iterrows():
            ask_lookup[row["account_id"]] = row
    reply_lookup = reply_tiers.to_dict() if isinstance(reply_tiers, pd.Series) else {}

    warnings = []
    printer_rows = []
    matchback_rows = []

    for _, code_row in mailable.iterrows():
        acct_id = code_row["account_id"]

        if acct_id in holdout_ids:
            continue  # Skip holdout donors from Printer File

        # Common fields
        donor_id = code_row["donor_id_9"]
        appeal_9 = code_row["appeal_code_9"]
        appeal_15 = code_row["appeal_code_15"]
        scanline = code_row["scanline"]
        package = code_row["package_code"]
        test_flag = code_row["test_flag"]
        ca_version = code_row["ca_version"]
        seg_code = code_row["segment_code"]

        # Account fields
        addressee = str(_get_acct_field(accts, acct_id, "npo02__Formal_Greeting__c"))
        salutation = str(_get_acct_field(accts, acct_id, "Special_Salutation__c"))
        first_name = str(_get_acct_field(accts, acct_id, "First_Name__c"))
        last_name = str(_get_acct_field(accts, acct_id, "Last_Name__c"))

        # If salutation is just a title (e.g., "Mr."), append last name
        if salutation and last_name and len(salutation.split()) == 1:
            salutation = f"{salutation} {last_name}"

        street = str(_get_acct_field(accts, acct_id, "BillingStreet"))
        city = str(_get_acct_field(accts, acct_id, "BillingCity"))
        state = str(_get_acct_field(accts, acct_id, "BillingState"))
        zip_code = _format_zip(_get_acct_field(accts, acct_id, "BillingPostalCode"))
        country = str(_get_acct_field(accts, acct_id, "BillingCountry"))

        # Parse address lines (BillingStreet may have line breaks)
        addr_lines = street.split("\n") if street else [""]
        address1 = addr_lines[0].strip() if len(addr_lines) > 0 else ""
        address2 = addr_lines[1].strip() if len(addr_lines) > 1 else ""

        # Ask strings
        ask_row = ask_lookup.get(acct_id, {})
        ask1 = ask_row.get("ask1", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask1", "")
        ask2 = ask_row.get("ask2", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask2", "")
        ask3 = ask_row.get("ask3", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask3", "")
        ask_label = ask_row.get("ask_label", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask_label", "")

        # Reply copy tier
        reply_tier = reply_lookup.get(acct_id, "")

        # Giving fields
        last_gift_amt = _get_acct_field(accts, acct_id, "npo02__LastOppAmount__c")
        last_gift_date = _get_acct_field(accts, acct_id, "npo02__LastCloseDate__c")
        current_fy = _get_acct_field(accts, acct_id, "Total_Gifts_This_Fiscal_Year__c")
        prior_fy = _get_acct_field(accts, acct_id, "Total_Gifts_Last_Fiscal_Year__c")
        cumulative = _get_acct_field(accts, acct_id, "npo02__TotalOppAmount__c")

        # Printer File row (NO 15-char code)
        printer_rows.append({
            "DonorID": donor_id,
            "CampaignAppealCode": appeal_9,
            "Scanline": scanline,
            "PackageCode": package,
            "Addressee": addressee,
            "Salutation": salutation,
            "FirstName": first_name,
            "LastName": last_name,
            "Address1": address1,
            "Address2": address2,
            "City": city,
            "State": state,
            "ZIP": zip_code,
            "Country": country,
            "AskAmount1": ask1,
            "AskAmount2": ask2,
            "AskAmount3": ask3,
            "AskAmountLabel": ask_label,
            "ReplyCopyTier": reply_tier,
            "LastGiftAmount": last_gift_amt,
            "LastGiftDate": last_gift_date,
            "CurrentFYGiving": current_fy,
            "PriorFYGiving": prior_fy,
            "CAVersion": ca_version,
        })

        # Matchback File row (includes 15-char code + analyst fields)
        # Waterfall result fields
        wf_row = waterfall_result[waterfall_result["account_id"] == acct_id]
        lifecycle = wf_row["lifecycle_stage"].iloc[0] if len(wf_row) > 0 else ""
        rfm_code = wf_row["RFM_code"].iloc[0] if len(wf_row) > 0 else ""

        cornerstone = _get_acct_field(accts, acct_id, "Cornerstone_Partner__c")
        email = _get_acct_field(accts, acct_id, "General_Email__c")
        sustainer = _get_acct_field(accts, acct_id, "Miracle_Partner__c")
        gifts_12m = _get_acct_field(accts, acct_id, "Gifts_in_L12M__c")

        matchback_rows.append({
            "DonorID": donor_id,
            "CampaignAppealCode": appeal_9,
            "InternalAppealCode": appeal_15,
            "SegmentCode": seg_code,
            "SegmentName": code_row.get("segment_code", ""),
            "PackageCode": package,
            "TestFlag": test_flag,
            "Addressee": addressee,
            "Salutation": salutation,
            "FirstName": first_name,
            "LastName": last_name,
            "Address1": address1,
            "Address2": address2,
            "City": city,
            "State": state,
            "ZIP": zip_code,
            "Country": country,
            "AskAmount1": ask1,
            "AskAmount2": ask2,
            "AskAmount3": ask3,
            "AskAmountLabel": ask_label,
            "ReplyCopyTier": reply_tier,
            "LastGiftAmount": last_gift_amt,
            "LastGiftDate": last_gift_date,
            "CurrentFYGiving": current_fy,
            "PriorFYGiving": prior_fy,
            "CumulativeGiving": cumulative,
            "LifecycleStage": lifecycle,
            "CAVersion": ca_version,
            "CornerstoneFlag": cornerstone,
            "Email": email,
            "SustainerFlag": sustainer,
            "GiftCount12Mo": gifts_12m,
            "RFMScore": rfm_code,
        })

    printer_df = pd.DataFrame(printer_rows, columns=PRINTER_COLUMNS)
    matchback_df = pd.DataFrame(matchback_rows, columns=MATCHBACK_COLUMNS)

    # Validate: 15-char code must NOT appear in Printer File
    if "InternalAppealCode" in printer_df.columns:
        warnings.append("FAIL: InternalAppealCode found in Printer File")
    # Validate: ZIP preservation
    bad_zips = printer_df[
        (printer_df["ZIP"] != "")
        & (printer_df["ZIP"].str.len() < 5)
        & (printer_df["ZIP"].str.len() > 0)
    ]
    if len(bad_zips) > 0:
        warnings.append(f"WARNING: {len(bad_zips)} ZIPs shorter than 5 chars")

    # Generate CSVs as strings (ZIP as text)
    printer_csv = printer_df.to_csv(index=False)
    matchback_csv = matchback_df.to_csv(index=False)

    logger.info(f"  Printer File: {len(printer_df):,} rows, {len(PRINTER_COLUMNS)} columns")
    logger.info(f"  Matchback File: {len(matchback_df):,} rows, {len(MATCHBACK_COLUMNS)} columns")
    logger.info(f"  Holdout: {len(holdout_ids):,} donors excluded from Printer File")

    # --- Housefile Suppression File ---
    # All current housefile donors for agency merge/purge
    # Includes No Name Sharing flagged donors
    all_assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ]
    supp_rows = []
    for _, row in all_assigned.iterrows():
        aid = row["account_id"]
        donor_id = str(_get_acct_field(accts, aid, "Constituent_Id__c")).strip().zfill(9)[:9]
        supp_rows.append({
            "DonorID": donor_id,
            "Name": _get_acct_field(accts, aid, "Name"),
            "Address1": str(_get_acct_field(accts, aid, "BillingStreet")).split("\n")[0].strip(),
            "City": _get_acct_field(accts, aid, "BillingCity"),
            "State": _get_acct_field(accts, aid, "BillingState"),
            "ZIP": _format_zip(_get_acct_field(accts, aid, "BillingPostalCode")),
        })

    supp_df = pd.DataFrame(supp_rows)
    suppression_csv = supp_df.to_csv(index=False)
    logger.info(f"  Housefile Suppression File: {len(supp_df):,} rows")

    return {
        "printer_csv": printer_csv,
        "matchback_csv": matchback_csv,
        "suppression_csv": suppression_csv,
        "printer_count": len(printer_df),
        "matchback_count": len(matchback_df),
        "suppression_count": len(supp_df),
        "holdout_count": len(holdout_ids),
        "warnings": warnings,
        "printer_df": printer_df,
        "matchback_df": matchback_df,
    }
