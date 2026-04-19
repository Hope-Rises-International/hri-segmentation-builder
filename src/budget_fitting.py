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
    segment_overrides: dict = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Three-pass budget-target fitting with optional per-segment operator overrides.

    Args:
        waterfall_result: Full waterfall output with all assigned donors.
        target_qty: Budget target quantity from MIC.
        segment_summary: Segment summary with quantities and statuses.
        segment_overrides: Optional per-segment operator controls:
            {segment_code: {'include': bool, 'percent_include': int}}
            - include=False: drop segment quantity to 0, freed slots re-fit
            - percent_include<100: keep top N% by RFM score, rest marked as
              quantity_reduction (go to Matchback, not Printer)

    Returns:
        (fitted_result, fitted_summary, fit_info) where:
        - fitted_result: waterfall_result with budget_trimmed and quantity_reduced flags
        - fitted_summary: segment_summary with Full Universe and Budget Fit columns
        - fit_info: dict with pass used, trimmed count, gap, etc.
    """
    segment_overrides = segment_overrides or {}

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
    result["quantity_reduced"] = False

    # ===================================================================
    # Operator Overrides: apply before budget fitting
    # ===================================================================
    overrides_applied = {}
    if segment_overrides:
        logger.info(f"  Applying operator overrides to {len(segment_overrides)} segments")
        for seg_code, override in segment_overrides.items():
            include = override.get("include", True)
            percent_include = override.get("percent_include", 100)

            seg_donors = assigned[assigned["segment_code"] == seg_code]
            if len(seg_donors) == 0:
                continue

            if not include:
                # Exclude entire segment — mark as budget_trimmed
                seg_ids = set(seg_donors["account_id"])
                result.loc[result["account_id"].isin(seg_ids), "budget_trimmed"] = True
                overrides_applied[seg_code] = {"excluded": len(seg_ids), "percent": 0}
                logger.info(f"    {seg_code}: EXCLUDED by operator ({len(seg_ids):,} donors)")
            elif percent_include < 100:
                # Keep top N% by RFM score, rest marked as quantity_reduced
                keep_count = max(1, int(len(seg_donors) * percent_include / 100))
                sorted_donors = seg_donors.sort_values(
                    by=["RFM_weighted_score", "cumulative_giving"],
                    ascending=[False, False],
                )
                reduce_ids = set(sorted_donors.iloc[keep_count:]["account_id"])
                result.loc[result["account_id"].isin(reduce_ids), "quantity_reduced"] = True
                overrides_applied[seg_code] = {
                    "kept": keep_count,
                    "reduced": len(reduce_ids),
                    "percent": percent_include,
                }
                logger.info(f"    {seg_code}: {percent_include}% include — "
                            f"keeping top {keep_count:,} of {len(seg_donors):,} by RFM score")

    # Recompute assigned universe after overrides (excluded = budget_trimmed treated as trimmed)
    assigned_after_overrides = assigned[
        ~assigned["account_id"].isin(
            set(result.loc[result["budget_trimmed"] == True, "account_id"]) |
            set(result.loc[result["quantity_reduced"] == True, "account_id"])
        )
    ]
    available_universe = len(assigned_after_overrides)

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
    # Pass 1: Full Universe (post-overrides)
    # ===================================================================
    logger.info(f"  Pass 1 — Full Universe: {full_universe:,} "
                f"(after overrides: {available_universe:,})")

    if available_universe <= target_qty * 1.02:  # Within 2% — close enough
        # ===============================================================
        # Pass 3: Expansion — universe < target
        # ===============================================================
        gap = target_qty - available_universe
        if gap > 0:
            logger.info(f"  Pass 3 — Universe below target. Gap: {gap:,}")
        else:
            logger.info(f"  Universe matches target (within 2%)")

        # Update summary with Budget Fit reflecting any overrides
        summary = _update_summary_with_overrides(summary, result, assigned, overrides_applied)
        return result, summary, {
            "pass": "3_expansion" if gap > 0 else "1_match",
            "full_universe": full_universe,
            "available_universe": available_universe,
            "target": target_qty,
            "fitted": available_universe,
            "trimmed": 0,
            "gap": max(gap, 0),
            "overrides_applied": overrides_applied,
        }

    # ===================================================================
    # Pass 2: Fit to Target — trim from bottom of waterfall
    # ===================================================================
    excess = available_universe - target_qty
    logger.info(f"  Pass 2 — Trimming {excess:,} records from bottom of waterfall")

    trimmed_total = 0
    trimmed_by_segment = {}

    for seg_code in TRIM_ORDER:
        if trimmed_total >= excess:
            break

        seg_donors = assigned_after_overrides[assigned_after_overrides["segment_code"] == seg_code]
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

    # Update summary with Budget Fit column (combining overrides + trim)
    summary = _update_summary_with_overrides(
        summary, result, assigned, overrides_applied, trimmed_by_segment
    )

    fitted_total = available_universe - trimmed_total
    logger.info(f"  Pass 2 complete — Fitted: {fitted_total:,} "
                f"(trimmed {trimmed_total:,} from {len(trimmed_by_segment)} segments)")

    return result, summary, {
        "pass": "2_fit",
        "full_universe": full_universe,
        "available_universe": available_universe,
        "target": target_qty,
        "fitted": fitted_total,
        "trimmed": trimmed_total,
        "trimmed_by_segment": trimmed_by_segment,
        "overrides_applied": overrides_applied,
        "gap": 0,
    }


def _update_summary_with_overrides(
    summary: pd.DataFrame,
    result: pd.DataFrame,
    assigned: pd.DataFrame,
    overrides_applied: dict,
    trimmed_by_segment: dict = None,
) -> pd.DataFrame:
    """Update segment summary with Budget Fit reflecting overrides + trim.

    Budget Fit = original Quantity - excluded - quantity_reduced - budget_trimmed.
    Status column reflects the operator action taken.
    """
    trimmed_by_segment = trimmed_by_segment or {}

    # Count per-segment how many records were excluded, reduced, or trimmed
    seg_stats = {}
    for seg_code in summary["Segment Code"].unique():
        seg_mask = result["segment_code"] == seg_code
        excluded = int((seg_mask & result["budget_trimmed"]).sum())
        reduced = int((seg_mask & result["quantity_reduced"]).sum())
        seg_stats[seg_code] = {
            "excluded": excluded,
            "reduced": reduced,
            "trimmed": trimmed_by_segment.get(seg_code, 0),
        }

    for idx, row in summary.iterrows():
        code = row["Segment Code"]
        qty = int(row["Quantity"])
        stats = seg_stats.get(code, {})
        # budget_trimmed + quantity_reduced flags cover both operator overrides and pass-2 trim
        removed = stats.get("excluded", 0) + stats.get("reduced", 0)
        summary.at[idx, "Budget Fit"] = qty - removed
        summary.at[idx, "Include"] = removed < qty

        override = overrides_applied.get(code, {})
        if override.get("percent", 100) == 0:
            summary.at[idx, "Status"] = "Excluded by operator"
            summary.at[idx, "% Include"] = 0
        elif "percent" in override and override["percent"] < 100:
            summary.at[idx, "Status"] = f"{override['percent']}% Include (top RFM)"
            summary.at[idx, "% Include"] = override["percent"]
        elif removed == qty:
            summary.at[idx, "Status"] = "Below Budget Line"
            summary.at[idx, "% Include"] = 100
        elif removed > 0:
            summary.at[idx, "Status"] = f"Partial Trim (-{removed:,})"
            summary.at[idx, "% Include"] = 100
        else:
            summary.at[idx, "Status"] = "Include"
            summary.at[idx, "% Include"] = 100

    return summary
