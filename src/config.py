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
# v3.3 (2026-04-28): Mid-Level redefined to a $750 floor with no upper
# cap, and the basis switched from lifetime cumulative
# (npo02__TotalOppAmount__c) to 24-month cumulative
# (Total_Gifts_Last_365_Days__c + Total_Gifts_730_365_Days_Ago__c). See
# SPEC §5.4. The MAX constant is kept as math.inf so existing callers
# that read it still work without changes; remove once nobody references it.
import math
MID_LEVEL_MIN = 750.0
MID_LEVEL_MAX = math.inf       # v3.3: no upper cap
# v3.3: Mid-Level Prospect cohort eliminated. Sub-$750 active donors
# route to Active Housefile / Lapsed RFM positions instead. The MP01
# code is preserved in SEGMENT_CODES below as a deprecated entry so
# historical Matchback files / Salesforce Campaign_Segment__c records
# referencing it continue to resolve. Constants kept for the same
# reason — easy reinstatement if Erica/Jessica need MP01 back.
MID_LEVEL_PROSPECT_MIN = 500.0
MID_LEVEL_PROSPECT_MAX = 999.99
MID_LEVEL_ACTIVE_MONTHS = 24  # gave in last 24 months
DM_GIFT_HIGH_THRESHOLD = 500.0  # no single DM gift $500+

# --- Segment Code Registry (spec Section 9.3) ---
SEGMENT_CODES = {
    "MJ01": "Major Gift Custom Package",
    "ML01": "Mid-Level (24-mo cumulative ≥ $750, no upper cap)",
    "SU01": "Sustainer (Miracle Partner)",
    "CS01": "Cornerstone Partner",
    "ND01": "New Donor",
    "AH01": "Active 0–6mo $50+ avg",
    "AH02": "Active 0–6mo $25–$49.99 avg",
    "AH03": "Active 0–6mo under $25 avg",
    "AH04": "Active 7–12mo $50+ avg",
    "AH05": "Active 7–12mo $25–$49.99 avg",
    "AH06": "Active 7–12mo under $25 avg",
    # v3.3: deprecated. Mid-Level Prospect cohort eliminated; sub-$750
    # active donors route to Active Housefile / Lapsed RFM. Code kept
    # so historical Matchback/Campaign_Segment__c rows still resolve.
    "MP01": "Mid-Level Prospect (DEPRECATED v3.3 — code kept for historical files only)",
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

# v3.4.1 (2026-04-29): California panel for Shipping campaigns.
# Bill: California has charitable-solicitation disclosure requirements
# that make the standard shipping creative non-compliant. CA donors
# under any Shipping or Christmas Shipping campaign (incl. chaser
# variants) route to a single CA panel package — `CA1` — regardless of
# segment. Cornerstone, Mid-Level, Major Gift CA donors all bucket
# into CA1 because the lettershop only has one non-shipping creative.
# If we ever need per-segment CA variants, switch this constant to a
# segment-prefix dict (CA1_AH, CA1_ML, etc.).
CA_SHIPPING_PACKAGE = "CA1"

# Shipping campaign-type labels (per src/campaign_types.py classifier
# output) that trigger the CA panel override. Includes base + chaser
# variants of Shipping and Christmas Shipping.
SHIPPING_CAMPAIGN_TYPES = {
    "Shipping",
    "Shipping Chaser",
    "Christmas Shipping",
    "Christmas Shipping Chaser",
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
# v3.3 (2026-04-28):
# - mid_level_prospect: removed (cohort eliminated, see SPEC §5.4 v3.3).
# - new_donor: removed from waterfall toggles. Promoted to Tier 1.5
#   pre-emption that runs above the waterfall (SPEC §6.2.1, v3.3).
#   The pre-emption is unconditional in standard appeals; the welcome
#   series itself runs as a separate workflow with all other GROUP
#   toggles OFF.
DEFAULT_TOGGLES = {
    "major_gift":       True,
    "mid_level":        True,
    "sustainer":        False,   # Default OFF — include for year-end/emergency
    "cornerstone":      True,
    "active_housefile": True,
    "lapsed":           True,
    "deep_lapsed":      True,
}


def fy_label_for_date(d: date) -> str:
    """Return the FY label (e.g. 'FY25') for a given date."""
    if d.month >= FY_START_MONTH:
        return f"FY{(d.year + 1) % 100:02d}"
    else:
        return f"FY{d.year % 100:02d}"


# --- Cohort → Required Campaign Prefix (v3.3, 2026-04-28) ---
# Each segment code maps to the campaign-prefix(es) it accepts. Used for
# multi-campaign runs to route donors to the correct campaign code.
#
#   "A"  — General housefile, Cornerstone, Sustainer, CBNC
#   "M"  — Mid-Level (ML01) and Major Gift (MJ01)
#   "N"  — In-house Major Donor cohort, only when major_donor_in_house
#          Tier 2 toggle is OFF (rare in-house-only mailing)
#
# Removed in v3.3:
#   - "J" prefix entries (J was a misinterpretation per Bill 2026-04-28
#     — there is no J campaign prefix at HRI).
#   - "MP01" routing (cohort eliminated; code retained as deprecated).
#   - "ND01" routing (New Donor moved to Tier 1.5 pre-emption — never
#     reaches the waterfall and never gets an appeal code under the
#     standard flow. The welcome series itself runs as a separate
#     workflow with its own appeal code.)
#
# The validator uses this list at run-time: for every segment present in
# the assigned universe, at least one selected campaign must have the
# required prefix. No auto-fallback.
COHORT_PREFIX_RULES = {
    # Housefile / Cornerstone / Sustainer / CBNC — A only
    "AH01": "A", "AH02": "A", "AH03": "A",
    "AH04": "A", "AH05": "A", "AH06": "A",
    "LR01": "A", "LR02": "A",
    "DL01": "A", "DL02": "A", "DL03": "A", "DL04": "A",
    "CB01": "A",
    "SU01": "A",
    "CS01": "A", "CS02": "A",
    # Mid-Level — M only
    "ML01": "M",
    # Major Gift — M only (J removed v3.3)
    "MJ01": "M",
    # MP01 / ND01: deprecated / pre-empted; intentionally absent from
    # the routing table. If an upstream change ever assigns to one of
    # these codes again, the resolver returns None and the validator
    # surfaces a clear error rather than silently routing.
}

# Toggle → required prefix for UI / pre-run validation. Keyed by toggle
# key so the validator can be evaluated before the waterfall runs (i.e.
# from operator toggle state, without yet knowing which segment codes
# will end up populated). Stays in sync with COHORT_PREFIX_RULES.
TOGGLE_PREFIX_RULES = {
    "cornerstone":      "A",
    "sustainer":        "A",
    "active_housefile": "A",
    "lapsed":           "A",
    "deep_lapsed":      "A",
    "mid_level":        "M",
    "major_gift":       "M",   # v3.3: M only. J removed.
}

# v3.3: when the Major_Donor_In_House Tier 2 toggle is OFF, the
# in-house cohort is mailed via an N-prefix campaign. The validator
# uses this map to require an N-prefix campaign in the selection
# whenever the operator turns the in-house suppression OFF.
INHOUSE_TOGGLE_KEY = "major_donor_in_house"
INHOUSE_PREFIX = "N"


def resolve_campaign_for_segment(segment_code, selected_campaigns):
    """Pick the campaign whose prefix matches a segment's cohort.

    Args:
        segment_code: HRI segment (AH01, ML01, MJ01, ...).
        selected_campaigns: list of dicts with at minimum
            `appeal_code` (9-char). The first character is the prefix
            (A, M, N, ...). Order does not matter.

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
    return by_prefix.get(required)


def validate_campaign_selection(toggles, selected_campaigns):
    """Validate that every ON cohort has a matching campaign prefix.

    Returns:
        list of error strings. Empty list means valid.

    Errors are operator-facing — the message names the toggle and the
    missing prefix so the operator knows whether to add a campaign or
    flip the toggle off.

    v3.3: N-prefix requirement added when major_donor_in_house Tier 2
    toggle is OFF. Operator running an in-house-only mailing must
    include an N-prefix campaign for the in-house cohort to route to.
    """
    errors = []
    if not selected_campaigns:
        errors.append("No campaign selected. Pick at least one campaign.")
        return errors
    selected_prefixes = {c.get("appeal_code", "")[:1] for c in selected_campaigns}
    label_for = {
        "cornerstone":      "Cornerstone",
        "sustainer":        "Sustainer",
        "active_housefile": "Active Housefile",
        "lapsed":           "Lapsed",
        "deep_lapsed":      "Deep Lapsed",
        "mid_level":        "Mid-Level",
        "major_gift":       "Major Gift",
    }
    for tk, required in TOGGLE_PREFIX_RULES.items():
        if not toggles.get(tk, DEFAULT_TOGGLES.get(tk, False)):
            continue
        ok = required in selected_prefixes
        if not ok:
            errors.append(
                f"{label_for[tk]} toggle is ON but no {required}-prefix campaign "
                f"is in your selection. Add a {required} campaign or turn "
                f"{label_for[tk]} OFF."
            )
    # v3.3: in-house override requires an N-prefix campaign in the run.
    # Default ON suppresses in-house donors; flipping OFF means the
    # operator is mailing them, which routes via N.
    inhouse_on = toggles.get(INHOUSE_TOGGLE_KEY, True)
    if not inhouse_on and INHOUSE_PREFIX not in selected_prefixes:
        errors.append(
            "Major Donor In-House suppression is OFF (in-house mailing) "
            f"but no {INHOUSE_PREFIX}-prefix campaign is in your selection. "
            f"Add an {INHOUSE_PREFIX} campaign or turn the suppression back ON."
        )
    return errors
