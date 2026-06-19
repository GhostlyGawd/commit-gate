---
name: commit-gate
description: >
  Install, verify, configure, and explain commit-gate — the mechanism that
  DETERMINISTICALLY enforces a commit-message template and updates Linear on
  EVERY git commit in an interactive Claude Code session. Use this skill when
  the user wants something to happen reliably on every commit (commit-message
  template/format enforcement, Linear issue updates, commit automation), asks
  to set up or repair commit hooks, or asks why a commit was blocked or
  rewritten. IMPORTANT: this skill is the INSTALLER and operator's manual — it
  is NOT itself the every-commit trigger. The installed PreToolUse/PostToolUse
  hooks are. (A skill loads on model judgment and so can never be a reliable
  per-commit trigger; that is exactly why the work lives in the hooks.)
---

# commit-gate

## What guarantees the behavior (read first)

The "on every commit" guarantee comes from two **hooks** this skill installs,
not from this skill body:

- A **PreToolUse** hook on the `Bash` tool inspects every `git commit` *before*
  it runs and either rewrites it (to add a `Refs:` trailer) or denies it (on a
  malformed subject / missing issue). The model cannot commit a non-conforming
  message — the runtime applies the hook's decision mechanically.
- A **PostToolUse** hook calls the **Linear GraphQL API directly** after a
  commit lands. The model is never asked to update Linear, so it cannot forget
  or refuse.

This skill's only job is a one-time, idempotent **install**. After that,
enforcement is automatic. Do not add any step that asks the model to apply the
template or call Linear — that would reintroduce non-determinism.

## Install (one step, idempotent)

1. Prerequisite: Python 3 on PATH (the hooks and installer are stdlib-only —
   no `jq`/`curl` needed).
2. From the commit-gate directory, run the installer against the target repo:

   ```
   python install.py --target /path/to/repo
   ```

   This writes the two hook entries into the repo's
   `.claude/settings.local.json` (personal, gitignored — safe for the absolute
   paths it uses). Re-running repairs/updates in place.
3. **Restart the `claude` session** (or run `/hooks`) so settings are re-read —
   hooks load at session start. This is the #1 "why didn't it fire" gotcha.
4. For Linear sync, export a token in the shell that launches `claude`:
   `export LINEAR_API_KEY=lin_api_xxx` (never commit it). Without it, the
   template is still enforced; Linear sync is skipped and noted.
5. Optional raw-commit coverage (commits made outside the Claude session):
   add `--with-git-backstop` to also wire `core.hooksPath` to the bundled git
   hooks. Note this overrides any existing `core.hooksPath` in the repo.

Verify: `python install.py --target /path/to/repo --check` prints `ACTIVE`,
and `/hooks` in-session lists the two commit-gate hooks.

## Team distribution (marketplace)

This plugin is also a one-plugin marketplace (`.claude-plugin/marketplace.json`).
Push it to a git repo, then either have each person run
`/plugin marketplace add GhostlyGawd/commit-gate` +
`/plugin install commit-gate@commit-gate-marketplace`, or commit the
`extraKnownMarketplaces` + `enabledPlugins` keys (object form — see
`examples/team-settings.json`) to the consuming repo's **tracked**
`.claude/settings.json` so teammates are prompted to install on clone. If anyone
enables per-person via `/plugin install`, the flag lands in gitignored
`settings.local.json` — add it to that repo's `.worktreeinclude` so Claude
worktrees don't start with the plugin off. Full details in `README.md`.

## Configure (no code edits)

Edit `commit-gate.config.json` (in the repo root, or the plugin root):

- `template.types`, `template.subjectMaxLen` — the conventional-commit rule.
- `template.issuePrefixes` — your team's issue prefixes, e.g. `["ENG", "OPS"]`.
  This is REQUIRED to turn on issue handling: issue requirement, `Refs:`
  stamping, and Linear sync are all OFF until prefixes (or an explicit
  `issueRefRegex`) are set. Prefixes are matched case-insensitively, so a
  lowercase branch like `eng-481` still derives `ENG-481`. (Leaving it empty
  deliberately avoids false matches on tokens like `utf-8` / `sha-256`.)
- `template.requireIssueRef` — when true AND prefixes are configured, a commit
  with no derivable issue id is denied.
- `linear.enabled`, `linear.action` (`comment` | `comment+move`),
  `linear.commentFormat`, `linear.moveToStateName`.

## When a commit is blocked or rewritten

- **Blocked (deny):** the subject doesn't match `<type>(<scope>): <subject>`,
  or no issue id was found in the message or branch. Fix the message and commit
  again — the exact reason is printed.
- **Rewritten (allow + updatedInput):** the message was fine but missing the
  `Refs:` trailer, which commit-gate derived from the branch and added. This is
  expected, not an error. Note: the rewrite path returns `permissionDecision:
  "allow"`, which auto-approves that (corrected) commit — it bypasses any
  permission prompt you'd normally get for `git commit`. The hook is vouching
  for the command it just rewrote.

## Why not call the Linear MCP tool from the hook?

A hook shell/Python script cannot invoke MCP tools, and *asking* the model to
call the Linear MCP tool would make the action non-deterministic (it might not
comply). commit-gate calls Linear's GraphQL API directly so the update is
guaranteed. This trade-off is intentional and load-bearing.

## Honest limits

- A `git commit` typed in a **separate terminal** (not a Bash tool call in this
  session) is not seen by the Claude hooks. Use `--with-git-backstop` for the
  template + Linear coverage on those, and a remote `pre-receive` hook for the
  only true guard against raw `git commit --no-verify`.
- Commit detection is robust for `git commit`, compound (`a && git commit`),
  env-prefixed, and wrapper forms, but does NOT see git **aliases** (`git ci`)
  or non-`git`-CLI writers (libgit2/JGit). Document your aliases or rely on the
  git/remote backstops.
- The in-session command rewrite only applies to **simple** commands; a complex
  compound commit that needs a trailer is denied with guidance rather than
  rewritten unsafely.
