"""``series.RunPoint`` and ``series.run_point``, against chunk C1's contract.

Written from `docs/superpowers/plans/2026-08-21-migkit-report-plan.md` lines
341-458 and section 4.1, and from nothing else. Per HANDOFF.md's working method
the author of `series.py` and the author of this file are different agents in
different worktrees, and **no expected value below was obtained by running
``model_migration_kit.series``** -- it did not exist when these were written.
Every expectation is one of:

* a literal from the contract (the field list; the edge table's eight rows; the
  four "must not"s);
* a value read out of a genuine evidence log -- `migkit demo --keep` was run and
  its `migkit.comparison` payload supplied the *shape* of every fixture here.
  The numbers were then changed, deliberately: a fixture copied verbatim from the
  demo cannot catch either of the two errors the plan's reviewer note names,
  because the demo has one judge and its baseline and candidate gates carry the
  same floor.

**Why the fixtures look adversarial.** Three of this module's assertions are
worthless if the fixture is symmetric, so none of them is:

* `thresholds.pass_rate_floor` is 0.90 and no gate `min_rate` is, so a `floor`
  lifted from the run-level mapping is visible;
* the baseline gate and the candidate gate never share a value, so a `floor`
  lifted from the baseline is visible -- both dicts carry identical keys and the
  banner reads plausibly either way, which is why it has to be the fixture and
  not the reader that tells them apart;
* the two-judge payload puts the *narrower* judge first and gives it different
  numbers throughout, so `judges[0]` is visible. On a single-judge payload --
  which is every log this project has produced, the demo included -- taking
  `judges[0]` is indistinguishable from taking the widest, and is wrong on the
  first two-judge log anyone runs.

**On `is None`.** `pass_rate`, `lower_bound`, `interval` and `floor` are each
required to be `None` in some row of the edge table, and `None` is the whole
point of the row: a judge that graded nothing has an *unknown* pass rate, not a
pass rate of zero, and a timeline that draws 0.0 there reports a catastrophe that
did not happen. So every one of those assertions is spelled `is None`. `assert
not point.pass_rate` would pass on 0.0 and is a bug in the test, not a shorthand.
"""

from __future__ import annotations

import builtins
import copy
import dataclasses
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tracemalloc
import typing
from pathlib import Path

import pytest
from opik_rigor import EvidenceError, EvidenceLog, EvidenceRecord

from model_migration_kit import series
from model_migration_kit.comparison import _require_comparable
from model_migration_kit.errors import ArtifactError
from model_migration_kit.judging import JudgedArtifact, JudgeRecord
from model_migration_kit.series import RunPoint, read_series, run_point

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"

#: The contract's field list, transcribed, with three deliberate departures
#: agreed in review. This tuple is asserted *exactly* -- `==`, not `<=` -- so any
#: departure has to be made here on purpose, which is the point of the assertion.
#:
#: * `floor_source` is **added**. The contract's "must not" permits falling back
#:   to `thresholds["pass_rate_floor"]` and says in the same sentence that "the
#:   next chunk needs to know it happened", then names no field to record it in.
#:   This is that field, on the pattern `created`/`created_source` already sets.
#: * `completions_baseline`/`completions_candidate` are **renamed** to
#:   `judged_baseline`/`judged_candidate`. The source is right and the name was
#:   not: the gate's `n` counts completions the judge graded, which excludes parse
#:   failures, and is not the run's completion count that the report's
#:   "completions" row shows.
#: * `failures_baseline`/`failures_candidate` are **renamed** to
#:   `judge_failures_*`. Gate `failures` means the judge failed the completion;
#:   the report's "failed completions" row means the adapter errored, and on the
#:   demo run those two numbers are 15 and 0. Five later chunks read `RunPoint`,
#:   so the rename is cheapest now.
_CONTRACT_FIELDS = (
    "created",
    "created_source",
    "verdict",
    "reason",
    "baseline_model",
    "candidate_model",
    "adapter_baseline",
    "adapter_candidate",
    "goldenset_hash",
    "judges_hash",
    "config_hash",
    "config_path",
    "n_per_item",
    "items",
    "judged_baseline",
    "judged_candidate",
    "judge_failures_baseline",
    "judge_failures_candidate",
    "pass_rate",
    "interval",
    "lower_bound",
    "floor",
    "floor_source",
    "confidence",
    "alpha",
    "judge_name",
    "judge_model_id",
    "rubric_hashes",
    "p_value",
    "latency_median_candidate",
    "runs_needed",
    "n_required",
    "warnings",
)

#: The three types the contract forbids the seam from naming.
_FORBIDDEN_TYPES = ("ComparisonReport", "JudgedArtifact", "RunArtifact")


# ----------------------------------------------------------------------------------
# Fixtures, shaped from a real `migkit.comparison` payload
# ----------------------------------------------------------------------------------


def _side(
    *,
    n: int,
    successes: int,
    pass_rate: float | None,
    interval_lower: float | None,
    interval_upper: float | None,
    lower_bound: float | None,
    min_rate: float | None,
    confidence: float | None,
    runs_needed: int | None = None,
) -> dict:
    """One side of one judge's gate, with the key set a real payload carries."""
    return {
        "confidence": confidence,
        "failures": n - successes,
        "gate": "pass_rate",
        "interval_lower": interval_lower,
        "interval_upper": interval_upper,
        "label": "gate",
        "lower_bound": lower_bound,
        "method": "wilson-one-sided",
        "min_rate": min_rate,
        "n": n,
        "pass_rate": pass_rate,
        "passed": False,
        "runs_needed": runs_needed,
        "successes": successes,
        "target_power": 0.8,
        "underpowered": False,
    }


def _judge(
    name: str,
    *,
    model_id: str,
    alpha: float | None,
    rubric_hash: str,
    p_value: float | None,
    runs_needed: int | None,
    n_required: int | None,
    items: int,
    baseline: dict,
    candidate: dict,
) -> dict:
    return {
        "alpha": alpha,
        "baseline": baseline,
        "candidate": candidate,
        "floor_cleared": False,
        "holm_threshold": 0.05,
        "imputed": {"baseline": 0, "candidate": 0},
        "item_counts": {
            "baseline": {"failing": 1, "passing": items - 1, "unstable": 0},
            "candidate": {"failing": 3, "passing": items - 3, "unstable": 0},
            "items": items,
        },
        "missing_scores": {"baseline": 0, "candidate": 0},
        "model_id": model_id,
        "mw_powered": False,
        "name": name,
        "note": "",
        "p_value": p_value,
        "parse_failures": {"baseline": 0, "candidate": 0},
        "power": {
            "alpha": 0.05,
            "method": "two-proportion-normal-approximation",
            "min_detectable_effect": 0.1,
            "n_observed": candidate["n"],
            "n_required": n_required,
            "power_target": 0.8,
            "powered": False,
        },
        "regressed": True,
        # A real payload records the same p-value twice, at the judge and inside
        # `regression`. Both are present and equal here: which path is read is not
        # something the contract settles, and not something to fail a build over.
        "regression": {
            "alpha": 0.05,
            "alternative": "less",
            "gate": "no_regression",
            "label": name,
            "n_baseline": baseline["n"],
            "n_current": candidate["n"],
            "p_value": p_value,
            "passed": False,
            "test": "mann-whitney-u",
        },
        "rubric_hash": rubric_hash,
        "runs_needed": runs_needed,
        "test_ran": "mann-whitney-u",
        "underpowered": False,
    }


#: The accuracy judge, single-judge payloads' only judge. Every number on the
#: candidate side differs from the number beside it on the baseline side.
_ACCURACY = _judge(
    "accuracy",
    model_id="fake-judge-v1",
    alpha=0.01,  # the judge's own alpha; `thresholds.alpha` is 0.05
    rubric_hash="cc39e4aad0ef5db821fb627bb1217bab78095543642634bc2d30581f642c6268",
    p_value=0.007843147236661033,
    runs_needed=931,
    n_required=140,
    items=12,
    baseline=_side(
        n=60,
        successes=55,
        pass_rate=0.9166666666666666,
        interval_lower=0.8193105798166558,
        interval_upper=0.9638795433982692,
        lower_bound=0.8385295580433538,
        min_rate=0.80,  # deliberately not the candidate's 0.85
        confidence=0.99,  # deliberately not the candidate's 0.90
        runs_needed=931,
    ),
    candidate=_side(
        n=60,
        successes=45,
        pass_rate=0.75,
        interval_lower=0.6276792992295219,
        interval_upper=0.8422347746994332,
        lower_bound=0.6486242412686939,
        min_rate=0.85,  # the gate that was applied
        confidence=0.90,
        runs_needed=931,
    ),
)


def _comparison(**overrides) -> dict:
    """A `migkit.comparison` payload, shaped from the one `migkit demo` writes.

    `thresholds.pass_rate_floor` is 0.90 and the candidate gate's `min_rate` is
    0.85: the contract's named first-failing test lives on that gap.
    """
    payload = {
        "baseline": {
            "adapter": "OpenAIAdapter",
            "adapters": ["OpenAIAdapter"],
            "imputed": 0,
            "model_id": "gpt-baseline-v1",
            "n_per_item": 5,
            "parse_failures": 0,
            "parts": 1,
            "records": 60,
            "run_parts": 1,
        },
        "candidate": {
            "adapter": "AnthropicAdapter",
            "adapters": ["AnthropicAdapter"],
            "imputed": 0,
            "model_id": "claude-candidate-v2",
            "n_per_item": 5,
            "parse_failures": 0,
            "parts": 1,
            "records": 60,
            "run_parts": 1,
        },
        "completion_rates": {
            "baseline": {"n": 60, "passes": 55},
            "candidate": {"n": 60, "passes": 45},
            "unit": "completion",
        },
        "config_hash": "1ad89c46dcbd426d364ddbd15af4a1d03446c48cb536bc879b87a96d03a40433",
        "config_path": "/srv/migkit/nightly.toml",
        "created": "2026-08-21T22:40:58.984925+00:00",
        "flips": [],
        "gains": [],
        "goldenset_hash": "5fef50364057cad869f16698df32d927b650778c34382f6f68d9fd53ba4e9a04",
        "goldenset_path": "/srv/migkit/goldenset.jsonl",
        "item_counts": {"per_judge": {}, "unit": "item"},
        "judges": [copy.deepcopy(_ACCURACY)],
        "judges_hash": "bb624f0ed1781d852cd961a9f4a338a3644ffddf262f4435c0d0f8628b7dcbc2",
        "latency": {
            "baseline": {"median": 0.42, "n": 60, "p90": 0.9},
            "candidate": {"median": 0.71, "n": 60, "p90": 1.4},
        },
        "n_per_item": 5,
        "thresholds": {
            "alpha": 0.05,
            "confidence": 0.95,
            "judge_failure_tolerance": 0.05,
            "min_detectable_effect": 0.1,
            "pass_rate_floor": 0.90,
            "power_target": 0.8,
        },
        "unstable": [],
        "warnings": ["judge 'accuracy': 60 completions per side cannot detect a 10% drop"],
    }
    payload.update(overrides)
    return payload


def _verdict(**overrides) -> dict:
    payload = {
        "baseline_model": "gpt-baseline-v1",
        "candidate_model": "claude-candidate-v2",
        "decided_by": "rule 1",
        "exit_code": 1,
        "judges": [{"name": "accuracy", "regressed": True}],
        "reason": "Judge 'accuracy' shows a statistically significant regression.",
        "rule": 1,
        "verdict": "NO-GO",
    }
    payload.update(overrides)
    return payload


#: A two-judge payload whose *first* judge is the narrower one. `accuracy` graded
#: 40 candidate completions, `tone` graded 90; every number the contract routes
#: through the widest judge differs between them.
_NARROW = _judge(
    "accuracy",
    model_id="judge-narrow-v1",
    alpha=0.02,
    rubric_hash="ffff0000000000000000000000000000000000000000000000000000000000ff",
    p_value=0.4,
    runs_needed=111,
    n_required=222,
    items=8,
    baseline=_side(
        n=40,
        successes=36,
        pass_rate=0.9,
        interval_lower=0.1,
        interval_upper=0.2,
        lower_bound=0.11,
        min_rate=0.10,
        confidence=0.51,
        runs_needed=111,
    ),
    candidate=_side(
        n=40,
        successes=30,
        pass_rate=0.30,
        interval_lower=0.31,
        interval_upper=0.32,
        lower_bound=0.33,
        min_rate=0.34,
        confidence=0.35,
        runs_needed=111,
    ),
)

_WIDE = _judge(
    "tone",
    model_id="judge-wide-v9",
    alpha=0.04,
    rubric_hash="00001111111111111111111111111111111111111111111111111111111111aa",
    p_value=0.6,
    runs_needed=777,
    n_required=888,
    items=8,
    baseline=_side(
        n=90,
        successes=81,
        pass_rate=0.9,
        interval_lower=0.7,
        interval_upper=0.8,
        lower_bound=0.71,
        min_rate=0.70,
        confidence=0.52,
        runs_needed=777,
    ),
    candidate=_side(
        n=90,
        successes=60,
        pass_rate=0.61,
        interval_lower=0.62,
        interval_upper=0.63,
        lower_bound=0.64,
        min_rate=0.65,
        confidence=0.66,
        runs_needed=777,
    ),
)


def _two_judge_comparison() -> dict:
    """Narrow judge first, wide judge second, and the panel counts follow the wide.

    `completion_rates` is set from the widest judge rather than summed across the
    panel, which is the contract's own rationale for the rule: two judges grading
    90 completions are 180 records and 90 completions.
    """
    return _comparison(
        judges=[copy.deepcopy(_NARROW), copy.deepcopy(_WIDE)],
        completion_rates={
            "baseline": {"n": 90, "passes": 81},
            "candidate": {"n": 90, "passes": 60},
            "unit": "completion",
        },
    )


#: Two judges whose widths point in opposite directions: `breadth` graded 90
#: baseline completions and 40 candidate ones, `depth` the reverse. Every other
#: two-judge fixture above is symmetric -- 40/40 against 90/90 -- so a selection
#: made on the *baseline* `n` passes all of them and this one alone catches it.
_BREADTH = _judge(
    "breadth",
    model_id="judge-breadth-v3",
    alpha=0.021,
    rubric_hash="2222" + "0" * 60,
    p_value=0.21,
    runs_needed=21,
    n_required=221,
    items=9,
    baseline=_side(
        n=90,
        successes=81,
        pass_rate=0.90,
        interval_lower=0.41,
        interval_upper=0.42,
        lower_bound=0.43,
        min_rate=0.44,
        confidence=0.45,
        runs_needed=21,
    ),
    candidate=_side(
        n=40,
        successes=20,
        pass_rate=0.50,
        interval_lower=0.51,
        interval_upper=0.52,
        lower_bound=0.53,
        min_rate=0.54,
        confidence=0.55,
        runs_needed=21,
    ),
)

_DEPTH = _judge(
    "depth",
    model_id="judge-depth-v3",
    alpha=0.031,
    rubric_hash="3333" + "0" * 60,
    p_value=0.31,
    runs_needed=31,
    n_required=331,
    items=9,
    baseline=_side(
        n=40,
        successes=32,
        pass_rate=0.80,
        interval_lower=0.61,
        interval_upper=0.62,
        lower_bound=0.63,
        min_rate=0.64,
        confidence=0.65,
        runs_needed=31,
    ),
    candidate=_side(
        n=90,
        successes=50,
        pass_rate=0.5555555555555556,
        interval_lower=0.71,
        interval_upper=0.72,
        lower_bound=0.73,
        min_rate=0.74,
        confidence=0.75,
        runs_needed=31,
    ),
)


def _lopsided_comparison() -> dict:
    """`breadth` first and wider on the baseline; `depth` second and wider on the
    candidate. The contract's rule reads the candidate side, so `depth` wins."""
    return _comparison(judges=[copy.deepcopy(_BREADTH), copy.deepcopy(_DEPTH)])


# ----------------------------------------------------------------------------------
# The shape of the record itself
# ----------------------------------------------------------------------------------


def test_a_run_point_carries_every_field_the_contract_names():
    """A field quietly absent is a column that reads as blank on every row of the
    timeline, with nothing to say whether the run lacked the number or the reader
    lost it."""
    assert dataclasses.is_dataclass(RunPoint)
    present = {field.name for field in dataclasses.fields(RunPoint)}
    expected = set(_CONTRACT_FIELDS)
    assert present == expected, (
        f"missing from RunPoint: {sorted(expected - present)}; "
        f"not in the contract: {sorted(present - expected)}"
    )


def test_a_run_point_cannot_be_edited_after_it_is_built():
    """The series is evidence. A stage downstream that can rewrite a pass rate on
    the way to the chart makes the chart unfalsifiable."""
    point = run_point(_comparison(), _verdict())
    with pytest.raises(dataclasses.FrozenInstanceError):
        point.pass_rate = 0.99  # type: ignore[misc]


def test_a_run_point_can_be_hashed():
    """`rubric_hashes` and `warnings` are declared as tuples rather than lists for
    a reason a later chunk needs: points get grouped and de-duplicated, and a
    single list field would make every point unhashable."""
    point = run_point(_comparison(), _verdict())
    assert {point, point} == {point}
    assert isinstance(point.rubric_hashes, tuple)
    assert isinstance(point.warnings, tuple)


def test_run_point_is_annotated_as_taking_mappings_and_not_artifacts():
    """The contract's reason: the report has payloads, and the artifacts are
    frequently absent. A signature typed against `JudgedArtifact` compiles fine
    and then cannot be called on the logs anyone actually has."""
    annotations = dict(getattr(run_point, "__annotations__", {}))
    assert "comparison" in annotations, "run_point must annotate its `comparison` parameter"
    assert "Mapping" in str(annotations["comparison"])
    assert "Mapping" in str(annotations["verdict"])
    everything = " ".join(
        str(value)
        for value in list(annotations.values())
        + [field.type for field in dataclasses.fields(RunPoint)]
    )
    for forbidden in _FORBIDDEN_TYPES:
        assert forbidden not in everything, f"the series seam must not name {forbidden}"


def test_the_envelope_timestamp_can_only_be_passed_by_keyword():
    """The contract puts a bare `*` before it. A third positional parameter would
    let a caller pass the envelope `ts` where the verdict payload belongs and get
    back a point with a plausible date and no verdict."""
    with pytest.raises(TypeError):
        run_point(_comparison(), _verdict(), "2026-08-21T22:40:58+00:00")  # type: ignore[misc]


def test_series_does_not_import_report():
    """Asserted in a fresh interpreter for the reason `test_import_purity.py`
    gives: by the time this module runs, pytest has already imported `report`, so
    an in-process `sys.modules` check would be red on a pure module. The series
    seam is what the report will be rebuilt *on*; if it imports the thing it is
    replacing, the rebuild has a cycle before it starts."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(_SRC), existing] if existing else [str(_SRC)])
    probe = (
        "import json, sys\n"
        "import model_migration_kit.series as s\n"
        "print(json.dumps({"
        "'file': s.__file__, "
        "'report': 'model_migration_kit.report' in sys.modules, "
        "'jinja2': 'jinja2' in sys.modules}))\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    assert completed.returncode == 0, f"the probe failed to run:\n{completed.stderr}"
    seen = json.loads(completed.stdout.strip().splitlines()[-1])
    # The probe must be looking at this checkout, or it is asserting about
    # whatever happens to be installed in the environment instead.
    assert str(_SRC) in seen["file"], f"the probe imported {seen['file']}, not this worktree's"
    assert seen["report"] is False, "importing series pulled in model_migration_kit.report"
    assert seen["jinja2"] is False, "importing series pulled in jinja2"


def test_building_a_run_point_does_not_touch_the_filesystem():
    """`run_point` is handed two mappings that have already been read. A module
    that reopens a path recorded in the payload -- `config_path`, `artifact` --
    would work on the machine that ran the comparison and fail on everyone
    else's, which is the failure a shared evidence log exists to avoid."""
    payload, verdict = _comparison(), _verdict()

    def _refuse(*args, **kwargs):
        raise AssertionError(f"series touched the filesystem: {args!r}")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", _refuse)
        patch.setattr(io, "open", _refuse)
        patch.setattr(os, "open", _refuse)
        patch.setattr(Path, "open", _refuse)
        patch.setattr(Path, "read_text", _refuse)
        patch.setattr(Path, "read_bytes", _refuse)
        point = run_point(payload, verdict)
    assert point.config_path == "/srv/migkit/nightly.toml"


def test_building_a_run_point_leaves_the_payloads_it_was_given_unchanged():
    """A reader that normalises in place corrupts the caller's data. C2 streams a
    whole log through this function; if the first point edits the payload, the
    record the report describes is not the record that was on disk."""
    payload, verdict = _comparison(), _verdict()
    before_payload, before_verdict = copy.deepcopy(payload), copy.deepcopy(verdict)
    run_point(payload, verdict)
    assert payload == before_payload
    assert verdict == before_verdict


# ----------------------------------------------------------------------------------
# The gate that was applied, not the one that was configured
# ----------------------------------------------------------------------------------


def test_a_run_point_takes_its_floor_from_the_gate_that_was_applied_and_not_from_the_configured_thresholds():  # noqa: E501
    """The contract's own failure mode: the timeline draws a floor rule at 0.9 on
    a run that was gated at 0.85, and every verdict on the chart is misattributed
    against a line that run was never held to."""
    payload = _comparison()
    assert payload["thresholds"]["pass_rate_floor"] == 0.90
    assert payload["judges"][0]["candidate"]["min_rate"] == 0.85
    assert run_point(payload, _verdict()).floor == 0.85


def test_confidence_and_alpha_are_also_taken_from_the_gate_rather_than_the_configuration():
    """The same lie, two more fields. A run gated at 90% confidence and drawn as
    95% reports an interval narrower than the one that decided it."""
    payload = _comparison()
    assert payload["thresholds"]["confidence"] == 0.95
    assert payload["thresholds"]["alpha"] == 0.05

    point = run_point(payload, _verdict())
    assert point.confidence == 0.90
    assert point.alpha == 0.01


def test_the_gate_numbers_come_from_the_candidate_side_and_not_the_baseline_side():
    """The plan names this as the likely subtle error: both gate dicts carry
    identical keys, so a baseline read produces a chart that is wrong in a way no
    reader can see. The floor being drawn is the one the *candidate* had to clear
    -- that is the decision the run turned on."""
    payload = _comparison()
    baseline = payload["judges"][0]["baseline"]
    candidate = payload["judges"][0]["candidate"]
    assert baseline["min_rate"] != candidate["min_rate"]
    assert baseline["confidence"] != candidate["confidence"]

    point = run_point(payload, _verdict())
    assert point.floor == candidate["min_rate"]
    assert point.confidence == candidate["confidence"]
    assert point.pass_rate == candidate["pass_rate"]
    assert point.lower_bound == candidate["lower_bound"]
    assert point.interval == (candidate["interval_lower"], candidate["interval_upper"])


def test_the_floor_falls_back_to_the_configured_threshold_when_the_gate_did_not_record_one():
    """Older logs exist. A point with no floor at all draws no rule, and the
    reader cannot tell a run that cleared its floor from one held to none."""
    payload = _comparison()
    del payload["judges"][0]["candidate"]["min_rate"]
    del payload["judges"][0]["candidate"]["confidence"]
    del payload["judges"][0]["alpha"]

    point = run_point(payload, _verdict())
    assert point.floor == 0.90
    assert point.confidence == 0.95
    assert point.alpha == 0.05


def test_no_floor_is_invented_when_neither_the_gate_nor_the_thresholds_recorded_one():
    """The contract is explicit that the fallback stops here: *record nothing
    rather than record a guess*. A substituted default is indistinguishable in the
    output from a floor the run genuinely had, and the next chunk needs to know
    which of the two it is looking at."""
    payload = _comparison()
    del payload["thresholds"]
    del payload["judges"][0]["candidate"]["min_rate"]
    del payload["judges"][0]["candidate"]["confidence"]
    del payload["judges"][0]["alpha"]

    point = run_point(payload, _verdict())
    assert point.floor is None
    assert point.confidence is None
    assert point.alpha is None
    # Only the gate went unrecorded; the judge is still there and still read.
    assert point.pass_rate == 0.75


def test_the_floor_records_which_of_the_two_sources_it_came_from():
    """The contract's "must not" permits the fallback to `thresholds` and says in
    the same breath that the next chunk needs to know it happened -- and then
    names no field to say so in. Two runs, one gated at 0.85 and one whose gate
    recorded nothing while its config asked for 0.85, hand a renderer the same
    float and are not the same claim: the first is the number the run was held to,
    the second the number someone intended. A chart that cannot tell them apart
    cannot say which of its floor rules it is entitled to draw solid."""
    gated = run_point(_comparison(), _verdict())
    assert gated.floor == 0.85
    assert gated.floor_source == "gate"

    payload = _comparison()
    del payload["judges"][0]["candidate"]["min_rate"]
    fell_back = run_point(payload, _verdict())
    assert fell_back.floor == 0.90
    assert fell_back.floor_source == "thresholds"

    del payload["thresholds"]
    neither = run_point(payload, _verdict())
    assert neither.floor is None
    assert neither.floor_source == "unrecorded"


def test_a_comparison_with_no_judges_calls_its_floor_unrecorded_rather_than_gated():
    """The empty-judges row of the edge table, read through the new field. There
    is no gate to have applied anything, so "gate" would be a claim about a
    measurement that does not exist."""
    payload = _comparison()
    del payload["judges"]
    del payload["thresholds"]

    point = run_point(payload, _verdict())
    assert point.floor is None
    assert point.floor_source == "unrecorded"


# ----------------------------------------------------------------------------------
# The widest judge
# ----------------------------------------------------------------------------------


def test_the_headline_numbers_come_from_the_judge_that_graded_the_most_completions():
    """The contract's second named subtlety. `judges[0]` is correct on every
    single-judge log this project has produced and wrong on the first two-judge
    log anyone runs -- and wrong silently, because the narrow judge's pass rate is
    a real number that plots without complaint."""
    payload = _two_judge_comparison()
    assert payload["judges"][0]["candidate"]["n"] < payload["judges"][1]["candidate"]["n"]

    point = run_point(payload, _verdict())
    assert point.judge_name == "tone"
    assert point.judge_model_id == "judge-wide-v9"
    assert point.pass_rate == 0.61
    assert point.interval == (0.62, 0.63)
    assert point.lower_bound == 0.64
    assert point.p_value == 0.6
    assert point.runs_needed == 777
    assert point.n_required == 888


def test_the_gate_numbers_come_from_the_same_widest_judge_as_the_pass_rate():
    """A floor read off one judge and a pass rate read off another draws a point
    below a line it was never measured against, which is the same misattribution
    the floor rule exists to prevent."""
    payload = _two_judge_comparison()
    point = run_point(payload, _verdict())
    assert point.floor == 0.65
    assert point.confidence == 0.66
    assert point.alpha == 0.04


def test_two_judges_of_equal_width_are_separated_on_the_payloads_own_order():
    """Ties have to resolve the same way on every run, or the timeline switches
    which judge it is reporting halfway along and shows a step change nobody
    made. The payload's order is the config order, which is stable."""
    narrow = copy.deepcopy(_NARROW)
    narrow["baseline"]["n"] = 90
    narrow["candidate"]["n"] = 90
    payload = _comparison(judges=[narrow, copy.deepcopy(_WIDE)])

    point = run_point(payload, _verdict())
    assert point.judge_name == "accuracy"
    assert point.pass_rate == 0.30


def test_the_widest_judge_is_measured_on_the_candidate_side_and_not_the_baseline():
    """Every two-judge fixture above is symmetric -- 40/40 against 90/90 -- so a
    reader that sorted the panel on the *baseline* `n` passes all of them, and the
    contract names reading the baseline as one of its two likely subtle errors.
    Here the two sides disagree: `breadth` graded 90 baselines and 40 candidates,
    `depth` 40 and 90. The candidate is the side the run is deciding on, so the
    judge that graded most of *it* is the one the point quotes. Choosing on the
    baseline picks `breadth`, whose pass rate is a perfectly plausible number
    belonging to a different member of the panel."""
    payload = _lopsided_comparison()
    assert payload["judges"][0]["baseline"]["n"] > payload["judges"][1]["baseline"]["n"]
    assert payload["judges"][0]["candidate"]["n"] < payload["judges"][1]["candidate"]["n"]

    point = run_point(payload, _verdict())
    assert point.judge_name == "depth"
    assert point.judge_model_id == "judge-depth-v3"
    assert point.pass_rate == 0.5555555555555556
    assert point.floor == 0.74
    assert point.confidence == 0.75
    assert point.alpha == 0.031
    assert point.p_value == 0.31
    assert point.runs_needed == 31
    assert point.n_required == 331
    # The baseline count comes from the same judge, and is the smaller one.
    assert point.judged_candidate == 90
    assert point.judged_baseline == 40


def test_the_judged_counts_are_the_widest_judges_and_are_not_summed_across_the_panel():
    """The contract's own rationale for the widest-judge rule: two judges grading
    the same completions are twice the records and the same completions. Summing
    the panel here gives 130 on a run that graded 90, and a completeness column
    that overstates every two-judge night by the size of the smaller judge."""
    payload = _two_judge_comparison()
    assert payload["judges"][0]["candidate"]["n"] + payload["judges"][1]["candidate"]["n"] == 130

    point = run_point(payload, _verdict())
    assert point.judged_candidate == 90
    assert point.judged_baseline == 90
    assert point.judge_failures_candidate == 30
    assert point.judge_failures_baseline == 9


def test_runs_needed_is_the_judges_own_number_and_not_either_of_its_gates():
    """A real payload records `runs_needed` in three places -- once on the judge
    and once inside each side's gate -- and on every other fixture in this file
    all three agree, so all three readings pass. The contract routes the point
    through the judge. These three differ, which is the only arrangement in which
    the assertion says anything at all."""
    judge = copy.deepcopy(_ACCURACY)
    judge["runs_needed"] = 500
    judge["baseline"]["runs_needed"] = 300
    judge["candidate"]["runs_needed"] = 400

    point = run_point(_comparison(judges=[judge]), _verdict())
    assert point.runs_needed == 500


def test_the_rubric_hashes_are_sorted_and_carry_one_entry_per_judge():
    """Sorted, because two runs with the same panel must produce the same tuple
    whatever order the config listed the judges in; one per judge, because the
    tuple is how a later chunk notices a rubric was edited mid-series."""
    payload = _two_judge_comparison()
    point = run_point(payload, _verdict())
    assert point.rubric_hashes == tuple(sorted((_NARROW["rubric_hash"], _WIDE["rubric_hash"])))
    assert point.rubric_hashes[0].startswith("0000")


# ----------------------------------------------------------------------------------
# The edge table
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("judges", [[], None], ids=["empty", "absent"])
def test_a_comparison_with_no_judges_still_yields_a_point(judges):
    """A comparison that recorded no judges is a run that happened; dropping it
    would take the run off the timeline entirely, and a gap in a chart whose whole
    argument is that gaps are information is the worst available way to say so."""
    payload = _comparison()
    del payload["thresholds"]  # so `floor` has nowhere legitimate to fall back to
    if judges is None:
        del payload["judges"]
    else:
        payload["judges"] = judges

    point = run_point(payload, _verdict())
    assert isinstance(point, RunPoint)
    assert point.pass_rate is None
    assert point.interval is None
    assert point.lower_bound is None
    assert point.floor is None
    assert point.confidence is None
    assert point.alpha is None
    assert point.p_value is None
    assert point.runs_needed is None
    assert point.n_required is None
    assert point.judge_name == ""
    assert point.judge_model_id == ""
    assert point.rubric_hashes == ()
    assert point.items == 0
    # Not judge-derived, and so still populated.
    assert point.candidate_model == "claude-candidate-v2"
    assert point.verdict == "NO-GO"


def test_a_judge_that_graded_nothing_reports_no_pass_rate_rather_than_a_pass_rate_of_zero():
    """0.0 and "unknown" are opposite claims. A run whose judging fell over
    entirely would be drawn at the bottom of the chart as a total collapse, and
    the person reading it would go looking for a regression that never happened."""
    payload = _comparison()
    payload["judges"][0]["candidate"].update(
        n=0,
        successes=0,
        failures=0,
        pass_rate=None,
        interval_lower=None,
        interval_upper=None,
        lower_bound=None,
    )

    point = run_point(payload, _verdict())
    assert point.pass_rate is None
    assert point.interval is None
    assert point.lower_bound is None
    # The judge still ran and is still the one being reported; it is the three
    # numbers above that are unknown, not the whole point.
    assert point.judge_name == "accuracy"
    assert point.floor == 0.85
    assert point.candidate_model == "claude-candidate-v2"


def test_a_judge_that_graded_nothing_reports_no_rate_even_when_the_gate_wrote_zeros():
    """The test above sets `n` to 0 *and* every derived field to `None`, so it
    passes on a reader that never looks at `n`: the `None`s alone produce the
    expected answer and the denominator guard is never exercised. A gate that
    recorded `pass_rate: 0.0` over `n: 0` is the case that needs the guard, and
    0.0 is the value that plots -- a point on the floor of the chart for a run
    that measured nothing, which every reader will take for a total collapse."""
    payload = _comparison()
    payload["judges"][0]["candidate"].update(
        n=0,
        successes=0,
        failures=0,
        pass_rate=0.0,
        interval_lower=0.0,
        interval_upper=1.0,
        lower_bound=0.0,
    )

    point = run_point(payload, _verdict())
    assert point.pass_rate is None
    assert point.interval is None
    assert point.lower_bound is None
    # The count itself is a real zero and is reported as one: nothing was graded.
    assert point.judged_candidate == 0
    assert point.judge_name == "accuracy"


@pytest.mark.parametrize(
    ("drop", "keep"),
    [("interval_upper", "interval_lower"), ("interval_lower", "interval_upper")],
    ids=["upper-missing", "lower-missing"],
)
def test_a_one_ended_interval_is_reported_as_no_interval_at_all(drop, keep):
    """A tuple with a `None` in it is a shaded band with one edge, and every
    consumer downstream has to guess whether the missing edge means zero, one, or
    unknown. `None` says the one true thing: there is no interval to draw."""
    payload = _comparison()
    payload["judges"][0]["candidate"][drop] = None
    assert payload["judges"][0]["candidate"][keep] is not None

    point = run_point(payload, _verdict())
    assert point.interval is None
    # The bound that *was* recorded is a separate field and is not collateral.
    assert point.lower_bound == 0.6486242412686939
    assert point.pass_rate == 0.75


def test_an_interval_missing_its_key_entirely_is_also_no_interval():
    """The same row of the table with the key absent rather than null -- a payload
    from a writer that omits nulls must not read differently from one that
    records them."""
    payload = _comparison()
    del payload["judges"][0]["candidate"]["interval_upper"]

    point = run_point(payload, _verdict())
    assert point.interval is None
    assert point.pass_rate == 0.75


def test_an_interval_with_both_ends_is_a_pair_of_floats_in_low_high_order():
    """The band is drawn from this tuple. Reversed, it draws inverted or not at
    all, depending on how forgiving the renderer is."""
    point = run_point(_comparison(), _verdict())
    assert point.interval == (0.6276792992295219, 0.8422347746994332)
    assert point.interval[0] < point.interval[1]


# ----------------------------------------------------------------------------------
# `created`, per section 4.1
# ----------------------------------------------------------------------------------


def test_created_is_the_payloads_own_timestamp_even_when_an_envelope_timestamp_is_available():
    """Section 4.1: `created` is a recorded fact about the comparison, the
    envelope `ts` a fact about when a line was written. The two differ on a log
    that was concatenated or copied, and the seeded series depends on the
    payload's because it is the only one this project can control."""
    point = run_point(_comparison(), _verdict(), envelope_ts="2019-01-01T00:00:00+00:00")
    assert point.created == "2026-08-21T22:40:58.984925+00:00"
    assert point.created_source == "payload"


def test_created_falls_back_to_the_envelope_timestamp_when_the_payload_did_not_record_one():
    """Section 4.1 names the consequence: a payload from a future writer that
    drops the field would otherwise sort as the epoch and put the run at the far
    left of the timeline, decades from the runs it belongs beside."""
    payload = _comparison()
    del payload["created"]

    point = run_point(payload, _verdict(), envelope_ts="2026-08-21T22:40:58.984971+00:00")
    assert point.created == "2026-08-21T22:40:58.984971+00:00"
    assert point.created_source == "envelope"


def test_created_falls_back_to_the_envelope_when_the_recorded_value_will_not_parse():
    """Section 4.1 covers absent *and unparseable* under the same fallback, and
    records which of the two it used. The envelope value is a real date; the
    payload's is not, and preferring the unusable one loses a point that could
    have been placed."""
    payload = _comparison(created="the night of the twenty-first")

    point = run_point(payload, _verdict(), envelope_ts="2026-08-21T22:40:58.984971+00:00")
    assert point.created == "2026-08-21T22:40:58.984971+00:00"
    assert point.created_source == "envelope"


def test_a_created_timestamp_ending_in_z_is_accepted_and_stored_as_it_was_written():
    """`datetime.fromisoformat` learned to accept a trailing `Z` in 3.11, and this
    package supports 3.10, so the parse check normalises one. Untested, that is
    live compatibility code whose failure is invisible on whichever interpreter
    the suite happens to run: the same evidence log would date this point on 3.11
    and fall back to the envelope on 3.10, so two charts drawn from one file would
    put the run in two places. The stored string is deliberately *not* normalised
    -- section 4.1 asks for the timestamp as recorded, and the envelope value here
    is a real date that would win if the `Z` form were rejected."""
    point = run_point(
        _comparison(created="2026-08-21T22:40:58Z"),
        _verdict(),
        envelope_ts="2019-01-01T00:00:00+00:00",
    )
    assert point.created == "2026-08-21T22:40:58Z"
    assert point.created_source == "payload"


def test_a_trailing_z_is_still_accepted_on_an_interpreter_that_cannot_parse_one():
    """The test above asserts the behaviour; on 3.11 and later it cannot assert the
    *mechanism*, because `fromisoformat` handles `Z` natively there and deleting
    the normalisation changes nothing. This suite runs on such an interpreter, so
    the compatibility line would be uncovered code that only fails on the oldest
    Python this package supports -- the one least likely to be the one anybody
    runs the tests on, and the one a user is most likely to be reading the log on.

    So 3.10's parser is stood in for: a `datetime` whose `fromisoformat` rejects a
    trailing `Z` exactly the way 3.10's does. Under it the payload timestamp must
    still be the one chosen, and the envelope's real date must still lose."""
    real = series.datetime

    class _Pre311:
        @staticmethod
        def fromisoformat(value: str):
            if value.endswith("Z"):
                raise ValueError(f"Invalid isoformat string: {value!r}")
            return real.fromisoformat(value)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(series, "datetime", _Pre311)
        point = run_point(
            _comparison(created="2026-08-21T22:40:58Z"),
            _verdict(),
            envelope_ts="2019-01-01T00:00:00+00:00",
        )
    assert point.created == "2026-08-21T22:40:58Z"
    assert point.created_source == "payload"


def test_created_is_empty_and_its_source_unknown_when_neither_was_recorded():
    """A point with a known verdict and an unknown date. Section 4.2 requires such
    a point to be excluded from the timeline and *named beneath it*, which the
    next chunk can only do if this one says `unknown` instead of guessing."""
    payload = _comparison()
    del payload["created"]

    point = run_point(payload, _verdict())
    assert point.created == ""
    assert point.created_source == "unknown"
    assert point.verdict == "NO-GO"


# ----------------------------------------------------------------------------------
# `n_per_item` coercion
# ----------------------------------------------------------------------------------


def test_an_n_per_item_recorded_as_a_string_is_coerced_to_an_integer():
    """`n_per_item` is one of the fields grouping keys on (section 4.4). If `"5"`
    and `5` are different keys, a log whose writer changed its JSON encoding
    splits one series into two, and the chart shows two short lines where there
    was one long one."""
    point = run_point(_comparison(n_per_item="5"), _verdict())
    assert point.n_per_item == 5
    assert isinstance(point.n_per_item, int)


@pytest.mark.parametrize(
    "value",
    ["many", "", None, [], {}],
    ids=["word", "blank", "null", "list", "dict"],
)
def test_an_n_per_item_that_will_not_coerce_is_reported_as_zero(value):
    """Zero, not an exception. A single malformed record must not take down a
    reader that is streaming a year of them."""
    point = run_point(_comparison(n_per_item=value), _verdict())
    assert point.n_per_item == 0
    assert point.pass_rate == 0.75  # one bad field, not one bad point


def test_an_absent_n_per_item_is_reported_as_zero():
    """The same argument as the row above, for the writer that omits the key
    rather than recording something odd in it."""
    payload = _comparison()
    del payload["n_per_item"]

    point = run_point(payload, _verdict())
    assert point.n_per_item == 0
    assert point.pass_rate == 0.75


# ----------------------------------------------------------------------------------
# Numbers a payload can hold that Python cannot count
# ----------------------------------------------------------------------------------


def test_a_json_payload_can_hold_nan_and_infinity_at_all():
    """The premise of the three tests below, asserted rather than assumed.
    Python's own JSON reader accepts bare `NaN`, `Infinity` and `-Infinity` by
    default -- they are not valid JSON and `json.loads` takes them anyway -- so a
    log written by any producer that emitted one parses into a float that `int()`
    refuses. `comparison.py` keeps a note about a degenerate test handing back
    `NaN`, which is where such a value would come from here."""
    parsed = json.loads('{"n": NaN, "rate": Infinity, "other": -Infinity}')
    assert parsed["n"] != parsed["n"]
    assert parsed["rate"] == float("inf")
    assert parsed["other"] == float("-inf")


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "inf", "-inf"],
)
def test_a_non_finite_count_reads_as_zero_rather_than_raising(value):
    """`int(float("nan"))` raises `ValueError` and `int(float("inf"))` raises
    `OverflowError`. Either one, reached from a count field, takes down a reader
    whose stated contract is that a single unreadable record costs that record and
    not the report -- and the record here is one that `json.loads` accepted
    without complaint."""
    payload = _comparison(n_per_item=value)
    payload["judges"][0]["candidate"]["n"] = value
    payload["judges"][0]["candidate"]["failures"] = value
    payload["judges"][0]["item_counts"]["items"] = value

    point = run_point(payload, _verdict())
    assert point.n_per_item == 0
    assert point.judged_candidate == 0
    assert point.judge_failures_candidate == 0
    assert point.items == 0
    # A count of zero blanks the rates, exactly as a recorded zero does.
    assert point.pass_rate is None
    # One bad judge, not one bad point.
    assert point.candidate_model == "claude-candidate-v2"


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_non_finite_rate_reads_as_unknown_rather_than_being_plotted(value):
    """Neither value is a quantity a chart can draw, and a `NaN` floor is worse
    than undrawable: every comparison against it is `False`, so a run would be
    shown failing a gate it was never really held to. Unknown is the honest
    reading, and it lets the floor fall back to the threshold that *was*
    recorded -- with `floor_source` saying that is what happened."""
    payload = _comparison()
    payload["judges"][0]["candidate"]["pass_rate"] = value
    payload["judges"][0]["candidate"]["min_rate"] = value
    payload["judges"][0]["p_value"] = value

    point = run_point(payload, _verdict())
    assert point.pass_rate is None
    assert point.p_value is None
    assert point.floor == 0.90
    assert point.floor_source == "thresholds"


@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_non_finite_sizing_number_reads_as_unknown_rather_than_raising(value):
    """`runs_needed` and `n_required` are the two fields that keep `None` rather
    than falling to zero, and they are also the two most likely to be handed a
    degenerate result: both are computed from a power calculation that a zero
    denominator makes meaningless."""
    judge = copy.deepcopy(_ACCURACY)
    judge["runs_needed"] = value
    judge["power"]["n_required"] = value

    point = run_point(_comparison(judges=[judge]), _verdict())
    assert point.runs_needed is None
    assert point.n_required is None


def test_a_rate_recorded_as_a_string_reads_the_way_the_report_reads_it():
    """A deliberate asymmetry with `n_per_item`, asserted so that a later reader
    does not "fix" it into agreement. `report._number` refuses a numeric string
    and renders an em-dash for it. If the series coerced one, a log with a quoted
    `pass_rate` would put 0.75 on the timeline and a dash in the table beside it,
    in the same document -- which is precisely the "timeline contradicts its own
    verdicts" failure this module exists to prevent, arrived at by being more
    helpful than its neighbour. Counts are the exception because they are typed
    `int`: they have no "unavailable" to fall to, so their only alternative to
    reading the string is 0, a claim about the run rather than an admission about
    the record."""
    payload = _comparison()
    payload["judges"][0]["candidate"]["pass_rate"] = "0.75"
    payload["judges"][0]["candidate"]["min_rate"] = "0.85"

    point = run_point(payload, _verdict())
    assert point.pass_rate is None
    assert point.floor == 0.90
    assert point.floor_source == "thresholds"
    # ...while the counts go on coercing, as the contract's edge table requires.
    assert run_point(_comparison(n_per_item="5"), _verdict()).n_per_item == 5
    assert point.judged_candidate == 60


# ----------------------------------------------------------------------------------
# The verdict side
# ----------------------------------------------------------------------------------


def test_a_comparison_with_no_verdict_record_still_carries_everything_it_knew():
    """A run whose verdict line is missing -- the process was killed between the
    two writes -- is still a measured pass rate on a date. Dropping it loses real
    evidence; the contract keeps the point and says the verdict is unknown."""
    point = run_point(_comparison(), None)
    assert point.verdict is None
    assert point.reason is None
    assert point.pass_rate == 0.75
    assert point.floor == 0.85
    assert point.created == "2026-08-21T22:40:58.984925+00:00"
    assert point.candidate_model == "claude-candidate-v2"


def test_a_verdict_payload_missing_the_verdict_key_reads_as_no_verdict():
    """The same row with a different cause. "The key was not there" and "the
    record was not there" have to produce the same point, or the timeline reports
    a difference between two logs that says nothing about the runs."""
    verdict = _verdict()
    del verdict["verdict"]
    del verdict["reason"]

    point = run_point(_comparison(), verdict)
    assert point.verdict is None
    assert point.reason is None
    assert point.pass_rate == 0.75


def test_the_verdict_and_its_reason_are_carried_across():
    """The reason is the sentence an operator reads when they ask why a point is
    red. Without it the chart says NO-GO and nothing about what decided it."""
    point = run_point(_comparison(), _verdict())
    assert point.verdict == "NO-GO"
    assert point.reason == "Judge 'accuracy' shows a statistically significant regression."


# ----------------------------------------------------------------------------------
# The plain derivations
# ----------------------------------------------------------------------------------


def test_a_point_carries_the_identifiers_that_decide_whether_two_runs_are_comparable():
    """Section 4.4: grouping keys on the golden-set hash, the judges hash and
    `n_per_item`, because `_require_comparable` cannot be called on payloads. A
    point that drops one of those lets the grouping admit a pair it should have
    refused, and the chart then draws a trend across two different experiments."""
    payload = _comparison()
    point = run_point(payload, _verdict())
    assert point.baseline_model == "gpt-baseline-v1"
    assert point.candidate_model == "claude-candidate-v2"
    assert point.adapter_baseline == "OpenAIAdapter"
    assert point.adapter_candidate == "AnthropicAdapter"
    assert point.goldenset_hash == payload["goldenset_hash"]
    assert point.judges_hash == payload["judges_hash"]
    assert point.config_hash == payload["config_hash"]
    assert point.config_path == "/srv/migkit/nightly.toml"
    assert point.n_per_item == 5


def test_a_point_that_recorded_no_adapter_reports_an_empty_string_rather_than_none():
    """`adapter_baseline` is typed `str`, and section 4.3 keys the synthetic-data
    band off it starting with "Fake". A `None` there raises inside the check that
    decides whether the band is shown, and the band must not be suppressible by a
    malformed record."""
    payload = _comparison()
    del payload["baseline"]["adapter"]
    del payload["candidate"]["adapter"]

    point = run_point(payload, _verdict())
    assert point.adapter_baseline == ""
    assert point.adapter_candidate == ""
    assert point.baseline_model == "gpt-baseline-v1"


def test_a_point_carries_what_the_judge_graded_and_what_the_judge_failed():
    """These four numbers are the judge's, not the run's, and the distinction is
    the reason they were renamed. `failures` on a gate means *the judge failed the
    completion* -- a graded answer that missed the rubric's bar, which is a quality
    signal. The report's "failed completions" row means *the adapter errored* --
    an answer that never arrived, which is a completeness signal. On the demo run
    those two readings are 15 and 0. Gate `n` is the same story one step over: it
    counts completions the judge graded, and a completion whose judge reply would
    not parse is produced and never graded, so it is not the run's completion
    count either. Both sides, or a reader cannot tell a candidate the judge
    disliked from a baseline it liked equally little."""
    point = run_point(_comparison(), _verdict())
    assert point.judged_baseline == 60
    assert point.judged_candidate == 60
    assert point.judge_failures_baseline == 5
    assert point.judge_failures_candidate == 15
    # The gate's own arithmetic, which is what makes these judge outcomes: the
    # candidate side graded 60 and passed 45.
    assert point.judge_failures_candidate == 60 - 45


def test_the_item_count_is_the_golden_sets_and_is_zero_when_it_was_not_recorded():
    """Items, not completions. Five completions per item across twelve items are
    sixty records, and a chart that labels the sixty as items overstates the
    golden set's coverage fivefold."""
    payload = _comparison()
    assert payload["judges"][0]["item_counts"]["items"] == 12
    assert run_point(payload, _verdict()).items == 12

    del payload["judges"][0]["item_counts"]
    assert run_point(payload, _verdict()).items == 0


def test_the_latency_reported_is_the_candidate_sides_median():
    """The candidate is the model being decided on. Reporting the baseline's
    latency under a label that says candidate makes a slower model look like a
    free swap."""
    payload = _comparison()
    assert payload["latency"]["baseline"]["median"] != payload["latency"]["candidate"]["median"]
    assert run_point(payload, _verdict()).latency_median_candidate == 0.71


def test_latency_is_none_when_the_run_recorded_none():
    """`None` rather than 0.0, on the same argument as the pass rate: a run with
    no timing recorded is not a run that was instantaneous."""
    payload = _comparison()
    del payload["latency"]

    point = run_point(payload, _verdict())
    assert point.latency_median_candidate is None
    assert point.pass_rate == 0.75


def test_the_warnings_are_carried_across_verbatim():
    """The underpowered warning is the one that stops a reader trusting a
    difference sixty completions cannot detect. It has to survive the trip to the
    timeline, or every point on the chart looks equally well evidenced."""
    payload = _comparison()
    assert run_point(payload, _verdict()).warnings == tuple(payload["warnings"])


def test_a_run_with_no_warnings_reports_an_empty_tuple():
    """Empty, not `None`. Every consumer downstream iterates this."""
    payload = _comparison()
    del payload["warnings"]

    point = run_point(payload, _verdict())
    assert point.warnings == ()
    assert point.pass_rate == 0.75


def test_a_single_warning_recorded_as_a_bare_string_is_one_warning_and_not_seven():
    """A `str` is iterable, so the obvious comprehension turns `"careful"` into
    `('c', 'a', 'r', 'e', 'f', 'u', 'l')` -- seven rows of single letters in the
    one place a reader looks to find out why a difference should not be trusted.
    Discarding it instead would be silent evidence loss, which is the same failure
    from the other side, so a writer that recorded one warning gets one."""
    payload = _comparison(warnings="judge 'accuracy': 60 completions cannot detect a 10% drop")

    point = run_point(payload, _verdict())
    assert point.warnings == ("judge 'accuracy': 60 completions cannot detect a 10% drop",)


@pytest.mark.parametrize(
    "value",
    [{"accuracy": "underpowered"}, 7, True],
    ids=["mapping", "number", "flag"],
)
def test_a_warnings_field_that_holds_no_warnings_yields_none(value):
    """Iterating a mapping lists its keys, which would render config key names as
    if they were warnings the run produced. Nothing recognisable, nothing shown --
    and still a point, because the field is not what the row is about."""
    point = run_point(_comparison(warnings=value), _verdict())
    assert point.warnings == ()
    assert point.pass_rate == 0.75


def test_a_payload_that_is_nothing_but_an_empty_mapping_still_yields_a_point():
    """The floor under the whole contract: `run_point` reads other people's data,
    and the one thing it must not do is raise. A single unreadable record in a
    year-long log should cost that record, not the report."""
    point = run_point({}, None)
    assert isinstance(point, RunPoint)
    assert point.created == ""
    assert point.created_source == "unknown"
    assert point.verdict is None
    assert point.pass_rate is None
    assert point.floor is None
    assert point.warnings == ()
    assert point.rubric_hashes == ()
    assert point.n_per_item == 0
    assert point.items == 0
    assert point.judged_baseline == 0
    assert point.judge_failures_candidate == 0
    assert point.floor_source == "unrecorded"


def test_the_annotations_resolve_to_real_types():
    """A forward reference that never resolves is a signature which documents
    nothing and a `get_type_hints` call that raises inside the next chunk's
    tooling."""
    assert typing.get_type_hints(run_point)
    assert typing.get_type_hints(RunPoint)



# ==================================================================================
# Chunk C2 -- `series.read_series`
# ==================================================================================
#
# Written from the same plan, chunk C2, and from nothing else. `read_series` did
# not exist in this worktree when these were written and no expected value below
# was obtained by running it.
#
# **What the fixtures are, and what they are not.** `migkit demo --keep` was run
# and its `evidence.jsonl` read: eleven event types, and per run exactly one
# `migkit.comparison`, one `migkit.verdict`, and forty `judge.verdict` records.
# **One** comparison per run, which is worth stating plainly, because it is the
# reason nothing below is generated. That log supplied the envelope --
# `schema_version`, `ts`, `event_type`, `payload`, written by `EvidenceLog.append`
# with `sort_keys=True` -- and the event-type names, and nothing else. No test here
# depends on how many records `migkit demo` writes, and none reads a file it did
# not itself write under `tmp_path`.
#
# The logs are built line by line for three reasons:
#
# * `migkit demo` cannot produce a multi-point log at any length, and a multi-point
#   log is this chunk's whole subject. A fixture generated from one would pass
#   against a reader that returned only the first point, or only the last -- which
#   is the reader this chunk exists to replace;
# * every log this project has ever written puts the verdict on the line after its
#   comparison, so even a demo log concatenated with itself cannot tell "pairs
#   correctly" from "assumes adjacency", the one error the reviewer note names;
# * two of these tests turn on what the timestamps *are*, and `EvidenceLog.append`
#   stamps its own.
#
# **A contradiction in the contract, asserted one way.** The prose says a point is
# closed by "the next `migkit.verdict` record before the next `migkit.comparison`",
# which on a log reading C, C, V, V closes nothing and leaves both points without a
# verdict. The edge table's own row for that log says the opposite: "first verdict
# closes the first point; the second verdict closes the second." The table is what
# is asserted below, and the reasoning is in the docstring of the test that does it.
#
# **On `judge.verdict`.** A reader that matches on the tail of the event type
# closes the first point with one judge's opinion of one completion, and the
# timeline then reports a verdict nobody reached.

_COMPARISON = "migkit.comparison"
_VERDICT = "migkit.verdict"


def _ts(second: int) -> str:
    """An envelope timestamp in the format `EvidenceLog.append` writes."""
    return f"2026-08-21T22:40:{second:02d}.000000+00:00"


def _line(event_type: str, payload: dict, ts: str) -> str:
    """One line of an evidence log, in rigor's envelope and rigor's key order.

    Serialised the way `EvidenceLog.append` serialises -- the same four keys,
    `sort_keys=True`, `ensure_ascii=False` -- and built through `EvidenceRecord`,
    so a change to rigor's schema breaks this fixture rather than quietly leaving
    it describing a log shape nobody writes.
    """
    record = EvidenceRecord(ts=ts, event_type=event_type, payload=dict(payload))
    return json.dumps(
        {
            "schema_version": record.schema_version,
            "ts": record.ts,
            "event_type": record.event_type,
            "payload": record.payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _write_log(
    path: Path,
    *records: tuple[str, dict],
    timestamps: list[str] | None = None,
    torn: str = "",
    raw: list[str] | None = None,
) -> Path:
    """Write an evidence log and return its path.

    `raw` is spliced in as complete lines after the records, for the malformed
    cases. `torn` is appended with **no trailing newline**, which is what a process
    killed mid-write leaves behind.
    """
    stamps = timestamps if timestamps is not None else [_ts(index) for index in range(len(records))]
    lines = [
        _line(event_type, payload, stamp)
        for (event_type, payload), stamp in zip(records, stamps, strict=True)
    ]
    lines.extend(raw or [])
    text = "".join(line + "\n" for line in lines) + torn
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def _a_comparison(model: str, **overrides) -> tuple[str, dict]:
    """A `migkit.comparison` record whose candidate model names the run."""
    payload = _comparison(**overrides)
    payload["candidate"] = dict(payload["candidate"], model_id=model)
    return (_COMPARISON, payload)


def _a_verdict(value: str, **overrides) -> tuple[str, dict]:
    """A `migkit.verdict` record whose reason names it, so the pairing is visible."""
    overrides.setdefault("reason", f"reason recorded with {value}")
    return (_VERDICT, _verdict(verdict=value, **overrides))


def _noise() -> list[tuple[str, dict]]:
    """The records a real log is mostly made of, in the proportions it holds them.

    `judge.verdict` is in here on purpose: it is one judge's opinion of one
    completion, it carries a `verdict` key of its own, and the demo log holds forty
    of them for every `migkit.verdict`.
    """
    return [
        ("migkit.run_started", {"model_id": "fake-candidate-v1", "n": 5}),
        ("migkit.completion", {"item_id": "extract-01", "index": 0}),
        ("judge.verdict", {"verdict": "pass", "score": 5, "raw": "5 -- exact match"}),
        ("migkit.item_completed", {"item_id": "extract-01"}),
        ("migkit.judging_completed", {"judge": "accuracy", "n": 60}),
        ("assertion.evaluated", {"name": "pass_rate", "passed": False}),
    ]


def _peak_bytes(work) -> int:
    """Peak allocation during `work`, the shape `tests/test_evidence_scale.py` uses."""
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


# ----------------------------------------------------------------------------------
# The shape of the call
# ----------------------------------------------------------------------------------


def test_read_series_is_annotated_as_taking_a_path_and_returning_a_tuple_of_points():
    """The contract's signature. A reader typed against an open file, or against a
    `ComparisonReport`, cannot be called on the only thing an operator reliably
    has, which is a path to a log."""
    hints = typing.get_type_hints(read_series)
    assert "evidence" in hints, "read_series must annotate its `evidence` parameter"
    accepted = str(hints["evidence"])
    assert "Path" in accepted and "str" in accepted, accepted
    returned = str(hints["return"])
    assert "RunPoint" in returned, returned
    assert "tuple" in returned.lower(), returned
    for forbidden in _FORBIDDEN_TYPES:
        assert forbidden not in accepted + returned, f"the series seam must not name {forbidden}"


def test_a_log_given_as_a_string_reads_the_same_as_one_given_as_a_path(tmp_path: Path):
    """`str | Path`, both halves of it. The CLI hands this an `argv` string and the
    report hands it a `Path`; a reader that survives only one of those fails in
    whichever of the two callers was written second."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    from_path = read_series(log)
    from_string = read_series(str(log))
    assert [point.candidate_model for point in from_path] == ["first", "second"]
    assert from_string == from_path


def test_the_series_is_a_tuple_and_not_a_list(tmp_path: Path):
    """`RunPoint` is frozen and hashable so a later chunk can group and de-duplicate
    points. A mutable series handed to that chunk is a set of points that can change
    after the chart was drawn from them."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    points = read_series(log)
    assert isinstance(points, tuple), f"read_series returned a {type(points).__name__}"
    assert [point.candidate_model for point in points] == ["first", "second"]
    assert all(isinstance(point, RunPoint) for point in points)


# ----------------------------------------------------------------------------------
# Every comparison is a point, in the order the log records them
# ----------------------------------------------------------------------------------


def test_a_log_holding_three_comparisons_yields_three_points_in_the_order_they_were_written(
    tmp_path: Path,
):
    """The contract's named first-failing test, and the whole reason this chunk
    exists. `from_evidence` keeps the last comparison and discards the rest, so a
    fortnight of nightly runs renders today as a single point and the trend the
    report is being rebuilt to show is not in the document at all."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("night-one"),
        _a_verdict("GO"),
        _a_comparison("night-two"),
        _a_verdict("NO-GO"),
        _a_comparison("night-three"),
        _a_verdict("GO"),
    )
    points = read_series(log)
    assert len(points) == 3, f"three comparisons became {len(points)} point(s)"
    assert [point.candidate_model for point in points] == [
        "night-one",
        "night-two",
        "night-three",
    ]
    assert [point.verdict for point in points] == ["GO", "NO-GO", "GO"]


def test_every_point_is_what_run_point_makes_of_the_two_payloads_it_was_paired_from(
    tmp_path: Path,
):
    """The pairing stated as an equality rather than field by field.

    Anything `read_series` reads differently from `run_point` -- a payload it
    normalised on the way past, a verdict it attached to the neighbouring run --
    shows up here as a whole wrong point, rather than only in the one field some
    test happened to look at."""
    comparisons = [_a_comparison("alpha"), _a_comparison("beta")]
    verdicts = [_a_verdict("GO"), _a_verdict("NO-GO")]
    log = _write_log(
        tmp_path / "evidence.jsonl",
        comparisons[0],
        verdicts[0],
        comparisons[1],
        verdicts[1],
    )
    points = read_series(log)
    expected = tuple(
        run_point(comparison[1], verdict[1])
        for comparison, verdict in zip(comparisons, verdicts, strict=True)
    )
    assert points == expected


def test_the_records_a_run_writes_around_its_comparison_are_stepped_over(tmp_path: Path):
    """A real log is three hundred records and three of them are comparisons. A
    reader that counts anything else as a run reports a hundred and twenty nights
    that never happened."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        *_noise(),
        _a_comparison("the-only-run"),
        _a_verdict("NO-GO"),
        *_noise(),
    )
    points = read_series(log)
    assert len(points) == 1, f"a log shaped like a real one gave {len(points)} points"
    assert points[0].candidate_model == "the-only-run"
    assert points[0].verdict == "NO-GO"


# ----------------------------------------------------------------------------------
# The edge table, row by row
# ----------------------------------------------------------------------------------


def test_a_log_with_no_comparison_record_is_an_empty_series_and_not_an_error(tmp_path: Path):
    """Row one. `ReportModel.from_evidence` keeps its own `ArtifactError` for a log
    with nothing to report on; `read_series` is the layer beneath it, and a caller
    rendering an empty timeline needs an empty tuple rather than an exception it
    must catch to discover there were no runs.

    The second half is the half that matters: the emptiness has to be a property of
    the log. A reader that returns `()` whatever it is handed passes the first
    assertion and nothing else."""
    log = _write_log(tmp_path / "evidence.jsonl", *_noise())
    assert read_series(log) == ()

    _write_log(tmp_path / "evidence.jsonl", *_noise(), _a_comparison("a-real-run"))
    after = read_series(log)
    assert len(after) == 1 and after[0].candidate_model == "a-real-run"


def test_a_log_with_no_lines_at_all_is_an_empty_series(tmp_path: Path):
    """The first night of a new pipeline: the log exists because something opened
    it, and the run died before comparing anything. That is a timeline with no
    points on it, not a crash on the way to one."""
    log = tmp_path / "evidence.jsonl"
    log.write_text("", encoding="utf-8")
    assert read_series(log) == ()

    _write_log(log, _a_comparison("later-that-night"), _a_verdict("GO"))
    after = read_series(log)
    assert [point.candidate_model for point in after] == ["later-that-night"]


def test_a_comparison_followed_by_its_verdict_is_one_point_carrying_both(tmp_path: Path):
    """Row two, which is every log this project has written so far. The verdict and
    the numbers have to arrive on the same point, or the timeline draws a GO band
    over a run whose pass rate came from somewhere else."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("claude-candidate-v2"),
        _a_verdict("NO-GO"),
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].verdict == "NO-GO"
    assert points[0].reason == "reason recorded with NO-GO"
    assert points[0].candidate_model == "claude-candidate-v2"
    assert points[0].baseline_model == "gpt-baseline-v1"
    assert points[0].pass_rate == 0.75
    assert points[0].floor == 0.85


def test_a_comparison_with_no_verdict_after_it_is_still_a_point(tmp_path: Path):
    """Row three. A run that compared and then died before deciding is a real night
    with real numbers and an unknown outcome. Dropping it leaves a gap in the trend
    exactly where the interesting thing happened."""
    log = _write_log(tmp_path / "evidence.jsonl", _a_comparison("died-before-deciding"))
    points = read_series(log)
    assert len(points) == 1, "the comparison was dropped for want of a verdict"
    assert points[0].verdict is None
    assert points[0].reason is None
    assert points[0].candidate_model == "died-before-deciding"
    assert points[0].pass_rate == 0.75, "the numbers went out with the missing verdict"


def test_a_verdict_written_before_any_comparison_belongs_to_no_point(tmp_path: Path):
    """Row four. The naive reader keeps the last verdict it saw and hands it to the
    next comparison, which dates a decision to a run that had not started when it
    was written -- a concatenated log, or a rerun appended to yesterday's file."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_verdict("GO"),
        _a_comparison("the-first-real-run"),
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].candidate_model == "the-first-real-run"
    assert points[0].verdict is None, "a verdict from before the run was attached to it"


def test_a_stray_leading_verdict_does_not_displace_the_verdict_that_followed(tmp_path: Path):
    """The same row, in the case where ignoring the stray record is not enough: it
    has to be ignored *without* consuming the slot. A reader that pairs by index
    across two collected lists gives this run yesterday's GO and reports tonight's
    NO-GO nowhere at all."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_verdict("GO", reason="left over from yesterday"),
        _a_comparison("tonight"),
        _a_verdict("NO-GO", reason="tonight's decision"),
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].verdict == "NO-GO"
    assert points[0].reason == "tonight's decision"


def test_two_comparisons_written_before_either_verdict_both_land_on_the_second(tmp_path: Path):
    """Row five, and the one row a fixture built from a real log cannot reach.

    Was `test_two_comparisons_written_before_either_verdict_pair_first_with_first`
    until C19, and inverted rather than deleted: C2 chose first-in-first-out on the
    reasoning kept below, deliberately and after argument, and the record of a
    decision that was later reconsidered is worth more than a tidy file.

    `compare` writes the verdict on the line after its comparison
    (`comparison.py:906-908`), so on every log in existence "pairs correctly" and
    "assumes the next line is the verdict" are the same reader. This log is C, C,
    V, V, and there the two answers differ. That much is unchanged, and so is the
    failure being defended against: a verdict landing on the wrong run, which draws
    a red night over a green one.

    **What changed is which of the two answers is right.** C2 read the contract's
    Edges row -- "first verdict closes the first point; the second closes the
    second" -- against the contract's own prose, and took the row. C19 finds the
    premise under the row false. There is exactly one writer of either record,
    `comparison.py:907-908`, two `evidence.append` calls back to back inside one
    `if`, so no pipeline in this repository can produce C, C, V, V at all, and
    first-in-first-out is right only about a log nobody writes. The shape that *is*
    written is a crash -- a comparison with no verdict after it, and the next night
    appended to the same growing file -- and there first-in-first-out hands night
    one night two's verdict and shifts every later verdict by one, permanently. So
    the rule is now that **every verdict record updates the most recently opened
    point**. On this log both verdicts land on run four, and run three, whose
    verdict this log genuinely does not contain, keeps `None`.
    """
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("run-three"),
        _a_comparison("run-four"),
        _a_verdict("GO", reason="the first of the two verdicts"),
        _a_verdict("NO-GO", reason="the second of the two verdicts"),
    )
    points = read_series(log)
    assert len(points) == 2, f"C, C, V, V gave {len(points)} point(s)"
    assert [point.candidate_model for point in points] == ["run-three", "run-four"]
    assert points[0].verdict is None, (
        "the first verdict closed the first point -- C2's rule, which shifts every "
        "verdict in a log that holds one crashed night"
    )
    assert points[0].reason is None
    assert points[1].verdict == "NO-GO", (
        "the second verdict did not overwrite the first on the most recent point"
    )
    assert points[1].reason == "the second of the two verdicts"


def test_a_third_verdict_after_two_comparisons_overwrites_the_most_recent_point(tmp_path: Path):
    """The tail of row five. Was
    `test_a_verdict_with_no_point_left_open_is_ignored_and_overwrites_nothing`
    until C19, and inverted rather than deleted, for the reason on the test above.

    Three verdicts against two comparisons is a log that was concatenated, and
    under C2's rule the extra record had to fall on the floor: a reader that let it
    overwrite the last point reported the wrong outcome on the most recent night,
    which is the one anybody looks at. That reasoning still holds, and the risk it
    names is now taken on purpose, because refusing it costs more than it saves.

    "Closes the most recent *open* point" cannot tell a concatenated log from a
    re-decided run either -- both are a verdict arriving after a point was closed,
    and nothing in the record distinguishes them. What it does do is drop the
    second verdict of a `C V1 V2` log, while the headline keeps the last verdict
    record unconditionally. A banner and a timeline disagreeing about tonight's
    outcome is the failure this module exists to prevent, and it is a worse one
    than an over-written night in a hand-concatenated file.

    So the last verdict in the log wins, on the last point opened, which is exactly
    what the headline does, and the two therefore agree by construction. What is
    still ignored is a verdict with no point at *all*: see
    `test_a_verdict_written_before_any_comparison_belongs_to_no_point`.
    """
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("run-one"),
        _a_comparison("run-two"),
        _a_verdict("GO", reason="one"),
        _a_verdict("NO-GO", reason="two"),
        _a_verdict("REVIEW", reason="a third verdict, written after both comparisons"),
    )
    points = read_series(log)
    assert len(points) == 2, "a verdict opened a point of its own"
    assert [point.verdict for point in points] == [None, "REVIEW"]
    assert [point.reason for point in points] == [
        None,
        "a third verdict, written after both comparisons",
    ]


def test_a_comparison_followed_by_two_verdicts_carries_the_second(tmp_path: Path):
    """Row eight, and the one row that separates "updates" from "closes once".

    Every other row of the agreement table reads the same under either rule. This
    one does not: a rule that closes a point and refuses to reopen it drops V2 and
    leaves the point reading GO, while `ReportModel.from_evidence` keeps the last
    verdict record it saw and prints NO-GO in the banner. One log, two answers,
    about the run at the right-hand end of the chart -- which is the disagreement
    the whole chunk exists to make impossible.
    """
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("tonight"),
        _a_verdict("GO", reason="decided, and then decided again"),
        _a_verdict("NO-GO", reason="the decision the log ends on"),
    )
    points = read_series(log)
    assert len(points) == 1, "the second verdict opened a point of its own"
    assert points[0].verdict == "NO-GO", (
        "the point kept the first verdict, so the timeline says GO where the banner says NO-GO"
    )
    assert points[0].reason == "the decision the log ends on"


def test_one_crashed_night_in_the_middle_of_a_log_moves_no_later_verdict(tmp_path: Path):
    """The Edges row that takes four nights to state and that two cannot reach.

    First-in-first-out does not merely mispair the crashed night. It shifts every
    verdict after it by one, and an evidence log only ever grows, so the shift is
    permanent and cumulative -- verbatim the failure `series.py`'s docstring says
    the module exists to prevent, produced by the rule chosen to prevent it.

    Night two is the crash: it compared, and died before deciding. On this log C2's
    rule hands night one its own GO, night two night three's NO-GO, night three
    night four's REVIEW, and leaves night four -- the run the banner reports on --
    with no verdict at all. Three of the four points move, and a two-night log
    would have shown only one of them.
    """
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("night-one"),
        _a_verdict("GO", reason="night one"),
        _a_comparison("night-two"),
        _a_comparison("night-three"),
        _a_verdict("NO-GO", reason="night three"),
        _a_comparison("night-four"),
        _a_verdict("REVIEW", reason="night four"),
    )
    points = read_series(log)
    assert [point.candidate_model for point in points] == [
        "night-one",
        "night-two",
        "night-three",
        "night-four",
    ]
    assert [point.verdict for point in points] == ["GO", None, "NO-GO", "REVIEW"]
    assert [point.reason for point in points] == [
        "night one",
        None,
        "night three",
        "night four",
    ]


def test_a_record_between_a_comparison_and_its_verdict_does_not_break_the_pairing(tmp_path: Path):
    """The reviewer's specific ask. `migkit.judging_completed` does not sit there
    today, but a future writer that logs one more line between the comparison and
    the decision must not cost the series every one of its verdicts, and a reader
    that only looks at the following line does exactly that."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("tonight"),
        ("migkit.judging_completed", {"judge": "accuracy", "n": 60}),
        ("assertion.evaluated", {"name": "no_regression", "passed": False}),
        _a_verdict("NO-GO"),
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].verdict == "NO-GO"


def test_a_judges_verdict_on_one_completion_is_not_the_runs_verdict(tmp_path: Path):
    """`judge.verdict` and `migkit.verdict` are different events, and the demo log
    holds forty of the first for every one of the second. A reader that matches on
    the tail of the event type closes the point with one judge's opinion of one
    completion, and `RunPoint.verdict` then reads `"pass"` on a night that was a
    NO-GO."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("tonight"),
        ("judge.verdict", {"verdict": "pass", "score": 5, "item_id": "extract-01"}),
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].verdict is None, "a judge's per-completion verdict closed the run's point"

    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("tonight"),
        ("judge.verdict", {"verdict": "pass", "score": 5, "item_id": "extract-01"}),
        _a_verdict("NO-GO"),
    )
    assert read_series(log)[0].verdict == "NO-GO"


def test_a_torn_final_line_is_dropped_rather_than_failing_the_whole_read(tmp_path: Path):
    """Row six, and the signature of a process killed mid-write. The nights that did
    finish are on disk and readable; refusing to draw any of them because the last
    one was interrupted loses a fortnight of history to a single `kill -9`."""
    fragment = _line(_COMPARISON, _comparison(), _ts(9))[:120]
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("finished"),
        _a_verdict("GO"),
        torn=fragment,
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].candidate_model == "finished"
    assert points[0].verdict == "GO"


def test_a_verdict_torn_off_mid_write_costs_the_verdict_and_not_the_point(tmp_path: Path):
    """The same row with the tear in the likelier place: the decision is the last
    thing written, so an interrupted run loses its verdict and keeps its numbers.
    The point stays, with `verdict is None`."""
    fragment = _line(_VERDICT, _verdict(), _ts(9))[:80]
    log = _write_log(tmp_path / "evidence.jsonl", _a_comparison("interrupted"), torn=fragment)
    points = read_series(log)
    assert len(points) == 1
    assert points[0].candidate_model == "interrupted"
    assert points[0].verdict is None


def test_a_malformed_line_that_is_not_the_last_is_an_error(tmp_path: Path):
    """Row seven, and the other half of row six. A tear at the end is a process that
    died; corruption in the middle is a file that has been edited or a disk that is
    lying, and reading past it silently reports a series with a hole in it that
    nothing on the page discloses."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("before"),
        _a_verdict("GO"),
        raw=['{"schema_version": 1, "ts": "2026-08-21T22:40:05', '{"filler": true}'],
    )
    with pytest.raises(EvidenceError):
        read_series(log)


def test_a_blank_line_in_the_middle_of_the_log_is_an_error_too(tmp_path: Path):
    """Row seven again, spelled the way `_stream_records` spells it. It is here to
    pin that the tolerance rules are rigor's rather than a second set written from
    memory: a reader and a writer of the same file that disagree about what a valid
    line is drift apart quietly, and always in the reader's favour."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("before"),
        _a_verdict("GO"),
        raw=["", '{"filler": true}'],
    )
    with pytest.raises(EvidenceError):
        read_series(log)


def test_a_directory_is_read_as_the_evidence_log_inside_it(tmp_path: Path):
    """Row eight. `from_evidence` takes either, and the CLI passes a work directory;
    a reader that takes only the file hands an operator who typed the argument that
    works everywhere else an `IsADirectoryError` from three frames down."""
    directory = tmp_path / "nightly"
    directory.mkdir()
    _write_log(
        directory / "evidence.jsonl",
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    points = read_series(directory)
    assert [point.candidate_model for point in points] == ["first", "second"]
    assert points == read_series(directory / "evidence.jsonl")


def test_a_directory_holding_no_evidence_log_is_an_error(tmp_path: Path):
    """Row eight resolving into row nine. An empty work directory is a mistyped path
    like any other, and the whole reason row nine is an error is that a mistyped
    path must not render as a valid report of a run that never happened."""
    directory = tmp_path / "empty"
    directory.mkdir()
    with pytest.raises(ArtifactError):
        read_series(directory)


def test_a_path_that_does_not_exist_is_an_error_and_never_an_empty_series(tmp_path: Path):
    """Row nine, and the contract supplies the reasoning: rigor reads a missing log
    as an empty one. So the one thing this must not do is what row one does. An
    operator who typed `evidence.json` gets a refusal that names the path, not a
    timeline of a fortnight in which nothing regressed."""
    missing = tmp_path / "evidence.json"
    with pytest.raises(ArtifactError) as raised:
        read_series(missing)
    assert str(missing) in str(raised.value), (
        f"the refusal has to name the path that was not there: {raised.value}"
    )


def test_a_path_that_does_not_exist_beside_one_that_does_is_still_an_error(tmp_path: Path):
    """The same row with a readable log one character away. A reader that falls back
    to a default location, or that resolves a near miss, turns a typo into a
    confident report of the wrong pipeline."""
    _write_log(tmp_path / "evidence.jsonl", _a_comparison("real"), _a_verdict("GO"))
    with pytest.raises(ArtifactError):
        read_series(tmp_path / "evidence.jsonl.bak")


# ----------------------------------------------------------------------------------
# The four "must not"s
# ----------------------------------------------------------------------------------


def test_the_points_come_back_in_file_order_even_when_their_dates_do_not(tmp_path: Path):
    """"Must not sort." The log is the record of what happened in the order it was
    written, and a reader that improves on it has decided by itself that a clock
    skew or a concatenated file is something it may silently reorder. Â§4.2 puts the
    date on the x-axis, but that is a decision for the layer that draws, and that
    layer needs the file order underneath it to say which run was appended when.

    The envelope timestamps and the payload's own `created` are both out of order
    here, and out of order the same way, so a sort on either key is visible."""
    created = [
        "2026-08-21T23:00:00.000000+00:00",
        "2026-08-19T09:00:00.000000+00:00",
        "2026-08-20T12:00:00.000000+00:00",
    ]
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("written-first", created=created[0]),
        _a_comparison("written-second", created=created[1]),
        _a_comparison("written-third", created=created[2]),
        timestamps=created,
    )
    points = read_series(log)
    assert [point.candidate_model for point in points] == [
        "written-first",
        "written-second",
        "written-third",
    ]
    assert [point.created for point in points] == created
    assert created != sorted(created), "the fixture is useless if its dates are already in order"


def test_a_point_whose_date_will_not_parse_keeps_its_place_in_the_series(tmp_path: Path):
    """The same rule, where sorting would have to invent something. Â§4.2 says a run
    whose `created` will not parse is a point with a known verdict and an unknown
    date, excluded from the timeline and *named beneath it* -- and it cannot be
    named if the reader has already moved it to the front or dropped it."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("before"),
        _a_comparison("undateable", created="the fourteenth of never"),
        _a_comparison("after"),
    )
    points = read_series(log)
    assert [point.candidate_model for point in points] == ["before", "undateable", "after"]


def test_two_identical_comparisons_are_two_points_and_not_one(tmp_path: Path):
    """"Must not deduplicate." A nightly job re-run against an unchanged golden set
    writes a byte-identical comparison, and those two nights are the evidence that
    the result is stable. Folding them reports one run and hides the repeat."""
    twice = _a_comparison("unchanged")
    log = _write_log(
        tmp_path / "evidence.jsonl",
        twice,
        _a_verdict("NO-GO"),
        (twice[0], copy.deepcopy(twice[1])),
        _a_verdict("NO-GO"),
    )
    points = read_series(log)
    assert len(points) == 2, "two identical runs were folded into one"
    assert points[0] == points[1]


def test_reading_a_series_never_calls_the_whole_log_into_memory(tmp_path: Path):
    """"Must not hold the whole log." `EvidenceLog.read()` is the call this reader
    exists to avoid: measured at 5.0 to 5.8 times the log's own bytes resident, an
    extra 502 MB on an 86 MB log, because rigor's `judge.verdict` record embeds the
    input, the output and the judge's raw reply for every completion. It is banned
    rather than merely discouraged, so here it is made to raise."""

    def _refuse(*args, **kwargs):
        raise AssertionError("read_series called EvidenceLog.read() and held the whole log")

    log = _write_log(
        tmp_path / "evidence.jsonl",
        *_noise(),
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(EvidenceLog, "read", _refuse)
        points = read_series(log)
    assert [point.candidate_model for point in points] == ["first", "second"]


#: Every place in the package a streaming reader could reasonably be bound: the
#: module that owns it, the module that used to own it, and the module under test.
#: Which of the three holds the definition is not something this chunk's contract
#: settles -- it permits promoting `report._stream_records` to a public name, and
#: C1's contract forbids `series` importing `report` at all -- so the two tests
#: below look for the reader rather than for a particular home for it.
_READER_HOMES = (
    "model_migration_kit.evidence",
    "model_migration_kit.report",
    "model_migration_kit.series",
)
_READER_NAMES = ("stream_records", "_stream_records")


def _bound_readers() -> dict[str, object]:
    """Every binding of a streaming reader the package exposes, by qualified name."""
    found: dict[str, object] = {}
    for module_name in _READER_HOMES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        for attribute in _READER_NAMES:
            reader = getattr(module, attribute, None)
            if reader is not None:
                found[f"{module_name}.{attribute}"] = reader
    return found


def test_the_whole_tree_holds_exactly_one_streaming_reader():
    """"Do not write a second reader", asserted as identity rather than as a call.

    The tolerance rules -- a torn last line dropped, anything malformed earlier an
    error, `newline` fixed so a bare carriage return inside a model's output does
    not split a record in one reader and not in the other -- are rigor's, and a
    second copy of them is how a reader and a writer of the same file drift apart:
    quietly, and always in the reader's favour.

    Where the definition lives is deliberately not asserted. The plan permits
    promoting `report._stream_records` to a public name, C1's contract forbids
    `series` importing `report`, and a third module owning the reader while the
    other two alias it satisfies both. What may not happen is two of these being
    different functions."""
    readers = _bound_readers()
    assert readers, f"no streaming reader found in any of {_READER_HOMES}"
    distinct = {id(reader) for reader in readers.values()}
    assert len(distinct) == 1, (
        "the tree holds more than one streaming reader, so the same log can now be "
        f"read two ways: {sorted(readers)}"
    )


def test_the_series_is_read_through_that_one_reader_and_reads_only_the_log(tmp_path: Path):
    """The other half of it: `read_series` has to actually go through the reader.

    Every binding found above is replaced, not just the module that defines it. A
    module-level `from ... import stream_records` binds the function into the
    importer's own namespace, and patching the module it came from would then be
    watching a name that nobody calls."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    read_series(log)  # let any deferred import happen before the reader is watched
    readers = _bound_readers()
    assert readers, f"no streaming reader found in any of {_READER_HOMES}"
    seen: list[Path] = []
    with pytest.MonkeyPatch.context() as patch:
        for qualified, original in readers.items():
            module_name, _, attribute = qualified.rpartition(".")

            def _spy(path, _original=original):
                seen.append(Path(path))
                yield from _original(path)

            patch.setattr(importlib.import_module(module_name), attribute, _spy)
        points = read_series(log)
    assert [point.candidate_model for point in points] == ["first", "second"]
    assert seen, "read_series read the log without going through the shared reader"
    assert set(seen) == {Path(log)}, seen


def test_the_evidence_log_is_opened_once_and_read_once(tmp_path: Path):
    """One streaming pass, in the contract's words. Two passes is not a style
    preference either: the evidence log is the largest artifact the pipeline writes
    -- 86 MB at 1000 items and n=50, against 45 MB of run plus judged -- and reading
    it twice reads those bytes twice on the machine that was already the one to run
    out."""
    log = _write_log(
        tmp_path / "evidence.jsonl",
        *_noise(),
        _a_comparison("first"),
        _a_verdict("GO"),
        _a_comparison("second"),
        _a_verdict("NO-GO"),
    )
    read_series(log)  # the count below is of one call, not of one call plus its imports
    opened: list[str] = []
    real_open = builtins.open

    def _counting(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", _counting)
        patch.setattr(io, "open", _counting)
        points = read_series(log)
    assert [point.candidate_model for point in points] == ["first", "second"]
    assert opened == [str(log)], f"the log was opened {len(opened)} time(s): {opened}"


def test_reading_a_series_opens_the_log_and_nothing_the_log_names(tmp_path: Path):
    """"Must not reach outside the given path." The comparison payload records a
    golden-set path and a config path, and `from_evidence` opens both -- resolved,
    refused if they leave the log's own directory, and disclosed in the provenance
    block. None of that machinery exists at this layer, so this layer opens nothing
    but the log it was handed. Both baits below are real files, so a reader that
    resolved them would succeed and say nothing."""
    goldenset_bait = tmp_path / "goldenset.jsonl"
    goldenset_bait.write_text('{"id": "bait"}\n', encoding="utf-8")
    config_bait = tmp_path / "nightly.toml"
    config_bait.write_text("[thresholds]\n", encoding="utf-8")

    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison(
            "tonight",
            goldenset_path=str(goldenset_bait),
            config_path=str(config_bait),
        ),
        _a_verdict("NO-GO"),
    )
    read_series(log)  # warm any deferred import
    opened: list[str] = []
    real_open = builtins.open

    def _recording(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "open", _recording)
        patch.setattr(io, "open", _recording)
        points = read_series(log)
    assert points[0].config_path == str(config_bait)
    assert set(opened) == {str(log)}, f"read_series opened more than the log: {opened}"


def test_the_cost_of_reading_stays_flat_as_the_log_grows_around_a_fixed_series(tmp_path: Path):
    """The amplification itself, asserted as a slope rather than a ceiling, for the
    reason `tests/test_evidence_scale.py` gives: a ceiling passes on a reader that
    has gone back to holding a fifth of the file.

    Both logs hold the same two comparisons, so the series is the same size in both
    and everything the peak does is the log. The padding is `judge.verdict` records,
    which is what a real log is mostly made of and what makes it the largest
    artifact this pipeline writes."""
    sizes: list[int] = []
    peaks: list[int] = []
    lengths: list[int] = []
    for index, padding in enumerate((1_500, 6_000)):
        log = tmp_path / f"grown-{index}.jsonl"
        _write_log(
            log,
            _a_comparison("first"),
            _a_verdict("GO"),
            *[
                ("judge.verdict", {"item_id": f"item-{n:05d}", "score": 4, "raw": "x" * 1500})
                for n in range(padding)
            ],
            _a_comparison("second"),
            _a_verdict("NO-GO"),
            timestamps=[_ts(n % 60) for n in range(padding + 4)],
        )
        sizes.append(log.stat().st_size)
        captured: list[tuple] = []
        peaks.append(_peak_bytes(lambda log=log, into=captured: into.append(read_series(log))))
        lengths.append(len(captured[0]))

    assert lengths == [2, 2], f"the two reads did not return the same series: {lengths}"
    assert sizes[1] > sizes[0] * 3
    assert peaks[1] < peaks[0] * 1.5, (
        f"peak allocation went {peaks[0]} -> {peaks[1]} bytes while the log went "
        f"{sizes[0]} -> {sizes[1]}; the cost of reading is supposed to be one line "
        f"and the points, not a share of the file"
    )


# ----------------------------------------------------------------------------------
# What the envelope is for, and what an unusable number is not
# ----------------------------------------------------------------------------------


def test_a_comparison_that_recorded_no_date_falls_back_to_the_line_it_was_written_on(
    tmp_path: Path,
):
    """Â§4.1's fallback, which only this function can supply: `run_point` takes
    `envelope_ts` by keyword, and `read_series` is the only caller that has the
    envelope to pass. C2's own edge table does not mention it, so this is the
    reading of Â§4.1 -- "falling back to the envelope `ts` when it is absent or
    unparseable" -- and not of C2's table.

    Without it, a payload from a future writer that drops `created` sorts as the
    epoch and puts that run at the far left of the timeline, years before the
    pipeline existed."""
    payload = _comparison()
    del payload["created"]
    log = _write_log(
        tmp_path / "evidence.jsonl",
        (_COMPARISON, payload),
        _a_verdict("GO"),
        timestamps=[_ts(31), _ts(32)],
    )
    points = read_series(log)
    assert len(points) == 1
    assert points[0].created == _ts(31)
    assert points[0].created_source == "envelope"


def test_a_number_the_reader_cannot_use_is_not_a_malformed_line(tmp_path: Path):
    """`json.loads` accepts a bare `NaN`, so a payload can hold a float that no
    arithmetic survives while the *line* is perfectly well formed. The two failures
    have to stay different: a malformed line is an `EvidenceError` and stops the
    read, whereas an unusable number is one field of one point, and C1's contract
    already says an `n_per_item` that will not coerce reads as 0.

    Conflating them means one strange float in a year-long log costs the whole
    report."""
    poisoned = (
        '{"schema_version": 1, "ts": "' + _ts(40) + '", "event_type": "' + _COMPARISON + '", '
        '"payload": {"n_per_item": NaN, "candidate": {"model_id": "odd"}}}'
    )
    log = _write_log(
        tmp_path / "evidence.jsonl",
        _a_comparison("before"),
        _a_verdict("GO"),
        raw=[poisoned],
    )
    points = read_series(log)
    assert [point.candidate_model for point in points] == ["before", "odd"]
    assert points[1].n_per_item == 0


# ----------------------------------------------------------------------------------
# `spot_check`, per chunk C11 and section 7.4
# ----------------------------------------------------------------------------------
#
# Written from the plan's `#### C11 -- the spot-check line` and `### 7.4`, and from
# nothing else: `series.spot_check` did not exist in this worktree when these were
# written, and no expected value below was obtained by running it. Every number is a
# `math.comb` quotient computed by hand and pasted here as a literal, deliberately --
# a test that recomputes `comb(N - F, k) / comb(N, k)` passes for any implementation
# that is consistently wrong in the same way, which is the failure mode this chunk is
# most exposed to.
#
# **The contract's own arithmetic is wrong and these tests do not follow it.** The
# edge table says `passing=88, failing=8, k=12` gives "approximately 0.351" and both
# the worked sentence and section 7.4 say "34%". The hypergeometric the same contract
# specifies gives
#
#     comb(88, 12) / comb(96, 12) == 0.3287693171387045
#
# and 0.351 is not a rounding of it -- it is `(88 / 96) ** 12 == 0.3519956...`, the
# *with-replacement* answer, i.e. the very same "independent draws" error the "must
# not" a dozen lines further down forbids. 34% is that wrong number rounded. So the
# contract's prose and the contract's formula disagree, the formula is the one that
# is right, and these tests assert 0.32877 -- 33% -- throughout. This was ruled on
# before dispatch and the same ruling was given to the author of `series.py`.
#
# **Why `series.spot_check` and not a direct import.** Naming these in this module's
# `from model_migration_kit.series import ...` line would fail the whole module at
# collection while C11 is unwritten, taking every pre-existing test in this file with
# it. Attribute access fails in the test that uses it and nowhere else, which is the
# same guarantee scoped to the tests that make the claim.


def test_no_spot_check_sentence_is_offered_when_nothing_was_failing():
    """The vacuous case. Nothing failed, so there is nothing a spot check could
    have missed, and "a spot check would have found nothing" is then not a
    concession -- it is a tautology dressed as one, and the most quotable line in
    the document would be quotable in exactly the case where it says nothing.

    This is the row an implementation optimising for "always show the persuasive
    line" gets wrong, which is why it is first."""
    assert series.spot_check(96, 0, 0) is None
    # Unstable items are counted as passing, so they do not rescue the sentence
    # either: a set with instability but no outright failure is still F == 0.
    assert series.spot_check(90, 0, 6) is None
    # And at a k the set is large enough for, so `None` here is the F == 0 rule
    # and not the N < k rule standing in for it.
    assert series.spot_check(96, 1, 0, k=12) is not None


def test_the_spot_check_counts_unstable_items_as_passing_so_f_is_only_established_regressions():
    """Three unstable items moved out of `passing` and into `unstable` must change
    nothing at all: same N, same F, same probability, same sentence. Only `F`
    decides the number, and only items this run *established* as failing enter
    `F`.

    This test was named `..._so_the_number_never_flatters_the_tool` and its
    reasoning ran that the alternative -- counting them as failures -- would
    "raise F to 11 and drop the probability, which reads as a *better* argument
    for the tool". That is backwards, and it is the same inversion the docstring
    shipped with. A dropped probability is a spot check that catches things more
    often, which is a *worse* argument for having run this harness. The rule
    keeps the bigger number, so it flatters the tool rather than restraining it;
    what defends it is that `F` names only established regressions.
    `test_excluding_unstable_items_from_f_raises_the_probability_and_the_docstring_says_that`
    holds the direction against the arithmetic."""
    without = series.spot_check(88, 8, 0, k=12)
    with_unstable = series.spot_check(85, 8, 3, k=12)

    assert without is not None
    assert with_unstable is not None
    assert with_unstable.probability == without.probability
    assert with_unstable.sentence == without.sentence
    assert with_unstable.items == without.items == 96
    assert with_unstable.failing == without.failing == 8
    # Both are N = 96, F = 8; `unstable` is reported as it was passed, because the
    # count is still a fact about the set even though it does not enter the sum.
    assert without.unstable == 0
    assert with_unstable.unstable == 3
    assert with_unstable.probability == pytest.approx(0.32877, abs=5e-6)


def test_the_probability_is_the_hypergeometric_one_and_not_the_with_replacement_one():
    """`comb(88, 12) / comb(96, 12)`, to the digit. The near miss is the danger:
    drawing the same twelve items *with* replacement gives 0.35200, which rounds
    to the 34% the contract's prose quotes, and both numbers look equally
    plausible printed in a sentence. Only one of them is the probability that a
    twelve-item sample drawn from ninety-six contains none of the eight bad
    ones -- you cannot inspect the same prompt twice and call it two prompts."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert check.probability == pytest.approx(0.3287693171387045, rel=1e-12)
    assert check.probability == pytest.approx(0.32877, abs=5e-6)
    assert check.probability != pytest.approx(0.3519956280141369, rel=1e-6)
    assert check.k == 12
    assert check.items == 96
    assert check.failing == 8


@pytest.mark.parametrize(
    ("passing", "failing", "expected", "with_replacement"),
    [
        (12, 4, 0.0005494505494505495, 0.03167635202407837),
        (18, 6, 0.006864988558352402, 0.03167635202407837),
    ],
    ids=["N=16", "N=24"],
)
def test_the_draw_is_of_items_without_replacement_and_not_a_completion_rate_raised_to_k(
    passing, failing, expected, with_replacement
):
    """Section 7.4's objection 2, made into a case where the two readings cannot
    be confused for each other. Both rows are a three-in-four pass rate, so the
    completion-level reading is `0.75 ** 12 == 0.0317` for each of them, while the
    item-level answer moves from 0.69% to 0.055% as the pool shrinks -- a factor
    of 58 at N = 16. On the contract's own N = 96 fixture the two readings are
    0.329 and 0.352 and a wrong implementation is nearly invisible; here it is off
    by more than an order of magnitude, which is the whole reason these rows exist
    rather than another variation on ninety-six items.

    At N = 16 the arithmetic also degenerates usefully: only twelve items pass, so
    there is exactly one clean twelve-item sample out of `comb(16, 12)` -- 1/1820.
    Any implementation treating the twelve draws as independent cannot produce
    that number by accident."""
    check = series.spot_check(passing, failing, 0, k=12)
    assert check is not None
    assert check.probability == pytest.approx(expected, rel=1e-12)
    assert check.probability != pytest.approx(with_replacement, rel=1e-3)


def test_the_docstring_describes_the_with_replacement_error_at_its_actual_size():
    """The "order of magnitude in the flattering direction" claim conflated two
    different errors, and only one of them is available on these numbers.

    Drawing *with replacement at the same item rate* -- the error the plan itself
    committed when it printed 0.351 as C11's expected value -- overstates by 7%,
    not by a factor of ten. Getting to 3% needs a *completion* pass rate of 0.75,
    which section 7.4's own determinism premise forbids: if all `n` draws of an
    item are identical the completion rate equals the item rate, so a set with an
    item rate of 88/96 cannot have a completion rate of 0.75.

    The 7% is the finding, not a footnote to it. An error of ten times announces
    itself; an error of 7% does not, and 35% and 33% read identically in a
    sentence."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    with_replacement = (88 / 96) ** 12
    assert with_replacement == pytest.approx(0.3519956280141369, rel=1e-12)
    # Overstatement, not understatement: the naive answer is the bigger one.
    assert with_replacement > check.probability
    assert with_replacement / check.probability == pytest.approx(1.0706, abs=5e-5)
    # And it is nowhere near an order of magnitude.
    assert with_replacement / check.probability < 1.1
    # The order-of-magnitude figure belongs to a rate the premise rules out.
    assert check.probability / 0.75**12 == pytest.approx(10.379, abs=5e-4)

    doc = " ".join((series.spot_check.__doc__ or "").split())
    assert "(88 / 96) ** 12 == 0.3520" in doc
    assert "0.75 ** 12 == 0.0317" in doc
    # The claim that shipped, which attached the ten-times figure to the error
    # that is actually 7% and pointed it the wrong way.
    assert "order of magnitude in the flattering direction" not in doc
    assert "it says 3% where the item-level answer is 33%" not in doc


def test_the_sentence_names_the_assumption_it_made_rather_than_leaving_it_implied():
    """Objection 3. Nobody picks twelve prompts at random -- an engineer picks
    twelve they believe are representative, and no arithmetic here models that. So
    the sentence has to say "drawn at random" out loud and let the reader discount
    it. A sentence that omits the phrase is claiming something about real spot
    checks that this function did not compute."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert "drawn at random" in check.sentence


def test_the_sentence_is_about_spot_checks_and_never_about_runs():
    """Section 7.4's objection 1, and the one a director finds. Nothing in this
    calculation is distributed over runs; the population is items and the thing
    being counted is samples of them. "in 34% of runs" invites the question "what
    is a run", and the honest answer -- that a run is not the unit here at all --
    is a hole in the most-quoted sentence in the document."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    lowered = check.sentence.lower()
    assert "spot check" in lowered
    assert "runs" not in lowered


@pytest.mark.parametrize(
    ("passing", "failing", "k"),
    [
        (88, 8, 12),
        (90, 6, 12),
        (80, 16, 12),
        (48, 4, 12),
        (36, 3, 12),
        (56, 8, 12),
    ],
    ids=["33pct", "44pct", "10pct", "34pct-at-N=52", "32pct-at-N=39", "17pct-at-N=64"],
)
def test_the_percentage_in_the_sentence_is_the_probability_that_was_computed(
    passing, failing, k
):
    """A sentence carrying a different number from the field beside it is the
    exact shape of this chunk's failure mode: the arithmetic is corrected and the
    prose keeps the old constant, or the prose is written from the contract's
    "34%" and the arithmetic is right.

    This was one fixture asserting the literal `"33%"`, and **one fixture can
    never establish agreement between two values**. An implementation that
    ignores `probability` entirely and interpolates the constant `"33%"` passed
    it, and passed the whole suite (mutant M27). Agreement is a claim about a
    relation, and a relation needs at least two points that disagree with each
    other. Six rows, five distinct percentages, and the expected string is
    derived from the returned `probability` rather than written down -- so a
    constant cannot satisfy them all and neither can a percentage computed from
    something other than the number in the field.

    The `34pct-at-N=52` row is deliberately the plan's old wrong answer arrived
    at honestly: 52 items with 4 failing really is 34%, so a suite that forbids
    the digits "34" everywhere would be forbidding a correct output."""
    check = series.spot_check(passing, failing, 0, k=k)
    assert check is not None
    expected = f"{round(check.probability * 100)}%"
    assert expected in check.sentence
    # The sentence is about *this* set, not a remembered one.
    assert f"{k}-prompt" in check.sentence
    assert f"{check.items} items" in check.sentence
    assert f"{failing} of which failed" in check.sentence
    # And the percentage is the chance of seeing *nothing*, on every row. The
    # clause is pinned here as well as in the literal-sentence test because the
    # inversion (M20) makes the same number mean the opposite thing, and a
    # meaning-inverting mutant should not hang on one assertion.
    assert "would have shown no failures at all in" in check.sentence


def test_no_other_percentage_appears_in_the_demo_sentence():
    """Kept from the original single-fixture test, scoped to the one set whose
    number the plan twice got wrong. 0.32877 is 33%; "34" or "35" in this
    particular sentence means the prose was written from the contract's struck
    numbers rather than from the arithmetic."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert "33%" in check.sentence
    assert "34" not in check.sentence
    assert "35" not in check.sentence


def test_the_sentence_counts_items_and_never_completions_or_prompts():
    """The unit is the one word this chunk exists to get right, and until now no
    test pinned it -- `"96 items"` could be mutated to `"96 completions"` or
    `"96 prompts"` and all 120 tests stayed green (M21, M22).

    The distinction is the entire content of section 7.4. The denominator is
    ninety-six *items*, i.e. ninety-six decisions; it is not ninety-six
    completions, and it is not ninety-six prompts either -- `k` is the count of
    prompts in this sentence, and reusing the word for `N` would make the line
    read as twelve prompts drawn from ninety-six prompts, which is the
    completion-level reading wearing the right noun."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    sentence = check.sentence
    assert "96 items" in sentence
    assert "completions" not in sentence
    assert "96 completions" not in sentence
    assert "96 prompts" not in sentence
    # "prompt" survives exactly once, attached to k.
    assert sentence.count("prompt") == 1
    assert "12-prompt" in sentence


def test_the_sentence_says_the_check_would_have_seen_nothing_not_caught_something():
    """The most dangerous survivor. Rewriting the clause to `"would have caught
    the regression in 33%"` inverts the meaning of the headline sentence -- the
    same number now claims a spot check *succeeds* a third of the time, which is
    an argument for skipping this harness rather than for running it -- and no
    test went red (M20).

    Nothing else in the suite constrained the verb, so the whole rendered
    sentence is pinned here as a literal. It is the one string in this module
    quoted directly into a document a director reads, and a change to it should
    have to be made on purpose."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert check.sentence == (
        "A 12-prompt spot check drawn at random from these 96 items, 8 of "
        "which failed, would have shown no failures at all in 33% of such "
        "checks."
    )
    # And the inversion named explicitly, for the next reader of this test.
    lowered = check.sentence.lower()
    assert "shown no failures at all" in lowered
    assert "caught" not in lowered
    assert "found" not in lowered
    assert "regression" not in lowered


def test_the_sentence_says_how_many_items_failed_so_the_number_is_checkable_in_place():
    """"Out of how many?" is the first question the line gets, and it used to be
    unanswerable from the line itself: `SpotCheck.failing` held the count and the
    sentence dropped it. A reader who cannot verify a claim where they read it
    has to take it on trust, which is the posture this whole chunk is written
    against."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert "8 of which failed" in check.sentence
    assert str(check.failing) in check.sentence
    # A different F must move the sentence, not just the field.
    other = series.spot_check(80, 16, 0, k=12)
    assert other is not None
    assert "16 of which failed" in other.sentence


def test_the_sentence_does_not_put_a_spot_check_inside_its_own_plural_denominator():
    """It read "A 12-prompt spot check ... in 33% of spot checks" -- a singular
    subject inside the plural set it is a member of, which eats its own tail. "of
    such checks" closes it while keeping the words the contract requires: the
    subject is still a *spot check* and the sentence still never says "runs"."""
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    lowered = check.sentence.lower()
    assert "of such checks" in lowered
    assert "of spot checks" not in lowered
    assert lowered.count("spot check") == 1
    assert "runs" not in lowered


def test_a_set_no_larger_than_the_check_offers_no_sentence_because_that_is_a_census():
    """N <= k. Twelve prompts against nine items is not a sample, it is the whole
    set read twice over, and the probability of missing a failure in it is zero by
    construction rather than by evidence. Printing a sentence here would be
    printing an argument that the set is too small to make.

    **N == k is excluded too, and this line was written the other way.** The
    blind suite asserted `series.spot_check(11, 1, 0, k=12) is not None` on the
    reasoning that N == k is "a sample -- of everything, once". It is a census,
    and the contract's own rationale for excluding N < k -- "the check would try
    every item" -- applies to it word for word. The sentence's whole rhetorical
    force is that you only looked at a *few*; a draw that takes the entire set
    and is then described as a spot check is an overclaim, produced by the one
    function in this module written to prevent overclaiming.

    Note what makes it worth an explicit guard rather than a rounding concern:
    at N == k the arithmetic is not wrong. `comb(N - F, k)` is 0 for any F >= 1,
    so the probability is a true 0.0 and the sentence renders cleanly and
    confidently. Nothing about the output announces that the "spot check" it
    describes read every item there was.

    This is a contract amendment out of review. `N < k` in the plan becomes
    `N <= k` here."""
    assert series.spot_check(8, 1, 0, k=12) is None
    # N == k: a census. Excluded, and this is the amendment.
    assert series.spot_check(11, 1, 0, k=12) is None
    assert series.spot_check(0, 12, 0, k=12) is None
    assert series.spot_check(6, 3, 3, k=12) is None
    # N == k + 1 is the smallest genuine sample and is still offered, so the
    # guard is `<=` and has not slid to `<= k + 1`.
    smallest = series.spot_check(11, 1, 0, k=11)
    assert smallest is not None
    assert smallest.items == 12
    assert series.spot_check(12, 1, 0, k=12) is not None


def test_an_empty_set_offers_no_sentence():
    """N == 0. There is nothing to draw from, and `comb(0, 12)` over `comb(0, 12)`
    is a zero-over-zero the caller should never be shown the result of."""
    assert series.spot_check(0, 0, 0) is None
    assert series.spot_check(0, 0, 0, k=1) is None


def test_a_set_that_fails_everywhere_still_gets_its_sentence_and_the_probability_is_zero():
    """The mirror of the vacuous case, and it is not vacuous. Every item fails, so
    no spot check of any size can come back clean, and 0.0 is the strongest
    version of the argument this line exists to make. Returning `None` here would
    suppress the sentence precisely where it is most earned."""
    check = series.spot_check(0, 96, 0, k=12)
    assert check is not None
    assert check.probability == 0.0
    assert check.items == 96
    assert check.failing == 96
    assert check.sentence
    assert "drawn at random" in check.sentence
    assert "runs" not in check.sentence.lower()


def test_a_spot_check_of_no_prompts_is_a_caller_error_and_not_a_certainty():
    """k == 0. `comb(N, 0) / comb(N, 0)` is 1.0, so the quiet answer is "a
    zero-prompt spot check finds nothing 100% of the time" -- true, useless, and
    indistinguishable in the rendered document from a real result. The contract
    makes it an error so it cannot reach a reader.

    Negative k is the same bug wearing a different sign, and it is worse: `comb`
    rejects it and the caller gets a ValueError from inside the arithmetic
    naming `comb`, not a message naming `k`. The guard is `k <= 0` and both
    sides of it are pinned here."""
    with pytest.raises(ValueError, match="positive number of prompts"):
        series.spot_check(88, 8, 0, k=0)
    with pytest.raises(ValueError, match="positive number of prompts"):
        series.spot_check(88, 8, 0, k=-1)
    with pytest.raises(ValueError, match="positive number of prompts"):
        series.spot_check(88, 8, 0, k=-12)
    # k == 1 is the smallest legitimate check and must not be caught by it.
    single = series.spot_check(88, 8, 0, k=1)
    assert single is not None
    assert single.k == 1
    assert single.probability == pytest.approx(88 / 96, rel=1e-12)


@pytest.mark.parametrize(
    ("passing", "failing", "unstable"),
    [(-1, 8, 0), (88, -8, 0), (88, 8, -1), (-1, -1, -1), (88, 8, -100)],
)
def test_negative_item_counts_are_a_caller_error_and_never_a_probability(
    passing, failing, unstable
):
    """The guard nothing tested, and it is load-bearing rather than defensive.

    Unguarded, `spot_check(88, -8, 0)` is not an exception and not a wrong
    number in the fourth decimal place. N becomes 80, `comb(80 - -8, 12)` is
    `comb(88, 12)`, and the quotient is **3.4088** -- a probability above 3 --
    which the renderer then formats without complaint as "... would have shown
    no failures at all in 341% of such checks." A negative count is a miswired
    caller, and a miswired caller must not be able to put an impossible
    percentage into the most-quoted line in the document.

    Checked before N is summed, because summing is what destroys the evidence:
    once the three counts are added, -8 failing and 96 passing is
    indistinguishable from 88 passing."""
    with pytest.raises(ValueError, match="cannot be negative"):
        series.spot_check(passing, failing, unstable, k=12)


def test_the_default_check_is_twelve_prompts_and_the_default_is_what_gets_used():
    """`k: int = 12` was never exercised for a non-None result -- every call that
    returned a `SpotCheck` passed `k=12` explicitly, and every call that omitted
    it returned `None` for some other reason. So the default could be changed to
    11 or 13 and the suite stayed green (M23, M24).

    Twelve is not arbitrary and it is not a tuning knob: it is the size the
    report's sentence is written around, and a default that silently disagreed
    with the prose would produce a document whose sentence and whose arithmetic
    describe different checks."""
    default = series.spot_check(88, 8, 0)
    explicit = series.spot_check(88, 8, 0, k=12)
    assert default is not None
    assert explicit is not None
    assert default.k == 12
    assert default == explicit
    assert default.probability == pytest.approx(0.3287693171387045, rel=1e-12)
    assert "12-prompt" in default.sentence
    # And it is genuinely the default rather than a coincidence of this set:
    # neighbouring k give different answers, so 11 or 13 would have shown.
    for neighbour in (11, 13):
        other = series.spot_check(88, 8, 0, k=neighbour)
        assert other is not None
        assert other.probability != default.probability


def test_the_spot_check_carries_the_six_fields_the_contract_names_and_is_frozen():
    """Transcribed from the contract's dataclass, asserted with `==` rather than
    a subset check so any addition or rename has to be made here on purpose.
    Frozen because a `SpotCheck` is a record of a computation that already
    happened; a `probability` that can be reassigned after the sentence naming it
    has been built is two numbers that can disagree."""
    assert [field.name for field in dataclasses.fields(series.SpotCheck)] == [
        "k",
        "items",
        "failing",
        "unstable",
        "probability",
        "sentence",
    ]
    check = series.spot_check(88, 8, 0, k=12)
    assert check is not None
    assert isinstance(check, series.SpotCheck)
    with pytest.raises(dataclasses.FrozenInstanceError):
        check.probability = 0.34


def test_excluding_unstable_items_from_f_raises_the_probability_and_the_docstring_says_that():
    """The direction of the thumb, pinned against the arithmetic rather than
    against a word list.

    This test replaces a word-presence check that asserted only `"unstable"` and
    `"passing"` appear in the docstring. That check passes for a docstring
    stating the rule's rationale *and* for one stating its exact negation, which
    is not a hypothetical: the shipped docstring claimed that counting unstable
    items as failures "would produce a larger, more quotable number", and it is
    measurably smaller. Both halves below have to agree or this goes red.

    The measurement, first, because it is what settles it. Excluding unstable
    items shrinks `F` from 11 to 8, and a smaller `F` means a *larger*
    probability -- a blinder spot check, which is a *stronger* argument for
    having run the harness. The rule therefore raises the quoted number and
    flatters the tool. It is defensible on the honesty of `F` -- the tool does
    not claim regressions it has not established -- and on nothing else, and a
    docstring that sells it as restraint is selling the opposite of what it is.
    """
    by_the_rule = series.spot_check(85, 8, 3, k=12)
    as_failures = series.spot_check(85, 11, 0, k=12)
    assert by_the_rule is not None
    assert as_failures is not None
    # Same set of 96 items either way; only F moves.
    assert by_the_rule.items == as_failures.items == 96
    assert by_the_rule.failing == 8
    assert as_failures.failing == 11
    assert by_the_rule.probability == pytest.approx(0.3287693171387045, rel=1e-12)
    assert as_failures.probability == pytest.approx(0.21061896729287496, rel=1e-12)
    # The whole ruling in one line: the rule's number is the bigger one.
    assert by_the_rule.probability > as_failures.probability

    # Whitespace-normalised: these are wrapped docstring lines, so a phrase can
    # straddle a newline and an unnormalised `in` check would miss it for a
    # reason that has nothing to do with what the docstring says.
    doc = " ".join((series.spot_check.__doc__ or "").split())
    lowered = doc.lower()
    assert "unstable" in lowered
    # The docstring must name the direction, and name it the way the two numbers
    # above just came out. Interpolated, not literal, so a docstring edited to
    # quote the swap the other way round cannot pass.
    high = f"{round(by_the_rule.probability * 100)}%"
    low = f"{round(as_failures.probability * 100)}%"
    assert f"from {high} to {low}" in doc, (
        "the docstring must quote the drop that counting unstable items as "
        "failures would cause, in the order the arithmetic produces it"
    )
    assert f"from {low} to {high}" not in doc
    assert "raises the quoted number" in lowered
    assert "strengthens the tool's own case" in lowered
    # The specific false claim that shipped, verbatim and in its near variants:
    # "counting unstable items as failures would produce a larger, more quotable
    # number". It would produce a smaller one.
    assert "larger, more quotable" not in lowered
    assert "would produce a larger" not in lowered
    assert "it is not a restraint on the number" in lowered


# The rounding rule, which nothing exercised. `_percent` is private and tested
# directly on purpose: the values that separate one rounding rule from another
# are exact halves, and `comb(N - F, k) / comb(N, k)` cannot be steered onto one.
# Pinning the rule only through `spot_check` pins it at 0.32877, which rounds to
# 33 under round-half-even, round-half-up, ceiling and truncation alike -- the
# single point where every candidate rule agrees, which is the one point that
# distinguishes none of them. Mutant M29 replaced `round` with "always round up,
# i.e. always toward the flattering number" and survived for exactly that reason.


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        # Exact zero is a computed certainty -- every item failed -- and must not
        # be softened into the hedge. This is the guard's `probability > 0` half.
        (0.0, "0%"),
        # Non-zero but rounding to zero: the hedge, because "0%" would claim a
        # spot check *always* catches it and nothing computed that.
        (0.004, "less than 1%"),
        # The half. Banker's rounding sends 0.5 to 0, so this is still the hedge.
        # "Always round up" returns "1%" here and dies.
        (0.005, "less than 1%"),
        (0.0051, "1%"),
        (0.006, "1%"),
        # Truncation returns "31%" here and dies.
        (0.315, "32%"),
        # The half at the demo's own magnitude. Round-half-even gives 32;
        # rounding up gives 33 -- the flattering number, and the demo's number,
        # which is precisely why a rule pinned only at 0.32877 cannot see it.
        (0.325, "32%"),
        (0.335, "34%"),
        # Rounding up gives 35 here and dies.
        (0.345, "34%"),
        # Rounding up gives 100, hence "more than 99%", and dies.
        (0.994, "99%"),
        # The upper guard: 99.5 rounds to 100, and "100%" would claim a spot
        # check could *never* have caught it. Also not computed.
        (0.995, "more than 99%"),
        (0.9951, "more than 99%"),
    ],
)
def test_the_percent_phrase_rounds_half_to_even_and_hedges_both_certainties(
    probability, expected
):
    """Every boundary of the rendering rule, including the two guards.

    The guards are not there because one end flatters the argument -- the
    docstring used to say both ends did, and they do not. "0%" claims a spot
    check *always* catches the regression, which undercuts the tool; "100%"
    claims it never does, which flatters it. They are wrong in opposite
    directions. The reason that actually holds is symmetric: **both ends assert
    a certainty the arithmetic did not compute**, and neither belongs in a
    sentence quoted in a review.

    `probability == 1.0` is not a row here. It needs `F == 0`, which returns
    `None` before any rendering happens, so the upper guard is a bare
    `percent == 100` and a row for 1.0 would pin behaviour on an unreachable
    input."""
    assert series._percent(probability) == expected


def test_the_small_probability_hedge_says_less_and_not_fewer():
    """"Fewer" wants a count noun and a probability is a proportion. It reads as
    a mistake, and it reads that way in the most-quoted sentence in the
    document, right beside a number whose correctness is the thing under
    review."""
    for probability in (0.0001, 0.004, 0.005):
        assert series._percent(probability) == "less than 1%"
        assert "fewer" not in series._percent(probability)


# ==================================================================================
# Chunk C4 -- the comparability key and the partition
# ==================================================================================
#
# Written from the same plan, chunk C4, and from the section it cites (§4.4), and
# from nothing else. `comparability_key`, `ComparabilityKey`, `Exclusion`, `Flag`
# and `partition_comparable` did not exist in this worktree when these were
# written; no expected value below was obtained by running any of them.
#
# **Amended by the fix pass at the end of this file, which is where the amendments
# are argued.** `Flag` is now `Caveat`; the three-tuple is now a `Partition`
# NamedTuple, which changes nothing any assertion in this section makes; and the
# edge table gained three exclusion rows. Nothing below this line was rewritten to
# suit the new code -- the whole section still passes as it was written, which is
# the evidence that the amendments were additions rather than a retreat.
#
# **Why every name below is reached as `series.something` rather than imported at
# the top of the file.** A module-level `from model_migration_kit.series import
# partition_comparable` fails at *collection* while the function is missing, and
# takes C1's and C2's 106 passing tests down with it -- a red suite that says
# nothing about which chunk is unfinished. An attribute lookup fails one test at a
# time, with `AttributeError: module 'model_migration_kit.series' has no attribute
# 'partition_comparable'`, which is what a test waiting on an unwritten function is
# supposed to look like. It is not a style preference and it is not indirection to
# be tidied away later.
#
# **Three departures from the contract as written, agreed in review before this
# file was started.** They are recorded here because a reader comparing this
# section against the plan will otherwise read them as drift:
#
# * `partition_comparable` returns a **three**-tuple, `(kept, excluded, flagged)`.
#   The edge table's last row requires "a flag on the kept point" and the
#   two-tuple signature has nowhere to put one. A flagged point is *also* in
#   `kept` -- a flag annotates a row, it does not remove it -- and empty input is
#   `((), (), ())`.
# * The flag is read off `judged_baseline`/`judged_candidate`. §4.4 names
#   `records`, which is a real key of the *payload* and not a field of `RunPoint`;
#   the field that carries the same reading is `judged_*`. Its docstring in
#   `series.py` is emphatic that it counts completions the judge **graded** rather
#   than completions the run produced, and the two are different numbers on any
#   run with a parse failure, so the sentence has to say which one it means.
# * Hashes are shown truncated to 16 characters, which is `_require_comparable`'s
#   own convention (`comparison.py:934`) and not a number invented here.
#
# **What "per-side coverage" means in the flag row, since it is the one thing in
# C4 the contract states twice in two different vocabularies.** §4.4: "`records`
# is recorded per side and is the available proxy; unequal `records` flags rather
# than excludes". Per *side* -- the baseline side and the candidate side of one
# run -- not per run within a group. That is the truncation `_require_comparable`
# catches key by key and the key cannot see at all: a run whose baseline was
# graded 60 times and whose candidate was graded 57 is a run where three
# completions went missing from one side, and the shortfall flatters whichever
# side finished. The group key is unchanged by it, so the row stays; the reader is
# told.

#: The two golden sets and the two judge panels these tests separate. They differ
#: from their first character, deliberately: a fixture whose two hashes share a
#: 16-character prefix would print two identical truncations and the exclusion
#: sentence would name one value twice while looking correct.
_GROUP_GOLDENSET = "5fef50364057cad869f16698df32d927b650778c34382f6f68d9fd53ba4e9a04"
_OTHER_GOLDENSET = "a1b2c3d4e5f60718293a4b5c6d7e8f901a2b3c4d5e6f708192a3b4c5d6e7f809"
_GROUP_JUDGES = "bb624f0ed1781d852cd961a9f4a338a3644ffddf262f4435c0d0f8628b7dcbc2"
_OTHER_JUDGES = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"

#: The four fields the contract's `ComparabilityKey` is made of.
_KEY_FIELDS = ("goldenset_hash", "judges_hash", "n_per_item", "baseline_model")


def _point(**changes: typing.Any) -> RunPoint:
    """One point in a series: C1's own payload fixtures, with `changes` applied.

    Built through `run_point` rather than by spelling out thirty-three constructor
    arguments, so every field C4 keys on holds the value a real `migkit.comparison`
    payload puts there rather than one typed in beside the assertion.
    """
    return dataclasses.replace(run_point(_comparison(), _verdict()), **changes)


def _group_key() -> typing.Any:
    """The key of the unmodified fixture point, which is every group's key here."""
    return series.comparability_key(_point())


# ----------------------------------------------------------------------------------
# The key: what it is made of, and what it deliberately ignores
# ----------------------------------------------------------------------------------


def test_the_comparability_key_is_made_of_the_four_fields_the_contract_names():
    """A fifth field would split groups that belong together and the field would
    render as a table of one row; a missing fourth is the failure mode the contract
    names outright -- "a table that quietly compares a 60-item run against a 40-item
    run is worse than no table"."""
    key = series.comparability_key(_point())
    assert dataclasses.is_dataclass(key)
    present = {field.name for field in dataclasses.fields(key)}
    assert present == set(_KEY_FIELDS), (
        f"missing from ComparabilityKey: {sorted(set(_KEY_FIELDS) - present)}; "
        f"not in the contract: {sorted(present - set(_KEY_FIELDS))}"
    )
    assert key.goldenset_hash == _GROUP_GOLDENSET
    assert key.judges_hash == _GROUP_JUDGES
    assert key.n_per_item == 5
    assert key.baseline_model == "gpt-baseline-v1"


def test_two_nights_that_tried_different_candidates_still_share_one_key():
    """The key answers one question -- may these two rows sit in the same table --
    and the whole purpose of that table is that the candidates differ. A key that
    included `candidate_model` would put every run in a group of one, and C5's field
    would then never have two candidates to render."""
    monday = _point(candidate_model="claude-candidate-v2", pass_rate=0.75)
    friday = _point(candidate_model="gpt-candidate-v9", pass_rate=0.81, created="")
    assert series.comparability_key(monday) == series.comparability_key(friday)


@pytest.mark.parametrize(
    "change",
    [
        {"goldenset_hash": _OTHER_GOLDENSET},
        {"judges_hash": _OTHER_JUDGES},
        {"n_per_item": 3},
        {"baseline_model": "gpt-baseline-v0"},
    ],
    ids=_KEY_FIELDS,
)
def test_a_run_that_differs_in_any_one_of_the_four_fields_does_not_share_the_key(change):
    """Each field asserted on its own, because a key built from three of the four
    passes every test that varies the fourth alongside another."""
    assert series.comparability_key(_point()) != series.comparability_key(_point(**change))


def test_a_comparability_key_can_be_a_dictionary_key_because_grouping_needs_one():
    """C5 groups points by this object. A key that is unhashable cannot be grouped
    at all, and one that is editable can be changed after the grouping that used
    it, which is a table whose rows no longer agree with the heading above them."""
    key = series.comparability_key(_point())
    same = series.comparability_key(_point(candidate_model="another"))
    assert {key: "group"}[same] == "group"
    assert len({key, same}) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        key.n_per_item = 3  # type: ignore[misc]


# ----------------------------------------------------------------------------------
# The partition, row by row of the contract's edge table
# ----------------------------------------------------------------------------------


def test_points_that_all_share_the_group_key_are_all_kept_and_none_excluded():
    """Row one. The ordinary case is worth pinning because it is the one a reader
    never checks: a partition that excluded a matching point would show a nightly
    job silently losing runs, with a reason nobody reads because the table looks
    plausible without them."""
    points = [_point(candidate_model=name) for name in ("first", "second", "third")]
    kept, excluded, flagged = series.partition_comparable(points, against=_group_key())
    assert [point.candidate_model for point in kept] == ["first", "second", "third"]
    assert excluded == ()
    assert flagged == ()


def test_an_empty_series_partitions_into_three_empty_tuples():
    """Row six. An empty log is the commonest input this code will ever see -- a
    pipeline whose first night has not run yet -- and it is not an error, an
    exception, or a `None` that the caller has to test for before unpacking."""
    assert series.partition_comparable([], against=_group_key()) == ((), (), ())


def test_a_run_with_a_different_n_per_item_is_excluded_and_the_reason_names_both_values():
    """The contract's named first-failing test, and the failure it exists to stop:
    "a table that quietly compares a 60-item run against a 40-item run is worse than
    no table". Three draws per item against five is that table -- the same golden
    set, the same judges, the same baseline, and 40% less evidence behind every
    number on the row.

    Both values are asserted, not merely that a reason exists. "excluded: n_per_item
    differs" is a sentence that passes a non-empty check and tells the reader
    nothing they can act on; the point of the sentence is that someone reading the
    exclusion list can see *which* run was the odd one and by how much."""
    odd = _point(candidate_model="three-draws", n_per_item=3)
    kept, excluded, _flagged = series.partition_comparable(
        [_point(candidate_model="five-draws"), odd], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["five-draws"]
    assert len(excluded) == 1, f"expected exactly one exclusion, got {excluded}"
    assert excluded[0].point == odd
    reason = excluded[0].reason
    assert re.search(r"\b3\b", reason), f"the excluded run's 3 draws are not named: {reason!r}"
    assert re.search(r"\b5\b", reason), f"the group's 5 draws are not named: {reason!r}"
    assert "n_per_item" in reason or "per item" in reason.lower(), (
        f"the sentence has to name the field that differed as well as the two "
        f"values, or a reader cannot tell what the numbers are: {reason!r}"
    )


def test_a_run_judged_against_a_different_golden_set_is_excluded_showing_both_hashes():
    """Row three. Two models scored on two different golden sets are, in
    `_require_comparable`'s words, "two unrelated numbers side by side".

    The hashes are asserted truncated to exactly sixteen characters -- present at
    16, absent at 17 -- because that is `comparison._require_comparable`'s own
    convention and this is the second place in the tree that prints a pair of
    them. Two truncation lengths for the same pair of hashes is how a reader ends
    up unable to match an exclusion here against a refusal there."""
    odd = _point(candidate_model="other-set", goldenset_hash=_OTHER_GOLDENSET)
    kept, excluded, _flagged = series.partition_comparable(
        [_point(candidate_model="right-set"), odd], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["right-set"]
    assert len(excluded) == 1, f"expected exactly one exclusion, got {excluded}"
    assert excluded[0].point == odd
    reason = excluded[0].reason
    assert _GROUP_GOLDENSET[:16] in reason, f"the group's golden set is not named: {reason!r}"
    assert _OTHER_GOLDENSET[:16] in reason, f"the odd run's golden set is not named: {reason!r}"
    assert _GROUP_GOLDENSET[:17] not in reason, f"truncated at more than 16: {reason!r}"
    assert _OTHER_GOLDENSET[:17] not in reason, f"truncated at more than 16: {reason!r}"
    assert "golden" in reason.lower(), (
        f"two bare hashes name neither the field nor what to do about it: {reason!r}"
    )


def test_a_run_graded_by_a_different_judge_panel_is_excluded_showing_both_panels():
    """Row four, and `_require_comparable`'s reasoning for it: "scores from two
    panels are readings from two instruments; the difference between them would
    measure the judges, not the models"."""
    odd = _point(candidate_model="other-panel", judges_hash=_OTHER_JUDGES)
    kept, excluded, _flagged = series.partition_comparable(
        [_point(candidate_model="right-panel"), odd], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["right-panel"]
    assert len(excluded) == 1, f"expected exactly one exclusion, got {excluded}"
    assert excluded[0].point == odd
    reason = excluded[0].reason
    assert _GROUP_JUDGES[:16] in reason, f"the group's judge panel is not named: {reason!r}"
    assert _OTHER_JUDGES[:16] in reason, f"the odd run's judge panel is not named: {reason!r}"
    assert _OTHER_JUDGES[:17] not in reason, f"truncated at more than 16: {reason!r}"


def test_a_run_that_differs_in_more_than_one_field_is_excluded_once_and_not_twice():
    """Not a row of the table, but the shape of every real disagreement: a run
    against a different golden set was usually graded by a different panel too. One
    point must produce one exclusion, or the rendered list repeats the same run
    under two headings and the count beneath the table is wrong."""
    odd = _point(
        candidate_model="different-everything",
        goldenset_hash=_OTHER_GOLDENSET,
        judges_hash=_OTHER_JUDGES,
        n_per_item=3,
    )
    kept, excluded, _flagged = series.partition_comparable(
        [_point(candidate_model="ordinary"), odd], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["ordinary"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["different-everything"]


# ----------------------------------------------------------------------------------
# The hole: an unrecorded hash is not a hash two runs share
# ----------------------------------------------------------------------------------


def test_a_run_that_recorded_no_golden_set_hash_is_excluded_and_the_reason_says_so():
    """Row five. An empty hash is not a value that happens to differ, so the
    sentence cannot be the one row three prints: `" vs 5fef50364057cad8"` names one
    golden set and an empty pair of quotes, and reads as a rendering bug rather
    than as the thing it is, which is a log that did not record what it was
    measured against."""
    odd = _point(candidate_model="no-hash", goldenset_hash="")
    kept, excluded, _flagged = series.partition_comparable(
        [_point(candidate_model="hashed"), odd], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["hashed"]
    assert len(excluded) == 1, f"expected exactly one exclusion, got {excluded}"
    assert excluded[0].point == odd
    reason = excluded[0].reason
    assert "record" in reason.lower(), (
        f"the sentence has to say the hash was not recorded rather than print it "
        f"as an empty value that differed: {reason!r}"
    )
    assert "golden" in reason.lower(), f"the sentence does not name the field: {reason!r}"


def test_two_runs_that_both_recorded_no_golden_set_hash_are_still_not_comparable():
    """The reviewer's note, and the single likeliest defect in this chunk. Two logs
    that both failed to record a golden-set hash have `==`-equal keys, and equal
    keys are exactly what a naive partition treats as a match. They are not
    comparable: an empty hash is not a golden set the two runs share, it is the
    absence of any evidence that they share one, and the table that results
    compares a model measured on last month's set against one measured on this
    month's and reports the difference as a regression.

    The fixture is checked first, because this test is worthless unless the two
    keys really are equal -- that equality is the whole thing the partition has to
    refuse to act on. Both points go, including the one the group key was taken
    from: a point whose golden set is unrecorded is not comparable to anything,
    itself included."""
    anchor = _point(candidate_model="anchor", goldenset_hash="")
    other = _point(candidate_model="also-blank", goldenset_hash="")
    key = series.comparability_key(anchor)
    assert key == series.comparability_key(other), (
        "the fixture is useless unless the two keys are genuinely equal; that "
        "equality is what the partition is required to refuse to act on"
    )
    kept, excluded, _flagged = series.partition_comparable([anchor, other], against=key)
    assert kept == (), f"an unrecorded golden set was matched against another: {kept}"
    assert [exclusion.point.candidate_model for exclusion in excluded] == [
        "anchor",
        "also-blank",
    ]


def test_two_runs_that_both_recorded_no_judges_hash_are_not_comparable_either():
    """The same hole in the field beside it. The contract's table names only the
    golden-set hash, and the reasoning it gives is the *absence*'s and not that
    particular field's: two runs that did not record which judge panel graded them
    have not been shown to share one, and a difference between two panels measures
    the judges rather than the models.

    Recorded here rather than assumed: this row is an extension of the contract's
    fifth row, agreed in review, and not a transcription of it."""
    anchor = _point(candidate_model="anchor", judges_hash="")
    other = _point(candidate_model="also-blank", judges_hash="")
    key = series.comparability_key(anchor)
    assert key == series.comparability_key(other), "the fixture's two keys must be equal"
    kept, excluded, _flagged = series.partition_comparable([anchor, other], against=key)
    assert kept == (), f"an unrecorded judge panel was matched against another: {kept}"
    assert [exclusion.point.candidate_model for exclusion in excluded] == [
        "anchor",
        "also-blank",
    ]


# ----------------------------------------------------------------------------------
# Stable order, and what a set or a dict would do to it
# ----------------------------------------------------------------------------------


def test_the_kept_points_come_back_in_the_order_they_were_given():
    """The contract's words: "ordering must be stable so the rendered list is
    stable". The dates below are out of order, and out of order in a way no sort
    would leave alone, so a partition that grouped through anything that reorders
    is visible here rather than in a table that changes its row order between two
    runs over one unchanged log."""
    created = [
        "2026-08-21T23:00:00.000000+00:00",
        "2026-08-19T09:00:00.000000+00:00",
        "2026-08-20T12:00:00.000000+00:00",
    ]
    points = [
        _point(candidate_model=name, created=when)
        for name, when in zip(("first", "second", "third"), created, strict=True)
    ]
    assert created != sorted(created), "the fixture is useless if its dates are already ordered"
    kept, _excluded, _flagged = series.partition_comparable(points, against=_group_key())
    assert [point.candidate_model for point in kept] == ["first", "second", "third"]
    assert [point.created for point in kept] == created


def test_three_identical_points_are_all_three_kept_rather_than_folded_into_one():
    """The other half of "not a set". A nightly job re-run against an unchanged
    golden set writes a byte-identical comparison, and those repeats are the
    evidence that a result is stable rather than noise. A partition that passed its
    points through a `set` to deduplicate them returns one row and reports three
    nights of agreement as one night."""
    points = [_point(), _point(), _point()]
    assert points[0] == points[1] == points[2], "the fixture must really be identical"
    kept, _excluded, _flagged = series.partition_comparable(points, against=_group_key())
    assert len(kept) == 3, f"identical points were folded together: {len(kept)} kept"


def test_the_excluded_points_come_back_in_the_order_they_were_given_too():
    """The exclusion list is rendered beneath the table, and a reader matches it
    against the log by eye. An order that comes out of a set is an order that
    changes between two runs over the same file, and there is nothing on the page
    that says the list is unordered."""
    points = [
        _point(candidate_model="excluded-first", n_per_item=3),
        _point(candidate_model="kept"),
        _point(candidate_model="excluded-second", goldenset_hash=_OTHER_GOLDENSET),
        _point(candidate_model="excluded-third", judges_hash=_OTHER_JUDGES),
    ]
    kept, excluded, _flagged = series.partition_comparable(points, against=_group_key())
    assert [point.candidate_model for point in kept] == ["kept"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == [
        "excluded-first",
        "excluded-second",
        "excluded-third",
    ]


def test_the_partition_hands_back_tuples_holding_the_very_points_it_was_given():
    """Tuples for the reason `RunPoint` is frozen: a caller that can append to the
    kept list can add a row the partition refused. And the points themselves, not
    copies -- a later chunk matches a flagged or excluded point against the series
    it came from, and a rebuilt point of equal value is not the same row."""
    points = [_point(candidate_model="first"), _point(candidate_model="second")]
    result = series.partition_comparable(points, against=_group_key())
    assert isinstance(result, tuple)
    assert len(result) == 3, f"the partition returns (kept, excluded, flagged), got {result}"
    kept, excluded, flagged = result
    assert isinstance(kept, tuple)
    assert isinstance(excluded, tuple)
    assert isinstance(flagged, tuple)
    for one, original in zip(kept, points, strict=True):
        assert one is original


def test_an_excluded_point_is_never_also_one_of_the_kept_points():
    """What "excluded" has to mean, asserted rather than assumed. A point that
    appears in both lists is a run that is named as unusable underneath a table it
    is also a row of."""
    points = [_point(candidate_model="kept"), _point(candidate_model="odd", n_per_item=3)]
    kept, excluded, _flagged = series.partition_comparable(points, against=_group_key())
    excluded_models = {exclusion.point.candidate_model for exclusion in excluded}
    assert excluded_models == {"odd"}
    assert {point.candidate_model for point in kept}.isdisjoint(excluded_models)


def test_the_group_key_can_only_be_passed_by_keyword():
    """The contract puts a bare `*` before it. Positionally, a caller could pass the
    key where the points belong -- or, worse, a second sequence of points -- and get
    back a partition of something they did not mean."""
    with pytest.raises(TypeError):
        series.partition_comparable([_point()], _group_key())  # type: ignore[misc]


# ----------------------------------------------------------------------------------
# The flag: a run whose two sides were not judged alike
# ----------------------------------------------------------------------------------


def test_a_run_whose_two_sides_were_judged_unequally_is_kept_and_flagged():
    """The table's last row. Coverage is the field the key cannot see, and §4.4
    refuses to paper over the gap: a run whose baseline was graded 60 times and
    whose candidate was graded 57 has the same hashes, the same `n_per_item` and the
    same baseline as every other row, so nothing about the key can exclude it -- and
    it should not be excluded, because the shortfall is already surfaced by
    `Completeness` and dropping the row would lose a night of history over three
    completions. It is kept, and it is annotated."""
    lopsided = _point(candidate_model="truncated", judged_candidate=57)
    assert lopsided.judged_baseline == 60, "the fixture must really be uneven"
    kept, excluded, flagged = series.partition_comparable(
        [_point(candidate_model="even"), lopsided], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["even", "truncated"]
    assert excluded == (), f"a coverage shortfall must flag rather than exclude: {excluded}"
    assert len(flagged) == 1, f"expected exactly one flag, got {flagged}"
    assert flagged[0].point == lopsided


def test_a_flagged_point_is_also_one_of_the_kept_points():
    """Stated separately because it is the whole difference between a flag and an
    exclusion, and a partition that returned the flagged point *instead* of keeping
    it would pass every assertion about the flag itself while dropping the row."""
    lopsided = _point(candidate_model="truncated", judged_candidate=57)
    kept, _excluded, flagged = series.partition_comparable([lopsided], against=_group_key())
    assert len(flagged) == 1
    assert flagged[0].point in kept


def test_the_flag_names_both_of_the_two_counts_that_disagreed():
    """The same requirement the exclusion sentence carries, for the same reason: a
    flag reading "the two sides were judged unequally" is a warning a reader cannot
    size. Three completions short of sixty is a footnote; forty-three is the run
    being unusable, and only the numbers say which one this is."""
    lopsided = _point(judged_baseline=60, judged_candidate=57)
    _kept, _excluded, flagged = series.partition_comparable([lopsided], against=_group_key())
    assert len(flagged) == 1, f"expected exactly one flag, got {flagged}"
    reason = flagged[0].reason
    assert re.search(r"\b60\b", reason), f"the baseline's 60 is not named: {reason!r}"
    assert re.search(r"\b57\b", reason), f"the candidate's 57 is not named: {reason!r}"


def test_the_flag_says_the_judge_graded_those_completions_and_does_not_call_them_records():
    """§4.4 names `records` as the available proxy, and `records` is a key of the
    payload rather than a field of `RunPoint`. The field that carries this reading
    is `judged_*`, whose docstring exists to stop exactly this sentence being
    written loosely: it counts completions the judge **graded**, which excludes the
    ones whose judge reply would not parse, and it is not the run's completion
    count that the report's "completions" row already shows. A flag that says "60
    records against 57" contradicts a row on the same page that says both sides
    produced 60, and the reader has no way to tell which number is wrong.

    "Completions" is not banned -- "graded 57 of 60 completions" is the correct
    sentence and says both things. What is banned is the word the payload uses for
    a different count."""
    lopsided = _point(judged_baseline=60, judged_candidate=57)
    _kept, _excluded, flagged = series.partition_comparable([lopsided], against=_group_key())
    assert len(flagged) == 1, f"expected exactly one flag, got {flagged}"
    reason = flagged[0].reason
    assert "graded" in reason.lower(), (
        f"the sentence has to say the judge *graded* these, which is what the "
        f"number counts: {reason!r}"
    )
    assert "records" not in reason.lower(), (
        f"'records' is the payload's word for a different count -- two judges "
        f"grading 60 completions are 120 records -- and using it here re-commits "
        f"the conflation `judged_*` was renamed to prevent: {reason!r}"
    )


def test_a_run_whose_two_sides_were_judged_alike_is_not_flagged():
    """The other side of it. A flag on every row is a flag on none of them, and the
    fixture's own sides are equal, so a partition that flagged unconditionally
    would pass every test above."""
    even = _point()
    assert even.judged_baseline == even.judged_candidate == 60
    kept, excluded, flagged = series.partition_comparable([even], against=_group_key())
    assert len(kept) == 1
    assert excluded == ()
    assert flagged == (), f"an evenly judged run was flagged: {flagged}"


def test_an_excluded_run_whose_sides_also_disagree_is_excluded_and_not_kept():
    """The two annotations meeting on one point. A truncated run against a different
    golden set is still not comparable, and a flag must not rescue it into the
    table: the flag annotates rows that are staying, and this one is not."""
    both = _point(
        candidate_model="truncated-and-odd",
        goldenset_hash=_OTHER_GOLDENSET,
        judged_candidate=57,
    )
    kept, excluded, flagged = series.partition_comparable(
        [_point(candidate_model="ordinary"), both], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["ordinary"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["truncated-and-odd"]
    assert [flag.point.candidate_model for flag in flagged] == [], (
        f"an excluded point was also flagged, so it is named twice beneath a table "
        f"it is not a row of: {flagged}"
    )


# ----------------------------------------------------------------------------------
# The shapes of the two annotations
# ----------------------------------------------------------------------------------


def test_an_exclusion_carries_the_point_it_removed_and_a_sentence_about_it():
    """Both halves are needed by the layer that renders the list beneath the table:
    the sentence to print, and the point to date it and name its candidate. A
    sentence on its own is a line of prose with no run attached to it."""
    odd = _point(candidate_model="odd", n_per_item=3)
    _kept, excluded, _flagged = series.partition_comparable([odd], against=_group_key())
    exclusion = excluded[0]
    assert dataclasses.is_dataclass(exclusion)
    assert {field.name for field in dataclasses.fields(exclusion)} == {"point", "reason"}
    assert exclusion.point is odd
    assert isinstance(exclusion.reason, str)
    assert exclusion.reason.strip(), "an exclusion with an empty reason explains nothing"
    with pytest.raises(dataclasses.FrozenInstanceError):
        exclusion.reason = "something else"  # type: ignore[misc]


def test_a_flag_carries_the_point_it_annotates_and_a_sentence_about_it():
    """The same shape as `Exclusion`, deliberately: the layer that renders them
    renders both, and two annotations with two different field names would be two
    templates for one list."""
    lopsided = _point(judged_candidate=57)
    _kept, _excluded, flagged = series.partition_comparable([lopsided], against=_group_key())
    flag = flagged[0]
    assert dataclasses.is_dataclass(flag)
    assert {field.name for field in dataclasses.fields(flag)} == {"point", "reason"}
    assert flag.point is lopsided
    assert isinstance(flag.reason, str)
    assert flag.reason.strip(), "a flag with an empty reason explains nothing"
    with pytest.raises(dataclasses.FrozenInstanceError):
        flag.reason = "something else"  # type: ignore[misc]


# ----------------------------------------------------------------------------------
# The bridge to `_require_comparable`, which §4.4 says must exist as code
# ----------------------------------------------------------------------------------


def _judged(
    model_id: str,
    *,
    goldenset_hash: str,
    judges_hash: str = _GROUP_JUDGES,
    n_per_item: int = 5,
    items: int = 12,
) -> JudgedArtifact:
    """A judged artifact: `items` items, every one graded `n_per_item` times.

    Shaped so that two of these differ in exactly the field the test varies.
    `coverage()` is (judge, item) -> how many samples were graded, so two artifacts
    built with the same `items` and `n_per_item` cover each other exactly and
    `_require_comparable` has nothing to object to but the hashes.
    """
    records = tuple(
        JudgeRecord(
            judge="accuracy",
            item_id=f"q{index:03d}",
            sample_index=sample,
            passed=sample < 4,
            score=5.0 if sample < 4 else 1.0,
        )
        for index in range(items)
        for sample in range(n_per_item)
    )
    return JudgedArtifact(
        model_id=model_id,
        goldenset_hash=goldenset_hash,
        judges_hash=judges_hash,
        n_per_item=n_per_item,
        records=records,
        judges=(
            {
                "name": "accuracy",
                "model": "fake-judge-v1",
                "adapter_class": "FakeAdapter",
                "rubric_hash": "cc39e4aad0ef5db821fb627bb1217bab78095543642634bc2d30581f642c6268",
            },
        ),
    )


def _point_of(baseline: JudgedArtifact, candidate: JudgedArtifact) -> RunPoint:
    """The `RunPoint` a comparison of these two artifacts would have written.

    Every field the comparability key is made of is read *off the artifacts* rather
    than typed in again beside them. A bridge whose two ends are two independent
    literals is not a bridge: it would go on passing after someone changed what
    `_require_comparable` compares, which is the one thing it exists to notice.
    """
    payload = _comparison(
        goldenset_hash=baseline.goldenset_hash,
        judges_hash=baseline.judges_hash,
        n_per_item=baseline.n_per_item,
    )
    payload["baseline"]["model_id"] = baseline.model_id
    payload["baseline"]["n_per_item"] = baseline.n_per_item
    payload["candidate"]["model_id"] = candidate.model_id
    payload["candidate"]["n_per_item"] = candidate.n_per_item
    return run_point(
        payload,
        _verdict(baseline_model=baseline.model_id, candidate_model=candidate.model_id),
    )


def test_grouping_never_admits_a_pair_that_require_comparable_would_have_refused():
    """§4.4's bridge, as code rather than as a paragraph.

    `_require_comparable` takes two live `JudgedArtifact`s and the report has
    payloads, so it cannot be called at this layer -- that is the whole reason
    `series` writes a second, narrower predicate. The claim being made is therefore
    not "grouping respects `_require_comparable`", which is not available, but
    "grouping never admits a pair `_require_comparable` would have refused on a
    field grouping can see". This is that claim, exercised on one pair.

    The scenario is the one a real log produces. Monday's run compared the baseline
    against `claude-candidate-v2` on one golden set; Friday's compared it against
    `claude-candidate-v3` after the golden set was edited. Each night is internally
    comparable and each wrote a perfectly good comparison payload -- both are
    asserted to pass `_require_comparable` below, so the refusal that follows cannot
    be an artifact of a broken fixture. What is not comparable is Monday's candidate
    against Friday's, and putting both rows in one table is exactly that comparison,
    made implicitly and with nothing on the page to disclose it.

    The refusal is *called*, not assumed, and the message is checked to be the
    golden-set one: `_require_comparable` has four ways to refuse, and a fixture
    that tripped the coverage check or the self-comparison check would prove
    something this test does not claim."""
    monday_baseline = _judged("gpt-baseline-v1", goldenset_hash=_GROUP_GOLDENSET)
    monday_candidate = _judged("claude-candidate-v2", goldenset_hash=_GROUP_GOLDENSET)
    friday_baseline = _judged("gpt-baseline-v1", goldenset_hash=_OTHER_GOLDENSET)
    friday_candidate = _judged("claude-candidate-v3", goldenset_hash=_OTHER_GOLDENSET)

    # Each night on its own is a comparison the pipeline would have run. If either
    # of these raised, the refusal below would prove nothing about the golden set.
    _require_comparable(monday_baseline, monday_candidate, allow_same_model=False)
    _require_comparable(friday_baseline, friday_candidate, allow_same_model=False)

    with pytest.raises(ArtifactError) as refused:
        _require_comparable(monday_candidate, friday_candidate, allow_same_model=False)
    assert "golden set" in str(refused.value).lower(), (
        f"the premise has to be the golden-set refusal specifically, or this test "
        f"is asserting about some other disagreement: {refused.value}"
    )

    monday = _point_of(monday_baseline, monday_candidate)
    friday = _point_of(friday_baseline, friday_candidate)
    kept, excluded, _flagged = series.partition_comparable(
        [monday, friday], against=series.comparability_key(monday)
    )
    assert [point.candidate_model for point in kept] == ["claude-candidate-v2"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["claude-candidate-v3"]


def test_grouping_also_refuses_the_pair_require_comparable_refuses_for_uneven_coverage():
    """The same bridge over the other field the two predicates share, where they
    reach the same answer by different routes. `_require_comparable` has no
    `n_per_item` check at all: it compares coverage key by key, and a run drawn
    three times per item covers every item three times against the other's five.
    Grouping cannot see coverage, and sees `n_per_item` instead.

    The two routes agreeing here is what makes the narrower predicate usable. If
    they disagreed, the report's table would admit a pair the pipeline itself
    refuses to compare, which is the failure §4.4 exists to rule out."""
    monday_baseline = _judged("gpt-baseline-v1", goldenset_hash=_GROUP_GOLDENSET)
    monday_candidate = _judged("claude-candidate-v2", goldenset_hash=_GROUP_GOLDENSET)
    thrifty_baseline = _judged("gpt-baseline-v1", goldenset_hash=_GROUP_GOLDENSET, n_per_item=3)
    thrifty_candidate = _judged(
        "claude-candidate-v3", goldenset_hash=_GROUP_GOLDENSET, n_per_item=3
    )

    _require_comparable(monday_baseline, monday_candidate, allow_same_model=False)
    _require_comparable(thrifty_baseline, thrifty_candidate, allow_same_model=False)

    with pytest.raises(ArtifactError) as refused:
        _require_comparable(monday_candidate, thrifty_candidate, allow_same_model=False)
    assert "cover" in str(refused.value).lower(), (
        f"the premise has to be the coverage refusal specifically: {refused.value}"
    )

    monday = _point_of(monday_baseline, monday_candidate)
    thrifty = _point_of(thrifty_baseline, thrifty_candidate)
    kept, excluded, _flagged = series.partition_comparable(
        [monday, thrifty], against=series.comparability_key(monday)
    )
    assert [point.candidate_model for point in kept] == ["claude-candidate-v2"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["claude-candidate-v3"]


# ==================================================================================
# Chunk C4, fix pass -- the six rulings the review settled, and the mutants it left
# ==================================================================================
#
# Everything above this line was written blind, against the plan. Everything below
# was written *after* a mutation-testing review of the implementation, and it says
# so rather than pretending otherwise: these tests exist because a specific mutant
# survived, or because a specific ruling changed the contract. Where a test names a
# mutant it names it, because the next reviewer's first question is "which of these
# is load-bearing and which is decoration", and the answer here is documented.
#
# The three departures recorded in the header above are amended by the review:
#
# * `partition_comparable` returns a **`Partition` NamedTuple**, not a bare
#   three-tuple. The fields are `kept`, `excluded`, `caveats`. Every positional
#   assertion above is unaffected -- a NamedTuple is a tuple -- and that was
#   verified rather than assumed before the change landed.
# * `Flag` is renamed **`Caveat`**. It collided with `enum.Flag`, which two
#   unwritten rendering chunks could import and shadow it with in silence, and it
#   shared one word with `spread_flagged` and `RunPoint.warnings`.
# * The edge table gains three rows, exactly as R14.2 amended the return type: a
#   run that graded nothing, a depth nobody recorded, and a baseline nobody
#   recorded are all **excluded**. A table is a floor and not a ceiling.

#: Two hashes sharing a sixteen-character prefix and differing after it. The
#: constants above deliberately differ from character 0, which is right for the
#: display assertions -- two identical truncations would let a sentence name one
#: value twice while looking correct -- and is exactly what blinds the *comparison*
#: assertions: `key.goldenset_hash[:16] != against.goldenset_hash[:16]` is a real
#: mutation, it is what someone writes when they mean "compare what is printed",
#: and against hashes that differ at character 0 it is indistinguishable from the
#: correct code. These two are the fixture that tells them apart.
_PREFIX_SHARED_A = "5fef50364057cad800000000000000000000000000000000000000000000aaaa"
_PREFIX_SHARED_B = "5fef50364057cad811111111111111111111111111111111111111111111bbbb"

#: The distinctive opening of each sentence `_incomparable` and `_ungraded` can
#: produce, longest-and-most-specific first, so that `_blamed` below returns the
#: field a reason actually blames rather than one it merely mentions in passing.
_BLAME_MARKERS = (
    ("ungraded", "nothing to compare"),
    ("ungraded", "graded on the baseline against"),
    ("goldenset_hash unrecorded", "no golden-set hash recorded"),
    ("goldenset_hash", "golden set "),
    ("judges_hash unrecorded", "no judges hash recorded"),
    ("judges_hash", "judge panel "),
    ("n_per_item unrecorded", "no draws per item recorded"),
    ("n_per_item", "draws per item against"),
    ("baseline_model unrecorded", "no baseline model recorded"),
    ("baseline_model", "baseline "),
)


def _blamed(reason: str) -> str:
    """Which field an exclusion sentence blames, read out of the sentence itself.

    Precedence is a claim about *which* of several true statements gets printed, so
    a test of it cannot assert "a reason exists" or even "the reason mentions the
    golden set" -- every one of these sentences mentions more than one thing. This
    reads the sentence the way a person does: the first marker that matches wins,
    and the markers are ordered so a more specific phrase is tried before a
    substring of it.
    """
    lowered = reason.lower()
    for label, marker in _BLAME_MARKERS:
        if marker in lowered:
            return label
    raise AssertionError(f"no exclusion sentence recognised in {reason!r}")


def _only_exclusion(points, key=None):
    """The single exclusion `points` must produce, with the count asserted first."""
    _kept, excluded, _caveats = series.partition_comparable(
        points, against=_group_key() if key is None else key
    )
    assert len(excluded) == 1, f"expected exactly one exclusion, got {excluded}"
    return excluded[0]


# ----------------------------------------------------------------------------------
# Ruling 1: a run that judged nothing is not a row
# ----------------------------------------------------------------------------------


def test_a_run_that_graded_nothing_on_either_side_is_excluded_rather_than_tabled():
    """The hole `0 != 0` leaves open, and the most degenerate run there is.

    A point with `judged_baseline == judged_candidate == 0` has no pass rate, no
    interval and an unrecorded floor, and it matches its group on all four key
    fields -- so nothing excluded it and nothing flagged it, and it rendered as an
    ordinary table row with em-dashes where the numbers go. That is the empty-hash
    hole wearing a third costume: `0 == 0` says "both sides silent", not "both
    sides the same".

    It is reachable from a payload and not only from a constructor -- the two tests
    named in C1's section, a comparison with no judges and a payload that is
    nothing but `{}`, both produce it -- which is why it is worth a row of its own
    rather than a note."""
    silent = _point(candidate_model="graded-nothing", judged_baseline=0, judged_candidate=0)
    kept, excluded, caveats = series.partition_comparable(
        [_point(candidate_model="ordinary"), silent], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["ordinary"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["graded-nothing"]
    assert caveats == (), f"an excluded point must not also be annotated: {caveats}"
    assert _blamed(excluded[0].reason) == "ungraded", (
        f"a run that graded nothing at all matches its group on all four key "
        f"fields, so blaming one of them would send the reader to the wrong "
        f"place: {excluded[0].reason!r}"
    )


def test_the_empty_comparison_is_refused_in_the_words_require_comparable_refuses_it_in():
    """The bridge, over the field the key cannot carry at all.

    `_require_comparable` raises on exactly this pair -- "neither artifact contains
    a judged completion, so there is nothing to compare. An empty comparison must
    not resolve to a verdict" -- and C4's claim is that grouping never admits a
    pair it would have refused on a field grouping can see. The refusal is called
    rather than quoted, so that a change to its wording is visible here.

    The sentence is asserted to borrow rather than to invent, because two parts of
    one page describing the same refusal in two vocabularies is how a reader
    concludes they are two different problems."""
    empty_baseline = _judged("gpt-baseline-v1", goldenset_hash=_GROUP_GOLDENSET, items=0)
    empty_candidate = _judged("claude-candidate-v2", goldenset_hash=_GROUP_GOLDENSET, items=0)
    with pytest.raises(ArtifactError) as refused:
        _require_comparable(empty_baseline, empty_candidate, allow_same_model=False)
    assert "nothing to compare" in str(refused.value).lower(), (
        f"the premise has to be the empty-comparison refusal specifically: {refused.value}"
    )

    silent = _point(judged_baseline=0, judged_candidate=0)
    reason = _only_exclusion([silent]).reason
    assert "nothing to compare" in reason.lower(), (
        f"the report's sentence has to be recognisably the CLI's: {reason!r}"
    )
    assert "verdict" in reason.lower(), (
        f"the half of the refusal that says why it matters -- an empty comparison "
        f"must not resolve to a verdict -- is the half worth keeping: {reason!r}"
    )


@pytest.mark.parametrize(
    ("judged_baseline", "judged_candidate"),
    [(0, 57), (57, 0)],
    ids=["baseline-graded-nothing", "candidate-graded-nothing"],
)
def test_a_run_with_one_side_graded_and_one_side_empty_is_excluded_not_merely_flagged(
    judged_baseline, judged_candidate
):
    """The ruling the review held firmly, and it is a ruling about wording as much
    as about principle.

    The coverage caveat's own sentence says the gap "may be lost judge replies
    rather than a truncated side". That is a true and useful thing to print about
    57 against 60. It is a false thing to print about 57 against 0: a side that
    graded nothing did not lose a few replies, and keeping the row would put a
    reason on the page that is not true of the run beside it.

    Both directions, because a check written on one side of the comparison passes
    every test that only ever empties the other."""
    lopsided = _point(
        candidate_model="one-sided",
        judged_baseline=judged_baseline,
        judged_candidate=judged_candidate,
    )
    kept, excluded, caveats = series.partition_comparable(
        [_point(candidate_model="ordinary"), lopsided], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == ["ordinary"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["one-sided"]
    assert caveats == (), (
        f"a side that graded nothing is an exclusion, and annotating it as well "
        f"would name the run twice beneath a table it is not a row of: {caveats}"
    )
    reason = excluded[0].reason
    assert re.search(r"\b0\b", reason), f"the empty side's 0 is not named: {reason!r}"
    assert re.search(r"\b57\b", reason), f"the graded side's 57 is not named: {reason!r}"


def test_a_run_graded_unevenly_but_not_emptily_is_still_kept_and_still_annotated():
    """The other side of the ruling above, so that "exclude on zero" cannot quietly
    become "exclude on any difference". 57 against 60 is three completions, the
    shortfall `Completeness` already reports, and dropping the row would cost a
    night of history to a suspicion the payload cannot settle."""
    lopsided = _point(candidate_model="truncated", judged_baseline=60, judged_candidate=57)
    kept, excluded, caveats = series.partition_comparable([lopsided], against=_group_key())
    assert [point.candidate_model for point in kept] == ["truncated"]
    assert excluded == (), f"a three-completion gap must not exclude: {excluded}"
    assert len(caveats) == 1


# ----------------------------------------------------------------------------------
# Ruling 2: an unrecorded depth and an unrecorded baseline are the same coercion
# ----------------------------------------------------------------------------------


def test_two_runs_whose_draw_depth_was_never_recorded_are_not_comparable_either():
    """C4's own "must not" says "coerce an empty hash to a match", which states a
    principle and not a field list. `_count` returns 0 for a `n_per_item` the
    payload never carried, so "not recorded" and "recorded as zero" are the same
    integer, and two runs whose depth nobody wrote down compare equal here. That is
    the named failure mode word for word -- "a table that quietly compares a
    60-item run against a 40-item run" -- reached by two unknown depths presented
    as one.

    The fixture's keys are asserted equal first, exactly as the two hash tests
    above do it: the equality is the thing the partition has to refuse to act on,
    and a fixture whose keys differ would prove nothing."""
    anchor = _point(candidate_model="anchor", n_per_item=0)
    other = _point(candidate_model="also-blank", n_per_item=0)
    key = series.comparability_key(anchor)
    assert key == series.comparability_key(other), "the fixture's two keys must be equal"
    kept, excluded, _caveats = series.partition_comparable([anchor, other], against=key)
    assert kept == (), f"an unrecorded draw depth was matched against another: {kept}"
    assert [exclusion.point.candidate_model for exclusion in excluded] == [
        "anchor",
        "also-blank",
    ]


@pytest.mark.parametrize("side", ["point", "group"], ids=["run-is-silent", "group-is-silent"])
def test_a_recorded_draw_depth_against_an_unrecorded_one_is_excluded(side):
    """Both directions of the same absence. A run that recorded 5 draws per item
    and a group that recorded none have not been shown to have sampled alike, and
    the direction of the silence does not change that -- but a check written on
    one side only passes whichever direction the author happened to try."""
    point = _point(candidate_model="odd", n_per_item=0 if side == "point" else 5)
    key = dataclasses.replace(_group_key(), n_per_item=0 if side == "group" else 5)
    reason = _only_exclusion([point], key).reason
    assert _blamed(reason) == "n_per_item unrecorded", reason
    assert "unrecorded" in reason.lower(), (
        f"a 0 printed as 0 reads as a run that drew nothing, which is a different "
        f"claim from a run whose depth nobody wrote down: {reason!r}"
    )
    assert re.search(r"\b5\b", reason), f"the recorded side's 5 is not named: {reason!r}"


def test_two_runs_whose_baseline_model_was_never_recorded_are_not_comparable():
    """The third instance of the one coercion, and the one the implementation's own
    wording had already half-admitted: the baseline exclusion sentence has read
    `{key.baseline_model or 'unrecorded'}` on both sides since it was written. The
    empty case was handled in the sentence and not in the rule, and the `or
    'unrecorded'` on the *group's* side could only ever fire for a group that had
    already been kept."""
    anchor = _point(candidate_model="anchor", baseline_model="")
    other = _point(candidate_model="also-blank", baseline_model="")
    key = series.comparability_key(anchor)
    assert key == series.comparability_key(other), "the fixture's two keys must be equal"
    kept, excluded, _caveats = series.partition_comparable([anchor, other], against=key)
    assert kept == (), f"an unrecorded baseline was matched against another: {kept}"
    assert [exclusion.point.candidate_model for exclusion in excluded] == [
        "anchor",
        "also-blank",
    ]


@pytest.mark.parametrize("side", ["point", "group"], ids=["run-is-silent", "group-is-silent"])
def test_a_named_baseline_against_an_unnamed_one_is_excluded(side):
    """A column of deltas is a column only if every delta was measured from the
    same baseline. A baseline nobody recorded is not evidence that it was."""
    point = _point(
        candidate_model="odd", baseline_model="" if side == "point" else "gpt-baseline-v1"
    )
    key = dataclasses.replace(
        _group_key(), baseline_model="" if side == "group" else "gpt-baseline-v1"
    )
    reason = _only_exclusion([point], key).reason
    assert _blamed(reason) == "baseline_model unrecorded", reason
    assert "unrecorded" in reason.lower(), (
        f"the sentence must not print an empty gap where the model id goes: {reason!r}"
    )
    assert "gpt-baseline-v1" in reason, f"the recorded side is not named: {reason!r}"


# ----------------------------------------------------------------------------------
# Ruling 3: the blame order, which the docstring claimed and the code did not have
# ----------------------------------------------------------------------------------


def test_an_edited_golden_set_is_blamed_for_the_golden_set_even_with_no_panel_hash():
    """The review's demonstration, as code.

    `_incomparable`'s docstring claims fields are tested in `_require_comparable`'s
    order "so that when a run differs in several ways at once the two guards blame
    the same one". It was false: both hash fields were swept for *absence* before
    either was tested for a *mismatch*, so this run -- judged against an edited
    golden set by a pipeline version that did not record panel hashes -- was blamed
    here for the judges and there for the golden set. That is not a contrived pair.
    It is what upgrading the pipeline mid-week looks like.

    The other guard is called, not quoted, so the two orders are compared against
    each other rather than against a transcription of one of them."""
    anchor = _judged(
        "claude-candidate-v2", goldenset_hash=_GROUP_GOLDENSET, judges_hash=_GROUP_JUDGES
    )
    edited = _judged("claude-candidate-v3", goldenset_hash=_OTHER_GOLDENSET, judges_hash="")
    with pytest.raises(ArtifactError) as refused:
        _require_comparable(anchor, edited, allow_same_model=False)
    assert "golden set" in str(refused.value).lower(), (
        f"the premise is that the other guard blames the golden set here: {refused.value}"
    )

    odd = _point(goldenset_hash=_OTHER_GOLDENSET, judges_hash="")
    reason = _only_exclusion([odd]).reason
    assert _blamed(reason) == "goldenset_hash", (
        f"the two guards blame different fields on the same disagreement, which is "
        f"the thing `_incomparable`'s docstring promises they do not: {reason!r}"
    )


@pytest.mark.parametrize(
    ("changes", "blamed"),
    [
        ({"goldenset_hash": "", "judges_hash": ""}, "goldenset_hash unrecorded"),
        ({"goldenset_hash": _OTHER_GOLDENSET, "judges_hash": ""}, "goldenset_hash"),
        ({"goldenset_hash": _OTHER_GOLDENSET, "judges_hash": _OTHER_JUDGES}, "goldenset_hash"),
        ({"goldenset_hash": _OTHER_GOLDENSET, "n_per_item": 3}, "goldenset_hash"),
        ({"judges_hash": "", "n_per_item": 0}, "judges_hash unrecorded"),
        ({"judges_hash": "", "baseline_model": ""}, "judges_hash unrecorded"),
        ({"judges_hash": _OTHER_JUDGES, "n_per_item": 0}, "judges_hash"),
        ({"judges_hash": _OTHER_JUDGES, "n_per_item": 3}, "judges_hash"),
        ({"n_per_item": 0, "baseline_model": ""}, "n_per_item unrecorded"),
        ({"n_per_item": 3, "baseline_model": ""}, "n_per_item"),
        ({"n_per_item": 3, "baseline_model": "gpt-baseline-v0"}, "n_per_item"),
        ({"baseline_model": ""}, "baseline_model unrecorded"),
        ({"baseline_model": "gpt-baseline-v0"}, "baseline_model"),
        ({"goldenset_hash": _OTHER_GOLDENSET, "judged_baseline": 0, "judged_candidate": 0},
         "goldenset_hash"),
    ],
    ids=[
        "both-hashes-silent-blames-the-golden-set",
        "edited-set-beats-a-missing-panel-hash",
        "edited-set-beats-a-different-panel",
        "edited-set-beats-a-different-depth",
        "missing-panel-hash-beats-a-missing-depth",
        "missing-panel-hash-beats-a-missing-baseline",
        "different-panel-beats-a-missing-depth",
        "different-panel-beats-a-different-depth",
        "missing-depth-beats-a-missing-baseline",
        "different-depth-beats-a-missing-baseline",
        "different-depth-beats-a-different-baseline",
        "a-missing-baseline-alone",
        "a-different-baseline-alone",
        "the-key-is-settled-before-the-run-is-asked-what-it-graded",
    ],
)
def test_a_run_wrong_in_several_ways_is_blamed_for_the_first_one_in_order(changes, blamed):
    """The order, asserted pair by adjacent pair: golden set unrecorded, golden set
    differs, judges unrecorded, judges differ, `n_per_item`, `baseline_model`, and
    only then what the run actually graded.

    This is `_require_comparable`'s order, and matching it is the whole reason to
    have one: a reader who has seen the CLI refuse a pair must find the report
    blaming the same field, or the two look like two problems. Mutants that
    reorder these checks survived the suite entirely before this test existed --
    nothing anywhere noticed precedence in either direction, which means the
    docstring's promise was untested as well as untrue.

    Every case below is a run that is genuinely wrong in two ways. Neither answer
    is a lie; only one of them is the one the other guard gives."""
    reason = _only_exclusion([_point(**changes)]).reason
    assert _blamed(reason) == blamed, reason


# ----------------------------------------------------------------------------------
# Ruling 4: the eight mutants that survived the blind suite
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["goldenset_hash", "judges_hash"], ids=["golden-set", "judges"])
def test_two_hashes_that_agree_for_sixteen_characters_and_then_differ_are_not_a_match(field):
    """Mutants S1 and S2: `[:_HASH_WIDTH]` applied to *both sides* of the equality,
    not merely to what is printed. Sixteen hex characters is 64 bits and a
    collision is not the worry -- the worry is that this is the mutation a careful
    person makes, because the truncation is already right there in the sentence and
    comparing what you print looks like consistency.

    Every hash fixture above differs from character 0, which is correct for the
    display assertions and is exactly what makes them blind to this: truncated or
    not, those two hashes are unequal either way. These two are not."""
    key = dataclasses.replace(_group_key(), **{field: _PREFIX_SHARED_A})
    odd = _point(candidate_model="odd", **{field: _PREFIX_SHARED_B})
    assert _PREFIX_SHARED_A[:16] == _PREFIX_SHARED_B[:16], "the fixture must share a prefix"
    assert _PREFIX_SHARED_A != _PREFIX_SHARED_B, "and it must differ past it"
    kept, excluded, _caveats = series.partition_comparable(
        [_point(candidate_model="matches", **{field: _PREFIX_SHARED_A}), odd], against=key
    )
    assert [point.candidate_model for point in kept] == ["matches"]
    assert [exclusion.point.candidate_model for exclusion in excluded] == ["odd"], (
        "two hashes were compared by their printed prefixes, so any two runs "
        "whose hashes agree for sixteen characters are now one group"
    )


@pytest.mark.parametrize("field", ["goldenset_hash", "judges_hash"], ids=["golden-set", "judges"])
def test_a_run_with_a_hash_measured_against_a_group_without_one_says_so(field):
    """Mutant S3: checking only the point's own hash and never the group's. Every
    unrecorded-hash test above empties the *point*, so a one-sided check passes all
    of them -- and this is not an exotic direction: the group key is taken from
    whichever point anchors the table, and that point may be the one that recorded
    nothing.

    What the mutant costs is the **sentence**, not the row, and the sentence is the
    whole of `Exclusion`'s reason for existing. With only the point's side checked,
    a recorded hash falls through to the *mismatch* branch and is measured against
    `_hash("")`, so the report prints "golden set 5fef50364057cad8 against the
    group's unrecorded ... Model A on one set and model B on another are two
    unrelated numbers". That is a claim about two golden sets. There is one. The
    reader is told to go and find the other, and it does not exist.

    Asserted by classifying the sentence rather than by looking for a word, because
    both branches contain the word: "unrecorded" is what the mismatch branch prints
    for the absent side, which is precisely why a keyword assertion here would be
    green against the mutant."""
    key = dataclasses.replace(_group_key(), **{field: ""})
    reason = _only_exclusion([_point(candidate_model="hashed")], key).reason
    assert _blamed(reason) == f"{field} unrecorded", (
        f"an absent hash on the group's side is being reported as a hash that "
        f"differed, which names a second golden set that does not exist: {reason!r}"
    )
    assert "the group" in reason.lower(), (
        f"the sentence has to say which side was silent, and it was the group: {reason!r}"
    )


def test_an_exclusion_sentence_never_prints_an_empty_gap_where_a_value_belongs():
    """Mutant S8: `_UNRECORDED = ""`. The constant's own comment is the test --
    "against the group's " with nothing after it looks like a formatting bug, not
    like a missing fact -- and nothing asserted that the word was there, so
    emptying the constant survived the whole suite.

    Asserted on all three fields the word now serves, because one constant serving
    three sentences is exactly the arrangement where a test on one of them is taken
    for a test on all three."""
    for changes, key in (
        ({"goldenset_hash": ""}, _group_key()),
        ({"judges_hash": ""}, _group_key()),
        ({"n_per_item": 0}, _group_key()),
        ({"baseline_model": ""}, _group_key()),
    ):
        reason = _only_exclusion([_point(**changes)], key).reason
        assert "unrecorded" in reason.lower(), (
            f"the absent value is printed as a gap rather than as a word: {reason!r}"
        )
        assert "  " not in reason, f"a doubled space is the gap showing through: {reason!r}"
        assert "'s ." not in reason and "'s -" not in reason, (
            f"the sentence trails off where a value belongs: {reason!r}"
        )


def test_three_identical_excluded_points_are_three_exclusions_and_not_one():
    """Mutant S9: de-duplicating exclusions. There is a `kept` twin of this test
    above and there was no `excluded` one, so folding the exclusion list through a
    set or a dict keyed on the point survived.

    A nightly job re-run three times against the wrong golden set writes three
    identical comparisons. Collapsing them to one exclusion makes the count beneath
    the table disagree with the log it was read from, and the count beneath the
    table is the only thing on the page that says how much was left out."""
    odd = _point(candidate_model="odd", n_per_item=3)
    points = [odd, odd, odd]
    assert points[0] == points[1] == points[2], "the fixture must really be identical"
    _kept, excluded, _caveats = series.partition_comparable(points, against=_group_key())
    assert len(excluded) == 3, f"identical exclusions were folded together: {excluded}"
    assert [exclusion.point for exclusion in excluded] == points


def test_three_identical_caveated_points_are_three_caveats_and_not_one():
    """The same collapse one tuple over. A caveat list shorter than the rows it
    annotates is a table with an unmarked row on it."""
    lopsided = _point(judged_candidate=57)
    _kept, _excluded, caveats = series.partition_comparable(
        [lopsided, lopsided, lopsided], against=_group_key()
    )
    assert len(caveats) == 3, f"identical caveats were folded together: {caveats}"


# ----------------------------------------------------------------------------------
# Mutant S6: `is_identifying` had no test at all, and answered the wrong question
# ----------------------------------------------------------------------------------
#
# The property was `hashes_recorded` and looked at two of the key's four fields.
# That answered the question C5 actually asks -- "can this key establish
# comparability?" -- only while the two hashes were the only grounds for exclusion.
# Once an unrecorded `n_per_item` and an unrecorded `baseline_model` became grounds
# too (ruling 2), a key with both hashes and `n_per_item == 0` answered `True` and
# then had every member removed by the partition: C5 builds a group and is told it
# is empty, with no sentence on the page saying why.
#
# So it is widened to all four fields and renamed for the question rather than for
# a stale version of the implementation. The rename is made here, before C5 and C7
# type against it, on the same argument that made `Flag` -> `Caveat` free.

#: Each field of the key, blanked the way a payload that never recorded it blanks
#: it. `n_per_item` is the odd one and that is the whole point of ruling 2: its
#: absence is an `int`, so it reads as a recorded zero rather than as a silence.
_KEY_BLANKS = {
    "goldenset_hash": "",
    "judges_hash": "",
    "n_per_item": 0,
    "baseline_model": "",
}


@pytest.mark.parametrize(
    ("changes", "identifying"),
    [
        ({}, True),
        ({"goldenset_hash": ""}, False),
        ({"judges_hash": ""}, False),
        ({"n_per_item": 0}, False),
        ({"baseline_model": ""}, False),
        ({"goldenset_hash": "", "judges_hash": ""}, False),
        (dict(_KEY_BLANKS), False),
        ({"goldenset_hash": "   "}, False),
        ({"judges_hash": "\t"}, False),
        ({"baseline_model": " "}, False),
        ({"n_per_item": -1}, False),
    ],
    ids=[
        "all-four-recorded",
        "no-golden-set",
        "no-panel",
        "no-depth",
        "no-baseline",
        "neither-hash",
        "nothing-at-all",
        "a-golden-set-of-spaces",
        "a-panel-of-one-tab",
        "a-baseline-of-one-space",
        "a-negative-depth",
    ],
)
def test_a_key_knows_whether_it_can_establish_comparability_at_all(changes, identifying):
    """Mutant S6: this is a public property of a public type and it had zero tests,
    so `and` became `or`, `bool(...)` became `True`, and nothing anywhere went red.

    It is not decoration. Its docstring says what it is for -- anything that groups
    on `ComparabilityKey` needs it, because dataclass equality alone will happily
    merge every run that recorded nothing into one confident-looking group -- and
    C5 is the chunk that will group. A property that answers `True` for a key made
    of two empty strings hands C5 the exact failure this chunk exists to prevent,
    one layer up and with no sentence attached.

    A negative depth is in the table because `_count` will return one: `n_per_item`
    recorded as `-1` is not a depth, and the property's test has to be the
    partition's test rather than a bare `!= 0`."""
    assert series.comparability_key(_point(**changes)).is_identifying is identifying


def test_the_property_is_false_on_every_ground_the_partition_excludes_a_key_on():
    """One row per exclusion ground, which is the cheap form of a drift test.

    The property is not an independent opinion about what a key needs; it is the
    same four questions `_incomparable` asks, answered ahead of time for a caller
    that has to *build* groups before it can partition them. Every ground the
    partition removes a point on has to be a ground the property refuses to vouch
    for, or the two have drifted -- and the drift is silent, because a group that
    renders empty looks like a group with nothing in it.

    The exclusion is checked to be the ground this row is about, through `_blamed`,
    so a test that passed for the wrong reason -- excluded, but on some other
    field -- is not available."""
    for field, blank in _KEY_BLANKS.items():
        point = _point(**{field: blank})
        key = series.comparability_key(point)
        kept, excluded, _caveats = series.partition_comparable([point], against=key)
        assert kept == (), f"the partition kept a point whose {field} is unrecorded"
        assert _blamed(excluded[0].reason) == f"{field} unrecorded", excluded[0].reason
        assert key.is_identifying is False, (
            f"`is_identifying` vouches for a key the partition excludes on its "
            f"{field}, so a caller that groups on it builds a group whose every "
            f"member is then removed: {key}"
        )


def test_the_key_and_the_partition_agree_over_every_combination_of_the_four_fields():
    """The drift test proper, and the reason a fifth ground cannot be added to
    `_incomparable` without being added to the property as well.

    Sixteen keys -- each of the four fields recorded or not -- each partitioned
    against itself. A point compared against its own key is excluded exactly when
    the key fails to identify anything, so `bool(kept)` and `is_identifying` are
    two computations of one answer and any disagreement is the drift.

    Self-partitioning is what makes this total rather than a sample. The fixture
    grades 60 against 60 and names two different models, so neither `_ungraded` nor
    the self-comparison caveat can fire and change the answer for a reason that has
    nothing to do with the key -- which is also why the property is documented as
    saying nothing about either.

    The field list is read off `ComparabilityKey` rather than typed here, so that
    a *fifth* field added to the key -- which is what "a new ground" concretely
    looks like -- fails on the next line with a sentence naming it, instead of
    being quietly enumerated over the old four and reported as agreement."""
    named = {field.name for field in dataclasses.fields(series.ComparabilityKey)}
    assert named == set(_KEY_BLANKS), (
        f"the key gained or lost a field, so this test no longer covers it and "
        f"`is_identifying` has to be told about it too: {named ^ set(_KEY_BLANKS)}"
    )
    fields = list(_KEY_BLANKS)
    for mask in range(1 << len(fields)):
        changes = {
            name: _KEY_BLANKS[name] for index, name in enumerate(fields) if mask >> index & 1
        }
        point = _point(**changes)
        key = series.comparability_key(point)
        kept, _excluded, caveats = series.partition_comparable([point], against=key)
        assert caveats == (), f"the fixture must be plain, so only the key decides: {changes}"
        assert bool(kept) is key.is_identifying, (
            f"`is_identifying` says {key.is_identifying} and the partition "
            f"{'kept' if kept else 'excluded'} the point the key was taken from. "
            f"A rule was added to one of them and not the other: unrecorded {changes}"
        )


# ----------------------------------------------------------------------------------
# Ruling 6: two sentences that were not true, and one emptiness test that was loose
# ----------------------------------------------------------------------------------


def test_the_unrecorded_hash_sentence_does_not_claim_both_runs_were_silent_when_one_was_not():
    """The sentence always ended "Two runs that both failed to record one are
    equally silent", including in the case where only one of them failed -- which
    is the majority case, and which reads as a claim about *this pair*. A reason
    printed beneath a table is read as a statement about the run it names, and a
    reader who checks the group's hash and finds one there concludes the report is
    wrong about something."""
    one_sided = _only_exclusion([_point(goldenset_hash="")]).reason
    assert "both" not in one_sided.lower(), (
        f"the group recorded a hash, so nothing about this pair is 'both': {one_sided!r}"
    )

    anchor = _point(candidate_model="anchor", goldenset_hash="")
    two_sided = _only_exclusion([anchor], series.comparability_key(anchor)).reason
    assert "both" in two_sided.lower(), (
        f"when neither side recorded one, that is the fact worth printing and the "
        f"sentence stops being a claim about one run: {two_sided!r}"
    )


@pytest.mark.parametrize(
    "blank",
    ["", " ", "   ", "\t", "\n"],
    ids=["empty", "one-space", "three-spaces", "a-tab", "a-newline"],
)
def test_a_hash_of_nothing_but_whitespace_is_a_hash_nobody_recorded(blank):
    """Free while we are here, and a real coercion rather than a hypothetical: a
    writer that padded the field wrote no hash, and `bool(" ")` is `True`. Two runs
    whose `goldenset_hash` is `"  "` would otherwise have matching keys *and* pass
    the emptiness test, which is the one combination that gets all the way to a
    rendered row."""
    anchor = _point(candidate_model="anchor", goldenset_hash=blank)
    other = _point(candidate_model="also-blank", goldenset_hash=blank)
    key = series.comparability_key(anchor)
    assert key == series.comparability_key(other), "the fixture's two keys must be equal"
    kept, excluded, _caveats = series.partition_comparable([anchor, other], against=key)
    assert kept == (), f"a whitespace hash was matched against another: {kept}"
    assert len(excluded) == 2
    assert "unrecorded" in excluded[0].reason.lower(), (
        f"a hash of spaces printed as spaces is the formatting-bug sentence again: "
        f"{excluded[0].reason!r}"
    )


# ----------------------------------------------------------------------------------
# The self-comparison: named, and deliberately not excluded
# ----------------------------------------------------------------------------------


def test_a_run_comparing_a_model_against_itself_is_kept_and_named():
    """The fourth refusal `_require_comparable` makes, and the one that must *not*
    become an exclusion here.

    That guard refuses a self-comparison unless it is told not to -- and it is told
    not to: `allow_same_model=True` is how the A/A calibration run is logged. That
    run is legitimate, deliberate, and the one row on the page that shows what "no
    difference" measures like on this panel, so excluding it would delete the
    control. What must not happen is the third thing, which is what happened
    before: admitting it silently, as an ordinary row whose flat delta a reader
    takes for a result."""
    calibration = _point(candidate_model="gpt-baseline-v1")
    assert calibration.baseline_model == calibration.candidate_model, "the fixture must be A/A"
    kept, excluded, caveats = series.partition_comparable(
        [_point(candidate_model="claude-candidate-v2"), calibration], against=_group_key()
    )
    assert [point.candidate_model for point in kept] == [
        "claude-candidate-v2",
        "gpt-baseline-v1",
    ]
    assert excluded == (), f"the A/A calibration run must not be excluded: {excluded}"
    assert [caveat.point for caveat in caveats] == [calibration]
    reason = caveats[0].reason
    assert "gpt-baseline-v1" in reason, f"the sentence does not name the model: {reason!r}"
    assert "allow_same_model" in reason or "itself" in reason.lower(), (
        f"the sentence has to say what the reader is looking at: {reason!r}"
    )


def test_a_run_that_is_both_lopsided_and_a_self_comparison_carries_two_caveats():
    """Two annotations on one row, which is the case a single-caveat-per-point
    implementation gets wrong by dropping whichever it checks second. They are
    different facts about the run and a reader needs both: how much was graded, and
    what was being compared."""
    both = _point(candidate_model="gpt-baseline-v1", judged_candidate=57)
    kept, excluded, caveats = series.partition_comparable([both], against=_group_key())
    assert kept == (both,)
    assert excluded == ()
    assert len(caveats) == 2, f"expected a coverage caveat and a self-comparison one: {caveats}"
    assert all(caveat.point is both for caveat in caveats)
    reasons = " || ".join(caveat.reason for caveat in caveats)
    assert "graded" in reasons.lower()
    assert "itself" in reasons.lower()


def test_an_ordinary_two_model_run_carries_no_self_comparison_caveat():
    """A caveat on every row is a caveat on none of them."""
    _kept, _excluded, caveats = series.partition_comparable([_point()], against=_group_key())
    assert caveats == (), f"an ordinary comparison was annotated: {caveats}"


# ----------------------------------------------------------------------------------
# Ruling 5: the two reshapes, before C5, C6 and C7 type them
# ----------------------------------------------------------------------------------


def test_the_partition_names_its_three_tuples():
    """R15.3's defect class, in its last instance: a contract that tells the caller
    about an *absence* through a return type with room only for presences. Unpacked
    positionally, the third element is the one that becomes `_flagged` and is
    dropped. Named, it has to be dropped on purpose."""
    result = series.partition_comparable(
        [_point(candidate_model="odd", n_per_item=3), _point(judged_candidate=57)],
        against=_group_key(),
    )
    assert result._fields == ("kept", "excluded", "caveats")
    assert result.kept is result[0]
    assert result.excluded is result[1]
    assert result.caveats is result[2]


def test_naming_the_three_tuples_costs_a_positional_caller_nothing():
    """Asserted rather than assumed, because it is the entire argument for making
    the change now: every assertion written against the bare three-tuple above --
    `== ((), (), ())`, `isinstance(result, tuple)`, `len(result) == 3`, and
    positional unpacking -- has to go on passing untouched, or this is a breaking
    change dressed up as a rename."""
    empty = series.partition_comparable([], against=_group_key())
    assert empty == ((), (), ())
    assert isinstance(empty, tuple)
    assert len(empty) == 3
    kept, excluded, caveats = empty
    assert (kept, excluded, caveats) == ((), (), ())
    assert series.partition_comparable([_point()], against=_group_key()) == (
        (_point(),),
        (),
        (),
    )


def test_the_kept_with_a_note_type_is_called_caveat_and_not_flag():
    """`Flag` collides with `enum.Flag`, and C6 and C7 are rendering chunks where a
    `from enum import Flag` shadows it with no error at all. It would also have
    shared a namespace with C5's `spread_flagged` and `RunPoint.warnings`, which is
    three concepts and one word.

    The old name is asserted *gone* rather than merely aliased: an alias left
    behind is how both names end up in a downstream signature."""
    assert dataclasses.is_dataclass(series.Caveat)
    assert not hasattr(series, "Flag"), (
        "the old name is still bound, so a consumer can still be written against it"
    )
    assert "Caveat" in series.__all__
    assert "Partition" in series.__all__
    assert "Flag" not in series.__all__


def test_the_partitions_annotations_resolve_to_the_named_types():
    """The names have to be real at runtime, not only in a docstring: C5, C6 and C7
    will type against them, and `from __future__ import annotations` makes a
    misspelled one invisible until something calls `get_type_hints`."""
    hints = typing.get_type_hints(series.partition_comparable)
    assert hints["return"] is series.Partition
    fields = typing.get_type_hints(series.Partition)
    assert set(fields) == {"kept", "excluded", "caveats"}
    assert fields["kept"] == tuple[RunPoint, ...]
    assert fields["excluded"] == tuple[series.Exclusion, ...]
    assert fields["caveats"] == tuple[series.Caveat, ...]


# ==================================================================================
# Chunk C7 -- the trend and the parameter strip
# ==================================================================================
#
# Written from the plan's chunk C7 **as amended by R15**, and from nothing else.
# `trend`, `Trend`, `Succession`, `parameter_strip` and `ParameterChange` did not
# exist in this worktree when these were written -- the implementation was being
# typed in another worktree at the time and was never read. No expected value below
# was obtained by running any of them.
#
# **Every new name is reached as `series.something`.** The reason is the one C4's
# banner gives 700 lines above and it has not changed: a module-level `from
# model_migration_kit.series import trend` fails at *collection* while the function
# is missing and takes the ~197 tests above it down with it, which is a red suite
# that says nothing about which chunk is unfinished. An attribute lookup fails one
# test at a time, with `AttributeError: module 'model_migration_kit.series' has no
# attribute 'trend'`.
#
# **What R15 changed, and why every test here turns on it.** The old signature was
# `trend(points, *, baseline_model, candidate_model)` -- it filtered the series by
# *the very field that moves*. Night 14 under `-b-v2` therefore landed in a
# different series from night 13 under `-b-v1`, so `parameter_strip` saw
# `previous is None`, so the `model_id` row reported `changed=False` against an
# empty `before`: the first run changed nothing because there was nothing to change
# from. The strip was always able to show the change and was prevented by its own
# caller. R15 replaces the filter with a **caller-declared lineage** and the bare
# tuple with a `Trend`, and the test that carries this chunk --
# `test_fourteen_nights_...` below -- is that whole argument made executable.
#
# **Three rulings taken here that the contract does not spell out.** Recorded so a
# reader comparing this section against the plan reads them as rulings and not as
# drift:
#
# * A point whose `candidate_model` is outside the declared lineage is asserted
#   only to be **absent from `points`**, never to be present in `excluded`.
#   `Exclusion` is C4's type and carries a comparability verdict; a run that simply
#   is not part of this line was never adjudicated and has no such verdict. Which
#   of the two the implementation does is not pinned, because the contract does not
#   say and the tests do not need it to.
# * The same for a run measured from a different `baseline_model`: absent from
#   `points`, and nothing asserted about where it went.
# * `n_per_item` and `items` are held to the same unrecorded-is-not-unchanged rule
#   as the four string fields, on `0`. The contract's edge row spells the
#   unrecorded value `""`, which is the string fields only -- but `RunPoint.items`
#   documents `0` as "unrecorded" in as many words, `_depth` in `series.py` already
#   prints `0` as the word "unrecorded", and a depth nobody wrote down rendered as
#   "5 -> 5" licenses exactly the false attribution the reviewer note is about.
#   **This is the one place a reasonable implementer might disagree**, and it is
#   two assertions in one test rather than scattered through the section so that
#   overturning it is a small edit.
#
# **On the word this section checks for.** The contract says to use "the existing
# `THRESHOLD_SOURCE_UNRECORDED` idiom" for a value nobody recorded, and the idiom is
# meant rather than the constant: `series.py` must not import `report`, and it
# already carries its own `_UNRECORDED = "unrecorded"` with a comment saying it
# adopts `report`'s vocabulary deliberately, so that two parts of one page do not
# describe the same absence in different words. The assertions below therefore
# require the substring `"recorded"` -- which `"unrecorded"` satisfies, as does
# `report`'s longer sentence, and which no rendered hash, model id or integer
# does. What they refuse is a blank, and a blank is the failure mode. Asserting
# either constant's exact text would pin the wording rather than the guarantee.
#
# **`Trend` has five fields and `caveats` is the fifth.** R15.3 names a defect
# class -- "a contract states the caller is told about an absence and gives a
# return type with room only for presences" -- and the four-field `Trend`
# committed it: `partition_comparable` computes caveats and `Trend` had nowhere to
# put them, so an A/A calibration run and an unevenly-graded night were drawn as
# ordinary rows. Corrected in the contract, appended last so that prefix unpacking
# of the first four still reads.
#
# **Partitioning is unconditional, including for a one-element lineage.** The
# contract's "a single-element sequence reproduces today's behaviour exactly" and
# its rule that `trend` partitions through `partition_comparable` contradict each
# other on a log holding an incomparable run, because the old `trend` partitioned
# nothing. Ruled: the single-element case reproduces the old *selection*, not the
# old permissiveness -- a line joining one model's runs across an edited golden set
# is the same false line, and the number of declared ids has nothing to do with it.
# So nothing below asserts that a one-element lineage skips exclusion.

#: The lineage of R15's worked example, and C16's night 14: thirteen nights under
#: one id and the fourteenth under its successor, declared together by the
#: operator. The two ids differ *only* in the version suffix, deliberately -- this
#: is the pair a `rstrip("-v2")` implementation would join by itself, and R15.1
#: forbids exactly that, so the fixture has to be the shape that tempts it.
_LINEAGE_V1 = "synthetic-candidate-b-v1"
_LINEAGE_V2 = "synthetic-candidate-b-v2"
_LINEAGE = (_LINEAGE_V1, _LINEAGE_V2)

#: The baseline every point in this section was measured from -- `_comparison`'s
#: own, so that `_point()` needs no override to belong to the line.
_BASELINE = "gpt-baseline-v1"

#: The fourteen nights, as literals rather than as a comprehension, so that the
#: assertion about the order the trend returns them in is a transcription and not a
#: second run of the formula that built them.
_FOURTEEN_NIGHTS = (
    "2026-08-01T22:40:58+00:00",
    "2026-08-02T22:40:58+00:00",
    "2026-08-03T22:40:58+00:00",
    "2026-08-04T22:40:58+00:00",
    "2026-08-05T22:40:58+00:00",
    "2026-08-06T22:40:58+00:00",
    "2026-08-07T22:40:58+00:00",
    "2026-08-08T22:40:58+00:00",
    "2026-08-09T22:40:58+00:00",
    "2026-08-10T22:40:58+00:00",
    "2026-08-11T22:40:58+00:00",
    "2026-08-12T22:40:58+00:00",
    "2026-08-13T22:40:58+00:00",
    "2026-08-14T22:40:58+00:00",
)

#: The six tracked parameters, in the order the contract's table lists them and the
#: order its `ParameterChange.name` comment repeats. Two independent statements of
#: one order, so the order is asserted and not merely the membership.
#:
#: **`goldenset`, not `golden set`, since R24.6.** The contract spelled it with a
#: space and five of the six names were identifier-safe while exactly one was not,
#: so a template deriving a CSS class, an anchor id or a dict key from `row.name`
#: broke on one row in six -- rare enough to ship and systematic enough to be wrong
#: every time. These strings are keys; the display label is the template's job.
_TRACKED = ("model_id", "n_per_item", "items", "judges", "goldenset", "config")

#: Two hashes sharing their first sixteen characters and differing at the
#: seventeenth. **This pair is the whole point of one test below.** On C4 the
#: mutant that compares truncated hashes survived the entire suite, because every
#: fixture hash in the file differs from character 0 and a 16-character comparison
#: is indistinguishable from a full one on such a pair. A strip that truncates
#: before comparing reports "judges unchanged" on these two.
_TWIN_PREFIX = "0123456789abcdef"
_TWIN_A = _TWIN_PREFIX + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TWIN_B = _TWIN_PREFIX + "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

#: Each tracked hash row, with the `RunPoint` field it reads and the sixteen
#: characters the unmodified fixture displays. The truncations are transcribed from
#: `_GROUP_GOLDENSET`, `_GROUP_JUDGES` and `_comparison`'s `config_hash` by hand;
#: `test_a_hash_is_truncated_to_sixteen_characters_for_display` checks the
#: transcription against the full strings before it checks anything of the code's.
_HASH_ROWS = (
    ("judges_hash", "judges", "bb624f0ed1781d85"),
    ("goldenset_hash", "goldenset", "5fef50364057cad8"),
    ("config_hash", "config", "1ad89c46dcbd426d"),
)


def _night(day: int, candidate: str, **changes: typing.Any) -> RunPoint:
    """One night of the lineage: the C1 fixture point, dated and re-badged."""
    return _point(created=_FOURTEEN_NIGHTS[day - 1], candidate_model=candidate, **changes)


def _fourteen_nights() -> list[RunPoint]:
    """R15's worked example: thirteen nights on `-b-v1`, the fourteenth on `-b-v2`."""
    nights = [_night(day, _LINEAGE_V1) for day in range(1, 14)]
    nights.append(_night(14, _LINEAGE_V2))
    return nights


def _rows(previous: RunPoint | None, current: RunPoint) -> dict[str, typing.Any]:
    """`parameter_strip`'s output as a mapping, for tests that name one row."""
    return {row.name: row for row in series.parameter_strip(previous, current)}


# ----------------------------------------------------------------------------------
# The parameter strip: one row per tracked parameter, always
# ----------------------------------------------------------------------------------


def test_the_parameter_strip_lists_every_tracked_parameter_including_the_ones_that_did_not_change():
    """The contract's named first-failing test, and the chunk's whole argument:
    "when one row moved and everything else held, the drop is attributable rather
    than merely observed". A strip that lists only what changed cannot make that
    claim, because absence of a row is indistinguishable from absence of a record --
    a reader who sees no judges row cannot tell whether the judges held or whether
    the tool never looked.

    All six names are asserted in order and every one of the six `changed` flags is
    asserted `False`, because a strip that returned the two rows it felt were
    interesting would pass any assertion phrased as a subset."""
    rows = series.parameter_strip(
        _point(created=_FOURTEEN_NIGHTS[0]), _point(created=_FOURTEEN_NIGHTS[1])
    )
    assert [row.name for row in rows] == list(_TRACKED)
    assert [row.changed for row in rows] == [False, False, False, False, False, False]
    assert [row.before for row in rows] == [
        "claude-candidate-v2",
        "5",
        "12",
        "bb624f0ed1781d85",
        "5fef50364057cad8",
        "1ad89c46dcbd426d",
    ]
    assert [row.after for row in rows] == [row.before for row in rows]


def test_every_tracked_parameter_name_is_usable_as_an_identifier():
    """R24.6, and the shape of the defect is what makes it a ruling rather than a
    tidy: five of the six names were identifier-safe and exactly one was not, so a
    template deriving a CSS class, an anchor id or a dict key from `row.name` worked
    on five rows and broke on the sixth. Rare enough to ship, systematic enough to be
    wrong every time it renders.

    These strings are keys. The display label -- "golden set", with the space and the
    capital if the page wants one -- is the template's job, which is where labels
    belong, and `ParameterChange` stays at four fields rather than growing a second
    one for the same fact."""
    rows = series.parameter_strip(None, _point())
    assert [row.name for row in rows] == list(_TRACKED)
    for row in rows:
        assert row.name.isidentifier(), (
            f"{row.name!r} cannot be a CSS class, an anchor id or a dict key, and one "
            f"row in six that cannot is worse than none of them being able to"
        )


def test_a_parameter_change_is_a_frozen_record_of_a_name_two_values_and_a_verdict():
    """Four fields, no more: a fifth that a renderer could read instead of `changed`
    is a second opinion about whether the run moved, and the two would disagree on
    the row that matters. Frozen because a strip that can be edited after it is
    built is a strip that can be made to disagree with the chart above it."""
    row = _rows(_point(), _point())["model_id"]
    assert dataclasses.is_dataclass(row)
    assert [field.name for field in dataclasses.fields(row)] == [
        "name",
        "before",
        "after",
        "changed",
    ]
    assert isinstance(row.name, str)
    assert isinstance(row.before, str)
    assert isinstance(row.after, str)
    assert isinstance(row.changed, bool)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.changed = True  # type: ignore[misc]


def test_the_first_run_of_a_series_has_nothing_to_change_from_and_says_so_on_every_row():
    """`previous is None` -- the first night, which has no predecessor. Every row
    still renders, `changed=False`, and every `before` says *in words* that there was
    no previous run: the first run changed nothing because there was nothing to
    change from, and the cell has to say that rather than merely be empty.

    Six rows here too. The first night is precisely when a reader most wants to know
    what the parameters *were*, and a strip that returned nothing at all for it
    would leave the top of every series blank.

    **The empty `before` this test used to assert is gone, and the contract has been
    amended.** The contract said `""` twice and the blank is *true* -- there really
    was no previous run -- but true is not the bar here, legible is. A blank cell in
    a column of values reads as "same as the row above", which is the one reading
    this chunk exists to prevent. And the first run is not the only thing that
    renders `previous=None`: a wrongly-split series renders it too, so six blanks
    were both the honest top of a real line and the middle of a broken one, with
    nothing in the output to tell them apart. That indistinguishability is precisely
    what R15 was written to kill, and it was sitting at the top of every line.

    Three assertions carry the ruling, and the non-empty one is what makes it stick.
    The marker is read off the output rather than compared against a constant, so
    this pins the guarantee and not the wording: it must be *something*, it must be
    the same something on all six rows, and it must not be what an unrecorded value
    renders as -- "there was no previous run" and "this run recorded no value" are
    facts about different things, the comparison and the log, and a reader who is
    handed one word for both draws a conclusion about the run from a gap in the
    log.

    **R24.3 adds the fourth assertion, and inequality was never enough.**
    `_NO_PREVIOUS_RUN = "no previous run recorded"` survived all 1998 tests: it
    differs from `unrecorded`, it is non-empty, it satisfies every assertion above
    -- and it prints the two absences as the same idea, which is exactly what the
    ruling forbade. The ruling's words are "must not print the same word", so the
    word is what is asserted: *recorded* is the vocabulary of "the value was not
    written down", and the marker for "the comparison could not be made" must stay
    out of it."""
    rows = series.parameter_strip(None, _point())
    marker = rows[0].before
    unrecorded = _rows(_point(judges_hash=""), _point(judges_hash=""))["judges"].before
    assert [row.name for row in rows] == list(_TRACKED)
    assert [row.before for row in rows] == [marker] * len(_TRACKED)
    assert [row.changed for row in rows] == [False, False, False, False, False, False]
    assert marker != ""
    assert marker.strip() != ""
    assert marker != unrecorded
    assert "recorded" not in marker.lower(), (
        f"the first-run marker is speaking the unrecorded vocabulary, so both "
        f"absences print as one idea: {marker!r} beside {unrecorded!r}"
    )
    assert [row.after for row in rows] == [
        "claude-candidate-v2",
        "5",
        "12",
        "bb624f0ed1781d85",
        "5fef50364057cad8",
        "1ad89c46dcbd426d",
    ]


def test_when_only_the_model_moved_exactly_one_row_says_so_and_the_other_five_hold():
    """The attributable-drop claim in one assertion. The claim the strip licenses is
    "this and nothing else moved", and it is licensed by the five `False`s quite as
    much as by the one `True`."""
    rows = series.parameter_strip(
        _point(candidate_model=_LINEAGE_V1), _point(candidate_model=_LINEAGE_V2)
    )
    assert [row.name for row in rows if row.changed] == ["model_id"]
    moved = _rows(_point(candidate_model=_LINEAGE_V1), _point(candidate_model=_LINEAGE_V2))[
        "model_id"
    ]
    assert moved.before == _LINEAGE_V1
    assert moved.after == _LINEAGE_V2
    assert moved.changed is True


@pytest.mark.parametrize(("field", "name", "shown"), _HASH_ROWS, ids=[row[1] for row in _HASH_ROWS])
def test_a_hash_is_truncated_to_sixteen_characters_for_display(field, name, shown):
    """`_require_comparable`'s own convention, and the report's, so that one page
    cannot print two different-looking prefixes of one hash and send a reader
    looking for a change that did not happen."""
    full = getattr(_point(), field)
    assert len(shown) == 16 and full.startswith(shown), "the transcribed prefix is wrong"
    assert len(full) > 16, "the fixture hash must be longer than the truncation"
    row = _rows(_point(), _point())[name]
    assert row.before == shown
    assert row.after == shown


@pytest.mark.parametrize(("field", "name", "shown"), _HASH_ROWS, ids=[row[1] for row in _HASH_ROWS])
def test_two_hashes_that_differ_only_after_the_sixteenth_character_are_still_a_change(
    field, name, shown
):
    """Truncate for display, compare in full. **On C4 exactly this mutant survived
    the whole suite** -- every fixture hash in this file differed from character 0,
    so a comparison of two 16-character prefixes was indistinguishable from a
    comparison of the whole strings, and nothing went red.

    A strip that compares the truncation reports "judges unchanged" on a night the
    panel was replaced, which is the contract's stated failure mode almost word for
    word: "says 'judges unchanged' on a run where the judge model changed and only
    the panel hash happened to collide". The displayed values are asserted equal
    *and* `changed` asserted `True` in the same test, because that combination --
    two identical-looking cells and a verdict of "changed" -- is the whole
    behaviour, and a test that checked only the flag would pass on an
    implementation that also stopped truncating."""
    row = _rows(_point(**{field: _TWIN_A}), _point(**{field: _TWIN_B}))[name]
    assert row.changed is True
    assert row.before == _TWIN_PREFIX
    assert row.after == _TWIN_PREFIX
    assert _TWIN_A[:16] == _TWIN_B[:16] and _TWIN_A != _TWIN_B, "the fixture twins are wrong"


@pytest.mark.parametrize(
    ("field", "name", "recorded"),
    [
        ("candidate_model", "model_id", "claude-candidate-v2"),
        ("judges_hash", "judges", "bb624f0ed1781d85"),
        ("goldenset_hash", "goldenset", "5fef50364057cad8"),
        ("config_hash", "config", "1ad89c46dcbd426d"),
    ],
    ids=["model_id", "judges", "goldenset", "config"],
)
def test_a_value_that_one_of_the_two_runs_never_recorded_never_renders_as_unchanged(
    field, name, recorded
):
    """The highest-consequence, lowest-visibility bug in the plan, per the
    reviewer's note: "the strip's entire job is to license an attribution, and a
    blank that reads as 'held' licenses a false one".

    Two claims are separated here and they are separated on purpose. The *verdict*
    is `changed=False` -- a field one side never wrote down is no evidence that it
    moved, and the contract's edge table says so. The *rendering* must nonetheless
    not be a blank cell beside a filled one, because a reader reads that pair as
    "same as above". So the assertion is that the unrecorded side is non-empty,
    differs from the recorded side, and says in a word that nothing was recorded --
    `series._UNRECORDED` and `report.THRESHOLD_SOURCE_UNRECORDED` both contain
    "recorded", and neither a hash nor a model id does.

    Both directions, because they are different nights: recorded then silent is a
    pipeline that stopped writing the field, silent then recorded is one that
    started, and an implementation that special-cased `current` would pass on
    half."""
    forgotten = _rows(_point(), _point(**{field: ""}))[name]
    assert forgotten.changed is False
    assert forgotten.before == recorded
    assert forgotten.after != ""
    assert forgotten.after != forgotten.before
    assert "recorded" in forgotten.after.lower()

    remembered = _rows(_point(**{field: ""}), _point())[name]
    assert remembered.changed is False
    assert remembered.after == recorded
    assert remembered.before != ""
    assert remembered.before != remembered.after
    assert "recorded" in remembered.before.lower()


def test_a_depth_or_an_item_count_nobody_recorded_never_renders_as_unchanged_either():
    """The section banner's third ruling, and the only assertion here the contract
    does not spell out: `n_per_item` and `items` record their absence as `0`, not as
    `""`. `RunPoint.items` documents "`0` when unrecorded" in as many words and
    `series._depth` already prints `0` as the word, so a strip that renders "5" in
    one cell and "0" in the next has published a 100% collapse in sampling depth
    that may never have happened -- and the row beneath it is then read as an
    explanation for the drop.

    `changed` is `False` for the same reason as the string fields: a number nobody
    wrote down is not evidence that the number moved."""
    depth = _rows(_point(), _point(n_per_item=0))["n_per_item"]
    assert depth.changed is False
    assert depth.before == "5"
    assert "recorded" in depth.after.lower()

    covered = _rows(_point(), _point(items=0))["items"]
    assert covered.changed is False
    assert covered.before == "12"
    assert "recorded" in covered.after.lower()


@pytest.mark.parametrize(
    ("field", "name", "recorded"),
    [
        ("candidate_model", "model_id", "claude-candidate-v2"),
        ("judges_hash", "judges", "bb624f0ed1781d85"),
        ("goldenset_hash", "goldenset", "5fef50364057cad8"),
        ("config_hash", "config", "1ad89c46dcbd426d"),
    ],
    ids=["model_id", "judges", "goldenset", "config"],
)
def test_a_value_that_is_only_whitespace_is_an_absence_and_never_a_blank_cell(
    field, name, recorded
):
    """**Mutant C6: `_text_cell` testing truthiness instead of `_recorded`.** It
    survived the whole suite for the plainest of R24.7's reasons -- *there is no
    whitespace-only value anywhere in this file*. Every fixture spells an absence
    `""`, on which `bool(value)` and `value.strip()` agree exactly.

    A writer that padded the field recorded nothing, and `_recorded` says so with a
    `.strip()` whose comment calls the case "essentially unreachable and one call
    wide". Unreachable is not the same as harmless: the cell renders **blank** under
    the mutant, and a blank cell beside a filled one reads as "held", which is the
    single failure this chunk was written to prevent. The reviewer's note is that
    the strip's whole job is to license an attribution, and a blank that reads as
    "held" licenses a false one.

    Both directions, as with `""`: a pipeline that started padding the field and one
    that stopped are different nights."""
    forgotten = _rows(_point(), _point(**{field: "   "}))[name]
    assert forgotten.changed is False
    assert forgotten.before == recorded
    assert forgotten.after.strip() != "", (
        "a padded field rendered as a blank cell, which a reader takes for 'held'"
    )
    assert "recorded" in forgotten.after.lower()

    remembered = _rows(_point(**{field: "   "}), _point())[name]
    assert remembered.changed is False
    assert remembered.after == recorded
    assert remembered.before.strip() != ""
    assert "recorded" in remembered.before.lower()


def test_a_padded_hash_against_a_real_one_is_not_reported_as_a_change():
    """R24.5's probe, and the reason the `_Cell` docstring had to be rewritten rather
    than merely annotated. The sentence said `value` is `""` exactly when the run
    recorded nothing, "so one emptiness test decides for hashes, ids and counts
    alike" -- false for a padded field, and an invitation to swap `_recorded(...)`
    for `== ""` in `_parameter_change`.

    Take the invitation and this row reads `changed=True`, "judges changed", **from
    a padding artifact**: the strip names the judge panel as the thing that moved on
    a night when nothing moved at all, and the attribution beneath it is drawn from
    whitespace. `changed` is the one field a renderer acts on, so it is asserted
    directly rather than through the rendering."""
    padded = _rows(_point(judges_hash=_TWIN_A), _point(judges_hash="   "))["judges"]
    assert padded.changed is False, (
        "a padded field was compared as a value, so a run that changed nothing is "
        "reported as having changed its judges"
    )
    both_padded = _rows(_point(judges_hash="  "), _point(judges_hash="\t"))["judges"]
    assert both_padded.changed is False


def test_a_run_that_sampled_to_a_different_depth_moves_the_n_per_item_row_and_only_that_row():
    """A recorded difference is a real change and reads as one, which is what makes
    the unrecorded rule above a distinction rather than a blanket refusal to
    report."""
    rows = series.parameter_strip(_point(), _point(n_per_item=3, items=9))
    assert [row.name for row in rows if row.changed] == ["n_per_item", "items"]
    moved = {row.name: row for row in rows}
    assert (moved["n_per_item"].before, moved["n_per_item"].after) == ("5", "3")
    assert (moved["items"].before, moved["items"].after) == ("12", "9")


# ----------------------------------------------------------------------------------
# The trend: one declared lineage, one line, and the change still visible
# ----------------------------------------------------------------------------------


def test_fourteen_nights_across_two_declared_model_ids_are_one_line_with_one_visible_change():
    """**The test that carries this chunk.** R14.6 framed the two properties as a
    choice -- either candidate B's fourteen nights are one line and the v1 -> v2
    change is hidden inside it, or the change is visible and the line splits 13+1 --
    and R15 ruled that the tension was manufactured by the filter. `trend` used to
    filter by `candidate_model`, the very field that moves, so night 14 was not in
    the same series as night 13, so `parameter_strip` saw `previous is None` and the
    `model_id` row said `changed=False` against an empty `before`.

    All three halves of the ruling are asserted here because each is silent without
    the others: fourteen points on one line, exactly one `Succession` at the index
    of the first run under the new id, and -- fed back through the strip that needed
    no change at all -- exactly one `changed=True` row across the whole fortnight,
    which is `model_id`."""
    line = series.trend(_fourteen_nights(), baseline_model=_BASELINE, candidate_models=_LINEAGE)

    assert len(line.points) == 14
    assert [point.created for point in line.points] == list(_FOURTEEN_NIGHTS)
    assert [point.candidate_model for point in line.points] == [_LINEAGE_V1] * 13 + [_LINEAGE_V2]
    assert line.excluded == ()
    assert line.undated == 0
    assert line.caveats == ()

    assert len(line.successions) == 1
    succession = line.successions[0]
    assert succession.index == 13
    assert succession.before == _LINEAGE_V1
    assert succession.after == _LINEAGE_V2
    assert succession.created == "2026-08-14T22:40:58+00:00"
    assert line.points[succession.index].candidate_model == _LINEAGE_V2

    moved = [
        row
        for previous, current in zip(line.points, line.points[1:], strict=False)
        for row in series.parameter_strip(previous, current)
        if row.changed
    ]
    assert [row.name for row in moved] == ["model_id"]
    assert moved[0].before == _LINEAGE_V1
    assert moved[0].after == _LINEAGE_V2


def test_a_lineage_is_never_inferred_by_stripping_a_version_suffix():
    """R15.1, and the forbidden implementation is named in it: "the tool must not
    infer that `-b-v2` succeeds `-b-v1`. Stripping a trailing version suffix is the
    obvious implementation and it is forbidden."

    Whether two model ids name the same lineage is a fact about the world that no
    log records, and a wrong guess silently joins two unrelated models into one
    line -- which is the "two unrelated numbers side by side" failure
    `_require_comparable` exists to prevent, arrived at from a new direction. The
    operator knows the lineage; the operator says so. Given only `-b-v1`, the
    fourteenth night is somebody else's series.

    The fixture is the one that tempts the guess: the two ids differ only in the
    suffix, so an implementation that normalised them would pass every other test in
    this section."""
    line = series.trend(
        _fourteen_nights(), baseline_model=_BASELINE, candidate_models=(_LINEAGE_V1,)
    )
    assert len(line.points) == 13
    assert [point.candidate_model for point in line.points] == [_LINEAGE_V1] * 13
    assert _LINEAGE_V2 not in {point.candidate_model for point in line.points}
    assert "2026-08-14T22:40:58+00:00" not in [point.created for point in line.points]
    assert line.successions == ()


def test_a_single_element_lineage_reproduces_the_old_single_candidate_behaviour():
    """R15.1's closing claim: "a single-element sequence reproduces today's
    behaviour exactly, so this is a strict generalisation and not a behaviour change
    for any existing caller." One candidate declared, one candidate drawn, sorted
    ascending, and no succession to draw a rule at.

    The unrelated candidate is interleaved rather than appended, so an
    implementation that took a prefix of the input would be visible."""
    nights = [
        _night(3, "gpt-candidate-v9"),
        _night(1, _LINEAGE_V1),
        _night(2, "gpt-candidate-v9"),
        _night(4, _LINEAGE_V1),
    ]
    line = series.trend(nights, baseline_model=_BASELINE, candidate_models=(_LINEAGE_V1,))
    assert [point.created for point in line.points] == [
        "2026-08-01T22:40:58+00:00",
        "2026-08-04T22:40:58+00:00",
    ]
    assert line.successions == ()
    assert line.undated == 0


def test_a_run_of_a_candidate_outside_the_declared_lineage_is_not_on_the_line():
    """The filter that survives R15: the operator declared which ids are this line,
    and a candidate they did not name is a different experiment. Asserted as absence
    from `points` only -- see the section banner's first ruling for why nothing is
    claimed about where it goes instead."""
    stranger = _night(2, "some-other-vendor-v1")
    line = series.trend(
        [_night(1, _LINEAGE_V1), stranger, _night(3, _LINEAGE_V2)],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert stranger not in line.points
    assert [point.candidate_model for point in line.points] == [_LINEAGE_V1, _LINEAGE_V2]


def test_a_run_measured_from_a_different_baseline_is_not_on_the_line():
    """A column of deltas measured from two different baselines is not a column.
    `baseline_model` stayed a scalar keyword through R15 precisely because it is not
    the field that moves."""
    rebased = _night(2, _LINEAGE_V1, baseline_model="gpt-baseline-v0")
    line = series.trend(
        [_night(1, _LINEAGE_V1), rebased, _night(3, _LINEAGE_V2)],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert rebased not in line.points
    assert [point.created for point in line.points] == [
        "2026-08-01T22:40:58+00:00",
        "2026-08-03T22:40:58+00:00",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"goldenset_hash": _OTHER_GOLDENSET},
        {"judges_hash": _OTHER_JUDGES},
        {"n_per_item": 3},
    ],
    ids=["goldenset_hash", "judges_hash", "n_per_item"],
)
def test_a_lineage_member_that_is_not_comparable_is_excluded_rather_than_drawn(change):
    """R15.2: "putting two model ids on one line is a claim that the runs are
    comparable. That claim is C4's to adjudicate", so `trend` partitions through
    `partition_comparable` and carries the exclusions out with it. "A lineage whose
    members disagree on golden set, judges or `n_per_item` is not one line and must
    not be drawn as one."

    The odd night is neither first in the input nor first by date, so an
    implementation that anchored the group key on it would draw the odd run and
    exclude the three good ones -- and this assertion would go red rather than
    silently pass on a line built from the wrong anchor.

    The exclusion is asserted to carry a reason, because a point that vanishes with
    no sentence is the table C4 exists to prevent."""
    odd = _night(2, _LINEAGE_V1, **change)
    line = series.trend(
        [_night(1, _LINEAGE_V1), odd, _night(3, _LINEAGE_V1), _night(4, _LINEAGE_V2)],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert odd not in line.points
    assert [point.created for point in line.points] == [
        "2026-08-01T22:40:58+00:00",
        "2026-08-03T22:40:58+00:00",
        "2026-08-04T22:40:58+00:00",
    ]
    assert [exclusion.point.created for exclusion in line.excluded] == ["2026-08-02T22:40:58+00:00"]
    assert isinstance(line.excluded[0], series.Exclusion)
    assert line.excluded[0].reason.strip(), (
        "an excluded point with no sentence is a run that vanished"
    )


def test_a_night_the_partition_kept_with_a_note_is_drawn_and_carries_the_note_out():
    """The fifth field, and the reason it exists: a caveat annotates a row, it does
    not remove one. A night whose baseline was graded 60 times and whose candidate
    was graded 57 has the right hashes and the right depth, so it stays on the line
    -- the payload cannot tell a truncated run from one that lost a few judge
    replies, and excluding on that suspicion would silently shrink the field. What
    must not happen is the third thing: drawing it silently, as an ordinary point
    whose shortfall flatters whichever side finished.

    The point is asserted to be in `points` *and* in `caveats`, because those are
    not alternatives and an implementation that treated the caveat as a removal
    would drop the run twice over."""
    lopsided = _night(2, _LINEAGE_V1, judged_baseline=60, judged_candidate=57)
    line = series.trend(
        [_night(1, _LINEAGE_V1), lopsided, _night(3, _LINEAGE_V2)],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert [point.created for point in line.points] == [
        "2026-08-01T22:40:58+00:00",
        "2026-08-02T22:40:58+00:00",
        "2026-08-03T22:40:58+00:00",
    ]
    assert line.excluded == ()
    assert [caveat.point.created for caveat in line.caveats] == ["2026-08-02T22:40:58+00:00"]
    assert isinstance(line.caveats[0], series.Caveat)
    assert line.caveats[0].reason.strip(), "a note with no sentence tells a reader nothing"


def test_the_a_a_calibration_run_is_drawn_and_named_rather_than_drawn_silently():
    """A model compared against itself always looks safe, which is why the pipeline
    refuses one unless it is passed `allow_same_model=True`. It is passed it: the
    A/A run is deliberate, and it is the one row on the page that shows what "no
    difference" measures like on this panel. So it is drawn -- and it is drawn with
    the note that says which row it is, because a flat delta with no label is a
    result a reader will quote."""
    calibration = _night(1, _BASELINE)
    line = series.trend(
        [calibration, _night(2, _LINEAGE_V1)],
        baseline_model=_BASELINE,
        candidate_models=(_BASELINE, _LINEAGE_V1),
    )
    assert calibration in line.points
    assert [caveat.point.created for caveat in line.caveats] == ["2026-08-01T22:40:58+00:00"]


def test_points_whose_created_will_not_parse_are_counted_in_undated_and_left_off_the_line():
    """R15.3, and the defect it fixes: C7's own contract said undated points "are
    excluded from the return and the caller learns of them separately", and then
    returned a bare `tuple[RunPoint, ...]` through which the caller can learn
    nothing. `undated` is the field they learn through.

    Two of them, spelled the two ways a payload goes undated -- a `created` nobody
    wrote and a `created` no parser will take. A count of 1 would pass on an
    implementation that noticed only one kind."""
    line = series.trend(
        [
            _night(1, _LINEAGE_V1),
            _point(created="", candidate_model=_LINEAGE_V1),
            _point(created="the twenty-first", candidate_model=_LINEAGE_V1),
            _night(2, _LINEAGE_V2),
        ],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert line.undated == 2
    assert [point.created for point in line.points] == [
        "2026-08-01T22:40:58+00:00",
        "2026-08-02T22:40:58+00:00",
    ]
    assert "" not in [point.created for point in line.points]


def test_the_line_is_sorted_by_the_parsed_instant_and_not_by_the_recorded_string():
    """Ascending by *parsed* `created`. The three timestamps below sort one way as
    text and the other way as instants: `+05:00` at 02:00 on the 10th is 21:00 UTC
    on the 9th, which is earlier than 23:00Z on the 9th, which is earlier than 09:00
    UTC on the 10th. A `sorted(points, key=lambda p: p.created)` -- which is the
    cheap implementation, and reads correctly on every log this project has
    written -- puts them in exactly the wrong order and draws a line that runs
    backwards through the evening."""
    line = series.trend(
        [
            _point(created="2026-08-10T09:00:00+00:00", candidate_model=_LINEAGE_V1),
            _point(created="2026-08-09T23:00:00Z", candidate_model=_LINEAGE_V1),
            _point(created="2026-08-10T02:00:00+05:00", candidate_model=_LINEAGE_V1),
        ],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert [point.created for point in line.points] == [
        "2026-08-10T02:00:00+05:00",
        "2026-08-09T23:00:00Z",
        "2026-08-10T09:00:00+00:00",
    ]


def test_two_points_recorded_at_the_identical_instant_keep_the_order_they_arrived_in():
    """A stable sort, asserted the only way it can be: the same pair fed in both
    orders and required to come out in the order it went in. One direction alone
    passes on a sort that breaks ties on any field at all, and the field it happened
    to choose would decide which of two runs a reader believes came first."""
    same = "2026-08-05T12:00:00+00:00"
    forwards = series.trend(
        [
            _point(created=same, candidate_model=_LINEAGE_V1, reason="first"),
            _point(created=same, candidate_model=_LINEAGE_V1, reason="second"),
        ],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert [point.reason for point in forwards.points] == ["first", "second"]

    backwards = series.trend(
        [
            _point(created=same, candidate_model=_LINEAGE_V1, reason="second"),
            _point(created=same, candidate_model=_LINEAGE_V1, reason="first"),
        ],
        baseline_model=_BASELINE,
        candidate_models=_LINEAGE,
    )
    assert [point.reason for point in backwards.points] == ["second", "first"]


def test_an_empty_log_is_an_empty_trend_and_never_an_error():
    """A pipeline whose first night has not run yet, which is the commonest input
    this code will ever see. Not an exception, not a `None` the caller has to test
    for before unpacking."""
    empty = series.trend([], baseline_model=_BASELINE, candidate_models=_LINEAGE)
    assert empty == ((), (), (), 0, ())
    assert isinstance(empty, tuple)
    assert len(empty) == 5
    points, successions, excluded, undated, caveats = empty
    assert (points, successions, excluded, undated, caveats) == ((), (), (), 0, ())
    assert empty.points == ()
    assert empty.successions == ()
    assert empty.excluded == ()
    assert empty.undated == 0
    assert empty.caveats == ()


def test_the_lineage_and_the_baseline_must_both_be_passed_by_keyword():
    """`trend(points, baseline, lineage)` positionally would read identically at
    every call site whichever order the two were declared in, and R15 changed one of
    them from a string to a sequence -- a caller that had not been updated would
    then pass a model id where a lineage belongs and get a line built from its
    individual characters."""
    with pytest.raises(TypeError):
        series.trend([_point()], _BASELINE, _LINEAGE)  # type: ignore[misc]


def test_the_trend_and_the_succession_are_named_tuples_with_the_fields_r15_names():
    """A bare tuple has nowhere to put the answer -- R15.3's heading, and the third
    instance in this plan of one defect class: a contract that tells the caller
    about an *absence* through a return type with room only for presences. C4's flag
    with no field to live in was the first, C13's counts that had to become a
    `Timeline` the second, and `undated` is the third. `caveats` is the fourth, and
    it was committed inside the very type written to fix the third: the partition
    computes caveats and the four-field `Trend` had nowhere to put them, so an A/A
    calibration run was drawn as an ordinary row whose flat delta a reader takes for
    a result. It is fifth and last so that unpacking the first four still reads.

    Resolved at runtime rather than read off the source, because `from __future__
    import annotations` makes a misspelled type invisible until something calls
    `get_type_hints` -- and C10 and C14 will type against these."""
    hints = typing.get_type_hints(series.trend)
    assert hints["return"] is series.Trend

    fields = typing.get_type_hints(series.Trend)
    assert list(fields) == ["points", "successions", "excluded", "undated", "caveats"]
    assert fields["points"] == tuple[RunPoint, ...]
    assert fields["successions"] == tuple[series.Succession, ...]
    assert fields["excluded"] == tuple[series.Exclusion, ...]
    assert fields["undated"] is int
    assert fields["caveats"] == tuple[series.Caveat, ...]

    succession = typing.get_type_hints(series.Succession)
    assert list(succession) == ["index", "before", "after", "created"]
    assert succession["index"] is int
    assert succession["before"] is str
    assert succession["after"] is str
    assert succession["created"] is str


def test_the_new_names_are_exported_so_the_rendering_chunks_can_reach_them():
    """C10 and C14 render these. A name that works under `series.Trend` and is
    missing from `__all__` is a name a star-import consumer cannot see, and the
    module has been strict about this since C1.

    **The two markers are here since R24.6, and they are the point of the ruling.**
    A template styling a first-run cell differently from an unrecorded one has to
    name both; while they were private its only options were reaching into a private
    name or hard-coding the literal, which `UNRECORDED`'s own comment forbids and
    R7 ruled on generally -- import the constant, never hard-code its value. They
    are constants rather than callables, so `check_merge.py`'s `__all__` check does
    not cover them by design and this does."""
    for name in (
        "NO_PREVIOUS_RUN",
        "ParameterChange",
        "Succession",
        "Trend",
        "UNRECORDED",
        "parameter_strip",
        "trend",
    ):
        assert name in series.__all__, f"{name} is not exported"
    assert not hasattr(series, "_NO_PREVIOUS_RUN"), "the private spelling outlived the rename"
    assert not hasattr(series, "_UNRECORDED"), "the private spelling outlived the rename"

# ==================================================================================
# Chunk C5 -- the candidate field
# ==================================================================================
#
# Written from the same plan, chunk C5 *as amended by R17.2 through R17.5*, and
# from nothing else. `Candidate`, `CandidateField` and `candidate_field` did not
# exist in this worktree when these were written; no expected value below was
# obtained by running any of them.
#
# **Every new name is reached as `series.something`, for the reason C4's section
# gives above and does not need repeating: a module-level import of a function
# that has not been written yet fails at *collection* and takes the other ~197
# tests down with it, which is a red suite that says nothing about which chunk is
# unfinished.
#
# **Four amendments this section is written against, none of them optional.**
#
# * `RunPoint` has no baseline pass rate -- its `pass_rate` is the *candidate*
#   side, and the only baseline-side numbers it carries are `judged_baseline` and
#   `judge_failures_baseline`. The rate is reconstructed as
#   `(judged_baseline - judge_failures_baseline) / judged_baseline`, `None` when
#   the denominator is zero. So the fixture below carries **three distinguishable
#   rates**: 0.80 on the baseline side, 0.66 if the candidate-side counts are read
#   by mistake, and whatever `pass_rate` was set to on the row. A fixture where
#   any two of those coincide passes for an implementation reading the wrong side.
# * `CandidateField` carries `caveats` beside `excluded`. C4 renamed `Flag` to
#   `Caveat`; R17.3 was written before that rename and its `flags:
#   tuple[Flag, ...]` is stale in the name only, not in the requirement. A caveat
#   dropped at this layer is a caveat that reaches nobody, which is the same as
#   not having computed it.
# * "Grouping by `comparability_key` ignoring `candidate_model`" is stale: the key
#   never held `candidate_model`. The test that matters is the other direction --
#   two nights with *different* candidates must share one key, because a key that
#   did hold it makes every group a group of one and the table never renders.
# * The tie-break is **total**: largest group, then newest point, then the key in
#   sorted order. The third tier is tested by building one input twice in
#   different orders and asserting the same winner, which pins determinism without
#   pinning one implementation's arbitrary choice of winner.
#
# **On expected numbers being literals.** `baseline_pass_rate` is asserted against
# `0.8` and `delta_pp` against `-15.0`, spelled out, not recomputed from the
# fixture's counts. A test that re-derives the value with the expression it is
# testing passes for any formula the implementation and the test agree on,
# including a wrong one.
#
# **Two readings this section had to settle, recorded because a reader comparing
# it against the plan will otherwise read them as drift.**
#
# * `baseline_pass_rate` is one number and every kept point carries its own
#   baseline-side counts. Which point it comes from is not settled by the
#   contract, so every fixture here that asserts on it gives *all* of its points
#   the same baseline-side counts, and the assertion holds for any of the
#   readings. The ambiguity is real and is left for the reviewer rather than
#   decided by a test.
# * The edge table says a candidate with no date sorts *last*, and the "must not"
#   says order is by `candidate_model`. Both are asserted, on the reading that the
#   dateless row is the exception and model order is the rule -- so the fixture
#   for it gives the dateless run the alphabetically *first* model name, where the
#   two readings disagree.

#: The dates these tests are spaced along, at midnight so that a difference in
#: days is an exact float and an assertion can be spelled `== 7.0` rather than
#: approximated. Aug 13 to Aug 20 is exactly the default window, which is the
#: boundary `spread_days > stale_after_days` is strict about.
_AUG_10 = "2026-08-10T00:00:00+00:00"
_AUG_12 = "2026-08-12T00:00:00+00:00"
_AUG_13 = "2026-08-13T00:00:00+00:00"
_AUG_17 = "2026-08-17T00:00:00+00:00"
_AUG_20 = "2026-08-20T00:00:00+00:00"

#: The baseline side of every point below: 50 graded, 10 failed, so the
#: reconstructed baseline pass rate is exactly 0.80. The candidate side grades the
#: same 50 -- equal, so no coverage caveat is raised by accident -- but fails 17 of
#: them, so a reconstruction that reads the candidate-side counts lands on 0.66 and
#: is visible. Neither number is any row's `pass_rate`.
_JUDGED_BASELINE = 50
_FAILURES_BASELINE = 10
_JUDGED_CANDIDATE = 50
_FAILURES_CANDIDATE = 17


def _run(model: str, *, created: str = _AUG_20, pass_rate: float | None = 0.65, **changes):
    """One night's comparison of `model`, on the group key every fixture here shares.

    Built on C1's `_point`, so every field C5 does not name holds the value a real
    `migkit.comparison` payload puts there.
    """
    fields: dict[str, typing.Any] = {
        "candidate_model": model,
        "created": created,
        "pass_rate": pass_rate,
        "judged_baseline": _JUDGED_BASELINE,
        "judge_failures_baseline": _FAILURES_BASELINE,
        "judged_candidate": _JUDGED_CANDIDATE,
        "judge_failures_candidate": _FAILURES_CANDIDATE,
    }
    fields.update(changes)
    return _point(**fields)


def _other_group(model: str, **changes):
    """A run against the other golden set: a second group, identifying, and never
    comparable with the first."""
    return _run(model, goldenset_hash=_OTHER_GOLDENSET, **changes)


def _field_of(points, **options):
    """The field these points make, asserted to exist, because every caller below
    was built to produce one and a `None` here would otherwise surface as an
    `AttributeError` on `None` three lines later."""
    field = series.candidate_field(points, **options)
    assert field is not None, "this fixture was built to produce a field and produced none"
    return field


def _models(field) -> list[str]:
    """The candidate models of a field, in the order it put them in."""
    return [candidate.point.candidate_model for candidate in field.candidates]


# ----------------------------------------------------------------------------------
# When there is no field at all
# ----------------------------------------------------------------------------------


def test_a_log_holding_one_comparison_yields_no_candidate_field_at_all():
    """The contract's named first-failing test. One candidate "collapses the table
    to a single row and it is not rendered as a table at all", and `None` is what
    makes that structural: a one-row `CandidateField` would put the decision in a
    template `{% if %}`, where the next chunk to render a field would get it right
    or wrong on its own and nothing here would notice.

    Spelled `is None`, not `not`: an empty-but-present field is falsey and is
    exactly the answer this rejects."""
    assert series.candidate_field([_run("claude-candidate-v2")]) is None


def test_a_log_with_no_comparisons_in_it_yields_no_field_rather_than_an_empty_one():
    """The first night of a pipeline that has not run yet. Not an error, not an
    exception, and not an empty field whose caller then has to distinguish "nothing
    ran" from "one thing ran"."""
    assert series.candidate_field([]) is None


def test_a_log_that_tried_one_candidate_on_two_nights_still_yields_no_field():
    """Two points, one distinct candidate model. The rule is "fewer than two
    distinct candidate models", not "fewer than two points" -- a nightly job that
    re-ran the same pair all week is the commonest log this will ever see, and
    rendering it as a two-row comparison table would invite a reader to compare a
    model against itself on two different nights."""
    points = [
        _run("claude-candidate-v2", created=_AUG_10),
        _run("claude-candidate-v2", created=_AUG_20),
    ]
    assert series.candidate_field(points) is None


def test_a_log_whose_every_run_graded_nothing_yields_no_field_rather_than_a_column_of_deltas():
    """A baseline that graded nothing has an *unknown* pass rate, and R17.2's whole
    argument is that a `delta_pp` of -100.0 against it "is the same lie in the same
    direction" as plotting 0.0 on a chart for a run that measured nothing.

    The route by which this is `None` is the partition's, not a special case: a run
    with nothing graded on a side is excluded by `_ungraded`, so a group of three
    such runs keeps none of them and has no candidates to render."""
    points = [
        _run(model, judged_baseline=0, judge_failures_baseline=0)
        for model in ("alpha-candidate", "beta-candidate", "gamma-candidate")
    ]
    assert series.candidate_field(points) is None


# ----------------------------------------------------------------------------------
# The shape of the two new types
# ----------------------------------------------------------------------------------


def test_the_candidate_field_carries_the_eight_fields_the_amended_contract_names():
    """Asserted exactly -- `==`, not `<=` -- because the two fields that are
    easiest to leave out are the two nothing else in the type forces anyone to
    populate. `caveats` came from R17.3: a `CandidateField` without it compiles,
    renders, and silently drops every note the partition raised.
    `stale_after_days` came from R20.3, which is why this test says eight and the
    plan's contract block says seven.

    The two are named individually rather than only counted. A test that asserts a
    number goes green the moment a field of any name is added, which is how a
    rename slips through a count; naming them says which fields, and the failure
    message says which one is missing."""
    field = _field_of([_run("alpha-candidate"), _run("beta-candidate")])
    assert dataclasses.is_dataclass(field)
    present = {member.name for member in dataclasses.fields(field)}
    wanted = {
        "key",
        "candidates",
        "excluded",
        "caveats",
        "spread_days",
        "spread_flagged",
        "baseline_pass_rate",
        "stale_after_days",
    }
    assert "caveats" in present, "R17.3's companion to `excluded` is missing"
    assert "stale_after_days" in present, (
        "R20.3: the field does not record the window it was built with, so a renderer "
        "holding only the field can print `more than 7 days apart` about a field built "
        "with `stale_after_days=30.0`"
    )
    assert present == wanted, (
        f"missing: {sorted(wanted - present)}; extra: {sorted(present - wanted)}"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        field.spread_flagged = True  # type: ignore[misc]


def test_a_candidate_carries_the_point_the_delta_and_the_staleness_and_no_statistic():
    """`delta_pp` "is subtraction of two recorded rates, not a statistic -- no
    interval is attached to it and none may be invented". Asserted over `dir` and
    not only over `dataclasses.fields`, because a property named `delta_interval`
    or `delta_confidence` is not a dataclass field and would pass the narrower
    check while being exactly the invention the contract forbids.

    **The `dir` half is a blacklist and used to be a whitelist**, and the change
    is a correction rather than a loosening. `public == {"point", "delta_pp",
    "stale_days"}` forbade *any* addition to the class's public surface, which is
    a rule the contract never states and which the Must-not does not imply: what
    it forbids is a statistic on the delta, not a second way of spelling
    something the row already holds. Under the whitelist the guard blocked
    `model` -- `point.candidate_model` under the name the rows are keyed, ordered
    and joined on -- and would have gone on blocking it for as long as the guard
    stood, with the test's failure message saying only "extra".

    So the guard is narrowed to what it was for. A statistic arriving here has a
    small and predictable vocabulary -- interval, CI, confidence, significance,
    p-value, margin or standard error, an upper or lower bound -- and every one
    of those names is refused. The list is exercised against names it must catch
    before it is used, because a blacklist that matches nothing passes silently
    and guards nothing at all."""
    banned_tokens = {
        "interval", "intervals", "ci", "cis", "confidence", "conf", "credible",
        "significance", "significant", "sig", "p", "pvalue", "pvalues", "pval",
        "margin", "moe", "stderr", "se", "sem", "err", "error", "errors",
        "bound", "bounds", "lower", "upper", "low", "high",
    }
    banned_phrases = (
        "interval", "confidence", "credible", "significan", "p_value", "pvalue",
        "margin_of_error", "std_err", "stderr",
    )

    def invented(name: str) -> bool:
        return bool(set(name.split("_")) & banned_tokens) or any(
            phrase in name for phrase in banned_phrases
        )

    must_catch = [
        "delta_interval", "delta_ci", "ci", "confidence_interval", "delta_confidence",
        "p_value", "pvalue", "significance", "margin_of_error", "standard_error",
        "delta_lower", "upper_bound", "credible_interval", "stderr",
    ]
    assert [name for name in must_catch if not invented(name)] == [], (
        "the guard's own vocabulary misses a name it exists to refuse"
    )
    assert [name for name in ("point", "delta_pp", "stale_days", "model") if invented(name)] == []

    field = _field_of([_run("alpha-candidate"), _run("beta-candidate")])
    candidate = field.candidates[0]
    assert dataclasses.is_dataclass(candidate)
    assert [member.name for member in dataclasses.fields(candidate)] == [
        "point",
        "delta_pp",
        "stale_days",
    ]
    public = {name for name in dir(candidate) if not name.startswith("_")}
    assert {"point", "delta_pp", "stale_days"} <= public
    assert sorted(name for name in public if invented(name)) == [], (
        "an interval, a confidence or a p-value has been attached to a subtraction "
        "of two recorded rates -- the contract's second Must-not"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.delta_pp = 0.0  # type: ignore[misc]


def test_every_sequence_the_field_returns_is_a_tuple_because_the_table_has_to_be_stable():
    """C4's reason, one chunk on: a rendered list has to be the same list between
    two renders of one log, and set or dict-view iteration order is stable only by
    accident of hashing."""
    field = _field_of([_run("alpha-candidate"), _run("beta-candidate")])
    assert isinstance(field.candidates, tuple)
    assert isinstance(field.excluded, tuple)
    assert isinstance(field.caveats, tuple)


def test_the_three_new_names_are_exported_so_the_rendering_chunks_can_reach_them():
    """C6 and C7 render this. A type that is not in `__all__` is a type a later
    chunk re-derives from a tuple."""
    wanted = {"Candidate", "CandidateField", "candidate_field"}
    exported = set(series.__all__)
    assert wanted <= exported, f"missing from `__all__`: {sorted(wanted - exported)}"


def test_the_candidate_fields_annotations_resolve_to_the_named_types():
    """`from __future__ import annotations` makes a misspelled type invisible until
    something calls `get_type_hints`, and the return type is the load-bearing one:
    `CandidateField | None` is the whole of the single-candidate rule."""
    hints = typing.get_type_hints(series.candidate_field)
    assert hints["return"] == series.CandidateField | None
    members = typing.get_type_hints(series.CandidateField)
    assert members["key"] is series.ComparabilityKey
    assert members["candidates"] == tuple[series.Candidate, ...]
    assert members["excluded"] == tuple[series.Exclusion, ...]
    assert members["caveats"] == tuple[series.Caveat, ...]
    assert members["stale_after_days"] is float, (
        "R20.3's window is `float` and not `float | None`: it is the argument the "
        "field was built with and a call always supplies one, the default included"
    )
    assert typing.get_type_hints(series.Candidate)["point"] is RunPoint
    assert typing.get_type_hints(series.Candidate.model.fget)["return"] is str


# ----------------------------------------------------------------------------------
# The baseline pass rate, which no point carries and every delta needs
# ----------------------------------------------------------------------------------


def test_the_baseline_pass_rate_is_rebuilt_from_the_counts_the_baseline_side_recorded():
    """`RunPoint.pass_rate` is documented as "Candidate side of the widest judge",
    and there is no baseline field to read. R17.2's ruling is that the rate is
    reconstructed from the two numbers rigor recorded it from, and that this is not
    an approximation but the recorded rate.

    50 graded and 10 failed is 0.80, spelled out here rather than recomputed. The
    fixture's candidate side grades the same 50 and fails 17, so a reconstruction
    that read the candidate-side counts lands on 0.66 and neither number is any
    row's `pass_rate`."""
    field = _field_of(
        [_run("alpha-candidate", pass_rate=0.65), _run("beta-candidate", pass_rate=0.90)]
    )
    assert field.baseline_pass_rate == pytest.approx(0.8)
    assert field.baseline_pass_rate != pytest.approx(0.66), (
        "this is the candidate side's rate -- `judge_failures_candidate` was read"
    )
    assert field.baseline_pass_rate not in (0.65, 0.90), (
        "`RunPoint.pass_rate` is the candidate side and is not a baseline rate"
    )


def test_a_run_that_graded_nothing_on_one_side_is_excluded_and_the_field_says_why():
    """The partition runs inside the field, and this is what proves it: the silent
    run is not merely absent from `candidates`, it is present in `excluded` with
    the sentence C4 wrote for it. A point that vanishes with no sentence is the
    table this whole pair of chunks exists to prevent.

    The expected reason is taken from `partition_comparable` itself, which is
    merged, reviewed, and not the code under test here."""
    silent = _run("gpt-candidate-v9", judged_baseline=0, judge_failures_baseline=0)
    field = _field_of([_run("alpha-candidate"), _run("beta-candidate"), silent])
    expected = series.partition_comparable(
        [silent], against=series.comparability_key(silent)
    ).excluded[0]
    assert _models(field) == ["alpha-candidate", "beta-candidate"]
    assert [exclusion.point for exclusion in field.excluded] == [silent]
    assert field.excluded[0].reason == expected.reason
    assert field.baseline_pass_rate == pytest.approx(0.8)


# ----------------------------------------------------------------------------------
# The delta, which is a subtraction and not a statistic
# ----------------------------------------------------------------------------------


def test_the_delta_is_the_two_recorded_rates_subtracted_and_expressed_in_points():
    """`(cand - base) * 100`. Both signs are asserted, from one fixture, because a
    delta computed the other way round reads perfectly on a page and reverses every
    migration decision on it. 0.65 against a baseline of 0.80 is -15.0 points and
    0.90 against the same baseline is +10.0; an implementation that forgot the
    hundred lands on -0.15, and one that subtracted backwards on +15.0."""
    field = _field_of(
        [_run("alpha-candidate", pass_rate=0.65), _run("beta-candidate", pass_rate=0.90)]
    )
    deltas = {candidate.point.candidate_model: candidate.delta_pp for candidate in field.candidates}
    assert deltas["alpha-candidate"] == pytest.approx(-15.0)
    assert deltas["beta-candidate"] == pytest.approx(10.0)


def test_a_candidate_whose_run_recorded_no_pass_rate_has_no_delta_rather_than_a_delta_of_zero():
    """`None` if either side is `None`. A run whose judge graded nothing has an
    unknown rate, and a delta of 0.0 for it is a row saying the candidate matched
    the baseline exactly -- the strongest claim on the page, drawn from no
    measurement at all."""
    field = _field_of(
        [_run("alpha-candidate", pass_rate=None), _run("beta-candidate", pass_rate=0.90)]
    )
    assert field.candidates[0].delta_pp is None
    assert field.candidates[1].delta_pp == pytest.approx(10.0)


# ----------------------------------------------------------------------------------
# Order: by model name, never by result
# ----------------------------------------------------------------------------------


def test_candidates_are_ordered_by_model_name_and_never_by_pass_rate():
    """The contract's second "must not", and the fixture is built so that the three
    plausible wrong answers are all visible: the input order is gamma, alpha, beta;
    the pass-rate order descending is beta, gamma, alpha and ascending is alpha,
    gamma, beta. Only the model order is alpha, beta, gamma.

    The reason is in the contract and is about the reader, not about tidiness: a
    table sorted by result invites position to be read as ranking, and the point of
    the field is that the reader does the ranking."""
    points = [
        _run("gamma-candidate", pass_rate=0.50),
        _run("alpha-candidate", pass_rate=0.10),
        _run("beta-candidate", pass_rate=0.90),
    ]
    assert _models(_field_of(points)) == ["alpha-candidate", "beta-candidate", "gamma-candidate"]


def test_the_same_candidate_compared_twice_appears_once_as_its_newer_run():
    """The edge table's third row. The newer point is asserted by its *numbers* as
    well as its identity: keeping the older run's row would render a stale delta
    beside a fresh date, which is worse than either.

    The newer run is passed first, so an implementation that simply keeps whichever
    it saw last is not accidentally right."""
    older = _run("claude-candidate-v2", created=_AUG_10, pass_rate=0.30)
    newer = _run("claude-candidate-v2", created=_AUG_20, pass_rate=0.65)
    other = _run("gpt-candidate-v9", created=_AUG_20, pass_rate=0.90)
    field = _field_of([newer, older, other])
    assert _models(field) == ["claude-candidate-v2", "gpt-candidate-v9"]
    assert field.candidates[0].point == newer
    assert older not in [candidate.point for candidate in field.candidates]
    assert field.candidates[0].delta_pp == pytest.approx(-15.0), (
        "the older run's 0.30 would have been -50.0 points"
    )


# ----------------------------------------------------------------------------------
# Spread and staleness, row by row of the edge table
# ----------------------------------------------------------------------------------


def test_three_candidates_compared_on_one_day_have_a_spread_of_zero_days_and_are_not_flagged():
    """The edge table's second row. `0.0` and not `None`: the runs *were* dated and
    the spread *was* measured, and collapsing "measured, and it was nothing" into
    "not measured" loses the only fact on the row worth having. The float check is
    there because `False == 0.0` and `0 == 0.0` both pass a bare equality."""
    field = _field_of([_run(model, created=_AUG_20) for model in ("alpha", "beta", "gamma")])
    assert len(field.candidates) == 3
    assert field.spread_days == 0.0
    assert isinstance(field.spread_days, float)
    assert field.spread_flagged is False
    assert [candidate.stale_days for candidate in field.candidates] == [0.0, 0.0, 0.0]


def test_each_candidates_staleness_is_its_age_against_the_newest_run_in_the_field():
    """The contract's wording is "this run's age against the newest in the field",
    so the newest run's own staleness is 0.0 and not `None`, and every age is
    measured against the field rather than against today -- which is what makes the
    number reproducible when the report is re-rendered a month later."""
    field = _field_of(
        [
            _run("alpha-candidate", created=_AUG_10),
            _run("beta-candidate", created=_AUG_17),
            _run("gamma-candidate", created=_AUG_20),
        ]
    )
    assert [candidate.stale_days for candidate in field.candidates] == [10.0, 3.0, 0.0]
    assert field.spread_days == 10.0


def test_a_candidate_whose_run_recorded_no_date_keeps_its_place_and_never_sets_the_spread():
    """The edge table's fourth row, in all three of its clauses.

    The dateless run carries the alphabetically *first* model name, which is exactly
    where the edge table's "sorted last" and the Must-not's "order by
    `candidate_model`" disagree. C5's implementer and this suite's author, working
    blind, read that sentence in opposite directions -- which is the disagreement a
    blind pair exists to surface. Ruled for `candidate_model` order, on two grounds:

    The Must-not is normative and unqualified, and the edge row's other two clauses
    (`stale_days is None`, never sets `spread_days`) are both facts about dates
    rather than about position -- so "sorted last" most plausibly means "sorts
    oldest wherever recency is compared", which is the per-model winner, the group
    tie-break and the spread. It has real work to do under that reading.

    And "last row" is not total **as the contract writes it**. That is a claim
    about the sentence, not about the idea: "dateless rows last, `candidate_model`
    within each block" is a perfectly total order, and an earlier draft of this
    docstring said "the only total reading", which is simply false -- a reader who
    notices that discounts the two grounds above with it. What the contract
    supplies is one clause, "sorted last", with no tie-break for two dateless
    runs; under it alone the table could differ between two renders of one log,
    which is the failure the Must-not's own stated reason ("stable across
    renders") exists to prevent. The second sentence would settle it and the
    contract does not contain one, so this is the weakest of the three grounds and
    the other two carry the ruling.

    Position also stops encoding anything a reader could misread: `stale_days is
    None` already marks the dateless row, in its own cell, where it belongs.

    "Never sets `spread_days`" is asserted by a literal: the two dated runs are two
    days apart, so the spread is 2.0. A dateless run folded in as an epoch or as
    today's date would produce a spread of thousands of days and flag a field that
    is two days wide."""
    field = _field_of(
        [
            _run("aaa-candidate", created=""),
            _run("mmm-candidate", created=_AUG_10),
            _run("zzz-candidate", created=_AUG_12),
        ]
    )
    assert _models(field) == ["aaa-candidate", "mmm-candidate", "zzz-candidate"]
    assert [candidate.stale_days for candidate in field.candidates] == [None, 2.0, 0.0]
    assert field.spread_days == 2.0
    assert field.spread_flagged is False


def test_a_field_in_which_no_run_recorded_a_date_has_no_spread_and_is_not_flagged():
    """The edge table's fifth row. `spread_days is None` and `spread_flagged is
    False` -- not a flag raised on an unknown spread, which would print "these runs
    are weeks apart" about runs whose dates nobody wrote down."""
    field = _field_of([_run("alpha-candidate", created=""), _run("beta-candidate", created="")])
    assert field.spread_days is None
    assert field.spread_flagged is False
    assert [candidate.stale_days for candidate in field.candidates] == [None, None]


def test_the_default_window_flags_a_spread_of_more_than_seven_days_and_not_of_exactly_seven():
    """`spread_flagged` is `spread_days > stale_after_days`, strictly. Seven days
    exactly is the boundary and is the one input that separates `>` from `>=`;
    eight is the case the contract's failure mode describes -- "three candidates
    measured three weeks apart render as a fair field, with the baseline having
    drifted underneath them"."""
    exactly = _field_of(
        [_run("alpha-candidate", created=_AUG_13), _run("beta-candidate", created=_AUG_20)]
    )
    assert exactly.spread_days == 7.0
    assert exactly.spread_flagged is False
    wider = _field_of(
        [_run("alpha-candidate", created=_AUG_12), _run("beta-candidate", created=_AUG_20)]
    )
    assert wider.spread_days == 8.0
    assert wider.spread_flagged is True


def test_the_staleness_window_is_a_keyword_parameter_and_not_a_literal_in_the_body():
    """The contract's reviewer note asks for exactly this: the default is
    defensible, but what matters is that it is a parameter. One field of fixed
    width is flagged at one setting and not at another, which a literal `7.0` in
    the body cannot do.

    Keyword-only, per the signature's `*`. A positional second argument would let a
    caller pass a window where a sequence was meant and get a silent answer."""
    points = [_run("alpha-candidate", created=_AUG_10), _run("beta-candidate", created=_AUG_20)]
    assert _field_of(points).spread_flagged is True
    assert _field_of(points, stale_after_days=30.0).spread_flagged is False
    assert _field_of(points, stale_after_days=3.0).spread_flagged is True
    assert _field_of(points, stale_after_days=30.0).spread_days == 10.0, (
        "the window decides the flag and must not change the measurement"
    )
    with pytest.raises(TypeError):
        series.candidate_field(points, 30.0)  # type: ignore[misc]


# ----------------------------------------------------------------------------------
# The caveats, which have to survive the trip from the partition to the table
# ----------------------------------------------------------------------------------


def test_a_caveat_the_partition_raised_on_a_kept_run_arrives_on_the_field():
    """R17.3's requirement, in the vocabulary C4 shipped. "Dropping the flags on
    the floor at this layer means the caveat never reaches the table, and a caveat
    that reaches nobody is the same as not having computed it."

    The run with uneven coverage is asserted to be in *both* tuples: a caveat
    annotates a row, it does not remove one, and an implementation that treated
    `caveats` as a second kind of exclusion would drop the run from the table while
    printing a note about it.

    The expected sentence comes from `partition_comparable`, which is merged and is
    not the code under test."""
    lopsided = _run("gpt-candidate-v9", judged_candidate=44, judge_failures_candidate=11)
    field = _field_of([_run("claude-candidate-v2"), lopsided])
    expected = series.partition_comparable(
        [lopsided], against=series.comparability_key(lopsided)
    ).caveats
    assert len(expected) == 1
    assert [caveat.reason for caveat in field.caveats] == [expected[0].reason]
    assert [caveat.point for caveat in field.caveats] == [lopsided]
    assert lopsided in [candidate.point for candidate in field.candidates]
    assert field.excluded == ()


def test_a_run_carrying_two_caveats_arrives_with_both_of_them():
    """One point may carry more than one, and the two are different claims: uneven
    coverage is a doubt about the numbers on the row, and an A/A comparison is a
    statement about what the row is for. A field that de-duplicated caveats by
    point would print the first and lose the second."""
    calibration = _run(
        "gpt-baseline-v1",
        judged_candidate=44,
        judge_failures_candidate=11,
    )
    field = _field_of([_run("claude-candidate-v2"), calibration])
    expected = series.partition_comparable(
        [calibration], against=series.comparability_key(calibration)
    ).caveats
    assert len(expected) == 2
    assert [caveat.reason for caveat in field.caveats] == [note.reason for note in expected]
    assert calibration in [candidate.point for candidate in field.candidates]


# ----------------------------------------------------------------------------------
# Grouping, and the tie-break that has to be total
# ----------------------------------------------------------------------------------


def test_two_nights_that_tried_different_candidates_land_in_one_field():
    """R17.4's mutant, inherited from C4's tester as that ruling asks. The key holds
    `goldenset_hash`, `judges_hash`, `n_per_item` and `baseline_model` and has never
    held `candidate_model` -- and a key that did hold it would make every group a
    group of one, `candidate_field` would return `None` every time, and the table
    would never render at all. That failure is silent: a report with no comparison
    table looks like a report of a log with nothing to compare."""
    monday = _run("claude-candidate-v2", created=_AUG_17, pass_rate=0.65)
    friday = _run("gpt-candidate-v9", created=_AUG_20, pass_rate=0.90)
    field = _field_of([monday, friday])
    assert field.key == series.comparability_key(monday)
    assert field.key == series.comparability_key(friday)
    assert [candidate.point for candidate in field.candidates] == [monday, friday]


def test_the_field_is_built_from_the_largest_group_of_comparable_runs():
    """Largest is the first tier and beats the other two. The smaller group here
    holds *both* of the newest runs, so an implementation that reached for the
    newest point before counting the groups renders a two-row table while a
    three-row one was available."""
    big = [
        _run(model, created=_AUG_10)
        for model in ("alpha-candidate", "beta-candidate", "gamma-candidate")
    ]
    small = [_other_group(model, created=_AUG_20) for model in ("delta-cand", "epsilon-cand")]
    field = _field_of([*small, *big])
    assert field.key == series.comparability_key(big[0])
    assert _models(field) == ["alpha-candidate", "beta-candidate", "gamma-candidate"]


def test_two_equally_large_groups_break_the_tie_on_the_group_holding_the_newest_run():
    """The edge table's last row, and the second tier. Both orderings are built,
    because a tie-break that fell through to the order the groups were seen in
    would be right in one of them by accident."""
    older = [_run(model, created=_AUG_10) for model in ("alpha-candidate", "beta-candidate")]
    newer = [_other_group(model, created=_AUG_20) for model in ("gamma-cand", "delta-cand")]
    field = _field_of([*older, *newer])
    assert field.key == series.comparability_key(newer[0])
    assert field.key != series.comparability_key(older[0])
    assert _field_of([*newer, *older]).key == field.key


def test_two_equally_large_groups_with_no_dated_run_still_pick_one_winner_deterministically():
    """R17.5's third tier, which exists because the first two can both run out:
    two groups can tie on size *and* contain no dated point at all, and then "the
    newest point" does not exist and the winner falls out of dict insertion order
    over hashes -- stable on one machine, not guaranteed across a rebuild.

    Which group wins is deliberately *not* asserted. A stable arbitrary answer is
    what the ruling asks for, so the same input is built three ways and the three
    answers are asserted equal; pinning one particular winner would pin whichever
    order the first implementation happened to produce, which is the opposite of
    the guarantee. What is asserted is that the winner is one of the two real
    groups and that the whole field, not merely the key, is the same each time."""
    left = [_run(model, created="") for model in ("alpha-candidate", "beta-candidate")]
    right = [_other_group(model, created="") for model in ("gamma-cand", "delta-cand")]
    orders = [
        [*left, *right],
        [*right, *left],
        [left[0], right[0], left[1], right[1]],
        [right[1], left[1], right[0], left[0]],
    ]
    fields = [_field_of(order) for order in orders]
    assert len({field.key for field in fields}) == 1, (
        "the winner depends on the order the groups were built in, which is a "
        "document that differs between two renders of one log"
    )
    assert len({tuple(_models(field)) for field in fields}) == 1
    assert fields[0].key in {
        series.comparability_key(left[0]),
        series.comparability_key(right[0]),
    }


def test_a_larger_group_of_runs_that_recorded_no_hashes_does_not_beat_a_smaller_group_that_did():
    """C4's `is_identifying` exists for this caller and says so: "dataclass equality
    alone will happily merge every run that recorded nothing into one
    confident-looking group -- `"" == ""` and `0 == 0` both read perfectly and both
    mean 'neither of us said'".

    Three runs that recorded no golden-set hash have equal keys and are not
    comparable with each other. If they win the group count, every one of them is
    then excluded by the partition and the field is `None` -- so a log with two
    perfectly comparable runs in it renders no table, and the reason is a group
    that never existed. The silent runs are also the newest here, so they would win
    the second tier as well."""
    silent = [
        _run(model, goldenset_hash="", created=_AUG_20)
        for model in ("alpha-cand", "beta-cand", "gamma-cand")
    ]
    real = [_run(model, created=_AUG_10) for model in ("mmm-candidate", "zzz-candidate")]
    field = _field_of([*silent, *real])
    assert field.key == series.comparability_key(real[0])
    assert _models(field) == ["mmm-candidate", "zzz-candidate"]


# ----------------------------------------------------------------------------------
# C5, the fix pass: what mutation testing found and the shipped suite did not
# ----------------------------------------------------------------------------------
#
# Thirty-nine mutants, fifteen survivors, and the survivors are the useful part.
# The tests below each kill one, and one lesson runs through most of them:
#
# **A fixture where the broken and the correct implementation agree is a fixture
# that tests nothing.** Every C5 fixture above hard-codes `judged_baseline=50`
# and `judge_failures_baseline=10` on *every* point, so a point's own baseline
# and the field's summary baseline are the same number and an implementation
# that subtracted the wrong one was invisible to 269 green tests. That is C4's
# hash mutant one chunk over, where every fixture hash differed at character 0
# and a prefix bug survived the whole suite. So the fixtures here differ on the
# axis the assertion is about, deliberately, and the docstrings say which axis.
#
# The other recurring shape is an absence with nowhere to be seen: a run that
# leaves the table through a rule that mints no sentence is a run that vanishes,
# and no assertion about `candidates` can notice. Several tests below therefore
# assert over `candidates` *and* `excluded` together.

#: Fifty days before `_AUG_20`, for the one fixture that needs a gap wide enough
#: that folding it into the spread is unmistakable rather than arguable.
_JUL_01 = "2026-07-01T00:00:00+00:00"


# ----------------------------------------------------------------------------------
# The baseline each row was actually measured against
# ----------------------------------------------------------------------------------


def test_each_delta_is_against_the_baseline_its_own_run_measured_and_not_the_headers():
    """The mutation review's first finding, and the one the whole section is shaped
    around. `delta_pp` computed against `CandidateField.baseline_pass_rate` instead
    of against the row's own baseline survived the entire suite, because every
    fixture in it gave every point the same baseline-side counts -- so the two
    quantities were the same float and no assertion could tell them apart.

    Here they differ. Both candidates scored 0.65. Alpha's night measured the
    baseline at 40/50 = 0.80, so alpha is -15.0 points. Gamma's night, ten days
    later, measured it at 30/50 = 0.60, so gamma is +5.0. An implementation that
    used the header for both would print +5.0 twice, which is not merely a wrong
    number: it reports a candidate that lost fifteen points as having gained five.

    The header itself is asserted too, and it is the *newest* row's baseline and
    not the first row's. Alphabetical order puts alpha first and the date puts
    gamma last, so `rendered[0]` and `_latest(rendered)` are different points and
    a header taken from the wrong one is visible."""
    early = _run(
        "alpha-candidate", created=_AUG_10, pass_rate=0.65,
        judged_baseline=50, judge_failures_baseline=10,
    )
    late = _run(
        "gamma-candidate", created=_AUG_20, pass_rate=0.65,
        judged_baseline=50, judge_failures_baseline=20,
    )
    field = _field_of([early, late])
    deltas = {candidate.model: candidate.delta_pp for candidate in field.candidates}
    assert deltas["alpha-candidate"] == pytest.approx(-15.0)
    assert deltas["gamma-candidate"] == pytest.approx(5.0)
    assert deltas["alpha-candidate"] != pytest.approx(5.0), (
        "alpha's delta was computed against the field's summary baseline rather than "
        "against the baseline alpha's own run measured"
    )
    assert field.baseline_pass_rate == pytest.approx(0.6), "the newest row's baseline"
    assert field.baseline_pass_rate != pytest.approx(0.8), (
        "the header came from `rendered[0]` -- the first row alphabetically -- rather "
        "than from the newest run in the field"
    )


def test_rows_measured_against_different_baselines_raise_a_caveat_on_the_row_the_header_came_from():  # noqa: E501
    """The fix for the finding above, and it is not the delta arithmetic -- that
    was already right. It is that a reader holding the field could not tell.

    `baseline_pass_rate` is one number from one run and every `delta_pp` beside it
    is against a different one, so once the baselines disagree the header is a
    number from one row printed above rows it is not the baseline of, and adding
    it back to a delta yields a pass rate nothing measured. Nothing in the type
    said so. The caveat is attached to the run the header came from, because that
    is the row a reader would otherwise take the number to be about, and it names
    both counts of every row so the disagreement is checkable in place.

    A caveat and not an exclusion, because no row is wrong: each delta is against
    its own baseline and each is correct. What is unsafe is the header."""
    early = _run("alpha-candidate", created=_AUG_10, judged_baseline=50, judge_failures_baseline=10)
    late = _run("gamma-candidate", created=_AUG_20, judged_baseline=50, judge_failures_baseline=20)
    field = _field_of([early, late])
    assert len(field.caveats) == 1
    note = field.caveats[0]
    assert note.point is late, "the caveat belongs on the run the header number came from"
    assert "40/50" in note.reason and "30/50" in note.reason, (
        "the sentence has to name the counts, or a reader cannot check it against the log"
    )
    assert "alpha-candidate" in note.reason and "gamma-candidate" in note.reason
    assert note.point in [candidate.point for candidate in field.candidates], (
        "a caveat whose point is not a rendered row has no row to be printed against"
    )


def test_a_baseline_that_moved_overnight_is_caveated_although_the_spread_is_not_flagged():
    """`spread_flagged` is a *proxy* for baseline drift and this is where it fails.
    It measures how far apart the runs are in time, which is neither necessary nor
    sufficient: a golden set re-scored on Tuesday night, or a hosted model changed
    under a fixed id, moves the baseline in a day. One day is inside every
    plausible window, so the flag is `False` and the drift is real.

    Two runs a day apart, baselines 0.80 and 0.60. `spread_flagged` says the field
    is fine. The caveat says what actually happened."""
    monday = _run(
        "alpha-candidate", created=_AUG_12, judged_baseline=50, judge_failures_baseline=10
    )
    tuesday = _run(
        "gamma-candidate", created=_AUG_13, judged_baseline=50, judge_failures_baseline=20
    )
    field = _field_of([monday, tuesday])
    assert field.spread_days == 1.0
    assert field.spread_flagged is False, "one day apart is inside any plausible window"
    assert len(field.caveats) == 1
    assert field.caveats[0].point is tuesday


def test_rows_that_share_a_baseline_raise_no_drift_caveat_at_all():
    """The other half, and the half a caveat raised unconditionally would fail. The
    commonest field this module will ever build is a week of runs against one
    unchanged baseline, and a note printed against every one of those is a note
    every reader learns to skip -- which costs the drifted field its warning."""
    field = _field_of(
        [
            _run("alpha-candidate", created=_AUG_10),
            _run("beta-candidate", created=_AUG_17),
            _run("gamma-candidate", created=_AUG_20),
        ]
    )
    assert field.baseline_pass_rate == pytest.approx(0.8)
    assert field.caveats == ()


@pytest.mark.parametrize(
    ("failures", "printed"),
    [(-10, "60/50"), (60, "-10/50")],
    ids=["negative-failures", "more-failures-than-graded"],
)
def test_a_baseline_side_whose_two_counts_cannot_both_be_true_yields_no_rate(failures, printed):
    """`_baseline_pass_rate` is the only rate this chunk *computes* rather than
    reads, and it was the only one with no sanity bound. `judge_failures_baseline`
    and `judged_baseline` are two independent integers off a JSON payload -- a
    payload is JSON, not a type, and `_count` passes through whatever integer it
    finds. Minus ten failures out of fifty grades gives a pass rate of 1.2, and
    sixty out of fifty gives -0.2. Both are impossible; both were computed,
    subtracted and returned.

    The second is the one that would be acted on: a candidate at 0.90 against a
    baseline of -0.2 is +110 points, the largest improvement any report of this
    project could print, out of a payload that recorded nonsense. This is the class
    C4 minted four exclusions for.

    The run is not excluded -- its candidate side is intact and its counts are
    still on the point for anyone who wants to see why -- but the rate is refused,
    and refusing it takes the delta with it. The row that still has a sound
    baseline keeps its own delta, which is the point of computing them per row."""
    sound = _run("aaa-candidate", created=_AUG_10, pass_rate=0.65)
    broken = _run(
        "zzz-candidate", created=_AUG_20, pass_rate=0.90,
        judged_baseline=50, judge_failures_baseline=failures,
    )
    field = _field_of([sound, broken])
    assert _models(field) == ["aaa-candidate", "zzz-candidate"], (
        "the run is refused a rate, not a row"
    )
    assert field.baseline_pass_rate is None
    assert field.candidates[1].delta_pp is None, "a delta against an impossible rate is not a delta"
    assert field.candidates[0].delta_pp == pytest.approx(-15.0)
    assert len(field.caveats) == 1, (
        "a row whose baseline rate is unknown does not share the others' baseline, and "
        "not knowing is not the same fact as agreeing"
    )
    assert printed in field.caveats[0].reason


@pytest.mark.parametrize(
    ("failures", "rate"), [(0, 1.0), (50, 0.0)], ids=["none-failed", "all-failed"]
)
def test_the_two_ends_of_the_possible_range_are_rates_and_not_refusals(failures, rate):
    """The boundary the guard above is strict about in the other direction. A
    baseline that failed none of its fifty graded completions has a pass rate of
    1.0 and one that failed all fifty has 0.0 -- both are real readings a real
    panel produces, and refusing either would delete a row for being decisive.
    `<` in place of `<=` on either end of the check does exactly that."""
    every = [
        _run("alpha-candidate", judged_baseline=50, judge_failures_baseline=failures),
        _run("beta-candidate", judged_baseline=50, judge_failures_baseline=failures),
    ]
    field = _field_of(every)
    assert field.baseline_pass_rate == pytest.approx(rate)
    assert field.candidates[0].delta_pp == pytest.approx((0.65 - rate) * 100.0)


def test_the_delta_is_the_unrounded_float_the_subtraction_produced():
    """`delta_pp` "is not rounded: a rounded value is a rendering decision, and a
    renderer that wants one decimal place can take one, while a model that has
    already rounded cannot give back what it dropped."

    Asserted with `==` against the exact binary float and deliberately **not**
    with `pytest.approx`, which is what let a `round(..., 1)` mutant survive the
    whole suite: approx swallows the entire difference between the contract and
    its violation. 0.90 against a baseline of 40/50 is 9.999999999999998, and a
    model that returns 10.0 has made a rendering decision on the renderer's
    behalf.

    The residue is not incidental to the test; it *is* the test. There is no other
    way to tell a model that did not round from one that did."""
    field = _field_of(
        [_run("alpha-candidate", pass_rate=0.90), _run("beta-candidate", pass_rate=0.90)]
    )
    assert field.candidates[0].delta_pp == 9.999999999999998
    assert field.candidates[0].delta_pp != 10.0, "the delta has been rounded"


# ----------------------------------------------------------------------------------
# Every run in the log is in exactly one of the two tuples
# ----------------------------------------------------------------------------------


def test_a_run_under_another_key_reaches_excluded_with_the_partitions_own_sentence():
    """The whole log is partitioned against the winning key, not merely the winning
    group -- which costs a pass and buys a sentence for every run in the log that
    did not make the table. An implementation that partitioned only the group's own
    members produces an identical `candidates` tuple and an `excluded` tuple that
    silently omits every stranger, and no assertion about the rows can see it.

    The expected sentence comes from `partition_comparable`, which is merged and
    reviewed and is not the code under test."""
    stranger = _other_group("delta-candidate", created=_AUG_20)
    field = _field_of([_run("alpha-candidate"), _run("beta-candidate"), stranger])
    expected = series.partition_comparable([stranger], against=field.key).excluded[0]
    assert _models(field) == ["alpha-candidate", "beta-candidate"]
    assert [exclusion.point for exclusion in field.excluded] == [stranger]
    assert field.excluded[0].reason == expected.reason


def test_two_runs_that_named_no_candidate_are_two_exclusions_and_never_one_row():
    """`_newest_per_model` skips a run with no recorded `candidate_model` rather
    than keying on `""`, because `"" == ""` would fold two anonymous runs into one
    row and drop the other -- the empty-value hole C4 closes over the key's four
    fields, in the fifth field the key does not carry.

    Two of them, with different pass rates and different dates, so a merge is
    visible as *whichever* run won: 0.20 and 0.90 cannot both be the row. And two
    separate exclusions, because two runs that both failed to say what they were
    testing are two unknowns rather than one candidate measured twice."""
    anonymous_first = _run("", created=_AUG_10, pass_rate=0.20)
    anonymous_second = _run("", created=_AUG_20, pass_rate=0.90)
    field = _field_of(
        [anonymous_first, _run("alpha-candidate"), _run("beta-candidate"), anonymous_second]
    )
    assert _models(field) == ["alpha-candidate", "beta-candidate"]
    assert [exclusion.point for exclusion in field.excluded] == [
        anonymous_first,
        anonymous_second,
    ]
    reasons = [exclusion.reason for exclusion in field.excluded]
    assert reasons[0] == reasons[1], "one rule, one sentence, said twice"
    assert "records no candidate model" in reasons[0]
    assert "superseded" not in reasons[0], "these two runs did not supersede each other"


def test_runs_that_named_no_candidate_do_not_pad_the_width_of_their_group():
    """A group is ranked on how many *distinct candidate models* it holds, and an
    anonymous run contributes none: it cannot be a row, so counting it toward the
    number of rows a group would render is counting a row that will not exist.

    Two groups, and the padded one has more runs. It holds two real candidates and
    two anonymous runs; the other holds three real candidates. Counting `""` as a
    distinct model makes them tie at three, and the padded group then wins on run
    count and renders a two-row table where a three-row one was available."""
    padded = [
        _run("alpha-candidate", created=_AUG_10),
        _run("beta-candidate", created=_AUG_10),
        _run("", created=_AUG_10),
        _run("", created=_AUG_10),
    ]
    real = [
        _other_group(model, created=_AUG_10)
        for model in ("gamma-cand", "delta-cand", "epsilon-cand")
    ]
    field = _field_of([*padded, *real])
    assert field.key == series.comparability_key(real[0])
    assert _models(field) == ["delta-cand", "epsilon-cand", "gamma-cand"]


def test_a_run_beaten_to_its_row_by_a_newer_run_of_the_same_candidate_says_so():
    """The nightly job that re-ran a candidate on Monday and Thursday is the
    commonest log this module will ever read, and until this sentence existed the
    Monday run was in `candidates`, `excluded` and `caveats` alike -- absent from
    all three. A run that is in the log and in none of the tuples the field returns
    has vanished, and a reader cannot tell a run that was dropped from a run that
    was never written. That is the quietly-shrunk table `Exclusion` was minted to
    prevent, and decision 2's own stated rationale is that no run leaves without a
    sentence.

    The winning run's date is in the sentence, so the claim is checkable against
    the log rather than merely asserted."""
    older = _run("claude-candidate-v2", created=_AUG_10, pass_rate=0.30)
    newer = _run("claude-candidate-v2", created=_AUG_20, pass_rate=0.65)
    other = _run("gpt-candidate-v9", created=_AUG_20, pass_rate=0.90)
    field = _field_of([older, newer, other])
    assert _models(field) == ["claude-candidate-v2", "gpt-candidate-v9"]
    assert [exclusion.point for exclusion in field.excluded] == [older]
    assert field.excluded[0].reason.startswith(
        "excluded: superseded by this candidate's run of 2026-08-20."
    )
    accounted = [candidate.point for candidate in field.candidates]
    accounted += [exclusion.point for exclusion in field.excluded]
    assert {id(point) for point in accounted} == {id(point) for point in (older, newer, other)}, (
        "a run in the log and in neither tuple has disappeared with nothing said"
    )


def test_a_superseded_run_is_not_given_a_date_the_log_never_recorded():
    """Where nothing is dated, position in an append-only log is what decided the
    row, and the sentence says that rather than printing a date it does not have.
    An exclusion that named a date here would be inventing evidence in the one
    place this module's sentences exist to supply it -- and `_UNRECORDED` spliced
    into "superseded by this candidate's run of ..." reads as a formatting bug
    rather than as a missing fact."""
    first = _run("claude-candidate-v2", created="", pass_rate=0.30)
    second = _run("claude-candidate-v2", created="", pass_rate=0.65)
    other = _run("gpt-candidate-v9", created="", pass_rate=0.90)
    field = _field_of([first, second, other])
    assert [exclusion.point for exclusion in field.excluded] == [first]
    assert "neither run having recorded a date" in field.excluded[0].reason
    assert "run of unrecorded" not in field.excluded[0].reason


def test_a_caveat_raised_on_a_superseded_run_never_reaches_the_table():
    """Decision 4 -- caveats filtered to rendered rows -- is correct *and* now
    complete. The superseded run carries an uneven-coverage caveat, and a note
    whose point has no row has no row to be printed against: printed anyway it is a
    warning about numbers nowhere on the page.

    What makes that safe rather than merely quiet is the exclusion beside it. The
    run is still accounted for, with a sentence, and a reader who wants to know why
    the note was dropped can see that the run was."""
    stale = _run(
        "claude-candidate-v2", created=_AUG_10, judged_candidate=44, judge_failures_candidate=11
    )
    fresh = _run("claude-candidate-v2", created=_AUG_20)
    other = _run("gpt-candidate-v9", created=_AUG_20)
    field = _field_of([stale, fresh, other])
    raised = series.partition_comparable([stale], against=series.comparability_key(stale)).caveats
    assert len(raised) == 1, "the fixture was built to raise exactly one caveat on the stale run"
    assert field.caveats == (), "a caveat on a run with no row has no row to be printed against"
    assert [exclusion.point for exclusion in field.excluded] == [stale]
    assert "superseded" in field.excluded[0].reason


def test_exclusions_from_all_three_rules_come_back_in_the_order_the_log_wrote_them():
    """`_excluded`'s docstring claims log order explicitly, and the claim is the
    useful part: a reader working through why a run is missing has the log in front
    of them, and three lists concatenated by rule read as three lists.

    Three rules produce exclusions here -- a run under another key, a run that
    named no candidate, and a run superseded by a later run of itself -- and the
    log interleaves them. Returning the accumulator's own order would group the
    partition's exclusion ahead of the other two, which is right by accident in
    any fixture that does not interleave."""
    anonymous = _run("", created=_AUG_20)
    alpha = _run("alpha-candidate", created=_AUG_20)
    stranger = _other_group("delta-candidate", created=_AUG_20)
    superseded = _run("alpha-candidate", created=_AUG_10)
    beta = _run("beta-candidate", created=_AUG_20)
    field = _field_of([anonymous, alpha, stranger, superseded, beta])
    assert _models(field) == ["alpha-candidate", "beta-candidate"]
    assert [exclusion.point for exclusion in field.excluded] == [
        anonymous,
        stranger,
        superseded,
    ], "the exclusions are grouped by the rule that made them rather than given in log order"


# ----------------------------------------------------------------------------------
# Which group wins, and which run in it is the row
# ----------------------------------------------------------------------------------


def test_thirteen_nightly_runs_of_one_candidate_do_not_take_the_table_from_a_real_field():
    """`_field_rank` counts distinct candidate models first and run count second,
    and this is the case where the two readings of "largest group" diverge. A
    fortnight of nightly runs against one candidate is thirteen points and one row;
    ranking by run count alone hands the table to a group that cannot be a table,
    `_newest_per_model` collapses it to a single row, and `candidate_field` returns
    `None` -- for a log with a perfectly good two-candidate field beside it.

    `None` is the failure, and it is silent: a report with no comparison table
    looks exactly like a report of a log with nothing to compare."""
    nightly = [
        _run("claude-candidate-v2", created=f"2026-08-{day:02d}T00:00:00+00:00")
        for day in range(1, 14)
    ]
    pair = [_other_group(model, created=_AUG_20) for model in ("gamma-cand", "delta-cand")]
    field = _field_of([*nightly, *pair])
    assert field.key == series.comparability_key(pair[0])
    assert _models(field) == ["delta-cand", "gamma-cand"]


def test_two_groups_of_equal_width_are_separated_by_how_many_runs_they_hold():
    """The second term of the rank, which the contract's literal reading ("largest
    group") supplies and which only has work to do once the first term ties. Both
    groups here render two rows; the deeper one holds three runs and the shallower
    two, and the shallower holds the newer point -- so an implementation that
    dropped the run count falls through to the date and picks the other group.

    Both are legitimate two-row tables, which is why this needs asserting rather
    than merely observing: nothing downstream would look wrong."""
    deeper = [
        _run("alpha-candidate", created=_AUG_10),
        _run("alpha-candidate", created=_AUG_12),
        _run("beta-candidate", created=_AUG_12),
    ]
    shallower = [_other_group(model, created=_AUG_20) for model in ("gamma-cand", "delta-cand")]
    field = _field_of([*deeper, *shallower])
    assert field.key == series.comparability_key(deeper[0])
    assert _models(field) == ["alpha-candidate", "beta-candidate"]


def test_the_spread_is_measured_over_the_rendered_rows_and_not_over_every_kept_run():
    """`spread_days` is "newest minus oldest across the kept candidates", and the
    candidates are the rows -- not everything the partition kept. A superseded run
    is kept and is not a row, so folding it in measures a night that is not on the
    page: fifty days here rather than three, and a field two days wide flagged as
    stale because of a run nobody can see."""
    ancient = _run("claude-candidate-v2", created=_JUL_01)
    fresh = _run("claude-candidate-v2", created=_AUG_20)
    other = _run("gpt-candidate-v9", created=_AUG_17)
    field = _field_of([ancient, fresh, other])
    assert _models(field) == ["claude-candidate-v2", "gpt-candidate-v9"]
    assert field.spread_days == 3.0
    assert field.spread_flagged is False
    assert [candidate.stale_days for candidate in field.candidates] == [0.0, 3.0]


# ----------------------------------------------------------------------------------
# Undated runs sort oldest, in all three places that decides something
# ----------------------------------------------------------------------------------
#
# `_UNDATED` is `datetime.min`, and inverting it to `datetime.max` inverts the
# ruling in every place it has work to do: which run of a candidate is the row,
# which of two tied groups wins, and which row's baseline is the header. That
# mutant survived 269 green tests, because only the fourth consequence -- the
# spread -- was asserted anywhere. Each of the three is pinned below.


def test_a_dated_run_beats_an_undated_run_of_the_same_candidate():
    """A run with no readable `created` "is neither fresh nor stale" and sorts
    before every dated run, "so that 'the newest point' never resolves to a point
    that is not on the timeline at all". Where a candidate has one dated run and
    one undated, the dated one is the row.

    The undated run is passed *first*, so position in the log cannot rescue the
    right answer by accident, and it carries a different pass rate so the wrong
    winner shows up in the delta as well as in the date."""
    undated = _run("claude-candidate-v2", created="", pass_rate=0.30)
    dated = _run("claude-candidate-v2", created=_AUG_20, pass_rate=0.65)
    other = _run("gpt-candidate-v9", created=_AUG_20, pass_rate=0.90)
    field = _field_of([undated, dated, other])
    assert field.candidates[0].point is dated
    assert field.candidates[0].delta_pp == pytest.approx(-15.0), "the undated run's 0.30 is -50.0"
    assert field.candidates[0].stale_days == 0.0
    assert [exclusion.point for exclusion in field.excluded] == [undated]


def test_two_undated_runs_of_one_candidate_resolve_to_the_later_record():
    """"Where nothing is dated the later record in an append-only log wins, which
    is the same claim 'newer' makes with the dates missing." Position breaks the
    tie and it breaks it forwards; breaking it backwards is a defensible-looking
    reading that makes the first run of a re-run candidate the row, and prints the
    number that was superseded."""
    first = _run("claude-candidate-v2", created="", pass_rate=0.30)
    second = _run("claude-candidate-v2", created="", pass_rate=0.65)
    other = _run("gpt-candidate-v9", created="", pass_rate=0.90)
    field = _field_of([first, second, other])
    assert field.candidates[0].point is second
    assert field.candidates[0].delta_pp == pytest.approx(-15.0)
    assert [exclusion.point for exclusion in field.excluded] == [first]


def test_a_group_holding_an_undated_run_loses_the_tie_break_to_an_all_dated_group():
    """`_field_rank`'s third term is "the group's newest point, with an undated
    group sorting below every dated one". Two groups, two rows each, one run in the
    second undated: the all-dated group's newest is Aug 20 and the other's is Aug
    10, so the all-dated group wins.

    Inverting `_UNDATED` makes the undated run the newest thing in the log and
    hands the table to the group that recorded the least -- the same shape as
    letting the biggest pile of silence win the selection, one tier down."""
    dated = [_run(model, created=_AUG_20) for model in ("alpha-candidate", "beta-candidate")]
    partly = [
        _other_group("gamma-cand", created=_AUG_10),
        _other_group("delta-cand", created=""),
    ]
    field = _field_of([*partly, *dated])
    assert field.key == series.comparability_key(dated[0])
    assert _models(field) == ["alpha-candidate", "beta-candidate"]


def test_the_header_baseline_comes_from_a_dated_row_rather_than_an_undated_one():
    """`_latest` decides which row's baseline the header quotes, and it answers the
    question the same way `_newest_per_model` does -- so the run whose baseline the
    header names is the run a reader would point at. An undated row sorting newest
    would quote the baseline of the one run that cannot be placed on the timeline
    at all.

    The two rows carry different baseline-side counts, which is the only way to
    tell which one was read: 30/50 on the undated row against 40/50 on the dated
    one."""
    undated = _run("aaa-candidate", created="", judged_baseline=50, judge_failures_baseline=20)
    dated = _run("zzz-candidate", created=_AUG_20, judged_baseline=50, judge_failures_baseline=10)
    field = _field_of([undated, dated])
    assert field.baseline_pass_rate == pytest.approx(0.8)
    assert field.baseline_pass_rate != pytest.approx(0.6), (
        "the header was taken from the undated row, which sorted newest"
    )


# ----------------------------------------------------------------------------------
# One observation is not a spread
# ----------------------------------------------------------------------------------


def test_one_dated_row_among_undated_ones_has_no_spread_rather_than_a_spread_of_zero():
    """`spread_days` is `None` "not `0.0`, which would claim the field was measured
    in a single sitting" -- and one dated row makes that claim just as falsely as
    none does. A single observation has no spread: `max(dated) - min(dated)` over
    one element is 0.0 by arithmetic and not by measurement, and the field it
    describes may have runs weeks apart whose dates nobody recorded.

    `stale_days` is unaffected and deliberately so: "the newest run in the field"
    exists as soon as one run is dated, and the dated row's age against itself is
    0.0. That number was measured. The spread was not."""
    field = _field_of(
        [_run("alpha-candidate", created=""), _run("beta-candidate", created=_AUG_20)]
    )
    assert field.spread_days is None, "one observation cannot say the field was measured at once"
    assert field.spread_flagged is False
    assert [candidate.stale_days for candidate in field.candidates] == [None, 0.0]


# ----------------------------------------------------------------------------------
# What a row and a field carry for the chunks that render them
# ----------------------------------------------------------------------------------


def test_a_candidate_row_names_its_model_without_reaching_through_the_point():
    """A row of this table *is* a candidate model: it is what `_newest_per_model`
    keys on, what the rows are ordered by, and what a run must record to have a row
    at all. C6's `thresholds` is keyed on model strings, so every line that joins a
    correction onto a row joins it on this, and without it every one of them reads
    `candidate.point.candidate_model`.

    A property and not a field, so there is still exactly one candidate model on a
    row and no second slot that can be made to disagree with the first."""
    field = _field_of([_run("alpha-candidate"), _run("beta-candidate")])
    assert [candidate.model for candidate in field.candidates] == [
        "alpha-candidate",
        "beta-candidate",
    ]
    assert all(
        candidate.model == candidate.point.candidate_model for candidate in field.candidates
    )
    assert "model" not in {member.name for member in dataclasses.fields(field.candidates[0])}, (
        "a second copy of the model is a pair that can disagree"
    )
    with pytest.raises(AttributeError):
        field.candidates[0].model = "something-else"  # type: ignore[misc]


def test_the_field_records_the_window_it_was_built_with():
    """R20.3. `stale_after_days` was a parameter of `candidate_field`,
    `_STALE_AFTER_DAYS` is private, and the field carried neither -- so a renderer
    holding only the field had one honest sentence available for `spread_flagged`
    and no number to put in it. "Measured more than 7 days apart", printed beside a
    field built with `stale_after_days=30.0`, is two true halves and a false
    sentence, which is this chunk's own failure mode one layer down.

    The non-default case is the one that matters and is asserted first: a field
    built with a thirty-day window reports thirty, not seven. A field that stored
    the module default would pass an assertion about the default alone."""
    points = [_run("alpha-candidate", created=_AUG_10), _run("beta-candidate", created=_AUG_20)]
    wide = _field_of(points, stale_after_days=30.0)
    assert wide.stale_after_days == 30.0, "the field reports this module's default, not the window"
    assert wide.spread_days == 10.0
    assert wide.spread_flagged is False
    default = _field_of(points)
    assert default.stale_after_days == 7.0
    assert isinstance(default.stale_after_days, float)
    assert default.spread_flagged is True
    tight = _field_of(points, stale_after_days=3.0)
    assert tight.stale_after_days == 3.0
    assert tight.spread_flagged is True

