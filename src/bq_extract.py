"""Nightly SF data extract: Salesforce Bulk API 2.0 → GCS → BigQuery.

Triggered by Cloud Scheduler at 11 PM ET nightly.
Populates sf_cache.accounts and sf_cache.opportunities in BigQuery.
"""

from __future__ import annotations
import csv
import gzip
import io
import logging
import time
from datetime import datetime

from google.cloud import bigquery, storage
from simple_salesforce import Salesforce

from salesforce_client import connect_salesforce, query_all
from config import (
    GCP_PROJECT, OPPORTUNITY_EARLIEST_DATE, CBNC_EARLIEST_DATE,
)

logger = logging.getLogger(__name__)

GCS_BUCKET = "hri-sf-cache"
GCS_PREFIX = "salesforce"
BQ_DATASET = "sf_cache"
BQ_ACCOUNTS_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.accounts"
BQ_OPPORTUNITIES_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.opportunities"

# Account SOQL — same fields as salesforce_client.ACCOUNT_SOQL
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

# Opportunity SOQL — combined: 10-year window covers both RFM (5yr) and CBNC (10yr)
OPPORTUNITY_SOQL = f"""
SELECT AccountId, Amount, CloseDate,
       npe03__Recurring_Donation__c, RecordType.Name
FROM Opportunity
WHERE IsWon = true
  AND Amount > 0
  AND Account.RecordType.Name = 'Household Account'
  AND RecordType.Name IN ('Donation', 'Funraise Donation')
  AND CloseDate >= {CBNC_EARLIEST_DATE}
ORDER BY AccountId, CloseDate
""".strip()


def _records_to_csv_gz(records, extra_cols=None):
    """Convert SF records to gzipped CSV bytes."""
    if not records:
        return b"", []

    # Get field names from first record
    fields = [k for k in records[0].keys() if k != "attributes"]
    if extra_cols:
        fields.extend(extra_cols.keys())

    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8", newline="") as gz:
        writer = csv.DictWriter(
            gz, fieldnames=fields, extrasaction="ignore",
            quoting=csv.QUOTE_ALL,  # Force-quote all fields for BQ compatibility
        )
        writer.writeheader()
        for rec in records:
            row = {k: v for k, v in rec.items() if k != "attributes"}
            # Flatten nested dicts (e.g., RecordType.Name)
            for k, v in list(row.items()):
                if isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        if sub_k != "attributes":
                            row[f"{k}.{sub_k}"] = sub_v
                    del row[k]
            # Replace embedded newlines in string fields (BillingStreet, etc.)
            for k, v in row.items():
                if isinstance(v, str) and "\n" in v:
                    row[k] = v.replace("\n", " ")
            if extra_cols:
                row.update(extra_cols)
            writer.writerow(row)

    return buf.getvalue(), fields


def _upload_to_gcs(csv_gz_bytes, blob_name):
    """Upload gzipped CSV to GCS."""
    client = storage.Client(project=GCP_PROJECT)
    bucket = client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(csv_gz_bytes, content_type="application/gzip")
    gcs_uri = f"gs://{GCS_BUCKET}/{blob_name}"
    size_mb = len(csv_gz_bytes) / 1048576
    logger.info(f"  Uploaded {blob_name} ({size_mb:.1f} MB) to {gcs_uri}")
    return gcs_uri


def _load_gcs_to_bq(gcs_uri, table_id, fields):
    """Load gzipped CSV from GCS to BigQuery (full replace)."""
    client = bigquery.Client(project=GCP_PROJECT)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        allow_quoted_newlines=True,
    )

    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()  # Wait for completion

    table = client.get_table(table_id)
    logger.info(f"  Loaded {table.num_rows:,} rows to {table_id}")
    return table.num_rows


def _add_load_timestamp(table_id):
    """Add _load_timestamp column to a BQ table."""
    client = bigquery.Client(project=GCP_PROJECT)
    query = f"""
    ALTER TABLE `{table_id}`
    ADD COLUMN IF NOT EXISTS _load_timestamp TIMESTAMP;

    UPDATE `{table_id}`
    SET _load_timestamp = CURRENT_TIMESTAMP()
    WHERE TRUE;
    """
    # BQ doesn't support multi-statement in one query via the Python client easily.
    # Use two separate queries.
    try:
        client.query(f"ALTER TABLE `{table_id}` ADD COLUMN IF NOT EXISTS _load_timestamp TIMESTAMP").result()
    except Exception:
        pass  # Column may already exist

    client.query(f"UPDATE `{table_id}` SET _load_timestamp = CURRENT_TIMESTAMP() WHERE TRUE").result()
    logger.info(f"  Set _load_timestamp on {table_id}")


def run_nightly_extract():
    """Execute the full nightly extract: SF → GCS → BQ."""
    timings = {}
    date_str = datetime.now().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info(f"NIGHTLY SF EXTRACT — {date_str}")
    logger.info("=" * 60)

    # --- Connect to SF ---
    t0 = time.time()
    sf = connect_salesforce()
    timings["sf_connect"] = round(time.time() - t0, 1)
    logger.info(f"Salesforce connected ({timings['sf_connect']}s)")

    # --- Extract Accounts ---
    logger.info("Extracting accounts...")
    t0 = time.time()
    account_records = query_all(sf, ACCOUNT_SOQL)
    timings["sf_accounts"] = round(time.time() - t0, 1)
    logger.info(f"  Fetched {len(account_records):,} accounts in {timings['sf_accounts']}s")

    t0 = time.time()
    acct_csv_gz, acct_fields = _records_to_csv_gz(account_records)
    acct_gcs_uri = _upload_to_gcs(acct_csv_gz, f"{GCS_PREFIX}/{date_str}/accounts.csv.gz")
    acct_rows = _load_gcs_to_bq(acct_gcs_uri, BQ_ACCOUNTS_TABLE, acct_fields)
    _add_load_timestamp(BQ_ACCOUNTS_TABLE)
    timings["bq_accounts"] = round(time.time() - t0, 1)

    # --- Extract Opportunities ---
    logger.info("Extracting opportunities...")
    t0 = time.time()
    opp_records = query_all(sf, OPPORTUNITY_SOQL)
    timings["sf_opps"] = round(time.time() - t0, 1)
    logger.info(f"  Fetched {len(opp_records):,} opportunities in {timings['sf_opps']}s")

    # Flatten RecordType.Name
    for rec in opp_records:
        if "RecordType" in rec:
            rt = rec.pop("RecordType", {})
            if isinstance(rt, dict):
                rec["RecordType.Name"] = rt.get("Name", "")

    t0 = time.time()
    opp_csv_gz, opp_fields = _records_to_csv_gz(opp_records)
    opp_gcs_uri = _upload_to_gcs(opp_csv_gz, f"{GCS_PREFIX}/{date_str}/opportunities.csv.gz")
    opp_rows = _load_gcs_to_bq(opp_gcs_uri, BQ_OPPORTUNITIES_TABLE, opp_fields)
    _add_load_timestamp(BQ_OPPORTUNITIES_TABLE)
    timings["bq_opps"] = round(time.time() - t0, 1)

    total_time = sum(timings.values())
    logger.info("=" * 60)
    logger.info(f"EXTRACT COMPLETE — {total_time:.0f}s total")
    logger.info(f"  Accounts: {acct_rows:,} rows in BQ")
    logger.info(f"  Opportunities: {opp_rows:,} rows in BQ")
    logger.info(f"  Timings: {timings}")

    return {
        "status": "success",
        "date": date_str,
        "accounts": acct_rows,
        "opportunities": opp_rows,
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
