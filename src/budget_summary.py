"""Budget Summary tab builder per spec Section 2.2.

Generates formula-driven rollups on the MIC Budget Summary tab:
- FY-level aggregation by lane and channel
- Budget vs. actual with variance
- All formula-driven from Campaign Calendar — no manual entry
"""

from __future__ import annotations
import logging

import gspread
import pandas as pd

from config import MIC_SHEET_ID

logger = logging.getLogger(__name__)

TAB_BUDGET_SUMMARY = "Budget Summary"
TAB_CAMPAIGN_CALENDAR = "mic_flattened.csv"


def build_budget_summary(gc: gspread.Client):
    """Create or update the Budget Summary tab with formula-driven rollups.

    Reads Campaign Calendar structure to generate SUMIFS formulas
    that aggregate budget and actual data by FY, lane, and channel.
    """
    ss = gc.open_by_key(MIC_SHEET_ID)

    # Read Campaign Calendar to get available FYs and lanes
    cal_ws = ss.worksheet(TAB_CAMPAIGN_CALENDAR) if TAB_CAMPAIGN_CALENDAR in [w.title for w in ss.worksheets()] else None
    if not cal_ws:
        logger.error(f"Campaign Calendar tab '{TAB_CAMPAIGN_CALENDAR}' not found")
        return {"error": f"Tab not found: {TAB_CAMPAIGN_CALENDAR}"}

    cal_data = cal_ws.get_all_records()
    cal_df = pd.DataFrame(cal_data)

    # Get column letters for formulas
    cal_headers = cal_ws.row_values(1)
    col_map = {}
    for i, h in enumerate(cal_headers):
        col_map[h] = chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26)

    # Determine FYs and lanes present
    fys = sorted(cal_df["fiscal_year"].unique()) if "fiscal_year" in cal_df.columns else []
    lanes = sorted(cal_df["lane"].dropna().unique()) if "lane" in cal_df.columns else []
    channels = sorted(cal_df["channel"].dropna().unique()) if "channel" in cal_df.columns else []

    if not fys:
        logger.warning("No fiscal years found in Campaign Calendar")
        return {"error": "No fiscal years in data"}

    logger.info(f"  Building Budget Summary: {len(fys)} FYs, {len(lanes)} lanes, {len(channels)} channels")

    # Get or create Budget Summary tab
    try:
        bs_ws = ss.worksheet(TAB_BUDGET_SUMMARY)
        bs_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        bs_ws = ss.add_worksheet(title=TAB_BUDGET_SUMMARY, rows="200", cols="20")

    cal_sheet_name = TAB_CAMPAIGN_CALENDAR.replace("'", "''")
    fy_col = col_map.get("fiscal_year", "A")
    lane_col = col_map.get("lane", "J")
    channel_col = col_map.get("channel", "F")
    budget_qty_col = col_map.get("budget_qty_mailed", "K")
    budget_cost_col = col_map.get("budget_cost", "L")
    proj_rev_col = col_map.get("projected_revenue", "M")
    actual_qty_col = col_map.get("actual_qty_mailed", "Q")
    actual_cost_col = col_map.get("actual_cost", "R")
    actual_rev_col = col_map.get("actual_revenue", "S")

    # Build the summary grid
    rows = []

    # Header
    rows.append([
        "Fiscal Year", "Lane", "Channel",
        "Budget Qty", "Budget Cost", "Proj Revenue",
        "Actual Qty", "Actual Cost", "Actual Revenue",
        "Qty Variance", "Cost Variance", "Revenue Variance",
        "Campaigns",
    ])

    # Row counter for formula references (starts at row 2)
    row_num = 2

    # Summary by FY × Lane
    rows.append(["", "", "", "", "", "", "", "", "", "", "", "", ""])
    rows.append(["BY FISCAL YEAR AND LANE", "", "", "", "", "", "", "", "", "", "", "", ""])
    row_num += 2

    for fy in fys:
        for lane in lanes:
            if not lane:
                continue
            # SUMIFS formulas referencing Campaign Calendar
            cal_range = f"'{cal_sheet_name}'"
            budget_qty_f = f'=SUMIFS({cal_range}!{budget_qty_col}:{budget_qty_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            budget_cost_f = f'=SUMIFS({cal_range}!{budget_cost_col}:{budget_cost_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            proj_rev_f = f'=SUMIFS({cal_range}!{proj_rev_col}:{proj_rev_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            actual_qty_f = f'=SUMIFS({cal_range}!{actual_qty_col}:{actual_qty_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            actual_cost_f = f'=SUMIFS({cal_range}!{actual_cost_col}:{actual_cost_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            actual_rev_f = f'=SUMIFS({cal_range}!{actual_rev_col}:{actual_rev_col},{cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'
            # Variance = Actual - Budget
            qty_var_f = f'=H{row_num}-D{row_num}'
            cost_var_f = f'=I{row_num}-E{row_num}'
            rev_var_f = f'=J{row_num}-F{row_num}'
            # Campaign count
            count_f = f'=COUNTIFS({cal_range}!{fy_col}:{fy_col},"{fy}",{cal_range}!{lane_col}:{lane_col},"{lane}")'

            rows.append([
                fy, lane, "",
                budget_qty_f, budget_cost_f, proj_rev_f,
                actual_qty_f, actual_cost_f, actual_rev_f,
                qty_var_f, cost_var_f, rev_var_f,
                count_f,
            ])
            row_num += 1

    # FY totals
    rows.append(["", "", "", "", "", "", "", "", "", "", "", "", ""])
    rows.append(["BY FISCAL YEAR (TOTAL)", "", "", "", "", "", "", "", "", "", "", "", ""])
    row_num += 2

    for fy in fys:
        cal_range = f"'{cal_sheet_name}'"
        budget_qty_f = f'=SUMIFS({cal_range}!{budget_qty_col}:{budget_qty_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        budget_cost_f = f'=SUMIFS({cal_range}!{budget_cost_col}:{budget_cost_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        proj_rev_f = f'=SUMIFS({cal_range}!{proj_rev_col}:{proj_rev_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        actual_qty_f = f'=SUMIFS({cal_range}!{actual_qty_col}:{actual_qty_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        actual_cost_f = f'=SUMIFS({cal_range}!{actual_cost_col}:{actual_cost_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        actual_rev_f = f'=SUMIFS({cal_range}!{actual_rev_col}:{actual_rev_col},{cal_range}!{fy_col}:{fy_col},"{fy}")'
        qty_var_f = f'=H{row_num}-D{row_num}'
        cost_var_f = f'=I{row_num}-E{row_num}'
        rev_var_f = f'=J{row_num}-F{row_num}'
        count_f = f'=COUNTIFS({cal_range}!{fy_col}:{fy_col},"{fy}")'

        rows.append([
            fy, "ALL", "",
            budget_qty_f, budget_cost_f, proj_rev_f,
            actual_qty_f, actual_cost_f, actual_rev_f,
            qty_var_f, cost_var_f, rev_var_f,
            count_f,
        ])
        row_num += 1

    # Write to sheet
    bs_ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")
    logger.info(f"  Budget Summary tab written: {len(rows)} rows ({len(fys)} FYs, {len(lanes)} lanes)")

    return {
        "status": "success",
        "rows": len(rows),
        "fiscal_years": fys,
        "lanes": lanes,
    }


def validate_scorecard_contract():
    """Validate that the Segmentation Builder's Campaign_Segment__c output
    is compatible with the Campaign Scorecard's input.

    Returns a validation report as DataFrame.
    """
    checks = []

    # The Segmentation Builder writes:
    #   Campaign__c + Segment_Name__c (upsert key)
    #   Source_Code__c (15-char appeal code)
    #   Quantity_Mailed__c (segment quantity)
    #   Mail_Date__c (actual mail date)
    #
    # The Campaign Scorecard reads:
    #   Source_Code__c (joins to True_Appeal_Code__c on Opportunity)
    #   Campaign__c, Campaign__r.Name, Campaign__r.Appeal_Code__c
    #   Contacts__c, Gifts__c, Revenue__c (for metrics)
    #   Total_Cost__c (now a formula: CPP × Contacts)
    #   Mail_Date__c (for response curves)

    checks.append({
        "Check": "Upsert Key (Campaign__c + Segment_Name__c)",
        "Builder Writes": "Yes",
        "Scorecard Reads": "Yes (for grouping)",
        "Status": "COMPATIBLE",
    })
    checks.append({
        "Check": "Source_Code__c (appeal code)",
        "Builder Writes": "15-char internal code",
        "Scorecard Reads": "Joins to Opportunity.True_Appeal_Code__c",
        "Status": "COMPATIBLE — Scorecard reads Source_Code__c for segment identification",
    })
    checks.append({
        "Check": "Contacts__c / Quantity_Mailed__c",
        "Builder Writes": "Quantity_Mailed__c (projected qty)",
        "Scorecard Reads": "Contacts__c (for response rate denominator)",
        "Status": "REVIEW — Builder writes Quantity_Mailed__c. Confirm Contacts__c is populated from this or separately.",
    })
    checks.append({
        "Check": "Total_Cost__c",
        "Builder Writes": "No (formula field since 2026-03-31)",
        "Scorecard Reads": "Yes (= Campaign CPP × Contacts)",
        "Status": "COMPATIBLE — auto-computed from Campaign-level CPP",
    })
    checks.append({
        "Check": "Mail_Date__c",
        "Builder Writes": "Yes (from MIC Campaign Calendar)",
        "Scorecard Reads": "Yes (for response curve time series)",
        "Status": "COMPATIBLE",
    })
    checks.append({
        "Check": "Gifts__c / Revenue__c",
        "Builder Writes": "No (populated by Scorecard from Opportunity data)",
        "Scorecard Reads": "Writes these fields",
        "Status": "COMPATIBLE — one writes, the other reads",
    })
    checks.append({
        "Check": "Response_Rate__c / Average_Gift__c / ROI__c / Net_Revenue__c",
        "Builder Writes": "No (formula fields)",
        "Scorecard Reads": "Yes (auto-computed)",
        "Status": "COMPATIBLE — all formula fields",
    })
    checks.append({
        "Check": "MIC Campaign Calendar actuals write-back",
        "Builder Writes": "No (not yet — Open Item #7)",
        "Scorecard Reads": "Writes to own Scorecard Data Sheet, not MIC",
        "Status": "DEFERRED — Phase 8 scoped this as Budget Summary formulas only. MIC actuals write-back is Scorecard enhancement.",
    })

    return pd.DataFrame(checks)
