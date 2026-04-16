# HRI Segmentation Builder — Full Specification

**Hope Rises International**
**Version 3.2 — April 13, 2026**
**FIS Component: Segmentation Engine + Segment Data Loader + Campaign Intelligence Workbook**

---

### Revision History

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | April 10, 2026 | Initial full spec with MIC integration, three-pass projection, appeal code architecture |
| 3.0 | April 10, 2026 | External review triage (ChatGPT + Gemini). Appeal code diagram fix. Intra-segment tie-breaker. Ask rounding direction. Run idempotency model. Draft-tab review pattern. Suppression audit log → Google Drive. Suppression rules as toggles. ZIP validation revised. Retention policy. Sheets-as-database migration flag. |
| 3.1 | April 10, 2026 | Segment group toggle panel (systematic include/exclude for all waterfall positions). Cornerstone flag read-only (scoring model deferred — flag maintained externally via triage query). Waterfall positions toggle-gated. |
| 3.2 | April 13–14, 2026 | Object model correction: Contact → Account throughout. "Source code" → "appeal code" terminology. Scanline architecture: 9-digit donor ID + 9-char appeal code, confirmed against physical mail piece. Two-file output: Printer File (agency, 9-char) and Internal Matchback File (HRI, 15-char). GCS → Google Drive. Three-tier donor-level suppression system (Bekah reviewed). Cornerstone field → `Cornerstone_Partner__c`. MIC confirmed live. Baseline campaign selector for historical performance projection (Section 7.2). Pipeline write recovery with sequential write order, per-target success/fail flags, and retry logic (Section 3). Phase 1 reframed as explicit diagnostic gate for open items 1–3. Single-operator constraint documented. `campaign_type` field added to MIC. Six open items resolved (#5, #6, #8, #11, #12, #13). |

---

## 1. Problem Definition

Hope Rises International's direct mail segmentation logic has been owned and executed by its agency (TLC/Lukens) for the entire history of the program. With TLC's contract terminating April 30, 2026 and VeraData assuming production starting in May, HRI must own the segmentation engine internally — the logic that determines who gets mailed, in which segment, with which appeal code, and in what output format. Without this, HRI cannot direct any agency to execute a mailing.

The Segmentation Builder is one of three systems in a closed-loop campaign intelligence cycle:

1. **MIC Campaign Calendar** (plan) — the master campaign record with budget quantities, costs, projected revenue, and actuals columns
2. **Segmentation Builder** (execute) — reads campaign targets from the MIC, runs the segment pull, writes segment detail back to the MIC
3. **Campaign Scorecard** (measure) — deployed at v26, fills the MIC actuals columns with performance data from Salesforce

The Segmentation Builder sits between planning and measurement. It pulls donor data from Salesforce, applies configurable segment rules, assigns every eligible donor to exactly one segment via waterfall logic, generates per-record ask strings, appends unique appeal codes, fits the universe to the campaign's budget target, and produces two output files — one for the agency (Printer File) and one for HRI's matchback. It also loads segment assignment data back into Salesforce for closed-loop tracking.

### Users

| Name | Role | Interaction |
|------|------|-------------|
| Bill Simmons | CEO / system architect | Configures default segment rules in MIC Segment Rules tab, reviews projections, approves mailing plans |
| Jessica Allen | Campaigns / direct response | Primary operator — selects campaign from MIC, runs pulls, reviews projections, approves segment inclusion, downloads output, transmits to agency, enters cost actuals post-mailing |
| Bekah Schwanbeck | Salesforce / data ops | Validates source data, troubleshoots field-level issues |
| VeraData (agency) | Production / creative | Receives Printer File, executes mailing, returns post-mailing quantities |

### Scope Boundary

**In scope (HRI builds):**
- MIC integration (reads campaign targets, writes segment detail and actuals)
- Salesforce data extraction (Account + Opportunity + Campaign fields)
- RFM computation (recency, frequency, monetary value per donor)
- Segment assignment engine (waterfall logic, configurable rules)
- Suppression engine (economic cutoffs, behavioral flags, donor-level exclusions)
- Budget-target fitting (three-pass projection with expansion levers)
- Ask string computation (per-segment basis, configurable multipliers and floors/ceilings)
- Reply copy tier classification
- Appeal code generation (unique per segment × campaign × package × test flag)
- Cornerstone flag integration (reads externally maintained flag; runtime scoring model deferred)
- Mailing universe projection (per-segment pro forma with break-even analysis)
- Output file generation (CSV for agency/lettershop handoff)
- Segment data load back to Salesforce (Campaign_Segment__c records)

**Out of scope (vendor services — VeraData/Wiland):**
- Predictive modeling
- Post-merge optimization (PMO)
- Optimal ask amount series (OAS)
- Merge/purge (NCOA, dedup, CASS)
- Digital co-targeting
- Print production
- Creative strategy

**Out of scope (other FIS tools):**
- Campaign Scorecard (deployed, consumes segment data, writes actuals back to MIC)
- Donor File Health Dashboard (deployed, provides lifecycle metrics)
- Direct Mail Template Engine (separate track)
- Major Gift App (deployed, separate tool)

---

## 2. Architecture & Data Flow

### 2.1 System Type

Cloud Run service + Apps Script web app (hybrid). Same pattern as Campaign Scorecard and Donor File Health Dashboard.

- **Cloud Run service**: Executes the heavy processing — Salesforce data pull (all ~50K accounts with opportunity-derived fields in one pass), RFM computation, segment assignment, suppression, appeal code generation, output file creation. Triggered via Apps Script web app.
- **Apps Script web app**: User interface for Jessica and Bill. Campaign selection from MIC, segment rule overrides, projection trigger, approve/generate workflow, output file download, segment data load trigger. Lives in the Internal Tools Portal. The UI is lightweight — heavy review happens in the Draft tab in Sheets.
- **MIC Google Sheet (Campaign Intelligence Workbook)**: Master campaign record. Five tabs — Campaign Calendar, Draft, Segment Detail, Budget Summary, Segment Rules. The Segmentation Builder reads from and writes to this sheet.
- **Google Drive folder**: Output files (CSV) and suppression audit logs archived with campaign ID and timestamp. Bill to provide designated folder ID.

### 2.2 The Campaign Intelligence Workbook (MIC)

The existing MIC Google Sheet becomes the Campaign Intelligence Workbook — the single source of truth where planning, segmentation, and measurement converge. Five tabs:

| Tab | Purpose | Written By | Read By |
|-----|---------|-----------|---------|
| **Campaign Calendar** | One row per campaign touch. Budget plan (qty, cost, projected revenue) + actuals (actual qty, cost, revenue, response rate, avg gift, etc.). This is the existing flattened MIC data. | Jessica (budget plan), Campaign Scorecard pipeline (revenue/response actuals), Jessica (cost actuals post-mailing) | Segmentation Builder (reads budget target), Campaign Scorecard (reads campaign metadata), Budget Summary (formula references) |
| **Draft** | Working projection for the active campaign pull. One row per segment. Auto-populated by the Segmentation Builder when a projection runs. Jessica reviews segment quantities, economics, inclusion/exclusion, and expansion levers here. Overwritten on each new projection run. Only one campaign's draft is active at a time. | Segmentation Builder (auto-populated) | Jessica (review and approve) |
| **Segment Detail** | Finalized segment records. One row per segment per approved campaign. Written when Jessica approves a projection — the Draft tab contents are copied to Segment Detail as permanent record. | Segmentation Builder (on approval) | Campaign Scorecard (reads segment-level plan for variance analysis), Campaign Calendar (link_to_segments column references) |
| **Budget Summary** | FY-level rollup by lane and channel. Budget vs. actual with variance. All formula-driven from Campaign Calendar. | Formulas (no manual entry) | Bill (review) |
| **Segment Rules** | Default segment definitions, waterfall hierarchy, suppression parameters (with toggle flags), appeal code registry, ask string defaults. | Bill (configures), Jessica (reviews) | Segmentation Builder (reads as configuration source at runtime) |

> **Future architecture consideration:** The MIC Google Sheet functions as a lightweight database for campaign planning, segment detail, and rules configuration. At current HRI scale (~20 campaigns/year, single operator), this is adequate. If scale or concurrency requirements change, evaluate migration to Cloud SQL or BigQuery with the Sheet as a connected view layer. This migration can be executed independently of the Segmentation Builder — the interface contract (read campaign targets, write segment detail) remains the same regardless of backend. Flag for discussion after first full year of operation.

### 2.3 Data Flow

```
MIC Campaign Calendar (Plan)
  │
  │  budget_qty_mailed → Target Quantity
  │  budget_cost → CPP computation
  │  campaign_name + appeal_code → Campaign identity
  │  mail_date → Recency calculations
  │
  ▼
Segmentation Builder (Cloud Run)
  │
  ├── READ: Salesforce (Account + Opportunity)
  │         Pull all ~50K accounts with opportunity-derived fields
  │         Compute RFM, lifecycle, HPC/MRC, flags
  │
  ├── READ: MIC Segment Rules tab
  │         Waterfall hierarchy, thresholds, suppression params + toggle states
  │
  ├── PROCESS: Waterfall assignment → suppression → ask strings
  │            → appeal codes → budget-target fitting
  │
  ├── WRITE: MIC Draft tab
  │          Per-segment projection (qty, response rate, revenue, break-even)
  │          Jessica reviews in Sheets → approves → copies to Segment Detail
  │
  ├── WRITE: Output CSV → Google Drive
  │          Two files per campaign:
  │          (a) Printer File — donor ID, 9-char appeal code, scanline,
  │              address, ask strings, reply copy tier. Goes to VeraData.
  │          (b) Internal Matchback File — everything in Printer File
  │              PLUS 15-char internal appeal code, segment detail,
  │              RFM scores, lifecycle, flags. Stays on HRI's side.
  │
  ├── WRITE: Suppression audit log CSV → Google Drive
  │          Donor-level suppression events archived per run
  │
  └── WRITE: Salesforce Campaign_Segment__c records (upsert)
             Closed-loop tracking for Campaign Scorecard
                │
                ▼
Campaign Scorecard Pipeline (existing, deployed)
  │
  ├── READ: Salesforce Campaign_Segment__c + Opportunities
  │
  └── WRITE: MIC Campaign Calendar (actuals columns)
             actual_qty_mailed, actual_revenue, gifts,
             new_donors, response_rate, avg_gift, roi
             (cost actuals entered manually by Jessica)
```

### 2.4 Repository

`hri-segmentation-builder` in the Hope-Rises-International GitHub org. CLAUDE.md seeded from template repo.

### 2.5 GCP Project

`gmail-agent-489116` (primary project). Service account: `hri-automation@gmail-agent-489116.iam.gserviceaccount.com`.

**Retention policy:** Google Drive-archived output files and suppression audit logs are retained for 3 fiscal years, then purged. Files can be regenerated from Salesforce source data if needed beyond the retention window.

### 2.6 Resolves Campaign Scorecard Phase 3 Open Item

The Dashboard Input Tab spec item from Campaign Scorecard Phase 3 is resolved by this architecture. Cost data entry lives in the MIC Campaign Calendar tab's actuals columns, not in a separate Scorecard interface. Revenue, response rate, and other performance metrics flow automatically from Salesforce via the Scorecard pipeline. Jessica enters cost actuals (actual_cost) in the MIC after receiving agency invoices — the only manual actuals entry required.

---

## 3. End-State UI — How Jessica Runs a Segment Pull

The Segmentation Builder lives in the Internal Tools Portal. Jessica clicks "Segmentation Builder" from the portal menu.

### Step 1: Select Campaign

Landing screen shows a list of campaigns pulled from the MIC Campaign Calendar tab, filtered to DM-eligible rows (channel = "Direct Mail Appeals", lane = "Housefile" or "Mid-Level"). Each row shows:

- Campaign name, month, appeal code
- Budget target quantity
- Budget cost
- Status: **Draft** (no pull run), **Projected** (projection generated), **Approved** (plan locked), **Pulled** (output file generated), **Mailed** (actuals entered)

Jessica clicks a campaign row to open it. The system pre-populates from the MIC:
- Target quantity from `budget_qty_mailed`
- Budget from `budget_cost`
- CPP computed as `budget_cost ÷ budget_qty_mailed`
- Campaign type inferred from `campaign_name` (standard appeal, match, catalog)
- Whether it's a 33x Shipping match (triggers CA versioning) — configurable toggle
- **Baseline campaign(s)** for projection — see below

**Baseline Campaign Selector:** Jessica selects one or more prior campaign appeal codes as the performance baseline for this campaign's projection. Example: "Use Easter 2025 to project Easter 2026." The builder pulls segment-level actuals (response rate, average gift, cost per piece) from the selected campaigns via Campaign_Segment__c records and applies them as the historical rates in the Draft tab.

Fallback hierarchy:
1. **Jessica selects specific baseline campaign(s)** → builder uses those actuals per segment code
2. **No baseline selected, but segment codes have prior data** → builder computes rolling average across all prior campaigns with matching segment codes from Campaign_Segment__c
3. **No historical data exists for a segment code** → builder shows blank cells in the Draft tab; Jessica enters manual estimates

The baseline field in the MIC Campaign Calendar stores the selected appeal code(s) as a comma-separated list: `baseline_appeal_codes`. Empty = auto-lookup (fallback #2).

### Step 2: Configure Segment Groups + Rules

The campaign opens to a **segment group toggle panel** showing which waterfall positions are active for this mailing. Every major waterfall position has an include/exclude switch. This is how Jessica composes a mailing from any combination of segment groups:

| Group | Default | What "OFF" means |
|---|---|---|
| Major Gift Portfolio | Include (custom package) | Donors skip position #2, fall through to their natural RFM position |
| Mid-Level | Include | Donors skip position #3, fall through as active housefile |
| Sustainers | Exclude | Toggle ON to include (year-end/emergency) |
| Cornerstone | Include | Donors skip position #5, flag ignored, score normally in waterfall |
| New Donor | Exclude (welcome window) | Toggle ON to include |
| Active Housefile | Include | Toggle OFF for mid-level-only or cornerstone-only mailings |
| Mid-Level Prospect | Include | Toggle OFF to narrow |
| Lapsed | Include | Toggle OFF to narrow |
| Deep Lapsed | Include (with break-even gate) | Toggle OFF to narrow |

Common configurations:
- **Standard housefile appeal**: Active + Lapsed + Deep Lapsed ON. Mid-Level ON (different panel via PackageCode). Cornerstone ON (different panel via PackageCode). Major Gift ON (custom package). Sustainers OFF. New Donor OFF.
- **Mid-level only mailing**: Mid-Level ON. Everything else OFF.
- **Cornerstone-only reactivation**: Cornerstone ON. Everything else OFF.
- **Year-end kitchen sink**: Everything ON including Sustainers and New Donor.

When a position is toggled OFF, the waterfall skips it. Donors that would have been caught at a skipped position fall through to the next active position. If a donor's only qualifying position is OFF, they are not in the mailing.

Below the toggle panel, a **segment rules panel** shows configurable parameters pulled from the MIC Segment Rules tab. Defaults are pre-populated — Jessica only adjusts what's different about this specific campaign:

- **Recency boundaries** (default: 0–6, 7–12, 13–24, 25–36, 37–48)
- **Recent-gift suppression window** (default: 45 days — may shorten to 30 for year-end) — toggle: ON/OFF
- **Deep lapsed cutoff** (default: 48 months — may narrow to 36)
- **Ask string multipliers** (default: 1×, 1.5×, 2×)
- **Ask floor/ceiling** (default: $15 / $4,999.99)
- **Response rate floor** (default: 0.8%)
- **Frequency cap** (default: 6 solicitations/year) — toggle: ON/OFF
- **Holdout percentage** (default: 5% of suppressed segments) — toggle: ON/OFF

Each parameter shows the default with an edit control. Most campaigns require no changes.

### Step 3: Run Projection

Jessica clicks "Run Projection." The system executes the three-pass projection:

**Pass 1 — Full Universe.** The engine queries Salesforce (all ~50K accounts with opportunity-derived fields in one pass), applies the waterfall with all rules, and computes the complete qualified universe with no quantity cap. Results write to the **Draft tab** in the MIC. Jessica reviews the projection in Sheets — segment quantities, economics, break-even flags, and inclusion/exclusion status are all visible in the familiar spreadsheet interface. The Draft tab shows one row per segment:

| Segment Code | Segment Name | Quantity | Hist. Response Rate | Hist. Avg Gift | Proj. Gross Revenue | CPP | Total Cost | Proj. Net Revenue | Break-Even Rate | Margin | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AH01 | Active 0–6mo $50+ | 3,200 | 8.2% | $72 | $18,893 | $0.48 | $1,536 | $17,357 | 0.67% | +7.53% | ✅ Include |
| AH02 | Active 0–6mo $25–49 | 5,100 | 6.1% | $38 | $11,818 | $0.48 | $2,448 | $9,370 | 1.26% | +4.84% | ✅ Include |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |
| DL03 | Deep Lapsed 37–48mo $100+ | 1,200 | 1.4% | $45 | $756 | $0.48 | $576 | $180 | 1.07% | +0.33% | ⚠️ Marginal |
| DL04 | Deep Lapsed 37–48mo <$100 | 800 | 0.9% | $32 | $230 | $0.48 | $384 | ($154) | 1.50% | -0.60% | ❌ Below BE |

**Running total** displayed prominently: "Full Universe: 38,200 | Target: 35,000"

**Suppression summary** row at bottom of Draft tab: aggregate counts by rule (e.g., "Recent Gift: 1,200 | Frequency Cap: 340 | Below BE: 2,100 | Holdout: 180").

**Pass 2 — Fit to Target.** If universe exceeds target, the system trims from the bottom of the waterfall up. Deep Lapsed with weakest economics cut first, then marginal Lapsed Recent sub-segments, working up the hierarchy until total ≤ target. The Draft tab shows two columns — "Full Universe" and "Budget Fit" — so Jessica can see what was cut and why. Trimmed segments appear greyed with "Below budget line" tag.

When the budget line cuts through a segment (e.g., budget requires removing 500 records from a segment of 1,200), the system applies an intra-segment tie-breaker: sort descending by composite RFM score, then by MRC descending, then by recency ascending. Records at the bottom of the sorted list are trimmed first. This ensures the strongest records within any segment are retained when partial trimming is required.

"Budget Fit: 35,100 | Target: 35,000 | Trimmed: 3,100 from Deep Lapsed + marginal Lapsed Recent"

**Pass 3 — Expansion Options (only when universe < target).** If the full universe is 33,000 against a 35,000 target, the system shows a gap and presents expansion levers. Expansion levers appear as additional rows in the Draft tab with an "Include?" column Jessica can toggle. Running totals update via formulas.

"Qualified Universe: 33,000 | Target: 35,000 | Gap: 2,000"

Each lever row shows:

| Lever | Records Added | Est. Response Rate | Est. Net Revenue | Break-Even? | Include? |
|---|---|---|---|---|---|
| Extend deep lapsed to 42 months ($75+ cum) | +1,200 | 1.8% | +$396 | ✅ Yes | ☐ |
| Reduce recent-gift window to 30 days | +400 | 5.5% | +$720 | ✅ Yes | ☐ |
| Drop response rate floor to 0.6% | +600 | 0.7% | ($48) | ❌ No | ☐ |
| Include new donors in welcome window | +350 | 4.2% | +$340 | ✅ Yes | ☐ |
| Include Cornerstone partners | +2,800 | 1.1% | +$150 | ⚠️ Marginal | ☐ |

Jessica toggles levers in the Draft tab. She may accept the gap if no lever is economically justified — the system shows: "Mailing 33,000 at projected $X net revenue vs. mailing 35,000 at projected $Y net revenue."

### Step 4: Approve Mailing Plan

Jessica clicks "Approve Plan" in the Apps Script UI. This:
- Copies the Draft tab contents to the Segment Detail tab as the permanent record for this campaign
- Locks the segment rules for this campaign
- Updates the campaign status to "Approved"
- Sends Bill an email notification with a link to the projection summary (configurable — can be disabled if Jessica self-approves)
- Clears the Draft tab for the next campaign

### Step 5: Generate Output File

Jessica clicks "Generate Mailing File." The engine runs the full pipeline — segment assignment, ask string computation, appeal code generation, scanline computation, reply copy tier assignment — and produces two CSVs (Printer File for VeraData, Internal Matchback File for HRI).

Summary screen shows:
- Total records by segment
- Total unique appeal codes generated (9-char for printer, 15-char for matchback)
- Warnings (missing addresses, donors who fell through waterfall, etc.)
- Download links: Printer File (for transmission to VeraData) and Internal Matchback File (retained in Google Drive)
- Confirmation that both files and the suppression audit log were archived to Google Drive

The `link_to_segments` column in the MIC Campaign Calendar tab auto-populates with a link to the Segment Detail rows for this campaign.

Jessica downloads the CSV and transmits to VeraData.

### Step 6: Post-Mailing Update

After mailing execution, Jessica returns to the campaign and:
1. Enters the **actual mail date** and **actual cost** (from agency invoice) in the MIC Campaign Calendar tab
2. Enters **actual quantities mailed** by segment if they differ from projected (post-merge/purge adjustments from lettershop)
3. Clicks "Load to Salesforce" — the system writes Campaign_Segment__c records to Salesforce
4. The Campaign Scorecard picks up these records on its next refresh and fills the remaining actuals columns (actual_revenue, gifts, new_donors, response_rate, avg_gift, roi, net_revenue)

**For a standard campaign where nothing unusual is happening, Steps 1–5 take approximately 15 minutes.** The system does the computation; Jessica reviews the economics in the Draft tab and confirms before committing.

### Run Idempotency

Each projection run is stamped with a run timestamp (ISO 8601). Campaign status transitions are one-directional: **Draft → Projected → Approved → Pulled → Mailed.** Re-running a projection from Draft or Projected status overwrites the prior projection for that campaign (keyed on campaign ID + run timestamp). Re-running from Approved status requires the operator to explicitly unlock the campaign back to Projected, which clears the prior approval and the Draft tab. The system prevents generating a new output file while a prior file for the same campaign is in Pulled status without explicit override.

MIC Segment Detail rows are keyed on campaign ID + segment code. Approving a projection replaces prior Segment Detail rows for that campaign, never appends duplicates.

### Pipeline Write Recovery

The Segmentation Builder writes to three targets in a single pipeline run: Google Drive (output files and suppression log), MIC Google Sheet (Draft tab or Segment Detail tab), and Salesforce (Campaign_Segment__c records). A failure at any target must not corrupt the other two or advance the campaign status.

**Write order (sequential, not parallel):**

1. **Google Drive first.** Output CSVs (Printer File, Internal Matchback File, suppression audit log) are written as timestamped files. This is the lowest-risk target — files are atomic (they either write completely or don't exist), and a duplicate write just creates a second timestamped file with no corruption risk.

2. **MIC Google Sheet second.** Draft tab or Segment Detail tab writes are keyed on campaign ID + segment code. Sheets API writes are row-level upserts — a retry overwrites the same rows, never appends duplicates. If the Sheets write fails after Drive succeeds, the output files exist but the MIC doesn't reflect them. The campaign status does not advance.

3. **Salesforce last.** Campaign_Segment__c records are upserted on `Campaign__c` + `Segment_Name__c`. This is the highest-risk target (network latency, API rate limits, token expiration). If the Salesforce write fails after Drive and Sheets succeed, the output files and MIC are consistent but the Scorecard won't see the segment data. The campaign status does not advance past "Approved."

**Failure handling rules:**

Each write target gets a success/fail flag logged to a `run_status` object stored on the campaign's status row in the MIC. The status object records: `drive_write: success|fail`, `sheets_write: success|fail`, `salesforce_write: success|fail`, `timestamp`, `error_message`.

If any write fails, the campaign status does NOT advance. The UI displays: "Pipeline incomplete — [target] write failed. [Error message]. Click Retry to re-execute failed writes only."

The **Retry** action re-executes only the failed target(s), not the entire pipeline. Because all three targets use upsert-keyed writes, retrying is safe — no duplicate records, no partial overwrites. The retry reads from the same in-memory pipeline result that produced the successful writes, ensuring consistency across targets.

If the retry fails a second time, the UI displays: "Retry failed. Contact Bill." The system logs the full error to Google Drive as a diagnostic file alongside the campaign's output files.

**Projection runs vs. output generation:** The write recovery logic applies to the output generation step (Step 5 — "Generate Mailing File") and the Salesforce load step (Step 6 — "Load to Salesforce"). Projection runs (Step 3) only write to the Draft tab — a single target with no cross-target consistency risk. A failed projection write simply means the Draft tab didn't populate; Jessica re-runs.

**Single-operator constraint:** The MIC supports only one active projection at a time. The Draft tab is a working scratchpad for one campaign. This is an intentional architectural constraint at HRI's current scale (~20 campaigns/year, single operator). If Jessica starts a projection for Campaign B while Campaign A is in Projected status, the system warns: "Campaign A projection will be overwritten. Continue?" This prevents silent data loss but does not prevent deliberate overwrite.

---

## 4. Salesforce Data Model

### 4.1 Source Fields — Account Object

The Segmentation Builder queries Account (Household Account model), not Contact. All donor-level rollup fields, address, and suppression flags live on Account. Opportunity detail is queried separately and joined via `AccountId`.

| Field API Name | Purpose | Notes |
|---------------|---------|-------|
| `Id` | Account ID | Primary key for join operations |
| `Constituent_Id__c` | Donor ID | External ID, Text(255). Used for scanline (9-digit zero-padded). |
| `Name` | Account Name | Household name |
| `First_Name__c` | First Name | Formula (Text) — derived from primary contact |
| `Last_Name__c` | Last Name | Formula (Text) — derived from primary contact |
| `Special_Salutation__c` | Salutation | Text(255) — for personalization |
| `npo02__Formal_Greeting__c` | Formal Greeting | Text Area(255) |
| `npo02__Informal_Greeting__c` | Informal Greeting | Text Area(255) |
| `BillingStreet`, `BillingCity`, `BillingState`, `BillingPostalCode`, `BillingCountry` | Address | ZIP as text (preserve leading zeros) |
| `General_Email__c` | Email | 80% empty — future multi-channel |
| `npo02__LastCloseDate__c` | Recency | Most recent gift date |
| `npo02__FirstCloseDate__c` | Inception date | Lifecycle classification |
| `npo02__TotalOppAmount__c` | Cumulative giving | Currency(14,2) — lifetime total |
| `npo02__NumberOfClosedOpps__c` | Frequency | Number(18,0) — lifetime gift count |
| `npo02__LargestAmount__c` | Largest gift | Currency(14,2) |
| `npo02__AverageAmount__c` | Average gift | Currency(14,2) |
| `npo02__LastOppAmount__c` | Last gift amount | Currency(14,2) |
| `Days_Since_Last_Gift__c` | Days since last gift | Formula (Number) |
| `First_Gift_Age_Days__c` | Days since first gift | Formula (Number) |
| `Cornerstone_Partner__c` | Cornerstone flag | Checkbox (Account) |
| `Gifts_in_L12M__c` | Gifts in last 12 months | Number(18,0) |
| `Cume_in_L12M__c` | Cumulative giving last 12 months | Currency(16,2) |
| `Total_Gifts_Last_365_Days__c` | Total giving last 365 days | Currency(16,2) |
| `Total_GIfts_13_24_Months_Ago__c` | Total giving 13–24 months ago | Currency(16,2) |
| `Total_Gifts_This_Fiscal_Year__c` | Current FY giving | Currency(16,2) |
| `Total_Gifts_Last_Fiscal_Year__c` | Prior FY giving | Currency(16,2) |
| `npsp__Sustainer__c` | Sustainer status | Picklist |
| `Lifecycle_Stage__c` | Lifecycle stage | Formula (Text) |

**Donor-level suppression fields (see Suppression Toggles v2 document for full three-tier system — Bekah reviewed April 13):**

| Tier | Field API Name | Label |
|------|---------------|-------|
| 1 (Hard) | `npsp__All_Members_Deceased__c` | All Household Members Deceased |
| 1 (Hard) | `Do_Not_Contact__c` | Do Not Contact At All |
| 1 (Hard) | `No_Mail_Code__c` | No Mail Code |
| 1 (Hard) | `npsp__Undeliverable_Address__c` | Undeliverable Billing Address |
| 1 (Hard) | `NCOA_Deceased_Processing__c` | NCOA Deceased Processing |
| 1 (Hard) | Blank address check (computed) | BillingStreet/City/PostalCode null or empty |
| 2 (Pref) | `Newsletter_and_Prospectus_Only__c` | Newsletter and Prospectus Only (conditional: include in newsletter campaigns) |
| 2 (Pref) | `Newsletters_Only__c` | Newsletters Only (conditional: include in newsletter campaigns) |
| 2 (Pref) | `No_Name_Sharing__c` | No Name Sharing |
| 2 (Pref) | `Match_Only__c` | Match Only (include in match campaigns only) |
| 2 (Pref) | `X1_Mailing_Xmas_Catalog__c` | 1 Mailing Xmas Catalog (annual frequency cap) |
| 2 (Pref) | `X2_Mailings_Xmas_Appeal__c` | 2 Mailings Xmas/Easter (annual frequency cap) |
| 3 (Rare) | See Suppression Toggles v2 document | 14 fields, toggle defaults OFF |

**Fields requiring Opportunity-level query:**

| Derived Field | Computation | Source |
|--------------|-------------|--------|
| HPC (Highest Previous Contribution) | MAX(Amount) WHERE IsWon = true | Opportunity |
| MRC (Most Recent Contribution) | Amount from most recent Opportunity WHERE IsWon = true | Opportunity |
| Months Since Last Gift | DATEDIFF(months, LastCloseDate, mail_date) | Computed against campaign mail date |
| Gifts in Last 12 Months | COUNT WHERE CloseDate >= mail_date - 365 | Opportunity |
| Gifts in Last 24 Months | COUNT WHERE CloseDate >= mail_date - 730 | Opportunity |
| Has DM Gift $500+ | EXISTS WHERE Amount >= 500 AND IsWon = true AND DM channel | Opportunity |
| Sustainer Flag | Derived from npe03 recurring donation fields | NPSP Recurring |
| Average Gift (5-year) | AVG(Amount) WHERE CloseDate >= mail_date - 1825 AND IsWon = true | Opportunity |
| CBNC Flag | 2+ lifetime gifts in non-consecutive fiscal years over 10-year window | Opportunity |

### 4.2 Query Strategy

Two-pass approach:

**Pass 1:** SOQL on Account with `npo02__NumberOfClosedOpps__c > 0`. Returns rollup fields. Expected volume: ~50K accounts.

**Pass 2:** Opportunity detail for all Pass 1 Accounts. Computes HPC, MRC, average gift, channel history, $500+ DM flag, CBNC flag. Batched in 200-record chunks.

This volume is well within Cloud Run's execution window. The Donor File Health pipeline already processes 500K+ transactions over 4+ FYs using the same REST/batch pattern. If Phase 1 diagnostic shows unexpected volume or rate limit issues, Bulk API 2.0 is the fallback — but is not expected to be necessary at HRI's scale.

### 4.3 Write-Back: Campaign_Segment__c

After mailing execution, segment assignments load to Salesforce:

| Field | Value |
|-------|-------|
| Campaign__c | Parent Campaign ID (matched via appeal_code from MIC) |
| Segment_Name__c | Segment label |
| Source_Code__c | Generated appeal code (label rename to "Segment Code" queued) |
| Quantity_Mailed__c | Count of records in segment |
| Mail_Date__c | Actual mail date |

Salesforce load uses **upsert** keyed on `Campaign__c` + `Segment_Name__c`. Re-loading segment data for a campaign that has already been loaded overwrites the prior records — no duplicate Campaign_Segment__c records are created.

---

## 5. Segment Rules Engine

### 5.1 Design Principle

All segment rules are **configurable parameters stored in the MIC Segment Rules tab**. The engine reads rules at runtime. Per-campaign overrides are applied through the UI and stored on the Segment Detail tab for auditability.

### 5.2 Primary Segmentation Axes

Two co-primary axes: **lifecycle stage** (drives messaging/creative) and **RFM** (drives mailing depth/economics).

**Lifecycle stages:**

| Stage | Definition |
|-------|-----------|
| New Donor | First gift within last 90 days |
| 2nd Year | First gift 12–24 months ago, gave again in last 12 months |
| Multi-Year | 3+ years of giving, gave in last 12 months |
| Reactivated | Had 13+ month gap, then gave in last 12 months |
| Lapsed (LYBUNT) | Last gift 13–24 months ago |
| Deep Lapsed (SYBUNT) | Last gift 25–48 months ago |
| Expired | Last gift 49+ months ago |

### 5.3 RFM Buckets (Configurable Defaults)

**Recency** (12-month active boundary — research-driven change from TLC's 24-month):

| Bucket | Range |
|--------|-------|
| R1 | 0–6 months |
| R2 | 7–12 months |
| R3 | 13–24 months |
| R4 | 25–36 months |
| R5 | 37–48 months |

**Frequency** (rolling 5-year window):

| Bucket | Range |
|--------|-------|
| F1 | 5+ gifts |
| F2 | 3–4 gifts |
| F3 | 2 gifts |
| F4 | 1 gift |

**Monetary** (average gift over 5-year lookback — research finding: average gift predicts response better than HPC):

| Bucket | Range |
|--------|-------|
| M1 | $100+ |
| M2 | $50–$99.99 |
| M3 | $25–$49.99 |
| M4 | $10–$24.99 |
| M5 | Under $10 |

**RFM weighting for DM:** R×3, F×2, M×1 (configurable per campaign type).

### 5.4 Giving Tier Segments

| Segment | Criteria | Package Treatment |
|---------|----------|-------------------|
| **Mid-Level** | Cumulative $1,000–$4,999.99, gave in 24 months, no single DM gift $500+ | High-touch: better paper, first-class postage, invitation envelope |
| **Mid-Level Prospect** | Cumulative $500–$999.99, gave in 24 months | Standard package with upgrade messaging |
| **Active Housefile** | Gave in last 12 months, $10–$499.99 cumulative | Standard DM package |
| **Lapsed Housefile** | Last gift 13–24 months, 2+ lifetime gifts, $10+ cumulative | Standard DM with lapsed messaging |
| **Deep Lapsed** | Last gift 25–48 months, $10+ cumulative | Selective — only when break-even supports it |
| **Cornerstone** | `Cornerstone_Partner__c = true` (flag maintained externally) | Legacy ALM branding package, distinct PackageCode |
| **Sustainer (Miracle Partners)** | Active monthly recurring | Suppressed from general; year-end + emergency override |

**Research-driven changes from TLC baseline:**

| Decision | TLC Baseline | HRI Decision | Rationale |
|----------|-------------|--------------|-----------|
| Active/lapsed boundary | 24 months | **12 months** | 13–24mo responds at lapsed rates. Earlier intervention. |
| Mid-level entry | $500 cumulative | **$1,000 cumulative** | $500–$999 is "Mid-Level Prospect." Reserves expensive package for demonstrated capacity. |
| Deep lapsed cutoff | 36 months hard stop | **48 months** with break-even gating | Research shows profitability 3–5 years deep for $100+ donors. |
| RFM monetary basis | Likely HPC | **Average gift** for RFM scoring | Better response predictor. HPC/MRC still used for ask strings. |
| Appeal codes | Shared across segments | **Unique** per segment × package × test | Fixes tracking gap. |
| Major donor DM | Not documented | **Custom package** (configurable) | Research: full suppression loses revenue. |
| CBNC detection | Not present | **Implemented** | Prevents suppressing reliable irregular donors. |
| Cornerstone normalization | Min-max | **Deferred** — flag maintained externally via triage query | Runtime scoring model deferred pending triage results. |
| Suppression measurement | No holdout | **5% holdout** from suppressed segments | Validates suppression improves ROI vs. just shrinking file. |

### 5.5 Cornerstone Partners

The Cornerstone flag (`Cornerstone_Partner__c`) identifies a curated population of high-value, long-tenure donors worth persistent reactivation pursuit. The flag is maintained externally via a triage query (see `cornerstone-partners-flag-logic.md`) — the Segmentation Builder reads the flag as-is and does not score or filter the population at runtime.

**Triage criteria (applied externally, not by the builder):**
- Days since first gift > 2,000
- Cumulative giving $500+ OR 5+ lifetime gifts
- Not deceased, do not mail, or single-gift donor

The Segmentation Builder treats Cornerstone as a binary toggle at waterfall position #5. Toggle ON: all flagged donors assigned to CS01 with a distinct PackageCode. Toggle OFF: flag ignored, donors fall through to their natural waterfall position.

> **Deferred: runtime scoring model.** The v2 spec included a scoring model (recency 50%, cumulative giving 30%, frequency 20%, percentile-rank normalization, quartile assignment) to filter the flagged population at runtime. This is deferred pending Cornerstone triage query results. If the triage reduces the population to a directly mailable size (~2,500–4,000), runtime scoring is unnecessary. If the post-triage population is still too large, the scoring model can be re-added as a Phase 7 enhancement.

---

## 6. Waterfall Assignment Logic

### 6.1 Priority Hierarchy

```
1. GLOBAL SUPPRESSION (removed entirely — Tier 1 defaults always ON, see Suppression Toggles v2 doc)
   ├── All Household Members Deceased (npsp__All_Members_Deceased__c)
   ├── Do Not Contact At All (Do_Not_Contact__c)
   ├── No Mail Code (No_Mail_Code__c)
   ├── Undeliverable Billing Address (npsp__Undeliverable_Address__c)
   ├── NCOA Deceased Processing (NCOA_Deceased_Processing__c)
   ├── Blank address (BillingStreet/City/PostalCode null or empty — per Bekah, this is how TLC suppressed)
   ├── International (unless campaign includes)
   └── Tier 2 communication preference flags (default ON, toggleable per campaign — see Suppression Toggles v2 doc)

2. MAJOR GIFT PORTFOLIO [TOGGLE-GATED]
   └── Assigned to gift officer in Salesforce
       Default ON: custom package (no ask amounts, handwritten note)
       Toggle OFF: donors skip this position, fall through to natural RFM position

3. MID-LEVEL ($1,000–$4,999.99 cumulative, active 24 months) [TOGGLE-GATED]

4. MONTHLY SUSTAINERS (Miracle Partners) [TOGGLE-GATED]
   └── Default OFF (suppressed from general appeals)
       Toggle ON: include in year-end + emergency

5. CORNERSTONE PARTNERS (Cornerstone_Partner__c = true) [TOGGLE-GATED]
   └── Flag is the curated population — no runtime scoring
       Flag maintained externally via Cornerstone triage query (see cornerstone-partners-flag-logic.md)
       Default ON: all flagged donors assigned to CS01, distinct PackageCode
       Toggle OFF: flag ignored, donors fall through to natural waterfall position

6. NEW DONOR (first gift within 90 days) [TOGGLE-GATED]
   └── Default OFF (suppressed during welcome window)
       Toggle ON: include in year-end + emergency

7. ACTIVE HOUSEFILE — HIGH VALUE (R1-R2, F1-F2, M1-M2) [TOGGLE-GATED]

8. ACTIVE HOUSEFILE — STANDARD (R1-R2, remaining) [TOGGLE-GATED]

9. MID-LEVEL PROSPECT ($500–$999.99, active 24 months) [TOGGLE-GATED]

10. LAPSED RECENT (R3: 13–24 months, 2+ lifetime gifts) [TOGGLE-GATED]

11. DEEP LAPSED (R4-R5: 25–48 months) [TOGGLE-GATED]
    └── Include only when break-even positive
        Sub-segment by cumulative giving tier

12. CBNC FLAG OVERRIDE
    └── Donors with 2+ gifts in non-consecutive years over 10-year window
        who would otherwise be suppressed by lapsed cutoffs
        → Include in Lapsed Recent regardless of recency
```

Every position marked `[TOGGLE-GATED]` can be set to ON or OFF per campaign in the segment group toggle panel (Step 2). When a position is OFF, the waterfall skips it entirely — donors that would have been caught at that position fall through to the next active position. Global Suppression (position 1) and CBNC Override (position 12) are always active and not toggleable.

Waterfall is **mutually exclusive** — once assigned at an active position, excluded from all subsequent tiers. Acquisition is handled by the agency via the housefile suppression file, not by this system.

**PackageCode routing for combined mailings:** When multiple segment groups are included in the same campaign (e.g., housefile + mid-level + cornerstone), the PackageCode field determines which creative version each donor receives. PackageCode is assigned per segment group from a configurable mapping in the Segment Rules tab (e.g., Active Housefile → P01, Mid-Level → P02, Cornerstone → P03). The lettershop sorts on PackageCode to route creative.

### 6.2 Suppression Engine

Suppression operates at two levels: **donor-level** (applied during Global Suppression at waterfall position #1, before segment assignment) and **segment-level** (applied after waterfall assignment, based on economic and behavioral rules).

#### 6.2.1 Donor-Level Suppression (Three-Tier Toggle System)

Every Account is checked against donor-level suppression fields before entering the waterfall. Fields are organized into three tiers with different default behaviors. All toggles are configurable per campaign in the MIC Segment Rules tab.

**Tier 1 — Hard Suppressions (default: always ON, warning if operator disables)**

These remove donors from the mailing universe entirely. The builder displays a warning if an operator attempts to disable any Tier 1 rule.

| # | Rule | Field / Logic | Notes |
|---|------|--------------|-------|
| 1 | All Household Members Deceased | `npsp__All_Members_Deceased__c = true` | Only deceased flag used for suppression. One-contact-deceased does NOT suppress — surviving spouse keeps receiving mail. |
| 2 | Do Not Contact | `Do_Not_Contact__c = true` | Explicit donor opt-out |
| 3 | No Mail | `No_Mail_Code__c = true` | Explicit no-mail request |
| 4 | Undeliverable Address | `npsp__Undeliverable_Address__c = true` | NPSP undeliverable flag |
| 5 | NCOA Deceased | `NCOA_Deceased_Processing__c = true` | Flagged via NCOA processing |
| 6 | Blank Address | `BillingStreet IS NULL OR BillingCity IS NULL OR BillingPostalCode IS NULL` | Computed at runtime. This is how TLC actually suppressed — on blank address fields, not the `Address_Unknown__c` checkbox. |

**Tier 2 — Communication Preference Suppressions (default: ON, toggleable per campaign)**

These suppress donors based on their stated communication preferences. Some have conditional logic tied to campaign type.

| # | Rule | Field | Default | Conditional Logic |
|---|------|-------|---------|-------------------|
| 1 | Newsletter and Prospectus Only | `Newsletter_and_Prospectus_Only__c` | ON | **Campaign-type conditional:** Suppress from all appeal mailings. INCLUDE when campaign type = Newsletter. These donors receive newsletters, receipts, and tax receipts only. |
| 2 | Newsletters Only | `Newsletters_Only__c` | ON | **Campaign-type conditional:** Same as above. Treat identically to Newsletter and Prospectus Only. (SF cleanup to combine these two flags is queued but not blocking.) |
| 3 | No Name Sharing | `No_Name_Sharing__c` | ON | Suppress from list exchange/rental and acquisition co-op files. |
| 4 | Match Only | `Match_Only__c` | ON | **Campaign-type conditional:** Suppress from standard appeals. INCLUDE only when campaign type = Match. |
| 5 | 1 Mailing Xmas Catalog | `X1_Mailing_Xmas_Catalog__c` | ON | **Frequency conditional:** Donor limited to 1 Christmas mailing per FY. Builder tracks mailing count for flagged donors and suppresses once limit is reached. |
| 6 | 2 Mailings Xmas/Easter | `X2_Mailings_Xmas_Appeal__c` | ON | **Frequency conditional:** Donor limited to 2 mailings per FY (Christmas + Easter). Builder tracks mailing count for flagged donors and suppresses once limit is reached. |

**Implementation requirements for Tier 2 conditionals:**

- **Campaign-type awareness (items 1, 2, 4):** The MIC Campaign Calendar must have a `campaign_type` field (or equivalent) that the builder reads. Values include at minimum: Appeal, Newsletter, Match, Catalog. When the campaign type matches the donor's preference, the suppression is skipped and the donor is included.
- **Mailing history tracking (items 5, 6):** The builder must query how many mailings a flagged donor has already received in the current FY. This can be derived from Campaign_Segment__c records loaded by prior runs. If count ≥ limit, suppress.

**Tier 3 — Rarely Used / Legacy (default: OFF)**

These fields exist on the Account but are not standard mail suppressions. Available in the toggle panel for edge cases. All default to OFF — donors are NOT suppressed unless an operator explicitly enables the rule for a specific campaign.

| # | Rule | Field | Notes |
|---|------|-------|-------|
| 1 | No Currency Mailers | `No_Currency_Mailers__c` | Legacy. HRI hasn't done currency mailers in years. |
| 2 | No Gifts or Premiums | `No_Gifts_or_Premiums__c` | Not recently used. |
| 3 | No Planned Giving Mail | `No_Planned_Giving_Mail__c` | PG mailings go through Canopy, not DM pipeline. Relevant if that changes. |
| 4 | One Contact Deceased | `Primary_Contact_is_Deceased__c` | Not used for mail suppression — only all-members-deceased suppresses. |
| 5 | Address Unknown | `Address_Unknown__c` | Legacy flag. Replaced by blank address field check in Tier 1. |
| 6 | Not Deliverable | `Not_Deliverable__c` | Legacy. Not in current SF views. |
| 7 | No Donor Elite | `No_Donor_Elite__c` | Legacy. Not used in recent years. |
| 8 | No Vaccine Solicitations | `No_Vaccine_Solicitations__c` | |
| 9 | Prospectus Only | `Prospectus_Only__c` | Not currently active. |
| 10 | No Presidents Gathering | `No_Presidents_Gathering__c` | Event-specific. |
| 11 | No Presidents Retreat | `No_Presidents_Retreat__c` | Event-specific. |
| 12 | Banner Ad Constituent | `Banner_Ad_Constituent__c` | Unknown current usage. |
| 13 | MC Only Account | `MC_Only_Account__c` | Unknown — investigate before using. |
| 14 | Mid Level Mail Review | `Mid_Level_Review__c` | Legacy. Previously for higher donors. Needs re-evaluation. |

#### 6.2.2 Segment-Level Suppression (Applied After Waterfall Assignment)

These rules operate on the segmented universe after donors have been assigned to waterfall positions. All toggleable rules can be enabled or disabled per campaign. Defaults are set in the MIC Segment Rules tab. Disabled rules are skipped entirely; they do not suppress any records. This toggle architecture accommodates future rules — new rules are added as new toggle rows in the Segment Rules tab without code changes to the engine.

| Rule | Default | Default State | Notes |
|------|---------|---------------|-------|
| Recent-gift window | 45 days | **Toggle: OFF (first 2 campaigns)** | Provisional default — no internal or external benchmark. Ships disabled. Enable after VeraData calibration provides benchmarked value. Override to 30 for year-end/emergency once enabled. |
| Break-even floor | CPP ÷ Avg Gift | **Always active** | Auto-suppress segments below floor for 3+ consecutive campaigns |
| Response rate floor | 0.8% | **Always active** | TLC-era rule (August memo). Starting point pending Campaign Scorecard segment-level data. |
| Frequency cap | 6 solicitations/year | **Toggle: OFF (first 2 campaigns)** | Provisional default — no internal or external benchmark. Ships disabled. Enable after VeraData calibration provides benchmarked value. Tracks cumulative mailings per donor per FY. |
| Holdout percentage | 5% of suppressed segments | **Toggle: ON** | Random sample retained for ROI measurement |

**Suppression audit log:** Every suppression action — both donor-level and segment-level — is logged with donor ID (or Account ID), rule triggered, tier, and campaign ID. The raw audit log is written to a **Google Drive CSV file** (one file per run, archived alongside the output files in the designated folder). The MIC never holds donor-level suppression data. Aggregate suppression counts by rule are surfaced in the Draft tab and the Apps Script UI summary (e.g., "Tier 1 Deceased: 340. Tier 2 Newsletter Only: 1,200. Recent Gift: 800. Frequency Cap: 340."). If total suppression exceeds 15% of the pre-suppression universe, the system flags for review in the UI.

---

## 7. Budget-Target Fitting

### 7.1 Campaign Configuration (from MIC)

When a campaign is selected, the system reads from the MIC Campaign Calendar row:

| MIC Field | Segmentation Builder Use |
|-----------|------------------------|
| `budget_qty_mailed` | Target Quantity |
| `budget_cost` | Total Budget |
| Computed: `budget_cost ÷ budget_qty_mailed` | CPP |
| `campaign_name` | Campaign identity |
| `appeal_code` | Appeal code campaign component (9-char `TYYMCPSS0`) |
| `mail_date` | Recency calculation reference date |
| `is_followup` | If true, additional lapsed suppression from follow-up universe |
| `lane` | Determines which segment tiers apply (Housefile vs. Mid-Level) |
| `campaign_type` | Appeal, Newsletter, Match, Catalog — drives Tier 2 conditional suppressions |
| `baseline_appeal_codes` | Comma-separated list of prior campaign appeal codes whose segment-level performance data drives the projection. Empty = auto-lookup. |

### 7.2 Historical Performance Lookup

The projection's economic columns — Hist. Response Rate, Hist. Avg Gift, and derived fields (Proj. Gross Revenue, Break-Even Rate, Margin) — require historical segment-level data. The builder resolves this through a three-level fallback:

**Level 1 — Baseline campaign(s) selected.** Jessica selects one or more prior campaigns by appeal code (stored in `baseline_appeal_codes`). The builder queries Campaign_Segment__c records for those campaigns, matches on segment code (AH01, ML01, CS01, etc.), and pulls actual response rate, average gift, and cost per piece. If multiple baseline campaigns are selected, the builder averages their metrics per segment code. This is the preferred path — Easter predicts Easter, year-end predicts year-end.

**Level 2 — No baseline selected, prior data exists.** If `baseline_appeal_codes` is empty, the builder queries all Campaign_Segment__c records with matching segment codes across all prior campaigns and computes a rolling average (weighted by recency — most recent campaign gets 2× weight, prior campaigns 1×). This auto-lookup improves as more campaigns flow through the system.

**Level 3 — No historical data for a segment code.** For new segment codes with no prior data (expected for the first 2–3 campaigns), the Draft tab shows blank cells in the Hist. Response Rate and Hist. Avg Gift columns. Jessica enters manual estimates based on TLC baseline data or industry benchmarks. The builder computes the remaining economic columns from her input.

The Draft tab clearly labels the data source per segment row: "Baseline: R2631" or "Avg: 3 campaigns" or "Manual" — so Jessica knows which numbers are grounded in actuals and which are estimates.

### 7.3 Three-Pass Projection

**Pass 1 — Full Universe.** Waterfall runs with all rules, no quantity cap. Every qualified segment displayed with quantity, economics, and break-even status.

**Pass 2 — Fit to Target (when universe > target).** Trims from the bottom of the waterfall upward. Deep Lapsed with weakest economics cut first, then marginal Lapsed Recent sub-segments, working up the hierarchy until total ≤ target. Draft tab shows "Full Universe" and "Budget Fit" columns side by side. Trimmed segments shown greyed with "Below budget line" tag.

When the budget line cuts through a segment (e.g., budget requires removing 500 records from a segment of 1,200), the system applies an intra-segment tie-breaker: sort descending by composite RFM score, then by MRC descending, then by recency ascending. Records at the bottom of the sorted list are trimmed first. This ensures the strongest records within any segment are retained when partial trimming is required.

**Pass 3 — Expansion Options (when universe < target).** System calculates gap and presents expansion levers ranked by economic attractiveness:

| Lever | Computation |
|-------|-------------|
| Extend deep lapsed window | Recalculate with wider recency boundary, show added records + economics |
| Relax recent-gift window | Reduce suppression window, show added records + economics |
| Lower response rate floor | Reduce floor, show sub-segments that re-enter + economics |
| Include new donors in welcome window | Add 90-day new donors, show count + economics |
| Include Cornerstone partners | Toggle Cornerstone ON, show flagged population count + economics |

Each lever shows: records added, estimated response rate, estimated net revenue impact, green/amber/red indicator. Jessica toggles in the Draft tab, running total updates.

**The system never auto-includes to hit target.** It presents options; Jessica decides. The system also shows the comparison: "Mailing 33,000 at projected $X net vs. mailing 35,000 at projected $Y net" — if adding marginal names costs more than they return, the smaller mailing is better.

### 7.4 Follow-Up / Chaser Campaigns

When `is_followup = true` in the MIC, the system applies additional suppression: lapsed donors (25+ months) are removed from the follow-up universe per TLC baseline logic. The follow-up pull references the parent campaign's segment assignments — only donors who were in the original appeal universe are eligible for the chaser.

---

## 8. Ask String Computation

### 8.1 Ask Basis by Segment

| Segment | Basis | Rationale |
|---------|-------|-----------|
| Active + Mid-Level + Mid-Level Prospect | HPC | Best gift reflects demonstrated capacity |
| Lapsed + Deep Lapsed | MRC | HPC may be stale; MRC is more realistic anchor |
| Cornerstone | HPC | High-value reactivation — anchor to demonstrated giving |
| New Donor | First gift amount | Only data point available |

### 8.2 Ask Array Formula

Standard: `[1× basis, 1.5× basis, 2× basis, "Best Gift of $___"]`

| Parameter | Default | Configurable |
|-----------|---------|-------------|
| Minimum ask | $15 | Yes |
| Maximum ask | $4,999.99 | Yes |
| Multipliers | 1×, 1.5×, 2× | Yes |
| Rounding | Nearest $5 below $100; nearest $25 above $100 | Yes |

**Rounding direction:** Always round up to the next increment. A computed ask of $22.50 rounds to $25, not $20. Rounding down an ask amount is never correct in fundraising context.

### 8.3 California Versioning

Boolean `CA_Version` flag on Printer File for California addresses when campaign is configured as 33x Shipping match. Not applied to all campaigns.

### 8.4 Reply Copy Tier

| Tier | Criteria | Copy Template Key |
|------|----------|-------------------|
| ACTIVE | Gave in current + prior FY | "Thank you for your faithful partnership..." |
| LAPSED | Last gift > 12 months | "It's been some time since we last heard from you..." |
| NEW | First gift in current FY | "Thank you for joining the family this year..." |
| REACTIVATED | Had 12+ month gap, gave in last 12 months | "Welcome back..." |

Appended as a field in the Printer File for the agency.

---

## 9. Appeal Code Architecture

### 9.1 Appeal Code Format

The Segmentation Builder generates a unique appeal code per segment × campaign × package × test flag. This is HRI's internal tracking code that enables closed-loop performance measurement at the segment level.

15-character string:

```
[Program][FY][Campaign][Segment][Package][Test]
  1 chr   2    2 chr     4 chr    3 chr   3 chr
```

| Position | Dimension | Values |
|----------|-----------|--------|
| 1 | Program | R = Renewal/Housefile, M = Mid-Level, C = Cornerstone |
| 2–3 | Fiscal Year | 26, 27 |
| 4–5 | Campaign | 01–12 (monthly), NL = Newsletter |
| 6–9 | Segment | 4-character persistent code (AH01, ML01, LR01, CS01, etc.) |
| 10–12 | Package | P01, P02 — persistent per creative treatment |
| 13–15 | Test/Control | CTL, TSA, TSB |

Example: `R2605AH01P01CTL` = Renewal, FY26, Easter, Active Housefile tier 1, Package 01, Control.

> **Relationship to existing 9-character convention:** HRI's historical appeal code format is `TYYMCPSS0` (9 characters — see Appeal Codes master document). The 15-character format extends this for segment-level tracking internally. The first character (Program) corresponds to the T position in the legacy format. **The 15-character code never leaves HRI.** It lives in the Internal Matchback File only. The printer and caging company receive the 9-character campaign-level appeal code (the same format currently in use). The 9-character code also lives on the Campaign object's `Appeal_Code__c` field and in the MIC Campaign Calendar.

### 9.2 Scanline Architecture

The **scanline** is the machine-readable code printed on the outer envelope and reply device. Both pieces carry the same scanline. The scanline consists of two components:

```
[9-digit zero-padded Donor ID]  [9-char Appeal Code]
         070113336                   R2631TYRE
```

- **Donor ID**: `Constituent_Id__c` from Account, zero-padded to 9 digits
- **Appeal Code**: The 9-character campaign-level appeal code in `TYYMCPSS0` format — NOT the 15-character internal code

The caging company reads the 9-digit donor ID from the scanline for gift processing and account matching. The 9-character appeal code identifies the campaign and panel.

**Key principle:** The 15-character internal appeal code — which carries segment, package, and test cell granularity — stays on HRI's side in the Internal Matchback File. It never goes to the printer, cager, or agency. Matchback is performed by joining the donor ID from the caging response file against the Internal Matchback File to recover the full 15-character code and all segment detail.

The Segmentation Builder generates the scanline as a computed field in the Printer File: `Scanline = LPAD(Constituent_Id__c, 9, '0') + CampaignAppealCode`.

### 9.3 Segment Code Registry (in MIC Segment Rules tab)

| Code | Segment |
|------|---------|
| AH01 | Active 0–6mo, $50+ avg |
| AH02 | Active 0–6mo, $25–$49.99 avg |
| AH03 | Active 0–6mo, under $25 avg |
| AH04 | Active 7–12mo, $50+ avg |
| AH05 | Active 7–12mo, $25–$49.99 avg |
| AH06 | Active 7–12mo, under $25 avg |
| ML01 | Mid-Level ($1,000–$4,999.99) |
| MP01 | Mid-Level Prospect ($500–$999.99) |
| LR01 | Lapsed Recent 13–18mo |
| LR02 | Lapsed Recent 19–24mo |
| DL01 | Deep Lapsed 25–36mo, $100+ cum |
| DL02 | Deep Lapsed 25–36mo, under $100 cum |
| DL03 | Deep Lapsed 37–48mo, $100+ cum |
| CS01 | Cornerstone (flagged population) |
| CS02 | Cornerstone subset (reserved — future use if population is split) |
| ND01 | New Donor |
| SU01 | Sustainer |
| CB01 | CBNC override |
| MJ01 | Major Gift custom package |

Persistent across all campaigns. Appeal codes generated by formula from individual dimension fields.

---

## 10. Output File Specification

The Segmentation Builder produces **two output files** per campaign run, plus one standing suppression file. The Printer File goes to VeraData/lettershop. The Internal Matchback File stays on HRI's side in Google Drive.

### 10.1 Printer File (goes to agency/lettershop)

CSV, UTF-8, comma-delimited, double-quote qualifier. One row per donor.
Filename: `HRI_[CampaignCode]_[Lane]_PRINT_[YYYYMMDD].csv`

This file contains only what the printer and caging company need. The 15-character internal appeal code is NOT in this file.

| # | Field | Type | Notes |
|---|-------|------|-------|
| 1 | DonorID | Text(9) | `Constituent_Id__c`, zero-padded to 9 digits |
| 2 | CampaignAppealCode | Text(9) | 9-character appeal code in `TYYMCPSS0` format |
| 3 | Scanline | Text | Computed: DonorID + CampaignAppealCode. Printed on outer envelope and reply device. |
| 4 | PackageCode | Text(3) | Routes creative version at lettershop |
| 5 | Addressee | Text | `npo02__Formal_Greeting__c` from Account. Full addressing name (e.g., "Mr. and Mrs. Brian Schwanbeck"). Per VeraData requirement — this is the name printed on the envelope. |
| 6 | Salutation | Text | `Special_Salutation__c` from Account. Expected format: "Mr. Petersen" (title + last name), not just "Mr." If field stores title only, builder computes from `Special_Salutation__c` + `Last_Name__c`. Confirm field content in Phase 1 diagnostic. |
| 7 | FirstName | Text | Personalization |
| 8 | LastName | Text | Personalization |
| 9 | Address1 | Text | Mailing address |
| 10 | Address2 | Text | Mailing address |
| 11 | City | Text | Mailing address |
| 12 | State | Text(2) | Mailing address |
| 13 | ZIP | Text(10) | Preserve leading zeros |
| 14 | Country | Text | Mailing address |
| 15 | AskAmount1 | Currency | Ask string — 1× basis |
| 16 | AskAmount2 | Currency | Ask string — 1.5× basis |
| 17 | AskAmount3 | Currency | Ask string — 2× basis |
| 18 | AskAmountLabel | Text | "Best Gift of $___" |
| 19 | ReplyCopyTier | Text | ACTIVE / LAPSED / NEW / REACTIVATED — drives variable copy |
| 20 | LastGiftAmount | Currency | For reply device variable copy |
| 21 | LastGiftDate | Date | For reply device variable copy |
| 22 | CurrentFYGiving | Currency | For reply device variable copy |
| 23 | PriorFYGiving | Currency | For reply device variable copy |
| 24 | CAVersion | Boolean | California version flag (33x Shipping match only) |

### 10.2 Internal Matchback File (stays on HRI's side)

CSV, UTF-8, comma-delimited, double-quote qualifier. One row per donor.
Filename: `HRI_[CampaignCode]_[Lane]_MATCHBACK_[YYYYMMDD].csv`
Stored in designated Google Drive folder. Never transmitted to agency.

This file contains everything — the full 15-character appeal code, all segment detail, and all analyst fields. When caging returns a response file with donor IDs, HRI joins against this file to recover segment-level performance data.

| # | Field | Type | Notes |
|---|-------|------|-------|
| 1 | DonorID | Text(9) | `Constituent_Id__c`, zero-padded to 9 digits |
| 2 | CampaignAppealCode | Text(9) | 9-char appeal code (matches Printer File) |
| 3 | InternalAppealCode | Text(15) | 15-char code with segment × package × test granularity |
| 4 | SegmentCode | Text(4) | AH01, ML01, CS01, etc. |
| 5 | SegmentName | Text | Human-readable segment label |
| 6 | PackageCode | Text(3) | P01, P02, etc. |
| 7 | TestFlag | Text(3) | CTL, TSA, TSB |
| 8 | Addressee | Text | `npo02__Formal_Greeting__c` |
| 9 | Salutation | Text | `Special_Salutation__c` |
| 10 | FirstName | Text | |
| 11 | LastName | Text | |
| 11 | Address1 | Text | |
| 12 | Address2 | Text | |
| 13 | City | Text | |
| 14 | State | Text(2) | |
| 15 | ZIP | Text(10) | |
| 16 | Country | Text | |
| 17 | AskAmount1 | Currency | |
| 18 | AskAmount2 | Currency | |
| 19 | AskAmount3 | Currency | |
| 20 | AskAmountLabel | Text | |
| 21 | ReplyCopyTier | Text | |
| 22 | LastGiftAmount | Currency | |
| 23 | LastGiftDate | Date | |
| 24 | CurrentFYGiving | Currency | |
| 25 | PriorFYGiving | Currency | |
| 26 | CumulativeGiving | Currency | Lifetime total |
| 27 | LifecycleStage | Text | New / 2nd Year / Multi-Year / Reactivated / Lapsed / Deep Lapsed / Expired |
| 28 | CAVersion | Boolean | |
| 29 | CornerstoneFlag | Boolean | |
| 30 | Email | Text | Future multi-channel |
| 31 | SustainerFlag | Boolean | |
| 32 | GiftCount12Mo | Integer | |
| 33 | RFMScore | Text(3) | Composite R×F×M bucket code |

### 10.3 Housefile Suppression File

Separate CSV with all current housefile donors (ID + name + address) for agency merge/purge against acquisition lists.

### 10.4 Post-Mailing Return Data

Agency returns: actual mail date, actual quantities by segment, nixie/return reports. Jessica enters actuals into MIC Campaign Calendar. Campaign Scorecard handles revenue/response actuals from Salesforce. HRI performs matchback by joining caging response file (donor ID) against the Internal Matchback File to attribute gifts to specific segments, packages, and test cells.

---

## 11. Build Phases

### Phase 1: MIC Integration + Data Extract (Diagnostic Gate)
**Scope:** Connect to MIC Google Sheet. Read Campaign Calendar and Segment Rules tabs. Create Draft tab structure. Build Salesforce data pull (all ~50K accounts + Opportunity detail). RFM computation. Diagnostic output to Google Sheet showing donor distribution by R/F/M bucket. **This phase is explicitly a diagnostic gate:** it confirms whether the Salesforce data model supports the architecture before any subsequent phase builds on it. Open items 1–3 (HPC/MRC availability, sustainer identification, major gift portfolio field) are resolved by the Phase 1 diagnostic — not by separate prep work.
**Gate:** Bill reviews RFM distribution. No zero-count buckets, no single bucket >60% of donors. MIC read confirmed. Draft tab structure validated. **Diagnostic gate criteria (must be confirmed before Phase 2):** (a) HPC (`npo02__LargestAmount__c`) and MRC (`npo02__LastOppAmount__c`) are populated with real data across the account base — not null or stale. (b) `Miracle_Partner__c` checkbox reliably identifies active monthly sustainers — spot-check 20 accounts against known sustainer list. (c) `Staff_Manager__c` lookup identifies major gift portfolio donors — review `hri-major-gift-app` repo for any additional include/exclude logic. If any diagnostic fails, the spec revises before Phase 2 begins.
**Sessions:** 1–2

### Phase 2: Waterfall Assignment Engine
**Scope:** Segment assignment logic, waterfall priority, mutual exclusivity. CBNC detection. Output: segmented donor list with assignments + audit trail written to Draft tab.
**Gate:** Jessica reviews against TLC's most recent campaign quantities. Segment sizes within ±20% of baseline (or explained by boundary changes).
**Sessions:** 1–2

### Phase 3: Suppression Engine + Budget-Target Fitting
**Scope:** Global + segment-level suppression with toggle architecture. Three-tier donor-level suppression system (see Suppression Toggles document). Recent-gift window, frequency caps, break-even calculation, holdout groups — all as toggleable rules. Three-pass projection (full universe → fit to target → expansion levers) with intra-segment tie-breaker. Suppression audit log to Google Drive. Aggregate suppression summary to Draft tab.
**Gate:** Total suppression ≤15% of pre-suppression universe without explicit justification. Budget fit demonstrated with real campaign targets from MIC. Suppression audit log CSV confirmed in Google Drive.
**Sessions:** 2

### Phase 4: Ask String + Appeal Code Generation
**Scope:** Per-record ask computation (HPC/MRC basis, multipliers, floors/ceilings, rounding — always round up). Reply copy tier. Appeal code generation: 9-character `TYYMCPSS0` format for Printer File scanline, 15-character internal format for Matchback File. Scanline computation (9-digit zero-padded donor ID + 9-char appeal code). CA version flag.
**Gate:** Jessica spot-checks 50 records across segments. Appeal codes unique (no duplicates at both 9-char and 15-char levels). Registry matches output. Ask rounding confirmed correct (up, never down). Scanline format validated against physical mail piece format (see Section 9.2).
**Sessions:** 1

### Phase 5: Output Files + MIC Write-Back
**Scope:** Two-file CSV generation per spec: Printer File (for VeraData) and Internal Matchback File (for HRI). Google Drive archival. Approve workflow: Draft tab → Segment Detail copy. link_to_segments auto-population. Housefile suppression file generation. Run idempotency (upsert keys, status transition enforcement). Validate Printer File format with VeraData.
**Gate:** Jessica reviews Draft tab with ZIPs displayed correctly. Printer File CSV validates programmatically (ZIP preservation, no truncation, 15-char code absent). VeraData confirms Printer File meets production requirements. Internal Matchback File contains 15-char codes and all analyst fields. MIC Segment Detail tab populated correctly on approval. Re-run produces no duplicates.
**Sessions:** 1

### Phase 6: Apps Script Web App
**Scope:** UI in Internal Tools Portal. Campaign selection from MIC. Segment rule override panel with toggle controls. Projection trigger (writes to Draft tab). Approve/generate workflow. Output download. Post-mailing actuals entry. Salesforce load trigger. Status transition display.
**Gate:** Jessica runs a full campaign cycle through the UI using the next upcoming mailing's actual parameters.
**Sessions:** 2–3

### Phase 7: Cornerstone Flag Validation + PackageCode Routing
**Scope:** Validate that the Cornerstone flag (`Cornerstone_Partner__c`) is clean (triage query executed separately — see `cornerstone-partners-flag-logic.md`). Confirm toggle ON/OFF behavior: flag ON assigns to CS01 with distinct PackageCode; flag OFF skips position, donors fall through. Implement configurable PackageCode mapping in Segment Rules tab for all segment groups in combined mailings (e.g., Active → P01, Mid-Level → P02, Cornerstone → P03).
**Gate:** Toggle ON: correct donors assigned to CS01. Toggle OFF: flagged donors land at their natural RFM position. PackageCode correctly assigned per segment group. No runtime scoring model — builder reads the clean flag directly.
**Sessions:** 1

> **Note:** The v2 spec included a Phase 7 scoring model (recency 50%, cumulative giving 30%, frequency 20%, percentile-rank normalization, quartile assignment) designed to filter 11,000 flagged donors down to ~2,800 at runtime. This is deferred. The Cornerstone flag is now maintained externally via a triage query that curates the population directly. If post-triage population is still too large for direct mailing, runtime scoring can be re-added. Decision made after Cornerstone diagnostic results are reviewed.

### Phase 8: Budget Summary Tab + Scorecard Integration
**Scope:** MIC Budget Summary tab with formulas rolling up Campaign Calendar by lane/channel/FY. Budget vs. actual with variance. Scorecard pipeline writes actuals to MIC Campaign Calendar. End-to-end closed-loop validation.
**Gate:** Bill reviews Budget Summary with at least one campaign's actuals flowing through the full loop: MIC plan → Segmentation Builder pull → Salesforce load → Scorecard refresh → MIC actuals.
**Sessions:** 1–2

### Phase 9: Deployment + Cloud Scheduler
**Scope:** Cloud Run deployment. IAM bindings. Post-deploy collateral damage check on all existing services in GCP project.
**Gate:** Force-run all Cloud Scheduler jobs, confirm all existing services function.
**Sessions:** 1

**Total estimated sessions:** 11–16

---

## 12. Acceptance Criteria

1. Jessica can select a campaign from the MIC, run a projection to the Draft tab, review budget fit in Sheets, approve, generate the output files (Printer File + Internal Matchback File), and transmit the Printer File to VeraData — through the Apps Script UI without Bill's intervention.
2. Budget-target fitting works in all three scenarios: universe > target (trims from bottom with intra-segment tie-breaker), universe ≈ target (proceeds), universe < target (presents expansion levers in Draft tab).
3. Appeal codes are unique: 9-character codes unique per campaign × panel, 15-character internal codes unique per segment × package × test flag. Scanline format matches physical mail piece spec (9-digit donor ID + 9-char appeal code). 15-character code appears only in Internal Matchback File, never in Printer File.
4. Every donor in the output files is assigned to exactly one segment.
5. Suppression audit log CSV archived to Google Drive with every run. Aggregate suppression counts displayed in Draft tab and UI summary.
6. Mailing projection computes break-even and flags marginal segments.
7. Jessica reviews segment data in the Draft tab with ZIP codes displayed correctly (text-formatted column). Printer File CSV validates programmatically: ZIP field preserves leading zeros, no truncation. VeraData confirms Printer File meets production requirements. Internal Matchback File archived to Google Drive.
8. Draft tab auto-populates when a projection runs. Segment Detail tab populates when Jessica approves.
9. MIC Campaign Calendar link_to_segments column references the Segment Detail rows.
10. Campaign_Segment__c records load to Salesforce via upsert (no duplicates on re-load) and Campaign Scorecard picks them up.
11. Scorecard actuals flow back to MIC Campaign Calendar actuals columns.
12. Budget Summary tab shows correct rollups with variance tracking.
13. Re-running a projection from Draft/Projected status overwrites prior data. Re-running from Approved requires explicit unlock. No duplicate Segment Detail rows or Campaign_Segment__c records on any rerun path.
14. Suppression toggle rules can be enabled/disabled per campaign without code changes.
15. Pipeline write failure at any target (Drive, Sheets, Salesforce) does not advance campaign status. Per-target success/fail flags are logged. Retry action re-executes only failed targets without duplicating data at successful targets. Tested by simulating a Salesforce write failure during output generation.
16. Provisional suppression defaults (45-day recent-gift window, 6-solicitation frequency cap) ship as OFF for the first two production campaigns. Enabled only after VeraData calibration provides benchmarked values.

---

## 13. Dependencies

| Dependency | Status | Impact if Blocked |
|-----------|--------|-------------------|
| Salesforce API access | Available | Cannot proceed |
| MIC Google Sheet | Exists (914 rows, FY20–FY26) | Must add Draft, Segment Detail, Budget Summary, Segment Rules tabs |
| Campaign Scorecard v26 | Deployed | Pro forma uses manual estimates until Scorecard data available by new segment codes |
| Appeal Index | Available | Appeal code campaign codes reference |
| VeraData onboarding | In progress, May start | Output format must be validated before Phase 5 gate |
| Campaign_Segment__c | Exists in Salesforce | No blocker |
| Google Drive output folder | Confirmed: `1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC` | No blocker |

---

## 14. Open Items for Build Sessions

1. ~~**Verify HPC/MRC field availability.**~~ **RESOLVED.** HPC = `npo02__LargestAmount__c` (Account rollup, Currency). MRC = `npo02__LastOppAmount__c` (Account rollup, Currency). Both are NPSP-maintained rollups on Account. Phase 1 diagnostic validates data quality (populated, not stale).

2. ~~**Sustainer identification field.**~~ **RESOLVED.** `Miracle_Partner__c` on Account (Checkbox). This is the toggle-gated waterfall position #4.

3. ~~**Major gift portfolio identification.**~~ **RESOLVED.** `Staff_Manager__c` on Account (Lookup to User). Populated = donor is assigned to a gift officer = qualifies for waterfall position #2. Phase 1 diagnostic should also review the `hri-major-gift-app` repo for any additional include/exclude logic beyond the Staff Manager lookup (that app already makes portfolio membership decisions).

4. ~~**Output file format validation with VeraData.**~~ **RESOLVED.** VeraData reviewed Printer File layout (Shaun Petersen, April 14, 2026). Two additions incorporated: (a) Addressee field added (`npo02__Formal_Greeting__c` — full addressing name for envelope), (b) Salutation field clarified (must be "Mr. Petersen" format, not just title). VeraData also requests NCOA certificate with data delivery — operational item for Jessica, not a system change. Printer File layout now confirmed.

5. ~~**Historical response rates by new segment codes.**~~ **RESOLVED in spec.** Three-level fallback: (1) Jessica selects baseline campaign(s) by appeal code, (2) auto-lookup rolling average across all prior campaigns with matching segment codes, (3) manual entry for new codes with no history. See Section 7.2.

6. ~~**MIC sheet ID.**~~ **RESOLVED.** MIC is a live Google Sheet: `12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw`. Draft, Segment Detail, Budget Summary, and Segment Rules tabs to be added during Phase 1.

7. **Scorecard actuals write-back.** The Campaign Scorecard currently writes to its own data sheet. Phase 8 extends it to also write actuals to the MIC Campaign Calendar tab. Confirm this doesn't require a Scorecard rebuild — likely just an additional Sheets API write at the end of the existing refresh pipeline.

8. ~~**Appeal code prefix precedence rule.**~~ **RESOLVED.** Applies to Campaign Scorecard data processing only (historical data interpretation). The Segmentation Builder generates appeal codes using the prefix conventions from the Appeal Codes master document — no conflict possible because the builder is the system of record for new codes. The first character (T position in `TYYMCPSS0`) is set by the segment group assignment at generation time.

9. **Suppression parameter calibration with VeraData.** The 45-day recent-gift window and 6-solicitation/year frequency cap are provisional defaults with no internal or external benchmark. Both ship as OFF for the first two production campaigns. Consult VeraData during onboarding for recommended values. Enable via toggle once calibrated.

10. **Sheets-as-database migration evaluation.** After first full year of operation, evaluate whether the MIC Google Sheet should migrate to Cloud SQL or BigQuery. See architecture note in Section 2.2.

11. ~~**Google Drive output folder ID.**~~ **RESOLVED.** Output folder: `https://drive.google.com/drive/folders/1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC`. Printer Files, Internal Matchback Files, and suppression audit logs archived here.

12. ~~**Suppression toggles team verification.**~~ **RESOLVED.** Bekah reviewed April 13, 2026. Key changes: `Primary_Contact_is_Deceased__c` moved to Tier 3 (only all-members-deceased suppresses). `Enews_Only__c` removed (Contact-level field). `Address_Unknown__c` and `Not_Deliverable__c` moved to Tier 3 (replaced by blank address field check). `Match_Only__c`, `No_Name_Sharing__c`, `X1_Mailing_Xmas_Catalog__c`, `X2_Mailings_Xmas_Appeal__c` moved up to Tier 2. `Newsletter_Only` and `Newsletter_and_Prospectus_Only` confirmed as conditional — include in newsletter campaigns, suppress from everything else. Pending: `EU_Data_Anonymization__c` disposition; SF cleanup to combine newsletter flags.

13. ~~**Campaign_Segment__c field name verification.**~~ **RESOLVED.** Builder writes to existing `Source_Code__c` field on Campaign_Segment__c using the generated appeal code values. Label rename from "Source Code" to "Segment Code" queued for Bekah but not blocking the build.
