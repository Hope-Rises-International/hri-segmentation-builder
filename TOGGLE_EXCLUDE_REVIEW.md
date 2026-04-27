# Toggle-Exclude Code Review

**Build:** hri-segmentation-builder
**Reviewed commit:** post-`a7ca69c` (refactor in this commit; line numbers reflect the refactor)
**Spec reference:** SPEC §6.1
**Author:** Builder, on architect request 2026-04-27

---

## Section 1 — Generic exclusion loop

**File:** `src/waterfall_engine.py`
**Function:** `run_waterfall(accounts_df, rfm_df, lifecycle, cbnc_ids, toggles)`

The exclusion pass runs immediately after Tier 1 hard suppression and before any waterfall position is assigned. It is **one table-driven loop**, not nine per-position calls. The loop body is identical for every position — only the data row varies.

```python
# src/waterfall_engine.py:181–207

TOGGLE_EXCLUDE_RULES = [
    ("major_gift",         True,  "Major Gift Portfolio",
     lambda: staff_mgr.notna() & (staff_mgr != "")),
    ("mid_level",          True,  "Mid-Level",
     lambda: (cumulative >= MID_LEVEL_MIN) & (cumulative <= MID_LEVEL_MAX)
             & (months_since_last <= MID_LEVEL_ACTIVE_MONTHS)),
    ("sustainer",          False, "Sustainers",
     lambda: accts.get("Miracle_Partner__c", pd.Series(False, index=accts.index)) == True),
    ("cornerstone",        True,  "Cornerstone",
     lambda: accts.get("Cornerstone_Partner__c", pd.Series(False, index=accts.index)) == True),
    ("new_donor",          False, "New Donor",
     lambda: accts["lifecycle_stage"] == "New Donor"),
    ("active_housefile",   True,  "Active Housefile",
     lambda: accts["R_bucket"].isin(["R1", "R2"])),
    ("mid_level_prospect", True,  "Mid-Level Prospect",
     lambda: (cumulative >= MID_LEVEL_PROSPECT_MIN)
             & (cumulative <= MID_LEVEL_PROSPECT_MAX)
             & (months_since_last <= MID_LEVEL_ACTIVE_MONTHS)),
    ("lapsed",             True,  "Lapsed Recent",
     lambda: (accts["R_bucket"] == "R3") & (total_gifts_pre >= 2)),
    ("deep_lapsed",        True,  "Deep Lapsed",
     lambda: accts["R_bucket"].isin(["R4", "R5"])
             & (cumulative >= 10) & (months_since_last <= 48)),
]

for toggle_key, default_on, label, mask_fn in TOGGLE_EXCLUDE_RULES:
    if toggles.get(toggle_key, default_on):
        continue
    _suppress(mask_fn(), f"Toggle Exclude: {label}")
```

To add or change a toggle, edit one row in `TOGGLE_EXCLUDE_RULES`. No other code changes.

---

## Section 2 — Per-position table

| # | Position | Toggle field | OFF excludes donors where… | Code location |
|---|---|---|---|---|
| 2 | Major Gift Portfolio | `toggles["major_gift"]` (default ON) | `Staff_Manager__c IS NOT NULL AND Staff_Manager__c != ""` | `src/waterfall_engine.py:182–183` |
| 3 | Mid-Level | `toggles["mid_level"]` (default ON) | `npo02__TotalOppAmount__c BETWEEN $1,000 AND $4,999.99 AND months_since_last_gift <= 24` | `src/waterfall_engine.py:184–186` |
| 4 | Sustainers | `toggles["sustainer"]` (default OFF) | `Miracle_Partner__c = TRUE` | `src/waterfall_engine.py:187–188` |
| 5 | Cornerstone | `toggles["cornerstone"]` (default ON) | `Cornerstone_Partner__c = TRUE` | `src/waterfall_engine.py:189–190` |
| 6 | New Donor | `toggles["new_donor"]` (default OFF) | `lifecycle_stage = "New Donor"` (90-day welcome window) | `src/waterfall_engine.py:191–192` |
| 7–8 | Active Housefile | `toggles["active_housefile"]` (default ON) | `R_bucket IN ("R1", "R2")` (gave in last 12 months) | `src/waterfall_engine.py:193–194` |
| 9 | Mid-Level Prospect | `toggles["mid_level_prospect"]` (default ON) | `npo02__TotalOppAmount__c BETWEEN $500 AND $999.99 AND months_since_last_gift <= 24` | `src/waterfall_engine.py:195–198` |
| 10 | Lapsed Recent | `toggles["lapsed"]` (default ON) | `R_bucket = "R3" AND npo02__NumberOfClosedOpps__c >= 2` (13–24mo, 2+ lifetime gifts) | `src/waterfall_engine.py:199–200` |
| 11 | Deep Lapsed | `toggles["deep_lapsed"]` (default ON) | `R_bucket IN ("R4", "R5") AND cumulative >= $10 AND months_since_last_gift <= 48` | `src/waterfall_engine.py:201–203` |

Toggle defaults source: `src/config.py::DEFAULT_TOGGLES`. The same defaults are applied in two places — `TOGGLE_EXCLUDE_RULES` (column 2) and the toggle-gate at each waterfall position. Both must agree; deviation would cause a donor to be excluded but no segment to know about it.

Positions 1 (Tier 1 hard suppression) and 12 (CBNC override) are **not toggleable** by design (SPEC §6.1) and so do not appear in this table.

---

## Section 3 — Self-check

For each row I confirmed:

1. **Mask matches the assignment criteria of the same position.** Every `mask_fn()` is the boolean expression that the position's `_assign(...)` call uses on its happy-path branch. I diffed the two side by side. The Active Housefile entry is the only one where the toggle covers a *broader* mask than the assignment: at the assign step, AH splits into 6 sub-segments by recency × monetary; on exclude, the toggle removes anyone in `R1` or `R2`. Spec §6.1 states "Active Housefile" is the toggle's scope — both R1 and R2 — so this matches. No deviation.

2. **All 9 positions go through the same loop.** No `if toggle_key == "cornerstone": …` branches anywhere in the file. `grep "Toggle Exclude:"` yields a single producer (the one `_suppress` call inside the loop) and one consumer (the post-loop log line).

3. **Edge cases:**
   - **Toggle key absent from `toggles` dict** → `toggles.get(key, default_on)` returns the spec default. Confirmed for all 9 keys against `config.py::DEFAULT_TOGGLES`.
   - **Donor matches multiple OFF toggles** → `_suppress` only marks `~assigned` rows; the first matching toggle wins for the audit reason, subsequent toggles are no-ops on the same donor. Donor is still excluded — same end state.
   - **Donor would have been Tier 1 suppressed already** → Tier 1 ran before the loop, those donors are already `assigned=True`, the toggle pass skips them. They keep their Tier 1 reason. No double-counting.
   - **Field missing from accounts cache** (e.g., a future SF schema change drops `Staff_Manager__c`) → `accts.get("Staff_Manager__c", pd.Series(None, index=accts.index))` returns an empty Series, `notna() & (… != "")` evaluates to all-False, no donor is excluded. The toggle becomes a no-op rather than crashing — graceful degradation.

4. **Spec alignment with SPEC §6.1:**
   - Position numbers 2–11 in §6.1 ↔ rule order in the loop. ✓
   - Defaults match (major_gift/mid_level/cornerstone/active_housefile/mid_level_prospect/lapsed/deep_lapsed = ON; sustainer/new_donor = OFF). ✓
   - "Toggle OFF means exclude these donors from the universe entirely. Not just from the position — from the campaign." (item 12 of the 2026-04-27 instruction, now SPEC §6.1) ↔ `_suppress(mask_fn(), …)` at line 209. ✓

**No deviations found.** All 9 toggleable positions follow the same code path; none has a per-position patch; criteria match SPEC §6.1.

### Live verification (already shipped)

A2651 with `toggles.cornerstone = false` ran post-deploy:
- `CornerstoneFlag = TRUE` count in any segment of the Matchback: **0/53,985** rows.
- CS01 segment present: **no** (segment dropped from output entirely).
- Matchback segment count: 16 (was 17 with default toggles).

Architect: code review only; no further verification requested per your instruction.
