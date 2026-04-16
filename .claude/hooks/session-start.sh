#!/bin/bash
set -euo pipefail

# Only run in remote (web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install clasp for Apps Script deployment
if ! command -v clasp &> /dev/null; then
  npm install -g @google/clasp
fi

# Set up global CLAUDE.md with Registry update instructions
# This ensures every repo session has access to the Sheets Bridge config
mkdir -p ~/.claude
cat > ~/.claude/CLAUDE.md << 'GLOBAL_EOF'
# Claude Code — Global Instructions

## HRI Systems Registry Auto-Update (REQUIRED)

At the end of every session where code changes are made, you MUST update the HRI Systems Registry. Do this automatically — do not ask or wait to be reminded.

### Endpoint

```
https://script.google.com/macros/s/AKfycbzen_jFFDULJQhj7rPK8khEopSRR1YIusTDqI7dpQPrefdi7Lxcv_J5UimlolVg49i-vQ/exec
```

### Authentication

- API Key: `b3317d76-a5f3-4657-9881-6f087455173d`
- Sheet ID: `115R3nf55qL23dftoZQg3jOQh5Drqvj-Cd08lsyR_Z2g`

### How to call the endpoint

Apps Script redirects POST responses. Use a two-step curl pattern:

```bash
REDIRECT_URL=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST \
  "https://script.google.com/macros/s/AKfycbzen_jFFDULJQhj7rPK8khEopSRR1YIusTDqI7dpQPrefdi7Lxcv_J5UimlolVg49i-vQ/exec" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") && curl -s -L "$REDIRECT_URL"
```

### What to update

After committing and pushing code, send a single payload with applicable operations:

1. **Repositories tab** (upsert, matchColumns: `["Repository"]`): Update the repo entry with current status, last updated date, and a note about what changed.
2. **Build Log tab** (append): Add a row summarizing the session — date, builder (Claude Code), system name, action taken, issues encountered, resolution, and notes.
3. **Deployed Services tab** (upsert, matchColumns: `["Service Name"]`): If a service was deployed or updated.
4. **Credentials & Secrets tab** (upsert, matchColumns: `["Secret Name"]`): If any new secrets were created or rotated.
5. **Tool Inventory tab** (upsert, matchColumns: `["Tool Name"]`): If a new tool was added or changed.
6. **Architecture Decisions tab** (append): If a significant architectural decision was made.
7. **Connected Sheets tab** (upsert, matchColumns: `["Sheet Name"]`): If a new Google Sheet was connected.

### Tab matching reference

| Tab | matchColumns | Mode |
|-----|-------------|------|
| Deployed Services | `["Service Name"]` | upsert |
| Credentials & Secrets | `["Secret Name"]` | upsert |
| Connected Sheets | `["Sheet Name"]` | upsert |
| Repositories | `["Repository"]` | upsert |
| Architecture Decisions | N/A | append |
| Build Log | N/A | append |
| SF Field Reference | `["Object", "API Name"]` | upsert |
| GL Code Exclusions | `["GL Code"]` | upsert |
| Tool Inventory | `["Tool Name"]` | upsert |

### Column headers by tab (exact names — use these as keys in the `data` object)

**Repositories**
`Repository` | `Platform` | `URL` | `Status` | `Last Updated` | `Notes`

**Build Log**
`Date` | `Builder` | `System` | `Action` | `Issues Encountered` | `Resolution` | `Time Spent` | `Notes`

**Deployed Services**
`Service Name` | `Type` | `Platform` | `Purpose` | `Status` | `Date Deployed` | `Region` | `Notes`

**Tool Inventory**
`Tool Name` | `Category` | `Status` | `Platform` | `Data Source` | `Notes`

**Credentials & Secrets**
`Secret Name` | `Location` | `Purpose` | `Authenticates To` | `Type` | `Created` | `Last Rotated`

**Connected Sheets**
`Sheet Name` | `Sheet ID` | `URL` | `Purpose` | `Updated By` | `Update Frequency` | `Shared With` | `Notes`

**Architecture Decisions**
`Date` | `Decision` | `Context` | `Rationale` | `Alternatives Considered` | `Consequences`

**SF Field Reference**
`Object` | `Field Label` | `API Name` | `Data Type` | `Package/Namespace` | `Verified Date` | `Used In` | `Notes`

**GL Code Exclusions**
`GL Code` (additional columns TBD)

### Example: standard end-of-session payload

Every session that makes code changes should send at minimum a Repositories upsert and a Build Log append. Replace the values below with actuals:

```bash
PAYLOAD=$(cat <<'PAYLOAD_EOF'
{
  "apiKey": "b3317d76-a5f3-4657-9881-6f087455173d",
  "sheetId": "115R3nf55qL23dftoZQg3jOQh5Drqvj-Cd08lsyR_Z2g",
  "operations": [
    {
      "tab": "Repositories",
      "mode": "upsert",
      "matchColumns": ["Repository"],
      "data": {
        "Repository": "REPO_NAME_HERE",
        "Platform": "GitHub",
        "URL": "https://github.com/bsimmons3rd/REPO_NAME_HERE",
        "Status": "Active",
        "Last Updated": "YYYY-MM-DD",
        "Notes": "BRIEF_DESCRIPTION_OF_CHANGES"
      }
    },
    {
      "tab": "Build Log",
      "mode": "append",
      "data": {
        "Date": "YYYY-MM-DD",
        "Builder": "Claude Code",
        "System": "SYSTEM_NAME_HERE",
        "Action": "WHAT_WAS_DONE",
        "Issues Encountered": "None or describe",
        "Resolution": "N/A or describe",
        "Time Spent": "ESTIMATE",
        "Notes": "ADDITIONAL_CONTEXT"
      }
    }
  ]
}
PAYLOAD_EOF
)

REDIRECT_URL=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST \
  "https://script.google.com/macros/s/AKfycbzen_jFFDULJQhj7rPK8khEopSRR1YIusTDqI7dpQPrefdi7Lxcv_J5UimlolVg49i-vQ/exec" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") && curl -s -L "$REDIRECT_URL"
```

### Important notes

- Do NOT try to read the Google Sheet directly via URL — it requires authentication this environment does not have. Use the bridge API endpoint above for all reads and writes.
- Column names are case-sensitive and must match exactly.
- Dates should be `YYYY-MM-DD` strings, not Date objects.
- The API returns `"action": "error"` with a message if a column name doesn't exist in the sheet headers — check the response.
GLOBAL_EOF
