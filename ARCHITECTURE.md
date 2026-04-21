# ARCHITECTURE — HRI Segmentation Builder

*Last updated: 2026-04-20, after Historical Baseline build (Phases 1–2).*

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
