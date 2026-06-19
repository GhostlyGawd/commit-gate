#!/usr/bin/env python3
"""commit-gate PreToolUse advisor.

Fires on the Bash tool. For a `git commit`, it HELPS the agent but NEVER
hard-blocks — the commit-msg git hook is the gate, and it sees every commit:

  * valid subject + an issue id derivable from the branch but missing from the
    message + a simple command  -> ALLOW with `updatedInput`, auto-stamping the
    `Refs: <ISSUE>` trailer so the floor never has to reject it.
  * a message the floor would reject (malformed subject / no derivable issue)
    -> emit advisory context so the agent fixes it pre-emptively (not a block).
  * already conforming, or an -F/editor commit we can't read inline -> stay
    silent and let the commit-msg floor do its job.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_commit as L  # noqa: E402


def _emit(obj: dict) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **obj}}))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if event.get("tool_name") != "Bash":
        return 0
    tool_input = event.get("tool_input") or {}
    cmd = tool_input.get("command", "")
    if not L.is_git_commit(cmd):
        return 0

    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = L.load_config(cwd)
    branch = L.head_info(cwd)["branch"]

    message = L.extract_message(cmd)
    if message is None:
        return 0  # -F / editor commit: nothing to read here; the floor handles it.

    result = L.evaluate_message(message, branch, cfg)

    if not result.ok:
        # Advisory only — the commit-msg floor is the gate. Best-effort nudge so
        # the agent can fix the message before the floor rejects it.
        _emit({"additionalContext":
               f"commit-gate: the commit-msg hook will reject this — "
               f"{result.deny_reason}"})
        return 0

    # Conforming. Auto-stamp the trailer only when it was DERIVED from the branch
    # (absent from the message) AND the command is simple enough to rewrite. The
    # floor stamps everything else; we just save the agent a round-trip here.
    needs_stamp = result.trailer is not None and \
        L.find_issue(message, cfg=cfg) is None
    if needs_stamp:
        rewritten = L.rebuild_with_message(cmd, result.subject, result.body,
                                           result.trailer)
        if rewritten is not None:
            new_input = dict(tool_input)
            new_input["command"] = rewritten
            _emit({"permissionDecision": "allow",
                   "updatedInput": new_input,
                   "additionalContext":
                       f"commit-gate: auto-stamped '{result.trailer}' "
                       f"(derived from branch '{branch}')."})
    return 0


if __name__ == "__main__":
    # Fail SAFE: an advisor bug must never block the user's commit.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"commit-gate(advisor): internal error, not blocking: {exc}\n")
        sys.exit(0)
