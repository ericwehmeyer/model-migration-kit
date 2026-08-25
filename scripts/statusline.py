#!/usr/bin/env python3
"""Claude Code status line for this repo, on either machine.

Why this exists rather than the default: this project runs many agents across
many worktrees, and the two mistakes that have actually cost time here are both
invisible without it.

1. **Not knowing which worktree you are in.** There are 70+ of them. Before the
   ``.pth`` fix an agent in the wrong tree silently imported another tree's
   code; the fix removed that failure, but "which checkout am I editing" is
   still the first question every agent asks.
2. **Not knowing how far behind ``main`` you are.** Branches are cut per chunk
   and ``main`` moves several times an hour. A branch fifteen commits behind is
   a branch whose merge is about to be interesting.

**Installation.** This lives in ``scripts/`` rather than ``.claude/`` because
``.claude/`` is gitignored here -- it holds each operator's local state, and a
tool both machines share is not local state. Wire it up in your own
``.claude/settings.json``, which stays untracked:

    {"statusLine": {"type": "command",
                    "command": "python scripts/statusline.py"}}

Use ``python3`` on macOS if ``python`` is not on PATH. The path is relative to
the repo root, so it resolves in every worktree without editing.

**On the input format.** Claude Code passes a JSON object on stdin. This script
reads it defensively -- every field through ``.get`` with a fallback -- because
the schema is not something this repo controls, and a status line that raises is
a status line that hides the very information it exists to show. If the JSON is
absent, unparseable, or shaped differently than expected, it falls back to the
process's own working directory and still prints something useful.

**On speed.** This runs on every render. Every subprocess call is capped by
``_TIMEOUT`` and any failure degrades to a missing field rather than an error.
The whole thing is four short git calls; if git is slow or absent you get a
shorter line, never a hang.

**On encoding, learned the hard way in this file.** The first version used
``U+2387`` as a branch glyph and died with ``UnicodeEncodeError`` on a Windows
console under ``cp1252`` -- which is the *same defect* a terminal audit had found
in this project's own renderer days earlier. So: **ASCII only in the output**,
and stdout is reconfigured to UTF-8 with ``errors="replace"`` as a second line of
defence. A status line that raises on one machine's code page is worse than no
status line, because it takes the whole render down with it.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

#: Seconds any single git call may take before its field is simply dropped.
#: Small on purpose: a stale status line is a nuisance, a blocking one is a bug.
_TIMEOUT = 2.0

#: The branch everything is cut from and merged back into.
_TRUNK = "main"

_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"
_CYAN = "\x1b[36m"


def _git(cwd: Path, *args: str) -> str | None:
    """One git call, or ``None`` if it fails for any reason at all.

    Returning ``None`` rather than raising is the whole contract: a status line
    that dies on a detached HEAD, a missing git, or a directory that is not a
    checkout tells you nothing at exactly the moment you needed it to.
    """
    try:
        done = subprocess.run(
            ("git", *args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def _workspace() -> Path:
    """Where Claude Code says we are, falling back to where this process is.

    The fallback is not defensive padding -- it is what makes the script usable
    from a plain shell for debugging, which is how you find out why a field is
    missing.
    """
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    if raw:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            workspace = payload.get("workspace")
            if isinstance(workspace, dict):
                for key in ("current_dir", "cwd", "project_dir"):
                    value = workspace.get(key)
                    if isinstance(value, str) and value:
                        return Path(value)
            for key in ("cwd", "current_dir"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return Path(value)
    return Path(os.getcwd())


def _tree_name(cwd: Path) -> str:
    """The worktree's directory name, which is how humans here refer to them.

    ``mk-main``, ``mk-c14b-fix``, ``mk-schema``. The branch name is often the
    same and often is not -- reviewers run detached on purpose -- so both are
    worth showing.
    """
    top = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(top).name if top else cwd.name


def _branch(cwd: Path) -> str:
    """The branch, or a short sha when detached.

    Detached is normal here rather than exceptional: reviewers are placed
    detached so several can sit on one commit without fighting over a branch,
    so this labels it plainly rather than treating it as an error state.
    """
    name = _git(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    if name:
        return name
    sha = _git(cwd, "rev-parse", "--short", "HEAD")
    return f"detached@{sha}" if sha else "?"


def _dirty(cwd: Path) -> int | None:
    """How many paths differ from HEAD, staged or not."""
    out = _git(cwd, "status", "--porcelain")
    if out is None:
        return None
    return len([line for line in out.split("\n") if line.strip()])


def _behind(cwd: Path, branch: str) -> int | None:
    """Commits on ``main`` that this checkout does not have.

    Deliberately measured against the local ``main`` rather than a remote: on
    this project ``main`` lives in a sibling worktree and moves several times an
    hour, while the remote is pushed rarely. The number that predicts a painful
    merge is the local one.
    """
    if branch == _TRUNK:
        return None
    counts = _git(cwd, "rev-list", "--left-right", "--count", f"{_TRUNK}...HEAD")
    if not counts:
        return None
    left, _, _right = counts.partition("\t")
    try:
        return int(left.strip())
    except ValueError:
        return None


def _cached_gate(cwd: Path) -> str | None:
    """The last recorded gate result, if anything wrote one.

    Optional by design. Nothing in the repo writes this today, and the status
    line must not imply a gate ran when none did -- an absence must not render
    as a measurement, which is this project's own central rule pointed at its
    own tooling. If you want the field, have your gate runner write
    ``.claude/last-gate.json`` as ``{"ok": true, "tests": 2252, "at": "<sha>"}``
    and it appears; until then it is simply absent.

    The ``at`` sha is what makes it honest: a cached result with no commit
    attached cannot be told from a stale one.
    """
    path = cwd / ".claude" / "last-gate.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    head = _git(cwd, "rev-parse", "--short", "HEAD")
    at = str(payload.get("at", ""))[: len(head or "")] if head else ""
    fresh = bool(head) and at == head
    tests = payload.get("tests")
    ok = payload.get("ok")
    mark = f"{_GREEN}gates ok{_RESET}" if ok else f"{_RED}gates red{_RESET}"
    count = f" {tests} tests" if isinstance(tests, int) else ""
    # A result measured at another commit is reported as stale rather than
    # silently shown as current. Same rule as everything else here.
    return f"{mark}{count}" if fresh else f"{_DIM}(stale) {mark}{count}{_RESET}"


def main() -> None:
    cwd = _workspace()
    if not cwd.is_dir():
        cwd = Path(os.getcwd())

    parts: list[str] = []
    tree = _tree_name(cwd)
    branch = _branch(cwd)

    # The worktree first, because "which checkout am I in" is the question this
    # line exists to answer and the eye reads left first.
    parts.append(f"{_CYAN}{tree}{_RESET}")
    if branch != tree:
        parts.append(f"{_DIM}on{_RESET} {branch}")

    dirty = _dirty(cwd)
    if dirty is not None:
        parts.append(
            f"{_GREEN}clean{_RESET}" if dirty == 0 else f"{_YELLOW}{dirty} changed{_RESET}"
        )

    behind = _behind(cwd, branch)
    if behind:
        # Only when non-zero: a branch level with main is the normal case and
        # printing "0 behind" every render trains the eye to skip the field.
        colour = _RED if behind >= 10 else _YELLOW
        parts.append(f"{colour}{behind} behind {_TRUNK}{_RESET}")

    gate = _cached_gate(cwd)
    if gate:
        parts.append(gate)

    sys.stdout.write(" | ".join(parts))


if __name__ == "__main__":
    # Belt and braces against a console code page that cannot encode what we
    # print. The output above is ASCII by construction; this catches anything a
    # branch name or directory name drags in -- a branch with an accented
    # character would otherwise take down the render on cp1252.
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
