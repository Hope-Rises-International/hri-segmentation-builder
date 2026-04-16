"""CBNC (Consistent But Not Continuous) detection per spec Section 6.1 position 12.

CBNC donors have 2+ lifetime gifts in non-consecutive fiscal years over a 10-year window.
These donors would otherwise be suppressed by lapsed cutoffs but should be retained
because their giving pattern is reliable (e.g., gives every other year).
"""

from __future__ import annotations
import logging
from datetime import date

import pandas as pd

from config import FY_START_MONTH

logger = logging.getLogger(__name__)


def _close_date_to_fy(close_date: str) -> str:
    """Convert a CloseDate string to FY label (e.g., 'FY25')."""
    d = pd.to_datetime(close_date)
    if d.month >= FY_START_MONTH:
        return f"FY{(d.year + 1) % 100:02d}"
    else:
        return f"FY{d.year % 100:02d}"


def detect_cbnc(cbnc_opps_df: pd.DataFrame) -> set[str]:
    """Detect CBNC donors from 10-year opportunity data.

    A CBNC donor has 2+ gifts in non-consecutive fiscal years.
    Non-consecutive means there is at least one FY gap between any two giving FYs.

    Args:
        cbnc_opps_df: Opportunity DataFrame with AccountId and CloseDate columns.

    Returns:
        Set of Account IDs flagged as CBNC.
    """
    if len(cbnc_opps_df) == 0:
        return set()

    df = cbnc_opps_df.copy()
    df["FY"] = df["CloseDate"].apply(_close_date_to_fy)

    # Get unique FYs per account
    fy_by_account = df.groupby("AccountId")["FY"].apply(lambda x: sorted(set(x)))

    cbnc_ids = set()
    for acct_id, fys in fy_by_account.items():
        if len(fys) < 2:
            continue

        # Check for non-consecutive FYs (at least one gap)
        fy_numbers = sorted(int(fy[2:]) for fy in fys)
        has_gap = False
        for i in range(1, len(fy_numbers)):
            if fy_numbers[i] - fy_numbers[i - 1] > 1:
                has_gap = True
                break

        if has_gap:
            cbnc_ids.add(acct_id)

    logger.info(f"  CBNC detection: {len(cbnc_ids):,} donors flagged "
                f"(from {cbnc_opps_df['AccountId'].nunique():,} accounts with 10yr history)")

    return cbnc_ids
