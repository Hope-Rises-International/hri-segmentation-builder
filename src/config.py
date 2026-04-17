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


# --- CBNC Lookback ---
CBNC_LOOKBACK_YEARS = 10
# 10-year window start: FY17 = July 1, 2016
CBNC_EARLIEST_DATE = "2016-07-01"

# --- Lifecycle Stage Definitions (spec Section 5.2) ---
# Thresholds in days
NEW_DONOR_WINDOW_DAYS = 90
SECOND_YEAR_MIN_DAYS = 365
SECOND_YEAR_MAX_DAYS = 730

# --- Giving Tier Thresholds (spec Section 5.4) ---
MID_LEVEL_MIN = 1000.0
MID_LEVEL_MAX = 4999.99
MID_LEVEL_PROSPECT_MIN = 500.0
MID_LEVEL_PROSPECT_MAX = 999.99
MID_LEVEL_ACTIVE_MONTHS = 24  # gave in last 24 months
DM_GIFT_HIGH_THRESHOLD = 500.0  # no single DM gift $500+

# --- Segment Code Registry (spec Section 9.3) ---
SEGMENT_CODES = {
    "MJ01": "Major Gift Custom Package",
    "ML01": "Mid-Level ($1,000–$4,999.99)",
    "SU01": "Sustainer (Miracle Partner)",
    "CS01": "Cornerstone Partner",
    "ND01": "New Donor",
    "AH01": "Active 0–6mo $50+ avg",
    "AH02": "Active 0–6mo $25–$49.99 avg",
    "AH03": "Active 0–6mo under $25 avg",
    "AH04": "Active 7–12mo $50+ avg",
    "AH05": "Active 7–12mo $25–$49.99 avg",
    "AH06": "Active 7–12mo under $25 avg",
    "MP01": "Mid-Level Prospect ($500–$999.99)",
    "LR01": "Lapsed Recent 13–18mo",
    "LR02": "Lapsed Recent 19–24mo",
    "DL01": "Deep Lapsed 25–36mo $100+ cum",
    "DL02": "Deep Lapsed 25–36mo under $100 cum",
    "DL03": "Deep Lapsed 37–48mo $100+ cum",
    "DL04": "Deep Lapsed 37–48mo under $100 cum",
    "CB01": "CBNC Override",
}

# --- PackageCode Routing (spec Section 6.1) ---
# Configurable per segment group. Lettershop sorts on PackageCode to route creative.
# Read from MIC Segment Rules tab at runtime; these are defaults.
DEFAULT_PACKAGE_CODES = {
    # Active Housefile → P01 (standard DM package)
    "AH": "P01",
    # Lapsed → P01
    "LR": "P01",
    # Deep Lapsed → P01
    "DL": "P01",
    # CBNC → P01
    "CB": "P01",
    # Mid-Level → P02 (high-touch: better paper, first-class postage)
    "ML": "P02",
    # Mid-Level Prospect → P01 (standard with upgrade messaging)
    "MP": "P01",
    # Cornerstone → P03 (legacy ALM branding, distinct package)
    "CS": "P03",
    # Major Gift → P04 (custom package, no ask amounts)
    "MJ": "P04",
    # Sustainer → P01
    "SU": "P01",
    # New Donor → P01
    "ND": "P01",
}


def get_package_code(segment_code, package_overrides=None):
    """Get PackageCode for a segment code.

    Looks up by 2-char prefix (segment group). Overrides take precedence.
    """
    overrides = package_overrides or {}
    # Check full segment code first (e.g., "CS01" override)
    if segment_code in overrides:
        return overrides[segment_code]
    # Then check 2-char prefix (e.g., "CS" → P03)
    prefix = segment_code[:2]
    if prefix in overrides:
        return overrides[prefix]
    return DEFAULT_PACKAGE_CODES.get(prefix, "P01")


# --- Default Waterfall Toggle States (spec Section 3, Step 2) ---
DEFAULT_TOGGLES = {
    "major_gift":       True,
    "mid_level":        True,
    "sustainer":        False,   # Default OFF — include for year-end/emergency
    "cornerstone":      True,
    "new_donor":        False,   # Default OFF — welcome window
    "active_housefile": True,
    "mid_level_prospect": True,
    "lapsed":           True,
    "deep_lapsed":      True,
}


def fy_label_for_date(d: date) -> str:
    """Return the FY label (e.g. 'FY25') for a given date."""
    if d.month >= FY_START_MONTH:
        return f"FY{(d.year + 1) % 100:02d}"
    else:
        return f"FY{d.year % 100:02d}"
