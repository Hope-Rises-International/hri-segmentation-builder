# HRI Stack Learnings

> **This is the single source of truth for stack-level gotchas across all HRI repos.**
> Every Claude Code session should read this file before infrastructure work.
> Update this file directly (via GitHub API) when you discover a stack-level learning.
> Do NOT create local copies of this file in project repos.

Shared knowledge base of stack-level gotchas and fixes discovered across all HRI projects. Every Claude Code user in the org should have access to this file.

**Scope:** Only infrastructure, tooling, and platform issues. Not project-specific logic or business rules.

---

## macOS / Terminal

### npm cache owned by root
Some directories under `~/.npm/_cacache` end up owned by `root` from a past `sudo npm install`. Symptom: `EACCES: permission denied` on `npm install`.
- **Workaround:** `npm install --cache /tmp/npm-cache`
- **Permanent fix:** `sudo chown -R $(whoami) ~/.npm`

### Python 3.9 vs 3.13 PATH priority
macOS system Python 3.9 takes priority even with 3.13 installed via Homebrew. Google API libraries throw deprecation warnings on 3.9 but still work.
- **Fix:** Add to `~/.zshrc`: `export PATH="/opt/homebrew/bin:$PATH"`

### gcloud "Python 3.9 is no longer supported"
gcloud SDK uses the system Python by default.
- **Fix:** Add to `~/.zshrc`: `export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3`

---

## Google Cloud / Cloud Scheduler + Cloud Run Auth

### Cloud Scheduler → Cloud Run (gen2 Cloud Functions) requires two explicit IAM bindings
When Cloud Scheduler calls an authenticated Cloud Run service via OIDC token, two IAM bindings must be in place. If either is missing, the scheduler fires on schedule but Cloud Run returns 401 "not authenticated" — the job looks like it's running but silently fails every time.

1. **Target service account needs `run.invoker` on the Cloud Run service:**
```bash
gcloud run services add-iam-policy-binding SERVICE_NAME \
  --member="serviceAccount:TARGET_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region=REGION --project=PROJECT
```

2. **Cloud Scheduler agent SA needs `iam.serviceAccountTokenCreator` on the target SA:**
```bash
gcloud iam service-accounts add-iam-policy-binding \
  TARGET_SA@PROJECT.iam.gserviceaccount.com \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project=PROJECT
```

- **Why both:** The scheduler agent impersonates the target SA to mint an OIDC token. Cloud Run then checks if the token's identity has `run.invoker`. Missing either = 401.
- **Gotcha:** These bindings can be wiped by IAM reconciliation when deploying other Cloud Functions in the same project. Always set them explicitly — don't rely on them persisting from a prior deployment.
- **Gotcha:** The OIDC token `audience` in the scheduler job must match the Cloud Run service URL exactly.
- **Discovered:** 2026-03-27, hri-major-gift-app — `sf-data-refresh` failed silently for 5 days after `campaign-scorecard-refresh` was deployed in the same project on 2026-03-22.

### Cloud Function gen2 default 512MB is insufficient for large pandas pipelines
The default 512Mi memory for Cloud Functions gen2 will OOM when processing large Salesforce datasets (e.g., 654K Opportunities + 358K Accounts) with pandas DataFrames. The container gets killed mid-execution with no useful error — just "Memory limit of 512 MiB exceeded."
- **Fix:** Deploy with `--memory=2Gi --cpu=1` for pandas-heavy pipelines. The extra CPU also speeds up DataFrame operations.
- **Also:** Cloud Scheduler must use `POST`, not `GET`, when triggering Cloud Functions gen2 (functions_framework). GET triggers worked for gen1 but gen2 returns 405 or auth errors with GET.
- **Discovered:** 2026-04-02, hri-donor-file-health — first deploy OOM'd at Salesforce query stage; also initial scheduler used GET which returned 401 even with correct IAM bindings.

---

## Google Cloud / Authentication

### ADC scopes must match intent
Application Default Credentials are scoped at auth time. `drive.readonly` silently blocks uploads — no error until the API call. Current working scopes: `cloud-platform` + `drive` (full).
- **Re-auth command:** `gcloud auth application-default login --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive"`
- **Credentials location:** `~/.config/gcloud/application_default_credentials.json`

### Shared drive API calls need three flags + quota project header
Without all three flags, shared drive files are completely invisible — the API returns an empty list with no error:
```python
service.files().list(
    q="'FOLDER_ID' in parents",
    supportsAllDrives=True,
    includeItemsFromAllDrives=True,
    corpora='allDrives',
    fields='files(id, name, mimeType)',
    pageSize=100
).execute()
```
This applies to every operation: list, get, create, update, delete. Missing `corpora='allDrives'` is the most common cause of silent empty results on shared drives.

For raw REST/`curl` calls, also pass the `x-goog-user-project` header alongside Authorization — without it, Drive and Sheets return 403 "quota project not set" even when ADC is correctly configured:
```bash
curl -H "Authorization: Bearer $TOKEN" \
     -H "x-goog-user-project: gmail-agent-489116" \
     "https://www.googleapis.com/drive/v3/files?..."
```
- **Discovered:** 2026-03-18 — connectivity check returned empty folder without `corpora=allDrives`; 403s without `x-goog-user-project` header.

### Use HRI custom OAuth client for ADC — not gcloud default
Google has blocked (or is blocking) the `drive` scope with gcloud's default client ID. New team members will get Drive access denied if they use the default `gcloud auth application-default login`.
- **Fix:** Use the HRI custom OAuth client file (`hri-oauth-client.json`, stored in `claude-repo` shared Drive folder):
```bash
gcloud auth application-default login \
  --client-id-file="$HOME/Downloads/hri-oauth-client.json" \
  --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive"
```
- **Note:** OAuth client creation cannot be done via API — requires GCP Console UI.
- **Client lives in:** GCP project `gmail-agent-489116`, OAuth consent screen is Internal (hoperises.org only).

### Granting SA access to Drive folders for ADC use
When ADC uses service account impersonation, the SA needs explicit Drive sharing to access folders. The SA can't share itself — you need a one-time `gcloud auth login --enable-gdrive-access --force` to get a personal token with Drive scopes, then use the Drive API to share the folder with the SA.
```python
# One-time setup: share folder with SA using personal gcloud token
import subprocess, json, urllib.request
token = subprocess.check_output(['gcloud', 'auth', 'print-access-token']).decode().strip()
url = f'https://www.googleapis.com/drive/v3/files/{FOLDER_ID}/permissions?supportsAllDrives=true&sendNotificationEmail=false'
data = json.dumps({'type': 'user', 'role': 'writer', 'emailAddress': SA_EMAIL}).encode()
req = urllib.request.Request(url, data=data, method='POST', headers={
    'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
urllib.request.urlopen(req)
```
After this, the SA can create/write files in that folder via normal ADC — no credential switching needed.
- **Note:** `gcloud auth login` without `--enable-gdrive-access` produces tokens without Drive scopes — the sharing call returns 403 "insufficient scopes."
- **Note:** Google may show "This app is blocked" on first browser prompt — retry or accept usually works.
- **Discovered:** 2026-04-09, hri-cornerstone-scoring — setting up HRI Code Outputs sheet in Drive folder.

### Domain-wide delegation requires IAM signBlob when using ADC impersonation (no SA key)
ADC with service account impersonation **cannot** set a `sub` (subject) claim for domain-wide delegation. The `google-auth` library's `ImpersonatedCredentials` doesn't support `with_subject()`. This means plain ADC impersonation will always fail with `403 insufficientPermissions` for APIs that require acting as a Workspace user (e.g., Gmail send).

**Solution:** Use the IAM `signBlob` API to manually construct and sign a JWT with a `sub` claim, then exchange it for an access token:
```python
import base64, json, time, urllib.request, urllib.parse
import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build

SA_EMAIL = "your-sa@your-project.iam.gserviceaccount.com"
SUBJECT = "user@yourdomain.com"  # Workspace user to impersonate

# 1. Get ADC creds (impersonated SA) for IAM API access
creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/iam"])
creds.refresh(Request())
iam = build("iam", "v1", credentials=creds)

# 2. Build JWT with sub claim
now = int(time.time())
payload = {"iss": SA_EMAIL, "sub": SUBJECT, "scope": "https://www.googleapis.com/auth/gmail.send",
           "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}
header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
signing_input = f"{header_b64}.{payload_b64}"

# 3. Sign via IAM signBlob (field name is 'bytesToSign', NOT 'payload')
sign_result = iam.projects().serviceAccounts().signBlob(
    name=f"projects/-/serviceAccounts/{SA_EMAIL}",
    body={"bytesToSign": base64.b64encode(signing_input.encode()).decode()}).execute()
signature = sign_result["signature"].rstrip("=").replace("+", "-").replace("/", "_")
signed_jwt = f"{signing_input}.{signature}"

# 4. Exchange JWT for access token
token_data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed_jwt}).encode()
resp = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data))
access_token = json.loads(resp.read())["access_token"]

# 5. Build API client with delegated token
gmail = build("gmail", "v1", credentials=OAuthCredentials(token=access_token))
```

**Prerequisites:**
1. Domain-wide delegation enabled on the SA (GCP Console → Service Accounts → Advanced settings)
2. SA Client ID added in Workspace Admin Console (Security → API Controls → Domain-wide Delegation) with required OAuth scopes
3. SA key creation blocked by org policy is fine — signBlob doesn't need a key file
4. ADC impersonation source creds need `iam` scope (or `cloud-platform`) for signBlob

**Common mistakes:**
- Using `payload` instead of `bytesToSign` as the signBlob request field → `400 Unknown name`
- Forgetting to url-safe the base64 signature (replace `+/-` with `-/_`)
- Trying `google.auth.default(scopes=[...gmail...])` directly → 403 because ADC can't add `sub`
- Trying `credentials.with_subject(...)` on ADC credentials → `AttributeError: 'Credentials' object has no attribute 'with_subject'`
- **Discovered:** 2026-04-10, hri-sf-uploader — Gmail send for Aegis pipeline notification emails.

**Simpler alternative on Cloud Run (no signBlob needed):**
On Cloud Run, the SA identity is available via the metadata server. You can create `impersonated_credentials.Credentials` directly with the `subject=` constructor parameter — no key file, no signBlob:
```python
import google.auth
from google.auth import impersonated_credentials
source_credentials, _ = google.auth.default()
delegated = impersonated_credentials.Credentials(
    source_credentials=source_credentials,
    target_principal="your-sa@your-project.iam.gserviceaccount.com",
    target_scopes=["https://www.googleapis.com/auth/gmail.send"],
    subject="user@yourdomain.com",
)
gmail = build("gmail", "v1", credentials=delegated)
```
**Key distinction:** The constructor `Credentials(..., subject=...)` works; the method `.with_subject()` does NOT exist on this class. The signBlob approach is still needed for local ADC impersonation (where source creds are already impersonated and don't support nested impersonation with subject).
- **Discovered:** 2026-04-13, hri-sf-backup — Gmail failure notification on Cloud Run.

### Apps Script → Cloud Run auth requires OIDC token minting via IAM Credentials API
Apps Script's `ScriptApp.getOAuthToken()` returns an OAuth access token and `ScriptApp.getIdentityToken()` returns an OIDC token whose `aud` claim is the Apps Script project's OAuth client ID — neither is accepted by Cloud Run, which requires an OIDC identity token with the Cloud Run service URL as `aud`. The HRI org policy also blocks `allUsers` and `allAuthenticatedUsers` IAM bindings on Cloud Run, so you cannot use `--allow-unauthenticated` as a workaround.

**Solution:** Use the IAM Credentials API to mint a properly-scoped OIDC token from Apps Script:
```javascript
function getCloudRunIdToken_(serviceAccount, cloudRunUrl) {
  var accessToken = ScriptApp.getOAuthToken();
  var url = 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'
    + serviceAccount + ':generateIdToken';
  var response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + accessToken },
    payload: JSON.stringify({ audience: cloudRunUrl, includeEmail: true }),
    muteHttpExceptions: true
  });
  if (response.getResponseCode() !== 200)
    throw new Error('Failed to mint ID token: ' + response.getContentText());
  return JSON.parse(response.getContentText()).token;
}
```

**Required setup (all four or it won't work):**
1. **IAM Credentials API enabled** on the Apps Script project's GCP project (the project number shown in the error, NOT the Cloud Run project)
2. **`roles/iam.serviceAccountTokenCreator`** granted to the deployer (e.g. `BSimmons@hoperises.org`) on the target service account
3. **`roles/run.invoker`** granted to the target service account on the Cloud Run service
4. **`https://www.googleapis.com/auth/cloud-platform`** in `appsscript.json` oauthScopes — deployer must revoke app access at https://myaccount.google.com/permissions and re-open the web app to trigger re-authorization

**Common failures and fixes:**
- `ACCESS_TOKEN_SCOPE_INSUFFICIENT` → The `cloud-platform` scope is missing from `appsscript.json` or the deployer hasn't re-authorized after adding it.
- `SERVICE_DISABLED` → IAM Credentials API not enabled. Enable via: `gcloud services enable iamcredentials.googleapis.com --project=<APPS_SCRIPT_GCP_PROJECT_NUMBER>`
- `clasp push` silently ignored → File extension mismatch. If the project has `Code.js`, pushing `Code.gs` creates a second file instead of replacing. Always `clasp pull` first to check the extension, then match it.
- **Discovered:** 2026-03-31, hri-donor-brief-builder — four deploy cycles before identifying the full chain of requirements.

---

## Google Apps Script

### All .gs files share one global namespace
Every `.gs` file in a GAS project compiles into the same global scope. Top-level `const`, `let`, and `function` declarations must be unique across ALL files — a duplicate name causes `SyntaxError: Identifier already declared` at page load, which silently breaks the entire web app.
- **Fix:** Keep shared constants in one file (e.g., `Config.gs`) and reference them everywhere else. Never redeclare them in engine files.
- **Symptom:** Web app loads but a tab or render function silently fails with `renderXxxView is not defined` in the console — caused by a SyntaxError earlier in the same script block preventing parsing.

### /dev vs /exec URLs — testing after clasp push
`clasp push` updates the project source immediately, but only the `/dev` URL serves the latest pushed code. The `/exec` URL serves the last *deployed* version and requires `clasp deploy` to update.
- **Get the /dev URL from:** Apps Script editor → Deploy → Test deployments. Do not manually swap `/exec` → `/dev` in an old URL — the subdomain can differ.
- **Browser caching:** The GAS iframe aggressively caches. Use DevTools with "Disable cache" checked, or open in Incognito, when testing after a push.

### Session.getActiveUser() fails in time-based triggers
Time-based triggers have no "active user." The call throws: `Exception: You do not have permission to call Session.getActiveUser`.
- **Fix:** Use `Session.getEffectiveUser()` instead (returns the trigger owner's email).
- **Also required:** Add `https://www.googleapis.com/auth/userinfo.email` to the `oauthScopes` array in `appsscript.json`.

### clasp deploy version flag is `-V`, not `--version`
When updating an existing deployment to a specific version, use the short flag `-V`:
```bash
clasp deploy -i DEPLOYMENT_ID -V 6
```
The long form `--version 6` silently fails (deployment doesn't update). Always use `-V`.

### clasp push does NOT update the live web app
`clasp push` updates the project source code but the deployed web app URL still serves the old version. You must also create or update a deployment.
- **Fix:** Run `clasp push && clasp deploy` (or update an existing deployment ID).

### clasp push overwrites remote changes if local is stale
`clasp push` replaces ALL project files with local versions. If another session pushed changes (e.g., added a tool card to the portal), pushing stale local code will silently erase those changes.
- **Fix:** Always run `clasp pull` before `clasp push` to merge remote changes into local first. If there are conflicts, resolve them before pushing.
- **Also:** Some Apps Script projects have multiple versioned deployments. After push + version, update ALL versioned deployments — not just the first one found.
- **Discovered:** 2026-04-02, hri-internal-portal — Timesheet Portal card (added by another session) was erased when stale local code was pushed.

### `clasp deploy` silently overrides web app access level from manifest
When you run `clasp deploy -i DEPLOY_ID -V N`, it reads the `access` field from `appsscript.json` and applies it to the deployment. If the manifest says `"access": "ANYONE"` but the live deployment was configured as `ANYONE_ANONYMOUS` (via the web UI or API), the deploy silently downgrades access — causing 401 for all unauthenticated callers.
- **Symptom:** Endpoint that was working returns 401 after redeployment. No error from clasp.
- **Fix:** Ensure `appsscript.json` matches the intended access level. For endpoints that need anonymous access (e.g., Sheets Bridge called from browser JS), use `"access": "ANYONE_ANONYMOUS"`.
- **Safer deploy path:** Use the Apps Script REST API (`PUT /deployments/{id}`) which shows the resolved access level in the response, so you can verify immediately.

### Refreshing the clasp OAuth token programmatically
The clasp token in `~/.clasprc.json` stores a refresh_token. When the access_token expires, clasp commands still work (they auto-refresh), but if you need a fresh token for the Apps Script REST API:
```python
# Read refresh_token from ~/.clasprc.json tokens.default
# POST to https://oauth2.googleapis.com/token with grant_type=refresh_token
# The resulting token has script.deployments, script.projects, script.webapp.deploy scopes
```

### POST responses redirect (302)
Apps Script web apps return a 302 redirect on POST. A simple `curl -X POST` gets an empty HTML page, not your JSON response.
- **Fix:** Two-step curl pattern:
```bash
REDIRECT_URL=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST "$URL" \
  -H "Content-Type: application/json" -d "$PAYLOAD") && curl -s -L "$REDIRECT_URL"
```

### Clasp's default OAuth client is blocked by hoperises.org Workspace admin
The default clasp OAuth client ID (`1072944905499-...`) is blocked by the Workspace admin ("This app is blocked" screen). This affects `clasp login`, `clasp login --use-project-scopes`, and any auth flow using the default client.
- **Fix:** Use HRI's own GCP OAuth client from gmail-agent-489116:
```bash
# Generate creds file from ADC
python3 -c "
import json
d = json.load(open('$HOME/.config/gcloud/application_default_credentials.json'))
json.dump({'installed': {'client_id': d['client_id'], 'client_secret': d['client_secret'], 'redirect_uris': ['http://localhost']}}, open('/tmp/clasp_creds.json','w'), indent=2)
"
# Login with HRI client
clasp login --creds /tmp/clasp_creds.json
# For Execution API (project scopes):
clasp login --creds /tmp/clasp_creds.json --use-project-scopes --user run
```
- **Discovered:** 2026-03-19, hri-claude-training — clasp login with `--use-project-scopes` hit "This app is blocked."

### Sidebar/in-page links don't reliably pass URL params in sandboxed iframe
Apps Script web apps are served inside a sandboxed iframe on `*.googleusercontent.com`. When HTML inside the iframe uses `<a href="?page=summary">`, the navigation happens within the iframe but `doGet(e)` may not receive the `page` parameter reliably — the user sees a blank page or gets stale content.
- **Fix:** Use client-side JavaScript routing instead of URL-based navigation. Load all data once via `google.script.run`, then switch pages by re-rendering the DOM without a page reload. Sidebar links should use `onclick="navigateTo('summary'); return false;"` instead of `href="?page=summary"`.
- **Benefit:** Instant page switching (no server round-trip), avoids iframe URL parameter issues, and avoids data re-fetching.
- **Discovered:** 2026-03-19, hri-gl-data-repo — Financial Summary page was blank when navigating via sidebar link.

### DriveApp OAuth consent cannot be triggered via CLI deployment
When a web app is deployed via `clasp push` + REST API (not the Apps Script editor UI), the deployer never sees the OAuth consent prompt. Any code calling DriveApp, UrlFetchApp, or other scope-requiring services will fail with "You do not have permission to call X. Required permissions: Y" — even though the scope is declared in `appsscript.json`.
- **Root cause:** OAuth consent for Apps Script is browser-only. CLI deployment bypasses it.
- **Workaround for USER_ACCESSING:** Add `DriveApp.getRootFolder();` as the first line of `doGet()` and include `https://www.googleapis.com/auth/userinfo.email` in oauthScopes. The email scope forces Google to re-prompt consent on next visit, and the DriveApp call in doGet ensures scope check happens at page load (not on first google.script.run call, which fails silently).
- **Workaround for zero-auth architecture:** Host files on GCS instead of Drive. Use the web app as a static landing page with no server-side logic requiring scopes.
- **Discovered:** 2026-03-19, hri-claude-training — spent significant time before identifying this as the root cause.

### Google Sheets auto-converts YYYY-MM strings to Date objects
When `YYYY-MM` (e.g. `"2026-03"`) is written to a Google Sheets cell via `appendRow` or `setValues`, Sheets interprets it as a date and stores it as a Date object. `getValues()` returns a JS `Date`, not a string. Strict string comparisons against `"2026-03"` silently fail — this was the root cause of a "no draft on file" bug in db_Drafts where every lookup missed.
- **Prevention:** Force the target column to text format BEFORE writing: `sheet.getRange('B:B').setNumberFormat('@');`
- **Read-time normalization:** Always normalize when reading potentially-date columns:
```javascript
function _normalizePeriodKeyFromCell(val) {
  if (!val && val !== 0) return '';
  if (val instanceof Date) {
    const y = val.getFullYear();
    const m = String(val.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}`;
  }
  return val.toString().trim();
}
```
- **Affected patterns:** Any string that looks like a partial date: `YYYY-MM`, `MM/DD`, bare month names. Full `YYYY-MM-DD` strings are generally safe.
- **Discovered:** 2026-03-17, timesheet_automation — db_Drafts period-key column.

### API key must go in JSON body, not HTTP headers
Apps Script web apps don't reliably receive custom HTTP headers from all callers. The Sheets Bridge expects `apiKey` as a field in the JSON body.

### Sheets Bridge column names are case-sensitive
`"repository"` ≠ `"Repository"`. The API returns `"action": "error"` with a message, but it's easy to miss. Always copy column names exactly from the CLAUDE.md reference.

---

## Salesforce API

### Hard-coded FY fields deleted — use relative fields
As of March 2026, SF admin deleted all hard-coded fiscal year fields (`Acct_Sum_of_FY_2019__c` through `Acct_Sum_of_FY_2024__c` and their count counterparts `Acct_Number_of_FY2019_Gifts__c` etc.). Replaced with relative fields that roll automatically each fiscal year (July 1):
- **Totals:** `Total_Gifts_5_Fiscal_Years_Ago__c` through `Total_Gifts_This_Fiscal_Year__c`
- **Counts:** `Number_of_Gifts_5_Fiscal_Years_Ago__c` through `Number_of_Gifts_This_Fiscal_Year__c`
- Also: `Total_Gifts_Last_365_Days__c`, `Total_Gifts_730_365_Days_Ago__c`, `Total_Gifts_1095_730_Days_Ago__c` (rolling monthly windows, not FY-aligned)
- `Donor_Lifecycle_Stage__c` was marked DEPRECATED (label changed) but NOT deleted — still queryable.
- **Impact:** Any SOQL query referencing the old `Acct_Sum_of_FY_*` fields will fail with `INVALID_FIELD`. No warning — the fields were deleted and their API names now end in `_del__c`.
- **Discovered:** 2026-03-19, hri-major-gift-app — Cloud Function failed on scheduled run after SF admin completed field migration.

### TaskSubtype is the primary activity classifier, not Type
The Task object's `Type` field is empty on ~87% of records. `TaskSubtype` is the reliable signal: values include `Call`, `Email`, `Task`. Subject-based fallback is needed for records where TaskSubtype = "Task" (no subtype). HRI has zero Event records — all activities are Tasks.
- **Discovered:** 2026-03-19, hri-major-gift-app — Activity Scorecard build.

### Metadata API deploys don't grant Field-Level Security
Fields created via Metadata API deploy (zip package) are invisible to `describe()`, SOQL, and the REST API until FLS is explicitly granted. This affects ALL profiles, including System Administrator. The deploy reports `created=True` and `status=Succeeded`, but the fields silently don't appear in any API response.
- **Fix:** Deploy a Profile metadata package granting `fieldPermissions` for each new field:
```xml
<Profile xmlns="http://soap.sforce.com/2006/04/metadata">
    <fieldPermissions>
        <field>MyObject__c.MyField__c</field>
        <readable>true</readable>
        <editable>true</editable>
    </fieldPermissions>
</Profile>
```
- **Gotcha:** Required fields (`required=true`) cannot be included in the Profile deploy — they're automatically visible. Including them causes: `You cannot deploy to a required field`.
- **Gotcha:** Formula fields must use `<editable>false</editable>` — they're read-only by definition.
- **Discovered:** 2026-03-22, hri-campaign-scorecard — Campaign schema creation.

### simple_salesforce checkDeployStatus returns done=None
The `sf.checkDeployStatus(deploy_id)` method always returns `done=None` due to a SOAP response parsing issue in the simple_salesforce library. The `status` field works correctly (returns `InProgress`, `Succeeded`, `Failed`).
- **Workaround:** Use the REST endpoint instead:
```python
url = f'https://{sf.sf_instance}/services/data/v{sf.sf_version}/metadata/deployRequest/{deploy_id}?includeDetails=true'
resp = requests.get(url, headers={'Authorization': f'Bearer {sf.session_id}'})
result = resp.json()['deployResult']
# result['status'], result['success'], result['numberComponentsDeployed'] all work
```
- **Discovered:** 2026-03-22, hri-campaign-scorecard — Campaign schema creation.

### High Assurance MFA on System Administrator profile breaks API access
If the System Administrator profile's "Session Security Level Required at Login" is set to **High Assurance**, all API connections using OAuth username-password flow (simple_salesforce) will fail with `INVALID_SESSION_ID` — "This session is not valid for use with the REST API." The OAuth flow creates a Standard-level session, which is rejected when the profile demands High Assurance.
- **Symptom:** `sf.query()` fails immediately after successful login — the session is created but invalid for API use.
- **Quick fix:** Set "Session Security Level Required at Login" back to **None** on the System Administrator profile (Setup → Profiles → System Administrator → Session Settings). Human admins still get MFA prompts from the org-wide MFA requirement.
- **Proper fix:** Create a dedicated "API Integration User" profile (clone of System Admin) without the High Assurance requirement. Assign the API service account to it. This keeps human admins on High Assurance while API users bypass it.
- **Context:** Salesforce mandates MFA for interactive UI logins but NOT for API-only connections. Enforcing High Assurance at the profile level is optional and catches API users in the crossfire.
- **Discovered:** 2026-04-07, hri-cornerstone-scoring — SF admin enabled phishing-resistant MFA for System Administrators, breaking all API integrations.

### Cornerstone Partner flag — Account is the source of truth
The Cornerstone flag has two fields:
- **Account:** `Cornerstone_Partner__c` (Checkbox) — this is the writable source of truth. Update this one.
- **Contact:** `Cornerstone_Partner_Account__c` (label: "Cornerstone Partner (Account)") — read-only mirror from Account. Do NOT update directly.
- NPSP giving rollups (`npo02__TotalOppAmount__c`, `npo02__LastCloseDate__c`, `npo02__NumberOfClosedOpps__c`, `npo02__LargestAmount__c`, etc.) live on **Account**, not Contact. The `npe01__` namespace on Contact contains address/phone/email fields only.
- **Discovered:** 2026-04-07 (field exists), corrected 2026-04-13 (Account vs Contact clarified), hri-cornerstone-scoring.

### NPSP rollup field namespaces: npo02 (Account) vs npe01 (Contact)
- `npo02__*` = giving rollups on **Account** (TotalOppAmount, LastCloseDate, NumberOfClosedOpps, LargestAmount, FirstCloseDate, LastOppAmount, AverageAmount, SmallestAmount, Best_Gift_Year_Total, etc.)
- `npe01__*` = address/phone/email/preference fields on **Contact** (HomeEmail, WorkEmail, PreferredPhone, Primary_Address_Type, etc.) — NO giving data.
- Do Not Contact on Account: `Do_Not_Contact__c`. On Contact: `Do_NOt_Contact_At_All__c` (note capitalization) and `npsp__Do_Not_Contact__c`.
- Deceased on Account: `Primary_Contact_is_Deceased__c`. On Contact: `npsp__Deceased__c`.
- **Discovered:** 2026-04-13, hri-cornerstone-scoring — active responder query failed with `INVALID_FIELD` when using `npe01__` fields on Contact for giving data.

---

## GitHub CLI

### gh repo list: use "visibility" not "private"
`gh repo list --json name,private` crashes — the field doesn't exist. Use `--json name,visibility` instead.

### Default branch isn't always main
Some repos have feature branches as their default (e.g., `hri-receipt-generator` defaults to `claude/add-receipt-generator-KPX7y`). Never assume `main`.
- **Check:** `git remote show origin | grep "HEAD branch"`

### GitHub org creation cannot be done via API
Must use the web UI at https://github.com/settings/organizations. Repo transfers CAN be done via API: `gh api /repos/{owner}/{repo}/transfer --method POST --field new_owner="Hope-Rises-International"`.

### GitHub nonprofit Teams plan is free
Apply at https://github.com/solutions/industry/nonprofits. Takes up to a week to process. Start on free plan, apply for upgrade.

---

## Frontend / Portable Builds

### file:// protocol blocks ES module loading
Browsers refuse to load `<script type="module">` from `file://` URLs due to CORS policy. The page renders blank with no visible error (check browser console).
- **Fix option 1:** Serve locally: `python3 -m http.server 8080`
- **Fix option 2:** Build as single file using `vite-plugin-singlefile` — inlines all JS and CSS into one HTML file.

### Use HashRouter for portable React apps
`BrowserRouter` requires a server to handle routes. `HashRouter` works everywhere — local files, GitHub Pages, Google Drive.
```tsx
import { HashRouter } from 'react-router-dom';
```

### Single-file HTML for Drive and non-technical users
When building artifacts that need to live in Google Drive or be shared outside the dev team, bundle everything into one HTML file. Install `vite-plugin-singlefile`, set `base: './'` in vite config, build. The output is a single `index.html` with all assets inlined.

### HRI logo — use local asset, not CDN URL
The old CDN logo URL (`https://www.leprosy.org/wp-content/uploads/2017/02/alm-hri-horizontal.png`) is broken — the leprosy.org domain was rebranded to hoperises.org. In `hri-claude-training`, use the local copy at `sessions/assets/hri_logo.png` (relative path `../assets/hri_logo.png` from session subfolders). For self-contained HTML files hosted on GCS, base64-encode the logo inline (Session 4 recap does this). Never reference the old CDN URL.

---

## Nimbalyst / MCP Tools

### `developer_git_commit_proposal` MCP tool drops connection on large repos
The Nimbalyst MCP commit widget (`mcp__nimbalyst-mcp__developer_git_commit_proposal`) intermittently returns `MCP error -32000: Connection closed` instead of presenting the commit UI. Retrying does not help — the connection drops repeatedly.
- **Workaround:** Fall back to standard `git add` + `git commit` from the CLI. The commit still works; you just lose the interactive review widget.
- **When to try the MCP tool first:** Small, single-file commits in repos with short git history. If it fails once, switch to CLI immediately — don't retry in a loop.
- **Discovered:** 2026-03-27, hri-campaign-scorecard — two consecutive failures before falling back to CLI.

---

## Google Sheets API

### New tabs have a 1000-row grid limit — expand before writing large datasets

When you create a new spreadsheet tab (via `addSheet`), Sheets provisions it with a default grid of 1000 rows × 26 columns. Writing data beyond row 1000 returns HTTP 400: `Range exceeds grid limits`.

**Fix:** Before batch-writing rows, call `appendDimension` to expand the grid:

```python
sheets.spreadsheets().batchUpdate(
    spreadsheetId=sheet_id,
    body={"requests": [{
        "appendDimension": {
            "sheetId": tab_sheet_id,
            "dimension": "ROWS",
            "length": needed_rows - current_rows + 100,
        }
    }]},
).execute()
```

Or set `rowCount` when creating the tab:
```python
{"addSheet": {"properties": {"title": "my_tab", "gridProperties": {"rowCount": 50000}}}}
```

**Discovered:** 2026-03-13, hri-gl-data-repo — trial_balance tab needed ~7,000 rows.

---

## Google Drive API

### Shared drive roles affect API operations
Contributor role can create files but may not delete them. Content Manager can create, delete, and organize. If a Drive API `files().delete()` returns 404 on a shared drive, check the user's role — it's likely Contributor, not Content Manager.

### Upload to shared Drive folder
```python
service.files().create(
    body={'name': 'filename', 'parents': ['FOLDER_ID']},
    media_body=MediaFileUpload(path, mimetype=mime),
    supportsAllDrives=True,
    fields='id, name'
).execute()
```
The `supportsAllDrives=True` flag is required even on create, not just list/get.

---

## Computer Use (MCP Server)

### macOS permissions require process restart
Granting Accessibility and Screen Recording in System Settings does NOT take effect until the terminal process (Terminal.app, iTerm, etc.) is fully restarted. The computer-use MCP server will report `✘ not granted` even after toggling the permission on.
- **Fix:** Quit Claude Code completely, then relaunch. Do not just open a new tab — the process must restart.
- **Screen Recording** is the stubborn one — Accessibility sometimes picks up without restart, but Screen Recording almost never does.

### Permission tiers control what actions are allowed per app
Each app is granted at a specific tier when `request_access` is called:
- **click** — Visible left-clicks and scrolling only. No typing, key presses, right-click, modifier-clicks, or drag. Terminal and IDE apps get this tier by default.
- **full** — All interaction including typing, right-click, drag-drop. Needed for apps where you're filling forms or doing real UI work.
- For Terminal/IDE apps, use the **Bash tool** for shell commands — don't try to type into the terminal via computer use.

### request_access must be called before any other computer-use tool
The first call in any session must be `request_access` listing the apps you need. You can call it again mid-session to add more apps. Previously granted apps remain granted.

### Screenshot filtering
macOS native screenshot filtering means only granted app windows are visible in screenshots. If you need to see a specific app, make sure it was included in the `request_access` call.

### claude-repo shared Drive folder
- **Folder ID:** `1iq-yqA0us2y3epYvbMstF_apdVV-O57e`
- **Location:** Shared drives → Claude → claude-repo
- **Purpose:** Sharing artifacts between Claude Code users and non-technical staff. Upload here when user says "put that in the shared drive."
- **Canonical files:** `hri-architecture-summary.md`, `settings.json`, `HRI GL Data Repository` (sheet), `Major-Gifts-Unicorn-System.html`
- **Do not leave** test/scratch files (`_connection_test.txt`, etc.) in this folder — they pollute Drive searches and confuse future sessions.
- **Always query with all three flags** (see "Shared drive API calls" entry above) or results will be empty.

## Anthropic API

### Advisor tool (beta) requires `client.beta.messages.create`
- The advisor tool (`advisor_20260301`) is a beta feature. Use `client.beta.messages.create()` (not `client.messages.create()`) and pass `betas=["advisor-tool-2026-03-01"]`.
- Tool declaration format: `{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}`
- Advisor token usage appears in the response `usage` object as `advisor_input_tokens` and `advisor_output_tokens`. As of SDK v0.84.0, these aren't typed fields — access via `getattr` or `__dict__` fallback.
- The response `content` array may include advisor-related blocks alongside the text block. Iterate `response.content` and look for blocks with a `.text` attribute to extract the final answer.
