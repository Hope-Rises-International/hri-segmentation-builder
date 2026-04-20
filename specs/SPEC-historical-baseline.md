# SPEC: Historical Baseline — Multi-Campaign Segment Averages

**Date:** 2026-04-20
**Author:** Architect
**Status:** Draft — pending Bill approval
**Scope:** Build a static multi-campaign baseline table (response rate + avg gift per HRI segment) from the Campaign Scorecard historical data, replacing the single-campaign rollup as the default economics source

---

## 1. Problem

The scenario editor's economics columns depend on a baseline campaign's actuals. Currently the operator picks one prior campaign (A2551) and the rollup maps its TLC source codes to HRI segments.

Problems with this:
- **Noisy.** One campaign's response rate is a single data point. Real response rates vary ±50% between campaigns for the same segment.
- **Incomplete.** Campaigns don't cover all HRI segment types. CS01 (Cornerstone), ML01 (Mid-Level), MJ01 (Major Gift), CB01 (CBNC), MP01 (Mid-Level Prospect) have no direct TLC equivalents — the rollup returns 0% for them.
- **Point-in-time artifact.** A2551 was one Shipping campaign. Its response rates reflect that specific offer, list condition, and seasonality — not HRI's underlying donor behavior.

Current result: $3,147 projected net on 36,139 contacts when actual last year was ~$12K net. The rollup is losing ~75% of expected revenue.

## 2. Solution

Build a **Historical Baseline grid** — one row per (HRI segment × campaign type) with multi-campaign average response rate and average gift, computed across historical campaigns in the Scorecard.

Not all campaigns are created equal. Response rates and avg gifts for a shipping label appeal differ materially from a tax receipt mailing or a year-end emergency appeal. Each recurring campaign type gets its own baseline grid. When a campaign type has insufficient historical volume, the operator falls back to the "Overall" meta-average across all campaign types.

The operator picks **either**:
- **A specific prior campaign** as baseline (best when 6-12+ months of HRI-originated A26xx data exists), OR
- **A campaign type** — e.g., "Shipping" for A2651 — which returns the aggregate of all Shipping appeals as pro-forma economics, OR
- **Overall** — meta-average across all campaign types when no specific match exists

This replaces the current single-campaign rollup as the default economics source.

## 3. Data Source

Campaign Scorecard Excel file (in `reference/HRI Campaign Scorecard Data.xlsx`) — the same data the Scorecard writes to its Google Sheet nightly. Specifically the **Campaign Detail** sheet:

- 10,564 rows
- 196 campaigns
- 35 columns
- Level 3 rows are segment-level: `source_code`, `contacts`, `gifts`, `revenue`, `cost`, `response_rate`, `avg_gift`

## 4. Aggregation Logic

### Step 1: Parse TLC source codes to HRI segments

Same parser used in current rollup (`baseline_rollup.py`). Maps TLC source code patterns to HRI segment codes:

- `BH6`, `BH7`, `AH6`, `AH7`, `BA6`, `BA7` (0-6mo, $50+) → **AH01**
- `BH4`, `BH5`, `AH4`, `AH5`, `BA4`, `BA5` (0-6mo, $10-49) → **AH02**
- `BH2`, `BH3`, `AH2`, `AH3`, `BA2`, `BA3` (0-6mo, <$10) → **AH03**
- `BI6`, `BI7`, `AI6`, `AI7`, `BB6`, `BB7` (7-12mo, $50+) → **AH04**
- `BI4`, `BI5`, `AI4`, `AI5`, `BB4`, `BB5` (7-12mo, $10-49) → **AH05**
- `BI2`, `BI3`, `AI2`, `AI3`, `BB2`, `BB3` (7-12mo, <$10) → **AH06**
- `BJ*`, `AJ*`, `BC*` (13-24mo) → **LR01** or **LR02** by recency sub-band
- `BK*`, `AK*`, `BD*` (25-36mo) → **DL01/DL02** by monetary
- Deep lapsed 37-48mo patterns → **DL03/DL04**
- M-prefix codes → **ML01**

### Step 2: Classify each campaign by type

Each historical campaign is assigned a type based on its name, appeal code pattern, and/or lane metadata. Types:

Classification is order-sensitive: **test for "Chaser" before the base type match.** A campaign name like "May 2025 Shipping Label Appeal Chaser — Housefile" must classify as "Shipping Chaser", not "Shipping".

| Type | Pattern / Identifier | Examples |
|------|----------------------|----------|
| **Shipping** | Campaign name contains "Shipping" AND does NOT contain "Chaser" | A2551, A2651 |
| **Shipping Chaser** | Campaign name contains "Shipping" AND contains "Chaser" | A2552 |
| **Tax Receipt** | Campaign name contains "Tax Receipt" AND does NOT contain "Chaser" | A2422, A2522 |
| **Tax Receipt Chaser** | Campaign name contains "Tax Receipt" AND contains "Chaser" | |
| **Year End** | Campaign name contains "Year End" (any variant) AND does NOT contain "Chaser" | A25B1, A2661 |
| **Year End Chaser** | Campaign name contains "Year End" AND contains "Chaser" | |
| **Easter** | Campaign name contains "Easter" AND does NOT contain "Chaser" | A2531, A2631 |
| **Easter Chaser** | Campaign name contains "Easter" AND contains "Chaser" | A2532, A2632 |
| **Renewal** | Campaign name contains "Renewal" AND does NOT contain "Chaser" | A2512, A2611 |
| **Renewal Chaser** | Campaign name contains "Renewal" AND contains "Chaser" | A2543 |
| **Faith Leaders** | Campaign name contains "Faith Leaders" AND does NOT contain "Chaser" | A2491 |
| **Faith Leaders Chaser** | Campaign name contains "Faith Leaders" AND contains "Chaser" | |
| **Shoes** | Campaign name contains "Shoes" AND does NOT contain "Chaser" | A2581 |
| **Shoes Chaser** | Campaign name contains "Shoes" AND contains "Chaser" | A2582 |
| **Whole Person Healing** | Campaign name contains "Whole Person Healing" AND does NOT contain "Chaser" | A2641 |
| **Whole Person Healing Chaser** | Contains "Whole Person Healing" AND "Chaser" | |
| **FYE / Fiscal Year End** | Campaign name contains "FYE" OR "Fiscal Year End" AND does NOT contain "Chaser" | A2561 |
| **FYE Chaser** | Contains "FYE" or "Fiscal Year End" AND "Chaser" | A2562 |
| **Newsletter** | `lane` = "Newsletter" | W2531 |
| **Acquisition** | `lane` = "Acquisition" | D2591, D2581 |
| **Other** | Everything else not matching above | |

Chasers are separate types because they have different response rate profiles — the initial appeal captures the highest-intent respondents, and the chaser works a more saturated universe. Aggregating them together would distort both baselines.

The classification rules live in config. When a new recurring campaign type emerges, add a pattern to the config.

### Step 3: Aggregate by (HRI segment × campaign type)

For each combination of HRI segment and campaign type, across qualifying historical campaigns:

```
weighted_response_rate = sum(gifts) / sum(contacts)
weighted_avg_gift = sum(revenue) / sum(gifts)
campaign_count = count(distinct appeal_code)
total_contacts = sum(contacts)
total_gifts = sum(gifts)
total_revenue = sum(revenue)
```

Weighted by contact volume. High-volume campaigns dominate the baseline for their type, which is statistically correct.

### Step 3b: Compute the Overall meta-average

For each HRI segment, also compute an "Overall" row — weighted average across ALL campaign types (except Acquisition, which is structurally different and should not dilute housefile baselines). This is the fallback when the operator picks a campaign type with no historical data for that segment.

### Step 3: Filter for quality

Exclude from aggregation:
- Campaigns with < 500 contacts (too small, noisy)
- Acquisition campaigns (cold mail, different behavior)
- Emergency/disaster appeals (atypical response rates)
- Campaigns older than FY22 (response behavior shifted post-COVID)
- Segments with < 3 campaigns contributing (not enough statistical power — flag as "low confidence" but include)

### Step 4 (renumbered from prior): Handle HRI-native segments with no TLC equivalent

Applied within each campaign type grid independently. A segment that uses a proxy in Shipping uses the same proxy logic in Tax Receipt — but computed from that type's historical data.

**CS01 (Cornerstone):** No TLC equivalent. Use proxy: weighted average of high-retention segments (BH6, BH7, BI6, BI7) where recency is strong and monetary is high. Flag with confidence="proxy".

**MJ01 (Major Gift):** No TLC equivalent. Use proxy: BH7, BI7 (0-12mo, $100+). Flag with confidence="proxy".

**ML01 (Mid-Level):** Use M-prefix codes from Scorecard. Should have real data.

**MP01 (Mid-Level Prospect $500-$999):** Proxy: BH6 + BI6 (0-12mo, $50+). Flag with confidence="proxy".

**CB01 (CBNC):** Hardest — these are donors who appear lapsed but give when mailed. Use proxy: LR01 average response rate scaled up by 1.5× (CBNC by definition are responsive). Flag with confidence="estimate".

### Step 5: Output table

One row per (HRI segment × campaign type + Overall), with columns:

| Column | Type | Notes |
|--------|------|-------|
| `campaign_type` | Text | Shipping, Tax Receipt, Year End, Easter, Renewal, Faith Leaders, Shoes, Whole Person Healing, Newsletter, Other, Overall |
| `hri_segment_code` | Text | AH01, AH02, ..., CB01 |
| `segment_name` | Text | Human-readable |
| `response_rate` | Float | Weighted across campaigns of this type |
| `avg_gift` | Currency | Weighted across campaigns of this type |
| `revenue_per_contact` | Currency | Response rate × avg gift |
| `campaign_count` | Int | Number of campaigns contributing |
| `total_contacts` | Int | Across campaigns of this type |
| `total_gifts` | Int | Across campaigns of this type |
| `total_revenue` | Currency | Across campaigns of this type |
| `confidence` | Text | "high" (≥3 campaigns, direct TLC mapping), "proxy" (uses proxy logic), "estimate" (< 3 campaigns, low volume), "fallback" (insufficient data for this type — use Overall) |
| `last_refreshed` | Timestamp | When this table was last rebuilt |

Result: ~21 campaign types (10 base + 9 chaser variants + Newsletter + Acquisition + Other) × 17 HRI segments = ~357 rows. Plus 17 Overall rows = ~374 rows total.

## 5. Where This Lives

**Primary:** BigQuery table `sf_cache.historical_baseline` — computed nightly from the Scorecard data.

**Secondary:** Written to a new MIC tab called `Historical Baseline` for operator visibility.

**Refresh cadence:** Nightly, as part of the existing BQ cache refresh job. The Scorecard writes to its own sheet and MIC `Segment Actuals` nightly; this new table computes from `Segment Actuals` and writes to BQ + MIC.

## 6. How It Feeds the Scenario Editor

### Step 1 UI — Baseline Selector Change

The "Compare to baseline campaign (optional)" dropdown becomes three grouped options:

```
Baseline Source (required):
  ○ Campaign Type (pro-forma) ← default
      [Dropdown: Shipping | Tax Receipt | Year End | Easter | Renewal | Faith Leaders | Shoes | Whole Person Healing | Newsletter | Other | Overall]
  ○ Specific Prior Campaign (compare to single campaign)
      [Dropdown: A2551 — May 2025 Shipping — FY2025 | ... ]
```

**Auto-selection:** When the operator picks a campaign in Step 1 (e.g., FY26 Shipping A2651), the baseline selector auto-populates to "Campaign Type: Shipping". Operator can change it.

When the operator picks "Campaign Type":
- Universe builds using that type's multi-campaign average
- Per-segment economics = weighted avg of all historical campaigns of that type
- Label on the Draft tab: "Baseline: Shipping (aggregate of 8 historical campaigns)"

When the operator picks "Specific Prior Campaign":
- Universe builds using that campaign's rollup (current behavior)
- Label on the Draft tab: "Baseline: A2551 — May 2025 Shipping"

### Fallback Logic

If a segment has `confidence = "fallback"` for the selected campaign type (no historical data for that segment + type combination), the pipeline silently uses the Overall row for that segment. UI shows a small indicator: "Fallback: Overall average".

### Backend Change

`build_universe.py`:
1. Accepts new parameter `baseline_type` in addition to `baseline_appeal_code`
2. If `baseline_type` is set, reads from `sf_cache.historical_baseline WHERE campaign_type = {type}`, falling back to `campaign_type = 'Overall'` for any segment without data for that type
3. If `baseline_appeal_code` is set (legacy path), uses existing `baseline_rollup.py`
4. Exactly one of these is required; the UI enforces the choice

The scenario editor UI stays the same below Step 1. The numbers behind it get dramatically more robust.

## 7. Phases

### Phase 1: Build the baseline grid
- Create `build_historical_baseline.py` in the segmentation-builder repo
- Campaign-type classification config (11 types + Overall) in `config/campaign_types.py`
- Reads Scorecard data (either from BQ if available, or from the Sheet/Excel)
- Applies parsing + campaign-type classification + aggregation logic above
- Writes to BQ `sf_cache.historical_baseline`
- Writes to MIC `Historical Baseline` tab (viewable as a grid: types as columns, segments as rows)
- Includes in nightly BQ refresh job
- **Gate:** Table has ~200 rows (17 segments × 11 types + 17 Overall). Shipping row for AH01 plausible (3-4% RR, ~$80 avg gift). CS01/MJ01/CB01 flagged as proxy/estimate. Overall row provides a sensible fallback for any (segment, type) pair without data.

### Phase 2: Wire into scenario editor
- Step 1 UI adds baseline source selector: Campaign Type (default, with dropdown of 11 types) OR Specific Prior Campaign OR Overall
- Campaign Type auto-populates based on campaign name (A2651 Shipping → "Shipping")
- `build_universe.py` accepts `baseline_type` or `baseline_appeal_code` parameter (exactly one)
- Fallback logic: segments with no (type, segment) data silently use Overall
- UI shows confidence badge per segment ("high" / "proxy" / "estimate" / "fallback")
- Draft tab header shows selected baseline: "Baseline: Shipping (aggregate of 8 historical campaigns)" or "Baseline: A2551 — May 2025 Shipping"
- **Gate:** Load universe for A2651 with "Shipping" type selected. AH01 shows ~3.5% RR based on Shipping-only aggregate. Switch to "Tax Receipt" baseline type — AH01 values shift (Tax Receipts perform differently). Switch to "Specific Prior Campaign: A2551" — values match prior single-campaign rollup. All three modes work. Projected net for 54K contacts using Shipping baseline is within 30% of last year's actual $12K (so $8K-$16K range).

**Estimated sessions:** 1-2

## 8. What This Changes

- Default baseline goes from "one campaign" to "weighted average of ~150 qualifying campaigns"
- Segments previously showing 0% RR now show real values
- Projected net revenue converges toward actual historical performance
- Operator can still pick a specific baseline for comparison, but no longer has to

## 9. What This Does NOT Change

- Waterfall assignment — unchanged
- Suppression logic — unchanged
- Scenario editor UI — unchanged (confidence badge is additive)
- Output files — unchanged
- Salesforce writes — unchanged

## 10. Why This Is the Right Move

- Addresses root cause: baseline data incompleteness, not pipeline logic
- Uses data already computed by the Scorecard, not new extractions
- Static table = fast reads, no complex real-time computation
- Refreshes nightly as Scorecard data updates
- Degrades gracefully — if a segment has no historical data at all, flagged as "estimate" rather than 0%
- When HRI's own A2651+ campaigns produce enough data (10+ campaigns), this table naturally shifts toward HRI-originated performance without changing the architecture
