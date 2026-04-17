"""BigQuery cache reader for the segmentation pipeline.

Reads from sf_cache.accounts and sf_cache.opportunities instead of live SF.
Includes staleness check and fallback to live SF queries.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta

from google.cloud import bigquery
import pandas as pd

from config import GCP_PROJECT, OPPORTUNITY_EARLIEST_DATE

logger = logging.getLogger(__name__)

BQ_DATASET = "sf_cache"
BQ_ACCOUNTS_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.accounts"
BQ_OPPORTUNITIES_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.opportunities"

STALENESS_THRESHOLD_HOURS = 36


def check_cache_freshness():
    """Check if BQ cache exists and is fresh enough.

    Returns:
        (is_fresh, age_hours, load_timestamp) or (False, None, None) if no cache.
    """
    client = bigquery.Client(project=GCP_PROJECT)
    try:
        query = f"SELECT MAX(_load_timestamp) as last_load FROM `{BQ_ACCOUNTS_TABLE}`"
        result = client.query(query).result()
        row = list(result)[0]
        last_load = row.last_load
        if last_load is None:
            return False, None, None

        age = datetime.utcnow() - last_load.replace(tzinfo=None)
        age_hours = age.total_seconds() / 3600
        is_fresh = age_hours < STALENESS_THRESHOLD_HOURS

        logger.info(f"  BQ cache age: {age_hours:.1f} hours (threshold: {STALENESS_THRESHOLD_HOURS}h). "
                    f"{'FRESH' if is_fresh else 'STALE'}")
        return is_fresh, round(age_hours, 1), last_load.isoformat()
    except Exception as e:
        logger.warning(f"  BQ cache check failed: {e}")
        return False, None, None


def fetch_accounts_from_bq():
    """Read accounts from BQ cache. Returns DataFrame matching SF query output."""
    client = bigquery.Client(project=GCP_PROJECT)
    t0 = time.time()

    query = f"SELECT * EXCEPT(_load_timestamp) FROM `{BQ_ACCOUNTS_TABLE}`"
    df = client.query(query).to_dataframe()

    elapsed = round(time.time() - t0, 1)
    logger.info(f"  BQ accounts: {len(df):,} rows in {elapsed}s")
    return df


def fetch_opportunities_from_bq():
    """Read opportunities (5-year window) from BQ cache."""
    client = bigquery.Client(project=GCP_PROJECT)
    t0 = time.time()

    query = f"""
    SELECT * EXCEPT(_load_timestamp)
    FROM `{BQ_OPPORTUNITIES_TABLE}`
    WHERE CloseDate >= '{OPPORTUNITY_EARLIEST_DATE}'
    """
    df = client.query(query).to_dataframe()

    elapsed = round(time.time() - t0, 1)
    logger.info(f"  BQ opportunities (5yr): {len(df):,} rows in {elapsed}s")
    return df


def fetch_opportunities_cbnc_from_bq():
    """Read all opportunities (10-year window) from BQ cache for CBNC detection."""
    client = bigquery.Client(project=GCP_PROJECT)
    t0 = time.time()

    query = f"SELECT * EXCEPT(_load_timestamp) FROM `{BQ_OPPORTUNITIES_TABLE}`"
    df = client.query(query).to_dataframe()

    elapsed = round(time.time() - t0, 1)
    logger.info(f"  BQ opportunities (10yr CBNC): {len(df):,} rows in {elapsed}s")
    return df
