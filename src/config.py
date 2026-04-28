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


# --- Cohort → Required Campaign Prefix (Bill 2026-04-28, Item C) ---
# Each segment code maps to the campaign-prefix(es) it accepts. Used for
# multi-campaign runs to route donors to the correct campaign code.
#
#   "A"  — General housefile, Cornerstone, Sustainer, New Donor folds under A
#   "M"  — Mid-Level / Mid-Level Prospect; never A or J
#   "MJ" — Major Gift; M default, J wins if a J-prefix campaign is selected
#
# The validator uses this list at run-time: for every segment present in
# the assigned universe, at least one selected campaign must have the
# required prefix. No A→other auto-fallback.
COHORT_PREFIX_RULES = {
    # Housefile / Cornerstone / Sustainer / New Donor / CBNC — A only
    "AH01": "A", "AH02": "A", "AH03": "A",
    "AH04": "A", "AH05": "A", "AH06": "A",
    "LR01": "A", "LR02": "A",
    "DL01": "A", "DL02": "A", "DL03": "A", "DL04": "A",
    "CB01": "A",
    "ND01": "A",
    "SU01": "A",
    "CS01": "A", "CS02": "A",
    # Mid-Level / Mid-Level Prospect — M only
    "ML01": "M",
    "MP01": "M",
    # Major Gift — M default; J wins if a J-prefix campaign is in the
    # selection. Special-cased in the prefix resolver below.
    "MJ01": "MJ",
}

# Toggle → required prefix for UI / pre-run validation. Keyed by toggle
# key so the validator can be evaluated before the waterfall runs (i.e.
# from operator toggle state, without yet knowing which segment codes
# will end up populated). Stays in sync with COHORT_PREFIX_RULES.
TOGGLE_PREFIX_RULES = {
    "cornerstone":        "A",
    "sustainer":          "A",
    "new_donor":          "A",
    "active_housefile":   "A",
    "lapsed":             "A",
    "deep_lapsed":        "A",
    "mid_level":          "M",
    "mid_level_prospect": "M",
    "major_gift":         "MJ",   # M default, J wins if a J campaign is selected
}


def resolve_campaign_for_segment(segment_code, selected_campaigns):
    """Pick the campaign whose prefix matches a segment's cohort.

    Args:
        segment_code: HRI segment (AH01, ML01, MJ01, ...).
        selected_campaigns: list of dicts with at minimum
            `appeal_code` (9-char). The first character is the prefix
            (A, M, J, ...). Order in the list does not matter — Major
            Gift always prefers J over M when both are present.

    Returns:
        The matching campaign dict, or None if none match. Caller is
        responsible for surfacing a validation error in that case.

    Single-campaign back-compat: when only one campaign is selected,
    return it for every segment. The handoff explicitly preserves the
    legacy "one campaign, one Print pair" shape — operators who pick
    a single A2651 don't have to think about prefix routing. Cohort-
    prefix discipline is enforced via `validate_campaign_selection`
    upstream, so by the time we reach this resolver in a single-
    campaign run, the assigned universe is consistent with that one
    campaign's prefix anyway.
    """
    if not selected_campaigns:
        return None
    if len(selected_campaigns) == 1:
        return selected_campaigns[0]
    required = COHORT_PREFIX_RULES.get(segment_code)
    if required is None:
        return None
    by_prefix = {c.get("appeal_code", "")[:1]: c for c in selected_campaigns}
    if required == "MJ":
        # Major Gift: prefer J, fall back to M.
        return by_prefix.get("J") or by_prefix.get("M")
    return by_prefix.get(required)


def validate_campaign_selection(toggles, selected_campaigns):
    """Validate that every ON cohort has a matching campaign prefix.

    Returns:
        list of error strings. Empty list means valid.

    Errors are operator-facing — the message names the toggle and the
    missing prefix so the operator knows whether to add a campaign or
    flip the toggle off.
    """
    errors = []
    if not selected_campaigns:
        errors.append("No campaign selected. Pick at least one campaign.")
        return errors
    selected_prefixes = {c.get("appeal_code", "")[:1] for c in selected_campaigns}
    label_for = {
        "cornerstone":        "Cornerstone",
        "sustainer":          "Sustainer",
        "new_donor":          "New Donor",
        "active_housefile":   "Active Housefile",
        "lapsed":             "Lapsed",
        "deep_lapsed":        "Deep Lapsed",
        "mid_level":          "Mid-Level",
        "mid_level_prospect": "Mid-Level Prospect",
        "major_gift":         "Major Gift",
    }
    for tk, required in TOGGLE_PREFIX_RULES.items():
        if not toggles.get(tk, DEFAULT_TOGGLES.get(tk, False)):
            continue
        if required == "MJ":
            ok = ("M" in selected_prefixes) or ("J" in selected_prefixes)
            label = "M or J"
        else:
            ok = required in selected_prefixes
            label = required
        if not ok:
            errors.append(
                f"{label_for[tk]} toggle is ON but no {label}-prefix campaign "
                f"is in your selection. Add a {label} campaign or turn "
                f"{label_for[tk]} OFF."
            )
    return errors
