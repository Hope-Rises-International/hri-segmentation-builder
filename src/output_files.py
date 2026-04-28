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
import csv
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
# Scanline at position 3 — Aegis matches incoming gifts to the Matchback by
# full ALM scanline, not DonorID alone. Without this column, attribution
# silently fails for the entire campaign.
# Account_CASESAFEID at the END — 18-char SF Account Id from
# Account_CASESAFEID__c formula field. Distinct from DonorID
# (Constituent_Id__c). Both go in Matchback so Aegis / merge-purge can
# pick whichever identifier they prefer.
MATCHBACK_COLUMNS = [
    "DonorID", "CampaignAppealCode", "Scanline", "InternalAppealCode",
    "SegmentCode", "SegmentName", "PackageCode", "TestFlag",
    "Addressee", "Salutation", "FirstName", "LastName",
    "Address1", "Address2", "City", "State", "ZIP", "Country",
    "AskAmount1", "AskAmount2", "AskAmount3", "AskAmountLabel",
    "ReplyCopyTier", "LastGiftAmount", "LastGiftDate",
    "CurrentFYGiving", "PriorFYGiving",
    "CumulativeGiving", "LifecycleStage", "CAVersion",
    "CornerstoneFlag", "Email", "SustainerFlag",
    "GiftCount12Mo", "RFMScore", "Holdout", "ExclusionReason",
    "Account_CASESAFEID",
]


def _format_zip_series(s):
    """Vectorized ZIP normalization per SPEC §10.1 (Text(10), preserve
    leading zeros). Goal: every non-empty value matches ^\\d{5}(-\\d{4})?$.

    Rules (in order; first match wins, otherwise pass-through):
      - empty / NaN / 'nan' / '0'           → ''
      - 1-4 pure-digit numeric              → left-pad to 5 ('1234' → '01234')
      - 9-char string of digits + space/hyphen separators → 'XXXXX-XXXX'
        (handles '012345678', '01234-5678', '01234 5678', '27410 3009')
      - already 5 digits OR 5-4 hyphenated  → pass through unchanged
      - anything else (international, malformed) → pass through

    Pre-strip trailing '.0' from float-coerced strings ('12345.0' → '12345').
    """
    s = s.fillna("").astype(str).str.strip()
    s = s.str.replace(r'\.0$', '', regex=True)
    # Treat the literal string 'nan' or bare '0' as empty rather than valid.
    s = s.where(~s.str.lower().isin(["nan", "none"]), "")
    s = s.where(s != "0", "")

    out = s.copy()

    # Case A: 1-4 digit pure-numeric → left-pad to 5.
    # Restricted to all-digit inputs so 'BH1 3HR' (UK postal code, len 2 after
    # stripping non-digits) doesn't get corrupted into '00013'.
    short_mask = s.str.match(r'^\d{1,4}$')
    out = out.mask(short_mask, s.str.zfill(5))

    # Case B: ZIP+4 with any separator (or none). Strip spaces + hyphens; if
    # what remains is exactly 9 digits AND the original was just digits and
    # separators, normalize to canonical 'XXXXX-XXXX'. Catches the architect-
    # cited '012345678' case plus the live A2651 '27410 3009' case.
    sep_stripped = s.str.replace(r'[\s\-]', '', regex=True)
    nine_mask = sep_stripped.str.match(r'^\d{9}$') & s.str.match(r'^[\d\s\-]+$')
    out = out.mask(nine_mask, sep_stripped.str[:5] + "-" + sep_stripped.str[5:])

    return out


def _split_street(s):
    """Vectorized street splitting: first line → Address1, second → Address2."""
    s = s.fillna("").astype(str)
    parts = s.str.split('\n', n=1, expand=True)
    addr1 = parts[0].str.strip() if 0 in parts.columns else pd.Series("", index=s.index)
    addr2 = parts[1].str.strip() if 1 in parts.columns else pd.Series("", index=s.index)
    return addr1.fillna(""), addr2.fillna("")


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
    selected_campaigns=None,
    holdout_pct_by_segment=None,
):
    """Generate all output files via vectorized pandas operations.

    No per-row iteration. All joins via merge/map.

    Multi-campaign mode (Item C, 2026-04-28): when
    `selected_campaigns` is supplied, the master DataFrame is split by
    the donor's campaign code (already attached upstream by
    appeal_codes.generate_appeal_codes) and one Print + one Matchback
    pair is emitted per campaign. The caller still receives the
    aggregate `printer_csv`/`matchback_csv` (concat of all campaigns)
    plus a `per_campaign` map keyed by campaign code so the writer
    can upload one pair per campaign with the correct filename prefix.

    Single-campaign mode (default): unchanged from prior behavior.

    v3.4 holdout (2026-04-28): when `holdout_pct_by_segment` is
    supplied (mapping segment_code → integer 0–5), per-segment holdout
    overrides the legacy global `holdout_pct`. A row value of 0 skips
    holdout for that segment entirely. The seed is mixed with the
    segment code so each segment's sample is independent and stable
    across re-runs of the same scenario. When `holdout_pct_by_segment`
    is None, the legacy single-percent behavior applies (all segments
    share `holdout_pct`).
    """
    import time
    t0 = time.time()

    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # --- DQ filter ---
    clean_codes, excluded_codes, exceptions_df = apply_constituent_id_filter(codes_df, accounts_df)

    # --- Holdout ---
    # v3.4: per-segment holdout. When `holdout_pct_by_segment` is
    # supplied (UI sends one row per segment with an integer 0–5), we
    # iterate per segment and sample that segment's clean donors at the
    # row's percent. The seed is `holdout_seed` mixed with the segment
    # code so each segment's sample is stable and independent across
    # re-runs of the same scenario. A row value of 0 skips holdout for
    # that segment (no records flagged Holdout=true).
    #
    # When `holdout_pct_by_segment` is None we fall back to the legacy
    # global single-percent path so callers that haven't been updated
    # (run_diagnostic, ad-hoc tools) keep working.
    holdout_ids = set()
    if holdout_pct_by_segment is not None and len(clean_codes) > 0:
        seg_groups = clean_codes.groupby("segment_code", dropna=False)
        per_segment_counts = []
        for seg_code, seg_rows in seg_groups:
            seg_pct = holdout_pct_by_segment.get(seg_code, 5)
            try:
                seg_pct = int(seg_pct)
            except (TypeError, ValueError):
                seg_pct = 5
            seg_pct = max(0, min(5, seg_pct))
            if seg_pct == 0 or len(seg_rows) == 0:
                per_segment_counts.append((seg_code, 0, len(seg_rows), seg_pct))
                continue
            n_seg_hold = max(1, int(len(seg_rows) * seg_pct / 100))
            n_seg_hold = min(n_seg_hold, len(seg_rows))
            # Mix seed with segment code so each segment's sample is
            # independent and stable across re-runs.
            seg_seed = (holdout_seed * 1_000_003) ^ hash(str(seg_code))
            rng = random.Random(seg_seed)
            seg_aids = list(seg_rows["account_id"])
            holdout_ids.update(rng.sample(seg_aids, n_seg_hold))
            per_segment_counts.append((seg_code, n_seg_hold, len(seg_rows), seg_pct))
        for seg_code, held, total, pct in per_segment_counts:
            if total > 0:
                logger.info(f"  Holdout [{seg_code}]: {held:,} of {total:,} ({pct}%)")
    elif holdout_pct > 0 and len(clean_codes) > 0:
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

    # Merge account fields. Account_CASESAFEID__c was added 2026-04-27 —
    # if the BQ cache hasn't been refreshed since the SOQL change, the
    # column won't be there. Inject an empty Series in that case so the
    # output stays well-formed; the next nightly cache refresh fills it.
    if "Account_CASESAFEID__c" not in accts.columns:
        accts = accts.copy()
        accts["Account_CASESAFEID__c"] = ""
        logger.warning("  Account_CASESAFEID__c missing from accounts cache — column will be blank until next sf-cache-extract")
    acct_fields = accts[[
        "npo02__Formal_Greeting__c", "npo02__Informal_Greeting__c",
        "Account_CASESAFEID__c",
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
    # Addressee = envelope name ("Mr. and Mrs. John Smith"). Formal greeting.
    # Salutation = letter opening ("Dear John,"). Informal greeting.
    # No fallback when informal is blank — leave Salutation empty so the
    # printer can apply their own default rather than us silently using
    # the wrong field.
    master["Addressee"] = master["npo02__Formal_Greeting__c"].fillna("")
    master["Salutation"] = master["npo02__Informal_Greeting__c"].fillna("")
    master["FirstName"] = master["First_Name__c"].fillna("")
    master["LastName"] = master["Last_Name__c"].fillna("")
    master["Address1"], master["Address2"] = _split_street(master["BillingStreet"])
    master["City"] = master["BillingCity"].fillna("")
    master["State"] = master["BillingState"].fillna("")
    # Force string dtype to defeat any later pandas int-coercion
    # (same belt-and-suspenders pattern as DonorID zero-padding).
    master["ZIP"] = _format_zip_series(master["BillingPostalCode"]).astype(object)
    master["Country"] = master["BillingCountry"].fillna("")

    # Rename code columns. DonorID must stay 9-char string with leading
    # zeros — both numeric IDs (zero-pad to 9) and S-prefixed IDs (already
    # 9 chars) flow through this same column. Pad defensively here to
    # survive any prior coercion to int.
    raw_donor = master.get("donor_id_9", pd.Series("", index=master.index))
    raw_donor = raw_donor.fillna("").astype(str).str.strip()
    is_s_prefixed = raw_donor.str.startswith("S")
    padded_numeric = raw_donor.str.zfill(9).str[:9]
    master["DonorID"] = padded_numeric.where(~is_s_prefixed, raw_donor)
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
    master["Account_CASESAFEID"] = master["Account_CASESAFEID__c"].fillna("").astype(str)

    logger.info(f"  Master DataFrame built: {len(master):,} rows in {time.time() - t0:.1f}s")

    # --- Per-donor campaign tag (multi-campaign routing — Item C) ---
    # `campaign_appeal_code_full` was attached by appeal_codes when a
    # multi-campaign run was requested. In single-campaign runs the
    # column may be missing or uniform; we derive the tag from the
    # CampaignAppealCode 9-char prefix (positions 1-5) as a fallback so
    # legacy single-campaign callers don't have to thread it through.
    if "campaign_appeal_code_full" in master.columns:
        campaign_tag = master["campaign_appeal_code_full"].fillna("").astype(str)
    else:
        campaign_tag = pd.Series("", index=master.index)
    fallback_tag = master["CampaignAppealCode"].fillna("").astype(str).str[:5]
    campaign_tag = campaign_tag.where(campaign_tag != "", fallback_tag)
    master["_campaign_tag"] = campaign_tag

    # --- Printer File: clean, non-holdout records only ---
    printer_mask = (~master["_is_excluded"]) & (~master["Holdout"])
    printer_df = master.loc[printer_mask, PRINTER_COLUMNS].copy()

    # --- Matchback File: mailed + holdouts only ---
    # Bill 2026-04-27: trim residue (quantity_reduction) and data-quality
    # exclusions (missing/duplicate Constituent_Id) leave the Matchback.
    # Their purpose is gift attribution: when a donor responds, the
    # Matchback row recovers segment/package/test detail. Donors who
    # weren't mailed and aren't a measurement control have no role here.
    # Audit trail for trimmed donors lives in the suppression audit log.
    matchback_mask = (
        ((~master["_is_excluded"]) & (~master["Holdout"]))   # mailed
        | master["Holdout"]                                   # holdouts (regardless of exclusion)
    )
    matchback_df = master.loc[matchback_mask, MATCHBACK_COLUMNS].copy()

    # --- Validations ---
    warnings = []
    if "InternalAppealCode" in printer_df.columns:
        warnings.append("FAIL: InternalAppealCode found in Printer File")
    if len(printer_df) > 0:
        bad_zips = printer_df[(printer_df["ZIP"] != "") & (printer_df["ZIP"].str.len() < 5)]
        if len(bad_zips) > 0:
            warnings.append(f"WARNING: {len(bad_zips)} ZIPs shorter than 5 chars")

    # QUOTE_NONNUMERIC: every non-numeric cell ships wrapped in double
    # quotes. Tells any RFC 4180-aware consumer (lettershop parsers,
    # Excel "From Text", Google Sheets import without auto-detect) that
    # the value is a string. ZIPs like 02861 keep their leading zero
    # because the CSV explicitly types them as text. Numeric columns
    # (Ask amounts, gift counts) write unquoted as before.
    printer_csv     = printer_df.to_csv(index=False,     quoting=csv.QUOTE_NONNUMERIC)
    matchback_csv   = matchback_df.to_csv(index=False,   quoting=csv.QUOTE_NONNUMERIC)
    exceptions_csv  = (exceptions_df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC)
                       if len(exceptions_df) > 0 else "")

    # --- Per-campaign split (Item C, 2026-04-28) ---
    # When a multi-campaign run has been requested, slice the master by
    # the `_campaign_tag` column and emit one Print + one Matchback CSV
    # per campaign so the writer can upload them as separate files. In
    # single-campaign runs `selected_campaigns` is None and we emit a
    # single entry keyed on the campaign_code argument so callers have
    # a uniform shape regardless of mode.
    per_campaign = {}
    if selected_campaigns:
        for c in selected_campaigns:
            ac = (c.get("appeal_code") or "")
            if not ac:
                continue
            prefix = ac[:5]
            sub_printer_mask = printer_mask & (master["_campaign_tag"].isin([ac, prefix]))
            sub_match_mask   = matchback_mask & (master["_campaign_tag"].isin([ac, prefix]))
            sub_printer_df   = master.loc[sub_printer_mask, PRINTER_COLUMNS].copy()
            sub_match_df     = master.loc[sub_match_mask,   MATCHBACK_COLUMNS].copy()
            per_campaign[ac] = {
                "appeal_code":      ac,
                "printer_df":       sub_printer_df,
                "matchback_df":     sub_match_df,
                "printer_count":    len(sub_printer_df),
                "matchback_count":  len(sub_match_df),
                "printer_csv":      sub_printer_df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC),
                "matchback_csv":    sub_match_df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC),
            }
            logger.info(f"  Per-campaign [{ac}]: printer={len(sub_printer_df):,}, "
                        f"matchback={len(sub_match_df):,}")
    else:
        per_campaign[campaign_code] = {
            "appeal_code":      campaign_code,
            "printer_df":       printer_df,
            "matchback_df":     matchback_df,
            "printer_count":    len(printer_df),
            "matchback_count":  len(matchback_df),
            "printer_csv":      printer_csv,
            "matchback_csv":    matchback_csv,
        }

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
    suppression_csv = supp_df.to_csv(index=False, quoting=csv.QUOTE_NONNUMERIC)
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
        "per_campaign": per_campaign,
    }
