"""Tests for scripts/check_merge.py -- the gate, checked by something other than itself.

`test_release_checks.py` exists because the script that decides whether a release may
happen needs its logic checked from outside. This file is the same argument applied to
the script that decides whether a *merge* may happen, and it was written because that
script had the failure it was built to catch.

The gate ran its three command checks through one `subprocess.run` and read one signal
from them, the exit status. `subprocess.run` hands a child `os.environ`, and pytest
prepends `PYTEST_ADDOPTS` to its own command line -- so a `--co` sitting in a shell,
left over from someone checking a collection error, made pytest collect the suite, run
none of it, and exit 0. Measured over a tree carrying a committed `assert 1 == 2`:
seven of seven `[PASS]`, exit 0, 16.4 seconds against an honest 4m36s.

Every test below drives the real functions from `check_merge.py` over a synthetic
suite of three tests in a temporary directory, so a run costs a second or so rather
than the four minutes the true suite costs. The synthetic suite is what makes the
count assertions legible: `minimum=3` here plays the part `MINIMUM_TESTS` plays there.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_merge.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_merge", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cm = _load_module()


def _suite(directory: Path, bodies: dict[str, str]) -> str:
    """Write a tiny test module and return the path to hand the gate as its target."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, body in bodies.items():
        lines.append(f"def {name}():\n    {body}\n")
    (directory / "test_synthetic.py").write_text("\n".join(lines), encoding="utf-8")
    return str(directory)


def _gate(target: str, minimum: int = 3):
    """Drive the gate's pytest check over a synthetic suite, rooted in that suite.

    `cwd` is the suite rather than the repository so the child's rootdir is the
    temporary tree: it keeps this repository's pytest configuration out of the
    fixture, and it is worth two seconds a call, which over the eight real pytest
    children below is most of what this file costs the suite it belongs to.
    """
    return cm.check_pytest(sys.executable, target=target, minimum=minimum, cwd=target)


@pytest.fixture(scope="module")
def passing_suite(tmp_path_factory) -> str:
    return _suite(
        tmp_path_factory.mktemp("green"),
        {
            "test_one": "assert True",
            "test_two": "assert 1 + 1 == 2",
            "test_three": "assert 'a' in 'abc'",
        },
    )


@pytest.fixture(scope="module")
def failing_suite(tmp_path_factory) -> str:
    return _suite(
        tmp_path_factory.mktemp("red"),
        {
            "test_one": "assert True",
            "test_two": "assert 1 == 2",
            "test_three": "assert True",
        },
    )


# ----------------------------------------------------------------------------------
# The controls. A gate that fails everything is as useless as one that passes
# everything, and these two are what stop the rest of the file being satisfiable by
# `return False`.
# ----------------------------------------------------------------------------------


def test_an_honest_green_suite_passes_and_reports_its_count(passing_suite):
    ok, detail, ran = _gate(passing_suite)
    assert ok, detail
    assert ran == 3


def test_an_honest_red_suite_fails(failing_suite):
    ok, _detail, ran = _gate(failing_suite)
    assert not ok
    # It ran everything; the verdict comes from the failure, not from the count.
    assert ran == 3


# ----------------------------------------------------------------------------------
# The defect: a variable in the shell must not be able to move the verdict, in
# either direction.
# ----------------------------------------------------------------------------------


def test_collect_only_in_the_environment_cannot_report_pass(monkeypatch, failing_suite):
    """The reported defect, at the size it was reported.

    `--co` collects, runs nothing, and exits 0. Against a suite with a failing test in
    it, the old gate printed `[PASS] pytest`.
    """
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co -q")
    ok, _detail, ran = _gate(failing_suite)
    assert not ok
    assert ran == 3, "the escape must be refused, not merely detected"


def test_a_selection_narrowed_to_nothing_does_not_reach_the_child(monkeypatch, passing_suite):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k zzz_matches_no_test_in_this_suite")
    ok, _detail, ran = _gate(passing_suite)
    assert ok, "a selection expression in the shell must not reach the child"
    assert ran == 3


def test_a_selection_narrowed_to_one_test_cannot_report_pass(monkeypatch, failing_suite):
    """`-k` narrowing away from the failure is the quiet version of `--co`.

    It exits 0 with tests genuinely executed, so nothing about the exit status or a
    bare "did anything run?" check catches it. Only the count does.
    """
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k test_one")
    ok, _detail, ran = _gate(failing_suite)
    assert not ok
    assert ran == 3


def test_maxfail_in_the_environment_cannot_cut_the_run_short(monkeypatch, failing_suite):
    monkeypatch.setenv("PYTEST_ADDOPTS", "--maxfail=1 -x")
    ok, _detail, ran = _gate(failing_suite)
    assert not ok
    assert ran == 3, "all three ran; the stop-early flag did not reach the child"


def test_a_plugin_named_in_the_environment_cannot_fail_an_honest_run(
    monkeypatch, passing_suite
):
    """The same defect pointed the other way: a false red is a broken gate too.

    `PYTEST_PLUGINS` is imported at startup, so a stale name in a shell used to take
    the whole suite down with a usage error the merge had nothing to do with.
    """
    monkeypatch.setenv("PYTEST_PLUGINS", "g18_no_such_plugin_anywhere")
    ok, detail, ran = _gate(passing_suite)
    assert ok, detail
    assert ran == 3


def test_the_escapes_are_dropped_and_pythonpath_is_kept(monkeypatch):
    """PYTHONPATH is the one variable that must survive.

    `conftest.py` sets it so child processes import the checkout under test instead of
    the editable install's; a gate that stripped it would run the suite against another
    worktree's code, which is this repository's most expensive recurring failure.
    """
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")
    monkeypatch.setenv("PYTEST_PLUGINS", "whatever")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTHONPATH", "/some/checkout/src")
    env = cm.child_env()
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in env
    assert env["PYTHONPATH"] == "/some/checkout/src"
    assert "PYTHONPATH" not in cm.ENV_ESCAPES


def test_no_command_check_inherits_an_escape(monkeypatch):
    """`run()` is the seam all three command checks share, so the refusal is shared.

    Asked of a real child rather than of a mock, because what is being tested is
    precisely what `subprocess.run` hands a process it starts.
    """
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co")
    monkeypatch.setenv("PYTEST_PLUGINS", "whatever")
    probe = "import os, sys; sys.exit(1 if 'PYTEST_ADDOPTS' in os.environ else 0)"
    ok, detail = cm.run("probe", [sys.executable, "-c", probe])
    assert ok, detail


# ----------------------------------------------------------------------------------
# The count itself: where it comes from, and what happens when it is missing.
# ----------------------------------------------------------------------------------


def test_a_suite_smaller_than_the_floor_is_refused(passing_suite):
    """The floor is what makes the count an assertion rather than a printed number."""
    ok, detail, ran = _gate(passing_suite, minimum=4)
    assert not ok
    assert ran == 3
    assert "4" in detail


def test_the_floor_for_this_repository_is_a_real_floor():
    """A floor of nought or one is not a floor.

    The suite stands at 2330; the constant is checked here so that lowering it to make
    a red gate go green has to argue with a test rather than only with a reader.
    """
    assert cm.MINIMUM_TESTS >= 1000


def test_a_missing_report_is_an_absence_not_a_zero(tmp_path):
    assert cm.junit_totals(tmp_path / "never-written.xml") is None
    unparseable = tmp_path / "junk.xml"
    unparseable.write_text("not xml at all", encoding="utf-8")
    assert cm.junit_totals(unparseable) is None
    empty = tmp_path / "empty.xml"
    empty.write_text('<?xml version="1.0"?><testsuites/>', encoding="utf-8")
    assert cm.junit_totals(empty) is None


def test_a_measured_zero_is_a_measurement(tmp_path):
    """Nothing collected is a number pytest reported; not knowing is not.

    The two arrive at the caller wearing different faces on purpose -- this is the
    house rule about absences, applied to the gate's own reporting.
    """
    collected_nothing = tmp_path / "co.xml"
    collected_nothing.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
        'errors="0" failures="0" skipped="0" tests="0" /></testsuites>',
        encoding="utf-8",
    )
    assert cm.junit_totals(collected_nothing) == {
        "tests": 0, "failures": 0, "errors": 0, "skipped": 0,
    }


def test_a_report_that_records_a_failure_is_refused_whatever_the_exit_status(
    monkeypatch, tmp_path
):
    """Exit status is no longer the only source of truth, and this is what that buys."""
    def _fake_run(label, cmd, cwd=None):
        report = Path(cmd[-1].split("=", 1)[1])
        report.write_text(
            '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest"'
            ' errors="0" failures="1" skipped="0" tests="2500" /></testsuites>',
            encoding="utf-8",
        )
        return True, ""  # a lying zero

    monkeypatch.setattr(cm, "run", _fake_run)
    ok, detail, ran = cm.check_pytest(sys.executable, target=str(tmp_path))
    assert not ok
    assert ran == 2500
    assert "failure" in detail


def test_a_worker_count_still_has_a_route_to_the_suite(monkeypatch, tmp_path):
    """Refusing PYTEST_ADDOPTS took away the documented way to run the gate under xdist.

    `-n` on the gate itself gives it back, without giving back the escape: a worker
    count is a thing the caller may choose, and a selection expression is not.
    """
    seen: list[list[str]] = []

    def _fake_run(label, cmd, cwd=None):
        seen.append(cmd)
        Path(cmd[cmd.index("-n") - 1].split("=", 1)[1]).write_text(
            '<?xml version="1.0"?><testsuites><testsuite name="pytest" errors="0" '
            'failures="0" skipped="0" tests="2500" /></testsuites>',
            encoding="utf-8",
        )
        return True, ""

    monkeypatch.setattr(cm, "run", _fake_run)
    ok, detail, ran = cm.check_pytest(sys.executable, target=str(tmp_path), jobs="8")
    assert ok, detail
    assert ran == 2500
    assert seen[0][-2:] == ["-n", "8"]


def test_the_gate_is_still_seven_checks(monkeypatch, capsys):
    """main() must actually use the counted check -- the fix has to be wired in."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "--co -q")
    monkeypatch.setattr(
        cm, "check_pytest",
        lambda py, jobs=None: (True, "", 1234),
    )
    monkeypatch.setattr(cm, "run", lambda label, cmd: (True, ""))
    assert cm.main([]) == 0
    out = capsys.readouterr().out
    assert "[PASS] pytest -- 1234 tests ran" in out
    assert "seven checks" in out
    assert os.environ["PYTEST_ADDOPTS"] == "--co -q", "the test's own env is untouched"
