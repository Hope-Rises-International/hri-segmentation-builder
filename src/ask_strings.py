"""Ask string computation per spec Section 8.

Per-record ask amounts based on HPC or MRC, with multipliers, floors/ceilings,
and rounding (always UP to next increment).

Ask basis by segment:
- Active + Mid-Level + Mid-Level Prospect: HPC (demonstrated capacity)
- Lapsed + Deep Lapsed: MRC (more realistic anchor for stale donors)
- Cornerstone: HPC (high-value reactivation)
- New Donor: First gift amount (only data point)
- CBNC: MRC (irregular giving pattern, use most recent)
- Major Gift: No ask amounts (custom package, handwritten note)
"""

from __future__ import annotations
import logging
import math

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Default parameters (configurable per campaign)
DEFAULT_ASK_PARAMS = {
    "multipliers": [1.0, 1.5, 2.0],
    "floor": 15.0,
    "ceiling": 4999.99,
    "low_threshold": 100.0,   # Below this, round to nearest $5
    "high_threshold": 100.0,  # At or above, round to nearest $25
    "low_increment": 5,
    "high_increment": 25,
}

# Segment codes that use HPC as basis
HPC_SEGMENTS = {"AH01", "AH02", "AH03", "AH04", "AH05", "AH06",
                "ML01", "MP01", "CS01"}
# Segment codes that use MRC as basis
MRC_SEGMENTS = {"LR01", "LR02", "DL01", "DL02", "DL03", "DL04", "CB01"}
# New donor uses first gift
NEW_DONOR_SEGMENTS = {"ND01"}
# Major gift has no ask amounts
NO_ASK_SEGMENTS = {"MJ01", "SU01"}


def _round_up(amount: float, increment: int) -> float:
    """Round UP to next increment. Never round down.

    $22.50 with increment 5 → $25 (not $20).
    $110 with increment 25 → $125 (not $100).
    """
    if amount <= 0 or increment <= 0:
        return amount
    return math.ceil(amount / increment) * increment


def _round_ask(amount: float, params: dict) -> float:
    """Apply rounding rules: UP to nearest $5 below $100, UP to nearest $25 at/above $100."""
    if amount < params["high_threshold"]:
        return float(_round_up(amount, params["low_increment"]))
    else:
        return float(_round_up(amount, params["high_increment"]))


def _clamp(amount: float, floor: float, ceiling: float) -> float:
    """Clamp to floor/ceiling."""
    return max(floor, min(amount, ceiling))


def compute_ask_strings(
    waterfall_result: pd.DataFrame,
    accounts_df: pd.DataFrame,
    params: dict = None,
) -> pd.DataFrame:
    """Compute ask string arrays for all assigned donors.

    Args:
        waterfall_result: Waterfall output with segment assignments.
        accounts_df: Account data with HPC/MRC fields.
        params: Ask string parameter overrides.

    Returns:
        DataFrame with account_id, ask1, ask2, ask3, ask_label columns.
    """
    if params is None:
        params = DEFAULT_ASK_PARAMS.copy()

    multipliers = params["multipliers"]
    floor = params["floor"]
    ceiling = params["ceiling"]

    # Join account fields
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    hpc = pd.to_numeric(accts["npo02__LargestAmount__c"], errors="coerce").fillna(0)
    mrc = pd.to_numeric(accts["npo02__LastOppAmount__c"], errors="coerce").fillna(0)

    # Build result
    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
        & (~waterfall_result.get("budget_trimmed", pd.Series(False)))
    ].copy()

    asks = []
    for _, row in assigned.iterrows():
        acct_id = row["account_id"]
        seg = row["segment_code"]

        # Determine basis
        if seg in NO_ASK_SEGMENTS:
            asks.append({
                "account_id": acct_id,
                "ask_basis": "none",
                "ask_basis_amount": 0,
                "ask1": None,
                "ask2": None,
                "ask3": None,
                "ask_label": "",
            })
            continue

        if seg in HPC_SEGMENTS:
            basis = hpc.get(acct_id, 0) or 0
            basis_type = "HPC"
        elif seg in MRC_SEGMENTS:
            basis = mrc.get(acct_id, 0) or 0
            basis_type = "MRC"
        elif seg in NEW_DONOR_SEGMENTS:
            basis = mrc.get(acct_id, 0) or 0  # First gift = most recent for new donors
            basis_type = "FirstGift"
        else:
            basis = mrc.get(acct_id, 0) or 0
            basis_type = "MRC"

        if basis <= 0:
            basis = floor  # Fallback to floor

        # Compute ask amounts: [1x, 1.5x, 2x] with rounding UP
        raw_asks = [basis * m for m in multipliers]
        rounded_asks = [_round_ask(a, params) for a in raw_asks]
        # Clamp: round DOWN to nearest increment at ceiling (don't produce non-round values)
        rounded_ceiling = math.floor(ceiling / params["high_increment"]) * params["high_increment"]
        clamped_asks = [_clamp(a, floor, rounded_ceiling) for a in rounded_asks]

        # Deduplicate (if rounding/clamping causes same value) — use appropriate increment
        if clamped_asks[0] == clamped_asks[1]:
            inc = params["high_increment"] if clamped_asks[0] >= params["high_threshold"] else params["low_increment"]
            clamped_asks[1] = min(clamped_asks[0] + inc, rounded_ceiling)
        if clamped_asks[1] == clamped_asks[2]:
            inc = params["high_increment"] if clamped_asks[1] >= params["high_threshold"] else params["low_increment"]
            clamped_asks[2] = min(clamped_asks[1] + inc, rounded_ceiling)

        asks.append({
            "account_id": acct_id,
            "ask_basis": basis_type,
            "ask_basis_amount": basis,
            "ask1": clamped_asks[0],
            "ask2": clamped_asks[1],
            "ask3": clamped_asks[2],
            "ask_label": f"Best Gift of ${basis:,.2f}",
        })

    df = pd.DataFrame(asks)
    logger.info(f"  Ask strings computed for {len(df):,} donors")

    # Log basis distribution
    if len(df) > 0:
        basis_dist = df["ask_basis"].value_counts()
        for basis_type, count in basis_dist.items():
            logger.info(f"    {basis_type}: {count:,}")

    return df


def classify_reply_copy_tier(
    waterfall_result: pd.DataFrame,
    accounts_df: pd.DataFrame,
) -> pd.Series:
    """Assign reply copy tier per spec Section 8.4.

    ACTIVE:      Gave in current + prior FY
    LAPSED:      Last gift > 12 months
    NEW:         First gift in current FY
    REACTIVATED: Had 12+ month gap, gave in last 12 months

    Returns Series indexed by account_id with tier strings.
    """
    accts = accounts_df.copy()
    if "Id" in accts.columns:
        accts = accts.set_index("Id")

    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ]

    tiers = {}
    for _, row in assigned.iterrows():
        acct_id = row["account_id"]
        lifecycle = row.get("lifecycle_stage", "")

        if lifecycle == "New Donor":
            tiers[acct_id] = "NEW"
        elif lifecycle == "Reactivated":
            tiers[acct_id] = "REACTIVATED"
        elif lifecycle in ("Lapsed", "Deep Lapsed", "Expired"):
            tiers[acct_id] = "LAPSED"
        else:
            tiers[acct_id] = "ACTIVE"

    result = pd.Series(tiers, name="reply_copy_tier")
    logger.info(f"  Reply copy tiers: {result.value_counts().to_dict()}")
    return result
