#!/usr/bin/env python3
"""commit-gate installer (cross-platform, stdlib only).

Installs the two layers into a target repo:

  1. FLOOR  — a `commit-msg` git hook (the enforcement gate; catches EVERY
     commit, any client). Installed as a tiny shim in the repo's hooks dir that
     execs this plugin's githooks/commit-msg with the detected interpreter.
  2. ADVISOR — a PreToolUse hook entry in the repo's Claude settings, so the
     in-session agent gets a fast nudge + trailer auto-stamp. Never blocks.

Usage:
  python install.py [--target DIR] [--scope local|project]
                    [--no-floor] [--no-advisor] [--check] [--uninstall]

Defaults: --target = cwd, --scope = local (.claude/settings.local.json), both
layers installed. Idempotent; re-running repairs in place.
"""
import argparse
import json
import os
import subprocess
import sys

PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
ADVISOR = os.path.join(PLUGIN_ROOT, "bin", "pre_commit_advisor.py")
FLOOR = os.path.join(PLUGIN_ROOT, "githooks", "commit-msg")
MARK = "commit-gate"


# ----------------------------- shared helpers ----------------------------- #

def _git(target, *args):
    return subprocess.run(["git", "-C", target, *args], capture_output=True,
                          text=True)


def _settings_path(target, scope):
    name = "settings.json" if scope == "project" else "settings.local.json"
    return os.path.join(target, ".claude", name)


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _hooks_dir(target):
    """(hooks_dir, is_custom_hookspath) or (None, False) if not a git repo."""
    r = _git(target, "config", "--get", "core.hooksPath")
    if r.returncode == 0 and r.stdout.strip():
        hp = r.stdout.strip()
        return (hp if os.path.isabs(hp) else os.path.join(target, hp)), True
    r = _git(target, "rev-parse", "--git-path", "hooks")
    if r.returncode != 0 or not r.stdout.strip():
        return None, False
    hd = r.stdout.strip()
    return (hd if os.path.isabs(hd) else os.path.join(target, hd)), False


# ------------------------------- the FLOOR -------------------------------- #

def _install_floor(target):
    hd, custom = _hooks_dir(target)
    if hd is None:
        print(f"commit-gate: {target} is not a git repo; skipping the floor.",
              file=sys.stderr)
        return
    os.makedirs(hd, exist_ok=True)
    path = os.path.join(hd, "commit-msg")

    # If core.hooksPath already points at our own githooks, the real hook runs
    # directly — no shim needed.
    if os.path.abspath(path) == os.path.abspath(FLOOR):
        print(f"commit-gate: floor already active via core.hooksPath -> {hd}")
        return

    if os.path.exists(path):
        existing = ""
        try:
            existing = open(path, "r", encoding="utf-8", errors="ignore").read()
        except OSError:
            pass
        if MARK not in existing:
            print(f"commit-gate: WARNING — {path} already exists and is not "
                  f"ours; NOT overwriting. To chain it, add this line to it:\n"
                  f'    "{sys.executable}" "{FLOOR}" "$1" || exit $?',
                  file=sys.stderr)
            return

    shim = (f'#!/bin/sh\n# {MARK} commit-msg floor (auto-generated)\n'
            f'exec "{sys.executable}" "{FLOOR}" "$@"\n')
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(shim)
    os.chmod(path, 0o755)
    note = " (into your custom core.hooksPath)" if custom else ""
    print(f"commit-gate: commit-msg floor installed at {path}{note}")


def _floor_status(target):
    hd, _ = _hooks_dir(target)
    if hd is None:
        return False
    path = os.path.join(hd, "commit-msg")
    if os.path.abspath(path) == os.path.abspath(FLOOR):
        return True
    try:
        return MARK in open(path, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return False


def _uninstall_floor(target):
    hd, _ = _hooks_dir(target)
    if hd is None:
        return
    path = os.path.join(hd, "commit-msg")
    if os.path.abspath(path) == os.path.abspath(FLOOR):
        print("commit-gate: floor is your core.hooksPath; left untouched.")
        return
    try:
        if os.path.exists(path) and MARK in open(path, encoding="utf-8",
                                                  errors="ignore").read():
            os.remove(path)
            print(f"commit-gate: removed floor shim {path}")
    except OSError:
        pass


# ------------------------------ the ADVISOR ------------------------------- #

def _advisor_cmd():
    return f'"{sys.executable}" "{ADVISOR}"'


def _is_ours(entry):
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []))


def _install_advisor(target, scope):
    path = _settings_path(target, scope)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = _load(path)
    hooks = cfg.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", [])
    entries[:] = [e for e in entries if not _is_ours(e)]   # idempotent
    entries.append({
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": _advisor_cmd(), "timeout": 15}],
    })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    print(f"commit-gate: PreToolUse advisor written to {path}")


def _advisor_status(target, scope):
    cfg = _load(_settings_path(target, scope))
    return any(_is_ours(e) for e in cfg.get("hooks", {}).get("PreToolUse", []))


def _uninstall_advisor(target, scope):
    path = _settings_path(target, scope)
    cfg = _load(path)
    pre = cfg.get("hooks", {}).get("PreToolUse")
    if pre is not None:
        cfg["hooks"]["PreToolUse"] = [e for e in pre if not _is_ours(e)]
        if not cfg["hooks"]["PreToolUse"]:
            del cfg["hooks"]["PreToolUse"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        print(f"commit-gate: removed advisor entries from {path}")


# --------------------------------- main ----------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=os.getcwd())
    ap.add_argument("--scope", choices=["local", "project"], default="local")
    ap.add_argument("--no-floor", action="store_true")
    ap.add_argument("--no-advisor", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    target = os.path.abspath(args.target)

    if args.check:
        floor = _floor_status(target)
        adv = _advisor_status(target, args.scope)
        print(f"commit-gate: floor (commit-msg gate) = {'ACTIVE' if floor else 'absent'}; "
              f"advisor (PreToolUse) = {'ACTIVE' if adv else 'absent'}")
        return 0 if (floor or adv) else 1

    if args.uninstall:
        _uninstall_floor(target)
        _uninstall_advisor(target, args.scope)
        return 0

    if not os.path.isfile(ADVISOR) or not os.path.isfile(FLOOR):
        print(f"commit-gate: plugin files missing under {PLUGIN_ROOT}",
              file=sys.stderr)
        return 1
    if not args.no_floor:
        _install_floor(target)
    if not args.no_advisor:
        _install_advisor(target, args.scope)
    print("commit-gate: done. The floor works immediately; RESTART the claude "
          "session (or /hooks) so the advisor loads. Enable Linear's GitHub "
          "integration for issue linking (the floor stamps 'Refs: <ID>').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
