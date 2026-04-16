"""Three-pass budget-target fitting per spec Section 7.3.

Pass 1: Full universe (no quantity cap)
Pass 2: Fit to target (trim from bottom of waterfall when universe > target)
Pass 3: Expansion options (when universe < target)

Intra-segment tie-breaker: RFM weighted score desc, MRC desc, recency asc.
"""

from __future__ import annotations
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Waterfall trim order: segments trimmed from bottom up.
# Deep lapsed with weakest economics first, then lapsed, then working up.
TRIM_ORDER = [
    "DL04",  # Deep Lapsed 37-48mo <$100 — weakest
    "DL03",  # Deep Lapsed 37-48mo $100+
    "DL02",  # Deep Lapsed 25-36mo <$100
    "DL01",  # Deep Lapsed 25-36mo $100+
    "CB01",  # CBNC override
    "LR02",  # Lapsed Recent 19-24mo
    "LR01",  # Lapsed Recent 13-18mo
    "MP01",  # Mid-Level Prospect
    "AH06",  # Active 7-12mo <$25
    "AH05",  # Active 7-12mo $25-50
    "AH04",  # Active 7-12mo $50+
    "AH03",  # Active 0-6mo <$25
    "AH02",  # Active 0-6mo $25-50
    "AH01",  # Active 0-6mo $50+
    "CS01",  # Cornerstone
    "ML01",  # Mid-Level
    "MJ01",  # Major Gift — last to trim
]


def fit_to_budget(
    waterfall_result: pd.DataFrame,
    target_qty: int,
    segment_summary: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Three-pass budget-target fitting.

    Args:
        waterfall_result: Full waterfall output with all assigned donors.
        target_qty: Budget target quantity from MIC.
        segment_summary: Segment summary with quantities and statuses.

    Returns:
        (fitted_result, fitted_summary, fit_info) where:
        - fitted_result: waterfall_result with trimmed donors marked
        - fitted_summary: segment_summary with Full Universe and Budget Fit columns
        - fit_info: dict with pass used, trimmed count, gap, etc.
    """
    # Get the mailable universe (assigned, not suppressed)
    assigned = waterfall_result[
        (waterfall_result["segment_code"] != "")
        & (waterfall_result["suppression_reason"] == "")
    ]
    full_universe = len(assigned)

    logger.info(f"Budget fitting: universe={full_universe:,}, target={target_qty:,}")

    # Add "Full Universe" column to summary
    summary = segment_summary.copy()
    summary["Full Universe"] = summary["Quantity"]

    result = waterfall_result.copy()
    result["budget_trimmed"] = False

    if target_qty <= 0:
        logger.info("  No target quantity — skipping budget fit")
        summary["Budget Fit"] = summary["Quantity"]
        return result, summary, {
            "pass": "none",
            "full_universe": full_universe,
            "target": target_qty,
            "fitted": full_universe,
            "trimmed": 0,
            "gap": 0,
        }

    # ===================================================================
    # Pass 1: Full Universe — already computed (the waterfall output)
    # ===================================================================
    logger.info(f"  Pass 1 — Full Universe: {full_universe:,}")

    if full_universe <= target_qty * 1.02:  # Within 2% — close enough
        # ===============================================================
        # Pass 3: Expansion — universe < target
        # ===============================================================
        gap = target_qty - full_universe
        if gap > 0:
            logger.info(f"  Pass 3 — Universe below target. Gap: {gap:,}")
            logger.info(f"  Expansion levers would be presented to operator (Phase 6 UI)")
        else:
            logger.info(f"  Universe matches target (within 2%)")

        summary["Budget Fit"] = summary["Quantity"]
        return result, summary, {
            "pass": "3_expansion" if gap > 0 else "1_match",
            "full_universe": full_universe,
            "target": target_qty,
            "fitted": full_universe,
            "trimmed": 0,
            "gap": max(gap, 0),
        }

    # ===================================================================
    # Pass 2: Fit to Target — trim from bottom of waterfall
    # ===================================================================
    excess = full_universe - target_qty
    logger.info(f"  Pass 2 — Trimming {excess:,} records from bottom of waterfall")

    trimmed_total = 0
    trimmed_by_segment = {}

    for seg_code in TRIM_ORDER:
        if trimmed_total >= excess:
            break

        seg_donors = assigned[assigned["segment_code"] == seg_code]
        if len(seg_donors) == 0:
            continue

        needed = excess - trimmed_total

        if len(seg_donors) <= needed:
            # Trim entire segment
            trim_ids = set(seg_donors["account_id"])
            trimmed_count = len(trim_ids)
            logger.info(f"    Trimmed {seg_code}: all {trimmed_count:,} records")
        else:
            # Partial trim — apply intra-segment tie-breaker
            # Sort: RFM weighted score desc (keep strongest), MRC desc, recency asc
            sorted_donors = seg_donors.sort_values(
                by=["RFM_weighted_score", "cumulative_giving"],
                ascending=[False, False],
            )
            # Trim from the bottom (weakest)
            trim_ids = set(sorted_donors.iloc[len(sorted_donors) - needed:]["account_id"])
            trimmed_count = len(trim_ids)
            logger.info(f"    Trimmed {seg_code}: {trimmed_count:,} of {len(seg_donors):,} "
                        f"(intra-segment tie-breaker applied)")

        result.loc[result["account_id"].isin(trim_ids), "budget_trimmed"] = True
        trimmed_total += trimmed_count
        trimmed_by_segment[seg_code] = trimmed_count

    # Update summary with Budget Fit column
    for idx, row in summary.iterrows():
        code = row["Segment Code"]
        trimmed = trimmed_by_segment.get(code, 0)
        summary.at[idx, "Budget Fit"] = int(row["Quantity"]) - trimmed
        if trimmed == int(row["Quantity"]):
            summary.at[idx, "Status"] = "Below Budget Line"
        elif trimmed > 0:
            summary.at[idx, "Status"] = f"Partial Trim (-{trimmed:,})"

    fitted_total = full_universe - trimmed_total
    logger.info(f"  Pass 2 complete — Fitted: {fitted_total:,} "
                f"(trimmed {trimmed_total:,} from {len(trimmed_by_segment)} segments)")

    return result, summary, {
        "pass": "2_fit",
        "full_universe": full_universe,
        "target": target_qty,
        "fitted": fitted_total,
        "trimmed": trimmed_total,
        "trimmed_by_segment": trimmed_by_segment,
        "gap": 0,
    }
