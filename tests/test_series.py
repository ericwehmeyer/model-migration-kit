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
import subprocess
import sys
import tracemalloc
import typing
from pathlib import Path

import pytest
from opik_rigor import EvidenceError, EvidenceLog, EvidenceRecord

from model_migration_kit import series
from model_migration_kit.errors import ArtifactError
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
    makes it an error so it cannot reach a reader."""
    with pytest.raises(ValueError):
        series.spot_check(88, 8, 0, k=0)


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
