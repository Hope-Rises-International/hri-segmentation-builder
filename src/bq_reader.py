"""BigQuery cache reader for the segmentation pipeline.

Reads from sf_cache.accounts (with pre-computed is_cbnc and has_dm_gift_500 flags).
No opportunity data needed at pipeline runtime — flags are pre-computed nightly.

Includes staleness check and fallback to live SF queries.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta

from google.cloud import bigquery
import pandas as pd

from config import GCP_PROJECT

logger = logging.getLogger(__name__)

BQ_DATASET = "sf_cache"
BQ_ACCOUNTS_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.accounts"

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
    """Read accounts from BQ cache (includes pre-computed is_cbnc and has_dm_gift_500).

    Returns DataFrame matching SF query output plus two flag columns.
    Uses to_dataframe() with pinned pyarrow==14.0.2 and db-dtypes==1.2.0 for Python 3.9.
    """
    client = bigquery.Client(project=GCP_PROJECT)
    t0 = time.time()

    query = f"SELECT * EXCEPT(_load_timestamp) FROM `{BQ_ACCOUNTS_TABLE}`"
    df = client.query(query).to_dataframe(create_bqstorage_client=False)

    # bq_extract flattens nested SOQL relationships with an underscore
    # (`RecordType: {Name: ...}` → `RecordType_Name`), but the live-SF
    # path in salesforce_client.fetch_accounts produces `RecordTypeName`
    # without the underscore. Normalize to the live-SF name so the
    # waterfall engine can read either source uniformly.
    if "RecordType_Name" in df.columns and "RecordTypeName" not in df.columns:
        df = df.rename(columns={"RecordType_Name": "RecordTypeName"})

    elapsed = round(time.time() - t0, 1)
    cbnc_count = df["is_cbnc"].sum() if "is_cbnc" in df.columns else 0
    dm500_count = df["has_dm_gift_500"].sum() if "has_dm_gift_500" in df.columns else 0
    logger.info(f"  BQ accounts: {len(df):,} rows in {elapsed}s "
                f"(is_cbnc={cbnc_count:,}, has_dm_gift_500={dm500_count:,})")
    return df
