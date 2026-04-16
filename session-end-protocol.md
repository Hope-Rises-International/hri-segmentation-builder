# Session-End Protocol

> **Canonical source:** `Hope-Rises-International/hri-template-repository/session-end-protocol.md`
> All repos fetch this file at session close. To change the protocol, edit THIS file — not individual repos.

Complete these steps before ending any session that made code changes, config changes, or significant decisions.

## Step 1: Update Project Knowledge (always)

Append a structured entry to the "Project Knowledge" section in this repo's CLAUDE.md using this format:

---
**[YYYY-MM-DD | <user> | <scope>]**
- **Decided:** <what was decided and why>
- **Changed:** <what was modified — systems, files, config — at summary level, not a file list>
- **Watch out:** <anything the next session needs to know — gotchas, incomplete work, fragile state>
- **Open:** <anything unfinished, with enough context to resume without re-reading this whole session>
---

Rules:
- Write for the NEXT session, not as a record of THIS session. If it doesn't help the next reader, don't include it.
- No commit hashes. No "updated X file" play-by-play. Decisions and state, not activity logs.
- The "Open" field replaces any need for a separate handoff file. If there's nothing open, omit the field.
- `<user>` is the person directing the session (e.g., "Bill", "Craig"). `<scope>` is a 3-5 word summary of the session's focus.

### Anti-pattern: Session-handoff files

**Do not create session-handoff files.** No `session-handoff.json`, `handoff.md`, `TODO-next-session.md`, or similar. CLAUDE.md is the handoff mechanism — the "Open" field in the Project Knowledge entry captures unfinished work with full context.

If a session-handoff file exists from a prior session, read it, incorporate anything still relevant into a new Project Knowledge entry, then delete the file and commit the deletion.

If a session is losing context and needs to end with unfinished work, the directing user will ask you to write a forward-looking Project Knowledge entry. Write it as a briefing for a fresh session that has zero memory of this conversation. That entry IS the handoff.

### What goes in Project Knowledge vs. what doesn't

| Belongs in Project Knowledge | Does NOT belong in Project Knowledge |
|------------------------------|--------------------------------------|
| Architectural decisions and why | Commit messages or file-level changelogs |
| API behavior discovered during session | Stack/infra gotchas (these go in `hri-stack-learnings.md`) |
| Business logic choices | Generic how-to knowledge |
| Current state of incomplete work | Routine "updated the registry" confirmations |
| Constraints or blockers for next session | Anything the next session can discover by reading the code |

## Step 2: Update stack learnings (conditional)

If this session discovered a stack-level gotcha (authentication, API behavior, deployment, tooling, environment), update the canonical `hri-stack-learnings.md` in `hri-template-repository` directly.

**How to update from any repo without switching context:**

```bash
# 1. Fetch current file contents and SHA
gh api /repos/Hope-Rises-International/hri-template-repository/contents/hri-stack-learnings.md \
  --jq '{content: .content, sha: .sha}' > /tmp/stack-learnings-meta.json

# 2. Decode current content
cat /tmp/stack-learnings-meta.json | jq -r '.content' | base64 -d > /tmp/hri-stack-learnings.md

# 3. Edit the file — slot new entry under the correct topic heading
#    (Do not append to the bottom. Find the right section.)

# 4. Push the update
SHA=$(cat /tmp/stack-learnings-meta.json | jq -r '.sha')
CONTENT=$(base64 -i /tmp/hri-stack-learnings.md | tr -d '\n')
gh api /repos/Hope-Rises-International/hri-template-repository/contents/hri-stack-learnings.md \
  --method PUT \
  --field message="Stack learning: <brief description>" \
  --field content="$CONTENT" \
  --field sha="$SHA"
```

**What qualifies as a stack learning:**
- Authentication or credential behavior (OAuth scopes, token refresh, ADC quirks)
- API behavior that differs from documentation
- Deployment or CI/CD gotchas (Cloud Functions, Apps Script, clasp)
- Environment or tooling issues (Python versions, npm, gcloud CLI)
- Google Workspace API quirks (Drive, Sheets, Gmail, Calendar)

**What does NOT qualify:**
- Project-specific business logic
- Configuration values unique to one system
- Decisions about how to structure a feature (that's Project Knowledge)

## Step 3: Update HRI Systems Registry (always)

Send a POST to the Sheets Bridge with at minimum a Repositories upsert and Build Log append.

**Endpoint & Auth:**

```
URL:      https://script.google.com/macros/s/AKfycbzen_jFFDULJQhj7rPK8khEopSRR1YIusTDqI7dpQPrefdi7Lxcv_J5UimlolVg49i-vQ/exec
API Key:  b3317d76-a5f3-4657-9881-6f087455173d
Sheet ID: 115R3nf55qL23dftoZQg3jOQh5Drqvj-Cd08lsyR_Z2g
```

Apps Script redirects POST responses. Use a two-step curl:

```bash
REDIRECT_URL=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST \
  "https://script.google.com/macros/s/AKfycbzen_jFFDULJQhj7rPK8khEopSRR1YIusTDqI7dpQPrefdi7Lxcv_J5UimlolVg49i-vQ/exec" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") && curl -s -L "$REDIRECT_URL"
```

### Operations to send

| When | Tab | Mode | matchColumns |
|------|-----|------|-------------|
| Always | Repositories | upsert | `["Repository"]` |
| Always | Build Log | append | — |
| If deployed/updated | Deployed Services | upsert | `["Service Name"]` |
| If secrets changed | Credentials & Secrets | upsert | `["Secret Name"]` |
| If tools changed | Tool Inventory | upsert | `["Tool Name"]` |
| If sheets connected | Connected Sheets | upsert | `["Sheet Name"]` |
| If significant decision | Architecture Decisions | append | — |

### Column headers (exact, case-sensitive)

**Repositories:** `Repository` | `Platform` | `URL` | `Status` | `Last Updated` | `Notes`

**Build Log:** `Date` | `Builder` | `System` | `Action` | `Issues Encountered` | `Resolution` | `Time Spent` | `Notes`

**Deployed Services:** `Service Name` | `Type` | `Platform` | `Purpose` | `Status` | `Date Deployed` | `Region` | `Notes`

**Tool Inventory:** `Tool Name` | `Category` | `Status` | `Platform` | `Data Source` | `Notes`

**Credentials & Secrets:** `Secret Name` | `Location` | `Purpose` | `Authenticates To` | `Type` | `Created` | `Last Rotated`

**Connected Sheets:** `Sheet Name` | `Sheet ID` | `URL` | `Purpose` | `Updated By` | `Update Frequency` | `Shared With` | `Notes`

**Architecture Decisions:** `Date` | `Decision` | `Context` | `Rationale` | `Alternatives Considered` | `Consequences`

**SF Field Reference:** `Object` | `Field Label` | `API Name` | `Data Type` | `Package/Namespace` | `Verified Date` | `Used In` | `Notes`

**GL Code Exclusions:** `GL Code` (additional columns TBD)

### Payload template

```bash
PAYLOAD=$(cat <<'EOF'
{
  "apiKey": "b3317d76-a5f3-4657-9881-6f087455173d",
  "sheetId": "115R3nf55qL23dftoZQg3jOQh5Drqvj-Cd08lsyR_Z2g",
  "operations": [
    {
      "tab": "Repositories",
      "mode": "upsert",
      "matchColumns": ["Repository"],
      "data": {
        "Repository": "REPO_NAME",
        "Platform": "GitHub",
        "URL": "https://github.com/Hope-Rises-International/REPO_NAME",
        "Status": "Active",
        "Last Updated": "YYYY-MM-DD",
        "Notes": "WHAT_CHANGED"
      }
    },
    {
      "tab": "Build Log",
      "mode": "append",
      "data": {
        "Date": "YYYY-MM-DD",
        "Builder": "Claude Code",
        "System": "SYSTEM_NAME",
        "Action": "WHAT_WAS_DONE",
        "Issues Encountered": "None or describe",
        "Resolution": "N/A or describe",
        "Time Spent": "ESTIMATE",
        "Notes": "CONTEXT"
      }
    }
  ]
}
EOF
)
```

### Important notes

- Do NOT read the Google Sheet directly via URL — use the bridge API endpoint above.
- Column names are case-sensitive and must match exactly.
- Dates: `YYYY-MM-DD` strings, not Date objects.
- Check the response — `"action": "error"` means a column name didn't match.

## Step 4: Update Project Status Tracker (always)

Update this project's row in the **Project Status Tracker** sheet. This is a high-level status board so Bill can see where every build stands at a glance.

**Sheet ID:** `1KD7Zds9WlIQRubD2z7r2SmcTIWbIQCQtkfE713P88Ng`

**Column headers (exact, case-sensitive):**

`Project #` | `Project Title` | `Repo` | `Status` | `Phase` | `Last Builder` | `Last Activity` | `Next Up` | `Last Updated` | `Total Lines of Code` | `Calculated Human Hours` | `$ for Human Equivalent` | `$ HRI Internal Hours` | `Net Value`

- **Project #** — Sequential ID (001, 002, …). **Permanent — never changes.** If this repo already has one, keep it. If this is the first session for a new project, assign the next available number. When a project is archived, its number moves with it to the Archived tab. Do not renumber remaining projects.
- **Status** — One of: `Active`, `Deployed`, `Paused`, `Planning`, `Archived`
- **Phase** — e.g., "Deployed", "Phase 1 Complete", "In Development", "Not Started"
- **Last Builder** — Who directed this session: `Bill`, `Craig`, or other name
- **Last Activity** — What happened this session, **10 words max**
- **Next Up** — What should happen next session, **10 words max**
- **Last Updated** — Today's date (YYYY-MM-DD). **Never leave blank.** Always set to today when updating any row.
- **Total Lines of Code** — Count of code lines in the repo (exclude CLAUDE.md, package-lock.json, node_modules, .git). Update if significant code was added/removed this session. Write `0` for newly scaffolded repos.
- **Calculated Human Hours** — `Total Lines of Code / 10` (10 LOC per human hour). Write `0` if LOC is 0.
- **$ for Human Equivalent** — `Calculated Human Hours * 150` ($150/hr blended dev rate). Write `0` if hours is 0.
- **$ HRI Internal Hours** — Estimated internal staff time babysitting/directing builds. Use `$600/day` per person involved. Add $600 for each full build day in this session. Read the existing value and increment it — this is a running total, not a per-session value. Write `0` for quick-fix sessions.
- **Net Value** — `$ for Human Equivalent` minus `$ HRI Internal Hours`. Write `0` if both are 0.

There are two tabs: **Active** (current projects) and **Archived** (retired/demo projects). Write to the tab where this project lives.

**How to update:** Use the Sheets API directly (not the Sheets Bridge — this is a separate sheet).

```python
import warnings; warnings.filterwarnings('ignore')
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import json

with open('/Users/USERNAME/.config/gcloud/application_default_credentials.json') as f:
    adc = json.load(f)
creds = Credentials(
    token=None, refresh_token=adc['refresh_token'],
    client_id=adc['client_id'], client_secret=adc['client_secret'],
    token_uri='https://oauth2.googleapis.com/token'
)
creds.refresh(Request())

service = build('sheets', 'v4', credentials=creds)
SHEET_ID = '1KD7Zds9WlIQRubD2z7r2SmcTIWbIQCQtkfE713P88Ng'

# 1. Read all rows to find this project's row number
result = service.spreadsheets().values().get(
    spreadsheetId=SHEET_ID, range="Active!A:N"
).execute()
rows = result.get('values', [])

# 2. Find the row where column C (Repo) matches this repo name
target_row = None
for i, row in enumerate(rows):
    if len(row) > 2 and row[2] == 'REPO_NAME':
        target_row = i + 1  # 1-indexed
        break

# 3. Count lines of code in this repo
import subprocess
loc_result = subprocess.run(
    'find . -type f \\( -name "*.py" -o -name "*.js" -o -name "*.gs" -o -name "*.html" '
    '-o -name "*.css" -o -name "*.json" -o -name "*.sh" -o -name "*.ts" -o -name "*.tsx" '
    '-o -name "*.jsx" \\) ! -name "package-lock.json" ! -name "CLAUDE.md" '
    '! -path "*/node_modules/*" ! -path "*/.git/*" -exec cat {} + | wc -l',
    shell=True, capture_output=True, text=True
)
loc = int(loc_result.stdout.strip()) if loc_result.stdout.strip() else 0
hours = round(loc / 10)
human_cost = hours * 150

# 4. Get existing internal cost (running total — read before overwriting)
existing_internal = 0
if target_row and len(rows[target_row-1]) > 12:
    val = str(rows[target_row-1][12]).replace("$","").replace(",","")
    existing_internal = int(val) if val else 0

# Add $600 per build day this session (adjust as needed)
SESSION_DAYS = 1  # Set to actual days spent this session
internal_cost = existing_internal + (SESSION_DAYS * 600)
net_value = human_cost - internal_cost

# 5. Update or append
# IMPORTANT: Project # is permanent. Never overwrite column A on existing rows.
# IMPORTANT: Write numeric columns (J-N) as plain numbers, NOT formatted strings.
# The sheet has column formatting applied (date for I, #,##0 for J-K, $#,##0 for L-N).
# Writing "$19,350" as text breaks formatting — write 19350 as a number instead.
row_data_no_num = ["PROJECT_TITLE", "REPO_NAME",
                   "STATUS", "PHASE", "BUILDER_NAME",
                   "LAST_ACTIVITY_10_WORDS", "NEXT_UP_10_WORDS",
                   "YYYY-MM-DD", loc, hours, human_cost,
                   internal_cost, net_value]

if target_row:
    # Update columns B:N only — leave Project # (column A) untouched
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=f"Active!B{target_row}:N{target_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row_data_no_num]}
    ).execute()
else:
    # New project — find the actual max Project # across both tabs and increment
    all_nums = []
    for tab in ['Active', 'Archived']:
        try:
            tab_result = service.spreadsheets().values().get(
                spreadsheetId=SHEET_ID, range=f"{tab}!A:A"
            ).execute()
            for r in tab_result.get('values', [])[1:]:  # skip header
                if r and r[0].isdigit():
                    all_nums.append(int(r[0]))
        except Exception:
            pass
    # Prefix with ' to force Sheets to treat as text (preserves "021" formatting)
    next_num = f"'{max(all_nums, default=0) + 1:03d}"
    row_data = [next_num] + row_data_no_num
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Active!A:N",
        valueInputOption="USER_ENTERED",
        body={"values": [row_data]}
    ).execute()
```

**Rules:**
- Only update YOUR project's row. Do not modify other rows.
- Keep "Last Activity" and "Next Up" to 10 words or fewer — this is a dashboard, not a log.
- If you don't know the Project # for this repo, read the sheet first to find it.
- The ADC path varies by user — use `~/.config/gcloud/application_default_credentials.json` or the path from the repo's CLAUDE.md.
- Always recalculate LOC, hours, and $ for Human Equivalent — don't reuse stale values.
- **$ HRI Internal Hours is a running total** — read the existing value and add to it. Do not reset it.
- Set `SESSION_DAYS` to the number of build days this session (usually 1). If it was a quick fix (<1 hour), use 0.
- **Always fill in ALL columns I–N** (Last Updated through Net Value). Never leave them blank — use `0` for numeric fields on new/empty repos.

**Column formatting (already applied to the sheet — do NOT write formatted strings):**
- **Column I (Last Updated):** Date format `yyyy-MM-dd`. Write as `"YYYY-MM-DD"` string with `USER_ENTERED`.
- **Columns J–K (LOC, Hours):** Number format `#,##0`. Write as plain integers (e.g., `1288`, not `"1,288"`).
- **Columns L–N ($ for Human Equivalent, $ HRI Internal Hours, Net Value):** Currency format `$#,##0`. Write as plain integers (e.g., `19350`, not `"$19,350"`). The sheet applies the `$` and commas.
- **Column A (Project #):** Text. For new rows, prefix with `'` to force text (e.g., `"'021"`). This prevents Sheets from stripping leading zeros.
- **`valueInputOption` must be `USER_ENTERED`**, not `RAW`. RAW writes literal strings that break number formatting.

## Step 5: Verify responses

Check the responses from both the Sheets Bridge (Step 3) and the Project Status Tracker update (Step 4). If any operation returns an error, fix and retry before ending the session. Common issues:
- Column name case mismatch (e.g., `"repository"` instead of `"Repository"`)
- Missing required field
- Tab name doesn't exist
- ADC credentials expired (re-run `gcloud auth application-default login`)

## Session-End Scope

The protocol ends after Step 5. After completing all steps, confirm the protocol is complete and let the user know that changes are ready to commit and push. Then wait for the user's go-ahead.

**Do not perform any of the following as part of the session-end sequence:**

**Git commit and push.** The user decides when to commit and what commit message to use. Do not run `git add`, `git commit`, or `git push` as part of session-end. After the protocol steps are complete, prompt the user — e.g., "Session-end protocol complete. Changes are ready to commit and push when you are."

**Deployment.** Do not deploy Cloud Functions, push Apps Script via clasp, or update any live service as part of session-end. Deployment is a separate activity that happens during the working session when the user directs it, not during the closing protocol. If the session included deployment work, note what was deployed in the Project Knowledge entry.

**Testing or health checks.** Do not run test suites, curl endpoints, or verify live services as part of session-end. If testing is still needed, flag it in the Project Knowledge entry under "Watch out" or "Open."

**The principle:** The session-end protocol is about recording what happened, not continuing to do things. Every step is a write to documentation or a metadata update. Operational actions — committing, deploying, testing — require the user's direction.
