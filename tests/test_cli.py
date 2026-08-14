"""Acceptance tests for :mod:`model_migration_kit.cli` and :mod:`model_migration_kit.demo`.

Written from the frozen contract, never from the modules: ``docs/session-3-contract.md``
§§3 and 5 (the command surface, the exit-code table, the exception map, the stream
split, and what ``migkit demo`` must be), §6 items 23-36 (the test inventory, each
phrased there as the assertion to write), ``docs/build-plan.md`` §§1, 5 and 6
(the CI-contract exit codes and the definition of done), and ``PROGRESS.md``
invariants 3 and 7. The author of this file did not write ``cli.py`` or
``demo.py`` and derived no expected value by running either.

**Where every expectation comes from.**

* The four exit codes are read out of ``contracts.Verdict.EXIT_CODES``, which is
  the frozen CI contract, and are *also* written out longhand in
  :data:`FROZEN_EXIT_CODES` so that a change to the source of truth cannot quietly
  change what this suite considers correct. One test compares the two.
* The demo's verdict is ``NO-GO`` because the contract says so twice -- session-3
  §5.2 ("the verdict is ``NO-GO`` at the bundled n") and the definition of done
  ("an HTML report that shows a NO-GO verdict"). It is asserted, not observed. If
  the implementation disagrees, the implementation is wrong, not this line.
* The bundled golden set's size and tag distribution are counted by hand from
  ``src/model_migration_kit/data/demo_goldenset.jsonl``: twelve items, four tagged
  ``arithmetic``, four ``extraction``, four ``refusal``, and two ``multi-value``
  (``extract-04`` and ``refuse-04``). Session-3 §5.1 independently states the
  twelve and the three slices.
* The verdict line's exact shape, ``VERDICT: <X> (exit <n>)``, is session-3 §2.7.
* That ``AdapterError`` and ``SampleTimeout`` do not inherit ``RigorError`` is
  session-3 §0, and :meth:`TestErrorMapping.test_adapter_error_and_sample_timeout_
  are_not_rigor_errors` re-verifies it against the installed package rather than
  trusting the document -- a handler that assumes otherwise lets a provider
  failure escape as a traceback, which is the whole reason §3.3 names them.

**Everything here is offline, keyless and free of RNG.** The evidence fixture is
built by driving the Session 1 and 2 modules over scripted ``FakeAdapter``s; those
modules are not under test here and no expectation is taken from what they
produce. The verdict record is then rewritten by hand to each of the three
verdicts, so the exit code a test expects comes from the contract table and never
from a statistic.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
from importlib import resources
from pathlib import Path

import pytest
from opik_rigor import (
    AdapterError,
    EvidenceError,
    EvidenceLog,
    FakeAdapter,
    JudgeOutputError,
    ModelPinError,
    PassRateError,
    RegressionError,
    RigorError,
    RubricDriftError,
    SampleTimeout,
)

from model_migration_kit import cli, demo
from model_migration_kit.comparison import compare
from model_migration_kit.contracts import EVENT_COMPARISON, EVENT_VERDICT, Verdict
from model_migration_kit.errors import (
    ArtifactError,
    ConfigError,
    GoldenSetError,
    JudgeConfigError,
    JudgeReliabilityError,
    ReportError,
)
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, judge_artifact
from model_migration_kit.runner import run_goldenset

# --------------------------------------------------------------------------- #
# Expectations, all from outside the implementation
# --------------------------------------------------------------------------- #

#: build-plan §1 ("Exit codes: 0 GO, 1 NO-GO, 2 REVIEW, 3 error -- documented as
#: the CI contract"), PROGRESS.md invariant 7, session-3 §3.2. Written longhand so
#: this suite has its own copy of the contract and does not merely agree with
#: whatever ``contracts.py`` currently says.
FROZEN_EXIT_CODES = {"GO": 0, "NO-GO": 1, "REVIEW": 2, "ERROR": 3}

#: session-3 §5.2 and build-plan §5. The demo exists to show a refused migration.
DEMO_VERDICT = "NO-GO"

#: Hand-counted from ``src/model_migration_kit/data/demo_goldenset.jsonl``.
DEMO_ITEM_IDS = (
    "arith-01",
    "arith-02",
    "arith-03",
    "arith-04",
    "extract-01",
    "extract-02",
    "extract-03",
    "extract-04",
    "refuse-01",
    "refuse-02",
    "refuse-03",
    "refuse-04",
)
DEMO_TAG_COUNTS = {"arithmetic": 4, "extraction": 4, "multi-value": 2, "refusal": 4}

#: session-3 §5.2: the CI job's ``timeout 120`` is the outer bound and "the suite
#: asserts a much tighter one". Sixty seconds is that tighter bound with a
#: generous margin over 120 in-process completions and 120 in-process judge calls,
#: none of which sleep, touch the network, or read a credential.
DEMO_BUDGET_SECONDS = 60.0

#: session-3 §2.7. The last line of stdout, always, including under ``--quiet``.
def verdict_line(verdict: str) -> str:
    return f"VERDICT: {verdict} (exit {FROZEN_EXIT_CODES[verdict]})"


#: session-3 §3.3. Every one of these must be caught at the ``main`` boundary and
#: turned into exit 3 with a one-line stderr message and no traceback. The two
#: that are the point of the list are ``AdapterError`` and ``SampleTimeout``: they
#: subclass ``Exception`` directly, so ``except RigorError`` misses them.
MAPPED_ERRORS = (
    GoldenSetError("golden set line 4: duplicate id 'arith-01'"),
    ArtifactError("no run artifact at ./.migkit/candidate.jsonl"),
    JudgeConfigError("the two sides were judged by different panels"),
    JudgeReliabilityError("accuracy", 7, 60, 0.05),
    ConfigError("threshold 'alpha' must be in (0.0, 1.0), got 1.5"),
    ReportError("this evidence log records no comparison"),
    EvidenceError("malformed evidence at line 2 of ./.migkit/evidence.jsonl"),
    ModelPinError("model id 'gpt-4o-latest' is not pinned"),
    JudgeOutputError("judge did not return an object", "{oops"),
    RubricDriftError("accuracy", "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"),
    AdapterError("the provider refused the connection"),
    SampleTimeout("a draw exceeded its 60s budget"),
    RegressionError("candidate regressed against baseline"),
    PassRateError("candidate did not clear the pass-rate floor"),
    OSError("the disk is full"),
    ValueError("--n must be an integer >= 1, got 0"),
)


def _error_id(exc: BaseException) -> str:
    return type(exc).__name__


# --------------------------------------------------------------------------- #
# Fixtures: a real evidence log, built without consulting the code under test
# --------------------------------------------------------------------------- #

_JUDGE_PASS = '{"pass": true, "score": 5, "reason": "answered correctly"}'


def _always(text: str):
    """A prompt-insensitive script for a FakeAdapter, so nothing depends on order."""

    def respond(_prompt: str) -> str:
        return text

    return respond


@pytest.fixture(scope="module")
def evidence_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A complete, honest evidence log with both artifacts on disk beside it.

    Built by running the Session 1/2 pipeline over scripted fakes. Nothing about
    the verdict it happens to reach is used: every test that cares rewrites the
    verdict record first. What this fixture supplies is *shape* -- a comparison
    payload whose recorded artifact paths resolve -- which is exactly what a
    hand-written stub cannot supply and what the report reads under invariant 2.
    """
    work = tmp_path_factory.mktemp("evidence")
    goldenset_path = work / "goldenset.jsonl"
    goldenset_path.write_text(
        "\n".join(
            json.dumps({"id": f"item-{index:02d}", "input": f"question {index}", "tags": ["demo"]})
            for index in range(4)
        )
        + "\n",
        encoding="utf-8",
    )
    rubric = work / "rubric.md"
    rubric.write_text("# Rubric\n\nScore 1-5. A response passes at 4 or 5.\n", encoding="utf-8")
    config = work / "migkit.toml"
    config.write_text(
        '[[judge]]\nname = "accuracy"\nmodel = "fake-judge-v1"\nrubric = "rubric.md"\n\n'
        "[thresholds]\npass_rate_floor = 0.90\n",
        encoding="utf-8",
    )

    goldenset = GoldenSet.load(goldenset_path)
    evidence = EvidenceLog(work / "evidence.jsonl")
    judge_config = JudgeConfig.load(config)
    panel = judge_config.build(
        evidence,
        lambda spec: FakeAdapter(model_id=spec.model, responses=_always(_JUDGE_PASS)),
    )

    judged = []
    for model_id in ("fake-baseline-v1", "fake-candidate-v1"):
        artifact = run_goldenset(
            goldenset,
            FakeAdapter(model_id=model_id, responses=_always("42")),
            out_dir=work,
            n=2,
            evidence=evidence,
        )
        judged.append(judge_artifact(artifact, goldenset, panel, evidence=evidence, out_dir=work))

    compare(
        judged[0],
        judged[1],
        thresholds=judge_config.thresholds,
        evidence=evidence,
        goldenset_path=str(goldenset_path),
        config_path=str(config),
    )
    return work / "evidence.jsonl"


def _rewrite_verdict(source: Path, destination: Path, verdict: str | None) -> Path:
    """Copy an evidence log, forcing (or dropping) its ``migkit.verdict`` record.

    ``verdict=None`` drops the record entirely, which is session-3 §2.6's
    "comparison present, verdict record missing" shape -- the run killed between
    the two writes.
    """
    lines = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event_type") == EVENT_VERDICT:
            if verdict is None:
                continue
            record["payload"]["verdict"] = verdict
            record["payload"]["exit_code"] = Verdict.exit_code(verdict)
            line = json.dumps(record)
        lines.append(line)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return destination


def _drop_comparison(source: Path, destination: Path) -> Path:
    """Everything but the comparison and verdict records: nothing to report on."""
    lines = [
        line
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and json.loads(line).get("event_type") not in (EVENT_COMPARISON, EVENT_VERDICT)
    ]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return destination


def _last_line(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    assert lines, "expected output, got nothing"
    return lines[-1].strip()


def _stderr_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# The exit-code contract
# --------------------------------------------------------------------------- #


class TestExitCodeContract:
    """0 GO, 1 NO-GO, 2 REVIEW, 3 error -- pinned through the real entry point.

    build-plan §1 calls this the CI contract and PROGRESS.md makes it invariant 7:
    changing it is a breaking change to every pipeline that consumes the tool.
    """

    def test_frozen_table_matches_this_suites_own_copy(self) -> None:
        assert dict(Verdict.EXIT_CODES) == FROZEN_EXIT_CODES

    def test_error_is_the_default_for_an_unknown_verdict(self) -> None:
        # session-3 §3.2: "a verdict the tool cannot interpret is a tool error,
        # and mapping it to GO would be the one failure mode that ships a bad model".
        assert Verdict.exit_code("SHIP-IT") == FROZEN_EXIT_CODES["ERROR"]

    @pytest.mark.parametrize("verdict", ["GO", "NO-GO", "REVIEW"])
    def test_report_returns_the_recorded_verdicts_code(
        self, verdict: str, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        log = _rewrite_verdict(evidence_fixture, tmp_path / f"{verdict}.jsonl", verdict)
        assert cli.main(["report", str(log)]) == FROZEN_EXIT_CODES[verdict]

    @pytest.mark.parametrize("verdict", ["GO", "NO-GO", "REVIEW"])
    def test_the_verdict_line_agrees_with_the_returned_code(
        self,
        verdict: str,
        evidence_fixture: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # session-3 §2.7: a CI log that scrolls past 200 lines of table still ends
        # with the finding, and the finding names the code the caller received.
        log = _rewrite_verdict(evidence_fixture, tmp_path / f"line-{verdict}.jsonl", verdict)
        code = cli.main(["report", str(log)])
        assert _last_line(capsys.readouterr().out) == verdict_line(verdict)
        assert code == FROZEN_EXIT_CODES[verdict]

    def test_a_report_with_no_verdict_record_returns_error(
        self, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        # session-3 §2.6 and §3.2: "a partial report is evidence, never a
        # decision". Every table still renders; the code is 3, not 0.
        log = _rewrite_verdict(evidence_fixture, tmp_path / "noverdict.jsonl", None)
        assert cli.main(["report", str(log)]) == FROZEN_EXIT_CODES["ERROR"]

    def test_an_unrecognised_verdict_string_returns_error(
        self, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        log = _rewrite_verdict(evidence_fixture, tmp_path / "bogus.jsonl", "PROBABLY-FINE")
        assert cli.main(["report", str(log)]) == FROZEN_EXIT_CODES["ERROR"]

    def test_an_evidence_log_with_no_comparison_returns_error(
        self, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        # session-3 §2.6: the one refusal. There is nothing to report *on*.
        log = _drop_comparison(evidence_fixture, tmp_path / "nocomparison.jsonl")
        assert cli.main(["report", str(log)]) == FROZEN_EXIT_CODES["ERROR"]

    def test_a_missing_evidence_path_returns_error_rather_than_an_empty_report(
        self, tmp_path: Path
    ) -> None:
        # session-3 §0: rigor returns [] for a missing log, so a typo'd path would
        # otherwise render as a blank "nothing happened" report.
        assert cli.main(["report", str(tmp_path / "absent.jsonl")]) == FROZEN_EXIT_CODES["ERROR"]

    def test_every_returned_value_is_one_of_the_four(
        self, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        # session-3 §6 item 24, the behavioural half.
        argvs = [
            ["report", str(_rewrite_verdict(evidence_fixture, tmp_path / "a.jsonl", "GO"))],
            ["report", str(_rewrite_verdict(evidence_fixture, tmp_path / "b.jsonl", "NO-GO"))],
            ["report", str(_rewrite_verdict(evidence_fixture, tmp_path / "c.jsonl", "REVIEW"))],
            ["report", str(_rewrite_verdict(evidence_fixture, tmp_path / "d.jsonl", None))],
            ["report", str(tmp_path / "never-written.jsonl")],
        ]
        codes = [cli.main(argv) for argv in argvs]
        assert set(codes) <= set(FROZEN_EXIT_CODES.values()), codes

    def test_cli_source_carries_no_integer_exit_literal(self) -> None:
        """session-3 §3.2 and §6 item 24, the source half.

        "No integer literal 0/1/2/3 appears in ``cli.py`` as an exit value -- a
        second copy of the CI contract is a second thing to forget to update."
        Codes must come from ``Verdict.exit_code``.
        """
        source = Path(inspect.getsourcefile(cli) or "").read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Return):
                value = node.value
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, int)
                    and not isinstance(value.value, bool)
                    and value.value in FROZEN_EXIT_CODES.values()
                ):
                    offenders.append(f"line {node.lineno}: return {value.value}")
            if isinstance(node, ast.Call):
                target = node.func
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else getattr(target, "id", "")
                )
                if name == "exit":
                    for arg in node.args:
                        if (
                            isinstance(arg, ast.Constant)
                            and isinstance(arg.value, int)
                            and not isinstance(arg.value, bool)
                        ):
                            offenders.append(f"line {node.lineno}: exit({arg.value})")
        assert not offenders, "exit codes must come from Verdict.exit_code: " + "; ".join(offenders)


# --------------------------------------------------------------------------- #
# Errors: everything maps to 3, and nothing escapes as a traceback
# --------------------------------------------------------------------------- #


def _raise(exc: BaseException):
    def boom(*_args: object, **_kwargs: object):
        raise exc

    return boom


@pytest.fixture
def runnable_goldenset(tmp_path: Path) -> Path:
    path = tmp_path / "set.jsonl"
    path.write_text(
        json.dumps({"id": "only-01", "input": "What is 2 + 2?", "reference": "4"}) + "\n",
        encoding="utf-8",
    )
    return path


def _run_argv(goldenset: Path) -> list[str]:
    return [
        "run",
        "--goldenset",
        str(goldenset),
        "--model",
        "fake-baseline-v1",
        "--adapter",
        "fake",
    ]


class TestErrorMapping:
    """session-3 §3.3: which exceptions map to 3, and how they are reported.

    The injection point is ``GoldenSet.load``/``GoldenSet.parse``, patched on the
    class so it holds however ``cli.py`` imported it. Every command has to read a
    golden set or an artifact eventually, and patching a frozen public seam is the
    only injection that does not assume a private function name.
    """

    def test_adapter_error_and_sample_timeout_are_not_rigor_errors(self) -> None:
        """Verified against the installed package, not taken from the document.

        This is the fact that makes the ``except RigorError`` shortcut wrong. If
        this test ever fails, §3.3's separate ``AdapterError``/``SampleTimeout``
        clauses became redundant -- and until it does, a handler that omits them
        lets a provider failure escape as an unhandled traceback.
        """
        assert not issubclass(AdapterError, RigorError)
        assert not issubclass(SampleTimeout, RigorError)
        assert issubclass(AdapterError, Exception)
        assert issubclass(SampleTimeout, Exception)

    def test_the_statistical_family_is_an_assertion_error(self) -> None:
        # session-3 §0: which is why §3.3 uses no blanket `except AssertionError`.
        assert issubclass(RegressionError, AssertionError)
        assert issubclass(PassRateError, AssertionError)

    @pytest.mark.parametrize("exc", MAPPED_ERRORS, ids=_error_id)
    def test_a_mapped_exception_returns_error(
        self,
        exc: BaseException,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
    ) -> None:
        monkeypatch.setattr(GoldenSet, "load", _raise(exc))
        monkeypatch.setattr(GoldenSet, "parse", _raise(exc))
        assert cli.main(_run_argv(runnable_goldenset)) == FROZEN_EXIT_CODES["ERROR"]

    @pytest.mark.parametrize("exc", MAPPED_ERRORS, ids=_error_id)
    def test_a_mapped_exception_is_one_stderr_line_with_no_traceback(
        self,
        exc: BaseException,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(GoldenSet, "load", _raise(exc))
        monkeypatch.setattr(GoldenSet, "parse", _raise(exc))
        cli.main(_run_argv(runnable_goldenset))
        captured = capsys.readouterr()
        reported = [line for line in _stderr_lines(captured.err) if line.startswith("migkit:")]
        assert len(reported) == 1, captured.err
        assert type(exc).__name__ in reported[0]
        assert str(exc) in reported[0]
        assert "Traceback" not in captured.err
        assert "migkit:" not in captured.out

    def test_the_stderr_line_has_the_shape_the_contract_states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # session-3 §3.3: "printed to stderr as `migkit: <type>: <message>`".
        # EvidenceError is used because its str() is exactly the message given.
        message = "malformed evidence at line 2 of ./.migkit/evidence.jsonl"
        monkeypatch.setattr(GoldenSet, "load", _raise(EvidenceError(message)))
        monkeypatch.setattr(GoldenSet, "parse", _raise(EvidenceError(message)))
        cli.main(_run_argv(runnable_goldenset))
        assert f"migkit: EvidenceError: {message}" in _stderr_lines(capsys.readouterr().err)

    def test_a_regression_error_is_an_error_and_not_a_no_go(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """session-3 §3.3, and it is the sharpest line in that section.

        "A ``RegressionError`` or ``PassRateError`` reaching ``cli.py`` is a bug in
        ``comparison.py``, not a NO-GO: the verdict is read from the evidence
        record and is never inferred from an exception type."
        """
        monkeypatch.setattr(GoldenSet, "load", _raise(RegressionError("candidate regressed")))
        monkeypatch.setattr(GoldenSet, "parse", _raise(RegressionError("candidate regressed")))
        code = cli.main(_run_argv(runnable_goldenset))
        assert code == FROZEN_EXIT_CODES["ERROR"]
        assert code != FROZEN_EXIT_CODES["NO-GO"]
        assert "RegressionError" in capsys.readouterr().err

    def test_an_unclassified_exception_returns_error_with_a_traceback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """session-3 §3.3 and §6 item 26.

        A ``KeyError`` is an unanticipated bug in the tool. It still returns 3,
        but its traceback is printed **always**, because for a failure nobody
        anticipated the traceback is the only diagnostic and suppressing it costs
        the bug report.
        """
        monkeypatch.setattr(GoldenSet, "load", _raise(KeyError("goldenset_hash")))
        monkeypatch.setattr(GoldenSet, "parse", _raise(KeyError("goldenset_hash")))
        code = cli.main(_run_argv(runnable_goldenset))
        captured = capsys.readouterr()
        assert code == FROZEN_EXIT_CODES["ERROR"]
        assert "Traceback" in captured.err
        assert "KeyError" in captured.err

    def test_keyboard_interrupt_returns_error_and_names_a_resume_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """session-3 §3.3 and §6 item 27.

        A deliberate departure from the shell's 128+SIGINT convention: the plan
        documents four exit codes as *the* CI contract, and a fifth value
        appearing only on Ctrl-C would break the promise that the code is always
        one of four.
        """
        monkeypatch.setattr(GoldenSet, "load", _raise(KeyboardInterrupt()))
        monkeypatch.setattr(GoldenSet, "parse", _raise(KeyboardInterrupt()))
        code = cli.main(_run_argv(runnable_goldenset))
        err = capsys.readouterr().err
        assert code == FROZEN_EXIT_CODES["ERROR"]
        assert "interrupted" in err
        assert "resume" in err.lower()
        assert "migkit" in err


# --------------------------------------------------------------------------- #
# `migkit run` produces no verdict, so it can only say 0 or 3
# --------------------------------------------------------------------------- #


class TestRun:
    """session-3 §3.2 and §6 item 28.

    "``migkit run`` never returns 1 or 2 -- it produces no verdict, so its 0 means
    'the run completed', not 'GO'. ... a pipeline that gates on ``migkit run``
    would otherwise be gating on nothing."
    """

    def test_a_keyless_fake_run_returns_zero(
        self, runnable_goldenset: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = cli.main(
            [*_run_argv(runnable_goldenset), "--n", "2", "--out-dir", str(tmp_path / "artifacts")]
        )
        assert code == FROZEN_EXIT_CODES["GO"]

    def test_run_never_returns_a_verdict_code(
        self,
        runnable_goldenset: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        allowed = {FROZEN_EXIT_CODES["GO"], FROZEN_EXIT_CODES["ERROR"]}
        argvs = [
            [*_run_argv(runnable_goldenset), "--n", "1", "--out-dir", str(tmp_path / "one")],
            [*_run_argv(runnable_goldenset), "--n", "3", "--out-dir", str(tmp_path / "three")],
            # Arguments that cannot produce a run at all still may not borrow a
            # verdict code to describe their failure.
            ["run", "--goldenset", str(tmp_path / "absent.jsonl"), "--model", "fake-baseline-v1"],
            [*_run_argv(runnable_goldenset), "--n", "0"],
            ["run", "--goldenset", str(runnable_goldenset), "--model", "gpt-4o-latest"],
        ]
        codes = [cli.main(argv) for argv in argvs]
        assert set(codes) <= allowed, codes


# --------------------------------------------------------------------------- #
# Streams
# --------------------------------------------------------------------------- #


class TestStreams:
    """session-3 §3.4 and §6 items 30-31."""

    def test_the_report_goes_to_stdout_and_not_to_stderr(
        self, evidence_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # "So `migkit report x.jsonl > report.txt` captures the document and still
        # shows progress in the terminal." The document must not be split across
        # the two streams, or the redirect captures half of it.
        log = _rewrite_verdict(evidence_fixture, tmp_path / "streams.jsonl", "NO-GO")
        cli.main(["report", str(log)])
        captured = capsys.readouterr()
        assert "NO-GO" in captured.out
        assert _last_line(captured.out) == verdict_line("NO-GO")
        assert "VERDICT:" not in captured.err

    def test_quiet_still_emits_the_verdict_line(
        self, evidence_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # session-3 §3.4: "--quiet silences progress but never the verdict line
        # or errors."
        log = _rewrite_verdict(evidence_fixture, tmp_path / "quiet.jsonl", "REVIEW")
        code = cli.main(["--quiet", "report", str(log)])
        assert code == FROZEN_EXIT_CODES["REVIEW"]
        assert _last_line(capsys.readouterr().out) == verdict_line("REVIEW")

    def test_quiet_does_not_silence_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        runnable_goldenset: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(GoldenSet, "load", _raise(ConfigError("threshold out of range")))
        monkeypatch.setattr(GoldenSet, "parse", _raise(ConfigError("threshold out of range")))
        code = cli.main(["--quiet", *_run_argv(runnable_goldenset)])
        assert code == FROZEN_EXIT_CODES["ERROR"]
        assert "ConfigError" in capsys.readouterr().err


class TestConsoleScript:
    """session-3 §6 item 29: a function returning 3 and a process exiting 3 are
    two different claims, and only the second one is what CI observes."""

    @pytest.mark.slow
    def test_process_exit_status_matches_the_in_process_return(
        self, evidence_fixture: Path, tmp_path: Path
    ) -> None:
        log = _rewrite_verdict(evidence_fixture, tmp_path / "subprocess.jsonl", "NO-GO")
        in_process = cli.main(["report", str(log)])
        assert in_process == FROZEN_EXIT_CODES["NO-GO"]

        script = shutil.which("migkit", path=str(Path(sys.executable).parent))
        assert script, f"the migkit console script is not installed beside {sys.executable}"
        completed = subprocess.run(  # noqa: S603
            [script, "report", str(log)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == in_process, completed.stderr


# --------------------------------------------------------------------------- #
# The bundled demo data, reachable the way a wheel would have to reach it
# --------------------------------------------------------------------------- #


class TestPackagedDemoData:
    """session-3 §5.1 and §6 item 35.

    The packaging trap this guards is already armed: ``.gitignore`` swallows
    ``*.jsonl``, hatchling honours VCS ignore files, and ``pip install -e .`` plus
    every local test would still pass with the golden set missing from the wheel.
    The demo would then fail only for the stranger in the definition of done. So
    the check goes through ``importlib.resources``, which is what a wheel install
    resolves, rather than through a path relative to this file.
    """

    @pytest.mark.parametrize(
        "name", ["demo_goldenset.jsonl", "demo_rubric.md", "demo.toml"]
    )
    def test_the_file_is_reachable_as_package_data(self, name: str) -> None:
        resource = resources.files("model_migration_kit.data") / name
        assert resource.is_file(), f"{name} is not reachable through importlib.resources"
        assert resource.read_text(encoding="utf-8").strip(), f"{name} is empty"

    def test_the_bundled_goldenset_is_the_twelve_items_the_contract_describes(self) -> None:
        raw = (resources.files("model_migration_kit.data") / "demo_goldenset.jsonl").read_bytes()
        goldenset = GoldenSet.parse(raw, source="model_migration_kit.data/demo_goldenset.jsonl")
        assert len(goldenset) == 12
        assert goldenset.ids == DEMO_ITEM_IDS
        assert goldenset.stats()["tags"] == DEMO_TAG_COUNTS

    def test_the_bundled_rubric_declares_rigors_one_to_five_scale(self) -> None:
        # session-3 §5.1: "one rubric, scored on rigor's 1-5 scale".
        text = (resources.files("model_migration_kit.data") / "demo_rubric.md").read_text(
            encoding="utf-8"
        )
        assert "1-5" in text or "1–5" in text

    def test_the_bundled_config_loads_and_pins_its_judge(self) -> None:
        # session-3 §5.1: demo.toml carries "the thresholds the demo runs under,
        # so the demo also demonstrates the threshold echo".
        with resources.as_file(resources.files("model_migration_kit.data") / "demo.toml") as path:
            config = JudgeConfig.load(path)
        assert config.specs, "the demo config declares no judge"
        assert all(spec.model.startswith("fake-") for spec in config.specs)
        assert 0.0 <= config.thresholds.pass_rate_floor <= 1.0


# --------------------------------------------------------------------------- #
# `migkit demo`
# --------------------------------------------------------------------------- #


KEY_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
)


def _strip_keys(monkeypatch: pytest.MonkeyPatch, *, mode: str) -> None:
    for name in KEY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if mode == "empty":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")


def _run_demo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> tuple[int, Path, Path]:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / f"{label}.html"
    work = tmp_path / f"{label}-work"
    code = cli.main(["demo", "--out", str(out), "--work-dir", str(work), "--keep"])
    return code, out, work


def _demo_evidence(*roots: Path):
    """Find the evidence log the demo wrote, wherever the demo chose to write it.

    Session-3 §5.2 fixes the artifact *directory* (``--work-dir``) but not the
    log's filename, so this looks for the file by content -- the one carrying a
    ``migkit.verdict`` record -- rather than by a name the contract never states.
    """
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            try:
                records = EvidenceLog(path).read()
            except Exception:  # noqa: BLE001 - artifacts share the extension
                continue
            if any(record.event_type == EVENT_VERDICT for record in records):
                return records
    raise AssertionError(f"no evidence log with a verdict record under {[str(r) for r in roots]}")


def _payload(records, event_type: str) -> dict:
    for record in records:
        if record.event_type == event_type:
            return dict(record.payload)
    raise AssertionError(f"no {event_type} record in the demo's evidence log")


def _flip_ids(records) -> list[str]:
    return [str(flip["item_id"]) for flip in _payload(records, EVENT_COMPARISON)["flips"]]


class TestDemo:
    """session-3 §5 and §6 items 32-36; build-plan §5, the definition of done."""

    def test_the_demo_is_its_own_module(self) -> None:
        # session-3 D4: "Frozen default: a small demo.py", so the wiring does not
        # make cli.py two pages. Asserted structurally, not by reading line counts.
        assert demo.__name__ == "model_migration_kit.demo"
        tree = ast.parse(Path(inspect.getsourcefile(cli) or "").read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        } | {
            alias.name.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "demo" in imported or any(
            isinstance(node, ast.ImportFrom) and (node.module or "").endswith("demo")
            for node in ast.walk(tree)
        ), "cli.py does not import the demo module; the wiring belongs in demo.py (D4)"

    def test_the_demo_reads_no_credential_from_the_environment(self) -> None:
        # session-3 §5.2: "no sleeps, no network, no keys read from the
        # environment". A source-level check, because an env read that happens to
        # find nothing today still runs on the stranger's machine tomorrow.
        source = Path(inspect.getsourcefile(demo) or "").read_text(encoding="utf-8")
        assert "API_KEY" not in source

    @pytest.mark.parametrize("mode", ["absent", "empty"])
    def test_the_demo_runs_keyless_and_refuses_the_migration(
        self, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The definition-of-done path: a stranger with no keys.

        The expected code is 1 because the contract says the demo's verdict is
        NO-GO -- session-3 §5.2 and build-plan §5 -- not because a run of the demo
        was observed to say so. PROGRESS.md already records the consequence: CI's
        ``demo`` job is knowingly broken until it is amended to expect this code,
        and making the demo exit 0 instead "would hide the day the scripted
        quality difference stopped being detected" (session-3 D3).
        """
        _strip_keys(monkeypatch, mode=mode)
        code, out, _work = _run_demo(tmp_path, monkeypatch, f"keyless-{mode}")
        assert code == FROZEN_EXIT_CODES[DEMO_VERDICT]
        assert out.is_file(), "the demo left nothing to open"
        assert out.stat().st_size > 0

    def test_the_demo_records_the_contracted_verdict_in_its_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _strip_keys(monkeypatch, mode="absent")
        _run_demo(tmp_path, monkeypatch, "recorded")
        records = _demo_evidence(tmp_path / "recorded-work", tmp_path / ".migkit", tmp_path)
        assert _payload(records, EVENT_VERDICT)["verdict"] == DEMO_VERDICT

    def test_the_demo_produces_a_non_empty_flip_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # build-plan §5: the report shows "a NO-GO verdict with confidence
        # intervals and a flip list". session-3 §5.2 constructs the candidate's
        # degradation precisely so the flip list is non-empty.
        _strip_keys(monkeypatch, mode="absent")
        _run_demo(tmp_path, monkeypatch, "flips")
        records = _demo_evidence(tmp_path / "flips-work", tmp_path / ".migkit", tmp_path)
        flips = _flip_ids(records)
        assert flips, "a NO-GO demo with an empty flip list shows the reader nothing"
        assert set(flips) <= set(DEMO_ITEM_IDS), flips

    def test_the_demos_last_stdout_line_is_the_verdict_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _strip_keys(monkeypatch, mode="absent")
        code, out, _work = _run_demo(tmp_path, monkeypatch, "lastline")
        captured = capsys.readouterr()
        assert code == FROZEN_EXIT_CODES[DEMO_VERDICT]
        assert _last_line(captured.out) == verdict_line(DEMO_VERDICT)
        # session-3 §5.2: the report's absolute path is printed as the last line
        # before the verdict line, "because the next thing the reader must do is
        # open it".
        assert str(out.resolve()) in captured.out

    @pytest.mark.slow
    def test_the_demo_is_deterministic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """session-3 §5.2 and §6 item 33.

        "The demo must be bit-for-bit deterministic, because a demo that
        occasionally returns GO would destroy the only claim the pitch makes."
        Two runs, identical verdicts, identical flip id lists in identical order.
        """
        _strip_keys(monkeypatch, mode="absent")
        _run_demo(tmp_path, monkeypatch, "det-one")
        first = _demo_evidence(tmp_path / "det-one-work", tmp_path / ".migkit")
        _run_demo(tmp_path, monkeypatch, "det-two")
        second = _demo_evidence(tmp_path / "det-two-work", tmp_path / ".migkit")

        assert _payload(first, EVENT_VERDICT)["verdict"] == DEMO_VERDICT
        assert _payload(second, EVENT_VERDICT)["verdict"] == DEMO_VERDICT
        assert _payload(first, EVENT_VERDICT)["verdict"] == _payload(second, EVENT_VERDICT)[
            "verdict"
        ]
        assert _flip_ids(first) == _flip_ids(second)

    def test_the_demos_report_carries_the_fake_model_band(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """session-3 §5.3 and §6 item 34.

        Five places say it and none is a footnote. Two are asserted here because
        they are the two a screenshot cannot crop away: the ``<title>`` and the
        band above the verdict banner. Demo-ness derives from the run artifact's
        adapter name, so this also fails if a flag ever starts driving it.
        """
        _strip_keys(monkeypatch, mode="absent")
        _code, out, _work = _run_demo(tmp_path, monkeypatch, "band")
        html = out.read_text(encoding="utf-8")
        assert "FAKE MODELS" in html
        title = html[html.find("<title") : html.find("</title>")]
        assert "FAKE" in title.upper(), title
        assert "fake-candidate-v1" in html
        assert "FakeAdapter" in html

    def test_the_demos_report_is_self_contained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """session-3 §2.4 and §6 item 34.

        The report is opened inside a compliance review on a machine with no route
        to the internet, and an outbound request from a document containing model
        outputs is itself the finding.
        """
        from model_migration_kit.report import assert_self_contained, external_urls

        _strip_keys(monkeypatch, mode="absent")
        _code, out, _work = _run_demo(tmp_path, monkeypatch, "contained")
        html = out.read_text(encoding="utf-8")
        assert external_urls(html) == ()
        assert_self_contained(html, source=str(out))
        assert "<script" not in html.lower()
        assert "<link" not in html.lower()

    def test_the_report_is_utf8_with_lf_endings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # session-3 §2.1: written with encoding="utf-8", newline="\n" explicitly,
        # so the file's hash does not differ per platform. Asserted on the bytes,
        # on Windows as well as Linux.
        _strip_keys(monkeypatch, mode="absent")
        _code, out, _work = _run_demo(tmp_path, monkeypatch, "bytes")
        raw = out.read_bytes()
        raw.decode("utf-8")
        assert b"\r\n" not in raw
        assert b'charset="utf-8"' in raw.lower() or b"charset=utf-8" in raw.lower()

    @pytest.mark.slow
    def test_the_demo_completes_within_the_wall_clock_budget(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """session-3 §5.2 and §6 item 36.

        The CI job's ``timeout 120`` is the outer bound; this is the tighter one,
        "because a test that only trusted CI would notice a regression a week
        late". The margin is deliberately generous: the budget is not a benchmark,
        it is a tripwire for a sleep or a network call sneaking into the demo.
        """
        _strip_keys(monkeypatch, mode="absent")
        started = time.monotonic()
        code, out, _work = _run_demo(tmp_path, monkeypatch, "budget")
        elapsed = time.monotonic() - started
        assert code == FROZEN_EXIT_CODES[DEMO_VERDICT]
        assert out.is_file()
        assert elapsed < DEMO_BUDGET_SECONDS, f"the demo took {elapsed:.1f}s"

    def test_the_demo_leaves_no_credential_in_the_environment_behind_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PROGRESS.md invariant 3: the suite is green with no credentials and no
        # network. A demo that quietly set a key for its own use would make the
        # keyless claim untestable from here on.
        _strip_keys(monkeypatch, mode="absent")
        _run_demo(tmp_path, monkeypatch, "env")
        assert not [name for name in KEY_VARIABLES if os.environ.get(name)]
