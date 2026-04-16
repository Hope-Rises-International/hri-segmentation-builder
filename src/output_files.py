"""Output file generation per spec Section 10.

Produces:
1. Printer File CSV — for VeraData/lettershop (no 15-char codes, clean records only)
2. Internal Matchback File CSV — for HRI (all codes + analyst fields, includes excluded records)
3. Housefile Suppression File CSV — for agency merge/purge
4. Exceptions CSV — records excluded due to missing/duplicate Constituent_Id

Plus holdout logic (5% random sample when toggle ON).
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

# Matchback File columns (spec Section 10.2) + exclusion_reason
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
    "GiftCount12Mo", "RFMScore", "Holdout", "ExclusionReason",
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
    if z.endswith(".0"):
        z = z[:-2]
    if z.isdigit() and len(z) < 5:
        z = z.zfill(5)
    return z


def apply_constituent_id_filter(
    codes_df: pd.DataFrame,
    accounts_df: pd.DataFrame,
) -> tuple:
    """Apply Constituent_Id data quality filter per architect instruction.

    Excludes:
    1. Records where Constituent_Id__c is null or empty
    2. Records where Constituent_Id__c is shared by 2+ accounts (ALL sharing accounts excluded)

    Args:
        codes_df: Appeal code data with account_id and donor_id columns.
        accounts_df: Account data with Constituent_Id__c.

    Returns:
        (clean_df, excluded_df, exceptions_df) where:
        - clean_df: records safe for Printer File
        - excluded_df: records to include in Matchback with exclusion_reason
        - exceptions_df: formatted for exceptions CSV upload
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Get Constituent_Id for each record in codes_df
    constituent_ids = codes_df["account_id"].map(
        lambda aid: _get_acct_field(accts, aid, "Constituent_Id__c", default="")
    )
    constituent_ids = constituent_ids.fillna("").astype(str).str.strip()

    # 1. Missing Constituent_Id
    missing_mask = (constituent_ids == "") | (constituent_ids == "nan") | (constituent_ids == "None")
    missing_count = missing_mask.sum()

    # 2. Duplicate Constituent_Id (across ALL accounts in the pipeline dataset, not just codes_df)
    # Build the full duplicate set from accounts_df
    all_cids = accts["Constituent_Id__c"].fillna("").astype(str).str.strip()
    all_cids = all_cids[all_cids != ""]
    dup_cids = set(all_cids[all_cids.duplicated(keep=False)].values)

    duplicate_mask = constituent_ids.isin(dup_cids) & ~missing_mask
    duplicate_count = duplicate_mask.sum()

    # Combined exclusion
    excluded_mask = missing_mask | duplicate_mask
    clean_df = codes_df[~excluded_mask].copy()
    excluded_df = codes_df[excluded_mask].copy()

    # Tag exclusion reason
    excluded_df["exclusion_reason"] = ""
    excluded_df.loc[missing_mask[excluded_mask.index[excluded_mask]], "exclusion_reason"] = "missing_constituent_id"
    # Fix: use the original index alignment
    for idx in excluded_df.index:
        if missing_mask.loc[idx]:
            excluded_df.at[idx, "exclusion_reason"] = "missing_constituent_id"
        elif duplicate_mask.loc[idx]:
            excluded_df.at[idx, "exclusion_reason"] = "duplicate_constituent_id"

    # Build exceptions CSV DataFrame
    exception_rows = []
    for idx, row in excluded_df.iterrows():
        aid = row["account_id"]
        cid = _get_acct_field(accts, aid, "Constituent_Id__c", default="")
        exception_rows.append({
            "AccountId": aid,
            "AccountName": _get_acct_field(accts, aid, "Name"),
            "Constituent_Id__c": cid,
            "ExclusionReason": row["exclusion_reason"],
            "SegmentCode": row.get("segment_code", ""),
            "AskAmount1": row.get("ask1", ""),
            "AskAmount2": row.get("ask2", ""),
            "AskAmount3": row.get("ask3", ""),
        })
    exceptions_df = pd.DataFrame(exception_rows)

    logger.info(f"  Constituent_Id filter: {len(codes_df):,} total → "
                f"{len(clean_df):,} clean, {len(excluded_df):,} excluded "
                f"({missing_count:,} missing ID, {duplicate_count:,} duplicate ID)")

    return clean_df, excluded_df, exceptions_df


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
    """Generate Printer File, Matchback File, Housefile Suppression File, and Exceptions File.

    Returns dict with CSV strings, counts, DataFrames, and warnings.
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # --- Data quality filter: missing/duplicate Constituent_Id ---
    clean_codes, excluded_codes, exceptions_df = apply_constituent_id_filter(codes_df, accounts_df)

    # Apply holdout on clean records only
    holdout_ids = set()
    if holdout_pct > 0 and len(clean_codes) > 0:
        n_holdout = max(1, int(len(clean_codes) * holdout_pct / 100))
        random.seed(holdout_seed)
        holdout_ids = set(random.sample(list(clean_codes["account_id"]), min(n_holdout, len(clean_codes))))
        logger.info(f"  Holdout: {len(holdout_ids):,} donors ({holdout_pct}% of {len(clean_codes):,})")

    # Index ask_df and reply_tiers for fast lookup
    ask_lookup = {}
    if len(ask_df) > 0:
        for _, row in ask_df.iterrows():
            ask_lookup[row["account_id"]] = row
    reply_lookup = reply_tiers.to_dict() if isinstance(reply_tiers, pd.Series) else {}

    warnings = []

    def _build_record(code_row, include_internal=False, exclusion_reason="", holdout=False):
        """Build a single output record from a codes_df row."""
        acct_id = code_row["account_id"]
        donor_id = code_row.get("donor_id_9", "")
        appeal_9 = code_row.get("appeal_code_9", "")
        appeal_15 = code_row.get("appeal_code_15", "")
        scanline = code_row.get("scanline", "")
        package = code_row.get("package_code", "")
        test_flag = code_row.get("test_flag", "")
        ca_version = code_row.get("ca_version", "")
        seg_code = code_row.get("segment_code", "")

        addressee = str(_get_acct_field(accts, acct_id, "npo02__Formal_Greeting__c"))
        salutation = str(_get_acct_field(accts, acct_id, "Special_Salutation__c"))
        first_name = str(_get_acct_field(accts, acct_id, "First_Name__c"))
        last_name = str(_get_acct_field(accts, acct_id, "Last_Name__c"))

        if salutation and last_name and len(salutation.split()) == 1:
            salutation = f"{salutation} {last_name}"

        street = str(_get_acct_field(accts, acct_id, "BillingStreet"))
        city = str(_get_acct_field(accts, acct_id, "BillingCity"))
        state = str(_get_acct_field(accts, acct_id, "BillingState"))
        zip_code = _format_zip(_get_acct_field(accts, acct_id, "BillingPostalCode"))
        country = str(_get_acct_field(accts, acct_id, "BillingCountry"))

        addr_lines = street.split("\n") if street else [""]
        address1 = addr_lines[0].strip() if len(addr_lines) > 0 else ""
        address2 = addr_lines[1].strip() if len(addr_lines) > 1 else ""

        ask_row = ask_lookup.get(acct_id, {})
        ask1 = ask_row.get("ask1", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask1", "")
        ask2 = ask_row.get("ask2", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask2", "")
        ask3 = ask_row.get("ask3", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask3", "")
        ask_label = ask_row.get("ask_label", "") if isinstance(ask_row, dict) else getattr(ask_row, "ask_label", "")

        reply_tier = reply_lookup.get(acct_id, "")

        last_gift_amt = _get_acct_field(accts, acct_id, "npo02__LastOppAmount__c")
        last_gift_date = _get_acct_field(accts, acct_id, "npo02__LastCloseDate__c")
        current_fy = _get_acct_field(accts, acct_id, "Total_Gifts_This_Fiscal_Year__c")
        prior_fy = _get_acct_field(accts, acct_id, "Total_Gifts_Last_Fiscal_Year__c")
        cumulative = _get_acct_field(accts, acct_id, "npo02__TotalOppAmount__c")

        wf_row = waterfall_result[waterfall_result["account_id"] == acct_id]
        lifecycle = wf_row["lifecycle_stage"].iloc[0] if len(wf_row) > 0 else ""
        rfm_code = wf_row["RFM_code"].iloc[0] if len(wf_row) > 0 else ""

        base = {
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
        }

        if include_internal:
            base.update({
                "InternalAppealCode": appeal_15,
                "SegmentCode": seg_code,
                "SegmentName": code_row.get("segment_code", ""),
                "TestFlag": test_flag,
                "CumulativeGiving": cumulative,
                "LifecycleStage": lifecycle,
                "CornerstoneFlag": _get_acct_field(accts, acct_id, "Cornerstone_Partner__c"),
                "Email": _get_acct_field(accts, acct_id, "General_Email__c"),
                "SustainerFlag": _get_acct_field(accts, acct_id, "Miracle_Partner__c"),
                "GiftCount12Mo": _get_acct_field(accts, acct_id, "Gifts_in_L12M__c"),
                "RFMScore": rfm_code,
                "Holdout": holdout,
                "ExclusionReason": exclusion_reason,
            })

        return base

    # --- Build Printer File (clean records only, minus holdout) ---
    printer_rows = []
    for _, code_row in clean_codes.iterrows():
        if code_row["account_id"] in holdout_ids:
            continue
        printer_rows.append(_build_record(code_row, include_internal=False))

    # --- Build Matchback File (ALL records: clean + excluded, with exclusion_reason) ---
    matchback_rows = []
    # Clean records (with holdout flag)
    for _, code_row in clean_codes.iterrows():
        is_holdout = code_row["account_id"] in holdout_ids
        matchback_rows.append(_build_record(
            code_row, include_internal=True, exclusion_reason="", holdout=is_holdout
        ))
    # Excluded records (with reason, not holdout)
    for _, code_row in excluded_codes.iterrows():
        reason = code_row.get("exclusion_reason", "unknown")
        matchback_rows.append(_build_record(
            code_row, include_internal=True, exclusion_reason=reason, holdout=False
        ))

    printer_df = pd.DataFrame(printer_rows, columns=PRINTER_COLUMNS)
    matchback_df = pd.DataFrame(matchback_rows, columns=MATCHBACK_COLUMNS)

    # Validate: 15-char code must NOT appear in Printer File
    if "InternalAppealCode" in printer_df.columns:
        warnings.append("FAIL: InternalAppealCode found in Printer File")
    # Validate: ZIP preservation
    if len(printer_df) > 0:
        bad_zips = printer_df[
            (printer_df["ZIP"] != "")
            & (printer_df["ZIP"].str.len() < 5)
            & (printer_df["ZIP"].str.len() > 0)
        ]
        if len(bad_zips) > 0:
            warnings.append(f"WARNING: {len(bad_zips)} ZIPs shorter than 5 chars")

    printer_csv = printer_df.to_csv(index=False)
    matchback_csv = matchback_df.to_csv(index=False)
    exceptions_csv = exceptions_df.to_csv(index=False) if len(exceptions_df) > 0 else ""

    logger.info(f"  Printer File: {len(printer_df):,} rows, {len(PRINTER_COLUMNS)} columns")
    logger.info(f"  Matchback File: {len(matchback_df):,} rows "
                f"({len(clean_codes):,} clean + {len(excluded_codes):,} excluded)")
    logger.info(f"  Holdout: {len(holdout_ids):,} donors excluded from Printer File")
    if len(excluded_codes) > 0:
        missing = (excluded_codes["exclusion_reason"] == "missing_constituent_id").sum()
        dupes = (excluded_codes["exclusion_reason"] == "duplicate_constituent_id").sum()
        logger.info(f"  Exceptions: {len(excluded_codes):,} excluded "
                    f"({missing:,} missing ID, {dupes:,} duplicate ID)")

    # --- Housefile Suppression File ---
    all_assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ]
    supp_rows = []
    for _, row in all_assigned.iterrows():
        aid = row["account_id"]
        donor_id = str(_get_acct_field(accts, aid, "Constituent_Id__c")).strip()
        if donor_id and donor_id != "" and donor_id != "nan":
            donor_id = donor_id.zfill(9)[:9]
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
        "exceptions_csv": exceptions_csv,
        "printer_count": len(printer_df),
        "matchback_count": len(matchback_df),
        "suppression_count": len(supp_df),
        "holdout_count": len(holdout_ids),
        "excluded_count": len(excluded_codes),
        "excluded_missing": int((excluded_codes["exclusion_reason"] == "missing_constituent_id").sum()) if len(excluded_codes) > 0 else 0,
        "excluded_duplicate": int((excluded_codes["exclusion_reason"] == "duplicate_constituent_id").sum()) if len(excluded_codes) > 0 else 0,
        "warnings": warnings,
        "printer_df": printer_df,
        "matchback_df": matchback_df,
        "exceptions_df": exceptions_df,
    }
