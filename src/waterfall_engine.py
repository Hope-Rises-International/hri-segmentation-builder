"""Waterfall assignment engine per spec Section 6.

Assigns each account to exactly one segment via priority hierarchy.
Mutually exclusive — once assigned at an active position, excluded from all subsequent.

Toggles fall into two semantic buckets (Bill 2026-04-28):

 GROUP toggles (cohort identity — OFF removes donor from universe):
   cornerstone, sustainer, major_gift, mid_level.

 RFM toggles (lifecycle position — OFF skips the assignment gate; donor
 stays in universe and may route to other ON positions):
   active_housefile, lapsed, deep_lapsed.

Waterfall positions (v3.3, 2026-04-28):
  1.   Global Suppression — Tier 1 hard (always active)
  1.5  New Donor Welcome pre-emption (always active; can be flipped via
       welcome-series workflow but not via standard waterfall toggles)
  2.   Major Gift Portfolio [GROUP]
  3.   Mid-Level (24-month cumulative ≥ $750, no upper cap) [GROUP]
  4.   Monthly Sustainers [GROUP, default OFF]
  5.   Cornerstone Partners [GROUP]
  6.   Active Housefile — High Value (R1-R2, F1-F2, M1-M2) [RFM]
  7.   Active Housefile — Standard (R1-R2, remaining) [RFM]
  8.   Lapsed Recent (R3: 13-24 months) [RFM]
  9.   Deep Lapsed (R4-R5: 25-48 months) [RFM]
 10.   CBNC Flag Override (always active, not toggleable)

Eliminated in v3.3:
  - Mid-Level Prospect (was position 9, $500–$999.99). Sub-$750 active
    donors route to Active Housefile / Lapsed RFM. MP01 retained as
    a deprecated code in SEGMENT_CODES so historical Matchback files
    continue to resolve.
  - New Donor as a waterfall position (was position 6). Promoted to
    Tier 1.5 hard pre-emption so welcome-window donors are never caught
    by cornerstone / portfolio / RFM rules.
"""

from __future__ import annotations
import logging

import pandas as pd
import numpy as np

from config import (
    DEFAULT_TOGGLES,
    SEGMENT_CODES,
    MID_LEVEL_MIN,
    MID_LEVEL_MAX,
    MID_LEVEL_ACTIVE_MONTHS,
)

# v3.3 Tier 1 Account-level suppression (SPEC §6.1):
# - Type ∈ these values → suppress (organization categories that aren't
#   mailable as DM).
# - RecordType.Name ∈ these values → suppress (ALM-organization record
#   types whose history doesn't belong in a DM file).
TIER1_BLOCKED_ACCOUNT_TYPES = {"Donor Advised Fund", "Government"}
TIER1_BLOCKED_RECORD_TYPES = {
    "ALM Foundation Organization",
    "ALM Grants/Partners Household",
    "ALM Grants/Partners Organization",
}

logger = logging.getLogger(__name__)


def run_waterfall(
    accounts_df: pd.DataFrame,
    rfm_df: pd.DataFrame,
    lifecycle: pd.Series,
    cbnc_ids: set[str],
    toggles: dict[str, bool] | None = None,
) -> pd.DataFrame:
    """Run the waterfall assignment engine.

    Args:
        accounts_df: Full account data (indexed or with 'Id' column).
        rfm_df: RFM scores (indexed by Account Id).
        lifecycle: Lifecycle stage per account (indexed by Account Id).
        cbnc_ids: Set of Account IDs flagged as CBNC.
        toggles: Per-campaign toggle overrides. None = use defaults.

    Returns:
        DataFrame with columns: account_id, segment_code, segment_name,
        waterfall_position, suppression_reason (if suppressed).
    """
    if toggles is None:
        toggles = DEFAULT_TOGGLES.copy()

    # Build working DataFrame
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    # Merge RFM
    accts = accts.join(rfm_df[["R_bucket", "F_bucket", "M_bucket", "RFM_code",
                                "RFM_weighted_score", "months_since_last_gift", "avg_gift_5yr"]],
                       how="left")

    # Merge lifecycle
    accts["lifecycle_stage"] = lifecycle.reindex(accts.index).fillna("Expired")

    # Parse numeric fields.
    # Lifetime cumulative — used by the Deep Lapsed threshold ($10
    # lifetime) and historical comparisons. Kept under the original
    # `lifetime_cumulative` name.
    lifetime_cumulative = pd.to_numeric(
        accts["npo02__TotalOppAmount__c"], errors="coerce"
    ).fillna(0)
    # 24-month cumulative — v3.3 (2026-04-28). Mid-Level is redefined
    # as ≥ $750 over the last 24 months (no upper cap), matching TLC's
    # historical baseline math. Built from the two SF rolling-window
    # fields rather than lifetime, so a donor whose lifetime giving is
    # high but who hasn't given recently doesn't anchor at ML01.
    last_365 = pd.to_numeric(
        accts.get("Total_Gifts_Last_365_Days__c", pd.Series(0, index=accts.index)),
        errors="coerce",
    ).fillna(0)
    prior_365 = pd.to_numeric(
        accts.get("Total_Gifts_730_365_Days_Ago__c", pd.Series(0, index=accts.index)),
        errors="coerce",
    ).fillna(0)
    cumulative_24mo = last_365 + prior_365
    # Existing call sites used a single `cumulative` variable for both
    # Mid-Level cohort math and Deep Lapsed thresholds. Per v3.3,
    # `cumulative` now means the 24-month basis used by Mid-Level and
    # Deep Lapsed's recency reasoning; lifetime_cumulative covers the
    # $10 lifetime gate. Be careful when adding new rules — pick the
    # right basis. Most new logic should default to cumulative_24mo.
    cumulative = cumulative_24mo
    months_since_last = accts["months_since_last_gift"].fillna(999)

    # Results tracking
    assigned = pd.Series(False, index=accts.index)
    segment_code = pd.Series("", index=accts.index)
    segment_name = pd.Series("", index=accts.index)
    waterfall_pos = pd.Series(0, index=accts.index)
    suppression_reason = pd.Series("", index=accts.index)

    def _assign(mask, code, position):
        """Assign unassigned accounts matching mask to a segment."""
        eligible = mask & ~assigned
        segment_code[eligible] = code
        segment_name[eligible] = SEGMENT_CODES.get(code, code)
        waterfall_pos[eligible] = position
        assigned[eligible] = True
        count = eligible.sum()
        if count > 0:
            logger.info(f"  Position {position:2d} [{code}] {SEGMENT_CODES.get(code, code)}: {count:,}")
        return count

    def _suppress(mask, reason, position=1):
        """Mark accounts as suppressed."""
        eligible = mask & ~assigned
        suppression_reason[eligible] = reason
        waterfall_pos[eligible] = position
        assigned[eligible] = True
        count = eligible.sum()
        if count > 0:
            logger.info(f"  Suppressed [{reason}]: {count:,}")
        return count

    logger.info("Running waterfall assignment...")
    logger.info(f"  Total accounts: {len(accts):,}")
    logger.info(f"  Toggles: {toggles}")

    # ===================================================================
    # Position 1: GLOBAL SUPPRESSION — Tier 1 hard (always active)
    # v3.3 (2026-04-28): see SPEC §6.2.1.
    #
    #   - npsp__Undeliverable_Address__c and NCOA_Deceased_Processing__c
    #     dropped (Faircom processor handles).
    #   - No_Mail_Code__c moved to Tier 2 (suppression_engine).
    #   - Account.Type and Account.RecordType.Name added — defense in
    #     depth against DAF / Government / ALM-organization records.
    # ===================================================================

    # All Household Members Deceased — only the household-level flag
    # suppresses. One-contact-deceased was Tier 3, deleted in v3.3.
    _suppress(
        accts.get("npsp__All_Members_Deceased__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: All Members Deceased"
    )

    # Do Not Contact
    _suppress(
        accts.get("Do_Not_Contact__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: Do Not Contact"
    )

    # v3.3: Account.Type — DAF / Government. RFM filters were already
    # catching these naturally because organization accounts rarely have
    # household-style giving rollups, but the explicit suppression
    # protects cornerstone-only and any future RFM-bypassing flow.
    acct_type = accts.get("Type", pd.Series("", index=accts.index)).fillna("").astype(str)
    _suppress(
        acct_type.isin(TIER1_BLOCKED_ACCOUNT_TYPES),
        "Tier1: Account Type (DAF/Govt)",
    )

    # v3.3: Account.RecordType.Name — ALM-organization record types.
    # The current SOQL only pulls Household Account records, so this
    # mask is a no-op until a future flow widens the WHERE clause.
    # Kept here so the suppression travels with the engine, not the
    # extract.
    rt_name = accts.get("RecordTypeName", pd.Series("", index=accts.index)).fillna("").astype(str)
    _suppress(
        rt_name.isin(TIER1_BLOCKED_RECORD_TYPES),
        "Tier1: ALM Organization RecordType",
    )

    # Blank Address (BillingStreet/City/PostalCode null or empty)
    street = accts.get("BillingStreet", pd.Series("", index=accts.index)).fillna("")
    city = accts.get("BillingCity", pd.Series("", index=accts.index)).fillna("")
    zip_code = accts.get("BillingPostalCode", pd.Series("", index=accts.index)).fillna("")
    blank_address = (street.str.strip() == "") | (city.str.strip() == "") | (zip_code.str.strip() == "")
    _suppress(blank_address, "Tier1: Blank Address")

    suppressed_count = assigned.sum()
    logger.info(f"  --- Total Tier 1 suppressed: {suppressed_count:,} ---")

    # ===================================================================
    # Position 1.5: NEW DONOR WELCOME PRE-EMPTION (v3.3, SPEC §6.2.1)
    #
    # Hard pre-emption above the waterfall. A donor in the 90-day
    # welcome window is suppressed from any non-welcome appeal,
    # regardless of cornerstone / portfolio / RFM status — first-match-
    # wins doesn't get to override the welcome stream.
    #
    # The welcome series itself runs as a separate workflow (campaign
    # type = "Newsletter" / "Welcome") with all GROUP toggles OFF; that
    # path doesn't reach this engine. Here we always suppress.
    # ===================================================================
    _suppress(
        accts["lifecycle_stage"] == "New Donor",
        "Tier1.5: New Donor Welcome Pre-empt",
    )
    new_donor_count = (suppression_reason == "Tier1.5: New Donor Welcome Pre-empt").sum()
    logger.info(f"  --- Tier 1.5 New Donor pre-empted: {new_donor_count:,} ---")

    # ===================================================================
    # GROUP_EXCLUDE pass (v3.3, SPEC §6.1)
    #
    # Toggles split into two semantic buckets:
    #
    #   GROUP toggles (cohort identity — OFF removes donor entirely):
    #     cornerstone, sustainer, major_gift, mid_level.
    #
    #   RFM toggles (lifecycle position — OFF just skips the assignment
    #   gate; donor stays in universe and may route to other ON positions):
    #     active_housefile, lapsed, deep_lapsed. Implemented by the
    #     `if toggles.get(...):` blocks at the assignment positions below.
    #
    # GROUP OFF removes; RFM OFF skips. A Mid-Level donor who is also
    # R1 still routes to ML01 (waterfall position 3 wins) regardless of
    # active_housefile state — cohort identity is preserved.
    #
    # Removed in v3.3:
    #   - new_donor (promoted to Tier 1.5 pre-emption above)
    #   - mid_level_prospect (cohort eliminated; sub-$750 routes to AH/LR)
    # ===================================================================
    total_gifts_pre = pd.to_numeric(accts["npo02__NumberOfClosedOpps__c"], errors="coerce").fillna(0)
    staff_mgr = accts.get("Staff_Manager__c", pd.Series(None, index=accts.index))

    # (toggle_key, default_on, label, mask_builder).
    # Each mask_builder is a thunk returning a boolean Series the same
    # length as accts — identical to the cohort's `_assign` criteria so
    # toggle-OFF removes exactly the donors who would otherwise have
    # been assigned to that cohort.
    #
    # Mid-Level mask (v3.3): 24-month cumulative ≥ $750, no upper cap.
    # MID_LEVEL_MAX is math.inf so the mask cleanly covers the whole
    # right tail; donors over the historic $5K cap (44 in last refresh)
    # now flow into ML01 instead of disappearing.
    GROUP_EXCLUDE_RULES = [
        ("major_gift",  True,  "Major Gift Portfolio",
         lambda: staff_mgr.notna() & (staff_mgr != "")),
        ("mid_level",   True,  "Mid-Level",
         lambda: (cumulative >= MID_LEVEL_MIN) & (cumulative <= MID_LEVEL_MAX)
                 & (months_since_last <= MID_LEVEL_ACTIVE_MONTHS)),
        ("sustainer",   False, "Sustainers",
         lambda: accts.get("Miracle_Partner__c", pd.Series(False, index=accts.index)) == True),
        ("cornerstone", True,  "Cornerstone",
         lambda: accts.get("Cornerstone_Partner__c", pd.Series(False, index=accts.index)) == True),
    ]

    for toggle_key, default_on, label, mask_fn in GROUP_EXCLUDE_RULES:
        if toggles.get(toggle_key, default_on):
            continue
        _suppress(mask_fn(), f"Group Exclude: {label}")

    group_excluded = (suppression_reason.str.startswith("Group Exclude:")).sum()
    logger.info(f"  --- Group-exclude removed: {group_excluded:,} ---")

    # ===================================================================
    # Position 2: MAJOR GIFT PORTFOLIO [toggle-gated]
    # ===================================================================
    if toggles.get("major_gift", True):
        staff_mgr = accts.get("Staff_Manager__c", pd.Series(None, index=accts.index))
        has_staff_mgr = staff_mgr.notna() & (staff_mgr != "")
        _assign(has_staff_mgr, "MJ01", 2)

    # ===================================================================
    # Position 3: MID-LEVEL [toggle-gated]
    # v3.3: 24-month cumulative ≥ $750, no upper cap, gave in last 24mo.
    # See SPEC §5.4 for rationale (split-the-difference floor between
    # TLC's $500 and prior $1,000 spec; uncapped to capture donors with
    # $5K+ recent giving who aren't on a portfolio).
    # ===================================================================
    if toggles.get("mid_level", True):
        mid_level = (
            (cumulative >= MID_LEVEL_MIN)
            & (cumulative <= MID_LEVEL_MAX)
            & (months_since_last <= MID_LEVEL_ACTIVE_MONTHS)
        )
        _assign(mid_level, "ML01", 3)

    # ===================================================================
    # Position 4: MONTHLY SUSTAINERS [toggle-gated, default OFF]
    # ===================================================================
    if toggles.get("sustainer", False):
        miracle = accts.get("Miracle_Partner__c", pd.Series(False, index=accts.index))
        _assign(miracle == True, "SU01", 4)

    # ===================================================================
    # Position 5: CORNERSTONE PARTNERS [toggle-gated]
    # ===================================================================
    if toggles.get("cornerstone", True):
        cornerstone = accts.get("Cornerstone_Partner__c", pd.Series(False, index=accts.index))
        _assign(cornerstone == True, "CS01", 5)

    # v3.3: Position 6 (New Donor) eliminated — promoted to Tier 1.5
    # pre-emption (above the waterfall). The welcome series itself runs
    # as a separate workflow. Position numbers below shifted down.

    # ===================================================================
    # Positions 6-7: ACTIVE HOUSEFILE [toggle-gated]
    # R1-R2 (gave in last 12 months). Sub-AH01-06 by recency × monetary.
    # Renumbered from 7-8 in v3.3 (New Donor removal).
    # ===================================================================
    if toggles.get("active_housefile", True):
        active = accts["R_bucket"].isin(["R1", "R2"])

        # Sub-segment by recency and monetary for segment codes.
        # AH01: Active 0-6mo $50+ avg
        avg_5yr = accts["avg_gift_5yr"].fillna(0)
        r1 = accts["R_bucket"] == "R1"
        r2 = accts["R_bucket"] == "R2"

        _assign(active & r1 & (avg_5yr >= 50), "AH01", 6)
        _assign(active & r1 & (avg_5yr >= 25) & (avg_5yr < 50), "AH02", 6)
        _assign(active & r1 & (avg_5yr < 25), "AH03", 6)

        _assign(active & r2 & (avg_5yr >= 50), "AH04", 7)
        _assign(active & r2 & (avg_5yr >= 25) & (avg_5yr < 50), "AH05", 7)
        _assign(active & r2 & (avg_5yr < 25), "AH06", 7)

    # v3.3: Position 9 (Mid-Level Prospect) eliminated. Sub-$750
    # active donors fall through to AH/LR positions naturally.

    # ===================================================================
    # Position 8: LAPSED RECENT [toggle-gated]
    # R3: 13-24 months, 2+ lifetime gifts. Renumbered from 10 in v3.3.
    # ===================================================================
    if toggles.get("lapsed", True):
        total_gifts = pd.to_numeric(accts["npo02__NumberOfClosedOpps__c"], errors="coerce").fillna(0)
        lapsed = (accts["R_bucket"] == "R3") & (total_gifts >= 2)

        # Sub-segment: LR01 = 13-18 months, LR02 = 19-24 months
        _assign(lapsed & (months_since_last <= 18), "LR01", 8)
        _assign(lapsed & (months_since_last > 18), "LR02", 8)

    # ===================================================================
    # Position 9: DEEP LAPSED [toggle-gated]
    # R4-R5: 25-48 months, $10+ lifetime cumulative.
    # Note: lifetime_cumulative (NOT the 24-month cumulative used by
    # Mid-Level). Deep Lapsed donors have last gift 25-48 months ago, so
    # their 24-month cumulative is $0 by definition — we'd suppress the
    # whole cohort if we used the same basis as Mid-Level. Renumbered
    # from 11 in v3.3.
    # ===================================================================
    if toggles.get("deep_lapsed", True):
        deep_lapsed_eligible = (
            accts["R_bucket"].isin(["R4", "R5"])
            & (lifetime_cumulative >= 10)
            & (months_since_last <= 48)  # Cap at 48 months per spec
        )

        # Sub-segments by recency and lifetime cumulative
        r4 = accts["R_bucket"] == "R4"
        r5 = accts["R_bucket"] == "R5"

        _assign(deep_lapsed_eligible & r4 & (lifetime_cumulative >= 100), "DL01", 9)
        _assign(deep_lapsed_eligible & r4 & (lifetime_cumulative < 100), "DL02", 9)
        _assign(deep_lapsed_eligible & r5 & (lifetime_cumulative >= 100), "DL03", 9)
        _assign(deep_lapsed_eligible & r5 & (lifetime_cumulative < 100), "DL04", 9)

    # ===================================================================
    # Position 10: CBNC FLAG OVERRIDE (always active, not toggleable)
    # Donors with 2+ gifts in non-consecutive FYs who weren't assigned
    # above. Renumbered from 12 in v3.3.
    # ===================================================================
    cbnc_mask = pd.Series(False, index=accts.index)
    cbnc_mask[cbnc_mask.index.isin(cbnc_ids)] = True
    _assign(cbnc_mask, "CB01", 10)

    # ===================================================================
    # Summary
    # ===================================================================
    total_assigned = (segment_code != "").sum()
    total_suppressed = (suppression_reason != "").sum()
    total_unassigned = len(accts) - total_assigned - total_suppressed + total_suppressed  # suppressed are "assigned"
    unassigned = (~assigned).sum()

    logger.info(f"  --- Waterfall complete ---")
    logger.info(f"  Assigned to segments: {(segment_code != '').sum():,}")
    logger.info(f"  Suppressed (Tier 1): {(suppression_reason != '').sum():,}")
    logger.info(f"  Unassigned (fell through): {unassigned:,}")

    # Build result DataFrame
    result = pd.DataFrame({
        "account_id": accts.index,
        "segment_code": segment_code.values,
        "segment_name": segment_name.values,
        "waterfall_position": waterfall_pos.values,
        "suppression_reason": suppression_reason.values,
        "lifecycle_stage": accts["lifecycle_stage"].values,
        "R_bucket": accts["R_bucket"].values,
        "F_bucket": accts["F_bucket"].values,
        "M_bucket": accts["M_bucket"].values,
        "RFM_code": accts["RFM_code"].values,
        "RFM_weighted_score": accts["RFM_weighted_score"].values,
        "cumulative_giving": cumulative.values,
    })

    return result


def build_segment_summary(waterfall_result: pd.DataFrame) -> pd.DataFrame:
    """Build segment summary for Draft tab from waterfall results.

    One row per segment code with quantity. Economic columns left blank
    (populated in Phase 3 with historical data).
    """
    # Filter to assigned (non-suppressed, non-empty segment code)
    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ]

    if len(assigned) == 0:
        return pd.DataFrame(columns=["Segment Code", "Segment Name", "Quantity"])

    summary = (
        assigned
        .groupby(["segment_code", "segment_name"])
        .size()
        .reset_index(name="Quantity")
        .rename(columns={"segment_code": "Segment Code", "segment_name": "Segment Name"})
        .sort_values("Segment Code")
    )

    # Add empty economic columns (Phase 3)
    for col in ["Hist. Response Rate", "Hist. Avg Gift", "Proj. Gross Revenue",
                "CPP", "Total Cost", "Proj. Net Revenue", "Break-Even Rate", "Margin"]:
        summary[col] = ""

    summary["Status"] = "Include"

    return summary


def build_suppression_summary(waterfall_result: pd.DataFrame) -> pd.DataFrame:
    """Build suppression summary showing counts by reason."""
    suppressed = waterfall_result[waterfall_result["suppression_reason"] != ""]
    if len(suppressed) == 0:
        return pd.DataFrame(columns=["Suppression Rule", "Count"])

    return (
        suppressed
        .groupby("suppression_reason")
        .size()
        .reset_index(name="Count")
        .rename(columns={"suppression_reason": "Suppression Rule"})
        .sort_values("Count", ascending=False)
    )
