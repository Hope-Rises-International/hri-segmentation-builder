"""Suppression engine: Tier 2 conditional + segment-level suppression per spec Section 6.2.

Tier 2 operates at donor level (after Tier 1 but before waterfall assignment output).
Segment-level suppression operates after waterfall assignment (economic gates).

All rules are toggleable per campaign.
"""

from __future__ import annotations
import logging
from datetime import datetime

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Default suppression toggle states per spec Section 6.2.2
DEFAULT_SUPPRESSION_TOGGLES = {
    # Tier 2 donor-level
    "newsletter_only": True,       # Suppress from appeals, include in newsletters
    "match_only": True,            # Suppress from standard, include in match
    "no_name_sharing": True,       # Suppress from list exchange
    "xmas_catalog_cap": True,      # 1 mailing/FY cap
    "xmas_easter_cap": True,       # 2 mailings/FY cap
    # Segment-level
    "recent_gift_window": False,   # OFF for first 2 campaigns (spec Section 6.2.2)
    "break_even_floor": True,      # Always active
    "response_rate_floor": True,   # Always active
    "frequency_cap": False,        # OFF for first 2 campaigns
    "holdout": True,               # 5% holdout ON
}

DEFAULT_SUPPRESSION_PARAMS = {
    "recent_gift_window_days": 45,
    "response_rate_floor_pct": 0.8,
    "frequency_cap_per_fy": 6,
    "holdout_pct": 5.0,
}


def apply_tier2_suppression(
    waterfall_result: pd.DataFrame,
    accounts_df: pd.DataFrame,
    campaign_type: str = "Appeal",
    toggles: dict | None = None,
) -> pd.DataFrame:
    """Apply Tier 2 communication preference suppressions.

    Operates on the waterfall result — suppresses donors who were assigned
    to segments but have communication preferences that exclude them from
    this campaign type.

    Args:
        waterfall_result: Output from run_waterfall().
        accounts_df: Full account data with suppression flag fields.
        campaign_type: "Appeal", "Newsletter", "Match", "Catalog".
        toggles: Suppression toggle overrides.

    Returns:
        Updated waterfall_result with additional suppression entries.
    """
    if toggles is None:
        toggles = DEFAULT_SUPPRESSION_TOGGLES.copy()

    result = waterfall_result.copy()
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Only suppress from assigned (non-suppressed) records
    assigned_mask = (result["segment_code"] != "") & (result["suppression_reason"] == "")
    assigned_ids = set(result.loc[assigned_mask, "account_id"])

    suppression_log = []  # (account_id, rule, tier)

    def _suppress_tier2(field_name, rule_name, condition_fn=None):
        """Suppress assigned donors based on a boolean field, with optional condition."""
        nonlocal result
        field_vals = accts.get(field_name, pd.Series(False, index=accts.index))
        flagged_ids = set(field_vals[field_vals == True].index) & assigned_ids

        if condition_fn is not None:
            flagged_ids = condition_fn(flagged_ids)

        if not flagged_ids:
            return 0

        mask = result["account_id"].isin(flagged_ids) & assigned_mask
        result.loc[mask, "suppression_reason"] = f"Tier2: {rule_name}"
        result.loc[mask, "segment_code"] = ""
        result.loc[mask, "segment_name"] = ""

        for aid in flagged_ids:
            suppression_log.append((aid, rule_name, 2))

        count = mask.sum()
        if count > 0:
            logger.info(f"  Tier 2 [{rule_name}]: {count:,} suppressed")
        return count

    logger.info(f"Applying Tier 2 suppression (campaign_type={campaign_type})...")

    # --- Newsletter and Prospectus Only / Newsletters Only ---
    # Suppress from appeals. Include in newsletter campaigns.
    if toggles.get("newsletter_only", True) and campaign_type != "Newsletter":
        _suppress_tier2("Newsletter_and_Prospectus_Only__c", "Newsletter/Prospectus Only")
        _suppress_tier2("Newsletters_Only__c", "Newsletters Only")

    # --- Match Only ---
    # Suppress from standard appeals. Include only in match campaigns.
    if toggles.get("match_only", True) and campaign_type != "Match":
        _suppress_tier2("Match_Only__c", "Match Only")

    # --- No Name Sharing ---
    # Suppress from list exchange. For now just flag — affects acquisition co-op, not DM.
    # Logged but not suppressed from the mailing itself.
    if toggles.get("no_name_sharing", True):
        no_share = accts.get("No_Name_Sharing__c", pd.Series(False, index=accts.index))
        no_share_count = (no_share == True).sum()
        if no_share_count > 0:
            logger.info(f"  Tier 2 [No Name Sharing]: {no_share_count:,} flagged (list exchange only, not suppressed from mailing)")

    # --- Frequency caps (Xmas Catalog 1/FY, Xmas/Easter 2/FY) ---
    # These require mailing history tracking from Campaign_Segment__c.
    # For Phase 3, log the flagged population. Full mailing count tracking
    # requires Salesforce query for prior campaign participation — deferred
    # to when Campaign_Segment__c records are being written (Phase 6).
    if toggles.get("xmas_catalog_cap", True):
        xmas_cat = accts.get("X1_Mailing_Xmas_Catalog__c", pd.Series(False, index=accts.index))
        xmas_cat_count = (xmas_cat == True).sum()
        if xmas_cat_count > 0:
            logger.info(f"  Tier 2 [1 Mailing Xmas Catalog]: {xmas_cat_count:,} flagged (frequency tracking deferred to Phase 6)")

    if toggles.get("xmas_easter_cap", True):
        xmas_easter = accts.get("X2_Mailings_Xmas_Appeal__c", pd.Series(False, index=accts.index))
        xmas_easter_count = (xmas_easter == True).sum()
        if xmas_easter_count > 0:
            logger.info(f"  Tier 2 [2 Mailings Xmas/Easter]: {xmas_easter_count:,} flagged (frequency tracking deferred to Phase 6)")

    tier2_total = (result["suppression_reason"].str.startswith("Tier2")).sum()
    logger.info(f"  --- Total Tier 2 suppressed: {tier2_total:,} ---")

    return result, suppression_log


def apply_segment_level_suppression(
    segment_summary: pd.DataFrame,
    cpp: float,
    toggles: dict | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """Apply segment-level economic suppression rules.

    Operates on the segment summary (one row per segment) after waterfall.
    Flags segments as below break-even, below response floor, etc.

    Args:
        segment_summary: DataFrame with Segment Code, Quantity, and economic columns.
        cpp: Cost per piece for this campaign.
        toggles: Suppression toggle overrides.
        params: Suppression parameter overrides.

    Returns:
        Updated segment_summary with Status column updated.
    """
    if toggles is None:
        toggles = DEFAULT_SUPPRESSION_TOGGLES.copy()
    if params is None:
        params = DEFAULT_SUPPRESSION_PARAMS.copy()

    result = segment_summary.copy()
    logger.info("Applying segment-level suppression...")

    # --- Break-even floor (always active) ---
    # A segment is below break-even when its projected response rate
    # can't cover the cost per piece from average gift.
    # Break-even rate = CPP / avg_gift
    if toggles.get("break_even_floor", True) and cpp > 0:
        for idx, row in result.iterrows():
            hist_avg = row.get("Hist. Avg Gift")
            hist_rr = row.get("Hist. Response Rate")
            if hist_avg and hist_rr:
                try:
                    avg_gift = float(hist_avg)
                    resp_rate = float(str(hist_rr).rstrip('%')) / 100
                    if avg_gift > 0:
                        be_rate = cpp / avg_gift
                        result.at[idx, "Break-Even Rate"] = f"{be_rate:.2%}"
                        if resp_rate < be_rate:
                            result.at[idx, "Status"] = "Below BE"
                            result.at[idx, "Margin"] = f"{(resp_rate - be_rate) * 100:.2f}%"
                        else:
                            result.at[idx, "Margin"] = f"+{(resp_rate - be_rate) * 100:.2f}%"
                except (ValueError, TypeError):
                    pass

    # --- Response rate floor ---
    if toggles.get("response_rate_floor", True):
        floor = params.get("response_rate_floor_pct", 0.8) / 100
        for idx, row in result.iterrows():
            hist_rr = row.get("Hist. Response Rate")
            if hist_rr:
                try:
                    resp_rate = float(str(hist_rr).rstrip('%')) / 100
                    if resp_rate < floor and row.get("Status") != "Below BE":
                        result.at[idx, "Status"] = "Below RR Floor"
                except (ValueError, TypeError):
                    pass

    # Count affected
    below_be = (result["Status"] == "Below BE").sum()
    below_rr = (result["Status"] == "Below RR Floor").sum()
    included = (result["Status"] == "Include").sum()

    logger.info(f"  Break-even: {below_be} segments below BE")
    logger.info(f"  Response rate floor: {below_rr} segments below {params.get('response_rate_floor_pct', 0.8)}% floor")
    logger.info(f"  Included: {included} segments")

    return result


def build_suppression_audit_log(
    waterfall_result: pd.DataFrame,
    tier2_log: list[tuple],
    campaign_id: str = "DIAGNOSTIC",
) -> pd.DataFrame:
    """Build the suppression audit log CSV.

    One row per suppressed donor with: account_id, rule, tier, campaign_id.
    Covers both Tier 1 (from waterfall) and Tier 2 (from this module).
    """
    rows = []

    # Tier 1 from waterfall result
    tier1 = waterfall_result[waterfall_result["suppression_reason"].str.startswith("Tier1")]
    for _, row in tier1.iterrows():
        rows.append({
            "account_id": row["account_id"],
            "suppression_rule": row["suppression_reason"],
            "tier": 1,
            "campaign_id": campaign_id,
        })

    # Tier 2
    for account_id, rule, tier in tier2_log:
        rows.append({
            "account_id": account_id,
            "suppression_rule": f"Tier2: {rule}",
            "tier": tier,
            "campaign_id": campaign_id,
        })

    df = pd.DataFrame(rows)
    logger.info(f"  Suppression audit log: {len(df):,} entries "
                f"(Tier 1: {len(tier1):,}, Tier 2: {len(tier2_log):,})")
    return df
