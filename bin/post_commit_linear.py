#!/usr/bin/env python3
"""commit-gate PostToolUse hook.

Fires after the Bash tool runs. If the command was a `git commit` that
SUCCEEDED, it updates Linear DETERMINISTICALLY by calling the GraphQL API
itself (never the model, never MCP). Idempotent via a per-SHA ledger, so it is
safe even if the optional git `post-commit` backstop also fires.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_commit as L  # noqa: E402


def _exit_code(event: dict):
    """PostToolUse payload field name has varied across versions; read both
    `tool_output` and `tool_response`, and tolerate camel/snake case."""
    for key in ("tool_output", "tool_response"):
        obj = event.get(key)
        if isinstance(obj, dict):
            for ec in ("exit_code", "exitCode"):
                if ec in obj:
                    return obj[ec]
    return None


def _emit_context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": text}}))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0

    if event.get("tool_name") != "Bash":
        return 0
    cmd = (event.get("tool_input") or {}).get("command", "")
    if not L.is_git_commit(cmd):
        return 0

    # If we can see the exit code and it's non-zero, the commit did not land.
    ec = _exit_code(event)
    if ec not in (None, 0, "0"):
        return 0

    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = L.load_config(cwd)
    if not cfg["linear"]["enabled"]:
        return 0

    head = L.head_info(cwd)
    if not head["sha"]:
        return 0  # no commit to sync (defensive)
    if L.already_synced(head["sha"], cwd):
        return 0  # the git backstop (or a prior run) already handled this SHA

    issue = L.find_issue(head["body"], head["branch"], cfg=cfg)
    if not issue:
        L.record_synced(head["sha"], "skip:no-issue", cwd)
        return 0

    ok, note = L.linear_sync(issue, head, cfg)
    # Only record TERMINAL outcomes. Transient failures (no token, network,
    # 5xx, not-found) are left UNRECORDED so a later commit/retry can sync —
    # recording them would poison this SHA permanently.
    if ok:
        L.record_synced(head["sha"], f"ok:{note}", cwd)
    _emit_context(f"commit-gate: {note} (commit {head['sha_short']}).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"commit-gate(post): internal error: {exc}\n")
        sys.exit(0)
