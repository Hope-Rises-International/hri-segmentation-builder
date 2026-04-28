# REVIEW — HRI Segmentation Builder (v3.4 amendment + v3.3 + Historical Baseline)

*Last updated: 2026-04-28. v3.4 review below; earlier sections retained.*

## v3.4 amendment review (2026-04-28) — per-segment Holdout %

### Why this structure?

The simplest implementation slot is the existing scenario-editor row
schema. `draftSegments[*]` already carries `include` and `percent`;
`holdout_pct` joins them as a third per-row dial — no new layer.

The actual sampling lives in `output_files.generate_output_files`
(unchanged file from v3.3). The handoff describes
`suppression_engine.apply_holdout()` as the target site, but the
engine's `apply_*` helpers are about flagging donors with reasons; the
random selection has always lived where the Print/Matchback split is
done. Moving it to suppression_engine would have meant either (a)
plumbing the `clean_codes` DataFrame into a place that doesn't see it,
or (b) duplicating segment-aware filtering already handled in
output_files. Kept the existing seam.

Alternatives considered:

- **A separate `holdout_overrides` map alongside `segment_overrides`.**
  Rejected. The two flow from the same scenario rows; one map keeps
  the API smaller. UI builds both from the same iteration.
- **Deriving holdout from a global toggle scaled per segment.** Rejected.
  Bill's mid-level argument was specifically that the operator should
  reason about each segment in isolation; a single global slider would
  reintroduce the all-or-nothing problem.
- **Floating-point Holdout %.** Rejected. Integer-only avoids "5.5"
  questions; range 0–5 is small enough that integers cover the useful
  decision space. UI clamps; backend re-clamps.

### What are the trade-offs?

- **Per-segment seeded RNG.** Each segment uses `random.Random` seeded
  with `(holdout_seed * 1_000_003) ^ hash(str(seg_code))`. Independent
  samples per segment + stable across re-runs. The `*1_000_003 ^ hash`
  formula is intentionally simple — it's not a cryptographic mix, just
  a way to make the seed depend on both the run-level seed and the
  segment code. Two segments with similar names produce different
  seeds because Python's `hash` for strings randomizes per process by
  default — but inside a single Python process the seed is stable, and
  `output_files` always runs in one process per request. Verified
  determinism in unit-form smoke test.
- **Cost / revenue use mailable, not gross fit.** UI used to multiply
  CPP × effective fit; now multiplies CPP × (effective fit − held).
  Means the "Net Rev" column in the segment table reflects what the
  operator actually pays for, not what they targeted. Trade-off: the
  number changes when the operator dials Holdout % even if Include /
  % Incl haven't changed. That's the intended signal — small cohorts
  with 5% holdout cost ~50 mailings; the column makes that visible.
- **Soft warning at <3 instead of hard block.** Bill wants the operator
  to make the call. The orange text + tooltip make the trade-off
  obvious without forcing it.
- **Default of 5 preserves v3.3 behavior.** Every new scenario row
  arrives at 5; running approve without touching anything reproduces
  prior holdout shape. Smoke test confirms 200 donors @ 5% → 10 held,
  60 donors @ 5% → 3 held, 40 donors @ 5% → 2 held (matches `int(n *
  0.05)` rounding).
- **`max(1, int(...))` floor.** Inherited from the v3.3 implementation.
  A 60-donor segment at 3% gives `max(1, int(60 * 0.03)) = max(1, 1) = 1`,
  i.e. 1.7% effective. Acceptable — for tiny cohorts, holdout granularity
  is inherently coarse and a value of "0" remains the explicit "skip"
  mechanism. Documented in the renderer comment.

### What breaks if a dependency changes?

- **UI passes the wrong shape.** Server clamps each row's
  `holdout_pct` to integer 0–5; `holdout_pct_by_segment.get(seg, 5)`
  defaults missing rows to 5. So a missing field doesn't break, just
  reverts to default.
- **`segment_code` rename.** If a segment code in the universe doesn't
  match any UI row (e.g. a new code shipped without UI update), the
  `.get(seg, 5)` fallback applies; that segment gets 5%. Quiet, not a
  failure.
- **Python hash randomization across processes.** If a long-running
  pool ever serialized the holdout selection across processes,
  per-segment seeds would diverge. Today every request is a fresh
  Cloud Function invocation in its own process — the determinism
  guarantee is "same scenario in same process" which is what the
  caller cares about.

### What's the failure mode?

- **Operator types `7` in a Holdout % field.** UI clamps to 5 on
  blur (`Math.max(0, Math.min(5, n))`). Server re-clamps. No corruption.
- **Operator types negative.** Same clamp; becomes 0 (no holdout).
- **All segments dialed to 0.** Allowed and non-pathological — output
  has zero `Holdout=true` rows; Matchback equals Print (modulo
  exclusions). The audit log just contains no holdout entries.
- **Nuclear mode.** Zeroes the per-segment map server-side before
  passing to `output_files`; no donors held regardless of UI input.
  Verified in `approve_scenario` Nuclear branch.

### What would you do differently with more time?

- **Persist Holdout % per-segment to the Draft tab.** Currently
  `holdout_pct` lives only in browser state and the approve payload.
  An operator who closes the tab loses dial-downs. The Draft tab read
  path already accepts `Holdout %` (we read it on `renderDraftTable`
  if present); a `saveDraftOverrides` extension that writes it back
  would close the loop.
- **Surface aggregate holdout impact on the target indicator.** The
  segment-table totals row shows `Holdout` + `Mailable` columns; the
  Target panel still shows "Current: X qty" without distinguishing.
  A "Mailable: X" line under "Current:" would make the holdout cost
  visible at glance.
- **Per-segment seeds from donor IDs.** The seed currently mixes only
  the segment code. If two campaigns happen to seed the same way (same
  `holdout_seed=42`), they'd produce the same sample within the same
  segment if donor IDs were identical. Spec wanted "donor ID + campaign
  ID + segment" — partial credit here. Adding `campaign_appeal_code`
  to the seed mix would close it; one-line change, deferred until
  someone sees the same-sample issue in practice.
- **Tests.** Unit tests for the holdout selection (default state,
  single segment 0, mixed values, deterministic re-runs, Nuclear
  zeroes) would catch regressions cheaply. Today verification is the
  ad-hoc smoke test in this session.

---

## v3.3 amendment review (2026-04-28)

### Why this structure?

The v3.3 changes are deliberately surgical. Each item slots into the
nearest existing seam rather than introducing a new layer:

- New Tier 1 rules (`Type`, `RecordType.Name`) live next to the
  existing Tier 1 `_suppress` calls. They share the same audit-log
  reason format (`"Tier1: Account Type (DAF/Govt)"`) so downstream
  audit consumers don't need new branches.
- Tier 1.5 (New Donor pre-emption) is a single `_suppress` call between
  Tier 1 and the GROUP_EXCLUDE pass. Same machinery, distinct reason
  prefix (`"Tier1.5:"`) so it's filterable in the audit log without
  changing the schema.
- Major Donor In-House at Tier 2 reuses the existing Tier 2 result-
  mutation pattern (`result.loc[mask, ...]` + `suppression_log.append`).
  It doesn't fit the shared `_suppress_tier2(field, rule)` helper
  because the test isn't `field == True` — it's `field == "In House"` —
  but the duplication is one block, not a refactor.
- N-prefix routing reuses `validate_campaign_selection` and
  `resolve_campaign_for_segment`. The validator gained one `if not
  inhouse_on` block; the resolver is unchanged because no segment maps
  to N (the routing happens at the suppression layer when in-house is
  flipped OFF — donors that would have been suppressed instead flow
  through to the M-prefix campaign and use the N-prefix only when an N
  campaign is bundled). *Decision recorded:* this means an in-house
  mailing is implemented as "in-house toggle OFF + add N campaign +
  exclude M campaign" rather than a positive routing rule. Operator-
  facing impact is the same; engine-side it's one fewer special case.

Alternatives considered:

- **A separate `tier1_5.py` module for the pre-emption.** Rejected — a
  single mask + one `_suppress` call doesn't earn its own file. The
  comment block in `waterfall_engine` carries the spec rationale.
- **Removing MP01 from `SEGMENT_CODES` entirely.** Rejected. Historical
  Matchback files and Salesforce `Campaign_Segment__c` records still
  reference it. Keeping the code as a deprecated entry preserves
  resolution and makes reinstatement a one-line revert.
- **Defining `MID_LEVEL_MAX = None` and special-casing in the mask.**
  Rejected. `math.inf` keeps the comparison `cumulative <= MAX` working
  unchanged; nothing else needs to know.

### What are the trade-offs?

- **24-month vs lifetime cumulative for Mid-Level.** 24-month catches
  recently active givers and aligns with TLC's historical baseline
  math. It also drops donors who gave $5K once five years ago and
  haven't returned — which is correct for ML01's purpose (cultivation)
  but means our pre-v3.3 baselines are not directly comparable. The
  expected ~3x reduction in ML01 size is a feature, not a regression.
  Bill called this out explicitly in the test plan (item 10a).
- **Tier 1.5 hard pre-emption vs operator-toggleable.** The spec made
  this hard / always-on. That removes a foot-gun (operator running the
  May appeal forgets to turn New Donor OFF and welcome-window donors
  receive both streams) at the cost of taking away a legitimate
  override case (true emergency where the welcome stream is paused).
  The override path: the welcome series workflow inverts all GROUP
  toggles and uses the welcome-flag as include criterion. Acceptable
  because welcome-stream pauses are workflow-level decisions, not
  per-campaign decisions.
- **Type/RecordType suppression as defense-in-depth.** The current SOQL
  `WHERE RecordType.Name = 'Household Account'` already excludes ALM-
  organization records and most DAF/Government accounts. The new Tier 1
  rules are no-ops today. Cost: ~zero (vector ops on already-pulled
  fields). Benefit: a future cornerstone-only or in-house-only flow
  that widens the WHERE doesn't need to remember to add this back.
- **In-house OFF requires N-prefix.** Forces the operator to
  consciously decide "I'm running an in-house mailing" by selecting an
  N-prefix campaign. Friction is the point — flipping the toggle is
  rare enough that defaulting through silently would invite the wrong
  outcome.
- **Major Donor In-House field rename.** Bekah did the SF rename
  + picklist cleanup before this build started. We assume the rename is
  complete and any external query that references the old name has been
  audited (verified per handoff). If a stray BQ view or Apps Script
  routine still references `TLC_Donor_Segmentation__c`, it will fail at
  query time — fast, loud, fixable.

### What breaks if a dependency changes?

- **Salesforce field rename rolled back.** `Major_Donor_In_House__c`
  goes 404 in the SOQL response → BQ extract fails on next nightly
  rebuild. Detection: `run_sf_extract` raises immediately. Recovery:
  rename in code or revert SF.
- **`Total_Gifts_730_365_Days_Ago__c` formula changes meaning.** Mid-
  Level cohort silently shifts. The field name has unusual casing in
  some SF environments (`Total_GIfts_13_24_Months_Ago__c` per SPEC §4.1
  — typo there); the live SOQL uses `Total_Gifts_730_365_Days_Ago__c`,
  which I confirmed against the existing query. If the SF org renames
  this, the `pd.to_numeric(accts.get(...))` returns 0 for everyone and
  Mid-Level empties. Detection: live-pull row counts.
- **`Lifecycle_Stage__c` enum value drift.** The Tier 1.5 mask is the
  literal string `"New Donor"`. If SF's lifecycle formula returns
  `"New Donor (90-day)"` or similar, pre-emption stops firing — welcome
  donors leak into standard appeals. Detection: zero Tier1.5
  suppressions in audit log. Mitigation: a periodic "expected New Donor
  cohort size > 0" check at run time would catch this.
- **`Type` and `RecordTypeName` not present in BQ.** Until the nightly
  re-run after deploy, the BQ cache has neither column. Code uses
  `accts.get(..., empty Series)` and the masks evaluate to all-False —
  suppression silently doesn't fire. Mitigation: re-run nightly
  immediately after deploy (handoff explicitly calls this out).

### What's the failure mode?

- **In-house toggle OFF + no N campaign.** Caught at the validator
  before the run; UI surfaces a clear error. Authoritative on both
  sides (client + server). Not a runtime failure.
- **MP01 in a historical Matchback.** `SEGMENT_CODES["MP01"]` still
  resolves to a (deprecated) human-readable string. Output files still
  format. New runs no longer assign MP01.
- **Operator runs A2651 with default toggles after the deploy.** Mid-
  Level cohort drops by ~2/3, ML01 row count drops, AH/LR cohorts
  unchanged. If they read the Draft tab without reading the spec, they
  will think there's a regression. The handoff calls this out as item
  10a; the v3.3 deploy report should headline it.
- **Major Donor In-House field has unexpected values.** Suppression
  fires only on `== "In House"`; null / blank / "Mid - TLC" (cleared by
  Bekah) all pass through. If Bekah's cleanup hasn't reached every
  record, donors that should be suppressed slip through — the symptom
  is a slightly larger universe, not a corrupt one.

### What would you do differently with more time?

- **Validate the SOQL changes against a sandbox before nightly.** I
  added two new fields plus a relationship traversal to the SOQL but
  haven't run the query against the live org from this session — the
  Cloud Run service will pick it up at next nightly. A `simple-
  salesforce` quick query in test mode would surface field-name typos
  / picklist mismatches before they hit production.
- **Tighten the Major Donor In-House comparison.** Current value test
  is `== "In House"`. If Bekah's cleanup leaves whitespace
  (`"In House "`) or case variants (`"in house"`) in some rows, those
  donors leak. A `.str.strip().str.casefold() == "in house"` test would
  be safer; cost is one chained call.
- **Property-test the toggle migrations.** Specifically: a test that
  asserts `DEFAULT_TOGGLES` doesn't contain the removed keys
  (`new_donor`, `mid_level_prospect`) and that `TOGGLE_PREFIX_RULES`
  has the expected mapping. Today we rely on the smoke script. A
  pytest fixture would prevent regressions on future amendments.
- **Add a pre-deploy diff sanity check.** The v3.3 changes touched 10
  files / ~900 lines. A pre-deploy pass that runs a fixed synthetic
  universe through `run_waterfall` and checks segment counts against
  expected v3.3 numbers would catch silent semantic drifts. Doable as
  a CI step.

---

*Earlier section, retained from Historical Baseline build:*

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
