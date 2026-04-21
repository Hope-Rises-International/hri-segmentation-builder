"""Historical Baseline grid — multi-campaign averages per (HRI segment × campaign type).

Replaces the single-campaign rollup as the default economics source for the
scenario editor. See specs/SPEC-historical-baseline.md for the full spec.

Pipeline:
    Segment Actuals (MIC)      ──┐
                                 ├─> parse TLC → HRI segment
    Campaign Calendar (MIC) ─────┤   classify campaign type (chaser-aware)
                                 │   filter for quality
                                 ▼
                          aggregate weighted by contacts
                                 │
                                 ├─> proxy/estimate logic for CS01/MJ01/MP01/CB01
                                 ├─> Overall meta-average per segment
                                 ▼
                     sf_cache.historical_baseline (BQ)
                     + MIC "Historical Baseline" tab
"""

from __future__ import annotations
import logging
from datetime import datetime, date

import gspread
import pandas as pd
import numpy as np
from google.cloud import bigquery

from baseline_rollup import _parse_tlc_source_code
from campaign_types import classify_campaign, ALL_TYPES, EXCLUDED_FROM_OVERALL
from config import GCP_PROJECT, MIC_SHEET_ID, SEGMENT_CODES

logger = logging.getLogger(__name__)

BQ_DATASET = "sf_cache"
BQ_TABLE = f"{GCP_PROJECT}.{BQ_DATASET}.historical_baseline"
MIC_HISTORICAL_BASELINE_TAB = "Historical Baseline"

MIN_CAMPAIGN_CONTACTS = 500   # campaigns below this are too noisy to count
FY_CUTOFF = "FY22"            # exclude anything older (pre-COVID behavior shift)
MIN_CAMPAIGNS_FOR_HIGH = 3    # <3 contributing campaigns → "estimate" confidence


# -------- Proxy definitions for HRI-native segments with no TLC equivalent --

# Each proxy says: "for segment X, use the weighted aggregate of these source
# segments' contacts/gifts/revenue — but flag the output as 'proxy' / 'estimate'."
#
# Source segments are the TLC-mappable HRI segments that exist in the data.
# The scale_factor adjusts for known divergence (e.g., CBNC responds ~1.5×
# better than its proxy LR01).
PROXY_SEGMENTS = {
    "CS01": {
        "sources":     ["AH01", "AH04"],   # high-retention, high-value actives
        "scale":       1.0,
        "confidence":  "proxy",
    },
    "MJ01": {
        "sources":     ["AH01", "AH04"],   # 0-12mo, $50+ as stand-in for $100+
        "scale":       1.0,
        "confidence":  "proxy",
    },
    "MP01": {
        "sources":     ["AH01", "AH04"],   # 0-12mo, $50+
        "scale":       1.0,
        "confidence":  "proxy",
    },
    "CB01": {
        "sources":     ["LR01"],
        "scale":       1.5,                # CBNC respond ~1.5× the lapsed baseline
        "confidence":  "estimate",
    },
}


# --------------------------------------------------------------------------
# Data loaders
# --------------------------------------------------------------------------

def _money(value) -> float:
    """Parse a '$1,234.56' or '$-385.23' style string into a float."""
    if value is None or value == "":
        return 0.0
    s = str(value).replace("$", "").replace(",", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fy_int(fy: str) -> int:
    """Turn 'FY2024' or 'FY24' into 2024. Returns 0 if unparseable."""
    if not fy:
        return 0
    s = str(fy).upper().replace("FY", "").strip()
    try:
        n = int(s)
    except ValueError:
        return 0
    return 2000 + n if n < 100 else n


def load_segment_actuals(gc: gspread.Client) -> pd.DataFrame:
    """Read the MIC 'Segment Actuals' tab — one row per (campaign, source_code)."""
    sh = gc.open_by_key(MIC_SHEET_ID)
    ws = sh.worksheet("Segment Actuals")
    data = ws.get_all_values()
    if len(data) <= 1:
        return pd.DataFrame()
    headers = data[0]
    df = pd.DataFrame(data[1:], columns=headers)
    df["contacts"] = pd.to_numeric(df["contacts"], errors="coerce").fillna(0)
    df["gifts"]    = pd.to_numeric(df["gifts"],    errors="coerce").fillna(0)
    df["revenue"]  = df["revenue"].map(_money)
    df["cost"]     = df["cost"].map(_money)
    df["fy_year"]  = df["fy"].map(_fy_int)
    logger.info(f"  Segment Actuals: {len(df):,} rows")
    return df


def load_campaign_metadata(gc: gspread.Client) -> pd.DataFrame:
    """Read the MIC Campaign Calendar tab and return one row per appeal_code
    with campaign_name, lane, is_followup — the inputs to type classification.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)
    ws = sh.worksheet("mic_flattened.csv")
    df = pd.DataFrame(ws.get_all_records())
    df = df[df["appeal_code"].astype(str).str.strip() != ""].copy()
    df["appeal_code"] = df["appeal_code"].astype(str).str.strip()
    # One campaign per appeal_code; pick the first occurrence.
    df = df.drop_duplicates(subset=["appeal_code"], keep="first")
    df["campaign_type"] = df.apply(
        lambda r: classify_campaign(
            str(r.get("campaign_name", "")),
            str(r.get("lane", "")),
            r.get("is_followup", ""),
        ),
        axis=1,
    )
    logger.info(f"  Campaign metadata: {len(df):,} appeal codes classified")
    return df[["appeal_code", "campaign_name", "lane", "is_followup", "campaign_type"]]


# --------------------------------------------------------------------------
# Filtering + aggregation
# --------------------------------------------------------------------------

def _qualifying_campaigns(actuals: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    """Attach campaign metadata and drop campaigns that fail quality gates."""
    merged = actuals.merge(metadata, on="appeal_code", how="left")
    # Campaigns with no calendar entry → "Other" (rather than dropping).
    merged["campaign_type"] = merged["campaign_type"].fillna("Other")
    merged["campaign_name"] = merged["campaign_name"].fillna("")

    # Exclusion: FY pre-cutoff
    fy_cutoff_int = _fy_int(FY_CUTOFF)
    before = len(merged)
    merged = merged[merged["fy_year"] >= fy_cutoff_int]
    logger.info(f"  FY filter ({FY_CUTOFF}+): dropped {before - len(merged):,} rows")

    # Exclusion: campaigns with < MIN_CAMPAIGN_CONTACTS total contacts.
    per_campaign = merged.groupby("appeal_code")["contacts"].sum()
    kept = per_campaign[per_campaign >= MIN_CAMPAIGN_CONTACTS].index
    before = len(merged)
    merged = merged[merged["appeal_code"].isin(kept)]
    logger.info(f"  Min-contacts filter (≥{MIN_CAMPAIGN_CONTACTS}): "
                f"dropped {before - len(merged):,} rows, "
                f"{merged['appeal_code'].nunique():,} campaigns remain")

    # Parse source codes → HRI segments. Pass the row's appeal_code so the
    # TLC parser can strip the 5-char prefix correctly.
    merged["hri_segment"] = merged.apply(
        lambda r: _parse_tlc_source_code(str(r["source_code"]), r["appeal_code"]),
        axis=1,
    )
    mapped = merged[merged["hri_segment"].notna()].copy()
    logger.info(f"  TLC→HRI mapped: {len(mapped):,}/{len(merged):,} rows "
                f"({mapped['hri_segment'].nunique()} distinct segments)")
    return mapped


def _aggregate(rows: pd.DataFrame, groupby_type: bool) -> pd.DataFrame:
    """Weighted aggregation of contacts/gifts/revenue.

    Weighted by contact volume → high-volume campaigns dominate. Avg gift
    weighted by gifts (so revenue-per-gift is correct)."""
    keys = ["hri_segment", "campaign_type"] if groupby_type else ["hri_segment"]
    agg = rows.groupby(keys).agg(
        contacts=("contacts", "sum"),
        gifts=("gifts", "sum"),
        revenue=("revenue", "sum"),
        campaign_count=("appeal_code", "nunique"),
    ).reset_index()
    if not groupby_type:
        agg["campaign_type"] = "Overall"
    agg["response_rate"] = np.where(agg["contacts"] > 0, agg["gifts"] / agg["contacts"], 0)
    agg["avg_gift"] = np.where(agg["gifts"] > 0, agg["revenue"] / agg["gifts"], 0)
    agg["revenue_per_contact"] = agg["response_rate"] * agg["avg_gift"]
    return agg


def _apply_proxies(grid: pd.DataFrame) -> pd.DataFrame:
    """For each campaign_type present in the grid, fill in proxy rows for
    CS01/MJ01/MP01/CB01 by aggregating their source segments inside that type."""
    rows_out = [grid]
    for campaign_type in grid["campaign_type"].unique():
        type_slice = grid[grid["campaign_type"] == campaign_type].set_index("hri_segment")
        for target_seg, cfg in PROXY_SEGMENTS.items():
            if target_seg in type_slice.index:
                continue   # direct data wins over proxy
            available = [s for s in cfg["sources"] if s in type_slice.index]
            if not available:
                continue
            sub = type_slice.loc[available]
            total_contacts = float(sub["contacts"].sum())
            total_gifts    = float(sub["gifts"].sum()) * cfg["scale"]
            total_revenue  = float(sub["revenue"].sum()) * cfg["scale"]
            if total_contacts <= 0:
                continue
            rr = total_gifts / total_contacts
            avg = (total_revenue / total_gifts) if total_gifts > 0 else 0
            rows_out.append(pd.DataFrame([{
                "hri_segment":      target_seg,
                "campaign_type":    campaign_type,
                "contacts":         total_contacts,
                "gifts":            total_gifts,
                "revenue":          total_revenue,
                "campaign_count":   int(sub["campaign_count"].max()),
                "response_rate":    rr,
                "avg_gift":         avg,
                "revenue_per_contact": rr * avg,
                "_proxy":           cfg["confidence"],
            }]))
    return pd.concat(rows_out, ignore_index=True, sort=False)


def _confidence(row) -> str:
    """Assign a confidence tier to a grid row."""
    proxy = row.get("_proxy")
    if isinstance(proxy, str) and proxy:
        return proxy
    if row["campaign_count"] < MIN_CAMPAIGNS_FOR_HIGH:
        return "estimate"
    return "high"


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

def build_historical_baseline(gc: gspread.Client) -> pd.DataFrame:
    """Build the full Historical Baseline grid. Returns a tidy DataFrame.

    Caller is responsible for writing the result to BQ + MIC.
    """
    logger.info("=" * 60)
    logger.info("HISTORICAL BASELINE — rebuild")
    logger.info("=" * 60)

    actuals  = load_segment_actuals(gc)
    metadata = load_campaign_metadata(gc)
    if actuals.empty or metadata.empty:
        raise RuntimeError("Segment Actuals or Campaign Calendar is empty")

    qualifying = _qualifying_campaigns(actuals, metadata)

    # Per-type aggregates.
    per_type = _aggregate(qualifying, groupby_type=True)

    # Overall meta-average. Exclude Acquisition (cold mail — structurally different).
    eligible_for_overall = qualifying[~qualifying["campaign_type"].isin(EXCLUDED_FROM_OVERALL)]
    overall = _aggregate(eligible_for_overall, groupby_type=False)

    grid = pd.concat([per_type, overall], ignore_index=True, sort=False)
    logger.info(f"  Direct grid rows: {len(grid):,}")

    # Inject proxy rows for HRI-native segments.
    grid = _apply_proxies(grid)

    # Final dressing.
    grid["segment_name"] = grid["hri_segment"].map(SEGMENT_CODES).fillna(grid["hri_segment"])
    grid["confidence"] = grid.apply(_confidence, axis=1)
    # Attribution methodology — HRI tracks direct response to direct appeals
    # only; cross-channel revenue (online, DAF, IRA) isn't in Scorecard totals.
    # This column is a hook for a future multi-attribution methodology.
    grid["revenue_basis"] = "direct_attribution"
    grid["last_refreshed"] = datetime.utcnow().isoformat()
    if "_proxy" in grid.columns:
        grid = grid.drop(columns=["_proxy"])

    # Round to reasonable precision.
    grid["response_rate"] = grid["response_rate"].round(6)
    grid["avg_gift"]      = grid["avg_gift"].round(2)
    grid["revenue_per_contact"] = grid["revenue_per_contact"].round(4)
    grid["contacts"]      = grid["contacts"].astype(int)
    grid["gifts"]         = grid["gifts"].round().astype(int)
    grid["revenue"]       = grid["revenue"].round(2)
    grid["campaign_count"] = grid["campaign_count"].astype(int)

    cols = [
        "campaign_type", "hri_segment", "segment_name",
        "response_rate", "avg_gift", "revenue_per_contact",
        "campaign_count", "contacts", "gifts", "revenue",
        "confidence", "revenue_basis", "last_refreshed",
    ]
    grid = grid[cols].rename(columns={
        "hri_segment": "hri_segment_code",
        "contacts":    "total_contacts",
        "gifts":       "total_gifts",
        "revenue":     "total_revenue",
    })

    # Sort for human readability: type groups, then AH/LR/DL/... in segment order.
    type_order = {t: i for i, t in enumerate(ALL_TYPES)}
    seg_order  = {s: i for i, s in enumerate(SEGMENT_CODES.keys())}
    grid["_t"] = grid["campaign_type"].map(type_order).fillna(999).astype(int)
    grid["_s"] = grid["hri_segment_code"].map(seg_order).fillna(999).astype(int)
    grid = grid.sort_values(["_t", "_s"]).drop(columns=["_t", "_s"]).reset_index(drop=True)

    logger.info(f"  Final grid: {len(grid):,} rows "
                f"({grid['campaign_type'].nunique()} types × "
                f"{grid['hri_segment_code'].nunique()} segments)")
    return grid


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------

def write_to_bq(grid: pd.DataFrame) -> None:
    """Write the baseline grid to sf_cache.historical_baseline (truncate + replace)."""
    client = bigquery.Client(project=GCP_PROJECT)
    schema = [
        bigquery.SchemaField("campaign_type",        "STRING"),
        bigquery.SchemaField("hri_segment_code",     "STRING"),
        bigquery.SchemaField("segment_name",         "STRING"),
        bigquery.SchemaField("response_rate",        "FLOAT"),
        bigquery.SchemaField("avg_gift",             "FLOAT"),
        bigquery.SchemaField("revenue_per_contact",  "FLOAT"),
        bigquery.SchemaField("campaign_count",       "INTEGER"),
        bigquery.SchemaField("total_contacts",       "INTEGER"),
        bigquery.SchemaField("total_gifts",          "INTEGER"),
        bigquery.SchemaField("total_revenue",        "FLOAT"),
        bigquery.SchemaField("confidence",           "STRING"),
        bigquery.SchemaField("revenue_basis",        "STRING"),
        bigquery.SchemaField("last_refreshed",       "STRING"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    # Use JSON rather than load_table_from_dataframe to avoid the pyarrow
    # build path — some environments ship a pyarrow that conflicts with the
    # installed numpy.
    records = grid.to_dict(orient="records")
    for r in records:
        for k, v in list(r.items()):
            if isinstance(v, float) and pd.isna(v):
                r[k] = None
            elif hasattr(v, "item"):   # numpy scalar → Python scalar
                r[k] = v.item()
    job = client.load_table_from_json(records, BQ_TABLE, job_config=job_config)
    job.result()
    logger.info(f"  BQ: wrote {len(grid):,} rows to {BQ_TABLE}")


METHODOLOGY_NOTE = (
    "Direct-attribution response only. Cross-channel revenue "
    "(online, DAF, IRA) not included but correlates proportionally."
)


def write_to_mic(gc: gspread.Client, grid: pd.DataFrame) -> None:
    """Write the grid to MIC 'Historical Baseline' tab in a wide layout
    (campaign types as columns, segments as rows) showing response rate.

    Row 1: methodology note (merged across all columns).
    Row 2: column headers.
    Row 3+: data.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)
    try:
        ws = sh.worksheet(MIC_HISTORICAL_BASELINE_TAB)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=MIC_HISTORICAL_BASELINE_TAB, rows="500", cols="30")

    # Tidy long-form write — easier to verify and BQ is source of truth anyway.
    headers = list(grid.columns)
    note_row = [METHODOLOGY_NOTE] + [""] * (len(headers) - 1)
    rows = (
        [note_row]
        + [headers]
        + grid.astype(object).where(pd.notnull(grid), "").values.tolist()
    )
    # Resize if needed
    needed_cols = max(len(headers), ws.col_count)
    needed_rows = max(len(rows) + 10, ws.row_count)
    if ws.col_count < needed_cols or ws.row_count < needed_rows:
        ws.resize(rows=needed_rows, cols=needed_cols)
    ws.update(range_name="A1", values=rows)
    logger.info(f"  MIC: wrote {len(grid):,} rows to '{MIC_HISTORICAL_BASELINE_TAB}'")


def rebuild_and_publish(gc: gspread.Client) -> dict:
    """End-to-end rebuild: compute grid, write BQ, write MIC. Returns a summary."""
    grid = build_historical_baseline(gc)
    write_to_bq(grid)
    write_to_mic(gc, grid)
    return {
        "rows": int(len(grid)),
        "campaign_types": int(grid["campaign_type"].nunique()),
        "segments":       int(grid["hri_segment_code"].nunique()),
        "confidence_breakdown": grid["confidence"].value_counts().to_dict(),
        "last_refreshed": grid["last_refreshed"].iloc[0] if len(grid) else "",
    }


# --------------------------------------------------------------------------
# Reader — used by build_universe when baseline_type is set
# --------------------------------------------------------------------------

def fetch_baseline_for_type(campaign_type: str) -> dict:
    """Read the rows for a given campaign_type from BQ, with Overall fallback.

    Returns: dict keyed by hri_segment_code → {response_rate, avg_gift, confidence}.
    For segments missing in this type, falls back to the Overall row and
    marks confidence='fallback'.
    """
    client = bigquery.Client(project=GCP_PROJECT)
    query = f"""
        WITH t AS (
            SELECT hri_segment_code, response_rate, avg_gift, confidence
            FROM `{BQ_TABLE}`
            WHERE campaign_type = @ct
        ),
        o AS (
            SELECT hri_segment_code, response_rate, avg_gift
            FROM `{BQ_TABLE}`
            WHERE campaign_type = 'Overall'
        )
        SELECT
            o.hri_segment_code,
            COALESCE(t.response_rate, o.response_rate) AS response_rate,
            COALESCE(t.avg_gift,      o.avg_gift)      AS avg_gift,
            CASE WHEN t.hri_segment_code IS NULL THEN 'fallback'
                 ELSE t.confidence END                 AS confidence
        FROM o LEFT JOIN t USING (hri_segment_code)
    """
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("ct", "STRING", campaign_type)]
        ),
    )
    out = {}
    for row in job.result():
        out[row["hri_segment_code"]] = {
            "response_rate": float(row["response_rate"] or 0),
            "avg_gift":      float(row["avg_gift"] or 0),
            "confidence":    row["confidence"] or "fallback",
        }
    return out
