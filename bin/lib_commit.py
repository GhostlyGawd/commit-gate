#!/usr/bin/env python3
"""commit-gate shared library.

Pure, dependency-free (Python 3 stdlib only) helpers shared by:
  - bin/pre_commit_gate.py     (PreToolUse hook: validate/auto-fix a commit)
  - bin/post_commit_linear.py  (PostToolUse hook: deterministic Linear sync)
  - githooks/prepare-commit-msg (raw-commit template backstop)
  - githooks/post-commit        (raw-commit Linear backstop)

Design notes (why it is the way it is):
  * Commit DETECTION and message PARSING are done here with shlex tokenisation,
    not regex-on-the-raw-string, so compound commands (`git add -A && git commit`),
    env-prefixed commands (`GIT_AUTHOR_NAME=x git commit`), and wrapper words
    (`command git commit`) are handled robustly. (Fixes the brittle regex in the
    "minimal" design and the shallow detection in others.)
  * Issue-id matching is CASE-INSENSITIVE and normalised to upper-case, so a
    lowercase branch like `eng-481-foo` still yields `ENG-481`. (Fixes the shipped
    lowercase bug in the "minimal" design whose headline example did not run.)
  * Template enforcement DENIES a malformed subject rather than silently
    fabricating a type. (Rejects the `wip:` -> `chore:` fabrication.)
  * The Linear action is performed HERE over HTTPS with urllib — never delegated
    to the model / MCP — so the action is as deterministic as the trigger.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "template": {
        # Conventional-commit-ish. Edit to taste; this is the whole contract.
        "types": ["feat", "fix", "docs", "refactor", "test", "chore",
                  "perf", "build", "ci", "style", "revert"],
        "subjectMaxLen": 72,
        "requireIssueRef": True,
        # Issue handling is driven by a PREFIX ALLOWLIST so it can't match tech
        # tokens like utf-8 / sha-256 / x86-64. Empty => issue handling is
        # DISABLED (no requirement, no stamping, no Linear) until configured.
        "issuePrefixes": [],          # e.g. ["ENG", "OPS"]
        "issueRefRegex": "",          # optional explicit override (regex, ci)
        "trailerFormat": "Refs: {ISSUE}",
    },
    "linear": {
        "enabled": True,
        "action": "comment",                 # "comment" | "comment+move"
        "commentFormat": "Commit `{SHA}` on `{BRANCH}`: {SUBJECT}",
        "moveToStateName": "",                # e.g. "In Review"; empty = no move
        "apiUrl": "https://api.linear.app/graphql",
        "timeoutSeconds": 10,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(start_dir: str | None = None) -> dict:
    """Load commit-gate.config.json, searching: explicit path env, the given
    dir, CLAUDE_PROJECT_DIR, then the plugin root. Missing file => defaults."""
    candidates = []
    explicit = os.environ.get("COMMIT_GATE_CONFIG")
    if explicit:
        candidates.append(explicit)
    for d in (start_dir, os.environ.get("CLAUDE_PROJECT_DIR"),
              _plugin_root()):
        if d:
            candidates.append(os.path.join(d, "commit-gate.config.json"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return _deep_merge(DEFAULT_CONFIG, json.load(fh))
        except (OSError, ValueError):
            continue
    return dict(DEFAULT_CONFIG)


def _plugin_root() -> str:
    # bin/ -> plugin root is its parent.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------- #
# Shell tokenisation + commit detection
# --------------------------------------------------------------------------- #

_CONTROL_TOKENS = {"&&", "||", "|", ";", "&", "\n"}


def _tokenize(cmd: str) -> list[str] | None:
    """shlex-tokenise a command line. Returns None if it cannot be parsed
    (e.g. unbalanced quotes) so callers can fail safe."""
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return None


def _commit_index(tokens: list[str]) -> int:
    """Index of the `commit` token of a `git ... commit` invocation, or -1.

    Picks the LAST such occurrence so `git log; git commit` resolves to the
    commit. A `commit` token only counts if a `git` token precedes it within
    the same simple-command (i.e. no control operator in between)."""
    found = -1
    for i, tok in enumerate(tokens):
        if tok != "commit":
            continue
        # Walk back to the start of this simple command.
        j = i - 1
        saw_git = False
        while j >= 0 and tokens[j] not in _CONTROL_TOKENS:
            if tokens[j] == "git":
                saw_git = True
                break
            j -= 1
        if saw_git:
            found = i
    return found


def is_git_commit(cmd: str) -> bool:
    """True iff the command (possibly compound/env-prefixed/wrapped) runs a
    `git commit`. Excludes `git commit --help`."""
    if not cmd or "commit" not in cmd:
        return False
    tokens = _tokenize(cmd)
    if tokens is None:
        # Unparseable: fall back to a conservative literal check so we still
        # fire on the common case rather than silently missing it.
        return bool(re.search(r"\bgit\b[^\n;|&]*\bcommit\b", cmd)) \
            and "--help" not in cmd
    if "--help" in tokens:
        return False
    return _commit_index(tokens) != -1


def _command_span(tokens: list[str], commit_idx: int) -> tuple[int, int]:
    """[start, end) token span of the simple command containing commit_idx."""
    start = commit_idx
    while start - 1 >= 0 and tokens[start - 1] not in _CONTROL_TOKENS:
        start -= 1
    end = commit_idx
    while end < len(tokens) and tokens[end] not in _CONTROL_TOKENS:
        end += 1
    return start, end


# --------------------------------------------------------------------------- #
# Commit message extraction / rebuild
# --------------------------------------------------------------------------- #

_MSG_FLAGS = {"-m", "--message"}
_FILE_FLAGS = {"-F", "--file"}


def extract_message(cmd: str) -> str | None:
    """Return the inline commit message (joining repeated -m blocks with blank
    lines), or None if the commit takes its message from $EDITOR / -F / -c."""
    tokens = _tokenize(cmd)
    if tokens is None:
        return None
    ci = _commit_index(tokens)
    if ci == -1:
        return None
    start, end = _command_span(tokens, ci)
    parts: list[str] = []
    i = ci + 1
    while i < end:
        tok = tokens[i]
        if tok in _MSG_FLAGS and i + 1 < end:
            parts.append(tokens[i + 1])
            i += 2
            continue
        if tok.startswith("--message="):
            parts.append(tok[len("--message="):])
            i += 1
            continue
        if tok.startswith("-m") and len(tok) > 2:        # -m"msg"
            parts.append(tok[2:])
            i += 1
            continue
        i += 1
    if not parts:
        return None
    return "\n\n".join(parts)


def _has_unquoted_shell_op(cmd: str) -> bool:
    """True if the command has a shell control/redirection operator or command
    substitution OUTSIDE of quotes. Quote-aware, so metacharacters INSIDE a
    commit message — e.g. -m "fix <x> | y && z" — are correctly ignored."""
    i, n = 0, len(cmd)
    in_single = in_double = False
    while i < n:
        c = cmd[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
        elif c == '"':
            in_double = True
        elif c == "\\" and i + 1 < n:
            i += 2
            continue
        elif c == "$" and i + 1 < n and cmd[i + 1] == "(":
            return True
        elif c == "`":
            return True
        elif cmd[i:i + 2] in ("&&", "||", ">>", "<<"):
            return True
        elif c in "|;&<>":
            return True
        i += 1
    return False


def _is_simple_command(cmd: str) -> bool:
    """A single git-commit invocation with no UNQUOTED shell operators — the
    only shape we rewrite in place, so re-quoting tokens can't change meaning."""
    return not _has_unquoted_shell_op(cmd)


def rebuild_with_message(cmd: str, subject: str, body: str,
                         trailer: str | None) -> str | None:
    """Rebuild a SIMPLE git-commit command so it commits the canonical message
    via repeated -m blocks (git joins them with blank lines). Existing
    -m/-F message flags are stripped. Returns None if the command is not
    simple enough to rewrite safely (caller should DENY instead)."""
    if not _is_simple_command(cmd):
        return None
    tokens = _tokenize(cmd)
    if tokens is None:
        return None
    ci = _commit_index(tokens)
    if ci == -1:
        return None

    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _MSG_FLAGS or tok in _FILE_FLAGS:
            i += 2 if i + 1 < len(tokens) else 1          # drop flag + value
            continue
        if tok.startswith(("--message=", "--file=")) or \
           (tok.startswith(("-m", "-F")) and tok not in ("-m", "-F")):
            i += 1                                         # drop -mVALUE / -FVALUE
            continue
        out.append(tok)
        i += 1

    # Re-find commit index in the filtered token list.
    new_ci = _commit_index(out)
    msg_tokens = ["-m", subject]
    if body.strip():
        msg_tokens += ["-m", body]
    if trailer:
        msg_tokens += ["-m", trailer]
    out[new_ci + 1:new_ci + 1] = msg_tokens
    return " ".join(shlex.quote(t) for t in out)


# --------------------------------------------------------------------------- #
# Template validation
# --------------------------------------------------------------------------- #

def _subject_regex(cfg: dict) -> re.Pattern:
    types = "|".join(re.escape(t) for t in cfg["template"]["types"])
    return re.compile(rf"^(?:{types})(?:\([\w./-]+\))?: .+")


def _issue_regex(cfg: dict):
    """Compiled issue-id pattern, or None when issue handling isn't configured.
    Preference: explicit issueRefRegex > issuePrefixes allowlist > disabled.
    Returning None (the default) disables issue requirement/stamping/sync, which
    is the safe choice: it avoids matching tech tokens like utf-8 / sha-256."""
    tmpl = cfg["template"]
    explicit = (tmpl.get("issueRefRegex") or "").strip()
    if explicit:
        return re.compile(explicit, re.IGNORECASE)
    prefixes = [p for p in tmpl.get("issuePrefixes", []) if p]
    if prefixes:
        body = "|".join(re.escape(p) for p in prefixes)
        return re.compile(rf"\b(?:{body})-[0-9]+\b", re.IGNORECASE)
    return None


def find_issue(*texts: str, cfg: dict) -> str | None:
    """First issue id across the texts, normalised UPPER-CASE; None when not
    found or when issue handling is unconfigured."""
    pat = _issue_regex(cfg)
    if pat is None:
        return None
    for text in texts:
        if not text:
            continue
        m = pat.search(text)
        if m:
            return m.group(0).upper()
    return None


class TemplateResult:
    """Outcome of validating/normalising a message against the template."""
    def __init__(self, ok: bool, *, subject="", body="", trailer=None,
                 deny_reason=None):
        self.ok = ok
        self.subject = subject
        self.body = body
        self.trailer = trailer            # e.g. "Refs: ENG-123" or None
        self.deny_reason = deny_reason


def evaluate_message(message: str, branch: str, cfg: dict) -> TemplateResult:
    """Validate a message against the template, deriving a missing issue ref
    from the branch when possible. Never fabricates a type/subject — a
    malformed subject yields a DENY."""
    tmpl = cfg["template"]
    lines = message.splitlines()
    subject = lines[0].strip() if lines else ""
    body_lines = lines[1:]
    # Strip any pre-existing Refs: trailer so we don't duplicate it.
    body_lines = [ln for ln in body_lines
                  if not re.match(r"^\s*Refs:\s*\S+\s*$", ln)]
    body = "\n".join(body_lines).strip()

    if not subject:
        return TemplateResult(False, deny_reason=(
            'empty commit subject — provide "<type>(<scope>): <subject>".'))

    if not _subject_regex(cfg).match(subject):
        return TemplateResult(False, deny_reason=(
            f'subject {subject!r} must match "<type>(<scope>): <subject>" '
            f'(type ∈ {{{", ".join(tmpl["types"])}}}). '
            f'Rewrite the message and commit again.'))

    if len(subject) > tmpl["subjectMaxLen"]:
        return TemplateResult(False, deny_reason=(
            f"subject is {len(subject)} chars; max is {tmpl['subjectMaxLen']}."))

    trailer = None
    # Only enforce/derive an issue when issue handling is actually configured
    # (issuePrefixes or issueRefRegex). Unconfigured => subject-only template.
    if tmpl["requireIssueRef"] and _issue_regex(cfg) is not None:
        issue = find_issue(message, branch, cfg=cfg)
        if not issue:
            want = tmpl.get("issuePrefixes") or "your configured issueRefRegex"
            return TemplateResult(False, deny_reason=(
                f"no issue id found in the message or branch name {branch!r}; "
                f"the template requires one (prefixes: {want})."))
        trailer = tmpl["trailerFormat"].replace("{ISSUE}", issue)

    return TemplateResult(True, subject=subject, body=body, trailer=trailer)


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #

def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run git, never raising. A bad cwd / missing git yields a failed result
    (returncode 1, empty stdout) so callers degrade gracefully instead of
    crashing the hook."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True)
    except OSError:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")


def head_info(cwd: str | None = None) -> dict:
    def out(*a):
        r = git(*a, cwd=cwd)
        return r.stdout.strip() if r.returncode == 0 else ""
    return {
        "sha": out("rev-parse", "HEAD"),
        "sha_short": out("rev-parse", "--short", "HEAD"),
        "subject": out("log", "-1", "--pretty=%s"),
        "body": out("log", "-1", "--pretty=%B"),
        # symbolic-ref resolves the branch even on an unborn branch (the very
        # first commit in a repo), where rev-parse --abbrev-ref HEAD fails.
        "branch": out("symbolic-ref", "--short", "HEAD"),
    }


# --------------------------------------------------------------------------- #
# Dedupe ledger (so the Linear sync never double-posts for one SHA)
# --------------------------------------------------------------------------- #

def _ledger_path(cwd: str | None) -> str:
    base = cwd or "."
    r = git("rev-parse", "--git-dir", cwd=cwd)
    git_dir = r.stdout.strip() if r.returncode == 0 else os.path.join(base, ".git")
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(base, git_dir)
    d = os.path.join(git_dir, "commit-gate")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "synced.log")


def already_synced(sha: str, cwd: str | None = None) -> bool:
    try:
        with open(_ledger_path(cwd), "r", encoding="utf-8") as fh:
            return any(line.split()[0:1] == [sha] for line in fh if line.strip())
    except OSError:
        return False


def record_synced(sha: str, note: str, cwd: str | None = None) -> None:
    try:
        with open(_ledger_path(cwd), "a", encoding="utf-8") as fh:
            fh.write(f"{sha} {note}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Linear (deterministic; no model, no MCP)
# --------------------------------------------------------------------------- #

def _gql(query: str, variables: dict, cfg: dict, token: str) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        cfg["linear"]["apiUrl"], data=payload,
        headers={"Authorization": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(
            req, timeout=cfg["linear"]["timeoutSeconds"]) as resp:
        return json.load(resp)


def linear_sync(issue_id: str, head: dict, cfg: dict) -> tuple[bool, str]:
    """Post a commit comment (and optionally move state) on the Linear issue.
    Returns (ok, human_message). Never raises — a failure is reported, never
    blocks a commit."""
    token = os.environ.get("LINEAR_API_KEY")
    if not token:
        return False, "LINEAR_API_KEY not set; Linear sync skipped"
    try:
        # issue(id:) accepts the human identifier (ENG-123) directly.
        data = _gql("query($id:String!){issue(id:$id){id}}",
                    {"id": issue_id}, cfg, token)
        uuid = (data.get("data") or {}).get("issue", {})
        uuid = uuid.get("id") if isinstance(uuid, dict) else None
        if not uuid:
            return False, f"Linear issue {issue_id} not found"

        body = (cfg["linear"]["commentFormat"]
                .replace("{SHA}", head["sha_short"])
                .replace("{BRANCH}", head["branch"])
                .replace("{SUBJECT}", head["subject"]))
        res = _gql(
            "mutation($i:String!,$b:String!){"
            "commentCreate(input:{issueId:$i,body:$b}){success}}",
            {"i": uuid, "b": body}, cfg, token)
        ok = ((res.get("data") or {}).get("commentCreate") or {}).get(
            "success", False)

        moved = ""
        target = cfg["linear"].get("moveToStateName") or ""
        if ok and cfg["linear"]["action"] == "comment+move" and target:
            moved = _move_state(uuid, target, cfg, token)
        return bool(ok), f"commented on {issue_id}{moved}"
    except urllib.error.URLError as exc:
        return False, f"Linear API error: {exc}"
    except (ValueError, KeyError) as exc:
        return False, f"Linear response error: {exc}"


def _move_state(uuid: str, state_name: str, cfg: dict, token: str) -> str:
    data = _gql(
        "query($id:String!){issue(id:$id){team{states{nodes{id name}}}}}",
        {"id": uuid}, cfg, token)
    nodes = (((data.get("data") or {}).get("issue") or {}).get("team") or {}) \
        .get("states", {}).get("nodes", [])
    sid = next((n["id"] for n in nodes if n.get("name") == state_name), None)
    if not sid:
        return f"; state {state_name!r} not found"
    _gql("mutation($i:String!,$s:String!){"
         "issueUpdate(id:$i,input:{stateId:$s}){success}}",
         {"i": uuid, "s": sid}, cfg, token)
    return f"; moved to {state_name!r}"
