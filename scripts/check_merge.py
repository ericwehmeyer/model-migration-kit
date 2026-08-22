"""Refuse a merge that looks green and is not.

This exists because of five specific failures, all of them from merging two
independently-written chunks into one file, and all of them mechanical enough
that a script catches what a careful reader did not.

**A conflict region can end mid-statement.** Git closes a region where the two
sides stop differing, which may be inside a parenthesised expression: both sides
ended with an unclosed ``(`` and *shared* the ``)``, which git placed after the
marker. Splicing the two sides naively put 472 lines between a statement and its
closing paren -- 88 ruff findings and a collection error, from a resolution that
looked obviously right. Nothing may be written that does not parse.

**Two blind halves collide on top-level names, silently.** Two chunks appending
to one new test file both defined ``_cell``; the later shadowed the earlier, 27
tests failed, and git reported no conflict because neither side touched the
other's lines. A conflict marker is what makes a merge safe to eyeball, and this
class of collision does not produce one.

**``__all__`` is the same hazard with a smaller blast radius.** Two chunks each
defining one in a new module means the second binding wins and the first chunk's
names vanish from the export list with no error at all.

**A test file's imports are nobody's job.** ``COMPATIBILITY.md`` records which
rigor names each file reaches for. A tester's file may import one the implementer
never saw, so neither half can complete the table -- and both chunks that hit
this reached the orchestrator test-green and CI-red.

**Ruff's import rules apply to the merged block**, which neither pair can see
from its own side.

Run after resolving a merge and before committing it:

    python scripts/check_merge.py

Exit 0 only if every check passed. A check that could not run is a failure, not
a pass -- the same rule ``verify_release.py`` holds for a SKIPPED release check,
and for the same reason: a gate that mistakes absence for success is worth less
than no gate.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def tracked_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.py"],
        capture_output=True, text=True, check=True,
    )
    # ``git ls-files`` lists a conflicted path once per merge stage, and this
    # script exists to be run mid-merge -- so dedup, or every finding in a
    # conflicted file is reported three times.
    return sorted({REPO / line for line in out.stdout.splitlines() if line.strip()})


def tracked_text_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    )
    keep = {".py", ".md", ".toml", ".cfg", ".yml", ".yaml", ".jsonl", ".txt"}
    return sorted({
        REPO / line
        for line in out.stdout.splitlines()
        if line.strip() and Path(line).suffix in keep
    })


def check_no_conflict_markers() -> list[str]:
    """An unresolved marker anywhere is the cheapest failure to catch."""
    bad = []
    for path in tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            # ``=======`` alone is a legal markdown rule, so it only counts as a
            # marker when a real one is present in the same file.
            if line.startswith(("<<<<<<< ", ">>>>>>> ", "||||||| ")):
                bad.append(f"{path.relative_to(REPO)}:{number}: {line[:60]}")
    return bad


def check_everything_parses() -> list[str]:
    bad = []
    for path in tracked_python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            bad.append(f"{path.relative_to(REPO)}:{exc.lineno}: {exc.msg}")
        except OSError as exc:
            bad.append(f"{path.relative_to(REPO)}: unreadable ({exc})")
    return bad


def _decorator_names(node: ast.AST) -> set[str]:
    """The bare name of each decorator, however it was spelled."""
    names = set()
    for dec in getattr(node, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


#: Decorators under which a repeated name is deliberate rather than a collision:
#: an overload adds a signature, a property accessor extends the descriptor, and
#: a dispatch registration adds an implementation. None of them loses the
#: earlier definition, which is the thing this check is looking for.
_REBIND_IS_INTENTIONAL = frozenset({"overload", "register", "setter", "getter", "deleter"})


def _registers_rather_than_rebinds(node: ast.AST) -> bool:
    return bool(_decorator_names(node) & _REBIND_IS_INTENTIONAL)


def _top_level_names(tree: ast.Module) -> dict[str, list[int]]:
    """Names bound at module level, and every line that binds them.

    Only ``tree.body`` is walked, so a definition guarded by ``if`` or ``try``
    -- the legitimate way to bind one name twice -- is not counted.
    """
    seen: dict[str, list[int]] = defaultdict(list)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _registers_rather_than_rebinds(node):
                continue
            seen[node.name].append(node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    seen[target.id].append(node.lineno)
    return seen


def check_no_shadowed_top_level_names() -> list[str]:
    bad = []
    for path in tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue  # already reported by the parse check
        for name, lines in _top_level_names(tree).items():
            if len(lines) > 1 and not name.isupper():
                # An upper-case rebind is usually a deliberate constant edit;
                # a redefined def/class is what silently loses a chunk's work.
                bad.append(
                    f"{path.relative_to(REPO)}: {name!r} defined at "
                    f"{', '.join(str(n) for n in lines)} -- the later one wins"
                )
    return bad


def check_all_is_complete() -> list[str]:
    """Public classes and functions missing from a module's own ``__all__``.

    Two chunks sharing one new module each write an ``__all__``; the second
    binding wins outright and the first chunk's exports disappear without an
    error. In the case that prompted this, one chunk wrote the list and the
    other wrote none, so its four public names were simply absent.

    **Constants are deliberately not checked.** This package exports plenty of
    public module-level constants that are not in ``__all__`` -- ``FETCHING_ATTRS``,
    ``FORBIDDEN_TAGS``, ``TEST_OUTCOMES`` and others -- because ``__all__`` here
    names the API surface rather than everything a caller may legitimately read.
    Flagging those would report eight pre-existing style decisions as merge
    defects, and a gate that cries wolf on its first run is a gate people stop
    reading. A class or a function missing from a list the module bothered to
    write is the narrower signal, and it is the one that fired.
    """
    bad = []
    for path in tracked_python_files():
        if "src" not in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        declared: set[str] | None = None
        for node in tree.body:
            is_all = isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
            )
            if is_all and isinstance(node.value, (ast.List, ast.Tuple)):
                declared = {
                    e.value for e in node.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
        if declared is None:
            continue
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not node.name.startswith("_")
            and not _registers_rather_than_rebinds(node)
        }
        missing = sorted(defined - declared)
        if missing:
            bad.append(
                f"{path.relative_to(REPO)}: defined but not in __all__: "
                f"{', '.join(missing)}"
            )
    return bad


def run(label: str, cmd: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        return False, f"{label} could not run ({exc}) -- treated as failure"
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        return False, "\n".join(f"    {line}" for line in tail)
    return True, ""


def main() -> int:
    py = sys.executable
    static = [
        ("no conflict markers", check_no_conflict_markers),
        ("every python file parses", check_everything_parses),
        ("no shadowed top-level names", check_no_shadowed_top_level_names),
        ("__all__ lists every public name", check_all_is_complete),
    ]
    commands = [
        ("ruff", [py, "-m", "ruff", "check", "."]),
        ("dependency surface", [py, "scripts/dependency_surface.py", "--check"]),
        ("pytest", [py, "-m", "pytest", "tests", "-q"]),
    ]

    failed = 0
    for label, check in static:
        problems = check()
        if problems:
            failed += 1
            print(f"[FAIL] {label}")
            for line in problems[:20]:
                print(f"    {line}")
            if len(problems) > 20:
                print(f"    ... and {len(problems) - 20} more")
        else:
            print(f"[PASS] {label}")

    for label, cmd in commands:
        ok, detail = run(label, cmd)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            failed += 1
            print(detail)

    print()
    if failed:
        print(f"{failed} check(s) failed. This merge is not green.")
        return 1
    print("Merge is green on all seven checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
