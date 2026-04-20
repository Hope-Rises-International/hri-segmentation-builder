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
const BUILD_UNIVERSE_URL = 'https://build-universe-qelitx2nya-ue.a.run.app';
const APPROVE_SCENARIO_URL = 'https://approve-scenario-qelitx2nya-ue.a.run.app';  // Phase 3 pending
const CLOUD_RUN_SA = 'hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com';
const DRIVE_OUTPUT_FOLDER = '1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC';


/**
 * Mint an OIDC identity token for the SA via IAM Credentials API.
 * The deploying user must have roles/iam.serviceAccountTokenCreator on the SA.
 * Returns a token with audience = Cloud Run URL, which Cloud Run accepts.
 */
function getCloudRunToken_(audience) {
  audience = audience || CLOUD_RUN_URL;
  const url = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/' +
    CLOUD_RUN_SA + ':generateIdToken';
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + ScriptApp.getOAuthToken() },
    payload: JSON.stringify({
      audience: audience,
      includeEmail: true,
    }),
    muteHttpExceptions: true,
  });
  if (response.getResponseCode() !== 200) {
    throw new Error('Failed to mint OIDC token: HTTP ' + response.getResponseCode() +
      ' — ' + response.getContentText().substring(0, 300));
  }
  return JSON.parse(response.getContentText()).token;
}


/**
 * Phase 3 — call /approve-scenario endpoint with operator's scenario selections.
 * Generates Printer/Matchback files, writes to Drive + MIC, sets status → Approved.
 */
function approveScenario(payload) {
  try {
    const token = getCloudRunToken_(APPROVE_SCENARIO_URL);
    const response = UrlFetchApp.fetch(APPROVE_SCENARIO_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    });

    const code = response.getResponseCode();
    const body = response.getContentText();

    if (code === 401 || code === 403) {
      return { error: 'Approve auth failed (HTTP ' + code + ').' };
    }
    if (body.charAt(0) !== '{' && body.charAt(0) !== '[') {
      return { error: 'Approve endpoint returned HTTP ' + code + '. Check logs.' };
    }
    const result = JSON.parse(body);
    if (code !== 200) {
      return { error: 'Approve error (HTTP ' + code + '): ' + (result.message || body.substring(0, 200)) };
    }
    return result;
  } catch (e) {
    if (e.message && (e.message.indexOf('Timeout') >= 0 || e.message.indexOf('timed out') >= 0)) {
      return { status: 'running', message: 'Approve running in background (~6 min). Check Drive folder when done.' };
    }
    throw e;
  }
}


/**
 * Phase 1 — call /build-universe endpoint, return universe JSON to browser.
 */
function buildUniverse(campaignConfig) {
  try {
    const token = getCloudRunToken_(BUILD_UNIVERSE_URL);
    const response = UrlFetchApp.fetch(BUILD_UNIVERSE_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(campaignConfig),
      muteHttpExceptions: true,
    });

    const code = response.getResponseCode();
    const body = response.getContentText();

    if (code === 401 || code === 403) {
      return { error: 'Universe auth failed (HTTP ' + code + ').' };
    }
    if (body.charAt(0) !== '{' && body.charAt(0) !== '[') {
      return { error: 'Universe endpoint returned HTTP ' + code + '. Check logs.' };
    }
    const result = JSON.parse(body);
    if (code !== 200) {
      return { error: 'Universe error (HTTP ' + code + '): ' + (result.message || body.substring(0, 200)) };
    }
    return result;
  } catch (e) {
    // UrlFetchApp timeout is expected on first build (~3 min) — return a message,
    // the client will show "loading in background, retry".
    if (e.message && (e.message.indexOf('Timeout') >= 0 || e.message.indexOf('timed out') >= 0)) {
      return { status: 'running', message: 'Universe building in background. Retry in 3 min.' };
    }
    throw e;
  }
}

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

    var campaignName = String(row[col['campaign_name']] || '');
    var laneVal = String(row[col['lane']] || '');
    var isFollowupStr = String(row[col['is_followup']] || '');
    campaigns.push({
      row: i + 1, // 1-indexed sheet row
      campaign_name: campaignName,
      appeal_code: String(row[col['appeal_code']] || ''),
      mail_date: String(row[col['mail_date']] || ''),
      lane: laneVal,
      audience: String(row[col['audience']] || ''),
      budget_qty_mailed: budgetQty,
      budget_cost: budgetCost,
      cpp: Math.round(cpp * 100) / 100,
      projected_revenue: Number(row[col['projected_revenue']] || 0),
      status: status,
      campaign_type: String(row[col['classification']] || 'Appeal'),
      historical_type: classifyCampaignType_(campaignName, laneVal, isFollowupStr),
      fiscal_year: String(row[col['fiscal_year']] || ''),
      month: String(row[col['month']] || ''),
      is_followup: isFollowupStr === 'true' || isFollowupStr === 'TRUE',
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
 * Get available baseline campaigns from Segment Actuals tab.
 * Returns distinct appeal_code + fy combinations.
 */
function getBaselineCampaigns() {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName('Segment Actuals');
  if (!ws) return { baselines: [], message: 'Segment Actuals tab not found' };

  const lastRow = ws.getLastRow();
  if (lastRow <= 1) return { baselines: [], message: 'No baseline data yet' };

  // Read columns A (appeal_code), D (fy), E (mail_date), G (gifts) — for grouping + filtering
  const colA = ws.getRange(2, 1, lastRow - 1, 1).getValues();  // appeal_code
  const colD = ws.getRange(2, 4, lastRow - 1, 1).getValues();  // fy
  const colE = ws.getRange(2, 5, lastRow - 1, 1).getValues();  // mail_date
  const colG = ws.getRange(2, 7, lastRow - 1, 1).getValues();  // gifts

  // Group by appeal_code: aggregate total gifts, capture fy and mail_date
  const campaigns = {};
  for (let i = 0; i < colA.length; i++) {
    const code = String(colA[i][0] || '').trim();
    if (!code) continue;
    if (!campaigns[code]) {
      campaigns[code] = {
        code: code,
        fy: String(colD[i][0] || '').trim(),
        mail_date: String(colE[i][0] || '').trim(),
        total_gifts: 0,
      };
    }
    campaigns[code].total_gifts += Number(colG[i][0] || 0);
  }

  // Look up campaign names from Campaign Calendar (single read)
  const nameMap = {};
  const monthMap = {};
  const calWs = ss.getSheetByName(TAB_CAMPAIGN_CALENDAR);
  if (calWs) {
    const calData = calWs.getDataRange().getValues();
    const calHeaders = calData[0];
    const calCol = {};
    calHeaders.forEach((h, j) => calCol[h] = j);
    for (let j = 1; j < calData.length; j++) {
      const ac = String(calData[j][calCol['appeal_code']] || '').trim();
      if (ac && !nameMap[ac]) {
        nameMap[ac] = String(calData[j][calCol['campaign_name']] || ac);
        monthMap[ac] = String(calData[j][calCol['month']] || '');
      }
    }
  }

  // Build dropdown entries — one per campaign, only those with gifts > 0
  const baselines = [];
  for (const code in campaigns) {
    const c = campaigns[code];
    if (c.total_gifts <= 0) continue;  // Skip campaigns with no actual data
    const name = nameMap[code] || code;
    const month = monthMap[code] || '';
    baselines.push({
      appeal_code: code,
      fy: c.fy,
      mail_date: c.mail_date,
      label: code + ' — ' + (month ? month + ' ' : '') + name + ' — ' + c.fy,
    });
  }

  // Sort by mail_date descending (most recent first), then FY
  baselines.sort((a, b) => b.mail_date.localeCompare(a.mail_date) || b.fy.localeCompare(a.fy));
  return { baselines: baselines };
}


/**
 * Return the set of campaign-type baselines available from
 * sf_cache.historical_baseline. Static list — kept in sync with
 * src/campaign_types.py (ALL_TYPES). The backend silently falls back
 * to 'Overall' for any (type, segment) combination without data.
 */
function getHistoricalBaselineTypes() {
  return {
    types: [
      // Base types
      'Shipping', 'Tax Receipt', 'Year End', 'Easter', 'Renewal',
      'Faith Leaders', 'Shoes', 'Whole Person Healing', 'FYE',
      // Chaser variants
      'Shipping Chaser', 'Tax Receipt Chaser', 'Year End Chaser',
      'Easter Chaser', 'Renewal Chaser', 'Faith Leaders Chaser',
      'Shoes Chaser', 'Whole Person Healing Chaser', 'FYE Chaser',
      // Lane-based
      'Newsletter', 'Acquisition',
      // Catch-all
      'Other',
      // Meta-average across all non-Acquisition types
      'Overall',
    ],
  };
}


/**
 * Classify a campaign name + is_followup into one of the types
 * returned by getHistoricalBaselineTypes. Mirrors the order-sensitive
 * logic in src/campaign_types.py — MUST stay in sync.
 * Chaser variants are tested before base type match.
 */
function classifyCampaignType_(campaignName, lane, isFollowup) {
  var name = String(campaignName || '');
  var nameLc = name.toLowerCase();
  var chaser = false;
  var fu = String(isFollowup || '').toUpperCase();
  if (fu === 'TRUE' || fu === '1' || fu === 'YES') chaser = true;
  if (!chaser && /\bchaser\b|\bf\/u\b|\bfu\b/i.test(name)) chaser = true;

  var baseTypes = [
    ['Shipping',              'shipping'],
    ['Tax Receipt',           'tax receipt'],
    ['Year End',              'year end'],
    ['Easter',                'easter'],
    ['Renewal',               'renewal'],
    ['Faith Leaders',         'faith leaders'],
    ['Shoes',                 'shoes'],
    ['Whole Person Healing',  'whole person healing'],
  ];
  for (var i = 0; i < baseTypes.length; i++) {
    if (nameLc.indexOf(baseTypes[i][1]) >= 0) {
      return chaser ? baseTypes[i][0] + ' Chaser' : baseTypes[i][0];
    }
  }
  if (nameLc.indexOf('fye') >= 0 || nameLc.indexOf('fiscal year end') >= 0) {
    return chaser ? 'FYE Chaser' : 'FYE';
  }

  var laneVal = String(lane || '').trim();
  if (laneVal === 'Newsletter')  return 'Newsletter';
  if (laneVal === 'Acquisition') return 'Acquisition';
  return 'Other';
}


/**
 * Get baseline segment actuals for a specific campaign appeal code.
 */
function getBaselineData(appealCode) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  let ws;
  try {
    ws = ss.getSheetByName('Segment Actuals');
  } catch (e) {
    return { error: 'Segment Actuals tab not found' };
  }
  if (!ws) return { error: 'Segment Actuals tab not found' };

  const data = ws.getDataRange().getValues();
  if (data.length <= 1) return { segments: [] };

  const headers = data[0];
  const col = {};
  headers.forEach((h, i) => col[h] = i);

  const segments = [];
  for (let i = 1; i < data.length; i++) {
    const row = data[i];
    if (String(row[col['appeal_code']] || '').trim() !== appealCode) continue;
    segments.push({
      source_code: String(row[col['source_code']] || ''),
      segment_description: String(row[col['segment_description']] || ''),
      response_rate: Number(row[col['response_rate']] || 0),
      avg_gift: Number(row[col['avg_gift']] || 0),
      contacts: Number(row[col['contacts']] || 0),
      revenue: Number(row[col['revenue']] || 0),
      cost: Number(row[col['cost']] || 0),
    });
  }
  return { segments: segments };
}


/**
 * Save operator per-segment overrides (include flag + percent_include) to Draft tab.
 * Adds columns operator_include_flag and percent_include if not present.
 */
function saveDraftOverrides(overrides) {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ws = ss.getSheetByName(TAB_DRAFT);
  if (!ws) return { error: 'Draft tab not found' };

  const data = ws.getDataRange().getValues();
  if (data.length <= 1) return { error: 'Draft tab is empty' };

  const headers = data[0];
  const segCodeCol = headers.indexOf('Segment Code');
  if (segCodeCol < 0) return { error: 'Segment Code column not found' };

  // Ensure operator_include_flag and percent_include columns exist
  let includeCol = headers.indexOf('operator_include_flag');
  let percentCol = headers.indexOf('percent_include');
  const newHeaders = headers.slice();
  let appendedHeaders = false;
  if (includeCol < 0) {
    newHeaders.push('operator_include_flag');
    includeCol = newHeaders.length - 1;
    appendedHeaders = true;
  }
  if (percentCol < 0) {
    newHeaders.push('percent_include');
    percentCol = newHeaders.length - 1;
    appendedHeaders = true;
  }

  // Resize worksheet to fit new columns
  if (ws.getMaxColumns() < newHeaders.length) {
    ws.insertColumnsAfter(ws.getMaxColumns(), newHeaders.length - ws.getMaxColumns());
  }
  if (appendedHeaders) {
    ws.getRange(1, 1, 1, newHeaders.length).setValues([newHeaders]);
  }

  // Build payload rows
  let count = 0;
  for (let i = 1; i < data.length; i++) {
    const code = String(data[i][segCodeCol] || '').trim();
    if (!code) continue;
    const ov = overrides[code];
    if (!ov) {
      // No override — default: include=true, percent=100
      ws.getRange(i + 1, includeCol + 1).setValue(true);
      ws.getRange(i + 1, percentCol + 1).setValue(100);
      continue;
    }
    ws.getRange(i + 1, includeCol + 1).setValue(Boolean(ov.include));
    ws.getRange(i + 1, percentCol + 1).setValue(Number(ov.percent_include || 0));
    count++;
  }

  return { status: 'success', count: count };
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

  // Call Cloud Run — with BQ cache, pipeline completes in ~2-3 min.
  // Without BQ cache (SF fallback), pipeline takes ~14 min and will timeout.
  try {
    const token = getCloudRunToken_();
    const response = UrlFetchApp.fetch(CLOUD_RUN_URL, {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Content-Type': 'application/json',
      },
      payload: JSON.stringify(campaignConfig),
      muteHttpExceptions: true,
    });

    const code = response.getResponseCode();
    const body = response.getContentText();

    // Guard against HTML error pages (401/403/5xx)
    if (code === 401 || code === 403) {
      return { error: 'Cloud Run auth failed (HTTP ' + code + '). ' +
               'The deploying user needs roles/run.invoker on the Cloud Run service.' };
    }

    if (body.charAt(0) !== '{' && body.charAt(0) !== '[') {
      Logger.log('Cloud Run returned non-JSON (HTTP ' + code + '): ' + body.substring(0, 500));
      return { error: 'Cloud Run returned HTTP ' + code + '. Check Cloud Run logs for details.' };
    }

    const result = JSON.parse(body);
    if (code !== 200) {
      return { error: 'Cloud Run error (HTTP ' + code + '): ' + (result.message || body.substring(0, 200)) };
    }

    return {
      status: 'success',
      message: 'Projection complete. Review the Draft tab in the MIC.',
      result: result,
    };
  } catch (e) {
    // UrlFetchApp timeout (~60s) is expected — pipeline takes ~9 minutes.
    // Cloud Run keeps processing after client disconnect.
    if (e.message && (e.message.indexOf('Timeout') >= 0 || e.message.indexOf('timed out') >= 0 ||
        e.message.indexOf('deadline') >= 0 || e.message.indexOf('DEADLINE') >= 0)) {
      return {
        status: 'running',
        message: 'Projection started. The pipeline takes about 9 minutes to process 358K accounts. ' +
                 'Cloud Run is working in the background. Click "Refresh Draft Tab" in a few minutes ' +
                 'to see results. You can also check the MIC Draft tab directly.',
      };
    }
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
      const token = getCloudRunToken_();
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
