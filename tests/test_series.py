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
import io
import json
import os
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from model_migration_kit import series
from model_migration_kit.series import RunPoint, run_point

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
