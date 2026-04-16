"""Configuration: GCP, Salesforce, Sheets, RFM bucket definitions."""

from __future__ import annotations
from datetime import date

# --- GCP ---
GCP_PROJECT = "hri-receipt-automation"

# --- Salesforce secret names (GCP Secret Manager) ---
SF_SECRETS = {
    "username": "sfdc-username",
    "password": "sfdc-password",
    "security_token": "sfdc-security-token",
    "consumer_key": "sfdc-consumer-key",
    "consumer_secret": "sfdc-consumer-secret",
}

# --- MIC Google Sheet ---
MIC_SHEET_ID = "12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw"
MIC_CAMPAIGN_CALENDAR_TAB = "mic_flattened.csv"  # TODO: rename tab to "Campaign Calendar" when MIC is formalized
MIC_DRAFT_TAB = "Draft"

# --- Google Drive output folder ---
DRIVE_OUTPUT_FOLDER_ID = "1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC"

# --- Fiscal Year ---
FY_START_MONTH = 7  # July

# --- RFM Lookback ---
RFM_LOOKBACK_YEARS = 5
# 5-year window start: FY22 = July 1, 2021
OPPORTUNITY_EARLIEST_DATE = "2021-07-01"

# --- RFM Bucket Definitions (spec Section 5.3) ---
# Each tuple: (label, lower_bound_months, upper_bound_months)
RECENCY_BUCKETS = [
    ("R1", 0, 6),
    ("R2", 7, 12),
    ("R3", 13, 24),
    ("R4", 25, 36),
    ("R5", 37, None),   # 37+ months (includes deep lapsed and expired)
]

# Each tuple: (label, min_gifts, max_gifts) — None means unbounded
FREQUENCY_BUCKETS = [
    ("F1", 5, None),
    ("F2", 3, 4),
    ("F3", 2, 2),
    ("F4", 1, 1),
]

# Each tuple: (label, min_amount, max_amount) — None means unbounded
MONETARY_BUCKETS = [
    ("M1", 100, None),
    ("M2", 50, 99.99),
    ("M3", 25, 49.99),
    ("M4", 10, 24.99),
    ("M5", 0, 9.99),
]

# RFM weighting for DM: R×3, F×2, M×1 (spec Section 5.3)
RFM_WEIGHTS = {"R": 3, "F": 2, "M": 1}

# --- Draft Tab Columns (spec Section 3, Step 3) ---
DRAFT_COLUMNS = [
    "Segment Code",
    "Segment Name",
    "Quantity",
    "Hist. Response Rate",
    "Hist. Avg Gift",
    "Proj. Gross Revenue",
    "CPP",
    "Total Cost",
    "Proj. Net Revenue",
    "Break-Even Rate",
    "Margin",
    "Status",
]


def fy_label_for_date(d: date) -> str:
    """Return the FY label (e.g. 'FY25') for a given date."""
    if d.month >= FY_START_MONTH:
        return f"FY{(d.year + 1) % 100:02d}"
    else:
        return f"FY{d.year % 100:02d}"
