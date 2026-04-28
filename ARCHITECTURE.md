# ARCHITECTURE — HRI Segmentation Builder

*Last updated: 2026-04-28, after SPEC v3.4 amendment.*

## v3.4 amendment surface area (2026-04-28)

Small-scope amendment. Holdout moves from a global ON/OFF toggle to a
per-segment column in the Step 3 scenario editor.

- **`apps-script/Index.html`** —
  - `SUPPRESSION_TOGGLES`: `holdout` row removed.
  - Step 3 segment-table renderer (`renderDraftHTML`): new `Holdout %`
    column adjacent to `% Incl`, plus two derived columns (`Holdout`,
    `Mailable`) so the operator sees both gross fit and the post-holdout
    mailable count side-by-side. Soft orange-text warning + ⚠ icon when
    a row's value drops below 3, with a tooltip explaining the
    measurement-power trade-off.
  - `draftSegments` rows carry `holdout_pct` (default 5; range 0–5
    integer). `onSegmentHoldout` clamps inputs to that band.
  - `computeScenarioTotals` subtracts holdout from cost / revenue, so
    auto-fit and the Current target indicator agree with the per-row
    numbers.
  - Scenario-payload serializers (Approve flow + Save scenario flow)
    include `holdout_pct` for each segment.
- **`src/output_files.py`** —
  - `generate_output_files` gains a `holdout_pct_by_segment` kwarg
    (mapping `segment_code` → integer 0–5). Per-segment sampling
    iterates the codes_df groupby; each segment uses its own seed
    derived from `(holdout_seed * 1_000_003) ^ hash(seg_code)` so
    samples are independent and stable across re-runs of the same
    scenario.
  - Legacy `holdout_pct` (single global float) kept for ad-hoc /
    diagnostic callers — falls through unchanged when
    `holdout_pct_by_segment` is None.
- **`src/suppression_engine.py`** —
  - `holdout` removed from `DEFAULT_SUPPRESSION_TOGGLES` (no longer a
    toggle).
  - `DEFAULT_SUPPRESSION_PARAMS["holdout_pct"]` retained as the
    per-segment default applied to new scenario rows.
- **`src/approve_scenario.py`** —
  - Builds `holdout_pct_by_segment` from `scenario.segments[*].holdout_pct`
    (default 5, clamped 0–5) and passes it through to
    `generate_output_files`.
  - Nuclear mode zeroes the per-segment map so no donors are held out.
  - Nuclear toggle-forcing list no longer references `holdout` (it's
    not a toggle).
- **Audit log shape unchanged.** Each held-out donor's row still
  carries `Holdout=true`; only the per-segment fraction varies. No
  Matchback / Drive-output schema change.
- **Migration: none.** Default state (every row at 5) reproduces v3.3
  behavior. Operator dial-down is purely additive.

---

## v3.3 amendment surface area (2026-04-28)

This amendment is a behavior change, not new infrastructure — the four
Cloud Functions, MIC sheet, GCS bucket, and Apps Script web app all
keep their identities. What moved:

- **SF/BQ extract** (`src/salesforce_client.py`, `src/bq_extract.py`): the
  Account SOQL now pulls `Type`, `RecordType.Name`, and the renamed
  `Major_Donor_In_House__c` (was `TLC_Donor_Segmentation__c`). Live-SF
  path flattens the nested RecordType dict to `RecordTypeName`;
  `bq_reader` renames `RecordType_Name` to match so callers see one name.
  Re-running the nightly extract is required after deploy so
  `accounts_raw` picks up the new columns.
- **Waterfall** (`src/waterfall_engine.py`):
  - Tier 1 grew two new rules: Account.Type ∈ {DAF, Government} and
    RecordType.Name ∈ {ALM Foundation Organization, ALM Grants/Partners
    Household, ALM Grants/Partners Organization}. Defense in depth —
    the current SOQL `WHERE` already filters to Household Account, but
    the suppression travels with the engine for any future RFM-bypassing
    flow (cornerstone-only, in-house-only).
  - Tier 1.5 — a new pre-emption pass between Tier 1 and the GROUP
    bucket — removes any donor with `Lifecycle_Stage__c == 'New Donor'`.
    Hard / always-on; the welcome series itself runs as a separate
    workflow with all GROUP toggles OFF.
  - GROUP_EXCLUDE shrinks by two rules: `new_donor` (promoted to
    Tier 1.5) and `mid_level_prospect` (cohort eliminated).
  - Mid-Level basis switches from `npo02__TotalOppAmount__c` (lifetime)
    to `Total_Gifts_Last_365_Days__c + Total_Gifts_730_365_Days_Ago__c`
    (24-month). Floor moves from $1,000 to **$750**, ceiling lifts to
    `math.inf`. This shrinks the cohort meaningfully — last A2651 audit
    suggests ~1,068 vs ~3,196 under lifetime — by design, not regression.
  - Position 11 Deep Lapsed reads from a new `lifetime_cumulative`
    series (kept under that explicit name) because the donor's 24-month
    cumulative is $0 by definition. Easy place to break later if
    someone forgets — comment in code calls it out.
  - Positions renumbered: New Donor at position 6 and MP01 at position 9
    are gone; AH/LR/DL/CB shift down accordingly.
- **Suppression** (`src/suppression_engine.py`):
  - Tier 1 → Tier 2 for `No_Mail_Code__c` (toggleable always-on so rare
    authorized cases — events, cornerstone exceptions — can override).
  - Tier 2 gains `major_donor_in_house` (suppress when value
    == `"In House"`). Default ON; OFF requires N-prefix campaign per
    the validator.
  - Tier 2 drops `Newsletter_and_Prospectus_Only__c` (Bekah
    consolidating in SF) and `No_Name_Sharing__c` (acquisition co-op
    only, never DM).
  - Tier 3 deleted entirely. The 14 legacy fields previously listed are
    no longer used; X1/X2 Christmas frequency caps were promoted to
    Tier 2.
- **Routing** (`src/config.py`):
  - `COHORT_PREFIX_RULES`: J entries removed (architect's earlier J
    prefix was a misinterpretation). Major Gift drops to M-only. ND01
    and MP01 absent from the routing table — neither cohort reaches
    appeal-code generation under v3.3.
  - New `INHOUSE_TOGGLE_KEY` / `INHOUSE_PREFIX = 'N'` constants. The
    validator requires an N-prefix campaign in the selection whenever
    the `major_donor_in_house` Tier 2 toggle is OFF. Each cohort still
    routes to one and only one campaign per run.
- **Apps Script UI** (`apps-script/Index.html`,
  `apps-script/Reference.html`, `apps-script/Buttons.html`):
  - Waterfall toggle list drops `mid_level_prospect` and `new_donor`.
  - Suppression toggle list adds `no_mail_code` and
    `major_donor_in_house`, drops `no_name_sharing`. Recent-gift label
    updated to "(spec'd, unbuilt)".
  - Multi-select campaign filter accepts A / M / N (J removed).
  - Reference tab gets a Tier 1.5 Pre-emption section above Tier 2;
    Tier 3 section is replaced with a "DELETED (v3.3)" callout. Per-
    position waterfall table renumbered.
  - Buttons tab gains a Major Donor In-House row; multi-select row
    rewritten for A / M / N.

What stayed the same:

- All authentication paths, GCP project layout, Drive folder, MIC sheet
  ID, Apps Script project ID. Web app URL unchanged.
- Multi-campaign output split (one Print + one Matchback per campaign),
  Nuclear mode (forces GROUP toggles ON, bypasses Tier 2 + recent-gift
  + freq cap + holdout, audit log to Drive), and per-campaign cohort
  prefix routing. The validator's shape is unchanged; only the rule set
  inside it changed.
- Historical baseline grid + Scenario Editor + budget fitting. None of
  v3.3 touches these.

---

*Earlier section, retained from Historical Baseline build:*

## 1. What this service does
The Segmentation Builder is HRI's direct-mail segmentation engine. Given a planned campaign (from MIC Campaign Calendar), it assigns every active donor to one HRI segment via a deterministic waterfall + suppression pipeline, projects per-segment economics from historical actuals, lets the operator edit a scenario in the browser (auto-fit to target / adjust per-segment), and on approval generates printer + matchback files and upserts segment rows to Salesforce. The **Historical Baseline** layer (this build) replaces the previous single-campaign baseline with a multi-campaign grid: for each HRI segment × campaign type (Shipping, Tax Receipt, Year End, Easter, Renewal, Faith Leaders, Shoes, Whole Person Healing, FYE, Newsletter, Acquisition, Other — each with chaser variants — plus Overall) it computes contact-weighted response rate and average gift. The operator now picks either a Campaign Type (default) or a Specific Prior Campaign, and the Scenario Editor uses that to populate Hist. RR / Hist. Avg Gift / projected net per segment.

## 2. How it runs
Four Cloud Functions (gen2, us-east1, `hri-receipt-automation`), all invoked by an Apps Script web app that operators drive in a browser:
- `build-universe` (4Gi, 300s) — runs when the operator clicks *Load Universe*, returns ~70K donor records + segment aggregates.
- `approve-scenario` (4Gi, 900s) — runs when the operator approves a scenario, generates outputs.
- `sf-cache-extract` (4Gi, 600s) — runs nightly (Cloud Scheduler `sf-cache-nightly`, 11 PM ET) to refresh `sf_cache.accounts` and `sf_cache.historical_baseline`.
- `rebuild-historical-baseline` (2Gi, 300s) — manual ad-hoc rebuild of the baseline grid when Scorecard data changes mid-day.

The Apps Script web app (domain-restricted, execute-as-user) sends requests signed with an OIDC token minted for `hri-sfdc-sync@…`.

## 3. What it reads from
- **Salesforce** Account object (all rollup fields + per-FY gift counts) via SOQL in `bq_extract.ACCOUNT_SOQL`.
- **BigQuery** `hri-receipt-automation.sf_cache.accounts` (pre-computed CBNC + $500 DM flags), `sf_cache.historical_baseline` (multi-campaign averages — this build).
- **MIC Google Sheet** `12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw`, tabs: `mic_flattened.csv` (Campaign Calendar), `Segment Actuals` (Scorecard output), `Segment Rules` (package overrides).
- **GCS** `hri-sf-cache` bucket for the raw SF snapshot between SF and BQ.

## 4. What it writes to
- **BigQuery** `sf_cache.accounts_raw`, `sf_cache.accounts`, and **`sf_cache.historical_baseline`** (new, this build — WRITE_TRUNCATE each rebuild).
- **MIC Google Sheet** tabs: `Draft`, `Segment Detail`, `Budget Summary`, `Universe`, and **`Historical Baseline`** (new, this build — mirrors the BQ table for operator visibility).
- **Google Drive** shared folder `1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC` — printer CSV, matchback CSV, suppression audit log, diagnostic Sheets.
- **Salesforce** `Campaign_Segment__c` — upsert of approved segment rows.

## 5. Authentication
- All Cloud Functions run as `hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`.
- Salesforce credentials in Secret Manager: `sfdc-username`, `sfdc-password`, `sfdc-security-token`, `sfdc-consumer-key`, `sfdc-consumer-secret`. HRI_Cloud_Sync connected app, `gcpuser@hoperises.org` API-Only user (migrated 2026-04-13).
- Apps Script web app executes as user and mints OIDC tokens via IAM Credentials API (`generateIdToken`) using the SA the deployer has `roles/iam.serviceAccountTokenCreator` on.
- Local development uses ADC with SA impersonation: `gcloud auth application-default login --impersonate-service-account hri-sfdc-sync@…`.

## 6. Dependencies
- Google Cloud: BigQuery, Cloud Functions gen2, Cloud Run, Cloud Storage, Cloud Scheduler, Secret Manager, IAM Credentials API.
- Google Workspace: Sheets API v4, Drive API v3 (both with `supportsAllDrives=True` for shared-drive writes).
- Salesforce REST API (via `simple-salesforce`).
- Python pins (required for Python 3.9 compatibility): `numpy==1.26.4`, `pyarrow==14.0.2`, `google-cloud-bigquery==3.13.0`, `google-cloud-storage==2.13.0`, `db-dtypes==1.2.0`, `pandas==2.*`.

## 7. What breaks if this stops running
- **Nightly extract fails** → `sf_cache.accounts` and `sf_cache.historical_baseline` go stale. `build-universe` falls back to a 14-minute live-SF path (scenario editor becomes sluggish but usable). Baseline grid freezes at yesterday's data — noticed within a day when operators see stale Last Refreshed.
- **`build-universe` fails** → operator can't load the Scenario Editor. Jessica blocked. Noticed immediately on next campaign.
- **`approve-scenario` fails** → scenarios can't be finalized; mailing blocked. Noticed when Jessica tries to approve.
- **`historical_baseline` table missing or empty** → baseline_type mode silently falls through to `hist_rr=0, avg_gift=0` for all segments (degraded UX but not a crash). Operator can fall back to `baseline_appeal_code` path.

## 8. Three most likely failure modes
1. **Scorecard Segment Actuals schema drift.** Renaming `appeal_code` / `source_code` / `contacts` / `gifts` / `revenue` breaks `load_segment_actuals`. Symptom: nightly rebuild fails with `KeyError`; baseline grid stops refreshing. Fix: update `historical_baseline.load_segment_actuals` to match new column names.
2. **Campaign Calendar missing `is_followup` or `campaign_name` column.** Classifier falls back to substring matching on whatever is there. Symptom: chasers wrongly bucketed as base types → Shipping baseline inflated by chaser rows. Fix: ensure `mic_flattened.csv` preserves those columns; re-run rebuild.
3. **pyarrow / numpy ABI mismatch on Cloud Run.** Gen2 build images occasionally ship a pyarrow that conflicts with the numpy ABI. Symptom: function cold-start errors with `_ARRAY_API not found`. The baseline code uses `load_table_from_json` to avoid the pyarrow build path in the write direction; BQ *reads* still go through db-dtypes → pyarrow. Fix: repin pyarrow to the version matching the installed numpy; `requirements.txt` already pins both.

## 9. How to manually re-run
- **Rebuild the baseline grid only** (~45s):
  ```bash
  TOKEN=$(gcloud auth print-identity-token \
    --impersonate-service-account=hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com \
    --audiences=https://rebuild-historical-baseline-qelitx2nya-ue.a.run.app)
  curl -X POST https://rebuild-historical-baseline-qelitx2nya-ue.a.run.app \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
  ```
- **Full nightly** (accounts + baseline, ~7 min):
  ```bash
  TOKEN=$(gcloud auth print-identity-token \
    --impersonate-service-account=hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com \
    --audiences=https://sf-cache-extract-qelitx2nya-ue.a.run.app)
  curl -X POST https://sf-cache-extract-qelitx2nya-ue.a.run.app \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
  ```
- **Local test with real ADC (no deploy):**
  ```bash
  cd hri-segmentation-builder
  python3 -c "
  import sys; sys.path.insert(0,'src')
  from sheets_client import get_sheets_client
  from historical_baseline import rebuild_and_publish
  print(rebuild_and_publish(get_sheets_client()))
  "
  ```
