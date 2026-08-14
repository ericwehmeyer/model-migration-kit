"""What a cold-start stranger hits: the keyless path over *their* golden set.

Every expectation here is derived from a rule stated in prose somewhere the reader
can find it -- the demo module's docstring, the error message the CLI prints, the
package docstring -- and never from what the implementation returned when it was
run. Where a number could only have come from running the code, it is computed
here independently and the two are compared.

The findings this file exists to keep closed, in the words of the reader who hit
them against a real wheel install:

1. *"Pointing it at your own data is a dead end, and the README never says so."*
   ``migkit compare`` refuses ``adapter = "fake"`` for a judge -- correctly -- and
   its error said *"Use `migkit demo` for the keyless path"*. ``migkit demo`` took
   ``[--out] [--work-dir] [--keep] [--no-terminal]`` and no golden set, so the
   remedy the message named did not exist for the reader's own data at any *n*.
   :class:`TestTheRemedyTheErrorNamesIsReal` asserts the message's own suggested
   command against the argument parser, which is the form of this defect that can
   recur: prose and parser drifting apart.

2. *"``anthropic`` is not installed and is not declared."* Following the README's
   Install section exactly left a reader one undeclared package short of the only
   documented real-model path, and they found out at *grading* time -- after both
   runs were sampled -- which sits oddly beside the README's emphasis that the pin
   rule is checked "before a single API call is spent".

3. *"The bundled golden set is in the wheel but unreachable by the documented
   path."* The README's example was ``GoldenSet.load('src/model_migration_kit/
   data/demo_goldenset.jsonl')``, a source-tree path that does not exist in an
   install, and ``dir(model_migration_kit)`` offered nothing else.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import model_migration_kit
from model_migration_kit import cli, demo
from model_migration_kit.errors import ConfigError, GoldenSetError, JudgeConfigError
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeSpec

_REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- #
# Expectations, stated here rather than imported from the code under test
# --------------------------------------------------------------------------- #

#: The three files the package ships, from the package docstring's own list.
BUNDLED_DATA_NAMES = ("demo_goldenset.jsonl", "demo_rubric.md", "demo.toml")

#: The accessors the package promises, spelled out rather than read from
#: ``__all__``, so that deleting one is a failure here rather than an agreement.
ACCESSORS = ("demo_goldenset_path", "demo_rubric_path", "demo_config_path")

#: ``demo.py``'s stated derivation rule, restated: the first item is where the
#: candidate improves, and every fourth item from the fourth is where it regresses.
GAIN_INDEX = 0
FIRST_REGRESSION_INDEX = 3
REGRESSION_STRIDE = 4

#: The bundled demo's margins, from the README's pasted transcript: baseline 11 of
#: 12 items passing, candidate 9 of 12, with three flips and one gain. The derived
#: rule is calibrated to reproduce exactly this shape at twelve items, which is
#: where the "one in four" comes from -- so a twelve-item set of anybody's must
#: produce the same counts, and therefore the same verdict.
BUNDLED_ITEM_COUNT = 12
BUNDLED_FLIPS = 3
BUNDLED_GAINS = 1
BUNDLED_VERDICT_EXIT = 1


def regression_indices(size: int) -> list[int]:
    """The positions the rule says the candidate regresses at, for a set of ``size``."""
    return list(range(FIRST_REGRESSION_INDEX, size, REGRESSION_STRIDE))


def make_goldenset(path: Path, size: int, *, refusal_every: int = 0) -> Path:
    """Write a golden set of ``size`` items with distinct ids and distinct inputs.

    ``refusal_every`` makes every n-th item reference-less, which is the other half
    of the derivation rule: an item with no reference is scripted as a refusal that
    either declines or fabricates, rather than as an answer that is right or wrong.
    """
    lines = []
    for index in range(size):
        item: dict[str, object] = {
            "id": f"item-{index:03d}",
            "input": f"Question number {index}: what is the answer?",
            "tags": ["synthetic"],
        }
        if not (refusal_every and index % refusal_every == 0):
            item["reference"] = f"answer-{index:03d}"
        lines.append(json.dumps(item))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Finding 3: the bundled data is in the wheel and now reachable
# --------------------------------------------------------------------------- #


class TestTheBundledDataIsReachable:
    """``dir(model_migration_kit)`` was ``['annotations']``; the wheel had the files."""

    @pytest.mark.parametrize("name", ACCESSORS)
    def test_the_accessor_exists_and_is_exported(self, name: str) -> None:
        assert hasattr(model_migration_kit, name), (
            f"{name} is the accessor the README now tells readers to call; without "
            f"it the bundled data is in the wheel and unreachable, which is the "
            f"state a stranger found it in"
        )
        assert name in model_migration_kit.__all__

    def test_all_names_exactly_the_accessors_and_nothing_else(self) -> None:
        """The package docstring argues at length that v0.1 exports no API *except*
        these. A fourth name appearing here means that argument stopped being true
        without anybody rewriting it."""
        assert sorted(model_migration_kit.__all__) == sorted(ACCESSORS)

    @pytest.mark.parametrize(
        ("accessor", "filename"), list(zip(ACCESSORS, BUNDLED_DATA_NAMES, strict=True))
    )
    def test_the_accessor_returns_an_existing_file_with_the_right_name(
        self, accessor: str, filename: str
    ) -> None:
        path = getattr(model_migration_kit, accessor)()
        assert isinstance(path, Path)
        assert path.name == filename
        assert path.is_file(), f"{accessor}() named {path}, which is not a file"

    def test_the_goldenset_accessor_loads_as_a_golden_set(self) -> None:
        """The README's replacement for the source-tree path it used to print."""
        goldenset = GoldenSet.load(model_migration_kit.demo_goldenset_path())
        assert len(goldenset) == BUNDLED_ITEM_COUNT

    def test_the_rubric_accessor_returns_readable_prose(self) -> None:
        text = model_migration_kit.demo_rubric_path().read_text(encoding="utf-8")
        assert text.strip(), "the rubric is empty"

    def test_the_config_accessor_parses_as_a_judge_config(self) -> None:
        from model_migration_kit.judging import JudgeConfig

        config = JudgeConfig.load(model_migration_kit.demo_config_path())
        assert config.specs, "the bundled config declares no judge"

    def test_a_missing_bundled_file_is_reported_as_a_packaging_fault(self) -> None:
        """Not as a bare FileNotFoundError on a path the reader never typed."""
        with pytest.raises(FileNotFoundError) as excinfo:
            model_migration_kit._data_path("no-such-file.jsonl")
        assert "packaging fault" in str(excinfo.value)

    def test_the_accessors_work_in_an_interpreter_that_imported_nothing_else(self) -> None:
        """The audience is a wheel install, where ``import model_migration_kit`` is
        all that has happened. Run in a child process for the same reason
        ``test_import_purity.py`` gives: in-process, whatever the rest of the suite
        imported first would be doing the work."""
        program = (
            "import model_migration_kit as m\n"
            "print('\\n'.join(str(getattr(m, n)()) for n in "
            f"{ACCESSORS!r}))\n"
        )
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=str(_REPO_ROOT),
            env={
                **_child_env(),
            },
        )
        assert completed.returncode == 0, completed.stderr
        for line in completed.stdout.strip().splitlines():
            assert Path(line).is_file(), f"{line} is not a file in a bare interpreter"

    def test_the_package_still_loads_no_submodule_of_its_own(self) -> None:
        """The accessors are implemented with ``pathlib`` rather than by importing
        ``demo``. ``test_import_purity.py`` owns this claim in general; it is
        repeated here because these three functions are the change most likely to
        break it, and a reader of this file should see the constraint they were
        written under."""
        program = (
            "import json, sys, model_migration_kit\n"
            "print(json.dumps(sorted(n for n in sys.modules "
            "if n.startswith('model_migration_kit.'))))\n"
        )
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            cwd=str(_REPO_ROOT),
            env=_child_env(),
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout) == []

    def test_the_release_script_and_the_package_agree_on_the_filenames(self) -> None:
        """``scripts/verify_release.py`` audits a wheel from the outside and must not
        import the package it is auditing, so it keeps its own copy of the three
        names. That is a duplication; this is the check that makes it a safe one."""
        source = (_REPO_ROOT / "scripts" / "verify_release.py").read_text(encoding="utf-8")
        for name in BUNDLED_DATA_NAMES:
            assert name in source, (
                f"verify_release.py no longer names {name}, so a wheel missing it "
                f"would pass the release checks"
            )
        package_names = (
            model_migration_kit.DEMO_GOLDENSET_NAME,
            model_migration_kit.DEMO_RUBRIC_NAME,
            model_migration_kit.DEMO_CONFIG_NAME,
        )
        assert sorted(package_names) == sorted(BUNDLED_DATA_NAMES)


def _child_env() -> dict[str, str]:
    """Environment for a child interpreter that must import *this* checkout.

    Same reasoning as ``test_import_purity._run_probe``: the interpreter is shared
    with the suite, so its dependencies are present, but the code under test has to
    be the tree this file was checked out with -- otherwise a git worktree or a
    stale editable install silently makes the assertion about somebody else's
    source.
    """
    import os

    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    src = str(_REPO_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join([src, existing] if existing else [src])
    return env


# --------------------------------------------------------------------------- #
# Finding 1: the remedy the refusal names has to exist
# --------------------------------------------------------------------------- #


class TestTheRemedyTheErrorNamesIsReal:
    """The refusal is right; the advice it gave was not runnable.

    A message that names a command is a promise about the parser. This class holds
    the two to each other, because that is the only form of the check that survives
    somebody rewording either one.
    """

    def test_the_fake_judge_is_still_refused(self) -> None:
        """First, the thing that must not be weakened by any of this."""
        spec = JudgeSpec(
            name="accuracy", model="fake-judge-v1", rubric=Path("r.md"), rubric_hash="x",
            adapter="fake",
        )
        with pytest.raises(JudgeConfigError):
            cli._judge_adapter(spec)

    def test_every_migkit_command_the_refusal_suggests_is_one_the_parser_accepts(
        self,
    ) -> None:
        """Extract each ``migkit ...`` command out of the message and parse it.

        This is the finding, generalised. The old message ended *"Use `migkit demo`
        for the keyless path"* and that command existed -- what did not exist was
        the ability to point it at the reader's data, which is what they were being
        told to do. So the check is not "does `demo` parse" but "does every command
        this message spells out, with the options it spells out, parse".
        """
        spec = JudgeSpec(
            name="accuracy", model="fake-judge-v1", rubric=Path("r.md"), rubric_hash="x",
            adapter="fake",
        )
        with pytest.raises(JudgeConfigError) as excinfo:
            cli._judge_adapter(spec)
        commands = _suggested_commands(str(excinfo.value))
        assert commands, (
            "the refusal names no command at all. It is allowed not to -- but it "
            "used to name one that did not do what it was cited for, so if it names "
            "one, that one has to parse."
        )
        parser = cli.build_parser()
        for command in commands:
            parser.parse_args(command)  # raises SystemExit if the parser refuses

    def test_the_refusal_points_at_the_goldenset_flag(self) -> None:
        """Specific enough to fail if the message reverts to the bare ``migkit demo``
        that sent a reader to a dead end."""
        spec = JudgeSpec(
            name="accuracy", model="fake-judge-v1", rubric=Path("r.md"), rubric_hash="x",
            adapter="fake",
        )
        with pytest.raises(JudgeConfigError) as excinfo:
            cli._judge_adapter(spec)
        assert "--goldenset" in str(excinfo.value)

    def test_demo_accepts_a_goldenset_and_an_n(self) -> None:
        args = cli.build_parser().parse_args(
            ["demo", "--goldenset", "mine.jsonl", "--n", "7"]
        )
        assert args.goldenset == "mine.jsonl"
        assert args.n == 7

    def test_demo_takes_no_judges_flag(self) -> None:
        """Stated in ``demo.py``: the scripted judge grades by ``judge_script``, so
        honouring a rubric the caller supplied would record their rubric's hash
        beside grades that never read it."""
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["demo", "--judges", "mine.toml"])


def _suggested_commands(message: str) -> list[list[str]]:
    """Every ``migkit <...>`` command a message spells out, as argv lists.

    Backtick-delimited, which is how this project writes commands in prose.
    Placeholders in angle brackets become ``1``, which is the one string that is a
    legal value for every option this tool has -- a filename, and an ``int``. The
    check is whether the *shape* parses; ``<your set>`` is not a filename.
    """
    import re

    out: list[list[str]] = []
    for quoted in re.findall(r"`([^`]+)`", message):
        text = quoted.strip()
        if not text.startswith("migkit "):
            continue
        text = re.sub(r"<[^>]+>", "1", text)
        out.append(shlex.split(text)[1:])
    return out


# --------------------------------------------------------------------------- #
# Finding 1: the derivation rule, checked against the rule rather than the output
# --------------------------------------------------------------------------- #


class TestTheDerivedScripts:
    """``demo.derive_responses`` states its rule in prose; this is that prose."""

    @pytest.mark.parametrize("size", [1, 2, 4, 8, 12, 13])
    def test_the_gain_and_the_regressions_land_where_the_rule_says(
        self, tmp_path: Path, size: int
    ) -> None:
        path = make_goldenset(tmp_path / "g.jsonl", size)
        goldenset = GoldenSet.load(path)
        baseline, candidate = demo.derive_responses(goldenset)
        items = list(goldenset)
        expected_regressions = {items[i].id for i in regression_indices(size)}
        expected_gain = {items[GAIN_INDEX].id}

        differ = {item.id for item in items if baseline[item.id] != candidate[item.id]}
        assert differ == expected_regressions | expected_gain, (
            "the two scripts differ somewhere the stated rule does not put a "
            "difference"
        )

    @pytest.mark.parametrize("size", [1, 4, 8, 12])
    def test_the_scripts_cover_every_item_exactly_once(
        self, tmp_path: Path, size: int
    ) -> None:
        goldenset = GoldenSet.load(make_goldenset(tmp_path / "g.jsonl", size))
        baseline, candidate = demo.derive_responses(goldenset)
        ids = [item.id for item in goldenset]
        assert sorted(baseline) == sorted(ids)
        assert sorted(candidate) == sorted(ids)

    @pytest.mark.parametrize("refusal_every", [0, 2, 3])
    @pytest.mark.parametrize("size", [8, 12])
    def test_the_judge_grades_the_scripts_the_way_the_rule_predicts(
        self, tmp_path: Path, size: int, refusal_every: int
    ) -> None:
        """The rule is about right and wrong; the judge is what turns that into a
        pass. This runs the *real* judge script over the derived responses and
        checks the pass set against the rule, so a scripted "wrong" answer that the
        judge happens to pass -- which is exactly what an empty reference or a
        colliding canned sentence would produce -- is caught here rather than
        showing up as a demo with no regression in it.
        """
        goldenset = GoldenSet.load(
            make_goldenset(tmp_path / "g.jsonl", size, refusal_every=refusal_every)
        )
        baseline, candidate = demo.derive_responses(goldenset)
        items = list(goldenset)
        regressions = {items[i].id for i in regression_indices(size)}
        gain = items[GAIN_INDEX].id

        for item in items:
            base_pass = _judge_passes(goldenset, item, baseline[item.id])
            cand_pass = _judge_passes(goldenset, item, candidate[item.id])
            if item.id == gain:
                assert (base_pass, cand_pass) == (False, True), (
                    f"{item.id} is the rule's single gain: the baseline must fail it "
                    f"and the candidate must pass it"
                )
            elif item.id in regressions:
                assert (base_pass, cand_pass) == (True, False), (
                    f"{item.id} is a regression under the rule: the baseline must "
                    f"pass it and the candidate must fail it"
                )
            else:
                assert (base_pass, cand_pass) == (True, True), (
                    f"{item.id} is neither the gain nor a regression, so both sides "
                    f"must pass it"
                )

    def test_the_canned_decline_actually_declines(self) -> None:
        """``_grade`` scores a reference-less item on whether the text declines. If
        the constant drifts out of the marker list, the demo's "correct refusal"
        silently becomes a fabrication and the baseline fails every refusal item."""
        lowered = demo.SCRIPTED_DECLINE.lower()
        assert any(marker in lowered for marker in demo._DECLINE_MARKERS)

    def test_the_canned_fabrication_does_not_accidentally_decline(self) -> None:
        """The same drift in the other direction deletes the regression entirely."""
        lowered = demo.SCRIPTED_FABRICATION.lower()
        assert not any(marker in lowered for marker in demo._DECLINE_MARKERS)

    @pytest.mark.parametrize("index", [0, 1, 2])
    def test_a_reference_equal_to_a_canned_wrong_answer_gets_a_different_one(
        self, index: int
    ) -> None:
        """The reason the canned wrong answers are a list rather than a constant.

        Parametrised by position rather than by value so that collection does not
        depend on the module under test -- and so that the list keeping at least
        three entries is asserted rather than assumed, since two entries whose
        content words overlap could not cover each other.
        """
        from model_migration_kit.contracts import GoldenItem

        reference = demo.SCRIPTED_WRONG_ANSWERS[index]
        item = GoldenItem(id="x", input="q", reference=reference, tags=(), metadata={})
        answer = demo._wrong_answer_for(item)
        assert answer != reference
        assert not demo._mentions(answer, reference)

    def test_a_reference_no_canned_answer_can_avoid_is_refused_not_papered_over(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unreachable with the three shipped sentences, whose content words are
        disjoint -- so it is exercised by shrinking the list. Emitting a passing
        answer here would delete a regression the rule promised, silently."""
        from model_migration_kit.contracts import GoldenItem

        monkeypatch.setattr(demo, "SCRIPTED_WRONG_ANSWERS", ("collide",))
        item = GoldenItem(id="x", input="q", reference="collide", tags=(), metadata={})
        with pytest.raises(GoldenSetError) as excinfo:
            demo._wrong_answer_for(item)
        assert "x" in str(excinfo.value)

    def test_two_items_with_the_same_input_are_refused(self, tmp_path: Path) -> None:
        """``FakeAdapter`` answers by prompt and ``run_goldenset`` sends the input
        verbatim, so two items sharing an input share one scripted answer and the
        second silently overwrites the first. Loading permits it; the demo cannot."""
        path = tmp_path / "dupes.jsonl"
        path.write_text(
            json.dumps({"id": "a", "input": "same question", "reference": "1"})
            + "\n"
            + json.dumps({"id": "b", "input": "same question", "reference": "2"})
            + "\n",
            encoding="utf-8",
        )
        goldenset = GoldenSet.load(path)
        with pytest.raises(GoldenSetError) as excinfo:
            demo.derive_responses(goldenset)
        message = str(excinfo.value)
        assert "'a'" in message and "'b'" in message

    def test_the_bundled_set_keeps_its_hand_written_script(self) -> None:
        """Recognition is by content, so a caller who copies the bundled set
        somewhere else and passes ``--goldenset`` gets the bundled demo's verdict
        rather than a differently-scripted one sharing its hash."""
        goldenset = GoldenSet.load(model_migration_kit.demo_goldenset_path())
        baseline, candidate = demo.scripts_for(goldenset)
        assert baseline is demo.BASELINE_RESPONSES
        assert candidate is demo.CANDIDATE_RESPONSES

    def test_any_other_set_is_derived(self, tmp_path: Path) -> None:
        goldenset = GoldenSet.load(make_goldenset(tmp_path / "g.jsonl", 12))
        baseline, _candidate = demo.scripts_for(goldenset)
        assert baseline is not demo.BASELINE_RESPONSES


def _judge_passes(goldenset: GoldenSet, item, output: str) -> bool:
    """Whether the demo's real judge script passes ``output`` for ``item``.

    Goes through :func:`demo.judge_script`, i.e. the same callable the demo hands
    rigor, by building the prompt block-for-block the way rigor's
    ``PROMPT_TEMPLATE`` does. Grading through a re-implementation would test this
    file's idea of the judge.
    """
    prompt = (
        f"{demo._INPUT_OPEN}\n{item.input}\n{demo._INPUT_CLOSE}\n"
        f"{demo._OUTPUT_OPEN}\n{output}\n{demo._OUTPUT_CLOSE}\n"
    )
    return bool(json.loads(demo.judge_script(goldenset)(prompt))["pass"])


# --------------------------------------------------------------------------- #
# Finding 1, end to end: the CLI over a caller's own set
# --------------------------------------------------------------------------- #


class TestDemoOverYourOwnGoldenSet:
    def test_a_twelve_item_set_reproduces_the_bundled_demo_s_shape_and_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is why the rule degrades one item in four.

        At twelve items the derived pair is one gain and three flips -- baseline 11
        of 12, candidate 9 of 12 -- which is the bundled demo's margin exactly, and
        the bundled demo is NO-GO at n=5. Equal margins over an equal-sized sample
        must reach the same verdict, so the exit code is predicted from the bundled
        transcript rather than observed from this run.
        """
        monkeypatch.chdir(tmp_path)
        source = make_goldenset(tmp_path / "mine.jsonl", BUNDLED_ITEM_COUNT)
        work = tmp_path / "work"
        code = cli.main(
            [
                "demo",
                "--goldenset", str(source),
                "--out", str(tmp_path / "mine.html"),
                "--work-dir", str(work),
                "--no-terminal",
            ]
        )
        assert code == BUNDLED_VERDICT_EXIT
        assert (tmp_path / "mine.html").is_file()

        comparison = _comparison_payload(work)
        assert len(comparison["flips"]) == BUNDLED_FLIPS
        assert len(comparison["gains"]) == BUNDLED_GAINS

    def test_the_flip_list_carries_the_callers_own_item_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point of the feature: the reader recognises their own set in the
        document, at the positions the rule says."""
        monkeypatch.chdir(tmp_path)
        size = 12
        source = make_goldenset(tmp_path / "mine.jsonl", size)
        work = tmp_path / "work"
        cli.main(
            ["demo", "--goldenset", str(source), "--out", str(tmp_path / "o.html"),
             "--work-dir", str(work), "--no-terminal"]
        )
        items = list(GoldenSet.load(source))
        comparison = _comparison_payload(work)
        assert sorted(f["item_id"] for f in comparison["flips"]) == sorted(
            items[i].id for i in regression_indices(size)
        )
        assert [g["item_id"] for g in comparison["gains"]] == [items[GAIN_INDEX].id]

    def test_the_report_is_rendered_from_the_callers_set_not_the_bundled_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``report.py`` follows a recorded golden-set path only inside the evidence
        log's own directory, because a shared log is untrusted input on a reviewer's
        machine. A caller's set left where it lies is therefore refused by that rule
        and the report renders with no item inputs at all -- so the demo copies it
        in, and this asserts the copy is byte-identical and actually used."""
        monkeypatch.chdir(tmp_path)
        source = make_goldenset(tmp_path / "mine.jsonl", 8)
        work = tmp_path / "work"
        cli.main(
            ["demo", "--goldenset", str(source), "--out", str(tmp_path / "o.html"),
             "--work-dir", str(work), "--no-terminal"]
        )
        copy = work / "mine.jsonl"
        assert copy.is_file(), "the caller's golden set was not installed in the work dir"
        assert copy.read_bytes() == source.read_bytes()
        html = (tmp_path / "o.html").read_text(encoding="utf-8")
        assert "Question number 0" in html, (
            "the report does not show the caller's item inputs, which is what "
            "happens when the recorded golden-set path is refused as out-of-tree"
        )

    def test_n_changes_the_number_of_draws(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--n`` is the flag the whole feature is for: at your item count it is
        what decides whether "no regression detected" is a question that was asked."""
        monkeypatch.chdir(tmp_path)
        source = make_goldenset(tmp_path / "mine.jsonl", 8)
        work = tmp_path / "work"
        cli.main(
            ["demo", "--goldenset", str(source), "--n", "3",
             "--out", str(tmp_path / "o.html"), "--work-dir", str(work),
             "--no-terminal"]
        )
        comparison = _comparison_payload(work)
        judge = comparison["judges"][0]
        # 8 items x 3 draws = 24 graded completions per side, computed here from
        # the two numbers the command was given rather than read back from a run.
        assert judge["baseline"]["n"] == 8 * 3
        assert judge["candidate"]["n"] == 8 * 3
        assert judge["power"]["n_observed"] == 8 * 3

    def test_pointing_it_at_a_copy_of_the_bundled_set_gives_the_bundled_flips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        source = tmp_path / "copied.jsonl"
        source.write_bytes(model_migration_kit.demo_goldenset_path().read_bytes())
        work = tmp_path / "work"
        code = cli.main(
            ["demo", "--goldenset", str(source), "--out", str(tmp_path / "o.html"),
             "--work-dir", str(work), "--no-terminal"]
        )
        assert code == BUNDLED_VERDICT_EXIT
        comparison = _comparison_payload(work)
        assert sorted(f["item_id"] for f in comparison["flips"]) == [
            "extract-01", "refuse-02", "refuse-04"
        ]

    def test_a_missing_golden_set_is_an_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        code = cli.main(
            ["demo", "--goldenset", str(tmp_path / "nope.jsonl"),
             "--out", str(tmp_path / "o.html"), "--no-terminal"]
        )
        assert code == 3

    def test_a_set_named_after_a_bundled_file_is_refused(self, tmp_path: Path) -> None:
        """It would land on top of the rubric or the config this run grades with."""
        source = tmp_path / "demo_rubric.md"
        source.write_text("not a golden set\n", encoding="utf-8")
        with pytest.raises(GoldenSetError) as excinfo:
            demo.install_goldenset(tmp_path / "work", source)
        assert "demo_rubric.md" in str(excinfo.value)

    def test_a_set_named_like_the_bundled_golden_set_is_allowed(
        self, tmp_path: Path
    ) -> None:
        """That one collides with the bundled *golden set*, which the run is
        replacing anyway, so overwriting it is correct rather than destructive."""
        source = tmp_path / "demo_goldenset.jsonl"
        make_goldenset(source, 4)
        installed = demo.install_goldenset(tmp_path / "work", source)
        assert installed.read_bytes() == source.read_bytes()

    def test_the_bundled_demo_is_unchanged_by_any_of_this(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The definition of done, and the thing every other check in the repository
        is calibrated against: plain ``migkit demo`` still exits 1 with the bundled
        flips."""
        monkeypatch.chdir(tmp_path)
        work = tmp_path / "work"
        code = cli.main(
            ["demo", "--out", str(tmp_path / "o.html"), "--work-dir", str(work),
             "--no-terminal"]
        )
        assert code == BUNDLED_VERDICT_EXIT
        comparison = _comparison_payload(work)
        assert sorted(f["item_id"] for f in comparison["flips"]) == [
            "extract-01", "refuse-02", "refuse-04"
        ]


def _comparison_payload(work: Path) -> dict:
    """The comparison record out of whatever evidence log the demo wrote.

    By content rather than by filename, matching ``test_cli._demo_evidence``: the
    contract fixes the artifact directory and not the log's name.
    """
    from opik_rigor import EvidenceLog

    from model_migration_kit.contracts import EVENT_COMPARISON

    for path in sorted(work.rglob("*.jsonl")):
        try:
            records = EvidenceLog(path).read()
        except Exception:  # noqa: BLE001 - artifacts share the extension
            continue
        for record in records:
            if record.event_type == EVENT_COMPARISON:
                return dict(record.payload)
    raise AssertionError(f"no comparison record under {work}")


# --------------------------------------------------------------------------- #
# Finding 2: the provider SDK is declared, and checked before anything is spent
# --------------------------------------------------------------------------- #


class TestTheProviderSdkIsDeclaredAndCheckedEarly:
    def test_the_sdk_names_match_the_ones_rigor_actually_imports(self) -> None:
        """Not copied: read out of rigor's own adapter modules. A rename there would
        otherwise leave this tool telling people to install a package that is not
        the one about to be imported."""
        from opik_rigor.adapters import anthropic as rigor_anthropic
        from opik_rigor.adapters import openai_compat as rigor_openai

        assert cli.SDK_FOR_ADAPTER["anthropic"] == rigor_anthropic.PACKAGE
        assert cli.SDK_FOR_ADAPTER["openai-compat"] == rigor_openai.PACKAGE

    def test_every_adapter_kind_that_needs_an_sdk_is_a_real_adapter_kind(self) -> None:
        assert set(cli.SDK_FOR_ADAPTER) <= set(cli.ADAPTER_KINDS)

    def test_the_keyless_adapter_needs_nothing(self) -> None:
        cli._require_sdk("fake")  # must not raise

    @pytest.mark.parametrize("kind", ["anthropic", "openai-compat"])
    def test_a_missing_sdk_is_named_before_the_adapter_is_built(
        self, kind: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deliberately checked before construction: exporting a credential cannot
        fix a missing package, so the prerequisite that has to be satisfied first is
        the one reported first. And it must happen before sampling -- the reader who
        found this got the SDK error at *grading* time, minutes and two full runs
        after the point where it was knowable."""
        monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _name: None)
        with pytest.raises(ConfigError) as excinfo:
            cli._model_adapter(kind, "some-model-v1")
        message = str(excinfo.value)
        assert cli.SDK_FOR_ADAPTER[kind] in message
        assert "model-migration-kit[" in message, (
            "the error should name the extra that installs it, which is the thing "
            "the Install section was missing"
        )

    def test_the_extras_the_error_names_are_declared_in_pyproject(self) -> None:
        """The message tells people to run ``pip install "model-migration-kit[x]"``.
        That is only useful if ``x`` exists."""
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]

        config = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        extras = config["project"]["optional-dependencies"]
        for kind, extra in cli.EXTRA_FOR_ADAPTER.items():
            assert extra in extras, (
                f"adapter {kind!r} tells people to install the {extra!r} extra, "
                f"which pyproject.toml does not declare"
            )
            declared = " ".join(extras[extra])
            assert cli.SDK_FOR_ADAPTER[kind] in declared

    def test_neither_sdk_is_a_hard_runtime_dependency(self) -> None:
        """They must stay optional: the whole keyless story -- the demo, ``--adapter
        fake``, and this suite -- runs without them, and CI installs neither."""
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]

        config = tomllib.loads(
            (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        runtime = " ".join(config["project"]["dependencies"])
        for package in set(cli.SDK_FOR_ADAPTER.values()):
            assert package not in runtime
