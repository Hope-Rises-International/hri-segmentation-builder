# RESOLUTION — Historical Baseline Attribution Methodology

**Date:** 2026-04-20
**Build:** hri-segmentation-builder (Historical Baseline build)
**Type:** Mid-flight spec clarification

## Context

VeraData (agency) flagged matchback attribution as a gap during their review of the segmentation spec. Architect and Bill reviewed — the gap exists, but HRI's direct-attribution approach has been consistent across every historical campaign, so segment ratios remain valid.

## Change to Apply

Add an attribution methodology section and a `revenue_basis` column to the Historical Baseline output.

### Methodology note (add to Step 4b, before "Step 5: Output table")

HRI has tracked direct response to direct appeals consistently for the entire history reflected in the Scorecard data. Revenue in the baseline is:

- **Included:** direct mail reply device responses (checks, reply envelopes, phone-in gifts referencing the appeal code)
- **Not included:** matchback revenue from other channels (online, DAF, IRA, whitemail) that may correlate with the mailing but are attributed to other source codes

Because this attribution rule has been consistent across every historical campaign:

- Segment rankings against each other are valid
- Ratios between segments (e.g., AH01 response rate vs LR01) are valid
- Absolute projected revenue is directionally correct but understates total value because other revenue streams correlate with mailings at consistent rates
- Operator decisions (include/exclude, % reduction) based on relative segment performance are sound

This limitation does not require remediation in the Segmentation Builder. If HRI adds cross-channel matchback attribution to the Scorecard (a separate future enhancement), this baseline will recalibrate upward proportionally without a spec change here.

### Output table addition

Add a `revenue_basis` column to the Historical Baseline table with default value `"direct_attribution"`.

### MIC tab header note

At the top of the `Historical Baseline` MIC tab, above the data rows, add this one-line methodology note:

> Direct-attribution response only. Cross-channel revenue (online, DAF, IRA) not included but correlates proportionally.

## What This Does NOT Change

- Aggregation logic — unchanged
- Campaign-type grid structure — unchanged
- Per-segment proxy/confidence rules — unchanged
- Phase gates — unchanged
- Builder completion criteria — unchanged

Apply this change before submitting Phase 1 for gate review.
