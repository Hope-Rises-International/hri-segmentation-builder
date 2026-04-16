"""Waterfall assignment engine per spec Section 6.

Assigns each account to exactly one segment via priority hierarchy.
Mutually exclusive — once assigned at an active position, excluded from all subsequent.

Waterfall positions:
 1. Global Suppression (Tier 1 hard — always active, not toggleable)
 2. Major Gift Portfolio [toggle-gated]
 3. Mid-Level [toggle-gated]
 4. Monthly Sustainers [toggle-gated, default OFF]
 5. Cornerstone Partners [toggle-gated]
 6. New Donor [toggle-gated, default OFF]
 7. Active Housefile — High Value (R1-R2, F1-F2, M1-M2) [toggle-gated]
 8. Active Housefile — Standard (R1-R2, remaining) [toggle-gated]
 9. Mid-Level Prospect [toggle-gated]
10. Lapsed Recent (R3: 13-24 months) [toggle-gated]
11. Deep Lapsed (R4-R5: 25-48 months) [toggle-gated]
12. CBNC Flag Override (always active, not toggleable)
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
    MID_LEVEL_PROSPECT_MIN,
    MID_LEVEL_PROSPECT_MAX,
    MID_LEVEL_ACTIVE_MONTHS,
)

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

    # Parse numeric fields
    cumulative = pd.to_numeric(accts["npo02__TotalOppAmount__c"], errors="coerce").fillna(0)
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
    # Position 1: GLOBAL SUPPRESSION (always active, not toggleable)
    # Tier 1 hard suppressions — remove entirely
    # ===================================================================

    # All Household Members Deceased (npsp__All_Members_Deceased__c)
    # Only all-members-deceased suppresses. One-contact-deceased is Tier 3.
    _suppress(
        accts.get("npsp__All_Members_Deceased__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: All Members Deceased"
    )

    # Do Not Contact
    _suppress(
        accts.get("Do_Not_Contact__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: Do Not Contact"
    )

    # No Mail Code
    _suppress(
        accts.get("No_Mail_Code__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: No Mail"
    )

    # Undeliverable Address (NPSP flag)
    _suppress(
        accts.get("npsp__Undeliverable_Address__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: Undeliverable Address"
    )

    # NCOA Deceased
    _suppress(
        accts.get("NCOA_Deceased_Processing__c", pd.Series(False, index=accts.index)) == True,
        "Tier1: NCOA Deceased"
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
    # Position 2: MAJOR GIFT PORTFOLIO [toggle-gated]
    # ===================================================================
    if toggles.get("major_gift", True):
        staff_mgr = accts.get("Staff_Manager__c", pd.Series(None, index=accts.index))
        has_staff_mgr = staff_mgr.notna() & (staff_mgr != "")
        _assign(has_staff_mgr, "MJ01", 2)

    # ===================================================================
    # Position 3: MID-LEVEL [toggle-gated]
    # $1,000-$4,999.99 cumulative, gave in last 24 months
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

    # ===================================================================
    # Position 6: NEW DONOR [toggle-gated, default OFF]
    # First gift within 90 days
    # ===================================================================
    if toggles.get("new_donor", False):
        new_donor = accts["lifecycle_stage"] == "New Donor"
        _assign(new_donor, "ND01", 6)

    # ===================================================================
    # Positions 7-8: ACTIVE HOUSEFILE [toggle-gated]
    # R1-R2 (gave in last 12 months)
    # ===================================================================
    if toggles.get("active_housefile", True):
        active = accts["R_bucket"].isin(["R1", "R2"])

        # Position 7: High Value — R1-R2 AND F1-F2 AND M1-M2
        high_value = (
            active
            & accts["F_bucket"].isin(["F1", "F2"])
            & accts["M_bucket"].isin(["M1", "M2"])
        )

        # Sub-segment by recency and monetary for segment codes
        # AH01: Active 0-6mo $50+ avg
        avg_5yr = accts["avg_gift_5yr"].fillna(0)
        r1 = accts["R_bucket"] == "R1"
        r2 = accts["R_bucket"] == "R2"

        _assign(active & r1 & (avg_5yr >= 50), "AH01", 7)
        _assign(active & r1 & (avg_5yr >= 25) & (avg_5yr < 50), "AH02", 7)
        _assign(active & r1 & (avg_5yr < 25), "AH03", 7)

        # Position 8: Standard — R1-R2, remaining (not yet assigned)
        _assign(active & r2 & (avg_5yr >= 50), "AH04", 8)
        _assign(active & r2 & (avg_5yr >= 25) & (avg_5yr < 50), "AH05", 8)
        _assign(active & r2 & (avg_5yr < 25), "AH06", 8)

    # ===================================================================
    # Position 9: MID-LEVEL PROSPECT [toggle-gated]
    # $500-$999.99 cumulative, active 24 months
    # ===================================================================
    if toggles.get("mid_level_prospect", True):
        ml_prospect = (
            (cumulative >= MID_LEVEL_PROSPECT_MIN)
            & (cumulative <= MID_LEVEL_PROSPECT_MAX)
            & (months_since_last <= MID_LEVEL_ACTIVE_MONTHS)
        )
        _assign(ml_prospect, "MP01", 9)

    # ===================================================================
    # Position 10: LAPSED RECENT [toggle-gated]
    # R3: 13-24 months, 2+ lifetime gifts
    # ===================================================================
    if toggles.get("lapsed", True):
        total_gifts = pd.to_numeric(accts["npo02__NumberOfClosedOpps__c"], errors="coerce").fillna(0)
        lapsed = (accts["R_bucket"] == "R3") & (total_gifts >= 2)

        # Sub-segment: LR01 = 13-18 months, LR02 = 19-24 months
        _assign(lapsed & (months_since_last <= 18), "LR01", 10)
        _assign(lapsed & (months_since_last > 18), "LR02", 10)

    # ===================================================================
    # Position 11: DEEP LAPSED [toggle-gated]
    # R4-R5: 25-48 months, $10+ cumulative
    # ===================================================================
    if toggles.get("deep_lapsed", True):
        deep_lapsed_eligible = (
            accts["R_bucket"].isin(["R4", "R5"])
            & (cumulative >= 10)
            & (months_since_last <= 48)  # Cap at 48 months per spec
        )

        # Sub-segments by recency and cumulative
        r4 = accts["R_bucket"] == "R4"
        r5 = accts["R_bucket"] == "R5"

        _assign(deep_lapsed_eligible & r4 & (cumulative >= 100), "DL01", 11)
        _assign(deep_lapsed_eligible & r4 & (cumulative < 100), "DL02", 11)
        _assign(deep_lapsed_eligible & r5 & (cumulative >= 100), "DL03", 11)
        _assign(deep_lapsed_eligible & r5 & (cumulative < 100), "DL04", 11)

    # ===================================================================
    # Position 12: CBNC FLAG OVERRIDE (always active, not toggleable)
    # Donors with 2+ gifts in non-consecutive FYs who weren't assigned above
    # ===================================================================
    cbnc_mask = pd.Series(False, index=accts.index)
    cbnc_mask[cbnc_mask.index.isin(cbnc_ids)] = True
    _assign(cbnc_mask, "CB01", 12)

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
