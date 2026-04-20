# SPEC: Dynamic Scenario Editor

**Date:** 2026-04-20
**Author:** Architect
**Status:** Draft — pending Bill approval
**Scope:** Replace the run-review-tweak-rerun loop with a single universe pull + fully dynamic browser-side scenario editing

---

## 1. Problem

Current interaction model:

```
Run Projection (5 min) → Review Draft → Change % → Re-fit (5 min) → Review → Change → Re-fit (5 min) → ...
```

Every adjustment is a 5-minute round-trip to Cloud Run. Jessica can't explore scenarios — she can only commit to one budget fit, adjust, and wait. The underlying data doesn't change between a 100% DL02 run and a 20% DL02 run. The only thing that changes is the selection logic. There is no reason that selection logic needs to run on a server.

## 2. Solution — Full Universe Pull + Browser-Side Scenario Engine

Split the pipeline into two distinct phases:

### Phase A — Build Universe (runs on Cloud Run, ~5 min, happens when universe changes)

Triggered when the operator:
- Selects a campaign
- Changes waterfall position toggles
- Changes suppression rules
- Changes the baseline campaign for economics

Produces a **universe dataset**: all donors post-waterfall + post-suppression, each with:
- Account ID, Constituent ID, Name
- Assigned HRI segment code
- RFM weighted score (intra-segment ranking)
- Per-donor projected response rate (from baseline rollup, applied at segment level)
- Per-donor projected avg gift (from baseline rollup)
- Per-donor cumulative giving, recency, frequency (for display / filters)
- Any suppression or exclusion flags

This dataset is ~70K rows × ~15 fields = ~10-15MB JSON. Written to a new MIC tab (`Universe`) and also returned to the UI as the working dataset.

### Phase B — Scenario Editor (runs entirely in the browser, instant)

The UI receives the universe dataset and provides:

1. **Per-segment controls** — include/exclude checkbox, % slider (already built, Tier 1+2)
2. **Target-driven fitting** — operator picks a target type and value, the browser computes the optimal segment/% mix to hit it
3. **Real-time economics** — Total Mailable, Total Cost, Gross Revenue, Net Revenue, ROI, Margin all recalculate on every edit, with no server call
4. **Scenario comparison** — save multiple scenarios (e.g., "Max Net", "Max Volume", "Hit Budget Target") and compare side-by-side

When satisfied, the operator clicks **Approve & Generate Files**. THIS call goes to Cloud Run to generate the Printer/Matchback files using the approved scenario's selections.

## 3. Target-Driven Fitting

The operator picks one of four target types:

| Target | Operator Specifies | Optimization |
|--------|-------------------|--------------|
| **Quantity** | Budget qty (e.g., 54,000) | Current logic — trim from bottom of waterfall until universe fits target |
| **Gross Revenue** | Revenue target (e.g., $150K) | Rank segments by marginal revenue per contact (response rate × avg gift). Include highest-value segments first. Stop when cumulative projected revenue ≥ target. |
| **Net Revenue** | Net target (e.g., $75K) | Rank segments by marginal net revenue per contact (response rate × avg gift − CPP). Include segments with positive marginal net first. Stop when cumulative net ≥ target. Segments with negative marginal net are excluded regardless of target. |
| **ROI Threshold** | Minimum ROI (e.g., 2:1) | Include only segments meeting or exceeding the ROI threshold. Sum all qualifying donors. No quantity fitting. |

The solver is a greedy algorithm — not complex, runs in milliseconds in JavaScript. For each segment, it computes marginal economics per contact from the universe dataset (response rate × avg gift, cost per piece), ranks segments, and fills toward target.

Operator can override the auto-fit at any time by adjusting per-segment controls. The target display shows "Target: $75K Net, Current: $73.2K Net" so the operator can see deviation.

## 4. Scenario Comparison

The UI supports multiple saved scenarios within a single campaign session:

| Scenario Name | Qty | Cost | Gross Rev | Net Rev | ROI | Notes |
|---------------|-----|------|-----------|---------|-----|-------|
| Budget Target | 54,000 | $23,760 | $148K | $124K | 6.2:1 | Default fit |
| Max Net | 42,000 | $18,480 | $135K | $117K | 7.3:1 | DL03, DL04, AH03 excluded |
| Mid-Level Focus | 38,000 | $16,720 | $155K | $138K | 9.3:1 | Mid-Level + Cornerstone only at 100%, everything else 50% |

Operator picks the scenario to approve, then generates files from that scenario's selections.

## 5. Data Flow

```
Campaign Selection / Toggle Change / Suppression Change
           │
           ▼
   [Phase A — Cloud Run]
   - Pull accounts from BQ
   - Apply waterfall
   - Apply suppression
   - Compute RFM scores
   - Apply baseline rollup for per-donor economics
   - Write universe to MIC Universe tab
   - Return universe JSON to browser
           │
           ▼
   [Phase B — Browser]
   - Render segment summary from universe
   - Operator edits inclusions, %s, target type
   - All recalculations happen in JS against universe dataset
   - No server calls
           │
           ▼
   Operator clicks Approve
           │
           ▼
   [Finalization — Cloud Run]
   - Receive approved scenario (segment overrides + targets)
   - Apply to universe dataset
   - Generate appeal codes for selected donors
   - Write Printer File + Matchback File + Exceptions to Drive
   - Write Segment Detail to MIC
   - Update campaign status to Approved
```

## 6. Economics Thresholds (sourced)

Default thresholds for the editor, each traceable to an external benchmark or explicitly provisional:

| Threshold | Value | Source |
|-----------|-------|--------|
| Housefile Response Rate Floor | 2% | Steven Screen (Better Fundraising Co.), ANA Response Rate Report |
| Housefile Response Rate Median | 3-5% | Practitioner consensus + ANA |
| Housefile ROI Floor | 2:1 | WifiTalents compilation, Five Maples ($0.20 CPDR = 5:1) |
| Housefile ROI Strong | 5:1+ | Five Maples practitioner standard |
| Faith-Sector Avg Gift (all channel) | $50 median | Virtuous/Masterworks 2025 Faith Benchmark |
| HRI Current Avg Gift | $78.43 | Internal FY26 FYTD |
| Mid-Level Response Rate Floor | 3% | WifiTalents Q4 2022 mid-level data (4.2%) |
| Mid-Level ROI Expected | 8:1-10:1 | Practitioner estimates (GFA, NonProfit PRO) |
| Net Revenue Per Piece Floor | $0.00 (breakeven) | Any negative flagged unless strategic |

These are pre-populated in the editor as reference lines on segment economics. Operator can override per-campaign. All values traceable — no arbitrary thresholds.

## 7. What This Replaces

- Current "Re-fit to Budget" button — replaced by target-driven auto-fit with user override
- Current 5-minute wait on every adjustment — replaced by instant browser computation
- Current single-scenario Draft tab — replaced by scenario comparison
- Current static Hist RR / Hist AvgGift / Proj Rev columns — replaced by dynamic recalculation on every edit

## 8. What This Preserves

- Waterfall assignment logic — unchanged
- Suppression rules — unchanged
- BQ cache — unchanged
- Baseline rollup — unchanged (feeds per-donor economics)
- Appeal code generation — unchanged (runs at Approve, not during editing)
- Output file format (Printer/Matchback) — unchanged
- Salesforce Campaign_Segment__c upsert — unchanged
- MIC Draft/Segment Detail tabs — unchanged structure, just populated at Approve time instead of projection time

## 9. Phases

### Phase 1: Universe Endpoint (Cloud Run)
- Build `/build-universe` endpoint that runs waterfall + suppression + baseline rollup
- Returns universe dataset as JSON (~10-15MB)
- Write universe to new MIC `Universe` tab for persistence
- **Gate:** Endpoint returns universe for A2651 in <60s (no output file generation, no Sheets writes beyond Universe tab). Payload validates against schema. Can be re-fetched on campaign / toggle change.

### Phase 2: Browser-Side Scenario Editor
- Replace Draft Tab Preview in Index.html with full scenario editor UI
- Load universe JSON on campaign selection
- Per-segment controls (include/exclude, % slider) — reuse existing work
- Target-type selector (Quantity / Gross Rev / Net Rev / ROI Threshold) with value input
- Greedy solver for each target type in pure JS
- Real-time economics recalculation on every edit
- Scenario comparison panel (save/load/compare up to 5 scenarios)
- **Gate:** Operator loads A2651 universe, picks "Net Revenue target $100K", sees solver output. Operator adjusts DL02 to 20%, sees instant recalc. Saves as "Scenario A", switches target type to "Quantity 45,000", saves as "Scenario B", compares side-by-side.

### Phase 3: Approve → Finalize Endpoint (Cloud Run)
- Build `/approve-scenario` endpoint that accepts the scenario definition
- Applies scenario to universe dataset
- Generates appeal codes, Printer/Matchback files, writes to Drive
- Updates MIC Draft and Segment Detail tabs with the finalized scenario
- Campaign status → Approved
- **Gate:** Operator approves Scenario A. Files generated in <60s. Printer File contains scenario-selected donors. Matchback contains all universe donors with exclusion reasons for non-selected.

**Estimated sessions:** 3 (one per phase)

## 10. Why Not Do This Now

The spec is sound and reduces operator friction 50×. But it's a substantial UI rework. Before committing to it:

1. Current system works end-to-end. Jessica can run the May Shipping campaign on what's built.
2. Per-segment controls (Tier 1+2) address the immediate pain.
3. This spec captures the "right" architecture for the iteration loop — to be built when Jessica's actual usage confirms the need.

**Recommendation:** Ship what's built. Use it for May Shipping. If Jessica's feedback is "I keep re-fitting because I want to test scenarios," build this. If her feedback is "one fit is enough, I just wish the controls were faster," this is overbuilt.

Decision point: after Jessica's first full campaign cycle through the current system.

## 11. Benchmark-Driven Defaults (applicable now, independent of scenario editor)

These can be applied to the current system without waiting for the scenario editor build:

1. **Response Rate Floor toggle default to 0.8% → 2.0%** — per Steven Screen housefile benchmark. Current 0.8% is far below any published floor.
2. **Break-Even Floor toggle default to ON** — per Five Maples, any net-negative segment flagged for review.
3. **Mid-level campaign type flag** — when campaign type = Mid-Level, surface mid-level-specific benchmarks in the UI (3%+ RR floor, 8:1+ ROI expected). Separate ask-string logic already in place.
4. **External benchmark lines on segment economics** — add reference lines to the Draft tab preview: "Housefile median RR: 3-5%", "Faith avg gift: $50", "Current HRI avg: $78". Operator sees anchors during review.

These are small changes applicable in the current system and don't require the full scenario editor.
