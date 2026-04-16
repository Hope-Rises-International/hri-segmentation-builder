"""RFM engine: compute Recency, Frequency, Monetary buckets per account."""

from __future__ import annotations
import logging
from datetime import date

import pandas as pd
import numpy as np

from config import (
    RECENCY_BUCKETS,
    FREQUENCY_BUCKETS,
    MONETARY_BUCKETS,
    RFM_WEIGHTS,
)

logger = logging.getLogger(__name__)


def _assign_bucket(value, buckets: list[tuple], unbounded_high: bool = True) -> str:
    """Assign a value to one of the defined buckets.

    buckets: list of (label, lower, upper) where None means unbounded.
    """
    for label, lower, upper in buckets:
        lo = lower if lower is not None else -float("inf")
        hi = upper if upper is not None else float("inf")
        if lo <= value <= hi:
            return label
    return "UNCLASSIFIED"


def _bucket_score(label: str) -> int:
    """Convert bucket label to numeric score (higher = better).

    R1/F1/M1 = 5, R2/F2/M2 = 4, etc.
    """
    if not label or not label[-1].isdigit():
        return 1  # Minimum score for unclassified
    digit = int(label[-1])
    return max(6 - digit, 1)


def compute_rfm(
    accounts_df: pd.DataFrame,
    opps_df: pd.DataFrame,
    reference_date: date | None = None,
) -> pd.DataFrame:
    """Compute RFM scores for all accounts.

    Args:
        accounts_df: Pass 1 account data with rollup fields.
        opps_df: Pass 2 opportunity data (5-year window).
        reference_date: Date for recency calculation (default: today).

    Returns:
        DataFrame indexed by Account Id with RFM columns.
    """
    if reference_date is None:
        reference_date = date.today()

    logger.info(f"Computing RFM for {len(accounts_df):,} accounts (ref date: {reference_date})")

    # --- Recency: from Account rollup field ---
    rfm = accounts_df[["Id"]].copy()
    rfm.set_index("Id", inplace=True)

    # Parse last close date and compute months since last gift
    last_close = pd.to_datetime(accounts_df.set_index("Id")["npo02__LastCloseDate__c"])
    ref = pd.Timestamp(reference_date)
    months_since = ((ref - last_close).dt.days / 30.44).round(0)
    rfm["months_since_last_gift"] = months_since

    # Assign recency bucket
    rfm["R_bucket"] = rfm["months_since_last_gift"].apply(
        lambda m: _assign_bucket(m, RECENCY_BUCKETS) if pd.notna(m) else "R5"
    )

    # --- Frequency: count of opps in 5-year window ---
    if len(opps_df) > 0:
        opp_counts = opps_df.groupby("AccountId").size().rename("gifts_5yr")
    else:
        opp_counts = pd.Series(dtype=int, name="gifts_5yr")

    rfm["gifts_5yr"] = opp_counts.reindex(rfm.index).fillna(0).astype(int)
    rfm["F_bucket"] = rfm["gifts_5yr"].apply(
        lambda g: _assign_bucket(g, FREQUENCY_BUCKETS)
    )

    # --- Monetary: average gift in 5-year window ---
    if len(opps_df) > 0:
        avg_gift = opps_df.groupby("AccountId")["Amount"].mean().rename("avg_gift_5yr")
    else:
        avg_gift = pd.Series(dtype=float, name="avg_gift_5yr")

    rfm["avg_gift_5yr"] = avg_gift.reindex(rfm.index)
    rfm["M_bucket"] = rfm["avg_gift_5yr"].apply(
        lambda a: _assign_bucket(a, MONETARY_BUCKETS) if pd.notna(a) else "M5"
    )

    # --- Fallback for accounts with no opps in 5-year window ---
    # Use Account-level rollup (npo02__AverageAmount__c) as fallback
    outside_window = rfm["gifts_5yr"] == 0
    rfm["_outside_window"] = outside_window

    if outside_window.any():
        acct_avg = accounts_df.set_index("Id")["npo02__AverageAmount__c"]
        fallback_avg = acct_avg.reindex(rfm.index[outside_window])
        rfm.loc[outside_window, "avg_gift_5yr"] = fallback_avg
        rfm.loc[outside_window, "M_bucket"] = fallback_avg.apply(
            lambda a: _assign_bucket(a, MONETARY_BUCKETS) if pd.notna(a) else "M5"
        )
        # Frequency fallback: they had at least 1 gift ever
        rfm.loc[outside_window, "F_bucket"] = "F4"
        logger.info(f"  {outside_window.sum():,} accounts outside 5-year window (using rollup fallback)")

    # --- Composite RFM code and weighted score ---
    rfm["RFM_code"] = rfm["R_bucket"] + rfm["F_bucket"] + rfm["M_bucket"]
    rfm["RFM_weighted_score"] = (
        rfm["R_bucket"].apply(_bucket_score) * RFM_WEIGHTS["R"]
        + rfm["F_bucket"].apply(_bucket_score) * RFM_WEIGHTS["F"]
        + rfm["M_bucket"].apply(_bucket_score) * RFM_WEIGHTS["M"]
    )

    logger.info(f"  RFM computation complete. Score range: "
                f"{rfm['RFM_weighted_score'].min()}-{rfm['RFM_weighted_score'].max()}")

    return rfm
