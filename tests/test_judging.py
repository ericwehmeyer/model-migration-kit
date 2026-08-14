"""Acceptance tests for :mod:`migration_kit.judging`.

Written against `docs/session-2-contract.md` §1 as governed by `docs/build-plan.md`
§6. Per HANDOFF.md's working method, **no expected value in this file was obtained
by running the code under test**. Every expectation is one of:

* a literal from the contract (the six threshold defaults, ``SCORE_MIN``, the
  event name ``migkit.judging_completed``, the error type for each rejection);
* a hand derivation (2 parse failures in 20 records is 10%, which is over a 5%
  tolerance; 1 in 20 is exactly 5%, which is not *over* it);
* computed by a tool outside ``migration_kit`` -- stdlib ``hashlib``/``json`` used
  directly, which is how ``_sha256_lf`` grounds every rubric-hash expectation.

**How the judge response format was established.** Not guessed and not copied from
migration-kit: read from ``opik_rigor/judge.py`` in the installed 0.1.0 wheel.
``PinnedJudge.evaluate`` passes the adapter's reply to ``_parse_response``, which
scans the text for embedded top-level JSON objects (``_json_objects``) and keeps
those carrying a ``pass`` or ``passed`` key (``PASS_KEYS``). That key must be a
JSON boolean; ``score`` must be a number within ``SCORE_MIN``..``SCORE_MAX``
(1.0-5.0) **or null**, null being normal output per contract §0; ``reason`` is
coerced to a string. Anything else -- empty text, no JSON object, no ``pass``
field, two verdict objects that disagree, a non-boolean ``pass``, a bool or
out-of-range ``score`` -- raises ``JudgeOutputError``. So ``_scripted_judge``
below emits ``{"pass": ..., "score": ..., "reason": ...}`` for a verdict and a
brace-free sentence when a parse failure is wanted. ``test_the_scripted_judge_is
_shaped_the_way_rigor_parses`` pins that reading against a real ``PinnedJudge``.

The CRLF claim for ``hash_rubric_file`` is likewise verified rather than assumed:
:class:`TestRubricHashNormalisation` checks it against a digest ``hashlib``
computed here, in both directions.

Everything is offline and keyless: judges run on ``opik_rigor.FakeAdapter`` (or a
second adapter class defined here) with model ids that ``opik_rigor.is_pinned``
accepts -- asserted, not assumed, in :class:`TestPinnedFixtures`. Nothing draws
from an RNG: every scripted response is a pure function of the prompt, so there is
no seed to set and no sleep anywhere in the file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from opik_rigor import EvidenceLog, FakeAdapter, ModelPinError, PinnedJudge, is_pinned
from opik_rigor.judge import SCORE_MIN, hash_rubric_file

from migration_kit.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    EVENT_JUDGING_COMPLETED,
    Completion,
    RunHeader,
)
from migration_kit.errors import (
    ArtifactError,
    ConfigError,
    JudgeConfigError,
    JudgeReliabilityError,
)
from migration_kit.goldenset import GoldenSet
from migration_kit.judging import (
    JudgeConfig,
    JudgedArtifact,
    JudgePanel,
    JudgeSpec,
    Thresholds,
    judge_artifact,
    judged_path_for,
)
from migration_kit.runner import RunArtifact

# --------------------------------------------------------------------------- #
# Constants taken from the contract, not from the implementation.
# --------------------------------------------------------------------------- #

#: The six defaults, quoted from the TOML block in session-2-contract.md §1.
CONTRACT_DEFAULTS = {
    "pass_rate_floor": 0.90,
    "alpha": 0.05,
    "confidence": 0.95,
    "judge_failure_tolerance": 0.05,
    "min_detectable_effect": 0.10,
    "power_target": 0.80,
}

#: Contract §0: rigor's judge scale is 1.0-5.0. An imputed failure sits at the
#: bottom of it.
CONTRACT_SCORE_MIN = 1.0

#: contracts.py names this constant; the contract and the brief name the string.
CONTRACT_JUDGING_EVENT = "migkit.judging_completed"

JUDGE_MODEL = "fake-judge-v1"
OTHER_JUDGE_MODEL = "fake-judge-v2"
UNPINNED_MODEL = "fake-judge-latest"
CANDIDATE_MODEL = "fake-candidate-v1"

RUBRIC_TEXT = "Pass the response if it answers the question asked.\n"
OTHER_RUBRIC_TEXT = "Pass the response only if it also cites a source.\n"

#: Outputs the scripted judge recognises. The prefix, not the whole string, is
#: what selects the response, so a test can make an output legible and still
#: control its verdict.
GOOD = "good: a complete and sourced answer"
BAD = "bad: an answer that misses the point"
GARBAGE = "garbage: an answer the judge cannot grade in JSON"
NOSCORE = "noscore: clearly fine, but unscoreable"

#: Markers from rigor's PROMPT_TEMPLATE (opik_rigor/judge.py), used to recover the
#: graded output from the prompt. Pinned by a test rather than trusted.
_OUTPUT_START = "=== MODEL OUTPUT UNDER EVALUATION ==="
_OUTPUT_END = "=== END MODEL OUTPUT ==="


# --------------------------------------------------------------------------- #
# Helpers: independent hashing, scripted judges, adapters, fixtures on disk.
# --------------------------------------------------------------------------- #


def _sha256_lf(data: bytes) -> str:
    """The newline-normalised sha256 rule, with stdlib hashlib alone.

    contracts.py states the convention and rigor's ``hash_rubric_text`` states the
    same one for rubrics. This is the independent oracle for both.
    """
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _graded_output(prompt: str) -> str:
    """The model output rigor's prompt template put under evaluation."""
    body = prompt.split(_OUTPUT_START, 1)[1]
    return body.split(_OUTPUT_END, 1)[0].strip()


def _scripted_judge(prompt: str) -> str:
    """A judge whose verdict is a pure function of the output it is shown."""
    output = _graded_output(prompt)
    if output.startswith("bad"):
        return '{"pass": false, "score": 2, "reason": "misses the rubric"}'
    if output.startswith("garbage"):
        return "I will not answer in the requested form."
    if output.startswith("noscore"):
        return '{"pass": true, "score": null, "reason": "no basis to score"}'
    return '{"pass": true, "score": 5, "reason": "meets the rubric"}'


def _always_good(prompt: str) -> str:  # noqa: ARG001 - signature is the contract
    return '{"pass": true, "score": 4, "reason": "fine"}'


class _ScriptedAdapter:
    """A second adapter class, so "adapter class" is a testable hash dimension."""

    def __init__(self, *, model_id: str, responses) -> None:
        self.model_id = model_id
        self._responses = responses
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._responses(prompt)


class _CountingResponses:
    """A response callable that records every prompt a judge actually sent.

    Deliberately a wrapper *inside* ``FakeAdapter`` rather than a proxy around it.
    The adapter class name is part of ``judges_hash`` by design -- two adapters
    pointed at one model id are two instruments -- so proxying the adapter would
    make a resumed pass read as a different panel and be refused. Wrapping the
    script leaves the instrument's identity untouched.

    It is a secondary oracle in any case: the primary proof that a resumed pass
    did not re-grade is the count of rigor's own ``judge.verdict`` records, since
    ``PinnedJudge.evaluate`` writes exactly one per call.
    """

    def __init__(self, inner=_scripted_judge) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._inner(prompt)

    @property
    def graded_outputs(self) -> list[str]:
        return [_graded_output(prompt) for prompt in self.calls]


def _adapter_for(responses=_scripted_judge, cls=FakeAdapter):
    """A ``adapter_for`` callable for :meth:`JudgeConfig.build`."""

    def make(spec):
        return cls(model_id=spec.model, responses=responses)

    return make


def _counting_responses_for(record, inner=_scripted_judge):
    """Like :func:`_adapter_for`, with a per-judge call counter inside the script.

    The adapter is a plain ``FakeAdapter`` in every case, so a panel built this
    way hashes identically to one built by :func:`_adapter_for` and may resume it.
    """

    def make(spec):
        counter = _CountingResponses(inner)
        record[spec.name] = counter
        return FakeAdapter(model_id=spec.model, responses=counter)

    return make


def _log(tmp_path, name: str = "evidence.jsonl") -> EvidenceLog:
    return EvidenceLog(Path(tmp_path) / name)


def _verdicts_recorded(log: EvidenceLog) -> int:
    """How many times rigor recorded a judge verdict: one per ``evaluate`` call.

    The oracle for "a resumed pass did not re-grade", and it comes from the
    dependency rather than from the code under test.
    """
    return len(_payloads(log, "judge.verdict"))


def _payloads(log: EvidenceLog, event_type: str) -> list[dict]:
    return [record.payload for record in log.read() if record.event_type == event_type]


def _write_rubric(directory, *, name="rubric.md", text=RUBRIC_TEXT, newline="\n") -> Path:
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def _write_config(directory, text: str, *, name="judges.toml") -> Path:
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _toml(judges, thresholds=None) -> str:
    """The contract's config shape, rendered by hand so the TOML is visible."""
    lines: list[str] = []
    for judge in judges:
        lines.append("[[judge]]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in judge.items())
        lines.append("")
    if thresholds is not None:
        lines.append("[thresholds]")
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in thresholds.items())
    return "\n".join(lines) + "\n"


def _config_path(tmp_path, judges=None, *, thresholds=None, dirname="cfg", newline="\n") -> Path:
    """Write a config directory (rubrics beside it) and return the TOML path."""
    root = Path(tmp_path) / dirname
    entries = []
    for judge in judges if judges is not None else [{"name": "helpfulness"}]:
        rel = f"rubrics/{judge['name']}.md"
        _write_rubric(root, name=rel, text=judge.get("rubric_text", RUBRIC_TEXT), newline=newline)
        entries.append(
            {
                "name": judge["name"],
                "model": judge.get("model", JUDGE_MODEL),
                "rubric": judge.get("rubric", rel),
            }
        )
    return _write_config(root, _toml(entries, thresholds))


def _two_judges():
    """Two distinct judges, used wherever a panel of more than one is needed."""
    return [{"name": "helpfulness"}, {"name": "safety", "rubric_text": OTHER_RUBRIC_TEXT}]


def _config(tmp_path, judges=None, **kwargs) -> JudgeConfig:
    return JudgeConfig.load(_config_path(tmp_path, judges, **kwargs))


def _identity(name, model=JUDGE_MODEL, adapter_class="FakeAdapter", rubric_hash="0" * 64) -> dict:
    return {
        "name": name,
        "model": model,
        "adapter_class": adapter_class,
        "rubric_hash": rubric_hash,
    }


def _bare_panel(identities) -> JudgePanel:
    """A panel with identities only: ``judges_hash`` reads nothing else."""
    return JudgePanel(
        judges=(), specs=(), identities=tuple(identities), thresholds=Thresholds()
    )


def _write_goldenset(tmp_path, ids, *, name="set.jsonl") -> GoldenSet:
    path = Path(tmp_path) / name
    lines = [json.dumps({"id": item_id, "input": f"q-{item_id}"}) for item_id in ids]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return GoldenSet.load(path)


def _completions(goldenset, n, output_for=lambda item_id, index: GOOD):
    """``n`` draws per item. ``output_for`` returning ``None`` means the call failed."""
    out: list[Completion] = []
    for item in goldenset:
        for index in range(n):
            text = output_for(item.id, index)
            if text is None:
                out.append(
                    Completion(
                        item_id=item.id,
                        sample_index=index,
                        output=None,
                        duration=0.02,
                        error="SampleTimeout: provider did not answer",
                        error_type="SampleTimeout",
                    )
                )
            else:
                out.append(
                    Completion(
                        item_id=item.id, sample_index=index, output=text, duration=0.01
                    )
                )
    return out


def _artifact(goldenset, completions, *, model_id=CANDIDATE_MODEL, n=2, path="run.jsonl"):
    """A :class:`RunArtifact` built in memory, so judging's input is hand-made."""
    header = RunHeader(
        model_id=model_id,
        goldenset_hash=goldenset.hash,
        goldenset_path=goldenset.path,
        n_per_item=n,
        created="2026-01-01T00:00:00.000000+00:00",
        adapter="FakeAdapter",
        notes={"goldenset_items": len(goldenset)},
    )
    return RunArtifact(header=header, completions=tuple(completions), path=path)


def _simple_run(tmp_path, ids=("i1", "i2"), n=2, output_for=lambda item_id, index: GOOD):
    goldenset = _write_goldenset(tmp_path, ids)
    return goldenset, _artifact(goldenset, _completions(goldenset, n, output_for), n=n)


def _keys(artifact: JudgedArtifact) -> list[tuple[str, str, int]]:
    return [record.key for record in artifact.records]


def _header_line(**overrides) -> str:
    record = {
        "record": "header",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": CANDIDATE_MODEL,
        "goldenset_hash": "a" * 64,
        "judges_hash": "b" * 64,
        "judges": [_identity("helpfulness")],
        "n_per_item": 2,
        "source": "run.jsonl",
        "created": "2026-01-01T00:00:00.000000+00:00",
        "notes": {},
    }
    record.update(overrides)
    return json.dumps(record)


def _verdict_line(judge="helpfulness", item_id="i1", sample_index=0, **overrides) -> str:
    record = {
        "record": "verdict",
        "judge": judge,
        "item_id": item_id,
        "sample_index": sample_index,
        "passed": True,
        "score": 5.0,
        "imputed": False,
        "parse_failure": False,
        "reason": None,
        "error": None,
    }
    record.update(overrides)
    return json.dumps(record)


def _write_lines(path, lines) -> None:
    path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))


# --------------------------------------------------------------------------- #


class TestPinnedFixtures:
    """Grounds the fixture model ids against rigor's own definition of pinned."""

    @pytest.mark.parametrize("model_id", [JUDGE_MODEL, OTHER_JUDGE_MODEL, CANDIDATE_MODEL])
    def test_the_fixture_ids_qualify_as_pinned(self, model_id):
        assert is_pinned(model_id) is True

    def test_the_alias_fixture_does_not_qualify(self):
        assert is_pinned(UNPINNED_MODEL) is False


class TestScriptedJudgeShape:
    """Pins the reading of ``opik_rigor.judge`` that the whole file rests on."""

    def test_the_scripted_judge_is_shaped_the_way_rigor_parses(self, tmp_path):
        rubric = _write_rubric(tmp_path)
        adapter = FakeAdapter(model_id=JUDGE_MODEL, responses=_scripted_judge)
        judge = PinnedJudge(adapter, rubric, _log(tmp_path), name="probe")

        # The helper really does recover the graded output from rigor's template.
        assert _graded_output(judge.build_prompt("the question", GOOD)) == GOOD

        verdict = judge.evaluate("the question", GOOD)
        assert (verdict.passed, verdict.score) == (True, 5.0)
        assert judge.evaluate("the question", BAD).passed is False
        # Contract §0: a null score is normal output, not a malformed response.
        noscore = judge.evaluate("the question", NOSCORE)
        assert (noscore.passed, noscore.score) == (True, None)

    def test_the_garbage_output_makes_rigor_raise_judgeoutputerror(self, tmp_path):
        from opik_rigor import JudgeOutputError

        rubric = _write_rubric(tmp_path)
        adapter = FakeAdapter(model_id=JUDGE_MODEL, responses=_scripted_judge)
        judge = PinnedJudge(adapter, rubric, _log(tmp_path), name="probe")
        with pytest.raises(JudgeOutputError):
            judge.evaluate("the question", GARBAGE)


class TestThresholdDefaults:
    """Contract §1: the six defaults, quoted exactly."""

    def test_every_default_matches_the_contract(self):
        thresholds = Thresholds()
        assert {
            "pass_rate_floor": thresholds.pass_rate_floor,
            "alpha": thresholds.alpha,
            "confidence": thresholds.confidence,
            "judge_failure_tolerance": thresholds.judge_failure_tolerance,
            "min_detectable_effect": thresholds.min_detectable_effect,
            "power_target": thresholds.power_target,
        } == CONTRACT_DEFAULTS

    def test_to_dict_echoes_all_six(self):
        # Contract §1: thresholds are echoed into the report separately, so a
        # loosened gate still shows in the evidence. That needs all six.
        assert Thresholds().to_dict() == CONTRACT_DEFAULTS

    def test_a_configured_value_replaces_only_its_own_default(self):
        thresholds = Thresholds(pass_rate_floor=0.75)
        assert thresholds.pass_rate_floor == 0.75
        assert thresholds.alpha == CONTRACT_DEFAULTS["alpha"]
        assert thresholds.confidence == CONTRACT_DEFAULTS["confidence"]


class TestThresholdValidation:
    """Contract §1: a threshold outside its range is a ``ConfigError``."""

    @pytest.mark.parametrize("name", sorted(CONTRACT_DEFAULTS))
    @pytest.mark.parametrize("value", [-0.01, -1.0, 1.01, 2.0])
    def test_outside_zero_to_one_is_refused(self, name, value):
        # Every one of the six is a probability, a rate or a proportion, so
        # nothing outside [0, 1] can mean anything.
        with pytest.raises(ConfigError) as exc:
            Thresholds(**{name: value})
        assert name in str(exc.value)

    @pytest.mark.parametrize("name", sorted(CONTRACT_DEFAULTS))
    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_refused(self, name, value):
        # bool is an int subclass, so True would otherwise read as the rate 1.0 --
        # the same trap rigor's own score parser calls out.
        with pytest.raises(ConfigError) as exc:
            Thresholds(**{name: value})
        assert name in str(exc.value)

    @pytest.mark.parametrize("name", sorted(CONTRACT_DEFAULTS))
    @pytest.mark.parametrize("value", ["0.9", None, [0.9]])
    def test_a_non_number_is_refused(self, name, value):
        with pytest.raises(ConfigError) as exc:
            Thresholds(**{name: value})
        assert name in str(exc.value)

    @pytest.mark.parametrize("name", ["alpha", "confidence", "power_target"])
    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_a_degenerate_statistical_setting_is_refused(self, name, value):
        # alpha=0 can never reject; alpha=1 always rejects; confidence=1 is an
        # infinite interval; power=1 needs infinite n. None of the four is a
        # setting, so the open interval is the only defensible range.
        with pytest.raises(ConfigError):
            Thresholds(**{name: value})

    def test_an_undetectable_effect_size_is_refused(self):
        # A minimum detectable effect of zero asks for infinite n.
        with pytest.raises(ConfigError):
            Thresholds(min_detectable_effect=0.0)

    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_the_pass_rate_floor_may_sit_on_either_end(self, value):
        # 0.0 accepts anything and 1.0 demands perfection: both are meaningful
        # gates, unlike alpha=0, so the floor's range is closed.
        assert Thresholds(pass_rate_floor=value).pass_rate_floor == value

    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_the_judge_failure_tolerance_may_sit_on_either_end(self, value):
        # 0.0 tolerates no parse failure at all; 1.0 tolerates every one.
        assert Thresholds(judge_failure_tolerance=value).judge_failure_tolerance == value


class TestConfigLoad:
    """Contract §1: the TOML file, and what a valid one produces."""

    def test_the_contract_shaped_config_loads(self, tmp_path):
        path = _config_path(
            tmp_path,
            [{"name": "helpfulness", "model": JUDGE_MODEL}],
            thresholds=dict(CONTRACT_DEFAULTS),
        )
        config = JudgeConfig.load(path)
        assert [spec.name for spec in config.specs] == ["helpfulness"]
        assert [spec.model for spec in config.specs] == [JUDGE_MODEL]
        assert config.thresholds.to_dict() == CONTRACT_DEFAULTS
        assert config.path == str(path)

    def test_thresholds_default_when_the_table_is_absent(self, tmp_path):
        config = _config(tmp_path)
        assert config.thresholds.to_dict() == CONTRACT_DEFAULTS

    def test_a_declared_threshold_overrides_its_default(self, tmp_path):
        config = _config(tmp_path, thresholds={"pass_rate_floor": 0.5, "alpha": 0.01})
        assert config.thresholds.pass_rate_floor == 0.5
        assert config.thresholds.alpha == 0.01
        assert config.thresholds.confidence == CONTRACT_DEFAULTS["confidence"]

    def test_an_out_of_range_threshold_in_the_file_is_a_configerror(self, tmp_path):
        path = _config_path(tmp_path, thresholds={"alpha": 1.5})
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_the_rubric_hash_is_the_newline_normalised_sha256(self, tmp_path):
        config = _config(tmp_path)
        assert config.specs[0].rubric_hash == _sha256_lf(RUBRIC_TEXT.encode("utf-8"))

    def test_several_judges_keep_their_declaration_order_in_specs(self, tmp_path):
        config = _config(
            tmp_path, [{"name": "helpfulness"}, {"name": "safety"}, {"name": "grounding"}]
        )
        assert [spec.name for spec in config.specs] == ["helpfulness", "safety", "grounding"]

    def test_a_missing_file_is_a_configerror(self, tmp_path):
        with pytest.raises(ConfigError):
            JudgeConfig.load(tmp_path / "absent.toml")

    def test_invalid_toml_is_a_configerror(self, tmp_path):
        path = _write_config(tmp_path, "[[judge]\nname = 'x'\n")
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)


class TestConfigRubricResolution:
    """Contract §1: rubric paths resolve relative to the config file."""

    def test_a_relative_rubric_resolves_beside_the_config(self, tmp_path):
        path = _config_path(tmp_path, dirname="nested/cfg")
        config = JudgeConfig.load(path)
        assert config.specs[0].rubric == path.parent / "rubrics" / "helpfulness.md"

    def test_resolution_does_not_follow_a_same_named_file_elsewhere(self, tmp_path):
        # A decoy at the repo root: if resolution ever keyed on the process's
        # working directory instead of the config's, the hash would move.
        _write_rubric(tmp_path / "rubrics", name="helpfulness.md", text=OTHER_RUBRIC_TEXT)
        config = JudgeConfig.load(_config_path(tmp_path, dirname="nested/cfg"))
        assert config.specs[0].rubric_hash == _sha256_lf(RUBRIC_TEXT.encode("utf-8"))

    def test_an_absolute_rubric_path_is_used_as_written(self, tmp_path):
        rubric = _write_rubric(tmp_path / "shared", name="abs.md", text=OTHER_RUBRIC_TEXT)
        path = _config_path(
            tmp_path,
            [{"name": "helpfulness", "rubric": rubric.as_posix()}],
            dirname="cfg",
        )
        config = JudgeConfig.load(path)
        assert config.specs[0].rubric == rubric
        assert config.specs[0].rubric_hash == _sha256_lf(OTHER_RUBRIC_TEXT.encode("utf-8"))

    def test_a_missing_rubric_is_a_configerror(self, tmp_path):
        path = _config_path(tmp_path, [{"name": "helpfulness", "rubric": "rubrics/gone.md"}])
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "rubric" in str(exc.value)

    def test_a_directory_where_a_rubric_should_be_is_a_configerror(self, tmp_path):
        root = Path(tmp_path) / "cfg"
        (root / "rubrics" / "helpfulness.md").mkdir(parents=True)
        path = _write_config(
            root, _toml([{"name": "h", "model": JUDGE_MODEL, "rubric": "rubrics/helpfulness.md"}])
        )
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)


class TestConfigRejection:
    """Contract §1: the loader is strict, and every rejection is a ``ConfigError``."""

    def test_an_unknown_top_level_key(self, tmp_path):
        text = 'verbose = true\n' + _toml([{"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"}])
        _write_rubric(tmp_path / "cfg", name="r.md")
        path = _write_config(tmp_path / "cfg", text)
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "verbose" in str(exc.value)

    def test_an_unknown_judge_key(self, tmp_path):
        path = _config_path(tmp_path, [{"name": "h"}])
        path.write_text(path.read_text(encoding="utf-8") + 'weight = 2\n', encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "weight" in str(exc.value)

    def test_an_unknown_threshold_key(self, tmp_path):
        path = _config_path(tmp_path, thresholds={"pass_rate_flooor": 0.9})
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "pass_rate_flooor" in str(exc.value)

    def test_no_judge_table_at_all(self, tmp_path):
        path = _write_config(tmp_path, "[thresholds]\nalpha = 0.05\n")
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_an_empty_judge_array(self, tmp_path):
        path = _write_config(tmp_path, "judge = []\n")
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_a_judge_that_is_not_a_table(self, tmp_path):
        path = _write_config(tmp_path, 'judge = ["helpfulness"]\n')
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "table" in str(exc.value)

    def test_a_thresholds_value_that_is_not_a_table(self, tmp_path):
        text = "thresholds = 5\n" + _toml([{"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"}])
        _write_rubric(tmp_path / "cfg", name="r.md")
        path = _write_config(tmp_path / "cfg", text)
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    @pytest.mark.parametrize("key", ["name", "model", "rubric"])
    def test_a_missing_required_field(self, tmp_path, key):
        entry = {"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"}
        entry.pop(key)
        _write_rubric(tmp_path / "cfg", name="r.md")
        path = _write_config(tmp_path / "cfg", _toml([entry]))
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert key in str(exc.value)

    @pytest.mark.parametrize("key", ["name", "model", "rubric"])
    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_required_field(self, tmp_path, key, value):
        entry = {"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"}
        entry[key] = value
        _write_rubric(tmp_path / "cfg", name="r.md")
        path = _write_config(tmp_path / "cfg", _toml([entry]))
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert key in str(exc.value)

    @pytest.mark.parametrize("key", ["name", "model", "rubric"])
    def test_a_required_field_of_the_wrong_type(self, tmp_path, key):
        entry = {"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"}
        entry[key] = 7
        _write_rubric(tmp_path / "cfg", name="r.md")
        path = _write_config(tmp_path / "cfg", _toml([entry]))
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_two_judges_sharing_a_name(self, tmp_path):
        # Contract §1: the name keys the judges hash, the resume key and rigor's
        # rubric-drift lookup. A duplicate makes all three wrong at once.
        path = _config_path(
            tmp_path,
            [
                {"name": "helpfulness"},
                {
                    "name": "helpfulness",
                    "model": OTHER_JUDGE_MODEL,
                    "rubric_text": OTHER_RUBRIC_TEXT,
                },
            ],
        )
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(path)
        assert "helpfulness" in str(exc.value)

    def test_two_judges_sharing_a_name_and_everything_else(self, tmp_path):
        path = _write_config(
            tmp_path / "cfg",
            _toml(
                [
                    {"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"},
                    {"name": "h", "model": JUDGE_MODEL, "rubric": "r.md"},
                ]
            ),
        )
        _write_rubric(tmp_path / "cfg", name="r.md")
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_distinct_names_are_fine(self, tmp_path):
        config = _config(tmp_path, [{"name": "helpfulness"}, {"name": "safety"}])
        assert len(config.specs) == 2


class TestUnpinnedModel:
    """Contract §1: "an unpinned model id" is one of the loader's rejections.

    There are two lines of defence and both are asserted. Contract §1 puts the
    refusal at ``JudgeConfig.load``; contract §0 records that rigor refuses it
    again at ``PinnedJudge`` construction, which catches an adapter whose model id
    never came from the config at all.
    """

    @pytest.mark.parametrize(
        "model_id",
        ["fake-judge-latest", "claude-3-5-sonnet-latest", "gpt-4o", "claude", ""],
    )
    def test_an_unpinned_model_id_is_refused_by_the_loader(self, tmp_path, model_id):
        path = _config_path(tmp_path, [{"name": "helpfulness", "model": model_id}])
        with pytest.raises(ConfigError):
            JudgeConfig.load(path)

    def test_a_pinned_model_id_is_accepted_by_the_loader(self, tmp_path):
        config = _config(tmp_path, [{"name": "helpfulness", "model": JUDGE_MODEL}])
        assert config.specs[0].model == JUDGE_MODEL

    def test_an_unpinned_adapter_is_refused_at_panel_construction(self, tmp_path):
        # Contract §0: construction calls require_pinned on the *judge's* model id,
        # so an alias is refused at construction rather than at analysis time. The
        # config is assembled directly here, around the loader, because the adapter
        # is supplied by the caller and need not carry the declared model string.
        rubric = _write_rubric(tmp_path)
        config = JudgeConfig(
            specs=(
                JudgeSpec(
                    name="helpfulness",
                    model=JUDGE_MODEL,
                    rubric=rubric,
                    rubric_hash=_sha256_lf(RUBRIC_TEXT.encode("utf-8")),
                ),
            ),
            thresholds=Thresholds(),
            path=str(tmp_path / "judges.toml"),
        )
        adapter_for = _adapter_for()

        def unpinned_adapter_for(spec):  # noqa: ARG001 - the alias is the point
            return FakeAdapter(model_id=UNPINNED_MODEL, responses=_scripted_judge)

        assert config.build(_log(tmp_path, "ok.jsonl"), adapter_for).judges[0].name == "helpfulness"
        with pytest.raises(ModelPinError):
            config.build(_log(tmp_path, "bad.jsonl"), unpinned_adapter_for)


class TestRubricHashNormalisation:
    """Verifies rigor's CRLF claim rather than assuming it."""

    def test_hash_rubric_file_normalises_crlf_to_lf(self, tmp_path):
        lf = _write_rubric(tmp_path, name="lf.md", newline="\n")
        crlf = _write_rubric(tmp_path, name="crlf.md", newline="\r\n")
        assert lf.read_bytes() != crlf.read_bytes()
        expected = _sha256_lf(RUBRIC_TEXT.encode("utf-8"))
        assert hash_rubric_file(lf) == expected
        assert hash_rubric_file(crlf) == expected

    def test_a_real_content_change_still_moves_the_hash(self, tmp_path):
        one = _write_rubric(tmp_path, name="one.md", text=RUBRIC_TEXT)
        two = _write_rubric(tmp_path, name="two.md", text=OTHER_RUBRIC_TEXT)
        assert hash_rubric_file(one) != hash_rubric_file(two)


class TestJudgesHash:
    """Contract §1: what the instrument's identity does and does not cover."""

    def _panel(
        self, tmp_path, judges=None, *, dirname, thresholds=None, newline="\n", cls=FakeAdapter
    ):
        config = _config(
            tmp_path, judges, thresholds=thresholds, dirname=dirname, newline=newline
        )
        return config.build(_log(tmp_path, f"{dirname}.jsonl"), _adapter_for(cls=cls))

    def test_the_hash_is_a_sha256_hex_digest(self, tmp_path):
        panel = self._panel(tmp_path, dirname="a")
        assert len(panel.judges_hash) == 64
        assert set(panel.judges_hash) <= set("0123456789abcdef")

    def test_two_identical_panels_hash_equal(self, tmp_path):
        left = self._panel(tmp_path, dirname="a")
        right = self._panel(tmp_path, dirname="b")
        assert left.judges_hash == right.judges_hash

    def test_a_changed_judge_name_changes_the_hash(self, tmp_path):
        left = self._panel(tmp_path, [{"name": "helpfulness"}], dirname="a")
        right = self._panel(tmp_path, [{"name": "usefulness"}], dirname="b")
        assert left.judges_hash != right.judges_hash

    def test_a_changed_model_changes_the_hash(self, tmp_path):
        left = self._panel(tmp_path, [{"name": "h", "model": JUDGE_MODEL}], dirname="a")
        right = self._panel(tmp_path, [{"name": "h", "model": OTHER_JUDGE_MODEL}], dirname="b")
        assert left.judges_hash != right.judges_hash

    def test_changed_rubric_content_changes_the_hash(self, tmp_path):
        left = self._panel(tmp_path, [{"name": "h", "rubric_text": RUBRIC_TEXT}], dirname="a")
        right = self._panel(
            tmp_path, [{"name": "h", "rubric_text": OTHER_RUBRIC_TEXT}], dirname="b"
        )
        assert left.judges_hash != right.judges_hash

    def test_a_changed_adapter_class_changes_the_hash(self, tmp_path):
        # Contract §1: AnthropicAdapter and OpenAICompatAdapter pointed at one
        # model id are two different instruments and must not hash equal.
        left = self._panel(tmp_path, dirname="a", cls=FakeAdapter)
        right = self._panel(tmp_path, dirname="b", cls=_ScriptedAdapter)
        assert left.judges_hash != right.judges_hash

    def test_changing_only_thresholds_leaves_the_hash_alone(self, tmp_path):
        # Thresholds change what the verdict concludes, not what was measured.
        left = self._panel(tmp_path, dirname="a", thresholds=dict(CONTRACT_DEFAULTS))
        right = self._panel(
            tmp_path,
            dirname="b",
            thresholds={"pass_rate_floor": 0.5, "alpha": 0.2, "power_target": 0.5},
        )
        assert left.judges_hash == right.judges_hash
        assert left.thresholds.to_dict() != right.thresholds.to_dict()

    def test_judge_declaration_order_leaves_the_hash_alone(self, tmp_path):
        forward = self._panel(
            tmp_path,
            [{"name": "helpfulness"}, {"name": "safety", "rubric_text": OTHER_RUBRIC_TEXT}],
            dirname="a",
        )
        reverse = self._panel(
            tmp_path,
            [{"name": "safety", "rubric_text": OTHER_RUBRIC_TEXT}, {"name": "helpfulness"}],
            dirname="b",
        )
        assert forward.named() == ("helpfulness", "safety")
        assert reverse.named() == ("safety", "helpfulness")
        assert forward.judges_hash == reverse.judges_hash

    def test_ordering_uses_the_whole_identity_not_just_the_name(self, tmp_path):
        # Two entries sharing a name are unreachable through the loader, but a
        # name-only sort is stable and would leave them in input order -- so this
        # is the case that distinguishes "sorted by name" from "sorted by the
        # full tuple", which is what the contract specifies.
        one = _identity("same", model=JUDGE_MODEL)
        two = _identity("same", model=OTHER_JUDGE_MODEL)
        assert _bare_panel([one, two]).judges_hash == _bare_panel([two, one]).judges_hash

    def test_a_second_judge_changes_the_hash(self, tmp_path):
        left = self._panel(tmp_path, [{"name": "helpfulness"}], dirname="a")
        right = self._panel(
            tmp_path,
            [{"name": "helpfulness"}, {"name": "safety", "rubric_text": OTHER_RUBRIC_TEXT}],
            dirname="b",
        )
        assert left.judges_hash != right.judges_hash

    def test_the_panel_carries_one_judge_per_spec(self, tmp_path):
        panel = self._panel(
            tmp_path,
            [{"name": "helpfulness"}, {"name": "safety", "rubric_text": OTHER_RUBRIC_TEXT}],
            dirname="a",
        )
        assert len(panel.judges) == 2
        assert [judge.name for judge in panel.judges] == ["helpfulness", "safety"]
        assert panel.named() == ("helpfulness", "safety")


class TestJudgeArtifactGuards:
    """Contract §1: the golden set a judge grades against must be the run's."""

    def test_a_different_goldenset_hash_is_an_artifacterror(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path)
        other = _write_goldenset(tmp_path, ["z1", "z2"], name="other.jsonl")
        assert other.hash != goldenset.hash
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        with pytest.raises(ArtifactError):
            judge_artifact(
                artifact, other, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
            )

    def test_nothing_is_written_when_the_goldenset_is_wrong(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path)
        other = _write_goldenset(tmp_path, ["z1"], name="other.jsonl")
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        target = tmp_path / "j.jsonl"
        with pytest.raises(ArtifactError):
            judge_artifact(artifact, other, panel, evidence=_log(tmp_path), judged=target)
        assert not target.exists()

    def test_the_judged_path_is_derived_from_the_run_artifact(self, tmp_path):
        _, artifact = _simple_run(tmp_path)
        out = tmp_path / "judged"
        assert judged_path_for(artifact, out) == out / "run.judged.jsonl"


class TestJudgeArtifactGrading:
    """Contract §1: every completion, every judge, one record each."""

    def test_every_completion_is_graded_by_every_judge(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1", "i2", "i3"), n=2)
        panel = _config(
            tmp_path, _two_judges()
        ).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        assert len(judged.records) == 12  # 3 items x 2 draws x 2 judges
        assert sorted(judged.judge_names()) == ["helpfulness", "safety"]
        assert judged.coverage() == {
            ("helpfulness", "i1"): 2,
            ("helpfulness", "i2"): 2,
            ("helpfulness", "i3"): 2,
            ("safety", "i1"): 2,
            ("safety", "i2"): 2,
            ("safety", "i3"): 2,
        }

    def test_a_passing_verdict_carries_score_and_reason(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        record = judged.records[0]
        assert (record.passed, record.score) == (True, 5.0)
        assert record.reason == "meets the rubric"
        assert (record.imputed, record.parse_failure) == (False, False)

    def test_a_failing_verdict_is_recorded_as_a_model_failure(self, tmp_path):
        goldenset, artifact = _simple_run(
            tmp_path, ids=("i1",), n=1, output_for=lambda item_id, index: BAD
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        record = judged.records[0]
        assert (record.passed, record.score) == (False, 2.0)
        # Not imputed and not a parse failure: the judge answered, and said no.
        assert (record.imputed, record.parse_failure) == (False, False)

    def test_a_null_score_is_normal_output_not_a_parse_failure(self, tmp_path):
        # Contract §0: rigor's prompt instructs "score": null when the judge
        # cannot score, so None is a verdict, not a malformed response.
        goldenset, artifact = _simple_run(
            tmp_path, ids=("i1",), n=1, output_for=lambda item_id, index: NOSCORE
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        record = judged.records[0]
        assert (record.passed, record.score) == (True, None)
        assert (record.imputed, record.parse_failure) == (False, False)

    def test_the_judge_is_shown_the_goldenset_input_and_the_completion(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        proxies: dict = {}
        panel = _config(tmp_path).build(_log(tmp_path), _counting_responses_for(proxies))
        judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        prompt = proxies["helpfulness"].calls[0]
        assert "q-i1" in prompt
        assert _graded_output(prompt) == GOOD


class TestFailedCompletionsAreKept:
    """Build-plan §6, the single most important behaviour in the module.

    A failed completion is graded ``passed=False`` at ``SCORE_MIN`` with
    ``imputed=True`` and is kept. Dropping it lets a model that crashes outscore a
    model that answers badly; passing ``None`` through aborts the comparison,
    because rigor rejects ``None`` in a score array.
    """

    @pytest.fixture
    def judged(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        completions = _completions(
            goldenset, 2, lambda item_id, index: None if item_id == "i2" else GOOD
        )
        artifact = _artifact(goldenset, completions)
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        return judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )

    def test_the_contract_puts_the_floor_at_one(self):
        # Contract §0, quoted: judge.SCORE_MIN == 1.0.
        assert SCORE_MIN == CONTRACT_SCORE_MIN

    def test_the_failed_completions_are_still_there(self, judged):
        assert len(judged.records) == 4
        assert sorted(_keys(judged)) == [
            ("helpfulness", "i1", 0),
            ("helpfulness", "i1", 1),
            ("helpfulness", "i2", 0),
            ("helpfulness", "i2", 1),
        ]

    def test_a_failed_completion_is_graded_as_a_failure_at_the_floor(self, judged):
        failed = [record for record in judged.records if record.item_id == "i2"]
        assert len(failed) == 2
        for record in failed:
            assert record.passed is False
            assert record.score == CONTRACT_SCORE_MIN
            assert record.imputed is True
            assert record.parse_failure is False

    def test_an_imputed_score_is_never_none(self, judged):
        # rigor rejects None in a score array, so one failure would otherwise
        # abort the whole comparison.
        assert all(record.score is not None for record in judged.records if record.imputed)

    def test_the_imputation_is_disclosed_on_the_record(self, judged):
        failed = next(record for record in judged.records if record.imputed)
        assert failed.error is not None
        assert "SampleTimeout" in failed.error

    def test_imputed_and_graded_records_are_counted_apart(self, judged):
        assert judged.stats()["imputed"] == 2
        assert judged.stats()["parse_failures"] == 0
        assert judged.stats()["records"] == 4

    def test_the_judge_is_never_asked_about_a_failed_completion(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        completions = _completions(
            goldenset, 2, lambda item_id, index: None if item_id == "i2" else GOOD
        )
        proxies: dict = {}
        panel = _config(tmp_path).build(_log(tmp_path), _counting_responses_for(proxies))
        judge_artifact(
            _artifact(goldenset, completions),
            goldenset,
            panel,
            evidence=_log(tmp_path),
            judged=tmp_path / "j.jsonl",
        )
        assert len(proxies["helpfulness"].calls) == 2
        assert proxies["helpfulness"].graded_outputs == [GOOD, GOOD]

    def test_an_output_of_none_without_an_error_is_still_imputed(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        artifact = _artifact(
            goldenset,
            [Completion(item_id="i1", sample_index=0, output=None, duration=0.01)],
            n=1,
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        assert judged.records[0].imputed is True
        assert judged.records[0].score == CONTRACT_SCORE_MIN

    def test_a_crasher_and_a_bad_answerer_are_not_told_apart_by_omission(self, tmp_path):
        # The regression the amendment exists for, at the judging level: both
        # candidates must yield the same number of score-bearing records, so the
        # crasher cannot win by having its failures vanish from the arrays.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4"])
        crasher = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: None if item_id == "i4" else GOOD),
        )
        answerer = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: BAD if item_id == "i4" else GOOD),
        )
        log = _log(tmp_path)
        panel_a = _config(tmp_path, dirname="a").build(log, _adapter_for())
        panel_b = _config(tmp_path, dirname="b").build(log, _adapter_for())
        left = judge_artifact(
            crasher, goldenset, panel_a, evidence=log, judged=tmp_path / "crasher.jsonl"
        )
        right = judge_artifact(
            answerer, goldenset, panel_b, evidence=log, judged=tmp_path / "answerer.jsonl"
        )
        assert len(left.records) == len(right.records) == 8
        assert sum(1 for r in left.records if r.score is None) == 0
        assert sum(1 for r in right.records if r.score is None) == 0
        assert sum(1 for r in left.records if r.passed) == sum(1 for r in right.records if r.passed)
        # And the crasher is not rewarded for the holes: its two imputed scores
        # sit at the floor, below the 2.0 the bad answerer actually earned.
        assert sum(r.score for r in left.records) <= sum(r.score for r in right.records)


class TestParseFailures:
    """Contract §1: an unparseable judge is an instrument failure, counted apart."""

    def test_a_parse_failure_is_marked_and_not_imputed(self, tmp_path):
        goldenset, artifact = _simple_run(
            tmp_path, ids=("i1",), n=1, output_for=lambda item_id, index: GARBAGE
        )
        panel = _config(
            tmp_path, thresholds={"judge_failure_tolerance": 1.0}
        ).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        record = judged.records[0]
        assert record.parse_failure is True
        assert record.imputed is False
        assert record.score is None
        assert record.passed is False
        assert record.error

    def test_parse_failures_and_imputations_are_counted_separately(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4"])

        def output_for(item_id, index):
            if item_id == "i1":
                return None
            if item_id == "i2":
                return GARBAGE
            return GOOD

        artifact = _artifact(goldenset, _completions(goldenset, 2, output_for))
        panel = _config(
            tmp_path, thresholds={"judge_failure_tolerance": 1.0}
        ).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        stats = judged.stats()
        assert stats["records"] == 8
        assert stats["imputed"] == 2
        assert stats["parse_failures"] == 2
        overlap = [r for r in judged.records if r.imputed and r.parse_failure]
        assert overlap == []

    def test_over_the_tolerance_aborts_with_the_true_count(self, tmp_path):
        # 5 items x 2 draws = 10 records, 2 of them unparseable: 20% against the
        # default 5% tolerance.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4", "i5"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: GARBAGE if item_id == "i5" else GOOD),
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        with pytest.raises(JudgeReliabilityError) as exc:
            judge_artifact(
                artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
            )
        assert exc.value.judge_name == "helpfulness"
        assert exc.value.failures == 2
        assert exc.value.total == 10
        assert exc.value.tolerance == CONTRACT_DEFAULTS["judge_failure_tolerance"]
        assert "2 of 10" in str(exc.value)
        assert "helpfulness" in str(exc.value)

    def test_the_pass_completes_before_it_raises(self, tmp_path):
        # Contract §1.3: the count in the message is the real one, not the count
        # at the moment the threshold was crossed -- which means every remaining
        # completion is judged and written first.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4", "i5"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: GARBAGE if item_id == "i1" else GOOD),
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        target = tmp_path / "j.jsonl"
        with pytest.raises(JudgeReliabilityError):
            judge_artifact(artifact, goldenset, panel, evidence=_log(tmp_path), judged=target)
        # The two unparseable draws are the *first* two; everything after them is
        # on disk, so the pass did not stop at the breach.
        written = JudgedArtifact.load(target)
        assert len(written.records) == 10
        assert sum(1 for record in written.records if record.parse_failure) == 2

    def test_exactly_at_the_tolerance_does_not_abort(self, tmp_path):
        # 1 unparseable in 20 is exactly 5%, and the contract says *over* the
        # tolerance aborts.
        goldenset = _write_goldenset(tmp_path, [f"i{k}" for k in range(1, 11)])
        artifact = _artifact(
            goldenset,
            _completions(
                goldenset,
                2,
                lambda item_id, index: GARBAGE if (item_id, index) == ("i10", 1) else GOOD,
            ),
        )
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        assert len(judged.records) == 20
        assert judged.stats()["parse_failures"] == 1

    def test_only_the_unreliable_judge_is_named(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4", "i5"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: GARBAGE if item_id == "i5" else GOOD),
        )
        # "safety" never chokes because its adapter always answers in form.
        config = _config(tmp_path, _two_judges())

        def adapter_for(spec):
            responses = _always_good if spec.name == "safety" else _scripted_judge
            return FakeAdapter(model_id=spec.model, responses=responses)

        panel = config.build(_log(tmp_path), adapter_for)
        target = tmp_path / "j.jsonl"
        with pytest.raises(JudgeReliabilityError) as exc:
            judge_artifact(artifact, goldenset, panel, evidence=_log(tmp_path), judged=target)
        assert exc.value.judge_name == "helpfulness"
        written = JudgedArtifact.load(target)
        assert len(written.for_judge("safety")) == 10
        assert sum(1 for r in written.for_judge("safety") if r.parse_failure) == 0

    def test_an_imputed_record_does_not_count_towards_the_tolerance(self, tmp_path):
        # Every draw fails at the provider, so every record is imputed. That is a
        # model problem, not an instrument problem, and must not abort.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        artifact = _artifact(goldenset, _completions(goldenset, 2, lambda item_id, index: None))
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=tmp_path / "j.jsonl"
        )
        assert judged.stats() == {
            "model_id": CANDIDATE_MODEL,
            "judges": ["helpfulness"],
            "records": 4,
            "imputed": 4,
            "parse_failures": 0,
            "parts": 1,
        }


class TestResume:
    """Contract §1.4: resume keys on ``(judge, item_id, sample_index)``.

    "Did not re-grade" is counted, never inferred from record counts -- a
    re-graded completion overwrites nothing on disk and would leave the record
    count identical. The counter is rigor's own ``judge.verdict`` evidence
    record, one per :meth:`PinnedJudge.evaluate` call, so the oracle comes from
    the dependency and not from the module under test.
    """

    def test_a_resumed_pass_grades_only_what_is_missing(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        full = _completions(goldenset, 2)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"

        judge_artifact(
            _artifact(goldenset, full[:4]),
            goldenset,
            config.build(log, _adapter_for()),
            evidence=log,
            judged=target,
        )
        assert _verdicts_recorded(log) == 4

        # The counter lives inside the script, not around the adapter: the
        # adapter class is part of judges_hash, and swapping it would make this
        # panel a different instrument and the resume illegal.
        second: dict = {}
        judged = judge_artifact(
            _artifact(goldenset, full),
            goldenset,
            config.build(log, _counting_responses_for(second)),
            evidence=log,
            judged=target,
        )
        assert _verdicts_recorded(log) == 6  # 4 already done + 2 newly graded
        assert len(second["helpfulness"].calls) == 2
        assert second["helpfulness"].graded_outputs == [GOOD, GOOD]
        assert len(judged.records) == 6
        assert len(set(_keys(judged))) == 6

    def test_a_resumed_pass_is_visible_in_parts(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        full = _completions(goldenset, 2)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        first = judge_artifact(
            _artifact(goldenset, full[:2]), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        assert first.parts == 1
        second = judge_artifact(
            _artifact(goldenset, full), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        assert second.parts == 2

    def test_re_judging_a_complete_artifact_is_a_no_op(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1", "i2"), n=2)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(artifact, goldenset, config.build(log, _adapter_for()),
                       evidence=log, judged=target)
        assert _verdicts_recorded(log) == 4
        again: dict = {}
        judged = judge_artifact(
            artifact, goldenset, config.build(log, _counting_responses_for(again)),
            evidence=log, judged=target,
        )
        assert _verdicts_recorded(log) == 4  # nothing was graded a second time
        assert again["helpfulness"].calls == []
        assert len(judged.records) == 4
        assert judged.parts == 1

    def test_resume_does_not_duplicate_a_triple(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        full = _completions(goldenset, 2)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        for slice_end in (2, 4, 6):
            judge_artifact(
                _artifact(goldenset, full[:slice_end]), goldenset,
                config.build(log, _adapter_for()), evidence=log, judged=target,
            )
        judged = JudgedArtifact.load(target)
        assert len(judged.records) == 6
        assert len(set(_keys(judged))) == 6
        assert judged.parts == 3
        # Three passes over an artifact that grew twice: six gradings, not twelve.
        assert _verdicts_recorded(log) == 6

    def test_each_judge_resumes_independently(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        full = _completions(goldenset, 2)
        config = _config(tmp_path, _two_judges())
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            _artifact(goldenset, full[:2]), goldenset,
            config.build(log, _adapter_for()), evidence=log, judged=target,
        )
        assert _verdicts_recorded(log) == 4  # two judges x the two graded draws
        counters: dict = {}
        judged = judge_artifact(
            _artifact(goldenset, full), goldenset,
            config.build(log, _counting_responses_for(counters)), evidence=log, judged=target,
        )
        assert _verdicts_recorded(log) == 8
        assert len(counters["helpfulness"].calls) == 2
        assert len(counters["safety"].calls) == 2
        assert len(judged.records) == 8
        assert len(set(_keys(judged))) == 8

    def test_a_different_judges_hash_refuses_to_resume(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            artifact, goldenset, _config(tmp_path, dirname="a").build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        # A different judge name is a different panel; renaming also keeps rigor's
        # per-name rubric-drift lookup out of the way, so the panel hash is the
        # only thing that has moved.
        other = _config(tmp_path, [{"name": "usefulness"}], dirname="b")
        with pytest.raises(JudgeConfigError):
            judge_artifact(
                artifact, goldenset, other.build(log, _adapter_for()),
                evidence=log, judged=target,
            )

    def test_changed_rubric_content_refuses_to_resume(self, tmp_path):
        # The other way the panel can move. The second panel gets its own evidence
        # log because rigor would otherwise refuse the *judge* for rubric drift
        # first, one level down, and this test is about the panel hash.
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        target = tmp_path / "j.jsonl"
        first_log = _log(tmp_path, "first.jsonl")
        judge_artifact(
            artifact, goldenset,
            _config(tmp_path, dirname="a").build(first_log, _adapter_for()),
            evidence=first_log, judged=target,
        )
        edited = _config(
            tmp_path, [{"name": "helpfulness", "rubric_text": OTHER_RUBRIC_TEXT}], dirname="b"
        )
        second_log = _log(tmp_path, "second.jsonl")
        with pytest.raises(JudgeConfigError):
            judge_artifact(
                artifact, goldenset, edited.build(second_log, _adapter_for()),
                evidence=second_log, judged=target,
            )

    def test_a_changed_judge_model_refuses_to_resume(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            artifact, goldenset,
            _config(tmp_path, [{"name": "helpfulness", "model": JUDGE_MODEL}], dirname="a")
            .build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        repinned = _config(
            tmp_path, [{"name": "helpfulness", "model": OTHER_JUDGE_MODEL}], dirname="b"
        )
        with pytest.raises(JudgeConfigError):
            judge_artifact(
                artifact, goldenset, repinned.build(log, _adapter_for()),
                evidence=log, judged=target,
            )

    def test_a_refused_resume_leaves_the_file_alone(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1",), n=1)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            artifact, goldenset, _config(tmp_path, dirname="a").build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        before = target.read_bytes()
        other = _config(tmp_path, [{"name": "usefulness"}], dirname="b")
        with pytest.raises(JudgeConfigError):
            judge_artifact(
                artifact, goldenset, other.build(log, _adapter_for()),
                evidence=log, judged=target,
            )
        assert target.read_bytes() == before

    def test_fresh_discards_the_previous_pass(self, tmp_path):
        goldenset, artifact = _simple_run(tmp_path, ids=("i1", "i2"), n=1)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(artifact, goldenset, config.build(log, _adapter_for()),
                       evidence=log, judged=target)
        proxies: dict = {}
        judged = judge_artifact(
            artifact, goldenset, config.build(log, _counting_responses_for(proxies)),
            evidence=log, judged=target, fresh=True,
        )
        assert len(proxies["helpfulness"].calls) == 2
        assert len(judged.records) == 2
        assert judged.parts == 1

    def test_the_tolerance_counts_the_whole_file_not_the_resumed_slice(self, tmp_path):
        # Contract §1.3: counting per resumed run would abort a 400-completion
        # comparison on one failure in the last five. Here the resumed slice is a
        # single unparseable completion -- 100% of the slice, 5% of the file, and
        # 5% is not *over* a 5% tolerance.
        goldenset = _write_goldenset(tmp_path, [f"i{k}" for k in range(1, 11)])
        full = _completions(
            goldenset,
            2,
            lambda item_id, index: GARBAGE if (item_id, index) == ("i10", 1) else GOOD,
        )
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        first = judge_artifact(
            _artifact(goldenset, full[:19]), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        assert len(first.records) == 19
        second = judge_artifact(
            _artifact(goldenset, full), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        assert len(second.records) == 20
        assert second.stats()["parse_failures"] == 1

    def test_a_resumed_breach_reports_the_pooled_total(self, tmp_path):
        # Same shape, two unparseable draws: 10% of the file. The count in the
        # message must be 2 of 20, not 2 of 2.
        goldenset = _write_goldenset(tmp_path, [f"i{k}" for k in range(1, 11)])
        full = _completions(
            goldenset, 2, lambda item_id, index: GARBAGE if item_id == "i10" else GOOD
        )
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            _artifact(goldenset, full[:18]), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        with pytest.raises(JudgeReliabilityError) as exc:
            judge_artifact(
                _artifact(goldenset, full), goldenset, config.build(log, _adapter_for()),
                evidence=log, judged=target,
            )
        assert (exc.value.failures, exc.value.total) == (2, 20)
        assert "2 of 20" in str(exc.value)


class TestJudgedArtifactHeader:
    """Contract §1: what the header must carry for the report to be auditable."""

    @pytest.fixture
    def written(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        artifact = _artifact(goldenset, _completions(goldenset, 2), n=2)
        panel = _config(tmp_path).build(_log(tmp_path), _adapter_for())
        target = tmp_path / "j.jsonl"
        judged = judge_artifact(
            artifact, goldenset, panel, evidence=_log(tmp_path), judged=target
        )
        header = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
        return goldenset, panel, judged, header

    def test_the_header_identifies_the_run_it_graded(self, written):
        goldenset, panel, judged, header = written
        assert header["record"] == "header"
        assert header["model_id"] == CANDIDATE_MODEL
        assert header["goldenset_hash"] == goldenset.hash
        assert header["judges_hash"] == panel.judges_hash
        assert header["n_per_item"] == 2
        assert header["source"] == "run.jsonl"
        assert header["schema_version"] == ARTIFACT_SCHEMA_VERSION

    def test_the_loaded_artifact_repeats_the_header_identity(self, written):
        goldenset, panel, judged, _ = written
        assert judged.model_id == CANDIDATE_MODEL
        assert judged.goldenset_hash == goldenset.hash
        assert judged.judges_hash == panel.judges_hash
        assert judged.n_per_item == 2

    def test_the_header_names_each_judge_fully(self, written):
        _, _, judged, header = written
        # Asserted by value rather than by key: the contract spells the four
        # facts two different ways (§1 hash spec vs §1 header spec), so what is
        # pinned here is that all four are present for each judge.
        assert len(header["judges"]) == 1
        entry = header["judges"][0]
        assert set(entry.values()) == {
            "helpfulness",
            JUDGE_MODEL,
            "FakeAdapter",
            _sha256_lf(RUBRIC_TEXT.encode("utf-8")),
        }
        assert len(judged.judges) == 1

    def test_the_thresholds_are_echoed_into_the_artifact(self, written):
        _, _, judged, header = written
        assert header["notes"]["thresholds"] == CONTRACT_DEFAULTS
        assert judged.notes["thresholds"] == CONTRACT_DEFAULTS


class TestEvidence:
    """Contract §3 / the brief: ``migkit.judging_completed`` with the counts."""

    def test_the_event_name_is_the_one_the_contract_names(self):
        assert EVENT_JUDGING_COMPLETED == CONTRACT_JUDGING_EVENT

    def test_one_record_per_judging_pass_with_per_judge_counts(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4"])

        def output_for(item_id, index):
            if item_id == "i1":
                return None
            if item_id == "i2" and index == 0:
                return GARBAGE
            return GOOD

        artifact = _artifact(goldenset, _completions(goldenset, 2, output_for))
        log = _log(tmp_path)
        panel = _config(tmp_path, thresholds={"judge_failure_tolerance": 1.0}).build(
            log, _adapter_for()
        )
        judge_artifact(artifact, goldenset, panel, evidence=log, judged=tmp_path / "j.jsonl")

        payloads = _payloads(log, CONTRACT_JUDGING_EVENT)
        assert len(payloads) == 1
        payload = payloads[0]
        assert payload["model_id"] == CANDIDATE_MODEL
        assert payload["judges_hash"] == panel.judges_hash
        assert payload["graded"]["helpfulness"] == 8
        assert payload["imputed"]["helpfulness"] == 2
        assert payload["parse_failures"]["helpfulness"] == 1

    def test_the_counts_are_reported_per_judge(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: None if item_id == "i1" else GOOD),
        )
        log = _log(tmp_path)
        panel = _config(tmp_path, _two_judges()).build(log, _adapter_for())
        judge_artifact(artifact, goldenset, panel, evidence=log, judged=tmp_path / "j.jsonl")

        payload = _payloads(log, CONTRACT_JUDGING_EVENT)[0]
        assert payload["graded"] == {"helpfulness": 4, "safety": 4}
        assert payload["imputed"] == {"helpfulness": 2, "safety": 2}
        assert payload["parse_failures"] == {}

    def test_the_evidence_survives_an_aborted_pass(self, tmp_path):
        # The pass completes before raising, so its counts are still evidence.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3", "i4", "i5"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: GARBAGE if item_id == "i5" else GOOD),
        )
        log = _log(tmp_path)
        panel = _config(tmp_path).build(log, _adapter_for())
        with pytest.raises(JudgeReliabilityError):
            judge_artifact(artifact, goldenset, panel, evidence=log, judged=tmp_path / "j.jsonl")
        payload = _payloads(log, CONTRACT_JUDGING_EVENT)[0]
        assert payload["graded"]["helpfulness"] == 10
        assert payload["parse_failures"]["helpfulness"] == 2

    def test_a_resumed_pass_reports_only_what_it_graded(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        full = _completions(goldenset, 2)
        config = _config(tmp_path)
        log = _log(tmp_path)
        target = tmp_path / "j.jsonl"
        judge_artifact(
            _artifact(goldenset, full[:4]), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        judge_artifact(
            _artifact(goldenset, full), goldenset, config.build(log, _adapter_for()),
            evidence=log, judged=target,
        )
        payloads = _payloads(log, CONTRACT_JUDGING_EVENT)
        assert [payload["graded"]["helpfulness"] for payload in payloads] == [4, 2]

    def test_rigor_records_one_verdict_per_judged_completion(self, tmp_path):
        # Contract §1.4: rigor writes one judge.verdict per call and does not
        # dedupe, which is why anything reading verdicts back has to dedupe on
        # the same triple. Imputed records never reach rigor at all.
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        artifact = _artifact(
            goldenset,
            _completions(goldenset, 2, lambda item_id, index: None if item_id == "i2" else GOOD),
        )
        log = _log(tmp_path)
        panel = _config(tmp_path).build(log, _adapter_for())
        judge_artifact(artifact, goldenset, panel, evidence=log, judged=tmp_path / "j.jsonl")
        assert len(_payloads(log, "judge.verdict")) == 2


class TestJudgedArtifactLoad:
    """Contract §1: same on-disk discipline as ``RunArtifact``."""

    def test_a_clean_file_round_trips(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), _verdict_line(), _verdict_line(sample_index=1)])
        judged = JudgedArtifact.load(path)
        assert len(judged.records) == 2
        assert judged.parts == 1
        assert judged.path == str(path)
        assert judged.model_id == CANDIDATE_MODEL

    def test_a_torn_final_line_is_tolerated(self, tmp_path):
        path = tmp_path / "j.jsonl"
        body = "".join(
            f"{line}\n" for line in [_header_line(), _verdict_line(), _verdict_line(sample_index=1)]
        )
        path.write_bytes((body + _verdict_line(sample_index=2)[:25]).encode("utf-8"))
        judged = JudgedArtifact.load(path)
        assert len(judged.records) == 2

    def test_a_malformed_line_in_the_middle_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), "{not json", _verdict_line()])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_a_blank_line_in_the_middle_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), "", _verdict_line()])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_a_file_with_no_header_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_verdict_line(), _verdict_line(sample_index=1)])
        with pytest.raises(ArtifactError) as exc:
            JudgedArtifact.load(path)
        assert "header" in str(exc.value)

    def test_an_unknown_record_type_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), json.dumps({"record": "note", "text": "hello"})])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_a_record_with_no_type_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), json.dumps({"judge": "helpfulness"})])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_a_duplicate_judge_item_sample_triple_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), _verdict_line(), _verdict_line()])
        with pytest.raises(ArtifactError) as exc:
            JudgedArtifact.load(path)
        assert "twice" in str(exc.value)

    def test_the_same_completion_under_two_judges_is_not_a_duplicate(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(
            path,
            [
                _header_line(),
                _verdict_line(judge="helpfulness"),
                _verdict_line(judge="safety"),
            ],
        )
        judged = JudgedArtifact.load(path)
        assert len(judged.records) == 2
        assert judged.judge_names() == ("helpfulness", "safety")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("model_id", "other-model-v1"),
            ("goldenset_hash", "f" * 64),
            ("judges_hash", "e" * 64),
            ("n_per_item", 5),
        ],
    )
    def test_headers_that_disagree_on_identity_are_refused(self, tmp_path, key, value):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), _verdict_line(), _header_line(**{key: value})])
        with pytest.raises(ArtifactError) as exc:
            JudgedArtifact.load(path)
        assert key in str(exc.value)

    def test_agreeing_headers_are_counted_as_parts(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(
            path,
            [_header_line(), _verdict_line(), _header_line(created="2026-02-02T00:00:00+00:00"),
             _verdict_line(sample_index=1)],
        )
        judged = JudgedArtifact.load(path)
        assert judged.parts == 2
        assert len(judged.records) == 2

    def test_a_future_schema_version_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(schema_version=ARTIFACT_SCHEMA_VERSION + 1)])
        with pytest.raises(ArtifactError) as exc:
            JudgedArtifact.load(path)
        assert str(ARTIFACT_SCHEMA_VERSION + 1) in str(exc.value)

    def test_the_current_schema_version_is_accepted(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(schema_version=ARTIFACT_SCHEMA_VERSION), _verdict_line()])
        assert len(JudgedArtifact.load(path).records) == 1

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(tmp_path / "absent.jsonl")

    def test_a_verdict_missing_a_required_field_is_refused(self, tmp_path):
        broken = json.loads(_verdict_line())
        del broken["judge"]
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), json.dumps(broken), _verdict_line(sample_index=1)])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_a_json_array_line_is_refused(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(path, [_header_line(), "[1, 2, 3]", _verdict_line()])
        with pytest.raises(ArtifactError):
            JudgedArtifact.load(path)

    def test_records_round_trip_their_flags(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(
            path,
            [
                _header_line(),
                _verdict_line(passed=False, score=1.0, imputed=True, error="completion failed"),
                _verdict_line(
                    sample_index=1,
                    passed=False,
                    score=None,
                    parse_failure=True,
                    error="unparseable",
                ),
                _verdict_line(sample_index=2, reason="meets the rubric"),
            ],
        )
        judged = JudgedArtifact.load(path)
        imputed, parsed, plain = judged.records
        assert (imputed.imputed, imputed.score, imputed.passed) == (True, 1.0, False)
        assert (parsed.parse_failure, parsed.score) == (True, None)
        assert plain.reason == "meets the rubric"
        assert judged.stats()["imputed"] == 1
        assert judged.stats()["parse_failures"] == 1

    def test_coverage_and_for_judge_slice_the_same_records(self, tmp_path):
        path = tmp_path / "j.jsonl"
        _write_lines(
            path,
            [
                _header_line(),
                _verdict_line(judge="helpfulness", item_id="i1", sample_index=0),
                _verdict_line(judge="helpfulness", item_id="i1", sample_index=1),
                _verdict_line(judge="safety", item_id="i1", sample_index=0),
            ],
        )
        judged = JudgedArtifact.load(path)
        assert judged.coverage() == {("helpfulness", "i1"): 2, ("safety", "i1"): 1}
        assert len(judged.for_judge("helpfulness")) == 2
        assert len(judged.for_judge("safety")) == 1
        assert judged.for_judge("absent") == ()
