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

**A green pytest check can mean no test ran at all.** This one was found in the
gate rather than in a merge. The three command checks below decided pass/fail on
the child's exit status and nothing else, and ``subprocess.run`` hands the child
``os.environ`` -- so a ``PYTEST_ADDOPTS="--co -q"`` left in a shell, a variable
this project's own docs recommend setting for ``-n 8``, made pytest collect the
suite, run none of it, and exit 0. Measured on a tree carrying a committed
``assert 1 == 2``: seven of seven ``[PASS]``, exit 0, in 16.4 seconds against an
honest 4m36s. A gate that cannot tell "everything passed" from "nothing ran" is
not a gate, so the pytest check now reads pytest's own JUnit report and holds the
count against a floor, and no command check inherits a variable that redefines
what it is running.

Run after resolving a merge and before committing it:

    python scripts/check_merge.py          # honest, serial, ~4 minutes
    python scripts/check_merge.py -n 8     # the same run under pytest-xdist

Exit 0 only if every check passed. A check that could not run is a failure, not
a pass -- the same rule ``verify_release.py`` holds for a SKIPPED release check,
and for the same reason: a gate that mistakes absence for success is worth less
than no gate.
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The fewest tests a run of the suite may report and still be believed. This is
#: a floor, not a census: it is deliberately well under the true count (2330
#: executed at the commit that set it) so that adding and removing tests never
#: touches it, and well over the handful any narrowed selection would leave, so
#: that a run reporting three tests cannot be mistaken for a run of the suite. It
#: is checked against the number pytest itself reports, so moving it is a visible
#: edit to a tracked file rather than an invisible variable in somebody's shell.
MINIMUM_TESTS = 2000

#: Environment variables that change what a child command *is* before it reads a
#: single argument of ours. ``PYTEST_ADDOPTS`` is prepended to pytest's command
#: line, so it can add ``--co`` (collect, run nothing, exit 0), narrow the
#: selection with ``-k``, cut the run short with ``--maxfail``, or point
#: ``--junitxml`` somewhere we will not look. ``PYTEST_PLUGINS`` and
#: ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` change which code is loaded at startup.
#: None of them may reach a gate's child: what the gate runs must be a function
#: of this repository, not of the shell the gate was launched from.
#:
#: ``PYTHONPATH`` is deliberately **not** in this list. It is load-bearing here --
#: ``conftest.py`` sets it so that child processes import the checkout under test
#: rather than the editable install's -- and stripping it would make the gate
#: measure a different worktree's code, which is the exact failure this repository
#: has paid for most often.
ENV_ESCAPES = frozenset({
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_CURRENT_TEST",
})


def child_env() -> dict[str, str]:
    """The environment a gate's child gets: this one, minus the escapes."""
    return {k: v for k, v in os.environ.items() if k not in ENV_ESCAPES}


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
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            # ``NAME: Final = ...`` binds exactly as ``NAME = ...`` does and was
            # invisible here. Measured across the tracked tree when this was
            # added: zero new reports, so it costs nothing and closes a hole a
            # type annotation would otherwise have opened.
            seen[node.target.id].append(node.lineno)
    return seen


def check_no_shadowed_top_level_names() -> list[str]:
    bad = []
    for path in tracked_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue  # already reported by the parse check
        for name, lines in _top_level_names(tree).items():
            if len(lines) > 1:
                # This used to skip UPPER_CASE names, on the premise that an
                # upper-case rebind is usually a deliberate constant edit. C22b's
                # merge falsified it: two branches independently defined a
                # module-level ``THIRD_MODEL`` in tests/test_report.py, the later
                # won for every reference in the file, and this check said PASS.
                # It was caught only because one of C10's tests happened to
                # assert an ordering over the constant it had lost.
                #
                # A deliberate constant edit rebinds a constant in *one* branch's
                # working copy; it does not leave two module-level assignments
                # standing. Measured before removing the exclusion: **zero**
                # upper-case module-level rebinds across the whole tracked tree,
                # so the premise cost a real catch and bought nothing.
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


def run(label: str, cmd: list[str], cwd: Path | str | None = None) -> tuple[bool, str]:
    """Run one command in a sanitised environment; exit status decides pass/fail.

    Exit status is a *necessary* condition and, for pytest, not a sufficient one --
    see ``check_pytest``. It is all there is for ``ruff`` and ``dependency_surface``:
    the four environment variables ruff reads (``RUFF_OUTPUT_FORMAT``,
    ``RUFF_OUTPUT_FILE``, ``RUFF_NO_CACHE``, ``RUFF_CACHE_DIR``) change where and how
    findings are printed and none of them changes whether ruff exits non-zero, and
    ``dependency_surface.py`` reads no environment at all. Both were checked rather
    than assumed, and both go through this function so that if either grows an
    ``ADDOPTS``-shaped variable later, the refusal is already in place.

    ``cwd`` defaults to this repository, which is the only value the gate itself ever
    passes; it is a parameter so that the gate's own tests can point a check at a
    synthetic tree without that tree inheriting this one's pytest configuration.
    """
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd or REPO), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=child_env(),
        )
    except OSError as exc:
        return False, f"{label} could not run ({exc}) -- treated as failure"
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        return False, "\n".join(f"    {line}" for line in tail)
    return True, ""


def junit_totals(report: Path) -> dict[str, int] | None:
    """Pytest's own counts from its JUnit report, or ``None`` if there are none.

    ``None`` means the report was not written, could not be parsed, or contained no
    ``testsuite`` element -- three different absences, all of which mean the same
    thing here: this run cannot say how many tests it ran. It is never a zero. A
    count of zero is a measurement (pytest ran and reported that nothing executed);
    an unwritten report is not, and the two must not arrive at the caller wearing
    the same face.

    Parsing the report rather than pytest's ``-q`` summary line is the point. That
    line is prose -- it changes shape between "2330 passed", "1 failed, 2329 passed"
    and "no tests ran", and disappears entirely at ``-q -q`` -- whereas the report
    is a channel pytest maintains for machines and fills in even when the run
    collected nothing.
    """
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError):
        return None
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    found = False
    for suite in root.iter("testsuite"):
        found = True
        for key in totals:
            totals[key] += int(suite.get(key) or 0)
    return totals if found else None


def check_pytest(
    py: str,
    target: str = "tests",
    minimum: int = MINIMUM_TESTS,
    jobs: str | None = None,
    cwd: Path | str | None = None,
) -> tuple[bool, str, int | None]:
    """Run the suite and refuse to call it green unless it can say what it ran.

    Three things must hold, and the second and third are the new ones: pytest exits
    zero, pytest's report says at least ``minimum`` tests actually executed, and
    that report records no failure or error. The last is redundant with the exit
    status today and costs nothing; it is there because the whole defect was a gate
    that had exactly one source of truth.

    Returns the count as well, because "how many" is what the caller is being asked
    to believe -- and ``None`` when the run could not say, so that main() can print
    an absence as an absence.
    """
    with tempfile.TemporaryDirectory(prefix="merge-gate-") as tmp:
        report = Path(tmp) / "pytest.xml"
        # ``--junitxml`` is given on the command line, which pytest applies *after*
        # both its config's ``addopts`` and the environment's, so a decoy path in
        # either loses to this one. Verified against a config carrying
        # ``addopts = --junitxml=decoy.xml``: the gate still read its own report.
        cmd = [py, "-m", "pytest", target, "-q", f"--junitxml={report}"]
        if jobs:
            # The only route a worker count has now that PYTEST_ADDOPTS is refused.
            cmd += ["-n", jobs]
        ok, detail = run("pytest", cmd, cwd=cwd)
        totals = junit_totals(report)

    if totals is None:
        note = (
            "    pytest wrote no readable report, so this run cannot say how many\n"
            "    tests it ran -- which is a failure, not a pass."
        )
        return False, "\n".join(part for part in (detail, note) if part), None

    ran = totals["tests"] - totals["skipped"]
    problems = []
    if ran < minimum:
        problems.append(
            f"    {ran} test(s) executed; at least {minimum} were expected. A run\n"
            f"    that skipped the suite is not a run of the suite."
        )
    if totals["failures"] or totals["errors"]:
        problems.append(
            f"    pytest recorded {totals['failures']} failure(s) and "
            f"{totals['errors']} error(s)."
        )
    if not ok and not problems:
        problems.append("    pytest exited non-zero.")
    if problems:
        return False, "\n".join(part for part in (detail, *problems) if part), ran
    return ok, detail, ran


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse a merge that looks green and is not.",
    )
    parser.add_argument(
        "-n", "--jobs", metavar="N", default=None,
        help=(
            "pytest-xdist worker count (4, 8, auto). The gate refuses to inherit "
            "PYTEST_ADDOPTS, so this is how a worker count reaches the suite."
        ),
    )
    args = parser.parse_args(argv)

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

    ok, detail, ran = check_pytest(py, jobs=args.jobs)
    # An absence must not render as a measurement: a run that could not say how
    # many tests it ran says so, rather than printing a zero it did not measure.
    counted = f"{ran} tests ran" if ran is not None else "test count unavailable"
    print(f"[{'PASS' if ok else 'FAIL'}] pytest -- {counted}")
    if not ok:
        failed += 1
        print(detail)

    print()
    if failed:
        print(f"{failed} check(s) failed. This merge is not green.")
        return 1
    print(f"Merge is green on all seven checks ({counted}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
