"""Output file generation per spec Section 10 — VECTORIZED.

All output DataFrames built via pandas merge/join operations.
No per-row Python loops. No _build_record(). No accts.at[] calls.

Produces:
1. Printer File CSV — for VeraData/lettershop (no 15-char codes, clean records only)
2. Internal Matchback File CSV — for HRI (all codes + analyst fields, includes excluded records)
3. Housefile Suppression File CSV — for agency merge/purge
4. Exceptions CSV — records excluded due to missing/duplicate Constituent_Id
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
    "GiftCount12Mo", "RFMScore", "Holdout", "ExclusionReason",
]


def _format_zip_series(s):
    """Vectorized ZIP formatting: preserve leading zeros."""
    s = s.fillna("").astype(str).str.strip()
    s = s.str.replace(r'\.0$', '', regex=True)
    # Pad numeric ZIPs shorter than 5 digits
    is_short_numeric = s.str.match(r'^\d{1,4}$')
    s = s.where(~is_short_numeric, s.str.zfill(5))
    return s


def _split_street(s):
    """Vectorized street splitting: first line → Address1, second → Address2."""
    s = s.fillna("").astype(str)
    parts = s.str.split('\n', n=1, expand=True)
    addr1 = parts[0].str.strip() if 0 in parts.columns else pd.Series("", index=s.index)
    addr2 = parts[1].str.strip() if 1 in parts.columns else pd.Series("", index=s.index)
    return addr1.fillna(""), addr2.fillna("")


def _fix_salutation(sal, last):
    """Vectorized salutation fix: append last name if salutation is single word."""
    sal = sal.fillna("").astype(str)
    last = last.fillna("").astype(str)
    is_single_word = (sal != "") & (~sal.str.contains(' ')) & (last != "")
    return sal.where(~is_single_word, sal + " " + last)


def apply_constituent_id_filter(codes_df, accounts_df):
    """Apply Constituent_Id data quality filter — vectorized.

    Returns (clean_df, excluded_df, exceptions_df).
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Map Constituent_Id onto codes_df
    cid_map = accts["Constituent_Id__c"].fillna("").astype(str).str.strip()
    constituent_ids = codes_df["account_id"].map(cid_map).fillna("")

    # Missing
    missing_mask = (constituent_ids == "") | (constituent_ids == "nan") | (constituent_ids == "None")

    # Duplicate (across all accounts, not just fitted)
    all_cids = cid_map[cid_map != ""]
    dup_cids = set(all_cids[all_cids.duplicated(keep=False)].values)
    duplicate_mask = constituent_ids.isin(dup_cids) & ~missing_mask

    excluded_mask = missing_mask | duplicate_mask
    clean_df = codes_df[~excluded_mask].copy()
    excluded_df = codes_df[excluded_mask].copy()

    # Tag exclusion reason vectorized — reindex masks to excluded_df's index
    if len(excluded_df) > 0:
        excluded_missing = missing_mask.reindex(excluded_df.index).fillna(False)
        excluded_df["exclusion_reason"] = np.where(
            excluded_missing, "missing_constituent_id", "duplicate_constituent_id"
        )
    else:
        excluded_df["exclusion_reason"] = pd.Series(dtype=str)

    # Exceptions CSV via merge (no per-row loop)
    if len(excluded_df) > 0:
        exc = excluded_df[["account_id", "exclusion_reason", "segment_code"]].copy()
        exc = exc.rename(columns={"account_id": "AccountId", "exclusion_reason": "ExclusionReason", "segment_code": "SegmentCode"})
        exc["AccountName"] = exc["AccountId"].map(accts.get("Name", pd.Series(dtype=str)))
        exc["Constituent_Id__c"] = exc["AccountId"].map(cid_map)
        exceptions_df = exc[["AccountId", "AccountName", "Constituent_Id__c", "ExclusionReason", "SegmentCode"]]
    else:
        exceptions_df = pd.DataFrame(columns=["AccountId", "AccountName", "Constituent_Id__c", "ExclusionReason", "SegmentCode"])

    missing_count = missing_mask.sum()
    duplicate_count = duplicate_mask.sum()
    logger.info(f"  Constituent_Id filter: {len(codes_df):,} total → "
                f"{len(clean_df):,} clean, {len(excluded_df):,} excluded "
                f"({missing_count:,} missing ID, {duplicate_count:,} duplicate ID)")

    return clean_df, excluded_df, exceptions_df


def generate_output_files(
    waterfall_result,
    accounts_df,
    ask_df,
    reply_tiers,
    codes_df,
    campaign_code="DIAG",
    lane="Housefile",
    holdout_pct=0.0,
    holdout_seed=42,
):
    """Generate all output files via vectorized pandas operations.

    No per-row iteration. All joins via merge/map.
    """
    import time
    t0 = time.time()

    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # --- DQ filter ---
    clean_codes, excluded_codes, exceptions_df = apply_constituent_id_filter(codes_df, accounts_df)

    # --- Holdout ---
    holdout_ids = set()
    if holdout_pct > 0 and len(clean_codes) > 0:
        n_holdout = max(1, int(len(clean_codes) * holdout_pct / 100))
        random.seed(holdout_seed)
        holdout_ids = set(random.sample(list(clean_codes["account_id"]), min(n_holdout, len(clean_codes))))
        logger.info(f"  Holdout: {len(holdout_ids):,} donors ({holdout_pct}%)")

    # --- Build the master DataFrame via merges ---
    # Start with codes_df (has account_id, appeal codes, scanline, segment, package, etc.)
    all_codes = pd.concat([clean_codes, excluded_codes], ignore_index=True)

    # Tag holdout and exclusion
    all_codes["Holdout"] = all_codes["account_id"].isin(holdout_ids)
    if "exclusion_reason" not in all_codes.columns:
        all_codes["exclusion_reason"] = ""
    all_codes["exclusion_reason"] = all_codes["exclusion_reason"].fillna("")
    # Mark quantity-reduced records with exclusion_reason so they go to Matchback not Printer
    if "quantity_reduced" in all_codes.columns:
        qr_mask = all_codes["quantity_reduced"].fillna(False) & (all_codes["exclusion_reason"] == "")
        all_codes.loc[qr_mask, "exclusion_reason"] = "quantity_reduction"
    all_codes["_is_excluded"] = all_codes["exclusion_reason"] != ""

    # Merge account fields
    acct_fields = accts[[
        "npo02__Formal_Greeting__c", "Special_Salutation__c",
        "First_Name__c", "Last_Name__c",
        "BillingStreet", "BillingCity", "BillingState", "BillingPostalCode", "BillingCountry",
        "npo02__LastOppAmount__c", "npo02__LastCloseDate__c",
        "Total_Gifts_This_Fiscal_Year__c", "Total_Gifts_Last_Fiscal_Year__c",
        "npo02__TotalOppAmount__c",
        "Cornerstone_Partner__c", "General_Email__c", "Miracle_Partner__c",
        "Gifts_in_L12M__c",
    ]].copy()
    master = all_codes.merge(acct_fields, left_on="account_id", right_index=True, how="left")

    # Merge ask strings
    if len(ask_df) > 0:
        ask_cols = ask_df[["account_id", "ask1", "ask2", "ask3", "ask_label"]].copy()
        master = master.merge(ask_cols, on="account_id", how="left")
    else:
        master["ask1"] = ""
        master["ask2"] = ""
        master["ask3"] = ""
        master["ask_label"] = ""

    # Merge reply tiers
    if isinstance(reply_tiers, pd.Series):
        rt_df = reply_tiers.reset_index()
        rt_df.columns = ["account_id", "ReplyCopyTier"]
        master = master.merge(rt_df, on="account_id", how="left")
    else:
        master["ReplyCopyTier"] = ""

    # Merge waterfall fields (lifecycle, RFM)
    wf_cols = waterfall_result[["account_id", "lifecycle_stage", "RFM_code"]].drop_duplicates("account_id")
    master = master.merge(wf_cols, on="account_id", how="left")

    # --- Vectorized field transforms ---
    master["Addressee"] = master["npo02__Formal_Greeting__c"].fillna("")
    master["Salutation"] = _fix_salutation(
        master["Special_Salutation__c"], master["Last_Name__c"]
    )
    master["FirstName"] = master["First_Name__c"].fillna("")
    master["LastName"] = master["Last_Name__c"].fillna("")
    master["Address1"], master["Address2"] = _split_street(master["BillingStreet"])
    master["City"] = master["BillingCity"].fillna("")
    master["State"] = master["BillingState"].fillna("")
    master["ZIP"] = _format_zip_series(master["BillingPostalCode"])
    master["Country"] = master["BillingCountry"].fillna("")

    # Rename code columns
    master["DonorID"] = master.get("donor_id_9", "")
    master["CampaignAppealCode"] = master.get("appeal_code_9", "")
    master["Scanline"] = master.get("scanline", "")
    master["PackageCode"] = master.get("package_code", "")
    master["InternalAppealCode"] = master.get("appeal_code_15", "")
    master["SegmentCode"] = master.get("segment_code", "")
    master["SegmentName"] = master.get("segment_code", "")  # same as code for now
    master["TestFlag"] = master.get("test_flag", "")
    master["CAVersion"] = master.get("ca_version", False)
    master["AskAmount1"] = master["ask1"]
    master["AskAmount2"] = master["ask2"]
    master["AskAmount3"] = master["ask3"]
    master["AskAmountLabel"] = master["ask_label"]
    master["LastGiftAmount"] = master["npo02__LastOppAmount__c"]
    master["LastGiftDate"] = master["npo02__LastCloseDate__c"]
    master["CurrentFYGiving"] = master["Total_Gifts_This_Fiscal_Year__c"]
    master["PriorFYGiving"] = master["Total_Gifts_Last_Fiscal_Year__c"]
    master["CumulativeGiving"] = master["npo02__TotalOppAmount__c"]
    master["LifecycleStage"] = master["lifecycle_stage"].fillna("")
    master["CornerstoneFlag"] = master["Cornerstone_Partner__c"]
    master["Email"] = master["General_Email__c"].fillna("")
    master["SustainerFlag"] = master["Miracle_Partner__c"]
    master["GiftCount12Mo"] = master["Gifts_in_L12M__c"]
    master["RFMScore"] = master["RFM_code"].fillna("")
    master["ExclusionReason"] = master["exclusion_reason"]
    master["ReplyCopyTier"] = master["ReplyCopyTier"].fillna("")

    logger.info(f"  Master DataFrame built: {len(master):,} rows in {time.time() - t0:.1f}s")

    # --- Printer File: clean, non-holdout records only ---
    printer_mask = (~master["_is_excluded"]) & (~master["Holdout"])
    printer_df = master.loc[printer_mask, PRINTER_COLUMNS].copy()

    # --- Matchback File: all records ---
    matchback_df = master[MATCHBACK_COLUMNS].copy()

    # --- Validations ---
    warnings = []
    if "InternalAppealCode" in printer_df.columns:
        warnings.append("FAIL: InternalAppealCode found in Printer File")
    if len(printer_df) > 0:
        bad_zips = printer_df[(printer_df["ZIP"] != "") & (printer_df["ZIP"].str.len() < 5)]
        if len(bad_zips) > 0:
            warnings.append(f"WARNING: {len(bad_zips)} ZIPs shorter than 5 chars")

    printer_csv = printer_df.to_csv(index=False)
    matchback_csv = matchback_df.to_csv(index=False)
    exceptions_csv = exceptions_df.to_csv(index=False) if len(exceptions_df) > 0 else ""

    logger.info(f"  Printer File: {len(printer_df):,} rows")
    logger.info(f"  Matchback File: {len(matchback_df):,} rows")
    logger.info(f"  Holdout: {len(holdout_ids):,} excluded from Printer")

    excluded_count = len(excluded_codes)
    excluded_missing = int((excluded_codes.get("exclusion_reason", pd.Series()) == "missing_constituent_id").sum()) if excluded_count > 0 else 0
    excluded_duplicate = int((excluded_codes.get("exclusion_reason", pd.Series()) == "duplicate_constituent_id").sum()) if excluded_count > 0 else 0
    if excluded_count > 0:
        logger.info(f"  Exceptions: {excluded_count:,} ({excluded_missing:,} missing, {excluded_duplicate:,} duplicate)")

    # --- Housefile Suppression File (vectorized) ---
    all_assigned = waterfall_result[
        (waterfall_result["segment_code"] != "") & (waterfall_result["suppression_reason"] == "")
    ]
    supp_aids = all_assigned["account_id"].reset_index(drop=True)
    cid_series = accts["Constituent_Id__c"] if "Constituent_Id__c" in accts.columns else pd.Series(dtype=str)
    supp_df = pd.DataFrame({
        "DonorID": supp_aids.map(cid_series).fillna("").astype(str).str.strip().str.zfill(9).str[:9],
        "Name": supp_aids.map(accts["Name"] if "Name" in accts.columns else pd.Series(dtype=str)).fillna(""),
        "Address1": supp_aids.map(accts["BillingStreet"] if "BillingStreet" in accts.columns else pd.Series(dtype=str)).fillna("").astype(str).str.split('\n').str[0].str.strip(),
        "City": supp_aids.map(accts["BillingCity"] if "BillingCity" in accts.columns else pd.Series(dtype=str)).fillna(""),
        "State": supp_aids.map(accts["BillingState"] if "BillingState" in accts.columns else pd.Series(dtype=str)).fillna(""),
        "ZIP": _format_zip_series(supp_aids.map(accts["BillingPostalCode"] if "BillingPostalCode" in accts.columns else pd.Series(dtype=str))),
    })
    suppression_csv = supp_df.to_csv(index=False)
    logger.info(f"  Housefile Suppression: {len(supp_df):,} rows")

    total_time = time.time() - t0
    logger.info(f"  Output generation total: {total_time:.1f}s")

    return {
        "printer_csv": printer_csv,
        "matchback_csv": matchback_csv,
        "suppression_csv": suppression_csv,
        "exceptions_csv": exceptions_csv,
        "printer_count": len(printer_df),
        "matchback_count": len(matchback_df),
        "suppression_count": len(supp_df),
        "holdout_count": len(holdout_ids),
        "excluded_count": excluded_count,
        "excluded_missing": excluded_missing,
        "excluded_duplicate": excluded_duplicate,
        "warnings": warnings,
        "printer_df": printer_df,
        "matchback_df": matchback_df,
        "exceptions_df": exceptions_df,
    }
