"""Campaign-type classification for the Historical Baseline grid.

Each historical campaign is assigned a type based on its name + metadata.
Order-sensitive: chaser variants (name contains "Chaser" / "F/U" / "FU",
or is_followup=TRUE) MUST be tested before the base type — otherwise a
chaser gets collapsed into the base baseline and distorts both profiles.

The live data uses three chaser conventions in the wild:
    "Shipping Chaser", "Shipping F/U", "Shipping FU"
plus the Campaign Calendar's is_followup boolean. Any one is enough.
"""

from __future__ import annotations
import re

# Base campaign-type patterns. Each entry:
#   (base_type_name, pattern)
# Pattern = substring matched case-insensitively against campaign_name.
# Chaser variants are derived automatically from this list.
#
# Order matters — the first pattern to match wins. "Christmas Shipping"
# must come BEFORE "Shipping" so a campaign named "Christmas Shipping"
# doesn't get collapsed into the regular Shipping baseline.
BASE_TYPES = [
    ("Christmas Shipping",   "christmas shipping"),
    ("Shipping",             "shipping"),
    ("Tax Receipt",          "tax receipt"),
    ("Year End",             "year end"),
    ("Easter",               "easter"),
    ("Renewal",              "renewal"),
    ("Faith Leaders",        "faith leaders"),
    ("Shoes",                "shoes"),
    ("Whole Person Healing", "whole person healing"),
    ("FYE",                  None),  # custom — matches "FYE" or "Fiscal Year End"
]

# Types derived from the `lane` column rather than campaign name.
# Lane check happens FIRST — before any name-based rule — so a campaign
# named "July Acquisition Shipping" with lane=Acquisition goes into
# Acquisition, not Shipping.
LANE_TYPES = {
    "Newsletter":  "Newsletter",
    "Acquisition": "Acquisition",
}

# Chaser indicators in the campaign name.
CHASER_NAME_PATTERNS = [r"\bchaser\b", r"\bf/u\b", r"\bfu\b"]
_CHASER_RE = re.compile("|".join(CHASER_NAME_PATTERNS), re.IGNORECASE)


def _is_chaser(name: str, is_followup) -> bool:
    if str(is_followup).strip().upper() in ("TRUE", "1", "YES"):
        return True
    return bool(_CHASER_RE.search(name or ""))


def _matches_fye(name: str) -> bool:
    n = (name or "").lower()
    return ("fye" in n) or ("fiscal year end" in n)


def classify_campaign(campaign_name: str, lane: str = "", is_followup=None) -> str:
    """Return the campaign type for a single campaign.

    Rules (order matters):
      1. If `lane` is Newsletter / Acquisition → that lane name.
         Lane wins over name-based rules: a campaign called "July
         Acquisition Shipping" with lane=Acquisition is Acquisition,
         not Shipping.
      2. If the name matches a known base type:
         - Chaser? → "<Base> Chaser"
         - Otherwise → "<Base>"
      3. Else → "Other".
    """
    name = campaign_name or ""
    name_lc = name.lower()
    chaser = _is_chaser(name, is_followup)

    lane_val = (lane or "").strip()
    if lane_val in LANE_TYPES:
        return LANE_TYPES[lane_val]

    for base_name, pattern in BASE_TYPES:
        if base_name == "FYE":
            matched = _matches_fye(name)
        else:
            matched = pattern in name_lc
        if matched:
            return f"{base_name} Chaser" if chaser else base_name

    return "Other"


# Campaign types whose rows should NOT feed the "Overall" meta-average.
# Acquisition is cold-mail — structurally different economics.
EXCLUDED_FROM_OVERALL = {"Acquisition"}

# All possible campaign-type labels (for enumeration / UI dropdown).
ALL_TYPES = (
    [b for b, _ in BASE_TYPES if b != "FYE"]
    + ["FYE"]
    + [f"{b} Chaser" for b, _ in BASE_TYPES if b != "FYE"]
    + ["FYE Chaser"]
    + list(LANE_TYPES.values())
    + ["Other", "Overall"]
)
