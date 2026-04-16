"""Google Sheets layer: MIC read/write and diagnostic output."""

from __future__ import annotations
import logging
from datetime import datetime

import gspread
from google.auth import default
import pandas as pd

from config import MIC_SHEET_ID, MIC_CAMPAIGN_CALENDAR_TAB, MIC_DRAFT_TAB, DRAFT_COLUMNS

logger = logging.getLogger(__name__)


def get_sheets_client() -> gspread.Client:
    """Authenticate with Google Sheets using ADC."""
    creds, _ = default(scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    return gspread.authorize(creds)


def _ensure_worksheet(
    sh: gspread.Spreadsheet, title: str, rows: int = 1000, cols: int = 20
) -> gspread.Worksheet:
    """Get or create a worksheet by title, clearing if it exists."""
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))
    return ws


def _df_to_sheet_values(df: pd.DataFrame) -> list[list]:
    """Convert DataFrame to list-of-lists for gspread, handling types."""
    headers = df.columns.tolist()
    values = [headers]
    for _, row in df.iterrows():
        row_vals = []
        for v in row:
            if pd.isna(v) or v is None:
                row_vals.append("")
            elif isinstance(v, bool):
                row_vals.append(v)
            elif isinstance(v, (int, float)):
                row_vals.append(float(v))
            else:
                row_vals.append(str(v))
        values.append(row_vals)
    return values


# ---------------------------------------------------------------------------
# MIC Operations
# ---------------------------------------------------------------------------

def read_campaign_calendar(gc: gspread.Client) -> pd.DataFrame:
    """Read MIC Campaign Calendar tab, return as DataFrame."""
    sh = gc.open_by_key(MIC_SHEET_ID)
    # Find the tab — try exact match, then case-insensitive
    ws = None
    for worksheet in sh.worksheets():
        if worksheet.title == MIC_CAMPAIGN_CALENDAR_TAB:
            ws = worksheet
            break
    if ws is None:
        for worksheet in sh.worksheets():
            if worksheet.title.strip().lower() == MIC_CAMPAIGN_CALENDAR_TAB.lower():
                ws = worksheet
                logger.info(f"  Found tab with fuzzy match: '{worksheet.title}'")
                break
    if ws is None:
        available = [w.title for w in sh.worksheets()]
        raise ValueError(
            f"Tab '{MIC_CAMPAIGN_CALENDAR_TAB}' not found in MIC. "
            f"Available tabs: {available}"
        )

    data = ws.get_all_records()
    df = pd.DataFrame(data)
    logger.info(f"  MIC Campaign Calendar: {len(df)} rows, {len(df.columns)} columns")
    logger.info(f"  Columns: {df.columns.tolist()}")
    return df


def ensure_draft_tab(gc: gspread.Client) -> gspread.Worksheet:
    """Create or clear the Draft tab on the MIC with header row."""
    sh = gc.open_by_key(MIC_SHEET_ID)
    ws = _ensure_worksheet(sh, MIC_DRAFT_TAB, rows=200, cols=len(DRAFT_COLUMNS))
    ws.update(range_name="A1", values=[DRAFT_COLUMNS])
    logger.info(f"  Draft tab ready with {len(DRAFT_COLUMNS)} columns")
    return ws


def write_draft_tab(gc: gspread.Client, segment_summary: 'pd.DataFrame') -> None:
    """Write segment summary data to the Draft tab on MIC.

    Clears existing data and writes header + segment rows.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)
    ws = _ensure_worksheet(sh, MIC_DRAFT_TAB, rows=200, cols=len(DRAFT_COLUMNS))
    values = _df_to_sheet_values(segment_summary)
    ws.update(range_name="A1", values=values)
    logger.info(f"  Draft tab written: {len(segment_summary)} segment rows")


# ---------------------------------------------------------------------------
# Diagnostic Output
# ---------------------------------------------------------------------------

def write_diagnostic(gc: gspread.Client, tabs_data: dict[str, pd.DataFrame]) -> str:
    """Write diagnostic DataFrames to a new Google Sheet.

    Falls back to writing local CSVs if sheet creation fails (e.g., quota exceeded).
    Returns the sheet URL or local directory path.
    """
    import os

    # Try Google Sheets first
    try:
        title = f"Segmentation Diagnostic — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        sh = gc.create(title)
        logger.info(f"  Created diagnostic sheet: {sh.url}")

        for tab_name, df in tabs_data.items():
            ws = _ensure_worksheet(sh, tab_name, rows=max(len(df) + 10, 100), cols=max(len(df.columns) + 2, 20))
            values = _df_to_sheet_values(df)
            ws.update(range_name="A1", values=values)
            logger.info(f"  Wrote tab '{tab_name}': {len(df)} rows")

        # Remove default Sheet1 if other tabs exist
        try:
            default_ws = sh.worksheet("Sheet1")
            if len(sh.worksheets()) > 1:
                sh.del_worksheet(default_ws)
        except gspread.exceptions.WorksheetNotFound:
            pass

        return sh.url

    except Exception as e:
        logger.warning(f"  Sheet creation failed ({e}), writing local CSVs instead")
        out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "diagnostic_output")
        os.makedirs(out_dir, exist_ok=True)
        for tab_name, df in tabs_data.items():
            path = os.path.join(out_dir, f"{tab_name}.csv")
            df.to_csv(path, index=False)
            logger.info(f"  Wrote {path}: {len(df)} rows")
        return out_dir
