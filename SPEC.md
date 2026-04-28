# HRI Segmentation Builder — Full Specification

**Hope Rises International**
**Version 3.4 — April 28, 2026**
**FIS Component: Segmentation Engine + Segment Data Loader + Campaign Intelligence Workbook**

---

### Revision History

| Version | Date | Changes |
| --- | --- | --- |
| 2.0 | April 10, 2026 | Initial full spec with MIC integration, three-pass projection, appeal code architecture |
| 3.0 | April 10, 2026 | External review triage (ChatGPT + Gemini). Appeal code diagram fix. Intra-segment tie-breaker. Ask rounding direction. Run idempotency model. Draft-tab review pattern. Suppression audit log → Google Drive. Suppression rules as toggles. ZIP validation revised. Retention policy. Sheets-as-database migration flag. |
| 3.1 | April 10, 2026 | Segment group toggle panel (systematic include/exclude for all waterfall positions). Cornerstone flag read-only (scoring model deferred — flag maintained externally via triage query). Waterfall positions toggle-gated. |
| 3.2 | April 13–14, 2026 | Object model correction: Contact → Account throughout. "Source code" → "appeal code" terminology. Scanline architecture: 9-digit donor ID + 9-char appeal code, confirmed against physical mail piece. Two-file output: Printer File (agency, 9-char) and Internal Matchback File (HRI, 15-char). GCS → Google Drive. Three-tier donor-level suppression system (Bekah reviewed). Cornerstone field → `Cornerstone_Partner__c`. MIC confirmed live. Baseline campaign selector for historical performance projection (Section 7.2). Pipeline write recovery with sequential write order, per-target success/fail flags, and retry logic (Section 3). Phase 1 reframed as explicit diagnostic gate for open items 1–3. Single-operator constraint documented. `campaign_type` field added to MIC. Six open items resolved (#5, #6, #8, #11, #12, #13). |
| 3.4 | April 28, 2026 | Per-segment Holdout %. Holdout moves from a global ON/OFF toggle to a per-segment column in the scenario editor segment table. Default 5%; range 0–5% (cap preserves measurement upper bound; 0 = no holdout for that segment). Soft UI warning when value < 3% ("low holdout reduces ROI measurement power"). Replaces the hard-coded global toggle with operator-tunable per-row decisions, matching the existing `% INCL` per-segment column pattern. Driven by Bill 2026-04-28 review of A2651+M2651 universe — 5% holdout costs ~53 mid-level mailings on a 1,074-donor cohort, worth letting the operator dial down per segment when the trade-off is justified. |
| 3.3 | April 28, 2026 | Bekah/Bill/Erica/Jessica review. Mid-Level redefined: 24-month cumulative (not lifetime), $750 floor, no upper cap. Mid-Level Prospect (MP01) eliminated — sub-$750 routes to active housefile / lapsed RFM. Account.Type (DAF, Government) and Account.RecordType (3 ALM org types) added to Tier 1 hard suppression. SF field rename: `TLC_Donor_Segmentation__c` → `Major_Donor_In_House__c`; picklist reduced to `Major - In House` + blank (574 `Mid - TLC` records cleared); routed as Tier 2 always-on suppression. Tier 1 / Tier 2 / Tier 3 reorganized: NCOA Deceased / Not Deliverable / Primary Contact Deceased removed; No Mail Code moved to Tier 2 always-on toggle; No Name Sharing / Address Unknown / Newsletter and Prospectus Only removed from Tier 2; X1/X2 Christmas mailing flags promoted from Tier 3 to Tier 2; Tier 3 deleted entirely. New Donor Welcome promoted from waterfall GROUP_EXCLUDE to Tier 1.5 hard pre-emption. Cohort prefix routing: J-prefix removed (was misinterpreted), N-prefix added for in-house Major Donor mailings. Recent-gift window clarified as spec'd-but-not-built pending Faircom guidance. Miracle Partner vs Cornerstone overlap: Miracle Partner wins (already correct in code; documented explicitly). **Patch (post-builder)**: §5.5.1 originally stated the picklist value would be `In House`; architect kept the live value `Major - In House` to avoid a 174-record migration and didn't update §5.5.1. Builder caught the drift; suppression code uses whitespace-stripped match for forward compatibility. SPEC §5.5.1 corrected. |

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
| --- | --- | --- |
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

The existing MIC Google Sheet becomes the Campaign Intelligence Workbook — the single source of truth where planning, segmentation, and measurement converge. Seven tabs (verified in MIC 2026-04-27):

| Tab | Purpose | Written By | Read By |
| --- | --- | --- | --- |
| **mic\_flattened.csv** | Campaign Calendar. One row per campaign touch. FY / year / month / mail date / donor type / program / campaign type / F/U flag / appeal code / lane / qty / cost / projected revenue + actuals. Tab name inherited from the original CSV import; functionally this is the Campaign Calendar. | Jessica (budget plan), Campaign Scorecard pipeline (revenue/response actuals), Jessica (cost actuals post-mailing) | Segmentation Builder (reads budget target), Campaign Scorecard (reads campaign metadata), Budget Summary (formula references) |
| **Draft** | Working projection for the active campaign pull. One row per segment. Auto-populated by the Segmentation Builder when a projection runs. Jessica reviews segment quantities, economics, inclusion/exclusion, and expansion levers here. Overwritten on each new projection run. Only one campaign's draft is active at a time. | Segmentation Builder (auto-populated) | Jessica (review and approve) |
| **Segment Detail** | Finalized segment records. One row per segment per approved campaign. Written when Jessica approves a projection — the Draft tab contents are copied to Segment Detail as permanent record. | Segmentation Builder (on approval) | Campaign Scorecard (reads segment-level plan for variance analysis), Campaign Calendar (link_to_segments column references) |
| **Budget Summary** | FY-level rollup by lane and channel. Budget vs. actual with variance. All formula-driven from `mic_flattened.csv`. | Formulas (no manual entry) | Bill (review) |
| **Segment Actuals** | Segment-level actuals from Campaign Scorecard. Closes the segment-level economics loop — written by Scorecard pipeline, read by Segmentation Builder for Historical Baseline. | Campaign Scorecard pipeline | Segmentation Builder (Historical Baseline lookup) |
| **Universe** | Snapshot of the eligible donor universe for the active campaign pull (post-Tier-1 suppression, post-toggle-exclusion). Used by the scenario editor for browser-side what-if iteration without re-querying SF/BQ. | Segmentation Builder (auto-populated on Refresh Universe) | Apps Script web app (scenario editor reads this) |
| **Historical Baseline** | Multi-campaign weighted-average performance per segment by campaign type (Shipping, Easter, Year End, etc.). Refreshed nightly. Drives the Draft tab's projected economics. | Segmentation Builder nightly job (reads `sf_cache.historical_baseline` BQ table → writes Sheet) | Segmentation Builder (Draft projection lookup) |

**Segment rules / configuration:** the prior spec named a "Segment Rules" tab here for waterfall hierarchy, toggle defaults, ask multipliers/floors/ceiling, package codes, etc. That tab was never built — those rules currently live in `src/config.py::DEFAULT_TOGGLES` and `src/config.py` constants in the build repo. To change a rule today, edit config and redeploy. Future enhancement: lift rules into a sheet tab so Jessica/Bill can edit without redeploy. Not in current scope.

**Operator-facing documentation lives in the Apps Script web app UI, not in MIC tabs.** See §17 for the in-UI Reference and Buttons tabs that ship with the segmentation builder.

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

`hri-receipt-automation` (primary project). Service account: `hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`. Same project and SA as Campaign Scorecard, Donor File Health, and all SF-connected services. SF secrets already in this project's Secret Manager.

> **Correction (2026-04-17):** Spec originally referenced `gmail-agent-489116` — that project is isolated for Gmail Agent only (OAuth, not ADC). Segmentation Builder uses ADC with SA impersonation and belongs in `hri-receipt-automation`.

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
| --- | --- | --- |
| Major Gift Portfolio | Include (custom package) | Donors skip position #2, fall through to their natural RFM position |
| Mid-Level | Include | Donors skip position #3, fall through as active housefile |
| Sustainers | Exclude | Toggle ON to include (year-end/emergency) |
| Cornerstone | Include | Donors skip position #5, flag ignored, score normally in waterfall |
| New Donor | Exclude (welcome window) | Toggle ON to include |
| Active Housefile | Include | Toggle OFF for mid-level-only or cornerstone-only mailings |
| Lapsed | Include | Toggle OFF to narrow |
| Deep Lapsed | Include (with break-even gate) | Toggle OFF to narrow |

Common configurations:
- **Standard housefile appeal**: Active + Lapsed + Deep Lapsed ON. Mid-Level ON (different panel via PackageCode). Cornerstone ON (different panel via PackageCode). Major Gift ON (custom package). Sustainers OFF. New Donor OFF.
- **Mid-level only mailing**: Mid-Level ON. Everything else OFF.
- **Cornerstone-only reactivation**: Cornerstone ON. Everything else OFF.
- **Year-end kitchen sink**: Everything ON including Sustainers and New Donor.

**Toggle semantics (REVISED 2026-04-28): mixed — flag-based positions EXCLUDE; RFM/composite positions SKIP-AND-FALL-THROUGH.**

Earlier today (2026-04-27) the spec said all toggle OFFs exclude universally. That rule worked for flag-based segments (Cornerstone, Sustainer, etc.) but composed badly for RFM-based segments because Mid-Level and Major Gift are cross-cuts of the RFM lifecycle, not parallel buckets. A "Mid-Level only" mailing (Mid-Level + Major Gift ON, everything else OFF) collapsed to ~65 donors because Active Housefile OFF removed R1+R2 donors and Lapsed OFF removed R3 donors — which are most Mid-Level donors by definition.

**Two-bucket toggle semantics (REVISED 2026-04-28):**

| Toggle | Type | Field / Criteria | OFF means |
| --- | --- | --- | --- |
| Cornerstone | Group (flag) | `Cornerstone_Partner__c = true` | **EXCLUDE** — remove all flagged donors from universe before waterfall |
| Sustainer (Miracle Partner) | Group (flag) | `Miracle_Partner__c = true` | **EXCLUDE** |
| New Donor | Group (lifecycle flag) | `lifecycle_stage = "New Donor"` | **EXCLUDE** |
| Major Gift Portfolio | Group (flag) | `Staff_Manager__c` populated | **EXCLUDE** |
| Mid-Level | Group (cohort) | Mid-Level criteria (24-mo cumulative ≥ $750, gave in 24mo) | **EXCLUDE** |
| Active Housefile | RFM (lifecycle) | `R_bucket IN (R1, R2)` | **SKIP** — donor not assigned to AH segments; remains in universe |
| Lapsed | RFM (lifecycle) | `R_bucket = R3` | **SKIP** |
| Deep Lapsed | RFM (lifecycle) | `R_bucket IN (R4, R5)` | **SKIP** |

**Why this bucketing:**

- **Group (flag-style) toggles** identify a donor cohort. The operator intent is binary: that cohort is either part of this mailing or it isn't. When OFF, the donors are removed before any routing — they never end up in another segment by accident. This includes Mid-Level, which is technically composite (cumulative giving + RFM activity) but operationally is a distinct cohort that should never commingle into general housefile.
- **RFM toggles** are pure lifecycle position controls. R1–R5 buckets are exhaustive — every active or lapsed donor sits in exactly one. The OFF semantic for RFM is "don't route donors to this position"; donors fall through to the next ON position that matches them, or drop out if no ON position matches. Skip preserves the cross-cut: a Mid-Level donor who is also R1 by RFM gets routed to ML01 (Mid-Level position 4 in the waterfall, before AH at position 7), regardless of AH being ON or OFF.

**Why Mid-Level moved from SKIP to EXCLUDE (2026-04-28):** Initial spec put Mid-Level in SKIP. Bill's clarification: Mid-Level donors should never blend into general housefile. They are a distinct cohort by HRI definition (volume-defined). When Mid-Level is OFF, those donors should be excluded entirely, not silently rolled into AH or LR by RFM.

**Mid-Level Prospect (MP01) eliminated in v3.3** — sub-$750 active donors route to active housefile / lapsed RFM; the MP toggle is removed from the segment group panel. Code retained as deprecated; reinstate by adding the cohort row back here and to `waterfall_engine.GROUP_EXCLUDE_RULES`.

**Concrete examples:**

**Edge case:** if `sum mod 10 = 0`, the check digit is `0` (the formula `(10 − 0) mod 10` = 0).
- **Standard housefile mailing** (Mid-Level OFF, Major Gift OFF, Cornerstone OFF, Sustainer OFF, New Donor OFF; AH + LR + DL ON): all six flag-OFFs remove their cohorts. Universe = full eligible population minus those six cohorts. Waterfall routes the remainder to AH / LR / DL by RFM. Result: pure general housefile, no commingling, no Mid-Level donors leaking into AH. ✓
- **Mid-Level only mailing** (Mid-Level ON, Major Gift ON; Cornerstone OFF, Sustainer OFF, New Donor OFF; AH OFF, LR OFF, DL OFF): flag-OFFs exclude Cornerstone/Sustainer/New. AH/LR/DL OFFs SKIP routing (don't exclude). Waterfall position 3 (Mid-Level ON) routes Mid-Level cohort to ML01. Position 2 (MG ON) routes MG cohort to MGP01. Other R1–R5 donors fall through and drop out. Result: ~Mid-Level + MG count, no leakage. ✓
- **Cornerstone-only reactivation** (Cornerstone ON; everything else OFF): all flag-OFFs exclude their cohorts. Cornerstone routing puts flagged donors in CS01. RFM SKIP-OFFs don't matter — there's nothing left to route. ✓
- **Cross-cut**: a Cornerstone-flagged donor who is also R1 by RFM. With Cornerstone ON + AH ON: Cornerstone position 5 (or wherever in waterfall) routes them to CS01 before AH runs. They don't double-count. ✓

**Implementation order at runtime:**

1. Pull universe from BQ cache (full eligible population).
2. **Group (flag-based) exclusion pass:** for each Group-type toggle that is OFF, mark every matching donor for exclusion. Remove all marked donors from the universe.
3. Waterfall assignment runs on the post-exclusion universe. RFM OFF toggles cause that position to skip its assignment; donors flow to the next ON position they match, or fall through entirely if no ON position matches.
4. Suppression engine (Tier 1/2/3) applies on top.

**Edge case — donor matches multiple Group toggles, one ON one OFF:** Group OFF wins. Example: Cornerstone ON, Major Gift OFF, donor flagged both — donor is excluded by Major Gift OFF before Cornerstone routing runs. Operator intent matches "OFF means excluded, period."

Below the toggle panel, a **segment rules panel** shows configurable parameters pulled from the MIC Segment Rules tab. Defaults are pre-populated — Jessica only adjusts what's different about this specific campaign:

- **Recency boundaries** (default: 0–6, 7–12, 13–24, 25–36, 37–48)
- **Recent-gift suppression window** (default: 45 days — may shorten to 30 for year-end) — toggle: ON/OFF
- **Deep lapsed cutoff** (default: 48 months — may narrow to 36)
- **Ask string multipliers** (default: 1×, 1.5×, 2×)
- **Ask floor/ceiling** (default: $15 / $4,999.99)
- **Response rate floor** (default: 0.8%)
- **Frequency cap** (default: 6 solicitations/year) — toggle: ON/OFF

**Holdout (v3.4):** the global Holdout toggle is removed from this panel. Holdout is now a **per-segment column** in the Step 3 scenario editor segment table — see below. Default 5% per segment; range 0–5%; operator can dial down per segment when the trade-off is justified.

Each parameter shows the default with an edit control. Most campaigns require no changes.

### Step 3: Run Projection

Jessica clicks "Run Projection." The system executes the three-pass projection:

**Pass 1 — Full Universe.** The engine queries Salesforce (all ~50K accounts with opportunity-derived fields in one pass), applies the waterfall with all rules, and computes the complete qualified universe with no quantity cap. Results write to the **Draft tab** in the MIC. Jessica reviews the projection in Sheets — segment quantities, economics, break-even flags, and inclusion/exclusion status are all visible in the familiar spreadsheet interface. The Draft tab shows one row per segment:

| Include | Segment Code | Segment Name | Quantity | % INCL | Holdout % | Budget Fit | CPP | Total Cost | Hist. RR | Hist. Avg Gift | Proj. Rev | Net Rev | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

**Per-segment Holdout % column (v3.4):** every segment row carries a `Holdout %` column. Default 5; range 0–5; integer step. Operator can dial down per segment when the trade-off is justified (e.g., small mid-level cohort where 5% costs ~50 mailable donors). The 5% upper bound is a guardrail — preserves the measurement-infrastructure cap. A 0 means no holdout for that segment; the rule simply doesn't fire on that row. UI shows a soft warning ("low holdout reduces ROI measurement power for this segment") when the value drops below 3.
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
| --- | --- | --- | --- | --- | --- |
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
| --- | --- | --- |
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

**Donor-level suppression fields (Bekah review 2026-04-28 — see §6.2.1 for full per-rule semantics; Tier 3 deleted in v3.3):**

| Tier | Field API Name | Label |
| --- | --- | --- |
| 1 (Hard) | `npsp__All_Members_Deceased__c` | All Household Members Deceased |
| 1 (Hard) | `Do_Not_Contact__c` | Do Not Contact At All |
| 1 (Hard) | `Type` | Account.Type ∈ {`Donor Advised Fund`, `Government`} → suppress |
| 1 (Hard) | `RecordType.Name` | Account Record Type ∈ {`ALM Foundation Organization`, `ALM Grants/Partners Household`, `ALM Grants/Partners Organization`} → suppress |
| 1 (Hard) | Blank address check (computed) | BillingStreet/City/PostalCode null or empty |
| 1.5 (Pre-emptive) | `Lifecycle_Stage__c == "New Donor"` | Hard pre-emption above the waterfall — see §6.1 |
| 2 (Pref, default ON) | `No_Mail_Code__c` | No Mail Code (toggleable; flip OFF in rare authorized cases) |
| 2 (Pref, default ON) | `Major_Donor_In_House__c` | Major Donor In-House (renamed from `TLC_Donor_Segmentation__c`; see §5.5.1) |
| 2 (Pref) | `Newsletters_Only__c` | Newsletters Only (conditional: include in newsletter campaigns) |
| 2 (Pref) | `Match_Only__c` | Match Only (include in match campaigns only) |
| 2 (Pref) | `X1_Mailing_Xmas_Catalog__c` | 1 Mailing Xmas Catalog (annual frequency cap) |
| 2 (Pref) | `X2_Mailings_Xmas_Appeal__c` | 2 Mailings Xmas/Easter (annual frequency cap) |

**Removed from Tier 1 in v3.3:** `npsp__Undeliverable_Address__c` and `NCOA_Deceased_Processing__c` (already removed 2026-04-21 — Faircom processor handles); `No_Mail_Code__c` (moved to Tier 2 as toggleable always-on); `Primary_Contact_is_Deceased__c` (redundant with `npsp__All_Members_Deceased__c`).

**Removed from Tier 2 in v3.3:** `Newsletter_and_Prospectus_Only__c` (Bekah consolidating in SF to `Newsletters_Only__c`); `No_Name_Sharing__c` (acquisition co-op only, not DM); `Address_Unknown__c` (covered by Tier 1 blank-address); `Not_Deliverable__c` (NCOA/Faircom handles).

**Tier 3 deleted in v3.3.** The 14 legacy/rare fields previously listed are no longer used for suppression. Christmas frequency-cap fields (`X1_Mailing_Xmas_Catalog__c`, `X2_Mailings_Xmas_Appeal__c`) promoted to Tier 2.

**Fields requiring Opportunity-level query:**

| Derived Field | Computation | Source |
| --- | --- | --- |
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
| --- | --- |
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
| --- | --- |
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
| --- | --- |
| R1 | 0–6 months |
| R2 | 7–12 months |
| R3 | 13–24 months |
| R4 | 25–36 months |
| R5 | 37–48 months |

**Frequency** (rolling 5-year window):

| Bucket | Range |
| --- | --- |
| F1 | 5+ gifts |
| F2 | 3–4 gifts |
| F3 | 2 gifts |
| F4 | 1 gift |

**Monetary** (average gift over 5-year lookback — research finding: average gift predicts response better than HPC):

| Bucket | Range |
| --- | --- |
| M1 | $100+ |
| M2 | $50–$99.99 |
| M3 | $25–$49.99 |
| M4 | $10–$24.99 |
| M5 | Under $10 |

**RFM weighting for DM:** R×3, F×2, M×1 (configurable per campaign type).

### 5.4 Giving Tier Segments

| Segment | Criteria | Package Treatment |
| --- | --- | --- |
| **Mid-Level (ML01)** | 1+ LTG, gave in last 24 months, **24-month cumulative giving ≥ $750** (no upper cap). Donors with `Staff_Manager__c` populated route to MJ01 first (waterfall position 2), so portfolio assignment naturally excludes them from ML01. | High-touch: better paper, first-class postage, invitation envelope |
| **Active Housefile** | Gave in last 12 months, 24-month cumulative < $750 | Standard DM package |
| **Lapsed Housefile** | Last gift 13–24 months, 2+ lifetime gifts, $10+ cumulative | Standard DM with lapsed messaging |
| **Deep Lapsed** | Last gift 25–48 months, $10+ cumulative | Selective — only when break-even supports it |
| **Cornerstone** | `Cornerstone_Partner__c = true` (flag maintained externally) | Legacy ALM branding package, distinct PackageCode |
| **Sustainer (Miracle Partners)** | `Miracle_Partner__c = true`. **Wins over Cornerstone:** when a donor is flagged both Sustainer and Cornerstone, Sustainer treatment governs. With sustainer toggle default OFF, Miracle Partners are removed from the universe before any other waterfall assignment; with sustainer toggle ON, they assign to SU01 at waterfall position 4 (which fires before CS01 at position 5). | Suppressed from general; year-end + emergency override |
| **Mid-Level Prospect (MP01)** | **Eliminated in v3.3.** Sub-$750 active donors route to active housefile / lapsed RFM positions. Code `MP01` retained in the registry as deprecated; not assigned by the engine. To reinstate, add the cohort definition back to `waterfall_engine.GROUP_EXCLUDE_RULES` and the assignment block. | n/a |

**Research-driven changes from TLC baseline:**

| Decision | TLC Baseline | HRI Decision | Rationale |
| --- | --- | --- | --- |
| Active/lapsed boundary | 24 months | **12 months** | 13–24mo responds at lapsed rates. Earlier intervention. |
| Mid-level entry | $500 lifetime cumulative + no DM $500 | **$750 cumulative over last 24 months, no upper cap** (v3.3) | Bill 2026-04-28: split-the-difference floor between TLC's $500 and prior $1,000 spec; lifting the cap captures non-portfolio donors with $5K+ recent giving (44 donors at last refresh). 24-month cumulative (not lifetime) aligns with TLC's historical baseline math (TLC ran every prior HRI campaign; Faircom takes over May 1, 2026). |
| Mid-Level Prospect tier | Separate $500–$999.99 cohort | **Eliminated** (v3.3) | Sub-$750 active donors route to active housefile / lapsed RFM. Reduces operator decision surface; can be reinstated if Erica/Jessica need the prospect distinction for ask-string or package routing. |
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

### 5.5.1 Major Donor In-House Suppression (renamed v3.3)

The Salesforce field `TLC_Donor_Segmentation__c` was renamed to `Major_Donor_In_House__c` on 2026-04-28 (architect-executed via Tooling API). Picklist reduced from three values (`Major - In House`, `Mid - TLC`, blank) to two:

- **`Major - In House`** — currently 166–174 records (live count drifts as Bekah works). This is the existing value, kept as-is to avoid migrating ~174 records' data.
- **blank** — all other accounts.

The 574 records previously flagged `Mid - TLC` were cleared to null and the `Mid - TLC` value was removed from the picklist. If Bekah later renames the active value to `In House` (shorter; matches the field name), no code change is required — the builder's suppression logic is whitespace-stripped and matches both forms.

**Semantic shift:** previously this field was a label TLC was supposed to honor in their segmentation but did not act on consistently. v3.3 promotes it to a **Tier 2 always-on suppression toggle** (default ON). Donors flagged in-house are suppressed from any mailing where a Major Gift Portfolio segment is assigned — they are managed in-house by Erica via portfolio reports, not via direct mail.

**Toggle semantics:**
- **Default ON (suppress):** flagged donors removed from the mailable universe even if they otherwise qualify for Major Gift Portfolio (MJ01) or any RFM position.
- **OFF (include):** flagged donors flow through the waterfall normally. Used only when an in-house-only mailing is run (rare). When OFF, the campaign uses the **N campaign prefix** (see §9.1) so the in-house cohort is routed to a distinct file/segment.

**Implementation note (forward-compatible match):** the builder's suppression code matches the in-house flag with case-insensitive whitespace-stripped comparison (e.g., `value.strip().lower() in {"major - in house", "in house", "major-in-house"}`) so that any future label cleanup by Bekah will not require a code change.

**Migration risk:** the SF field rename invalidates any external query that references `TLC_Donor_Segmentation__c`. Verified 2026-04-28 with operator: no HRI repos reference the field. The rename was executed with no consumer breakage — 9 SF page-layout dependencies auto-updated to the new API name.

---

## 6. Waterfall Assignment Logic

### 6.1 Priority Hierarchy

```
1. GLOBAL SUPPRESSION — TIER 1 (removed entirely, always ON)
   ├── All Household Members Deceased (npsp__All_Members_Deceased__c)
   ├── Do Not Contact At All (Do_Not_Contact__c)
   ├── Account.Type ∈ {Donor Advised Fund, Government}                    [v3.3]
   ├── Account.RecordType.Name ∈ {ALM Foundation Organization,
   │                              ALM Grants/Partners Household,
   │                              ALM Grants/Partners Organization}        [v3.3]
   └── Blank address (BillingStreet/City/PostalCode null or empty)

   Notes:
   • npsp__Undeliverable_Address__c and NCOA_Deceased_Processing__c removed 2026-04-21
     (Faircom processor handles).
   • No_Mail_Code__c moved to Tier 2 always-on toggle (was Tier 1) — v3.3.
   • Type / RecordType suppression added 2026-04-28 (Bekah). Existing RFM filters were
     already catching DAF/Govt/ALM-org records; this is defense-in-depth for
     cornerstone-only and other RFM-bypassing flows.

1.5 NEW DONOR WELCOME PRE-EMPTION                                         [v3.3]
   └── If Lifecycle_Stage__c == "New Donor" (first gift within 90-day welcome window):
       Default behavior: suppress from any non-welcome appeal.
       Runs BEFORE the cornerstone/major-portfolio/RFM waterfall — first-match-wins
       does not override this.
       Reverse: when running the welcome series itself, all other waterfall toggles
       are turned OFF and the new-donor flag is the sole include criterion. The
       welcome series is materialized via the New Donor Welcome workflow (separate
       from the standard waterfall), not via Position 6 of this hierarchy.

2. TIER 2 DONOR-LEVEL SUPPRESSIONS (default ON, toggleable)
   ├── No_Mail_Code__c                     (always-on toggle)             [v3.3]
   ├── Major_Donor_In_House__c             (always-on toggle; renamed
   │                                        from TLC_Donor_Segmentation__c) [v3.3]
   ├── Newsletters_Only__c                 (campaign-type conditional)
   ├── Match_Only__c                       (campaign-type conditional)
   ├── X1_Mailing_Xmas_Catalog__c          (frequency conditional)
   └── X2_Mailings_Xmas_Appeal__c          (frequency conditional)
   See §6.2.1 for per-rule semantics.

3. MAJOR GIFT PORTFOLIO [TOGGLE-GATED]
   └── Staff_Manager__c populated → assigned to MJ01.
       Default ON: included as own segment with M-prefix campaign code.
       Toggle OFF: donors skip this position (no fall-through to RFM, since
       portfolio donors are managed in-house and would otherwise be in-house-flagged).

4. MID-LEVEL (1+ LTG, gave in 24 months, 24-month cumulative ≥ $750) [TOGGLE-GATED]   [v3.3]
   └── Assigned to ML01.
       No upper cap. Donors with Staff_Manager__c populated already routed to
       MJ01 at position 3 above; they never reach ML01.

5. MONTHLY SUSTAINERS (Miracle Partners) [TOGGLE-GATED]
   └── Default OFF — Miracle Partners removed from universe entirely.
       Toggle ON: assigned to SU01 at this position. Wins over Cornerstone (position 6).
       Year-end / emergency override use case.

6. CORNERSTONE PARTNERS (Cornerstone_Partner__c = true) [TOGGLE-GATED]
   └── Flag maintained externally via Cornerstone triage query.
       Default ON: assigned to CS01.
       Toggle OFF: flag ignored, donors fall through to natural waterfall position.
       Sustainer at position 5 takes precedence — Cornerstone-AND-Sustainer donors
       route to SU01 (or are removed if sustainer toggle OFF).

7. ACTIVE HOUSEFILE — HIGH VALUE (R1-R2, F1-F2, M1-M2) [TOGGLE-GATED]

8. ACTIVE HOUSEFILE — STANDARD (R1-R2, remaining) [TOGGLE-GATED]

9. LAPSED RECENT (R3: 13–24 months, 2+ lifetime gifts) [TOGGLE-GATED]

10. DEEP LAPSED (R4-R5: 25–48 months) [TOGGLE-GATED]
    └── Include only when break-even positive
        Sub-segment by cumulative giving tier

11. CBNC FLAG OVERRIDE
    └── Donors with 2+ gifts in non-consecutive years over 10-year window
        who would otherwise be suppressed by lapsed cutoffs
        → Include in Lapsed Recent regardless of recency

ELIMINATED in v3.3:
   • Mid-Level Prospect (was position 9, $500–$999.99). Sub-$750 active donors
     route to active housefile / lapsed RFM. MP01 code retained as deprecated.
   • New Donor as a waterfall position (was position 6). Promoted to Tier 1.5
     pre-emption (above the waterfall).
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
| --- | --- | --- | --- |
| 1 | All Household Members Deceased | `npsp__All_Members_Deceased__c = true` | Only deceased flag used for suppression. One-contact-deceased does NOT suppress — surviving spouse keeps receiving mail. |
| 2 | Do Not Contact | `Do_Not_Contact__c = true` | Explicit donor opt-out |
| 3 | Account Type — DAF / Government | `Type IN ('Donor Advised Fund', 'Government')` | v3.3. Non-mailable account categories. RFM filters were catching these naturally; explicit suppression is defense-in-depth for cornerstone-only and any future flow that bypasses RFM. |
| 4 | Account Record Type — ALM organizations | `RecordType.Name IN ('ALM Foundation Organization', 'ALM Grants/Partners Household', 'ALM Grants/Partners Organization')` | v3.3. Same rationale as #3. Most ALM-org records have stale or program-specific giving history that doesn't belong in DM. |
| 5 | Blank Address | `BillingStreet IS NULL OR BillingCity IS NULL OR BillingPostalCode IS NULL` | Computed at runtime. This is how TLC actually suppressed — on blank address fields, not the `Address_Unknown__c` checkbox. |

**Removed from Tier 1 in v3.3:**
- `npsp__Undeliverable_Address__c` and `NCOA_Deceased_Processing__c` — already removed 2026-04-21 (Faircom processor handles).
- `No_Mail_Code__c` — moved to Tier 2 as a toggleable always-on suppression. Rationale: there are rare authorized cases (events, cornerstone exceptions) where the operator legitimately ignores no-mail. Always-on default preserves safety; the toggle adds operator override capability.
- `Primary_Contact_is_Deceased__c` (was Tier 3) — redundant with `npsp__All_Members_Deceased__c`. The household-level deceased flag is the canonical source; one-contact-deceased should not suppress mail to the surviving spouse.

**Tier 1.5 — New Donor Welcome Pre-emption (default: ON)** *(v3.3)*

Promoted from the GROUP_EXCLUDE waterfall bucket to a hard pre-emption that runs above the waterfall. Rationale: a donor in the 90-day welcome window should never receive a non-welcome appeal regardless of cornerstone / major-portfolio / RFM status — first-match-wins shouldn't be able to override the welcome stream.

| Rule | Field / Logic | Default | Behavior |
| --- | --- | --- | --- |
| New Donor Welcome | `Lifecycle_Stage__c = 'New Donor'` | ON (suppress) | Donor removed from any non-welcome appeal. Engineering note: implementation moves this rule out of `waterfall_engine.GROUP_EXCLUDE_RULES` and into a pre-waterfall suppression pass between Tier 1 and Major Gift Portfolio assignment. |

When the welcome series itself runs, the operator turns all other GROUP_EXCLUDE / RFM toggles OFF and the welcome flag becomes the sole include criterion. The welcome series is materialized as a separate workflow (campaign type = `Newsletter` or `Welcome`), not via this position.

**Tier 2 — Communication Preference Suppressions (default: ON, toggleable per campaign)**

| # | Rule | Field | Default | Conditional Logic |
| --- | --- | --- | --- | --- |
| 1 | No Mail Code | `No_Mail_Code__c` | ON (always-on toggle) | v3.3. Was Tier 1; moved here to allow rare authorized override (events, cornerstone exceptions). Default ON preserves safety. |
| 2 | Major Donor In-House | `Major_Donor_In_House__c` | ON (always-on toggle) | v3.3. Renamed from `TLC_Donor_Segmentation__c`. Suppresses in-house major donors from any standard mailing; they are managed via portfolio reports. Toggle OFF only when running an N-prefix in-house-only campaign (see §9.1). |
| 3 | Newsletters Only | `Newsletters_Only__c` | ON | **Campaign-type conditional:** Suppress from appeals; INCLUDE when campaign type = Newsletter. Bekah is consolidating `Newsletter_and_Prospectus_Only__c` into this single flag in SF. |
| 4 | Match Only | `Match_Only__c` | ON | **Campaign-type conditional:** Suppress from standard appeals; INCLUDE only when campaign type = Match. |
| 5 | 1 Mailing Xmas Catalog | `X1_Mailing_Xmas_Catalog__c` | ON | **Frequency conditional:** Donor limited to 1 Christmas mailing per FY. Builder tracks mailing count for flagged donors and suppresses once limit is reached. Promoted from Tier 3 in v3.3. |
| 6 | 2 Mailings Xmas/Easter | `X2_Mailings_Xmas_Appeal__c` | ON | **Frequency conditional:** Donor limited to 2 mailings per FY (Christmas + Easter). Promoted from Tier 3 in v3.3. |

**Removed from Tier 2 in v3.3:**
- `Newsletter_and_Prospectus_Only__c` — Bekah consolidating the underlying SF picklist value into `Newsletters_Only__c`. After Bekah's cleanup, this rule has no consumers.
- `No_Name_Sharing__c` — used for list-exchange/acquisition co-op only, not direct mail suppression. Bekah confirmed.
- `Address_Unknown__c` — covered by Tier 1 blank-address check.
- `Not_Deliverable__c` — NCOA / Faircom handles undeliverable hygiene at processor layer.

**Implementation requirements for Tier 2 conditionals:**

- **Campaign-type awareness (items 3, 4):** The MIC Campaign Calendar must have a `campaign_type` field (or equivalent) that the builder reads. Values include at minimum: `Appeal`, `Newsletter`, `Match`, `Catalog`. When the campaign type matches the donor's preference, the suppression is skipped and the donor is included.
- **Mailing history tracking (items 5, 6):** The builder must query how many mailings a flagged donor has already received in the current FY. This can be derived from Campaign_Segment__c records loaded by prior runs. If count ≥ limit, suppress.

**Tier 3 — DELETED (v3.3)**

The 14 legacy/rare fields previously in Tier 3 are no longer used for suppression. Two fields previously here were promoted/redirected:

- `X1_Mailing_Xmas_Catalog__c` and `X2_Mailings_Xmas_Appeal__c` → promoted to Tier 2 (frequency caps).
- `Primary_Contact_is_Deceased__c` → not used; redundant with the household deceased flag.

The other 12 fields (`No_Currency_Mailers__c`, `No_Gifts_or_Premiums__c`, `No_Planned_Giving_Mail__c`, `Address_Unknown__c`, `Not_Deliverable__c`, `No_Donor_Elite__c`, `No_Vaccine_Solicitations__c`, `Prospectus_Only__c`, `No_Presidents_Gathering__c`, `No_Presidents_Retreat__c`, `Banner_Ad_Constituent__c`, `MC_Only_Account__c`, `Mid_Level_Review__c`) are not active in current operations and not specced for suppression behavior. Per Bill 2026-04-28: too many toggles is not a feature — the cereal-aisle problem.

#### 6.2.2 Segment-Level Suppression (Applied After Waterfall Assignment)

These rules operate on the segmented universe after donors have been assigned to waterfall positions. All toggleable rules can be enabled or disabled per campaign. Defaults are set in the MIC Segment Rules tab. Disabled rules are skipped entirely; they do not suppress any records. This toggle architecture accommodates future rules — new rules are added as new toggle rows in the Segment Rules tab without code changes to the engine.

| Rule | Default | Default State | Notes |
| --- | --- | --- | --- |
| Recent-gift window | 45 days *(spec'd, not built — v3.3)* | **Toggle: OFF + no implementation** | The toggle and parameter (`recent_gift_window_days`) exist in `suppression_engine.py` defaults, but no code path consumes them today. The Reference UI text shows "21 days" — both numbers are inert. Remains unbuilt pending Faircom guidance (Bill 2026-04-28): mail-pull-to-mail-drop timing varies enough that a fixed window may either suppress people whose receipt has cleared or fail to catch the actual overlap. Implementation, value, and toggle wiring will be specced together once Faircom confirms their assumptions. |
| Break-even floor | CPP ÷ Avg Gift | **Always active** | Auto-suppress segments below floor for 3+ consecutive campaigns |
| Response rate floor | 0.8% | **Always active** | TLC-era rule (August memo). Starting point pending Campaign Scorecard segment-level data. |
| Frequency cap | 6 solicitations/year | **Toggle: OFF (first 2 campaigns)** | Provisional default — no internal or external benchmark. Ships disabled. Enable after VeraData calibration provides benchmarked value. Tracks cumulative mailings per donor per FY. **Currently a no-op** — `frequency` field is not populated, so even if toggle is flipped ON the rule has nothing to gate on. |
| Holdout percentage | 5% per segment, **per-segment configurable** (v3.4) | **Per-segment column** in scenario editor; default 5; range 0–5; integer | Random sample retained for ROI measurement. Replaces the global ON/OFF toggle. Operator can reduce per segment when the trade-off is justified; cap at 5% prevents over-holding. UI shows soft warning when row value < 3% ("low holdout reduces ROI measurement power"). When value = 0, the holdout rule does not fire on that segment. |

**Suppression audit log:** Every suppression action — both donor-level and segment-level — is logged with donor ID (or Account ID), rule triggered, tier, and campaign ID. The raw audit log is written to a **Google Drive CSV file** (one file per run, archived alongside the output files in the designated folder). The MIC never holds donor-level suppression data. Aggregate suppression counts by rule are surfaced in the Draft tab and the Apps Script UI summary (e.g., "Tier 1 Deceased: 340. Tier 2 Newsletter Only: 1,200. Recent Gift: 800. Frequency Cap: 340."). If total suppression exceeds 15% of the pre-suppression universe, the system flags for review in the UI.

---

## 7. Budget-Target Fitting

### 7.1 Campaign Configuration (from MIC)

When a campaign is selected, the system reads from the MIC Campaign Calendar row:

| MIC Field | Segmentation Builder Use |
| --- | --- |
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
| --- | --- |
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
| --- | --- | --- |
| Active + Mid-Level | HPC | Best gift reflects demonstrated capacity |
| Lapsed + Deep Lapsed | MRC | HPC may be stale; MRC is more realistic anchor |
| Cornerstone | HPC | High-value reactivation — anchor to demonstrated giving |
| New Donor | First gift amount | Only data point available |

### 8.2 Ask Array Formula

Standard: `[1× basis, 1.5× basis, 2× basis, "Best Gift of ``````$<HPC>"]`

| Parameter | Default | Configurable |
| --- | --- | --- |
| Minimum ask (floor) | $15 | Yes |
| Maximum ask (ceiling) | $4,999.99 | Yes |
| Multipliers | 1×, 1.5×, 2× | Yes |
| Rounding | Nearest $5 below $100; nearest $25 above $100 | Yes |
| Fallback ladder (floor collapse) | $15 / $25 / $35 | Yes |

**Rounding direction:** Always round up to the next increment. A computed ask of $22.50 rounds to $25, not $20. Rounding down an ask amount is never correct in fundraising context.

**Floor-collapse fallback (REQUIRED):** When the donor's basis is small enough that `basis × multiplier < floor` for ANY of the three tiers, the entire ladder is replaced with the fallback `$15 / $25 / $35`, applied as a unit. The engine MUST NOT re-floor each tier independently — that produces non-monotonic ladders like `15 / 20 / 15` (observed in A2651 production output for 1,587 lapsed donors with sub-$5 basis). The ladder must always be monotonically increasing.

**AskAmountLabel — BLANK, not prepopulated (REVISED 2026-04-27):** The reply device's "Best Gift of $***" line is a fill-in for the donor, not a prepopulated number. Verified against TLC's production mid-level files. The CSV column \******`AskAmountLabel`*****\* is therefore left empty — the lettershop template renders the static "Best Gift of $***" label with a blank fill-in line.

Earlier spec versions populated this field with LastGift or HPC; both were wrong. Donors complete the field themselves; HRI does not anchor it. The column stays in the schema (so column count is stable across campaigns), but it is always empty in the CSV.

**Mid-Level and Major segments — ask arrays REQUIRED, never blank:** All segments classified as Mid-Level (ML*, MJ*, MP* prefixes) or Major (Staff_Manager populated) MUST have populated ask arrays in the Print and Matchback files. The previous behavior of leaving MJ01 blank (because high-value donors get personal-note treatment) is incorrect — even personal-note packages need ask arrays so the reply device renders correctly. The lettershop template, not the builder output, decides whether to print the asks; the builder always supplies them. Major Gift Portfolio donors routed to the custom-package waterfall position #2 also receive ask arrays (lettershop suppresses display per their template).

### 8.3 California Versioning

Boolean `CA_Version` flag on Printer File for California addresses when campaign is configured as 33x Shipping match. Not applied to all campaigns.

### 8.4 Reply Copy Tier

| Tier | Criteria | Copy Template Key |
| --- | --- | --- |
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
| --- | --- | --- |
| 1 | Program | A = Active Housefile, M = Mid-Level + Major Gift Portfolio, C = Cornerstone, **N = In-House Major Donor** *(v3.3)*. Legacy R-prefix (Renewal/Housefile) maps to A. **No J-prefix** — earlier interpretation in v25/v26 was incorrect; "MJ" inside an M-prefix campaign is the segment portion (Major Gift Portfolio segment), not a campaign prefix. |
| 2–3 | Fiscal Year | 26, 27 |
| 4–5 | Campaign | 01–12 (monthly), NL = Newsletter |
| 6–9 | Segment | 4-character persistent code (AH01, ML01, LR01, CS01, etc.) |
| 10–12 | Package | P01, P02 — persistent per creative treatment |
| 13–15 | Test/Control | CTL, TSA, TSB |

**Cohort-prefix routing rules (v3.3 — code lives in `config.COHORT_PREFIX_RULES`):**

| Cohort | Campaign prefix | Triggered when |
| --- | --- | --- |
| Active Housefile, Cornerstone, Sustainer, CBNC | A | Standard housefile mailings (default) |
| Mid-Level, Major Gift Portfolio | M | Mid-Level toggle ON or Major Gift Portfolio toggle ON |
| **In-House Major Donor** | **N** | Major_Donor_In_House__c suppression toggle is OFF (rare — running an in-house-only mailing). Routed to a distinct file/segment. |
| Cornerstone-only test | C | Cornerstone toggle ON, all other toggles OFF (e.g., A2643 selection postcards) |

**Removed:** the J-prefix rule that v25/v26 had wired for "in-house mailings" was a misinterpretation — there is no J campaign prefix. Replace any J-prefix rules in `config.COHORT_PREFIX_RULES` with N-prefix for in-house and remove the J entry entirely.

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
| --- | --- |
| AH01 | Active 0–6mo, $50+ avg |
| AH02 | Active 0–6mo, $25–$49.99 avg |
| AH03 | Active 0–6mo, under $25 avg |
| AH04 | Active 7–12mo, $50+ avg |
| AH05 | Active 7–12mo, $25–$49.99 avg |
| AH06 | Active 7–12mo, under $25 avg |
| ML01 | Mid-Level (24-month cumulative ≥ $750, no upper cap) — *v3.3 redefinition* |
| MP01 | Mid-Level Prospect — **DEPRECATED v3.3.** Code retained in registry; not assigned by the engine. Sub-$750 active donors route to active housefile / lapsed RFM. |
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
| --- | --- | --- | --- |
| 1 | DonorID | Text(9) | `Constituent_Id__c`, zero-padded to 9 digits |
| 2 | CampaignAppealCode | Text(9) | 9-character appeal code in `TYYMCPSS0` format |
| 3 | Scanline | Text | ALM scanline format. Computed per algorithm in §10.1.1. Printed on outer envelope and reply device. |
| 4 | PackageCode | Text(3) | Routes creative version at lettershop |
| 5 | Addressee | Text | `npo02__Formal_Greeting__c` from Account. Full addressing name (e.g., "Mr. and Mrs. Brian Schwanbeck"). Per VeraData requirement — this is the name printed on the envelope. |
| 6 | Salutation | Text | `npo02__Informal_Greeting__c` from Account. Drives **letter body** content (e.g., "Dear Benjamin,"). Distinct from Addressee (column 5) which uses `npo02__Formal_Greeting__c` and drives envelope addressing. A2643 production output had this column blank — bug, wrong source field name in original spec. Corrected 2026-04-27. |
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

#### 10.1.1 ALM Scanline Format and Check Digit Algorithm

**Format:** `<DonorID> <CampaignAppealCode> <CheckDigit>`

- **DonorID:** 9 characters. Numeric IDs zero-padded left to 9 digits (e.g., `70264965` → `070264965`). S-prefixed IDs (`S00xxxxxx`) are already 9 characters; pass through unchanged. Prospect IDs are 9 digits starting with `3`.
- **Single space** separator
- **CampaignAppealCode:** 9 characters, `TYYMCPSS0` format
- **Single space** separator
- **CheckDigit:** 1 numeric character, computed per the algorithm below.

Total length: 21 characters (9 + 1 + 9 + 1 + 1).

Example: `070122327 W16B1AJ30 6`

**Check digit algorithm (7 steps):**

Operates on the concatenated 18-character string of `DonorID + CampaignAppealCode` (no spaces, no check digit).

**Step 1.** Treat the 18-character scanline-without-check-digit as 18 individual characters.

**Step 2.** Replace alphabetical characters with their conversion-table values. Numeric characters retain their value. Conversion table:

| Char | Value |  | Char | Value |  | Char | Value |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 1 |  | J | 1 |  | S | 2 |
| B | 2 |  | K | 2 |  | T | 3 |
| C | 3 |  | L | 3 |  | U | 4 |
| D | 4 |  | M | 4 |  | V | 5 |
| E | 5 |  | N | 5 |  | W | 6 |
| F | 6 |  | O | 6 |  | X | 7 |
| G | 7 |  | P | 7 |  | Y | 8 |
| H | 8 |  | Q | 8 |  | Z | 9 |
| I | 9 |  | R | 9 |  |  |  |

Numerics: `0`→0, `1`→1, …, `9`→9.

**Step 3.** Assign alternating weights `1, 2, 1, 2, 1, 2, …` across the 18 positions (position 1 → 1, position 2 → 2, position 3 → 1, etc.).

**Step 4.** Multiply each Step-2 value by its Step-3 weight. Result is 18 products.

**Step 5.** For each product: if the product is greater than 9, subtract 9. Otherwise keep as-is. Result is 18 single-digit values.

**Step 6.** Sum the 18 values from Step 5.

**Step 7.** Compute `CheckDigit = (10 - (sum mod 10)) mod 10`. Single-digit result, 0–9.

**Worked example 1** — scanline `070122327W16B1AJ30`:
- Step 2: `0,7,0,1,2,2,3,2,7, 6,1,6,2,1,1,1,3,0`
- Step 3: `1,2,1,2,1,2,1,2,1, 2,1,2,1,2,1,2,1,2`
- Step 4: `0,14,0,2,2,4,3,4,7, 12,1,12,2,2,1,2,3,0`
- Step 5: `0,5,0,2,2,4,3,4,7, 3,1,3,2,2,1,2,3,0`
- Step 6: sum = 44
- Step 7: (10 − (44 mod 10)) mod 10 = (10 − 4) mod 10 = **6**
- Full scanline: `070122327 W16B1AJ30 6`

**Worked example 2** — scanline `010050933M2042AH70`:
- Step 2: `0,1,0,0,5,0,9,3,3, 4,2,0,4,2,1,8,7,0`
- Step 4: `0,2,0,0,5,0,9,6,3, 8,2,0,4,4,1,16,7,0`
- Step 5: `0,2,0,0,5,0,9,6,3, 8,2,0,4,4,1,7,7,0`
- Step 6: sum = 58
- Step 7: (10 − 8) mod 10 = **2**
- Full scanline: `010050933 M2042AH70 2`

**Worked example 3** — scanline `000016196A2631AH60`:
- Step 2: `0,0,0,0,1,6,1,9,6, 1,2,6,3,1,1,8,6,0`
- Step 4: `0,0,0,0,1,12,1,18,6, 2,2,12,3,2,1,16,6,0`
- Step 5: `0,0,0,0,1,3,1,9,6, 2,2,3,3,2,1,7,6,0`
- Step 6: sum = 46
- Step 7: (10 − 6) mod 10 = **4**
- Full scanline: `000016196 A2631AH60 4`


**Matchback row scope (REVISED 2026-04-27):** The Matchback File contains **only mailed donors + holdouts**, NOT donors excluded by Pass 2 budget trim or data quality. Specifically:

- Include rows where `Holdout=False AND ExclusionReason=""` (mailed, in Print File).
- Include rows where `Holdout=True` regardless of ExclusionReason (5% control group, used for ROI measurement — kept even if also flagged for quantity_reduction).
- **Exclude rows where \****`Holdout=False AND ExclusionReason != ""`** — these are donors trimmed by Pass 2 (`quantity_reduction`), donors with bad IDs (`missing_constituent_id`, `duplicate_constituent_id`), or other data-quality exclusions. They were not mailed, are not part of the control group, and have no role in matchback or attribution.

Earlier behavior emitted all 48,565 rows for A2651 (mailed + holdouts + 8,932 quantity_reduction trims). New rule emits ~39,624 (37,197 mailed + 2,427 holdouts including 477 in trim-overlap). The trimmed rows are still recorded in the suppression audit log (separate file) for audit purposes — the Matchback is for matchback, not for trim accounting.

### 10.2 Internal Matchback File (stays on HRI's side)

CSV, UTF-8, comma-delimited, double-quote qualifier. One row per donor.
Filename: `HRI_[CampaignCode]_[Lane]_MATCHBACK_[YYYYMMDD].csv`
Stored in designated Google Drive folder. Never transmitted to agency.

This file contains everything — the full 15-character appeal code, all segment detail, and all analyst fields. When caging returns a response file with donor IDs, HRI joins against this file to recover segment-level performance data.

| # | Field | Type | Notes |
| --- | --- | --- | --- |
| 1 | DonorID | Text(9) | `Constituent_Id__c`, zero-padded to 9 digits |
| 2 | CampaignAppealCode | Text(9) | 9-char appeal code (matches Printer File) |
| 3 | Scanline | Text(21) | Full ALM scanline matching Printer File. Required for Aegis gift-matchback (Aegis matches incoming gifts on full scanline, not DonorID alone). Computed per §10.1.1. |
| 4 | InternalAppealCode | Text(15) | 15-char code with segment × package × test granularity |
| (last) | Account_CASESAFEID | Text(18) | `Account.Account_CASESAFEID__c` formula field (returns 18-char case-safe SF Account Id). Sourced via SOQL keyed on `Constituent_Id__c`. Use HRI's documented formula field (not raw `Account.Id`) so the column lineage matches HRI's SF schema vocabulary. Distinct from `Constituent_Id__c` (HRI donor account number, in the DonorID column) — this is the SF technical cross-reference. Added to Matchback only — not Print. |
| 4 | SegmentCode | Text(4) | AH01, ML01, CS01, etc. |
| 5 | SegmentName | Text | Human-readable segment label |
| 6 | PackageCode | Text(3) | P01, P02, etc. |
| 7 | TestFlag | Text(3) | CTL, TSA, TSB |
| 8 | Addressee | Text | `npo02__Formal_Greeting__c` |
| 9 | Salutation | Text | `Special_Salutation__c` |
| 10 | FirstName | Text |  |
| 11 | LastName | Text |  |
| 11 | Address1 | Text |  |
| 12 | Address2 | Text |  |
| 13 | City | Text |  |
| 14 | State | Text(2) |  |
| 15 | ZIP | Text(10) |  |
| 16 | Country | Text |  |
| 17 | AskAmount1 | Currency |  |
| 18 | AskAmount2 | Currency |  |
| 19 | AskAmount3 | Currency |  |
| 20 | AskAmountLabel | Text |  |
| 21 | ReplyCopyTier | Text |  |
| 22 | LastGiftAmount | Currency |  |
| 23 | LastGiftDate | Date |  |
| 24 | CurrentFYGiving | Currency |  |
| 25 | PriorFYGiving | Currency |  |
| 26 | CumulativeGiving | Currency | Lifetime total |
| 27 | LifecycleStage | Text | New / 2nd Year / Multi-Year / Reactivated / Lapsed / Deep Lapsed / Expired |
| 28 | CAVersion | Boolean |  |
| 29 | CornerstoneFlag | Boolean |  |
| 30 | Email | Text | Future multi-channel |
| 31 | SustainerFlag | Boolean |  |
| 32 | GiftCount12Mo | Integer |  |
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
| --- | --- | --- |
| Salesforce API access | Available | Cannot proceed |
| MIC Google Sheet | Exists (914 rows, FY20–FY26) | Must add Draft, Segment Detail, Budget Summary, Segment Rules tabs |
| Campaign Scorecard v26 | Deployed | Pro forma uses manual estimates until Scorecard data available by new segment codes |
| Appeal Index | Available | Appeal code campaign codes reference |
| VeraData onboarding | In progress, May start | Output format must be validated before Phase 5 gate |
| Campaign_Segment__c | Exists in Salesforce | No blocker |
| Google Drive output folder | Confirmed: `1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC` | No blocker |

---

## 14. Open Items for Build Sessions

1. **~~Verify HPC/MRC field availability.~~** **RESOLVED.** HPC = `npo02__LargestAmount__c` (Account rollup, Currency). MRC = `npo02__LastOppAmount__c` (Account rollup, Currency). Both are NPSP-maintained rollups on Account. Phase 1 diagnostic validates data quality (populated, not stale).

2. **~~Sustainer identification field.~~** **RESOLVED.** `Miracle_Partner__c` on Account (Checkbox). This is the toggle-gated waterfall position #4.

3. **~~Major gift portfolio identification.~~** **RESOLVED.** `Staff_Manager__c` on Account (Lookup to User). Populated = donor is assigned to a gift officer = qualifies for waterfall position #2. Phase 1 diagnostic should also review the `hri-major-gift-app` repo for any additional include/exclude logic beyond the Staff Manager lookup (that app already makes portfolio membership decisions).

4. **~~Output file format validation with VeraData.~~** **RESOLVED.** VeraData reviewed Printer File layout (Shaun Petersen, April 14, 2026). Two additions incorporated: (a) Addressee field added (`npo02__Formal_Greeting__c` — full addressing name for envelope), (b) Salutation field clarified (must be "Mr. Petersen" format, not just title). VeraData also requests NCOA certificate with data delivery — operational item for Jessica, not a system change. Printer File layout now confirmed.

5. **~~Historical response rates by new segment codes.~~** **RESOLVED in spec.** Three-level fallback: (1) Jessica selects baseline campaign(s) by appeal code, (2) auto-lookup rolling average across all prior campaigns with matching segment codes, (3) manual entry for new codes with no history. See Section 7.2.

6. **~~MIC sheet ID.~~** **RESOLVED.** MIC is a live Google Sheet: `12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw`. Draft, Segment Detail, Budget Summary, and Segment Rules tabs to be added during Phase 1.

7. **Scorecard actuals write-back.** The Campaign Scorecard currently writes to its own data sheet. Phase 8 extends it to also write actuals to the MIC Campaign Calendar tab. Confirm this doesn't require a Scorecard rebuild — likely just an additional Sheets API write at the end of the existing refresh pipeline.

8. **~~Appeal code prefix precedence rule.~~** **RESOLVED.** Applies to Campaign Scorecard data processing only (historical data interpretation). The Segmentation Builder generates appeal codes using the prefix conventions from the Appeal Codes master document — no conflict possible because the builder is the system of record for new codes. The first character (T position in `TYYMCPSS0`) is set by the segment group assignment at generation time.

9. **Suppression parameter calibration with VeraData.** The 45-day recent-gift window and 6-solicitation/year frequency cap are provisional defaults with no internal or external benchmark. Both ship as OFF for the first two production campaigns. Consult VeraData during onboarding for recommended values. Enable via toggle once calibrated.

10. **Sheets-as-database migration evaluation.** After first full year of operation, evaluate whether the MIC Google Sheet should migrate to Cloud SQL or BigQuery. See architecture note in Section 2.2.

11. **~~Google Drive output folder ID.~~** **RESOLVED.** Output folder: `https://drive.google.com/drive/folders/1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC`. Printer Files, Internal Matchback Files, and suppression audit logs archived here.

12. **~~Suppression toggles team verification.~~** **RESOLVED.** Bekah reviewed April 13, 2026. Key changes: `Primary_Contact_is_Deceased__c` moved to Tier 3 (only all-members-deceased suppresses). `Enews_Only__c` removed (Contact-level field). `Address_Unknown__c` and `Not_Deliverable__c` moved to Tier 3 (replaced by blank address field check). `Match_Only__c`, `No_Name_Sharing__c`, `X1_Mailing_Xmas_Catalog__c`, `X2_Mailings_Xmas_Appeal__c` moved up to Tier 2. `Newsletter_Only` and `Newsletter_and_Prospectus_Only` confirmed as conditional — include in newsletter campaigns, suppress from everything else. Pending: `EU_Data_Anonymization__c` disposition; SF cleanup to combine newsletter flags.

13. **~~Campaign\_Segment\_\_c field name verification.~~** **RESOLVED.** Builder writes to existing `Source_Code__c` field on Campaign_Segment__c using the generated appeal code values. Label rename from "Source Code" to "Segment Code" queued for Bekah but not blocking the build.

---

## 17. Operator Documentation — In-UI Reference Tabs

Two tabs in the Apps Script **web app UI** (not the MIC sheet) populated from this spec at deploy time. Operator clicks a tab in the tool and reads the docs without leaving the workflow. They exist so any operator can answer "why is the tool doing this?" without reading SPEC.md or asking Bill.

**UI placement:** add tab navigation to the existing web app. Recommended layout:

```
┌──────────────────────────────────────────────────────┐
│  HRI Segmentation Builder                            │
│  [Build Campaign] [Reference] [Buttons]              │
├──────────────────────────────────────────────────────┤
│  (active panel content here)                          │
└──────────────────────────────────────────────────────┘
```

- **Build Campaign** — existing UI (campaign select, toggle panel, scenario editor, etc.). Default tab on load.
- **Reference** — content from §17.1 below, rendered as HTML. Static content, no interaction.
- **Buttons** — content from §17.2 below, rendered as HTML table.

**Content delivery:** static HTML in the Apps Script web app files (`Reference.html`, `Buttons.html` or equivalent). Content baked at deploy time from this spec. When the spec changes substantively, builder regenerates the HTML and redeploys via clasp push. No runtime sync mechanism required — the spec change cycle is the deploy cycle. Header on each tab shows "Synced from SPEC v<version>, deployed <date>" so operators can see currency.

### 17.1 Reference tab content (Logic & Math)

**Header (top of Reference tab panel):**
- Title: "HRI Segmentation Builder — Logic & Math Reference"
- Subtitle: "Synced from SPEC.md v<version>, deployed <ISO date>. Static content; re-deploys with the Apps Script when spec changes."

**Section blocks (each as a labeled section in the HTML):**

1. **Waterfall Hierarchy** — table with columns: Position #, Position Name, Field/Logic, Toggle Default, Notes. Rows (v3.3): Tier 1 Global Suppression, Tier 1.5 New Donor Welcome Pre-emption, Tier 2 Donor-Level Suppressions, Major Gift Portfolio, Mid-Level, Sustainers, Cornerstone, Active Housefile (high value + standard), Lapsed, Deep Lapsed, CBNC Override. Source: SPEC §6.1.

2. **Suppression — Tier 1 (Hard, always ON)** — columns: #, Rule, Field, Notes. Source: SPEC §6.2.1.

3. **Suppression — Tier 2 (Donor-Level + Communication Preferences, default ON, toggleable)** — columns: #, Rule, Field, Default, Conditional Logic. Source: SPEC §6.2.1.

4. **Tier 3 — DELETED in v3.3.** Reference tab should NOT render a Tier 3 section. If any pre-v3.3 deploy of the Reference HTML still shows Tier 3, regenerate from the v3.3 spec at next deploy.

5. **Segment-Level Suppression Rules** — columns: Rule, Default, State (toggleable/always/per-segment), Notes. Includes: Recent-gift window, Break-even floor, Response rate floor, Frequency cap, Holdout percentage (per-segment column in scenario editor as of v3.4). Source: SPEC §6.2.2.

6. **Ask String Math** — narrative section explaining:
  - Basis selection per segment (HPC for active/mid-level/cornerstone, MRC for lapsed, first gift for new donor).
  - Formula: `[1× basis, 1.5× basis, 2× basis, "Best Gift of $<HPC>"]`.
  - Floor ($15), ceiling ($4,975), round-up rule.
  - Floor-collapse fallback: when ANY tier < floor, ladder replaced as a unit with $15 / $25 / $35.
  - AskAmountLabel sources from HPC (`npo02__LargestAmount__c`), not LastGift. Fallback to ask3 if HPC null.
  - Mid-Level (ML*, MJ*, MP*) and Major Gift Portfolio donors ALWAYS get populated ask arrays; lettershop template controls display.

7. **Scanline & Check Digit** — narrative + table:
  - Format: `<DonorID 9-char> <CampaignAppealCode 9-char> <CheckDigit 1-char>` = 21 chars total.
  - Conversion table (A→1 ... Z→9 mapping per SPEC §10.1.1).
  - 7-step algorithm.
  - One worked example (e.g., `070122327W16B1AJ30` → check digit 6).

8. **Appeal Code Structure** — table:
  - 9-char Print code: `<TypePrefix><YY><CC><SegmentCode>` (positions 1, 2-3, 4-5, 6-9).
  - 15-char Internal code: `<TypePrefix><YYCC><SegmentCode><PackageCode><TestFlag>` (used in Matchback InternalAppealCode).
  - TypePrefix legend: A=Appeal, M=Mid-Level, R=Renewal, C=Cornerstone (preserve existing per-file prefix).

9. **Reply Copy Tier** — table: Tier, Criteria, Copy Template Key. Four rows (ACTIVE, LAPSED, NEW, REACTIVATED). Source: SPEC §8.4.

10. **Holdout & Floor Collapse Edge Cases** — narrative explaining:
  - 5% holdout from suppressed segments — Holdout=true rows in Matchback are tracked but not mailed.
  - Floor collapse — when basis is too small for any ladder tier to clear floor, fallback ladder applied as unit.

11. **PackageCode Routing** — narrative explaining:
  - PackageCode mapping per segment group from MIC Segment Rules tab (Active → P01, Mid-Level → P02, Cornerstone → P03, etc.).
  - Lettershop sorts on PackageCode to route creative.

12. **Output File Columns — Print** — table with Print File column list (24 columns), source field per column. Source: SPEC §10.1.

13. **Output File Columns — Matchback** — table with Matchback column list (37+ columns including new Scanline at pos 3 and Account_CASESAFEID at end), source field per column. Source: SPEC §10.2.

### 17.2 Buttons tab content

**Header (top of Buttons tab panel):**
- Title: "HRI Segmentation Builder — Buttons & Actions"
- Subtitle: "Synced from SPEC.md v<version>, deployed <ISO date>. Static content; re-deploys with the Apps Script when spec changes."

**Single HTML table** with columns: **Button / Action**, **What It Does**, **When To Use**, **What Gets Written / Changed**, **Reversible?**.

Rows (the button list will evolve with the UI; current set):

| Button / Action | What It Does | When To Use | What Gets Written | Reversible? |
| --- | --- | --- | --- | --- |
| Refresh Universe | Re-fetches the qualified universe from BigQuery cache for the selected campaign. Re-applies waterfall, suppressions, segment assignment. | When Jessica selects a different campaign, or when underlying SF data has changed since last pull. | In-memory universe (browser state). Draft tab not yet written. | Yes — just click again. |
| Run Projection | Computes economics (response rate, avg gift, projected gross/net revenue, break-even, margin) per segment based on Historical Baseline. Writes one row per segment to the Draft tab. | After the universe is loaded and Jessica wants to see the projection. | Draft tab in the MIC. Overwrites prior Draft contents. | Yes — re-run. |
| Edit Scenario (per-segment toggle, % slider, target type) | Browser-side what-if. Adjusts segment inclusion or quantity within the qualified universe. No new SF query. Updates running totals on the screen. | Iterating to hit a quantity / gross / net / ROI target. | UI display only. Does not touch Draft tab until "Save Scenario" or "Approve". | Yes — toggle back. |
| Save Scenario | Saves current scenario state to Draft tab. | When iterating on a non-final scenario across sessions or to share with Bill. | Draft tab updated. | Partially — overwrites prior Draft. |
| Approve | Locks the current Draft as the final segmentation for this campaign. Copies Draft tab to Segment Detail tab as permanent record. Triggers Generate Mailing File. | When Jessica + Bill have signed off on the projection. | Segment Detail tab (one row per segment for this campaign). Campaign Calendar `link_to_segments` column auto-populates. | Difficult — re-running Approve overwrites Segment Detail rows for this campaign on upsert. |
| Generate Mailing File | Produces Print File CSV + Matchback File CSV with per-donor scanlines, ask arrays, addressee/salutation, etc. Writes to Drive output folder with timestamp. | Auto-triggered on Approve, OR can be run independently to regenerate files. | Two CSVs in Drive: `HRI_<CampaignCode>_<Lane>_PRINT_<YYYYMMDD>.csv` + `..._MATCHBACK_<YYYYMMDD>.csv` | Yes — new files have new timestamp; old files preserved. |
| Load to Salesforce | DEFERRED. Will write Campaign + Campaign_Segment__c + CampaignMember records to SF, populated with post-merge-purge final mailed quantities. | After Faircom returns merge/purge file. Triggered via the merge/purge processor (separate build). | SF Campaign, Campaign_Segment__c (segment-level), CampaignMember (donor-level), Account NCOA address corrections. | Idempotent upsert — re-runs produce same end state. |
| Refresh Reference Tabs | Regenerates the Logic & Math Reference and Button Reference tabs from the current SPEC.md. | After any spec change is shipped to the build repo. | Both reference tabs rewritten in place. | Yes — re-run. |
| Download Print File | Direct download link to the Print File CSV from Drive. | When Jessica is preparing to transmit to VeraData / Faircom. | Browser download. | N/A. |
| Download Matchback File | Direct download link to the Matchback CSV. | Internal HRI use; not transmitted to lettershop. | Browser download. | N/A. |

If new buttons are added to the UI, this table must be updated in the same commit. The reference tab content lives in the spec, not the code — implementation reads SPEC.md §17.2 and renders this table.

### 17.3 Update mechanism

The HTML content for both Reference and Buttons tabs is **static, baked at deploy time** from SPEC.md §17.1 and §17.2. When the spec changes:

1. Architect updates SPEC.md §17.x.
2. Builder updates the corresponding HTML files in the Apps Script repo.
3. Builder runs `clasp push` to redeploy the web app.
4. Header version stamp updates accordingly.

No runtime fetch or refresh button needed. The deploy cycle is the sync cycle. This is simpler than a server-side refresh function and avoids any drift risk between deploys.

If a future need emerges (e.g., HRI wants Bill to edit the reference content without a redeploy), revisit this. For now, deploy-time bake is correct.

---
