#!/usr/bin/env python3
"""commit-gate PreToolUse hook.

Fires on the Bash tool. If the command is a `git commit`, it enforces the
commit-message template DETERMINISTICALLY before the commit runs:

  * conforming + issue ref present  -> allow unchanged
  * conforming but issue ref missing, derivable from branch, SIMPLE command
        -> ALLOW with `updatedInput`, rewriting the command to add a `Refs:`
           trailer (the model cannot commit the un-stamped message)
  * malformed subject / no derivable issue / cannot rewrite safely
        -> DENY with a precise reason (never fabricates a type or subject)

Non-commit Bash calls exit 0 immediately. The model is never trusted to apply
the template — the runtime applies the hook's decision mechanically.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_commit as L  # noqa: E402


def _emit(decision: str, *, reason: str = "", updated_input: dict | None = None,
          context: str | None = None) -> None:
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        out["permissionDecisionReason"] = reason
    if updated_input is not None:
        # updatedInput replaces the ENTIRE tool_input, so callers pass the full
        # (copied) input dict with only `command` changed — preserving any
        # sibling fields like `description`.
        out["updatedInput"] = updated_input
    if context:
        out["additionalContext"] = context
    print(json.dumps({"hookSpecificOutput": out}))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # malformed input: do not interfere

    if event.get("tool_name") != "Bash":
        return 0
    tool_input = event.get("tool_input") or {}
    cmd = tool_input.get("command", "")
    if not L.is_git_commit(cmd):
        return 0  # not a commit: allow normal flow (no output)

    cwd = event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = L.load_config(cwd)
    branch = L.head_info(cwd)["branch"]

    message = L.extract_message(cmd)
    if message is None:
        _emit("deny", reason=(
            "commit-gate: this commit has no inline message. Pass "
            '-m "<type>(<scope>): <subject>" so the template can be enforced '
            "deterministically (an editor-driven commit cannot be inspected "
            "before it runs)."))
        return 0

    result = L.evaluate_message(message, branch, cfg)
    if not result.ok:
        _emit("deny", reason=f"commit-gate: {result.deny_reason}")
        return 0

    # Conforming. Ensure the Refs: trailer is actually present in the commit.
    needs_trailer = result.trailer is not None
    issue_in_message = L.find_issue(message, cfg=cfg) is not None

    if not needs_trailer or issue_in_message:
        # Already compliant as written; let it through untouched.
        return 0

    # Trailer must be added (issue came from the branch). Rewrite if we can do
    # so safely; otherwise DENY rather than risk mangling a compound command.
    rewritten = L.rebuild_with_message(cmd, result.subject, result.body,
                                       result.trailer)
    if rewritten is not None:
        new_input = dict(tool_input)
        new_input["command"] = rewritten
        _emit("allow", updated_input=new_input,
              context=f"commit-gate: added trailer '{result.trailer}' "
                      f"(derived from branch '{branch}').")
        return 0

    _emit("deny", reason=(
        f"commit-gate: this commit needs a '{result.trailer}' trailer "
        f"(issue derived from branch '{branch}'), but the command is too "
        f"complex to rewrite safely. Add the trailer to your message, e.g. a "
        f"second -m \"{result.trailer}\", or run the commit as a standalone "
        f"command."))
    return 0


if __name__ == "__main__":
    # Fail SAFE: an internal error must never crash the session or block work.
    # We allow the commit (exit 0, no decision) and surface the error on stderr.
    # The git/remote backstops remain the hard enforcement floor.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"commit-gate(pre): internal error, not blocking: {exc}\n")
        sys.exit(0)
