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
      'Christmas Shipping', 'Shipping', 'Tax Receipt', 'Year End', 'Easter',
      'Renewal', 'Faith Leaders', 'Shoes', 'Whole Person Healing', 'FYE',
      // Chaser variants
      'Christmas Shipping Chaser', 'Shipping Chaser', 'Tax Receipt Chaser',
      'Year End Chaser', 'Easter Chaser', 'Renewal Chaser',
      'Faith Leaders Chaser', 'Shoes Chaser', 'Whole Person Healing Chaser',
      'FYE Chaser',
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

  // Lane wins over name-based rules so "July Acquisition Shipping"
  // (lane=Acquisition) classifies as Acquisition, not Shipping.
  var laneVal = String(lane || '').trim();
  if (laneVal === 'Newsletter')  return 'Newsletter';
  if (laneVal === 'Acquisition') return 'Acquisition';

  // Christmas Shipping tested BEFORE Shipping so the substring check
  // doesn't collapse it into regular Shipping.
  var baseTypes = [
    ['Christmas Shipping',    'christmas shipping'],
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

// NOTE: The Salesforce Campaign_Segment__c upsert lives in the separate
// SF Uploader build now (bundling what Bekah and Jessica do together).
// The Drive output-files listing also moved out of this UI — operators
// navigate the shared Drive folder directly.


// --- Operator Documentation Tabs (SPEC §17) ---
//
// Two tabs in the MIC document the engine's logic and the UI buttons in
// language Jessica + Bill can read. Refreshed by refreshReferenceTabs(),
// either via the "Refresh Reference Tabs" button in the web app or by
// running the function from the Apps Script editor after a spec change.
// Content mirrors the architect's 2026-04-27 instruction; once SPEC.md
// §17.1/§17.2 land in main, this content should be regenerated from
// the spec rather than hand-curated here.

const SPEC_VERSION = '2026-04-27';
const TAB_LOGIC_REFERENCE  = 'Logic & Math Reference';
const TAB_BUTTON_REFERENCE = 'Button Reference';

function refreshReferenceTabs() {
  const ss = SpreadsheetApp.openById(MIC_SHEET_ID);
  const ts = new Date().toISOString();
  _writeLogicReferenceTab_(ss, ts);
  _writeButtonReferenceTab_(ss, ts);
  return {
    status: 'success',
    message: 'Reference tabs refreshed at ' + ts,
    spec_version: SPEC_VERSION,
  };
}

function _ensureTab_(ss, name, rows, cols) {
  let ws = ss.getSheetByName(name);
  if (!ws) ws = ss.insertSheet(name);
  ws.clear();
  if (ws.getMaxRows() < rows) ws.insertRowsAfter(ws.getMaxRows(), rows - ws.getMaxRows());
  if (ws.getMaxColumns() < cols) ws.insertColumnsAfter(ws.getMaxColumns(), cols - ws.getMaxColumns());
  return ws;
}

function _writeBlock_(ws, startRow, values) {
  if (!values || values.length === 0) return startRow;
  const cols = Math.max.apply(null, values.map(r => r.length));
  // pad to rectangular
  const padded = values.map(r => {
    const p = r.slice();
    while (p.length < cols) p.push('');
    return p;
  });
  ws.getRange(startRow, 1, padded.length, cols).setValues(padded);
  return startRow + padded.length;
}

function _writeSectionHeader_(ws, row, title) {
  ws.getRange(row, 1).setValue(title);
  ws.getRange(row, 1).setFontWeight('bold');
  return row + 1;
}

function _writeLogicReferenceTab_(ss, ts) {
  const ws = _ensureTab_(ss, TAB_LOGIC_REFERENCE, 400, 8);
  let r = 1;
  ws.getRange(1, 1).setValue('HRI Segmentation Builder — Logic & Math Reference').setFontWeight('bold');
  ws.getRange(2, 1).setValue(
    'Synced from SPEC.md v' + SPEC_VERSION + '. Last refreshed: ' + ts +
    '. Do not edit directly — re-run "Refresh Reference Tabs" from the web app after any spec change.');
  r = 4;

  // 1. Waterfall Hierarchy
  r = _writeSectionHeader_(ws, r, '1. Waterfall Hierarchy');
  r = _writeBlock_(ws, r, [
    ['Position', 'Position Name', 'Field / Logic', 'Toggle Default', 'Notes'],
    ['1', 'Global Suppression (Tier 1 hard)', 'Always-on suppressions', 'Always ON', 'See section 2.'],
    ['2', 'Major Gift Portfolio', 'Staff_Manager__c populated', 'ON', 'Toggle OFF excludes from universe.'],
    ['3', 'Mid-Level', 'Cumulative $1k–$5k AND active 24mo', 'ON', 'Toggle OFF excludes from universe.'],
    ['4', 'Monthly Sustainers', 'Miracle_Partner__c = TRUE', 'OFF', 'Toggle OFF (default) excludes from general appeals.'],
    ['5', 'Cornerstone Partners', 'Cornerstone_Partner__c = TRUE', 'ON', 'Toggle OFF excludes from universe entirely.'],
    ['6', 'New Donor', 'Lifecycle = New Donor (90-day welcome)', 'OFF', 'Toggle OFF (default) excludes new donors.'],
    ['7', 'Active Housefile — High Value', 'R1–R2 + F1–F2 + M1–M2', 'ON', 'Sub-segments AH01–AH03.'],
    ['8', 'Active Housefile — Standard', 'R2 remaining', 'ON', 'Sub-segments AH04–AH06.'],
    ['9', 'Mid-Level Prospect', 'Cumulative $500–$999 AND active 24mo', 'ON', 'MP01.'],
    ['10', 'Lapsed Recent', 'R3 (13–24mo) + 2+ lifetime gifts', 'ON', 'LR01 (13–18), LR02 (19–24).'],
    ['11', 'Deep Lapsed', 'R4–R5 (25–48mo) + cumulative ≥ $10', 'ON', 'DL01–DL04 by recency × monetary.'],
    ['12', 'CBNC Flag Override', 'Pre-computed CBNC flag', 'Always ON', 'Catches anyone unassigned by 2–11.'],
  ]);
  r += 1;

  // 2. Tier 1 Suppression
  r = _writeSectionHeader_(ws, r, '2. Suppression — Tier 1 (hard, always ON)');
  r = _writeBlock_(ws, r, [
    ['#', 'Rule', 'Field', 'Notes'],
    ['1', 'All household members deceased', 'npsp__All_Members_Deceased__c', 'Single-contact-deceased is Tier 3.'],
    ['2', 'Do Not Contact', 'Do_Not_Contact__c', ''],
    ['3', 'No Mail Code', 'No_Mail_Code__c', ''],
    ['4', 'Undeliverable Address', 'npsp__Undeliverable_Address__c', ''],
    ['5', 'NCOA Deceased', 'NCOA_Deceased_Processing__c', ''],
    ['6', 'Blank Address', 'BillingStreet / BillingCity / BillingPostalCode null', 'Any one blank → suppress.'],
  ]);
  r += 1;

  // 3. Tier 2 Suppression
  r = _writeSectionHeader_(ws, r, '3. Suppression — Tier 2 (communication preferences, default ON)');
  r = _writeBlock_(ws, r, [
    ['#', 'Rule', 'Field', 'Default', 'Conditional Logic'],
    ['1', 'Newsletters Only', 'Newsletters_Only__c', 'ON', 'Suppress for non-newsletter campaigns.'],
    ['2', 'Newsletter and Prospectus Only', 'Newsletter_and_Prospectus_Only__c', 'ON', 'Same — only newsletters/prospectus.'],
    ['3', 'No Name Sharing', 'No_Name_Sharing__c', 'ON', 'Suppress from acquisition campaigns only.'],
    ['4', 'Match Only', 'Match_Only__c', 'ON', 'Suppress from non-matching-gift campaigns.'],
    ['5', 'Address Unknown', 'Address_Unknown__c', 'ON', 'Hard-suppress mail.'],
    ['6', 'Not Deliverable', 'Not_Deliverable__c', 'ON', 'Hard-suppress mail.'],
  ]);
  r += 1;

  // 4. Tier 3 Suppression
  r = _writeSectionHeader_(ws, r, '4. Suppression — Tier 3 (rare/legacy, default OFF)');
  r = _writeBlock_(ws, r, [
    ['#', 'Rule', 'Field', 'Default', 'Conditional Logic'],
    ['1', 'Primary Contact Deceased', 'Primary_Contact_is_Deceased__c', 'OFF', 'Some households still mail-eligible via spouse.'],
    ['2', 'X1 Mailing Christmas Catalog Only', 'X1_Mailing_Xmas_Catalog__c', 'OFF', 'Suppress except for Christmas Catalog.'],
    ['3', 'X2 Mailings Christmas Appeal Only', 'X2_Mailings_Xmas_Appeal__c', 'OFF', 'Suppress except for Christmas Appeal.'],
  ]);
  r += 1;

  // 5. Segment-Level Suppression Rules
  r = _writeSectionHeader_(ws, r, '5. Segment-Level Suppression Rules');
  r = _writeBlock_(ws, r, [
    ['Rule', 'Default', 'State', 'Notes'],
    ['Recent-gift suppression', 'ON', 'Active', 'Skip donors who gave in last 21 days.'],
    ['Break-even suppression', 'ON', 'Active', 'Drop segments below break-even RR.'],
    ['Response-rate floor', 'ON', 'Active', 'Drop segments below configured RR floor.'],
    ['Frequency cap', 'ON', 'Active', 'Skip donors who already received N appeals this FY.'],
    ['5% holdout', 'ON', 'Active', 'Random 5% suppressed-segment holdout for measurement.'],
  ]);
  r += 1;

  // 6. Ask String Math
  r = _writeSectionHeader_(ws, r, '6. Ask String Math');
  r = _writeBlock_(ws, r, [
    ['Step', 'Rule'],
    ['Basis', 'Active / Mid-Level / MP / Cornerstone use HPC (npo02__LargestAmount__c). Lapsed / Deep Lapsed / CBNC use MRC (npo02__LastOppAmount__c).'],
    ['Ladder', 'AskAmount1 = basis × 1.0,  AskAmount2 = basis × 1.5,  AskAmount3 = basis × 2.0.'],
    ['Rounding', 'UP to nearest $5 below $100, UP to nearest $25 at/above $100.'],
    ['Floor / Ceiling', 'Floor = $15, Ceiling = $4,975 (rounded down to $25 increments).'],
    ['Floor-collapse fallback', 'If ANY raw tier (basis × multiplier) is < $15 floor, replace the WHOLE ladder with $15 / $25 / $35 as a unit. Never re-floor tiers independently.'],
    ['Mid-Level / Major', 'ML*, MJ*, MP*, CS01, SU01: AskAmount1/2/3 always populated regardless of segment. Letterhead template controls display.'],
    ['AskAmountLabel', 'ALWAYS BLANK in CSV. Donor fill-in line; the lettershop renders the static label.'],
  ]);
  r += 1;

  // 7. Scanline & Check Digit
  r = _writeSectionHeader_(ws, r, '7. Scanline & Check Digit');
  r = _writeBlock_(ws, r, [
    ['Field', 'Definition'],
    ['Format', '<DonorID:9> <CampaignAppealCode:9> <CheckDigit:1> — 21 chars total, two literal spaces.'],
    ['DonorID', '9 chars; numeric Constituent_Id zero-padded; S-prefixed pass through.'],
    ['CampaignAppealCode', '<TypePrefix:1><FY:2><Campaign:2><SegmentCode:4>.  e.g. A2651AH01.'],
    ['Check digit step 1', 'Treat the 18-char (DonorID+AppealCode) as 18 individual chars.'],
    ['Check digit step 2', 'Replace alpha → digit per table: A=1 B=2 C=3 D=4 E=5 F=6 G=7 H=8 I=9 / J=1 K=2 L=3 M=4 N=5 O=6 P=7 Q=8 R=9 / S=2 T=3 U=4 V=5 W=6 X=7 Y=8 Z=9. Numerics keep value.'],
    ['Check digit step 3', 'Alternating weights 1, 2, 1, 2, … across 18 positions.'],
    ['Check digit step 4', 'Multiply value × weight per position.'],
    ['Check digit step 5', 'If product > 9, subtract 9; otherwise keep.'],
    ['Check digit step 6', 'Sum all 18 step-5 values.'],
    ['Check digit step 7', 'CheckDigit = (10 − (sum mod 10)) mod 10.'],
    ['Worked example', '070122327W16B1AJ30 → CD 6.  Full scanline: "070122327 W16B1AJ30 6".'],
  ]);
  r += 1;

  // 8. Appeal Code Structure
  r = _writeSectionHeader_(ws, r, '8. Appeal Code Structure');
  r = _writeBlock_(ws, r, [
    ['Code', 'Length', 'Position 1', 'Pos 2-3', 'Pos 4-5', 'Pos 6-9', 'Pos 10-12', 'Pos 13-15'],
    ['CampaignAppealCode (Print)', '9', 'TypePrefix', 'FY', 'Campaign #', 'SegmentCode', '—', '—'],
    ['InternalAppealCode (Matchback only)', '15', 'Program', 'FY', 'Month', 'SegmentCode', 'PackageCode', 'TestFlag'],
    ['TypePrefix legend', '', 'A = Appeal,  M = Mid-Level,  R = Renewal,  C = Cornerstone', '', '', '', '', ''],
  ]);
  r += 1;

  // 9. Reply Copy Tier
  r = _writeSectionHeader_(ws, r, '9. Reply Copy Tier');
  r = _writeBlock_(ws, r, [
    ['Tier', 'Criteria', 'Copy template key'],
    ['ACTIVE', 'Gave in current + prior FY', 'reply.active'],
    ['LAPSED', 'Last gift > 12 months', 'reply.lapsed'],
    ['NEW', 'First gift in current FY', 'reply.new'],
    ['REACTIVATED', '12+ month gap, gave in last 12 months', 'reply.reactivated'],
  ]);
  r += 1;

  // 10. Holdout & Floor Collapse Edge Cases
  r = _writeSectionHeader_(ws, r, '10. Holdout & Floor-Collapse Edge Cases');
  r = _writeBlock_(ws, r, [
    ['Topic', 'Rule'],
    ['5% holdout', 'Random 5% of each campaign suppressed from Print. Kept in Matchback (Holdout=TRUE) for measurement.'],
    ['Floor-collapse', 'When any tier of basis × multiplier < $15 floor, replace WHOLE ladder with $15/$25/$35 as a unit (not per-tier re-floor).'],
    ['Toggle exclude', 'Toggle OFF removes matching donors from the universe entirely BEFORE waterfall runs (no fall-through).'],
  ]);
  r += 1;

  // 11. PackageCode Routing
  r = _writeSectionHeader_(ws, r, '11. PackageCode Routing');
  r = _writeBlock_(ws, r, [
    ['Segment Family', 'Default Package', 'Notes'],
    ['Active Housefile (AH*)', 'P01', 'Standard DM package.'],
    ['Lapsed Recent (LR*)', 'P01', ''],
    ['Deep Lapsed (DL*)', 'P01', ''],
    ['CBNC (CB*)', 'P01', ''],
    ['Mid-Level (ML*)', 'P02', 'High-touch: better paper, first-class postage.'],
    ['Mid-Level Prospect (MP*)', 'P01', 'Standard with upgrade messaging.'],
    ['Cornerstone (CS*)', 'P03', 'Legacy ALM branding.'],
    ['Major Gift (MJ*)', 'P04', 'Custom package, no ask amounts on reply device (template suppresses display).'],
    ['Sustainer (SU*)', 'P01', ''],
    ['New Donor (ND*)', 'P01', ''],
    ['Override mechanism', '—', 'Per-segment overrides live in MIC Segment Rules tab; default ladder above.'],
  ]);
  r += 1;

  // 12. Print columns
  r = _writeSectionHeader_(ws, r, '12. Output File Columns — Print');
  r = _writeBlock_(ws, r, [
    ['#', 'Column', 'Source'],
    ['1', 'DonorID', 'Constituent_Id__c (zero-padded to 9)'],
    ['2', 'CampaignAppealCode', '<TypePrefix><YY><CC><SegmentCode>'],
    ['3', 'Scanline', '<DonorID> <CampaignAppealCode> <CheckDigit>'],
    ['4', 'PackageCode', 'Per-segment routing (Section 11)'],
    ['5', 'Addressee', 'npo02__Formal_Greeting__c'],
    ['6', 'Salutation', 'npo02__Informal_Greeting__c'],
    ['7–8', 'FirstName, LastName', 'First_Name__c, Last_Name__c'],
    ['9–13', 'Address1, Address2, City, State, ZIP', 'BillingStreet (split), BillingCity, BillingState, BillingPostalCode'],
    ['14', 'Country', 'BillingCountry'],
    ['15–17', 'AskAmount1, AskAmount2, AskAmount3', 'Ask ladder (Section 6)'],
    ['18', 'AskAmountLabel', 'BLANK (donor fill-in line)'],
    ['19', 'ReplyCopyTier', 'Lifecycle-derived (Section 9)'],
    ['20–21', 'LastGiftAmount, LastGiftDate', 'npo02__LastOppAmount__c, npo02__LastCloseDate__c'],
    ['22–23', 'CurrentFYGiving, PriorFYGiving', 'Total_Gifts_This_Fiscal_Year__c, Total_Gifts_Last_Fiscal_Year__c'],
    ['24', 'CAVersion', 'TRUE if BillingState = CA AND campaign is CA-versioned'],
  ]);
  r += 1;

  // 13. Matchback columns
  r = _writeSectionHeader_(ws, r, '13. Output File Columns — Matchback');
  r = _writeBlock_(ws, r, [
    ['#', 'Column', 'Source'],
    ['1', 'DonorID', 'Constituent_Id__c (zero-padded to 9)'],
    ['2', 'CampaignAppealCode', '<TypePrefix><YY><CC><SegmentCode>'],
    ['3', 'Scanline', 'Same as Print (full ALM 21-char with check digit)'],
    ['4', 'InternalAppealCode', '<Program><FY><Month><Segment><Package><Test>'],
    ['5–7', 'SegmentCode, SegmentName, PackageCode', 'Waterfall + routing'],
    ['8', 'TestFlag', 'CTL / TSA / TSB'],
    ['9–10', 'Addressee, Salutation', 'Formal / Informal greeting'],
    ['11–12', 'FirstName, LastName', ''],
    ['13–18', 'Address1–Country', ''],
    ['19–22', 'AskAmount1, 2, 3, AskAmountLabel', 'Ladder; AskAmountLabel BLANK'],
    ['23', 'ReplyCopyTier', ''],
    ['24–25', 'LastGiftAmount, LastGiftDate', ''],
    ['26–27', 'CurrentFYGiving, PriorFYGiving', ''],
    ['28', 'CumulativeGiving', 'npo02__TotalOppAmount__c'],
    ['29', 'LifecycleStage', ''],
    ['30', 'CAVersion', ''],
    ['31', 'CornerstoneFlag', 'Cornerstone_Partner__c'],
    ['32', 'Email', 'General_Email__c'],
    ['33', 'SustainerFlag', 'Miracle_Partner__c'],
    ['34', 'GiftCount12Mo', 'Gifts_in_L12M__c'],
    ['35', 'RFMScore', ''],
    ['36–37', 'Holdout, ExclusionReason', ''],
    ['38', 'Account_CASESAFEID', 'Account_CASESAFEID__c (18-char SF Account Id, distinct from DonorID)'],
  ]);
  r += 1;

  // 14. Matchback row scope
  r = _writeSectionHeader_(ws, r, '14. Matchback Row Scope');
  r = _writeBlock_(ws, r, [
    ['Included rows'],
    ['Mailed donors: Holdout=FALSE AND ExclusionReason="" — match Print 1:1.'],
    ['Holdouts: Holdout=TRUE — kept for ROI measurement regardless of any exclusion flag.'],
    ['Excluded rows (go to suppression audit log file, not Matchback)'],
    ['Holdout=FALSE AND ExclusionReason="quantity_reduction" (Pass 2 budget trim).'],
    ['Holdout=FALSE AND ExclusionReason="missing_constituent_id" (data quality).'],
    ['Holdout=FALSE AND ExclusionReason="duplicate_constituent_id" (data quality).'],
    ['Why', 'Matchback supports gift attribution. Donors who weren\'t mailed and aren\'t a measurement control have no role in the join. Trim audit lives in the suppression audit log file alongside Print + Matchback.'],
  ]);

  // Format
  ws.setColumnWidth(1, 220);
  for (let c = 2; c <= 6; c++) ws.setColumnWidth(c, 280);
  ws.setFrozenRows(2);
}

function _writeButtonReferenceTab_(ss, ts) {
  const ws = _ensureTab_(ss, TAB_BUTTON_REFERENCE, 60, 5);
  ws.getRange(1, 1).setValue('HRI Segmentation Builder — Button Reference').setFontWeight('bold');
  ws.getRange(2, 1).setValue(
    'Synced from SPEC.md v' + SPEC_VERSION + '. Last refreshed: ' + ts +
    '. Do not edit directly — re-run "Refresh Reference Tabs" from the web app after any spec change.');

  const rows = [
    ['Button / Action', 'What It Does', 'When To Use', 'What Gets Written / Changed', 'Reversible?'],
    ['Refresh Universe', 'Re-fetches qualified universe from BQ cache. Applies exclusions (per OFF toggles), then waterfall + suppressions + segment assignment.', 'Campaign change, or SF data has changed since last pull.', 'Browser state.', 'Yes — re-click.'],
    ['Run Projection', 'Computes economics per segment from Historical Baseline. Writes one row per segment to Draft tab.', 'After universe loaded; want to see projection.', 'Draft tab. Overwrites prior contents.', 'Yes — re-run.'],
    ['Edit Scenario (per-segment toggles, % slider, target type)', 'Browser-side what-if. Adjusts inclusion/quantity. No new SF query.', 'Iterating to hit target.', 'UI only until Save/Approve.', 'Yes — toggle back.'],
    ['Save Scenario', 'Saves current scenario state to Draft tab.', 'Iterating across sessions or sharing with Bill.', 'Draft tab updated.', 'Partial — overwrites prior Draft.'],
    ['Approve', 'Locks Draft as final. Copies to Segment Detail tab. Triggers Generate Mailing File.', 'After sign-off.', 'Segment Detail tab + link_to_segments populated.', 'Difficult — re-run upserts on same Campaign + Segment.'],
    ['Generate Mailing File', 'Produces Print + Matchback CSVs to Drive output folder. Matchback contains mailed + holdouts only (no trim residue).', 'Auto on Approve, or independent regeneration.', 'Two timestamped CSVs in Drive.', 'Yes — new files have new timestamp.'],
    ['Load to Salesforce', 'DEFERRED. Will write Campaign + Campaign_Segment__c + CampaignMember post-merge-purge.', 'After Faircom returns merge/purge file (separate processor).', 'SF Campaign / Segment / Member / Account NCOA updates.', 'Idempotent upsert.'],
    ['Refresh Reference Tabs', 'Regenerates Logic & Math Reference and Button Reference tabs from SPEC.md §17.', 'After any spec change is shipped.', 'Both reference tabs rewritten in place.', 'Yes — re-run.'],
    ['Download Print File', 'Direct download link from Drive.', 'Preparing transmission to Faircom.', 'Browser download.', 'N/A.'],
    ['Download Matchback File', 'Direct download link from Drive.', 'Internal HRI use.', 'Browser download.', 'N/A.'],
  ];
  ws.getRange(4, 1, rows.length, 5).setValues(rows);
  ws.getRange(4, 1, 1, 5).setFontWeight('bold');
  ws.setColumnWidths(1, 1, 240);
  for (let c = 2; c <= 5; c++) ws.setColumnWidth(c, 320);
  ws.setFrozenRows(4);
}
