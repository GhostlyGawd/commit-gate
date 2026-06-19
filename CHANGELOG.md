# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-06-19

Breaking redesign to a layered model: enforcement moved from a blocking
PreToolUse hook to a `commit-msg` git hook, and the per-commit Linear API
integration was removed in favor of native tracker linking.

### Added

- `githooks/commit-msg` — the enforcement **floor**: validates the final commit
  message and stamps the `Refs:` trailer for every commit regardless of client
  (`-m` / repeated `-m` / `-F` / heredoc / editor all handled uniformly).
- `bin/pre_commit_advisor.py` — a non-blocking PreToolUse **advisor** that
  auto-stamps the trailer and nudges the agent.
- `install.py` `--no-floor` / `--no-advisor` flags; the floor installs as a
  non-clobbering shim in the repo's hooks dir (detects husky/`core.hooksPath`).
- `RELEASING.md` — release-process checklist (SemVer + Keep a Changelog).

### Changed

- Commit enforcement now runs at the git layer (`commit-msg`), so it covers
  **all** commits — not just agent-session ones — and handles `-F`/heredoc/editor
  messages, which the PreToolUse-only design rejected.
- The PreToolUse hook is now advisory (auto-stamp + nudge) and never hard-blocks.

### Removed

- The per-commit Linear GraphQL integration: `bin/post_commit_linear.py`, the
  `post-commit` git hook, the `linear` config block, the dedupe ledger, and the
  `LINEAR_API_KEY` requirement. Issue linking is delegated to the tracker's
  native git integration; commit-gate ensures `Refs: <ID>` is present.
- `githooks/prepare-commit-msg` — superseded by `commit-msg` (which sees the
  final message).

## [1.0.0] - 2026-06-19

Initial stable release of commit-gate, a Claude Code plugin that enforces conventional-commit messages and keeps Linear in sync.

### Added

- **Conventional-commit enforcement (PreToolUse Bash hook):** Validates every `git commit` against a configurable conventional-commit template. Non-conforming commits are denied with a precise, human-readable reason, and the hook never fabricates a commit type or subject on your behalf.
- **Automatic issue trailers:** For simple commit commands, the hook rewrites the command via the `updatedInput` contract to append a `Refs: <ISSUE>` trailer derived from the current branch name.
- **Linear sync on commit (PostToolUse Bash hook):** After a successful commit, updates the corresponding Linear issue by calling the Linear GraphQL API directly — never through the model or an MCP server. A per-SHA dedupe ledger guarantees a given commit is never posted twice.
- **Shared commit library (`bin/lib_commit.py`):** Provides the core engine used by both hooks:
  - `shlex`-based git-commit detection that handles compound (`… && git commit`), env-prefixed, and wrapper command forms.
  - Case-insensitive issue-id extraction driven by a configurable prefix allowlist (`issuePrefixes`).
  - Conventional-commit template evaluation.
  - Safe, token-aware command rebuilding (for simple commands only).
  - Deterministic Linear synchronization over `urllib` (Python standard library only — no third-party dependencies).
- **Raw-commit git backstops (optional):** `githooks/prepare-commit-msg` and `githooks/post-commit`, wired through `core.hooksPath`, extend enforcement and Linear sync to commits made outside a Claude session.
- **Cross-platform installer (`install.py`):** Performs an idempotent settings merge and supports `--check`, `--uninstall`, and `--with-git-backstop`.
- **Packaging and distribution:**
  - A Claude Code skill (`skills/commit-gate/SKILL.md`) that carries a real, runnable installer.
  - A single-plugin marketplace (`.claude-plugin/marketplace.json`, source `"./"`).
  - An `examples/team-settings.json` for team-wide auto-enablement.
- **Configuration via `commit-gate.config.json`:** Tune commit types, `subjectMaxLen`, `issuePrefixes`, and the Linear action, comment format, and state transition.
- **Fail-safe hooks:** An internal error in any hook never blocks the user — commits proceed rather than being held hostage to a plugin bug.
- **Cross-platform shebang safety:** A `.gitattributes` enforces LF line endings so shell shebangs work consistently across platforms.

[Unreleased]: https://github.com/GhostlyGawd/commit-gate/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/GhostlyGawd/commit-gate/releases/tag/v2.0.0
[1.0.0]: https://github.com/GhostlyGawd/commit-gate/releases/tag/v1.0.0
