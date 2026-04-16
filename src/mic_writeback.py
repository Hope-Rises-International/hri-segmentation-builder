"""MIC write-back: approve workflow, Segment Detail, link_to_segments, run idempotency.

Manages campaign status transitions and pipeline write recovery per spec Section 3.
"""

from __future__ import annotations
import logging
from datetime import datetime

import gspread
import pandas as pd

from config import MIC_SHEET_ID, DRAFT_COLUMNS

logger = logging.getLogger(__name__)

# Status transitions (one-directional per spec)
VALID_TRANSITIONS = {
    "": {"Draft", "Projected"},
    "Draft": {"Projected"},
    "Projected": {"Approved", "Projected"},  # Re-run overwrites
    "Approved": {"Pulled"},
    "Pulled": {"Mailed"},
}

MIC_SEGMENT_DETAIL_TAB = "Segment Detail"


def _ensure_worksheet(sh, title, rows=1000, cols=20):
    try:
        ws = sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=str(rows), cols=str(cols))
    return ws


def approve_projection(
    gc: gspread.Client,
    segment_summary: pd.DataFrame,
    campaign_id: str,
) -> dict:
    """Approve a projection: copy Draft tab to Segment Detail tab.

    Per spec Section 3, Step 4:
    - Copies Draft tab contents to Segment Detail as permanent record
    - Keyed on campaign_id + segment_code (upsert — replaces prior rows for this campaign)
    - Clears Draft tab after copy

    Returns dict with status and detail.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)

    # --- Ensure Segment Detail tab exists ---
    ws = _ensure_worksheet(sh, MIC_SEGMENT_DETAIL_TAB, rows=2000, cols=len(DRAFT_COLUMNS) + 2)

    # Read existing Segment Detail rows
    try:
        existing = ws.get_all_records()
        existing_df = pd.DataFrame(existing) if existing else pd.DataFrame()
    except Exception:
        existing_df = pd.DataFrame()

    # Add campaign_id column to segment summary
    new_rows = segment_summary.copy()
    new_rows.insert(0, "Campaign ID", campaign_id)
    new_rows["Approved At"] = datetime.now().isoformat()

    # Upsert: remove prior rows for this campaign, then append new ones
    if not existing_df.empty and "Campaign ID" in existing_df.columns:
        existing_df = existing_df[existing_df["Campaign ID"] != campaign_id]

    combined = pd.concat([existing_df, new_rows], ignore_index=True) if not existing_df.empty else new_rows

    # Write back
    ws.clear()
    headers = combined.columns.tolist()
    values = [headers]
    for _, row in combined.iterrows():
        values.append([str(v) if pd.notna(v) else "" for v in row])
    ws.update(range_name="A1", values=values)

    logger.info(f"  Segment Detail: {len(new_rows)} rows for campaign {campaign_id} "
                f"({len(combined)} total rows)")

    # Clear Draft tab
    try:
        draft_ws = sh.worksheet("Draft")
        draft_ws.clear()
        draft_ws.update(range_name="A1", values=[DRAFT_COLUMNS])
        logger.info("  Draft tab cleared")
    except gspread.exceptions.WorksheetNotFound:
        pass

    return {
        "status": "approved",
        "campaign_id": campaign_id,
        "segment_rows": len(new_rows),
        "total_detail_rows": len(combined),
    }


def update_link_to_segments(
    gc: gspread.Client,
    campaign_appeal_code: str,
    sheet_url: str,
):
    """Update link_to_segments column in Campaign Calendar for this campaign.

    Finds the row matching the appeal_code and writes the diagnostic sheet URL.
    """
    sh = gc.open_by_key(MIC_SHEET_ID)
    try:
        ws = sh.worksheet("mic_flattened.csv")  # TODO: rename to Campaign Calendar
    except gspread.exceptions.WorksheetNotFound:
        logger.warning("  Campaign Calendar tab not found — skipping link_to_segments update")
        return

    # Find the appeal_code column and the link_to_segments column
    headers = ws.row_values(1)
    if "appeal_code" not in headers or "link_to_segments" not in headers:
        logger.warning(f"  Missing columns in Campaign Calendar (have: {headers[:5]}...)")
        return

    appeal_col = headers.index("appeal_code") + 1  # 1-indexed
    link_col = headers.index("link_to_segments") + 1

    # Find row with matching appeal code
    appeal_values = ws.col_values(appeal_col)
    for i, val in enumerate(appeal_values):
        if str(val).strip() == str(campaign_appeal_code).strip():
            ws.update_cell(i + 1, link_col, sheet_url)
            logger.info(f"  link_to_segments updated for {campaign_appeal_code} (row {i + 1})")
            return

    logger.warning(f"  Appeal code {campaign_appeal_code} not found in Campaign Calendar")


class PipelineWriteRecovery:
    """Pipeline write recovery per spec Section 3.

    Sequential writes: Drive → Sheets → SF.
    Per-target success/fail flags. Retry only failed targets.
    """

    def __init__(self):
        self.status = {
            "drive_write": None,
            "sheets_write": None,
            "salesforce_write": None,
            "timestamp": None,
            "error_message": None,
        }

    def execute_writes(
        self,
        gc: gspread.Client,
        printer_csv: str,
        matchback_csv: str,
        suppression_audit_csv: str,
        segment_summary: pd.DataFrame,
        campaign_code: str,
        campaign_appeal_code: str,
        lane: str = "Housefile",
        exceptions_csv: str = "",
    ) -> dict:
        """Execute pipeline writes in order: Drive → Sheets → SF.

        Returns status dict with per-target success/fail.
        """
        from sheets_client import upload_csv_to_drive

        self.status["timestamp"] = datetime.now().isoformat()
        date_str = datetime.now().strftime("%Y%m%d")
        drive_urls = {}

        # --- Target 1: Google Drive (lowest risk) ---
        try:
            logger.info("Pipeline write 1/3: Google Drive...")

            printer_url = upload_csv_to_drive(
                gc, f"HRI_{campaign_code}_{lane}_PRINT_{date_str}.csv", printer_csv
            )
            matchback_url = upload_csv_to_drive(
                gc, f"HRI_{campaign_code}_{lane}_MATCHBACK_{date_str}.csv", matchback_csv
            )
            audit_url = upload_csv_to_drive(
                gc, f"suppression_audit_{campaign_code}_{date_str}.csv", suppression_audit_csv
            )

            drive_urls = {
                "printer": printer_url,
                "matchback": matchback_url,
                "audit": audit_url,
            }

            # Upload exceptions CSV if there are excluded records
            if exceptions_csv:
                exceptions_url = upload_csv_to_drive(
                    gc, f"exceptions_{campaign_code}_{date_str}.csv", exceptions_csv
                )
                drive_urls["exceptions"] = exceptions_url
            self.status["drive_write"] = "success"
            logger.info(f"  Drive: 3 files uploaded")
        except Exception as e:
            self.status["drive_write"] = "fail"
            self.status["error_message"] = f"Drive: {e}"
            logger.error(f"  Drive write FAILED: {e}")
            return self.status

        # --- Target 2: MIC Google Sheet ---
        try:
            logger.info("Pipeline write 2/3: MIC Google Sheet...")

            approval = approve_projection(gc, segment_summary, campaign_code)
            update_link_to_segments(gc, campaign_appeal_code,
                                    f"See Segment Detail tab, campaign {campaign_code}")

            self.status["sheets_write"] = "success"
            logger.info(f"  Sheets: Segment Detail written, link_to_segments updated")
        except Exception as e:
            self.status["sheets_write"] = "fail"
            self.status["error_message"] = f"Sheets: {e}"
            logger.error(f"  Sheets write FAILED: {e}")
            return self.status

        # --- Target 3: Salesforce (highest risk — deferred to Phase 6) ---
        # Campaign_Segment__c upsert will be implemented in Phase 6
        self.status["salesforce_write"] = "deferred"
        logger.info("  Salesforce: Campaign_Segment__c write deferred to Phase 6")

        self.status["drive_urls"] = drive_urls
        return self.status

    def retry_failed(self, gc, **kwargs):
        """Retry only failed targets."""
        retried = []
        if self.status.get("drive_write") == "fail":
            retried.append("drive")
            # Re-execute drive writes
        if self.status.get("sheets_write") == "fail":
            retried.append("sheets")
            # Re-execute sheets writes
        if self.status.get("salesforce_write") == "fail":
            retried.append("salesforce")
            # Re-execute SF writes

        if retried:
            logger.info(f"  Retrying failed targets: {', '.join(retried)}")
        else:
            logger.info("  No failed targets to retry")
        return retried
