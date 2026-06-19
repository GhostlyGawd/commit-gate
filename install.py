#!/usr/bin/env python3
"""commit-gate installer (cross-platform, stdlib only).

Wires the two hooks into a target repo's Claude Code settings, using ABSOLUTE
paths and the detected Python interpreter — so it works as a plain drop-in
without the plugin/marketplace system. (For team-wide sharing, prefer the
plugin route in README.md, which uses ${CLAUDE_PLUGIN_ROOT} and avoids
machine-specific paths.)

Usage:
  python install.py [--target DIR] [--scope local|project]
                    [--with-git-backstop] [--check] [--uninstall]

Defaults: --target = current dir, --scope = local (.claude/settings.local.json,
gitignored — safe for absolute paths). The merge is idempotent and de-dupes by
command string; re-running repairs/updates in place.
"""
import argparse
import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
PRE = os.path.join(PLUGIN_ROOT, "bin", "pre_commit_gate.py")
POST = os.path.join(PLUGIN_ROOT, "bin", "post_commit_linear.py")
GITHOOKS = os.path.join(PLUGIN_ROOT, "githooks")
MARK = "commit-gate"  # substring that identifies our hook entries


def _cmd(script: str) -> str:
    return f'"{sys.executable}" "{script}"'


def _settings_path(target: str, scope: str) -> str:
    name = "settings.json" if scope == "project" else "settings.local.json"
    return os.path.join(target, ".claude", name)


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _is_ours(entry: dict) -> bool:
    for h in entry.get("hooks", []):
        if MARK in h.get("command", ""):
            return True
    return False


def _install(target: str, scope: str, git_backstop: bool) -> int:
    if not os.path.isfile(PRE) or not os.path.isfile(POST):
        print(f"commit-gate: hook scripts missing under {PLUGIN_ROOT}/bin/",
              file=sys.stderr)
        return 1

    path = _settings_path(target, scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = _load(path)
    hooks = cfg.setdefault("hooks", {})

    plan = {
        "PreToolUse": _cmd(PRE),
        "PostToolUse": _cmd(POST),
    }
    timeouts = {"PreToolUse": 15, "PostToolUse": 20}
    for event, command in plan.items():
        entries = hooks.setdefault(event, [])
        # Drop any prior commit-gate entries (idempotent re-install).
        entries[:] = [e for e in entries if not _is_ours(e)]
        entries.append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": command,
                       "timeout": timeouts[event]}],
        })

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print(f"commit-gate: hooks written to {path}")

    if git_backstop:
        _install_git_backstop(target)

    print("commit-gate: installed. RESTART the claude session (or /hooks) so "
          "settings are re-read.")
    print("commit-gate: set LINEAR_API_KEY in the launching shell to enable "
          "Linear sync (the template is enforced regardless).")
    return 0


def _install_git_backstop(target: str) -> None:
    r = subprocess.run(["git", "-C", target, "config", "core.hooksPath",
                        GITHOOKS], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"commit-gate: could not set core.hooksPath: {r.stderr.strip()}",
              file=sys.stderr)
        return
    # Make the git hooks executable on POSIX (no-op semantics on Windows).
    for name in ("prepare-commit-msg", "post-commit"):
        p = os.path.join(GITHOOKS, name)
        try:
            os.chmod(p, 0o755)
        except OSError:
            pass
    print(f"commit-gate: git backstop active (core.hooksPath -> {GITHOOKS}). "
          "Note: this overrides any existing core.hooksPath in this repo.")


def _check(target: str, scope: str) -> int:
    path = _settings_path(target, scope)
    cfg = _load(path)
    hooks = cfg.get("hooks", {})
    pre = any(_is_ours(e) for e in hooks.get("PreToolUse", []))
    post = any(_is_ours(e) for e in hooks.get("PostToolUse", []))
    status = "ACTIVE" if (pre and post) else "INCOMPLETE/NOT installed"
    print(f"commit-gate: {status} in {path} "
          f"(PreToolUse={pre}, PostToolUse={post})")
    return 0 if (pre and post) else 1


def _uninstall(target: str, scope: str) -> int:
    path = _settings_path(target, scope)
    cfg = _load(path)
    hooks = cfg.get("hooks", {})
    for event in ("PreToolUse", "PostToolUse"):
        if event in hooks:
            hooks[event] = [e for e in hooks[event] if not _is_ours(e)]
            if not hooks[event]:
                del hooks[event]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print(f"commit-gate: removed hook entries from {path}. "
          "core.hooksPath (if set) is left untouched; unset it manually if "
          "you enabled the git backstop.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=os.getcwd())
    ap.add_argument("--scope", choices=["local", "project"], default="local")
    ap.add_argument("--with-git-backstop", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    target = os.path.abspath(args.target)
    if args.check:
        return _check(target, args.scope)
    if args.uninstall:
        return _uninstall(target, args.scope)
    return _install(target, args.scope, args.with_git_backstop)


if __name__ == "__main__":
    sys.exit(main())
