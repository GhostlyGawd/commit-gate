---
name: commit-gate
description: >
  Install, verify, configure, and explain commit-gate — which enforces a
  conventional-commit message template and ensures issue refs on EVERY commit
  via a `commit-msg` git hook (the gate), and gives the Claude agent a fast
  in-session nudge + trailer auto-stamp via a PreToolUse hook (advisory, never
  blocks). Use when the user wants commit-message/format enforcement, issue-ref
  stamping, or commit automation set up or repaired, or asks why a commit was
  rejected or auto-stamped. The enforcement lives in the git hook; this skill
  installs it. (A skill loads on model judgment, so it can't be the trigger.)
---

# commit-gate

## How it works (two layers)

- **Floor — a `commit-msg` git hook.** The gate. It validates the *final*
  commit message against the template and stamps a `Refs: <ISSUE>` trailer, on
  **every** commit (any client), aborting on a violation. Because it reads the
  final message, `-m` / `-F` / heredoc / editor commits all work.
- **Advisor — a PreToolUse hook.** When the agent runs `git commit`, it
  auto-stamps the trailer and nudges the agent if the message would be rejected.
  It **never hard-blocks** — the floor is the gate. (Earlier versions blocked in
  PreToolUse; that wrongly rejected `-F`/heredoc commits and only covered
  agent-session commits. This is the fix.)
- **Linking — native.** Issue linking is delegated to your tracker's git
  integration (e.g. Linear's GitHub integration). commit-gate only guarantees
  the `Refs: <ID>` is present so the integration links it — no tokens, no API.

## Install (one step, idempotent)

```
python install.py --target /path/to/repo
python install.py --target /path/to/repo --check    # -> floor + advisor ACTIVE
```

- The floor works immediately. **Restart the `claude` session** (or `/hooks`)
  so the advisor loads — hooks load at session start.
- If a `commit-msg` hook already exists (husky/lefthook), commit-gate won't
  clobber it; it prints how to chain it.
- Flags: `--no-floor` / `--no-advisor` to install one layer, `--scope project`
  to write the advisor into the tracked `.claude/settings.json`, `--uninstall`.

## Configure (`commit-gate.config.json`)

- `template.types`, `template.subjectMaxLen` — the subject rule (always on).
- `template.issuePrefixes` — your prefixes, e.g. `["ENG", "OPS"]`. Issue
  requirement + `Refs:` stamping are OFF until set (so an unconfigured wildcard
  can't match `utf-8` / `sha-256`). Case-insensitive; `eng-441` -> `ENG-441`.

## Issue linking

Enable your tracker's native git integration (Linear → GitHub integration). The
floor stamps `Refs: <ID>` and branch names carry the id, so issues auto-link
with no per-developer token and no comment-per-commit noise.

## When a commit is rejected or auto-stamped

- **Rejected (by the floor):** the subject doesn't match the template, or an
  issue id is required but not derivable. Fix the message and commit again — the
  exact reason is printed by the git hook.
- **Auto-stamped (by the advisor):** the subject was valid but missing the
  `Refs:` trailer, which commit-gate derived from the branch and added. The
  advisor returns `permissionDecision: "allow"` for that rewrite, which
  auto-approves the corrected commit.

## Honest limits

- The floor is a git hook: `git commit --no-verify` skips it; a remote
  `pre-receive` hook is the only un-bypassable guard.
- Governs the `git` CLI; aliases / non-CLI writers aren't seen.
- The advisor is pure convenience — if a Claude Code version doesn't honor its
  `updatedInput`/`additionalContext`, the floor still enforces correctly.
