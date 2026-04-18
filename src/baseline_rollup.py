"""TLC-to-HRI segment rollup for baseline economics.

Maps TLC source codes to HRI segment codes, aggregates actuals,
and writes/reads a Baseline Rollup tab on the MIC.

TLC source code structure (after 5-char appeal code prefix):
  Position 1-2: Gift history + Recency
    BH = 2+ DM gifts, 0-6mo MRC     BA = 1 DM gift, 0-6mo MRC
    BI = 2+ DM gifts, 7-12mo MRC    BB = 1 DM gift, 7-12mo MRC
    BJ = 2+ DM gifts, 13-24mo MRC   BC = 1 DM gift, 13-24mo MRC
    BK = 2+ DM gifts, 25-36mo MRC   BD = 1 DM gift, 25-36mo MRC
    AH = 2+ gifts (incl non-DM), 0-6mo
    AI = 2+ gifts, 7-12mo
    AJ = 2+ gifts, 13-24mo
    AK = 2+ gifts, 25-36mo
  Position 3: Monetary tier (HPC)
    2 = $10-14.99, 3 = $15-24.99, 4 = $25-49.99
    5 = $50-99.99, 6 = $100-249.99, 7 = $250-499.99
    8 = $500-999.99, 9 = $1000+, M = mid-level
  Position 4: Panel/test (0 = control)

HRI segments map on recency + monetary:
  0-6mo + $50+  → AH01    7-12mo + $50+  → AH04
  0-6mo + $25-50 → AH02   7-12mo + $25-50 → AH05
  0-6mo + <$25   → AH03   7-12mo + <$25   → AH06
  13-24mo → LR01/LR02     25-36mo $100+ → DL01, <$100 → DL02
  M-prefix or tier 9/M → ML01
"""

from __future__ import annotations
import logging
import re

import gspread
import pandas as pd
import numpy as np

from config import MIC_SHEET_ID

logger = logging.getLogger(__name__)

# Recency mapping: TLC recency code → HRI recency band
RECENCY_MAP = {
    "H": "0-6",   # 0-6 months
    "A": "0-6",   # BA = 1 gift 0-6mo (char after B)
    "I": "7-12",  # 7-12 months
    "B": "7-12",  # BB = 1 gift 7-12mo
    "J": "13-24", # 13-24 months
    "C": "13-24", # BC = 1 gift 13-24mo
    "K": "25-36", # 25-36 months
    "D": "25-36", # BD = 1 gift 25-36mo
}

# Monetary tier mapping: TLC tier digit → HRI monetary band
MONETARY_MAP = {
    "2": "under25",    # $10-14.99
    "3": "under25",    # $15-24.99
    "4": "25-50",      # $25-49.99
    "5": "50+",        # $50-99.99
    "6": "50+",        # $100-249.99
    "7": "50+",        # $250-499.99
    "8": "mid_level",  # $500-999.99
    "9": "mid_level",  # $1000+
    "M": "mid_level",  # Mid-level
    "0": "under25",    # Unknown → default
}

# HRI segment lookup: (recency_band, monetary_band) → segment code
SEGMENT_LOOKUP = {
    ("0-6", "50+"): "AH01",
    ("0-6", "25-50"): "AH02",
    ("0-6", "under25"): "AH03",
    ("7-12", "50+"): "AH04",
    ("7-12", "25-50"): "AH05",
    ("7-12", "under25"): "AH06",
    ("13-24", "50+"): "LR01",
    ("13-24", "25-50"): "LR01",
    ("13-24", "under25"): "LR01",
    ("25-36", "50+"): "DL01",
    ("25-36", "25-50"): "DL02",
    ("25-36", "under25"): "DL02",
    ("0-6", "mid_level"): "ML01",
    ("7-12", "mid_level"): "ML01",
    ("13-24", "mid_level"): "ML01",
    ("25-36", "mid_level"): "ML01",
}


def _parse_tlc_source_code(source_code, appeal_code):
    """Parse a TLC source code into HRI segment code.

    Returns the HRI segment code or None if unparseable.
    """
    # Strip the appeal code prefix
    if source_code.startswith(appeal_code):
        suffix = source_code[len(appeal_code):]
    else:
        suffix = source_code

    if not suffix or len(suffix) < 2:
        return None

    # M-prefix codes are mid-level
    if suffix.startswith("M") or suffix.startswith("1M") or suffix.startswith("2M"):
        return "ML01"

    # Standard pattern: 2-char history+recency, 1-char monetary, 1-char panel
    # First char: A or B (gift count category)
    # Second char: H/I/J/K (recency) or A/B/C/D (1-gift variant)
    if len(suffix) >= 3:
        char1 = suffix[0]  # A or B
        char2 = suffix[1]  # Recency indicator
        char3 = suffix[2]  # Monetary tier

        # Determine recency
        if char1 == "B" and char2 in RECENCY_MAP:
            recency = RECENCY_MAP[char2]
        elif char1 == "A" and char2 in RECENCY_MAP:
            recency = RECENCY_MAP[char2]
        else:
            return None

        # Determine monetary
        monetary = MONETARY_MAP.get(char3, "under25")

        # Look up HRI segment
        return SEGMENT_LOOKUP.get((recency, monetary))

    return None


def build_baseline_rollup(gc, baseline_appeal_code):
    """Build a rollup of TLC segment actuals into HRI segment buckets.

    Reads from Segment Actuals tab, maps TLC source codes to HRI segments,
    aggregates contacts/gifts/revenue/cost, and returns a DataFrame.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)
    ws = sh.worksheet("Segment Actuals")
    data = ws.get_all_values()

    if len(data) <= 1:
        logger.warning("  Segment Actuals tab is empty")
        return pd.DataFrame()

    headers = data[0]
    col = {h: i for i, h in enumerate(headers)}

    # Filter to the selected baseline campaign
    rows = []
    unmapped = []
    for row in data[1:]:
        ac = str(row[col["appeal_code"]]).strip()
        if ac != baseline_appeal_code:
            continue

        sc = str(row[col["source_code"]]).strip()
        hri_seg = _parse_tlc_source_code(sc, baseline_appeal_code)

        contacts = float(row[col["contacts"]] or 0)
        gifts = float(row[col["gifts"]] or 0)
        revenue = float(str(row[col["revenue"]]).replace("$", "").replace(",", "") or 0)
        cost = float(str(row[col["cost"]]).replace("$", "").replace(",", "") or 0)

        if hri_seg:
            rows.append({
                "hri_segment": hri_seg,
                "contacts": contacts,
                "gifts": gifts,
                "revenue": revenue,
                "cost": cost,
            })
        else:
            if contacts > 0:
                unmapped.append(sc)

    if unmapped:
        logger.info(f"  {len(unmapped)} unmapped source codes (skipped): {unmapped[:5]}...")

    if not rows:
        logger.warning(f"  No mapped rows for baseline {baseline_appeal_code}")
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Aggregate by HRI segment
    agg = df.groupby("hri_segment").agg({
        "contacts": "sum",
        "gifts": "sum",
        "revenue": "sum",
        "cost": "sum",
    }).reset_index()

    # Compute derived metrics
    agg["response_rate"] = np.where(agg["contacts"] > 0, agg["gifts"] / agg["contacts"], 0)
    agg["avg_gift"] = np.where(agg["gifts"] > 0, agg["revenue"] / agg["gifts"], 0)
    agg["net_revenue"] = agg["revenue"] - agg["cost"]
    agg["roi"] = np.where(agg["cost"] > 0, agg["revenue"] / agg["cost"], 0)

    logger.info(f"  Baseline rollup for {baseline_appeal_code}: {len(agg)} HRI segments")
    for _, r in agg.iterrows():
        logger.info(f"    {r['hri_segment']:6s} contacts={r['contacts']:,.0f} "
                    f"rr={r['response_rate']:.2%} avg=${r['avg_gift']:.0f}")

    return agg


def apply_baseline_to_summary(segment_summary, baseline_df, cpp):
    """Apply baseline economics to the segment summary (Draft tab).

    Populates Hist. Response Rate, Hist. Avg Gift, and derived columns.
    """
    if baseline_df.empty:
        return segment_summary

    result = segment_summary.copy()
    baseline_map = baseline_df.set_index("hri_segment")

    for idx, row in result.iterrows():
        seg_code = row["Segment Code"]
        if seg_code not in baseline_map.index:
            continue

        bl = baseline_map.loc[seg_code]
        rr = bl["response_rate"]
        avg = bl["avg_gift"]

        result.at[idx, "Hist. Response Rate"] = f"{rr:.2%}"
        result.at[idx, "Hist. Avg Gift"] = round(avg, 2)

        # Compute projected economics
        fit_col = "Budget Fit" if "Budget Fit" in result.columns else "Quantity"
        qty = float(row.get(fit_col, row.get("Quantity", 0)) or 0)
        if qty > 0 and rr > 0 and avg > 0:
            proj_revenue = rr * qty * avg
            total_cost = qty * cpp
            net_revenue = proj_revenue - total_cost
            be_rate = cpp / avg if avg > 0 else 0
            margin = net_revenue / proj_revenue if proj_revenue > 0 else 0

            result.at[idx, "Proj. Gross Revenue"] = round(proj_revenue, 2)
            result.at[idx, "Proj. Net Revenue"] = round(net_revenue, 2)
            result.at[idx, "Break-Even Rate"] = f"{be_rate:.2%}"
            result.at[idx, "Margin"] = f"{margin:.1%}"

    matched = sum(1 for _, r in result.iterrows() if r.get("Hist. Response Rate", "") != "")
    logger.info(f"  Baseline applied: {matched}/{len(result)} segments matched")

    return result
