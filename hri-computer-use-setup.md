# Computer Use Setup — Claude Code

> **Audience:** HRI team members using Claude Code
> **Purpose:** Enable the computer-use MCP server so Claude Code can interact with macOS applications visually

---

## Prerequisites

- Claude Code installed and working
- macOS (computer use is macOS-only)
- Admin access to System Settings on your Mac

---

## Step 1: Add the Computer Use MCP Server

Run this command in your terminal:

```bash
claude mcp add computer-use -- npx @anthropic-ai/computer-use@latest
```

This registers the computer-use server in your Claude Code config. You only need to do this once.

### Verify it was added

```bash
claude mcp list
```

You should see `computer-use` in the output.

---

## Step 2: Grant macOS Permissions

Claude Code needs two macOS permissions to use computer use:

1. **Accessibility** — allows clicking and interacting with UI elements
2. **Screen Recording** — allows taking screenshots of app windows

### Grant them:

1. Open **System Settings > Privacy & Security > Accessibility**
   - Toggle ON your terminal app (Terminal.app, iTerm2, Warp, etc.)
2. Open **System Settings > Privacy & Security > Screen Recording**
   - Toggle ON your terminal app

### Critical: Restart after granting

macOS does not pick up these permissions until the terminal process fully restarts. **Quit your terminal app completely and relaunch it.** Opening a new tab is not enough — the process must restart.

Screen Recording is especially stubborn about this. If permissions still show as not granted after restart, try logging out and back in.

---

## Step 3: Verify in Claude Code

Start Claude Code and ask it to check computer use permissions. It will call `request_access` and you should see:

```
Accessibility: granted
Screen Recording: granted
```

If either shows `not granted`, revisit Step 2 and make sure you restarted.

---

## How It Works

### Requesting app access

Before Claude Code can interact with any app, it must call `request_access` listing the apps it needs. You'll see a dialog asking you to approve. This happens per session — previously granted apps don't carry over.

### Permission tiers

Each app gets a permission tier:

| Tier | What's allowed | Typical apps |
|------|---------------|--------------|
| **click** | Left-clicks, scrolling. No typing or keyboard input. | Terminal, IDEs |
| **full** | All interaction — typing, right-click, drag-drop | Chrome, Safari, Finder, forms |

Terminal and IDE apps are restricted to click-only by design. Claude Code uses the Bash tool for shell commands — it doesn't type into the terminal.

### Screenshot filtering

Only windows from granted apps are visible in screenshots. If Claude Code needs to see a specific app, it must be included in the `request_access` call.

---

## What Computer Use Enables

- **GUI testing** — Click through a web app in Chrome/Safari, verify UI behavior, fill forms
- **Screenshot verification** — Capture what a running app looks like to confirm it matches the spec
- **Cross-app workflows** — Interact with System Settings, Finder, Preview, or any macOS app
- **Visual QA** — Open exported files (PDFs, spreadsheets) and verify formatting

## When NOT to Use Computer Use

- **Shell commands** — Use the Bash tool, not computer use typing into Terminal
- **API-available tasks** — If something can be done via CLI or API, that's faster and more reliable
- **Bulk data entry** — Computer use is slower than programmatic approaches

Computer use is best for **visual verification** and **tasks that genuinely require a GUI**.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Permissions show `not granted` after toggling on | Fully quit and restart your terminal app. Log out/in if needed. |
| `request_access` fails | Check that the MCP server is registered: `claude mcp list` |
| Can't see an app in screenshots | Add the app to the `request_access` call |
| Computer use is slow/unreliable | Prefer CLI/API when possible. Computer use is for GUI-only tasks. |
| MCP server not found on startup | Re-run: `claude mcp add computer-use -- npx @anthropic-ai/computer-use@latest` |

---

## Reference

- **Stack learnings:** `hri-template-repository/hri-stack-learnings.md` (Computer Use section)
- **MCP server package:** `@anthropic-ai/computer-use`
