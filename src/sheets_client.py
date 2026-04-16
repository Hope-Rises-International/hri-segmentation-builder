"""Google Sheets layer: MIC read/write and diagnostic output."""

from __future__ import annotations
import logging
from datetime import datetime

import gspread
from google.auth import default
import pandas as pd

from config import MIC_SHEET_ID, MIC_CAMPAIGN_CALENDAR_TAB, MIC_DRAFT_TAB, DRAFT_COLUMNS, DRIVE_OUTPUT_FOLDER_ID

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

def _get_drive_service(gc: gspread.Client):
    """Get Drive API v3 service from gspread's auth credentials."""
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=gc.http_client.auth)


def _create_sheet_in_shared_drive(gc: gspread.Client, title: str) -> gspread.Spreadsheet:
    """Create a Google Sheet directly in the shared drive output folder.

    Uses Drive API v3 with supportsAllDrives=True to bypass SA My Drive quota.
    Then opens via gspread for tab/data operations.
    """
    drive = _get_drive_service(gc)
    metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [DRIVE_OUTPUT_FOLDER_ID],
    }
    file = drive.files().create(
        body=metadata,
        supportsAllDrives=True,
        fields="id",
    ).execute()

    sheet_id = file["id"]
    sh = gc.open_by_key(sheet_id)
    logger.info(f"  Created sheet in shared drive folder: {sh.url}")
    return sh


def upload_csv_to_drive(gc: gspread.Client, filename: str, csv_content: str) -> str:
    """Upload a CSV file directly to the shared drive output folder.

    Uses Drive API v3 with supportsAllDrives=True.
    Returns the file URL.
    """
    from googleapiclient.http import MediaInMemoryUpload

    drive = _get_drive_service(gc)
    metadata = {
        "name": filename,
        "mimeType": "text/csv",
        "parents": [DRIVE_OUTPUT_FOLDER_ID],
    }
    media = MediaInMemoryUpload(csv_content.encode("utf-8"), mimetype="text/csv")
    file = drive.files().create(
        body=metadata,
        media_body=media,
        supportsAllDrives=True,
        fields="id, webViewLink",
    ).execute()

    url = file.get("webViewLink", f"https://drive.google.com/file/d/{file['id']}")
    logger.info(f"  Uploaded {filename} to shared drive: {url}")
    return url


def write_diagnostic(gc: gspread.Client, tabs_data: dict[str, pd.DataFrame]) -> str:
    """Write diagnostic DataFrames to a Google Sheet in the shared drive output folder.

    Falls back to writing local CSVs if sheet creation fails.
    Returns the sheet URL or local directory path.
    """
    import os

    try:
        title = f"Segmentation Diagnostic — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        sh = _create_sheet_in_shared_drive(gc, title)

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
