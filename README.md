# commit-gate

Deterministically enforce a **commit-message template** and **update Linear on
every `git commit`** in an interactive Claude Code session — packaged as a
Claude Code skill/plugin.

It is the hybrid of three independent designs: the safest enforcement engine
(rewrite-before-commit, **deny rather than fabricate**, per-SHA dedupe, robust
commit detection, raw-commit git backstop) wrapped in clean, vendorable plugin
packaging, with a skill that genuinely installs rather than just documents.

## The core idea

A **skill cannot be the trigger** — skills load on the model's judgment, which
is not deterministic. Only **hooks** fire mechanically on every tool event. So:

- the **hooks** are the trigger and the actor (deterministic), and
- the **skill** is the carrier/installer/manual.

Two hooks, both on the `Bash` tool:

| Phase | Event | Does |
|---|---|---|
| Before the commit runs | `PreToolUse` | Validate the message. Rewrite (add `Refs:` trailer) via `updatedInput`, or **deny** with a precise reason. The model cannot commit a non-conforming message. |
| After the commit lands | `PostToolUse` | Call the **Linear GraphQL API directly** with `LINEAR_API_KEY`. No model, no MCP — so the action is as deterministic as the trigger. |

## How it meets the three requirements

1. **Interactive mode** — `PreToolUse`/`PostToolUse` hooks fire during a live
   `claude` session. No GitHub Actions, no `claude -p` headless.
2. **Every time, deterministic** — the runtime invokes the hooks on every Bash
   tool call regardless of what the model decides; the script (not the model)
   performs the template decision and the Linear call.
3. **Saved as a skill** — `skills/commit-gate/SKILL.md` carries a real,
   idempotent install/verify step (`install.py`); the skill is not vestigial.

## Layout

```
commit-gate/
├── .claude-plugin/
│   ├── plugin.json                # plugin manifest
│   └── marketplace.json           # one-plugin marketplace (source: "./")
├── examples/team-settings.json    # copy-paste team auto-enablement keys
├── hooks/hooks.json               # plugin hook registration (${CLAUDE_PLUGIN_ROOT})
├── bin/
│   ├── lib_commit.py              # shared: detection, template, Linear, dedupe
│   ├── pre_commit_gate.py         # PreToolUse hook
│   └── post_commit_linear.py      # PostToolUse hook
├── githooks/                      # optional raw-commit backstop (core.hooksPath)
│   ├── prepare-commit-msg
│   └── post-commit
├── skills/commit-gate/SKILL.md    # the carrier/installer skill
├── commit-gate.config.json        # team-tunable template + Linear config
└── install.py                     # cross-platform installer
```

## Install

**Single developer / any repo (recommended):**

```
python install.py --target /path/to/repo            # writes .claude/settings.local.json
python install.py --target /path/to/repo --check    # verify -> ACTIVE
export LINEAR_API_KEY=lin_api_xxx                    # in the shell that launches claude
# restart claude (or /hooks) so settings are re-read
```

`--scope project` writes to the committed `.claude/settings.json` instead — but
note it uses absolute machine-specific paths, so that's only appropriate for a
single machine. For team-wide sharing use the plugin route below.

**Team-wide (marketplace, zero per-person setup):** this directory is *also* a
one-plugin marketplace (`.claude-plugin/marketplace.json`, plugin `source: "./"`),
so push it to a git repo (e.g. `GhostlyGawd/commit-gate`) and distribute it one of
two ways:

- *Manual, per person:*
  ```
  /plugin marketplace add GhostlyGawd/commit-gate
  /plugin install commit-gate@commit-gate-marketplace
  ```
- *Auto-enable for everyone (recommended):* commit these keys to the **consuming
  repo's tracked** `.claude/settings.json` (ready to copy in
  `examples/team-settings.json`):
  ```json
  {
    "extraKnownMarketplaces": {
      "commit-gate-marketplace": {
        "source": { "source": "github", "repo": "GhostlyGawd/commit-gate" },
        "autoUpdate": true
      }
    },
    "enabledPlugins": { "commit-gate@commit-gate-marketplace": true }
  }
  ```
  On clone + workspace-trust, teammates are prompted to install the marketplace
  and the plugin. `hooks/hooks.json` uses `${CLAUDE_PLUGIN_ROOT}`, so no absolute
  paths leak (unlike the `install.py` route). Note: `enabledPlugins` is an
  **object** (`"plugin@marketplace": true`), not an array.

**Worktrees gotcha.** `/plugin install` records the enable flag in gitignored
`.claude/settings.local.json`, so Claude-created worktrees start with the plugin
**OFF** — add `.claude/settings.local.json` to the consuming repo's
`.worktreeinclude` (gitignore-syntax; only copies files that are also gitignored).
Committing enablement to the *tracked* `.claude/settings.json` (above) sidesteps
this entirely, since tracked files are always present in a worktree.

**Updates.** Bump `version` in `.claude-plugin/plugin.json` to push updates
(`autoUpdate: true` makes teammates pick them up); or omit `version` to treat
every commit as a new version. The Windows `python3`-vs-`python` caveat below
applies to this route too — `install.py` bakes in the detected interpreter.

**Optional raw-commit backstop:** `--with-git-backstop` wires `core.hooksPath`
to `githooks/` so commits made *outside* a Claude session are also templated and
Linear-synced. The per-SHA dedupe ledger keeps the git hook and the Claude
PostToolUse hook from double-posting.

## Configuration (`commit-gate.config.json`)

- `template.issuePrefixes` — your team's issue prefixes, e.g. `["ENG", "OPS"]`.
  **Issue handling (requirement, `Refs:` stamping, Linear sync) is OFF until
  this — or an explicit `template.issueRefRegex` — is set.** This is deliberate:
  an unconfigured wildcard would match tokens like `utf-8` / `sha-256` and stamp
  garbage. Prefixes match case-insensitively, so `eng-481` → `ENG-481`.
- `template.types` / `subjectMaxLen` — the conventional-commit subject rule
  (always enforced, independent of issue handling).
- `linear.*` — `enabled`, `action` (`comment` | `comment+move`),
  `commentFormat`, `moveToStateName`.

Note: the PreToolUse rewrite path returns `permissionDecision: "allow"`, which
auto-approves the (corrected) commit — it bypasses any permission prompt you'd
otherwise get for `git commit`. The hook vouches for the command it rewrote.

## Worked examples

**Template.** Model runs `git commit -m "fix login"`. The subject fails the
template → PreToolUse **denies** with the rule. Model retries
`git commit -m "fix(auth): handle failed login"` on branch `eng-441-login`; the
issue ref is missing but derivable, the command is simple → PreToolUse **allows
with `updatedInput`**, appending `-m "Refs: ENG-441"`. The commit lands
conforming and issue-stamped — the model could not have committed otherwise.

**Linear.** That commit lands. PostToolUse reads HEAD, extracts `ENG-441`
(case-insensitively, so a lowercase branch still works), resolves the issue via
GraphQL, and posts ``Commit `a1b2c3d` on `eng-441-login`: fix(auth)…``. The
model did nothing for this.

## Limits (honest)

- Commits typed in another terminal aren't Bash tool calls → not seen in-session
  (use `--with-git-backstop`; remote `pre-receive` is the only true guard for
  raw `--no-verify`).
- Git **aliases** (`git ci`) and non-CLI writers aren't detected.
- In-session rewrite is applied only to **simple** commands; a complex compound
  commit needing a trailer is denied with guidance, never rewritten unsafely.
- Requires Python 3 on PATH. The plugin `hooks/hooks.json` invokes `python3`;
  on Windows where only `python` exists, prefer `install.py` (it bakes in the
  detected interpreter via `sys.executable`).
