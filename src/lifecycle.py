"""Lifecycle stage computation per spec Section 5.2."""

from __future__ import annotations
import logging
from datetime import date

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Lifecycle stages (spec Section 5.2):
# New Donor:    First gift within last 90 days
# 2nd Year:     First gift 12-24 months ago, gave again in last 12 months
# Multi-Year:   3+ years of giving, gave in last 12 months
# Reactivated:  Had 13+ month gap, then gave in last 12 months
# Lapsed:       Last gift 13-24 months ago
# Deep Lapsed:  Last gift 25-48 months ago
# Expired:      Last gift 49+ months ago


def compute_lifecycle(accounts_df: pd.DataFrame, reference_date: date | None = None) -> pd.Series:
    """Compute lifecycle stage for each account.

    Uses Account rollup fields:
    - npo02__FirstCloseDate__c (inception)
    - npo02__LastCloseDate__c (recency)
    - Gifts_in_L12M__c (gifts in last 12 months)
    - First_Gift_Age_Days__c (days since first gift)
    - Days_Since_Last_Gift__c (days since last gift)

    Returns a Series indexed by Account Id with lifecycle stage strings.
    """
    if reference_date is None:
        reference_date = date.today()

    ref = pd.Timestamp(reference_date)
    df = accounts_df.set_index("Id").copy()

    first_close = pd.to_datetime(df["npo02__FirstCloseDate__c"])
    last_close = pd.to_datetime(df["npo02__LastCloseDate__c"])
    days_since_last = pd.to_numeric(df["Days_Since_Last_Gift__c"], errors="coerce")
    gifts_12m = pd.to_numeric(df["Gifts_in_L12M__c"], errors="coerce").fillna(0)
    first_gift_age_days = pd.to_numeric(df["First_Gift_Age_Days__c"], errors="coerce")

    months_since_last = days_since_last / 30.44
    months_since_first = first_gift_age_days / 30.44

    # Compute months between first and last gift for gap detection
    gift_span_months = ((last_close - first_close).dt.days / 30.44)

    # Default to Expired
    stage = pd.Series("Expired", index=df.index)

    # Deep Lapsed: last gift 25-48 months ago
    stage[(months_since_last >= 25) & (months_since_last <= 48)] = "Deep Lapsed"

    # Lapsed (LYBUNT): last gift 13-24 months ago
    stage[(months_since_last >= 13) & (months_since_last < 25)] = "Lapsed"

    # Now handle accounts that gave in last 12 months
    gave_recently = months_since_last < 13  # use 13-month grace

    # Multi-Year: 3+ years of giving history, gave in last 12 months
    multi_year = gave_recently & (months_since_first >= 36)
    stage[multi_year] = "Multi-Year"

    # 2nd Year: first gift 12-24 months ago, gave again in last 12 months
    second_year = gave_recently & (months_since_first >= 12) & (months_since_first < 36) & (gifts_12m >= 1)
    stage[second_year] = "2nd Year"

    # Reactivated: had 13+ month gap, then gave in last 12 months
    # We detect this by: gave recently, but their giving history has a gap
    # Proxy: total lifetime gifts > gifts_12m (they gave before the last 12 months)
    # AND months_since_first > 13 (not a brand new donor)
    # AND they would otherwise be multi-year or 2nd year but had a gap
    total_gifts = pd.to_numeric(df["npo02__NumberOfClosedOpps__c"], errors="coerce").fillna(0)
    prior_gifts = total_gifts - gifts_12m
    # A donor is reactivated if they gave recently, had prior gifts,
    # and there was a 13+ month gap between their last pre-recent gift and their recent gift
    # Best proxy with rollup fields: they have prior gifts AND their giving span
    # is much longer than 12 months AND they weren't continuously giving
    # Use Total_Gifts_Last_365_Days__c vs total giving to detect gap
    # If they gave recently but NOT in the 13-24 month window, there was a gap
    # Proxy: if prior_gifts > 0 and months_since_first > 13, check if giving is clustered
    # Actual SF field: Total_Gifts_730_365_Days_Ago__c = "Total Gifts 13-24 Months Ago"
    gifts_13_24 = pd.to_numeric(df.get("Total_Gifts_730_365_Days_Ago__c",
                                       pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    reactivated = (
        gave_recently
        & (prior_gifts > 0)
        & (months_since_first >= 13)
        & (gifts_13_24 == 0)
        & (months_since_first < 36)  # Multi-year donors aren't reactivated by this proxy
    )
    stage[reactivated] = "Reactivated"

    # Also catch multi-year reactivations: gave recently, 3+ year history, but had a gap
    reactivated_multi = (
        gave_recently
        & (prior_gifts > 0)
        & (months_since_first >= 36)
        & (gifts_13_24 == 0)
    )
    stage[reactivated_multi] = "Reactivated"

    # New Donor: first gift within last 90 days (overrides all above)
    new_donor = first_gift_age_days <= 90
    stage[new_donor] = "New Donor"

    # Log distribution
    dist = stage.value_counts()
    logger.info(f"  Lifecycle distribution:")
    for s, c in dist.items():
        logger.info(f"    {s}: {c:,}")

    return stage
