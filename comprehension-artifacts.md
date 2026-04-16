# Comprehension Artifacts — Required for Every Build

## SPEC.md

Before beginning implementation, commit the full content of the build spec/instruction as `SPEC.md` in the repo root. This is the build spec and must be preserved as-is.

**Commit message:** `Add build spec as SPEC.md`

---

## ARCHITECTURE.md

After implementation is complete but before final commit, generate `ARCHITECTURE.md` in the repo root. Answer all nine of the following questions in plain English. No code snippets. Write for someone who has never seen this repo and needs to understand it in 10 minutes.

1. **What this service does** — One paragraph. What business function does it serve?
2. **How it runs** — Trigger mechanism (Cloud Scheduler cron, manual, webhook?), runtime environment (Cloud Run, Apps Script?), expected frequency.
3. **What it reads from** — Data sources with specifics: which Salesforce objects and fields, which Google Sheets by ID, which APIs.
4. **What it writes to** — Destinations with specifics: which Sheets, which Salesforce objects, which files.
5. **Authentication** — Which credentials it uses (Secret Manager secret names, Apps Script properties), which service accounts.
6. **Dependencies** — External services that must be available for this to work (Salesforce API, Google Sheets API, etc.).
7. **What breaks if this stops running** — Business impact in plain English. Who notices? How quickly?
8. **Three most likely failure modes** — What goes wrong, what the symptom looks like, and how to fix it.
9. **How to manually re-run** — Step-by-step for triggering the service outside its normal schedule.

---

## REVIEW.md

After implementation is complete but before final commit, generate `REVIEW.md` in the repo root. Answer all five of the following questions honestly. Do not default to "everything is great." Surface real trade-offs and real risks.

1. **Why this structure?** — Why is the code organized this way? What alternatives were considered and why were they rejected?
2. **What are the trade-offs?** — What did this approach optimize for and what did it sacrifice? (speed vs. readability, simplicity vs. extensibility, etc.)
3. **What breaks if a dependency changes?** — If Salesforce changes a field name, if a Google Sheet structure changes, if a Cloud Run environment variable is missing — what happens?
4. **What's the failure mode?** — When this code fails in production, what does the failure look like? Error message? Silent failure? Partial write?
5. **What would you do differently with more time?** — What shortcuts were taken? What would a more robust version look like?

---

## Rules

- **STOP** after generating `ARCHITECTURE.md` and `REVIEW.md`. Do **not** commit them until the user has reviewed both files. Report the contents and wait for approval.
- For **Phase 2 or later** modifications to an existing repo, **update** `ARCHITECTURE.md` and `REVIEW.md` to reflect all changes made in this phase rather than generating from scratch. Preserve prior content where it is still accurate.
