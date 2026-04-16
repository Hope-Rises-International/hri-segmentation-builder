"""Salesforce query layer: fetch Accounts and Opportunities for segmentation."""

import logging
import time

from google.cloud import secretmanager
from simple_salesforce import Salesforce, SalesforceResourceNotFound
import pandas as pd

from config import GCP_PROJECT, SF_SECRETS, OPPORTUNITY_EARLIEST_DATE

logger = logging.getLogger(__name__)


def get_secret(client: secretmanager.SecretManagerServiceClient, secret_name: str) -> str:
    name = f"projects/{GCP_PROJECT}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


def connect_salesforce() -> Salesforce:
    """Connect to Salesforce using secrets from GCP Secret Manager."""
    sm = secretmanager.SecretManagerServiceClient()
    return Salesforce(
        username=get_secret(sm, SF_SECRETS["username"]),
        password=get_secret(sm, SF_SECRETS["password"]),
        security_token=get_secret(sm, SF_SECRETS["security_token"]),
        consumer_key=get_secret(sm, SF_SECRETS["consumer_key"]),
        consumer_secret=get_secret(sm, SF_SECRETS["consumer_secret"]),
    )


def query_all(sf: Salesforce, soql: str) -> list[dict]:
    """Execute SOQL and paginate through all results."""
    result = sf.query(soql)
    records = result["records"]
    while not result["done"]:
        result = sf.query_more(result["nextRecordsUrl"], identifier_is_url=True)
        records.extend(result["records"])
    for r in records:
        r.pop("attributes", None)
    return records


# ---------------------------------------------------------------------------
# Pass 1: Account rollup fields
# ---------------------------------------------------------------------------

ACCOUNT_SOQL = """
SELECT Id, Name, Constituent_Id__c,
       npo02__LastCloseDate__c, npo02__FirstCloseDate__c,
       npo02__NumberOfClosedOpps__c, npo02__TotalOppAmount__c,
       npo02__LargestAmount__c, npo02__AverageAmount__c,
       npo02__LastOppAmount__c, Days_Since_Last_Gift__c,
       Cornerstone_Partner__c, Miracle_Partner__c,
       Staff_Manager__c, Lifecycle_Stage__c,
       BillingState, BillingPostalCode
FROM Account
WHERE npo02__NumberOfClosedOpps__c > 0
  AND RecordType.Name = 'Household Account'
""".strip()

# ---------------------------------------------------------------------------
# Pass 2: Opportunity detail (5-year window for RFM)
# ---------------------------------------------------------------------------

OPPORTUNITY_SOQL = f"""
SELECT AccountId, Amount, CloseDate,
       npe03__Recurring_Donation__c, RecordType.Name
FROM Opportunity
WHERE IsWon = true
  AND Amount > 0
  AND Account.RecordType.Name = 'Household Account'
  AND RecordType.Name IN ('Donation', 'Funraise Donation')
  AND CloseDate >= {OPPORTUNITY_EARLIEST_DATE}
ORDER BY AccountId, CloseDate
""".strip()


def fetch_accounts(sf: Salesforce) -> pd.DataFrame:
    """Pass 1: Fetch all household accounts with giving history."""
    logger.info("Pass 1: Querying accounts...")
    start = time.time()
    records = query_all(sf, ACCOUNT_SOQL)
    elapsed = time.time() - start
    logger.info(f"  Fetched {len(records):,} accounts in {elapsed:.1f}s")
    return pd.DataFrame(records)


def fetch_opportunities(sf: Salesforce) -> pd.DataFrame:
    """Pass 2: Fetch opportunity detail for RFM computation (5-year window)."""
    logger.info("Pass 2: Querying opportunities...")
    start = time.time()
    records = query_all(sf, OPPORTUNITY_SOQL)
    elapsed = time.time() - start
    logger.info(f"  Fetched {len(records):,} opportunities in {elapsed:.1f}s")

    df = pd.DataFrame(records)
    # Flatten nested RecordType dict
    if "RecordType" in df.columns:
        df["RecordTypeName"] = df["RecordType"].apply(
            lambda x: x.get("Name") if isinstance(x, dict) else None
        )
        df.drop(columns=["RecordType"], inplace=True)
    return df


def probe_sustainer_field(sf: Salesforce) -> bool:
    """Check whether npsp__Sustainer__c exists on Account.

    Returns True if the field exists, False if not.
    """
    try:
        sf.query("SELECT Id, npsp__Sustainer__c FROM Account LIMIT 1")
        return True
    except (SalesforceResourceNotFound, Exception) as e:
        error_msg = str(e)
        if "No such column" in error_msg or "INVALID_FIELD" in error_msg:
            logger.info("npsp__Sustainer__c does not exist on Account object")
            return False
        # Re-raise unexpected errors
        raise
