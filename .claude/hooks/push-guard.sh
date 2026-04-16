#!/bin/bash
# Push guard: warns if the person pushing is not the repo owner.
# Reads REPO_OWNER from .claude/settings.json env and GH_USER from
# the user's ~/.claude/settings.json env.
#
# This hook receives JSON on stdin from Claude Code's PreToolUse event.
# It only fires on Bash calls matching "git push".

set -euo pipefail

# Read the command being executed
COMMAND=$(jq -r '.tool_input.command // ""')

# Only check git push commands
if ! echo "$COMMAND" | grep -qE '^\s*git\s+push'; then
    exit 0
fi

# Get repo owner from project env (set in .claude/settings.json)
REPO_OWNER="${REPO_OWNER:-}"

# Get current user from global env (set in ~/.claude/settings.json)
GH_USER="${GH_USER:-}"

# If either is unset, skip the check (don't block)
if [ -z "$REPO_OWNER" ] || [ -z "$GH_USER" ]; then
    exit 0
fi

# If they match, allow the push
if [ "$REPO_OWNER" = "$GH_USER" ]; then
    exit 0
fi

# Mismatch — block with a warning
cat <<WARN
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "You ($GH_USER) are pushing to a repo owned by $REPO_OWNER. Please confirm this is intentional."
  }
}
WARN
