# Claude Code — Project Instructions

> **Repository Visibility:** All HRI repos MUST be set to **Private**.
> When creating a new repo from this template, verify the visibility
> is set to Private in the GitHub creation dialog. Public repos risk
> exposing hardcoded credentials, Sheet IDs, and internal email addresses.

## About this project

HRI Segmentation Builder — direct mail segmentation engine. Cloud Run service (Python) + Apps Script web app.
- **Cloud Run:** `segmentation-builder` in `hri-receipt-automation` (us-east1), 2Gi memory, SA `hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com`
- **Cloud Run URL:** `https://segmentation-builder-qelitx2nya-ue.a.run.app`
- **Apps Script project ID:** `1FaECO-ldkuWhS9gFulP_T1GBxqSFJ9_mmeJnBM40y50eIPAG14VpsBdr`
- **Apps Script web app URL:** `https://script.google.com/a/macros/hoperises.org/s/AKfycbwB4bkAboYdzdllptuxHDhcE8TnUpNHIBuFVAWpjQipegzVXc9_8Of8lxXPSpwx8G7AEQ/exec`
- **MIC Sheet:** `12mLmegbb89Rf4-XGPfOozYRdmXmM67SP_QaW8aFTLWw`
- **Drive output folder:** `1GTBtYglpBaAfxynjZM1e3lioTb6O-qyC`
- **Spec:** `SPEC.md` (v3.2) — all 9 phases complete

## Authentication

This project authenticates via GCP service account impersonation. All API calls
(Sheets, Cloud Run, Salesforce, Secret Manager) go through:

    hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Developers authenticate with their own @hoperises.org account and impersonate
the service account. Setup:

    gcloud auth application-default login \
      --impersonate-service-account hri-sfdc-sync@hri-receipt-automation.iam.gserviceaccount.com

Do NOT use personal ADC (`gcloud auth application-default login` without
impersonation). Do NOT create or download service account key files.

## Comprehension Artifacts (required for every build)

**The full protocol lives in one place:** `comprehension-artifacts.md` in `hri-template-repository`.

Every build must produce three files: `SPEC.md` (committed before implementation), then `ARCHITECTURE.md` + `REVIEW.md` (generated after implementation, held for user review before committing). For Phase 2+ work, update rather than regenerate.

At build start, fetch and follow it:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/comprehension-artifacts.md \
  --jq '.content' | base64 -d > /tmp/comprehension-artifacts.md
```

Then read `/tmp/comprehension-artifacts.md` and execute all steps.

## Stack Learnings (canonical source)

Stack-level learnings live in ONE place:
- Repo: `Hope-Rises-International/hri-template-repository`
- File: `hri-stack-learnings.md`
- Read before any infrastructure, auth, deployment, or tooling work.
- Update directly via GitHub API when you discover a stack-level gotcha. See session-end protocol below.

Do NOT create a local `learnings.md` or `hri-stack-learnings.md` in this repo. If one exists, merge any unique content upstream and delete the local copy.

## Project knowledge

<!-- This section grows over time. Every session that makes meaningful changes
     should append what it learned. This is where compound value accrues.

     Good entries answer: What would the NEXT session need to know?
     - Decisions made and WHY (not just what changed — git log has that)
     - Things that are fragile or non-obvious
     - What was tried and didn't work (so nobody tries it again)
     - Patterns discovered in the data or the APIs
     - Gotchas that aren't obvious from reading the code

     Bad entries: "Updated foo.py" (that's a commit message, not knowledge) -->

2026-04-13: SF API credentials migrated from bsimmons@hoperises.org to gcpuser@hoperises.org (API Only User profile). Three secrets updated in Secret Manager: sfdc-username, sfdc-password, sfdc-security-token. Connected App (HRI_Cloud_Sync) unchanged.

---

## Session Start

**The full protocol lives in one place:** `session-start-protocol.md` in `hri-template-repository`.

At session start, fetch and follow it:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/session-start-protocol.md \
  --jq '.content' | base64 -d > /tmp/session-start-protocol.md
```

Then read `/tmp/session-start-protocol.md` and execute all steps.

---

## Session-End Protocol

**The full protocol lives in one place:** `session-end-protocol.md` in `hri-template-repository`.

At session close, fetch and follow it:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/session-end-protocol.md \
  --jq '.content' | base64 -d > /tmp/session-end-protocol.md
```

Then read `/tmp/session-end-protocol.md` and execute all steps.

This ensures every repo uses the latest protocol without needing per-repo updates.
