"""Nightly SF data extract: Account-only → GCS → BigQuery.

Triggered by Cloud Scheduler at 11 PM ET nightly.
No Opportunity queries — CBNC and $500 DM flags approximated from Account rollup fields.

Architecture:
1. Bulk query Account (all rollup fields + per-FY gift counts)
2. CSV → GCS → BQ sf_cache.accounts_raw
3. BQ SQL: compute is_cbnc + has_dm_gift_500 → sf_cache.accounts
"""

from __future__ import annotations
import csv
import logging
import os
import tempfile
import time
from datetime import datetime

from google.cloud import bigquery, storage
from simple_salesforce import Salesforce

from salesforce_client import connect_salesforce, query_all
from config import GCP_PROJECT

logger = logging.getLogger(__name__)

GCS_BUCKET = "hri-sf-cache"
GCS_PREFIX = "salesforce"
BQ_DATASET = "sf_cache"
BQ_ACCOUNTS_RAW = f"{GCP_PROJECT}.{BQ_DATASET}.accounts_raw"
BQ_ACCOUNTS_FINAL = f"{GCP_PROJECT}.{BQ_DATASET}.accounts"

# Account SOQL — all rollup fields + per-FY gift counts for CBNC approximation
ACCOUNT_SOQL = """
SELECT Id, Name, Constituent_Id__c,
       First_Name__c, Last_Name__c, Special_Salutation__c,
       npo02__Formal_Greeting__c, npo02__Informal_Greeting__c,
       npo02__LastCloseDate__c, npo02__FirstCloseDate__c,
       npo02__NumberOfClosedOpps__c, npo02__TotalOppAmount__c,
       npo02__LargestAmount__c, npo02__AverageAmount__c,
       npo02__LastOppAmount__c, Days_Since_Last_Gift__c,
       First_Gift_Age_Days__c,
       Gifts_in_L12M__c, Cume_in_L12M__c,
       Total_Gifts_Last_365_Days__c, Total_Gifts_730_365_Days_Ago__c,
       Total_Gifts_This_Fiscal_Year__c, Total_Gifts_Last_Fiscal_Year__c,
       Number_of_Gifts_Last_Fiscal_Year__c,
       Number_of_Gifts_2_Fiscal_Years_Ago__c,
       Number_of_Gifts_3_Fiscal_Years_Ago__c,
       Number_of_Gifts_4_Fiscal_Years_Ago__c,
       Number_of_Gifts_5_Fiscal_Years_Ago__c,
       Cornerstone_Partner__c, Miracle_Partner__c,
       npsp__Sustainer__c,
       Staff_Manager__c, Lifecycle_Stage__c,
       BillingStreet, BillingCity, BillingState, BillingPostalCode, BillingCountry,
       General_Email__c,
       npsp__All_Members_Deceased__c, npsp__Undeliverable_Address__c,
       Primary_Contact_is_Deceased__c, Do_Not_Contact__c, No_Mail_Code__c,
       Address_Unknown__c, Not_Deliverable__c, NCOA_Deceased_Processing__c,
       Newsletter_and_Prospectus_Only__c, Newsletters_Only__c,
       No_Name_Sharing__c, Match_Only__c,
       X1_Mailing_Xmas_Catalog__c, X2_Mailings_Xmas_Appeal__c
FROM Account
WHERE npo02__NumberOfClosedOpps__c > 0
  AND RecordType.Name = 'Household Account'
""".strip()

# BQ merge query: compute is_cbnc and has_dm_gift_500 from Account rollup fields
# CBNC approximation: 2+ FYs with gifts AND at least one gap year between them (5-FY window)
# $500 DM approximation: npo02__LargestAmount__c >= 500
MERGE_SQL = f"""
CREATE OR REPLACE TABLE `{BQ_ACCOUNTS_FINAL}` AS
WITH fy_flags AS (
  SELECT
    Id,
    COALESCE(SAFE_CAST(Number_of_Gifts_Last_Fiscal_Year__c AS INT64), 0) AS fy1,
    COALESCE(SAFE_CAST(Number_of_Gifts_2_Fiscal_Years_Ago__c AS INT64), 0) AS fy2,
    COALESCE(SAFE_CAST(Number_of_Gifts_3_Fiscal_Years_Ago__c AS INT64), 0) AS fy3,
    COALESCE(SAFE_CAST(Number_of_Gifts_4_Fiscal_Years_Ago__c AS INT64), 0) AS fy4,
    COALESCE(SAFE_CAST(Number_of_Gifts_5_Fiscal_Years_Ago__c AS INT64), 0) AS fy5
  FROM `{BQ_ACCOUNTS_RAW}`
),
cbnc_check AS (
  SELECT
    Id,
    -- Count of FYs with gifts
    IF(fy1 > 0, 1, 0) + IF(fy2 > 0, 1, 0) + IF(fy3 > 0, 1, 0)
      + IF(fy4 > 0, 1, 0) + IF(fy5 > 0, 1, 0) AS giving_fys,
    -- Build a giving pattern string like '10101' for gap detection
    CONCAT(
      IF(fy1 > 0, '1', '0'),
      IF(fy2 > 0, '1', '0'),
      IF(fy3 > 0, '1', '0'),
      IF(fy4 > 0, '1', '0'),
      IF(fy5 > 0, '1', '0')
    ) AS pattern
  FROM fy_flags
)
SELECT
  a.*,
  -- CBNC: 2+ giving FYs AND pattern contains '10' (a gap after a giving year)
  CASE WHEN c.giving_fys >= 2 AND REGEXP_CONTAINS(c.pattern, r'10+1')
    THEN TRUE ELSE FALSE
  END AS is_cbnc,
  -- $500 DM approximation: largest gift >= 500
  CASE WHEN SAFE_CAST(a.npo02__LargestAmount__c AS FLOAT64) >= 500
    THEN TRUE ELSE FALSE
  END AS has_dm_gift_500,
  CURRENT_TIMESTAMP() AS _load_timestamp
FROM `{BQ_ACCOUNTS_RAW}` a
LEFT JOIN cbnc_check c ON a.Id = c.Id
"""


def _flatten_record(rec):
    """Flatten a SF record dict, removing attributes and handling nested dicts."""
    row = {}
    for k, v in rec.items():
        if k == "attributes":
            continue
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if sub_k != "attributes":
                    row[f"{k}_{sub_k}"] = sub_v
        elif isinstance(v, str):
            row[k] = v.replace("\n", " ").replace("\r", "")
        else:
            row[k] = v
    return row


def _write_records_to_tempfile(records):
    """Write SF records to a temp CSV file. Returns (path, fieldnames)."""
    if not records:
        return None, []

    flat_sample = [_flatten_record(r) for r in records[:10]]
    fieldnames = list(flat_sample[0].keys())

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(tmp, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()

    for i in range(0, len(records), 10000):
        for rec in records[i:i + 10000]:
            writer.writerow(_flatten_record(rec))

    tmp.close()
    logger.info(f"  Wrote {len(records):,} records to {tmp.name}")
    return tmp.name, fieldnames


def _upload_file_to_gcs(filepath, blob_name):
    """Upload a file to GCS."""
    client = storage.Client(project=GCP_PROJECT)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(filepath)
    gcs_uri = f"gs://{GCS_BUCKET}/{blob_name}"
    size_mb = os.path.getsize(filepath) / 1048576
    logger.info(f"  Uploaded {blob_name} ({size_mb:.1f} MB) to {gcs_uri}")
    return gcs_uri


def _load_gcs_to_bq(gcs_uri, table_id):
    """Load CSV from GCS to BigQuery (full replace, autodetect schema)."""
    client = bigquery.Client(project=GCP_PROJECT)
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        allow_quoted_newlines=True,
    )
    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()
    table = client.get_table(table_id)
    logger.info(f"  Loaded {table.num_rows:,} rows to {table_id}")
    return table.num_rows


def run_nightly_extract():
    """Execute nightly extract: SF Accounts → GCS → BQ → merge (compute flags)."""
    timings = {}
    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info(f"NIGHTLY SF EXTRACT (accounts only) — {date_str}")
    logger.info("=" * 60)

    # --- Connect to SF ---
    t0 = time.time()
    sf = connect_salesforce()
    timings["sf_connect"] = round(time.time() - t0, 1)
    logger.info(f"Salesforce connected ({timings['sf_connect']}s)")

    # --- Extract Accounts ---
    logger.info("Step 1: Querying accounts from Salesforce...")
    t0 = time.time()
    account_records = query_all(sf, ACCOUNT_SOQL)
    timings["sf_query"] = round(time.time() - t0, 1)
    logger.info(f"  Fetched {len(account_records):,} accounts in {timings['sf_query']}s")

    # --- Write to temp file → GCS → BQ ---
    logger.info("Step 2: Loading to BigQuery...")
    t0 = time.time()
    acct_path, _ = _write_records_to_tempfile(account_records)
    acct_gcs = _upload_file_to_gcs(acct_path, f"{GCS_PREFIX}/{date_str}/accounts.csv")
    acct_rows = _load_gcs_to_bq(acct_gcs, BQ_ACCOUNTS_RAW)
    os.unlink(acct_path)
    del account_records
    timings["bq_load"] = round(time.time() - t0, 1)

    # --- BQ merge: compute is_cbnc + has_dm_gift_500 ---
    logger.info("Step 3: Computing is_cbnc + has_dm_gift_500 flags...")
    t0 = time.time()
    bq_client = bigquery.Client(project=GCP_PROJECT)
    merge_job = bq_client.query(MERGE_SQL)
    merge_job.result()
    timings["bq_merge"] = round(time.time() - t0, 1)

    # Verify
    final_table = bq_client.get_table(BQ_ACCOUNTS_FINAL)
    final_rows = final_table.num_rows

    cbnc_count = list(bq_client.query(
        f"SELECT COUNT(*) AS n FROM `{BQ_ACCOUNTS_FINAL}` WHERE is_cbnc = TRUE"
    ).result())[0].n
    dm500_count = list(bq_client.query(
        f"SELECT COUNT(*) AS n FROM `{BQ_ACCOUNTS_FINAL}` WHERE has_dm_gift_500 = TRUE"
    ).result())[0].n

    total_time = sum(timings.values())
    logger.info("=" * 60)
    logger.info(f"EXTRACT COMPLETE — {total_time:.0f}s total")
    logger.info(f"  Accounts: {final_rows:,} rows")
    logger.info(f"  is_cbnc = TRUE: {cbnc_count:,}")
    logger.info(f"  has_dm_gift_500 = TRUE: {dm500_count:,}")
    logger.info(f"  Timings: {timings}")

    return {
        "status": "success",
        "date": date_str,
        "accounts_raw": acct_rows,
        "accounts_final": final_rows,
        "is_cbnc_count": cbnc_count,
        "has_dm_gift_500_count": dm500_count,
        "timings": timings,
        "total_seconds": round(total_time, 1),
    }


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run_nightly_extract()
    print(result)
