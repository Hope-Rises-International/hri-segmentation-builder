/**
 * HRI Segmentation Builder — Apps Script Web App
 * Phase 6: Operator UI for Jessica and Bill
 *
 * Architecture: Lightweight UI that triggers Cloud Run for heavy processing.
 * Heavy review happens in the Draft tab in the MIC Google Sheet.
 */

// --- Configuration ---
const MIC_SHEET_ID = '12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw';
const CLOUD_RUN_URL = 'https://segmentation-builder-qelitx2nya-ue.a.run.app';
const DRIVE_OUTPUT_FOLDER = '1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC';

// MIC tab names
const TAB_CAMPAIGN_CALENDAR = 'mic_flattened.csv';
const TAB_DRAFT = 'Draft';
const TAB_SEGMENT_DETAIL = 'Segment Detail';
const TAB_SEGMENT_RULES = 'Segment Rules';

// Status transitions (one-directional)
const STATUS_ORDER = ['Draft', 'Projected', 'Approved', 'Pulled', 'Mailed'];

// --- Entry Point ---

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('HRI Segmentation Builder')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// --- Campaign Operations ---

/**
 * Get list of DM-eligible campaigns from MIC Campaign Calendar.
 * Returns array of campaign objects for the selector.
 */
function getCampaigns() {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (!ws) return { error: 'Campaign Calendar tab not found' };

  const data = ws.getDataRange().getValues();
  const headers = data[0];
  const campaigns = [];

  // Column indices
  const col = {};
  headers.forEach((h, i) => col[h] = i);

  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    const channel = String(row[col['channel']] || '');
    const budgetQty = Number(row[col['budget_qty_mailed']] || 0);

    // Filter to DM-eligible with budget
    if (!channel.toLowerCase().includes('direct mail') || budgetQty <= 0) continue;

    const budgetCost = Number(row[col['budget_cost']] || 0);
    const cpp = budgetQty > 0 ? (budgetCost / budgetQty) : 0;
    const status = String(row[col['status']] || 'Draft');

    campaigns.push({
      row: i + 1, // 1-indexed sheet row
      campaign_name: String(row[col['campaign_name']] || ''),
      appeal_code: String(row[col['appeal_code']] || ''),
      mail_date: String(row[col['mail_date']] || ''),
      lane: String(row[col['lane']] || ''),
      audience: String(row[col['audience']] || ''),
      budget_qty_mailed: budgetQty,
      budget_cost: budgetCost,
      cpp: Math.round(cpp * 100) / 100,
      projected_revenue: Number(row[col['projected_revenue']] || 0),
      status: status,
      campaign_type: String(row[col['classification']] || 'Appeal'),
      fiscal_year: String(row[col['fiscal_year']] || ''),
      month: String(row[col['month']] || ''),
      is_followup: String(row[col['is_followup']] || '') === 'true',
    });
  }

  // Sort by fiscal year desc, then month
  campaigns.sort((a, b) => {
    if (a.fiscal_year !== b.fiscal_year) return b.fiscal_year.localeCompare(a.fiscal_year);
    return (b.month || '').localeCompare(a.month || '');
  });

  return { campaigns: campaigns };
}

/**
 * Get current Draft tab contents (segment summary).
 */
function getDraftData() {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_DRAFT);
  if (!ws) return { error: 'Draft tab not found' };

  const data = ws.getDataRange().getValues();
  if (data.length <= 1) return { segments: [], headers: data[0] || [] };

  const headers = data[0];
  const segments = [];
  for (let i = 1; i < data.length; i++) {
    const row = {};
    headers.forEach((h, j) => row[h] = data[i][j]);
    segments.push(row);
  }
  return { segments: segments, headers: headers };
}

// --- Projection ---

/**
 * Run a projection for the selected campaign.
 * Currently writes directly to Draft tab via the pipeline data already there.
 * When Cloud Run is deployed (Phase 9), this will trigger the service.
 */
function runProjection(campaignConfig) {
  // Single-operator constraint: check if Draft tab has data from another campaign
  const draftData = getDraftData();
  if (draftData.segments && draftData.segments.length > 0) {
    // Draft tab has existing data — warn operator
    return {
      warning: 'Draft tab already has projection data. Running a new projection will overwrite it.',
      needsConfirm: true,
    };
  }

  // Until Cloud Run is deployed, return a message
  if (!CLOUD_RUN_URL) {
    return {
      status: 'info',
      message: 'Cloud Run service not yet deployed. Run the pipeline locally: ' +
               'cd src && python3 run_diagnostic.py. Results will appear in the Draft tab.',
    };
  }

  // Call Cloud Run (Phase 9)
  try {
    const token = ScriptApp.getOAuthToken();
    const response = UrlFetchApp.fetch(CLOUD_RUN_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(campaignConfig),
      muteHttpExceptions: true,
    });

    const result = JSON.parse(response.getContentText());
    if (response.getResponseCode() !== 200) {
      return { error: 'Cloud Run error: ' + (result.message || response.getContentText()) };
    }

    return {
      status: 'success',
      message: 'Projection complete. Review the Draft tab in the MIC.',
      result: result,
    };
  } catch (e) {
    return { error: 'Projection failed: ' + e.message };
  }
}

/**
 * Confirm and overwrite existing projection.
 */
function runProjectionConfirmed(campaignConfig) {
  // Clear Draft tab first
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_DRAFT);
  if (ws) {
    const lastRow = ws.getLastRow();
    if (lastRow > 1) {
      ws.getRange(2, 1, lastRow - 1, ws.getLastColumn()).clearContent();
    }
  }

  // Re-run projection
  if (!CLOUD_RUN_URL) {
    return {
      status: 'info',
      message: 'Draft tab cleared. Run the pipeline locally to populate.',
    };
  }

  return runProjection(campaignConfig);
}

// --- Approve Workflow ---

/**
 * Approve the current projection: copy Draft tab to Segment Detail tab.
 * Updates campaign status to "Approved".
 */
function approveProjection(campaignId) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);

  // Read Draft tab
  const draftWs = ss.getSheetByName(TAB_DRAFT);
  if (!draftWs) return { error: 'Draft tab not found' };

  const draftData = draftWs.getDataRange().getValues();
  if (draftData.length <= 1) return { error: 'Draft tab is empty — run a projection first' };

  // Get or create Segment Detail tab
  let detailWs = ss.getSheetByName(TAB_SEGMENT_DETAIL);
  if (!detailWs) {
    detailWs = ss.insertSheet(TAB_SEGMENT_DETAIL);
  }

  // Read existing Segment Detail data
  const existingData = detailWs.getDataRange().getValues();
  const headers = draftData[0];

  // Add Campaign ID column if not present
  let campaignIdCol = headers.indexOf('Campaign ID');
  if (campaignIdCol === -1) {
    headers.push('Campaign ID');
    campaignIdCol = headers.length - 1;
  }

  // Tag draft rows with campaign ID
  const newRows = [];
  for (let i = 1; i < draftData.length; i++) {
    const row = [...draftData[i]];
    while (row.length < headers.length) row.push('');
    row[campaignIdCol] = campaignId;
    newRows.push(row);
  }

  // Remove existing rows for this campaign (upsert behavior)
  const keepRows = [headers];
  if (existingData.length > 1) {
    const existingCampaignCol = existingData[0].indexOf('Campaign ID');
    for (let i = 1; i < existingData.length; i++) {
      if (existingCampaignCol >= 0 && existingData[i][existingCampaignCol] === campaignId) {
        continue; // Remove old rows for this campaign
      }
      keepRows.push(existingData[i]);
    }
  }

  // Write: existing (minus this campaign) + new rows
  const allRows = [...keepRows, ...newRows];
  detailWs.clearContents();
  if (allRows.length > 0) {
    detailWs.getRange(1, 1, allRows.length, headers.length).setValues(allRows);
  }

  // Update campaign status
  updateCampaignStatus(campaignId, 'Approved');

  // Update link_to_segments on Campaign Calendar
  updateLinkToSegments(campaignId);

  // Clear Draft tab (ready for next campaign)
  const lastRow = draftWs.getLastRow();
  if (lastRow > 1) {
    draftWs.getRange(2, 1, lastRow - 1, draftWs.getLastColumn()).clearContent();
  }

  return {
    status: 'success',
    message: `Approved. ${newRows.length} segment rows copied to Segment Detail. Draft tab cleared.`,
    segmentCount: newRows.length,
  };
}

// --- Status Management ---

/**
 * Update campaign status in MIC Campaign Calendar.
 */
function updateCampaignStatus(campaignId, newStatus) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (!ws) return;

  const data = ws.getDataRange().getValues();
  const headers = data[0];
  const statusCol = headers.indexOf('status');
  const appealCodeCol = headers.indexOf('appeal_code');
  if (statusCol === -1 || appealCodeCol === -1) return;

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][appealCodeCol]) === campaignId) {
      // Enforce one-directional status transitions
      const currentStatus = String(data[i][statusCol] || 'Draft');
      const currentIdx = STATUS_ORDER.indexOf(currentStatus);
      const newIdx = STATUS_ORDER.indexOf(newStatus);
      if (newIdx >= currentIdx || currentIdx === -1) {
        ws.getRange(i + 1, statusCol + 1).setValue(newStatus);
      }
      break;
    }
  }
}

/**
 * Unlock a campaign back to Projected (from Approved).
 * Requires explicit operator action.
 */
function unlockCampaign(campaignId) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (!ws) return { error: 'Campaign Calendar not found' };

  const data = ws.getDataRange().getValues();
  const headers = data[0];
  const statusCol = headers.indexOf('status');
  const appealCodeCol = headers.indexOf('appeal_code');

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][appealCodeCol]) === campaignId) {
      const current = String(data[i][statusCol] || '');
      if (current === 'Approved') {
        ws.getRange(i + 1, statusCol + 1).setValue('Projected');
        return { status: 'success', message: 'Campaign unlocked back to Projected.' };
      }
      return { error: `Cannot unlock — current status is "${current}", not "Approved".` };
    }
  }
  return { error: 'Campaign not found.' };
}

/**
 * Update link_to_segments on Campaign Calendar.
 */
function updateLinkToSegments(campaignId) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (!ws) return;

  const data = ws.getDataRange().getValues();
  const headers = data[0];
  const linkCol = headers.indexOf('link_to_segments');
  const appealCodeCol = headers.indexOf('appeal_code');
  if (linkCol === -1 || appealCodeCol === -1) return;

  const detailWs = ss.getSheetByName(TAB_SEGMENT_DETAIL);
  if (!detailWs) return;

  const link = `=HYPERLINK("#gid=${detailWs.getSheetId()}", "View Segments")`;

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][appealCodeCol]) === campaignId) {
      ws.getRange(i + 1, linkCol + 1).setFormula(link);
      break;
    }
  }
}

// --- Post-Mailing Actuals ---

/**
 * Update post-mailing actuals on Campaign Calendar.
 */
function updateActuals(campaignId, actuals) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (!ws) return { error: 'Campaign Calendar not found' };

  const data = ws.getDataRange().getValues();
  const headers = data[0];
  const appealCodeCol = headers.indexOf('appeal_code');
  if (appealCodeCol === -1) return { error: 'appeal_code column not found' };

  for (let i = 1; i < data.length; i++) {
    if (String(data[i][appealCodeCol]) === campaignId) {
      // Write actuals to their respective columns
      const fieldsToUpdate = {
        'actual_qty_mailed': actuals.actual_qty_mailed,
        'actual_cost': actuals.actual_cost,
      };

      for (const [field, value] of Object.entries(fieldsToUpdate)) {
        const col = headers.indexOf(field);
        if (col >= 0 && value !== undefined && value !== '') {
          ws.getRange(i + 1, col + 1).setValue(Number(value));
        }
      }

      // Update status to Mailed
      updateCampaignStatus(campaignId, 'Mailed');

      return { status: 'success', message: 'Actuals updated. Status set to Mailed.' };
    }
  }
  return { error: 'Campaign not found.' };
}

// --- Salesforce Campaign_Segment__c Upsert ---

/**
 * Trigger Salesforce Campaign_Segment__c load.
 * Reads approved Segment Detail for this campaign and upserts to SF.
 */
function loadToSalesforce(campaignId) {
  // Read Segment Detail for this campaign
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const detailWs = ss.getSheetByName(TAB_SEGMENT_DETAIL);
  if (!detailWs) return { error: 'Segment Detail tab not found' };

  const data = detailWs.getDataRange().getValues();
  if (data.length <= 1) return { error: 'Segment Detail is empty' };

  const headers = data[0];
  const campaignIdCol = headers.indexOf('Campaign ID');
  const segCodeCol = headers.indexOf('Segment Code');
  const segNameCol = headers.indexOf('Segment Name');
  const qtyCol = headers.indexOf('Quantity');

  if (segCodeCol === -1 || qtyCol === -1) {
    return { error: 'Required columns not found in Segment Detail' };
  }

  // Filter to this campaign's rows
  const segments = [];
  for (let i = 1; i < data.length; i++) {
    if (campaignIdCol >= 0 && String(data[i][campaignIdCol]) !== campaignId) continue;
    segments.push({
      segment_code: String(data[i][segCodeCol] || ''),
      segment_name: String(data[i][segNameCol] || ''),
      quantity: Number(data[i][qtyCol] || 0),
    });
  }

  if (segments.length === 0) {
    return { error: `No Segment Detail rows found for campaign ${campaignId}` };
  }

  // If Cloud Run is deployed, delegate the SF upsert to it
  if (CLOUD_RUN_URL) {
    try {
      const token = ScriptApp.getOAuthToken();
      const response = UrlFetchApp.fetch(CLOUD_RUN_URL + '/sf-load', {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer ' + token,
          'Content-Type': 'application/json',
        },
        payload: JSON.stringify({
          action: 'sf_load',
          campaign_id: campaignId,
          segments: segments,
        }),
        muteHttpExceptions: true,
      });
      const result = JSON.parse(response.getContentText());
      if (response.getResponseCode() !== 200) {
        return { error: 'SF load error: ' + (result.message || response.getContentText()) };
      }
      updateCampaignStatus(campaignId, 'Pulled');
      return { status: 'success', message: `Loaded ${segments.length} segments to Salesforce.`, result: result };
    } catch (e) {
      return { error: 'SF load failed: ' + e.message };
    }
  }

  // Fallback: log what would be loaded (Cloud Run not yet deployed)
  updateCampaignStatus(campaignId, 'Pulled');
  return {
    status: 'info',
    message: `Cloud Run not deployed. ${segments.length} segments ready for SF load. ` +
             'Status set to Pulled. Run SF upsert via pipeline when Cloud Run is live.',
    segments: segments,
  };
}

// --- Drive Output Files ---

/**
 * Get list of output files for a campaign from Drive.
 */
function getOutputFiles(campaignId) {
  try {
    const folder = DriveApp.getFolderById(DRIVE_OUTPUT_FOLDER);
    const files = folder.getFiles();
    const results = [];

    while (files.hasNext()) {
      const file = files.next();
      const name = file.getName();
      if (name.includes(campaignId) || name.includes('DIAG')) {
        results.push({
          name: name,
          url: file.getUrl(),
          size: file.getSize(),
          date: file.getDateCreated().toISOString(),
        });
      }
    }

    results.sort((a, b) => b.date.localeCompare(a.date));
    return { files: results };
  } catch (e) {
    return { error: 'Could not list files: ' + e.message };
  }
}
