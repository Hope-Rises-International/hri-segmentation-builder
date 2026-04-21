# REVIEW — HRI Segmentation Builder (Historical Baseline addition)

*Scope: this build's additions — the Historical Baseline grid + its wiring into the Scenario Editor. Does not re-review pre-existing modules.*

## 1. Why this structure?
The historical baseline is split into three layers with narrow contracts:
- `src/campaign_types.py` — pure classification: `classify_campaign(name, lane, is_followup) → type`. No I/O, no pandas. Easy to unit-test and mirror in JS.
- `src/historical_baseline.py` — pure aggregation: loads two MIC tabs, joins, filters, aggregates, writes to BQ + MIC. No Salesforce, no waterfall.
- `src/build_universe.py` + `src/approve_scenario.py` — unchanged shape; they gain one new keyword arg (`baseline_type`) that selects between the new grid reader and the legacy single-campaign rollup.

Alternatives considered and rejected:
- **Putting the classifier inside `historical_baseline.py`.** Rejected — the UI needs the exact same classifier to auto-populate the baseline type dropdown from a campaign name, and a JS mirror (Code.gs `classifyCampaignType_`) is far simpler when there's a clean spec with no I/O.
- **Computing the grid on-demand inside `build-universe`.** Rejected — aggregation across ~9K Scorecard rows on every Load Universe click would add ~5s and hit the sheet's read quota hard. Nightly precompute → fast reads is the obvious shape.
- **Replacing `baseline_rollup.py` outright.** Rejected — the legacy single-campaign path is still useful for QA ("what would the numbers look like if we just used A2551?") and the spec explicitly calls it out as a UI option. Keeping both paths is the honest implementation of the spec.

## 2. What are the trade-offs?
- **Freshness vs. speed.** Baseline is nightly; if Scorecard data changes mid-day, the operator sees yesterday's grid. Mitigated by the `/rebuild-historical-baseline` endpoint. Acceptable because the Scorecard itself refreshes nightly — the only staleness path is intraday manual Scorecard edits, which are rare.
- **Breadth vs. statistical power.** Lumping all Shipping campaigns together makes AH01 noisy campaign-to-campaign signal disappear, but also averages away idiosyncratic one-off results. A quality filter (≥500 contacts, FY22+, exclude Acquisition/emergency) cuts the worst noise; segments with <3 contributing campaigns are flagged `estimate`. Confidence badge in the UI surfaces this to the operator.
- **Proxy simplicity vs. accuracy.** CS01/MJ01/MP01 use AH01+AH04 as a proxy — known to understate MJ01's real avg-gift because AH01 captures $50+ donors, not $100+. CB01 uses LR01 × 1.5 scale — a guess. Trading accuracy for coverage: a `proxy`-flagged number is better than 0. Operator can override on a per-segment basis.
- **Static type list in Apps Script.** `Code.gs getHistoricalBaselineTypes()` returns a hardcoded list that mirrors `campaign_types.ALL_TYPES`. If Python adds a new base type and we forget to add it in Code.gs, it still works — the BQ query silently falls back to Overall — but the operator won't see it in the dropdown. Duplication is load-bearing for zero-roundtrip loading.

## 3. What breaks if a dependency changes?
- **Scorecard renames a column in Segment Actuals** → nightly rebuild raises `KeyError`. The grid stops refreshing; `build-universe` keeps working on the previous day's BQ data. Detection: `campaign-scorecard-refresh` runs earlier in the night; schema-drift there is upstream of this.
- **Campaign Calendar drops `is_followup`** → chaser detection falls back to substring matching ("Chaser" / "F/U" / "FU"). Campaigns that relied purely on the boolean flag would be misclassified. Low risk — the current data uses both conventions.
- **BigQuery pyarrow version bump** → `to_dataframe` path may break. The write direction uses JSON and is resilient; the read direction (`fetch_baseline_for_type`) iterates over `job.result()` rows directly, not `.to_dataframe()`, so this is already hardened.
- **Service account loses BQ IAM** → all paths fail. SA must retain `roles/bigquery.dataEditor` on `sf_cache` dataset. This is pre-existing to this build.

## 4. What's the failure mode?
- **Nightly rebuild fails** → `run_sf_extract` logs the traceback and continues; the account extract succeeds, the baseline rebuild returns `{"error": "..."}` in the response. Silent from the operator's perspective until they notice stale Last Refreshed. Monitoring gap — no alert yet.
- **BQ table missing entirely** → `fetch_baseline_for_type` raises `google.api_core.exceptions.NotFound`. `build-universe` catches this (`try/except Exception` around the fetch), logs the warning, and returns an empty `baseline_by_segment`. The Scenario Editor loads with all zeros and the operator sees `Baseline: Shipping (multi-campaign avg)` in the meta but 0% RR everywhere — confusing. A clearer UX would show an explicit "Baseline data unavailable" banner.
- **Classifier mismatch between Python and JS** → the auto-populated type in the UI doesn't match what the backend would classify the same campaign as. The UI always sends what the user selected, so this is purely a UX issue (wrong default) not a correctness issue. Review when adding new base types: update both files.

## 5. What would you do differently with more time?
- **Add per-campaign contribution transparency.** The grid currently shows totals but not which campaigns fed each row. When a number surprises the operator (Shipping ML01 = 5.76% RR), they can't see "these 6 campaigns drove it." Adding a `campaign_codes: array<string>` column to the BQ table would make this one SQL query away.
- **Split ML01 out of general-purpose types.** The live gate test surfaced that Shipping baseline is dominated by ML01 revenue ($99K of $119K total on a 63K universe), because M-prefix campaigns get bundled into "Shipping" under the current classifier. A reasonable refinement: treat M-prefix appeal codes as a separate baseline type (`Shipping — Mid-Level` vs `Shipping — Housefile`) so the operator can separate those economics.
- **Alerting on rebuild failure.** No page/email today. A lightweight Cloud Logging alert on `run_sf_extract` exceptions with `baseline_summary.error` present would close the silent-failure gap.
- **Property-based tests on the classifier.** `classify_campaign("Shipping F/U") == "Shipping Chaser"` etc. — quick unit tests. Right now the JS mirror is only validated by eyeball against a sample of 300 names.
- **Weighted aggregation beyond contact volume.** A campaign with 100K contacts dominates the baseline even if its response pattern is structurally unusual (special offer, unusual mail date). A cube-root weighting or explicit campaign-weight override in config would reduce a single outlier's pull.
