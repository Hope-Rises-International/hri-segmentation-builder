# Session-Start Protocol

> **Canonical source:** `Hope-Rises-International/hri-template-repository/session-start-protocol.md`
> All repos fetch this file at session start. To change the protocol, edit THIS file — not individual repos.

Complete these steps at the beginning of every session.

## Step 1: Context window management

- Monitor context usage throughout the session. When it reaches 20–25%, evaluate whether to `/clear`. Context retrieval degrades significantly beyond this threshold — don't stay in a bloated session without a reason.
- Before clearing: confirm all working state is committed and `CLAUDE.md` is updated with current progress. `/clear` wipes conversation history but preserves files.
- If the session involves deep exploration (large codebases, long API responses, many file reads), be proactive about clearing earlier. A fresh context with good CLAUDE.md state is better than a degraded context with full history.

## Step 2: Read project state

- Read this repo's `CLAUDE.md` — specifically the most recent **Project Knowledge** entry.
- If it contains an **Open** field, surface the open items to the user and confirm whether to continue that work or start something new.
- Do NOT start coding before aligning with the user on what this session's goal is.

## Step 3: Read stack learnings (conditional)

If the session will involve infrastructure, auth, deployment, or tooling work:

```bash
gh api /repos/Hope-Rises-International/hri-template-repository/contents/hri-stack-learnings.md \
  --jq '.content' | base64 -d > /tmp/hri-stack-learnings.md
```

Read `/tmp/hri-stack-learnings.md` before starting. Many hours have been lost to gotchas already documented there.

## Step 4: Confirm session scope

State what you understand the session goal to be. Wait for the user to confirm or redirect before writing any code.
