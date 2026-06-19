# commit-gate

Enforce a conventional-commit message template and keep work linked to your
issue tracker on **every** commit — and give your Claude Code agent a fast
in-session nudge so it gets commits right the first time.

## Architecture: two layers, each in its right place

commit-gate deliberately separates "the gate" from "the agent helper", and
delegates issue linking to your tracker:

| Layer | What it does | Where it lives |
|---|---|---|
| **Floor** | A `commit-msg` git hook: validates the **final** message against the template and stamps a `Refs: <ISSUE>` trailer; **aborts** on violation. Runs on every commit — terminal, IDE, CI, or agent — and because it reads the final message, `-m`, repeated `-m`, `-F`, heredoc, and editor commits all work uniformly. | git hook (`commit-msg`) |
| **Advisor** | A Claude Code `PreToolUse` hook on the agent's `git commit` calls: **auto-stamps** the trailer and gives a fast "here's the fix" nudge. It **never hard-blocks** — the floor is the gate. | Claude settings (`PreToolUse`) |
| **Linking** | Issue linking. | **Your tracker's native git integration.** commit-gate just guarantees `Refs: <ID>` is present; the integration does the linking. No tokens, no API calls. |

Why this split: deterministic, universal enforcement belongs at the **git layer**
(it sees every commit and the final message). Agent-loop help belongs in the
**Claude hook** (fast feedback, auto-fix). And issue linking is already solved by
trackers' native integrations, so commit-gate doesn't reimplement it — it only
ensures the ref is in the message.

> This is a deliberate redesign of the earlier blocking-PreToolUse + per-commit
> Linear-API approach, whose enforcement only covered agent-session commits and
> whose `-F`/heredoc handling was brittle. See `CHANGELOG.md`.

## Install (per-repo)

```
python /path/to/commit-gate/install.py --target /path/to/repo
python /path/to/commit-gate/install.py --target /path/to/repo --check   # -> ACTIVE
# the floor works immediately; restart claude (or /hooks) so the advisor loads
```

- The **floor** is installed as a tiny shim in the repo's hooks dir that execs
  the plugin's `githooks/commit-msg` with the detected interpreter. If a
  `commit-msg` hook already exists (husky/lefthook) or `core.hooksPath` is custom,
  commit-gate installs into the right place and won't clobber a foreign hook —
  it prints how to chain it instead.
- The **advisor** is written to `.claude/settings.local.json` by default
  (`--scope project` for the tracked `.claude/settings.json`).
- Flags: `--no-floor`, `--no-advisor`, `--uninstall`, `--check`.

## Issue linking (native — no tokens)

commit-gate stamps `Refs: <ISSUE>` (derived from the message or the branch name)
on every commit. Turn on your tracker's git integration to do the linking:

- **Linear:** enable the GitHub integration. It auto-links issues referenced by
  id and can transition them on PR events. Branch names like `eng-441-foo` and
  the stamped `Refs: ENG-441` both link.

This replaces the old per-developer `LINEAR_API_KEY` + a comment-on-every-commit
(noisy, and only covered commits made through the agent).

## Configure (`commit-gate.config.json`)

- `template.types`, `template.subjectMaxLen` — the subject rule (always enforced).
- `template.issuePrefixes` — e.g. `["ENG", "OPS"]`. Issue requirement and `Refs:`
  stamping are **off until this (or `issueRefRegex`) is set** — deliberately, so
  an unconfigured wildcard can't match `utf-8` / `sha-256`. Matched
  case-insensitively, so `eng-441` derives `ENG-441`.

## Workflow

Agent runs `git commit -m "fix login"` on branch `eng-441-foo`:

1. **Advisor** sees the malformed subject and nudges the agent ("the commit-msg
   hook will reject this — subject must match `<type>(<scope>): <subject>`").
   It does **not** block.
2. Agent retries `git commit -m "fix(auth): handle failed login"`. The subject is
   valid but the issue ref is missing; the advisor **auto-stamps** `Refs: ENG-441`
   (rewriting the command) so the floor won't have to reject it.
3. The commit runs. The **floor** reads the final message, confirms it conforms,
   normalises it, and lets the commit land.
4. Linear's native integration links `ENG-441`.

A human committing in their own terminal skips steps 1–2 (no agent) but still
hits the **floor** at step 3 — that's the universal enforcement the old
PreToolUse-only design lacked.

## Layout

```
commit-gate/
├── .claude-plugin/
│   ├── plugin.json
│   └── marketplace.json           # one-plugin marketplace (source "./")
├── examples/team-settings.json
├── bin/
│   ├── lib_commit.py              # shared: detection, template, issue derivation
│   └── pre_commit_advisor.py      # PreToolUse advisor (nudge + auto-stamp)
├── githooks/commit-msg            # the enforcement floor
├── hooks/hooks.json               # registers the advisor (${CLAUDE_PLUGIN_ROOT})
├── skills/commit-gate/SKILL.md
├── commit-gate.config.json
├── install.py
├── CHANGELOG.md
└── RELEASING.md
```

## Team distribution (marketplace)

This directory is also a one-plugin marketplace. Push it to a git repo
(`GhostlyGawd/commit-gate`) and either:

- *Manual:* `/plugin marketplace add GhostlyGawd/commit-gate` then
  `/plugin install commit-gate@commit-gate-marketplace`.
- *Auto-enable:* commit the keys in `examples/team-settings.json` to the
  consuming repo's tracked `.claude/settings.json`; teammates are prompted on
  clone. `enabledPlugins` is an **object** (`"plugin@marketplace": true`).

Note: the marketplace route installs the **advisor** (a Claude plugin hook); the
**floor** is a git hook, so run `install.py --no-advisor` (or `--with`-style
setup) to add the `commit-msg` hook per repo, or commit it via your own git-hook
manager. Plugins can't install git hooks for you.

**Worktrees:** if the advisor is enabled per-person via `/plugin install`, its
flag lives in gitignored `.claude/settings.local.json`, so Claude worktrees start
with it off — add that file to the repo's `.worktreeinclude`. Committing
enablement to the tracked `.claude/settings.json` avoids this.

## Limits

- The floor is a git hook: a `git commit --no-verify` skips it. For a hard,
  un-bypassable guard, add a server-side `pre-receive` hook.
- It governs the `git` CLI; aliases (`git ci`) and non-CLI writers aren't seen.
- The advisor relies on PreToolUse `updatedInput`/`additionalContext`; if a
  Claude Code version doesn't honor them, the **floor still enforces** correctly
  — the advisor is pure convenience.
- Requires Python 3 on PATH. The plugin `hooks.json` invokes `python3`; on
  Windows where only `python` exists, prefer `install.py` (it bakes in the
  detected interpreter).
