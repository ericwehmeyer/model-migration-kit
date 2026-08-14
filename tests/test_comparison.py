"""Acceptance tests for :mod:`model_migration_kit.comparison`.

Written from the frozen contract, never from the module: `docs/build-plan.md`
§6 Amendment 1 (the authoritative verdict spec), `docs/session-2-contract.md`
§§2-3, and the measured failures recorded in `docs/session-2-verdict-review.md`.
The author of this file did not write `comparison.py` and did not read it while
deriving a single expected value.

**Where every number comes from.** Nothing here was produced by running the code
under test.

* Mann-Whitney p-values are the verdict review's own figures, re-derived with
  ``scipy.stats.mannwhitneyu(current, baseline, alternative="less")`` -- the test
  ``opik-rigor`` wraps -- outside this package. 200 values against 200 with ten
  imputed floors gives **p = 0.0006944255237317142**, exactly the 0.00069 the
  review reports; four low values out of 200 gives **p = 0.022478743471552588**;
  two low out of 40 gives **p = 0.07994233688696428**.
* Pass-rate bounds and the ``underpowered``/``runs_needed`` pair come from
  ``opik_rigor.assert_pass_rate`` itself, which the contract says the verdict
  *consumes* rather than recomputes: 190/200 -> lower bound 0.9181081670817905;
  38/40 -> 0.8596681784340271 with ``underpowered=True`` and
  ``runs_needed=113``; 36/40 (observed exactly on the floor) -> underpowered with
  no achievable n.
* Required-n is derived here from the contract's own formula,
  ``n ~= (z_a + z_b)^2 (p1(1-p1) + p2(1-p2)) / d^2``, with stdlib
  ``statistics.NormalDist`` -- see :func:`_required_n`. It reproduces the
  contract's stated 108-229 band across baseline rates 0.95 down to 0.80, which
  is the cross-check that the reading of the formula is the intended one.
* Latency expectations are hand-picked so that every common p90 convention
  (stdlib ``quantiles`` exclusive and inclusive, and nearest-rank) agrees, so the
  assertion tests the number and not a tie-breaking choice.

Everything is offline, keyless, and free of RNG: every artifact is constructed
completion by completion.

The module under test is being written in parallel against the same contract.
Accessors here (:func:`_get`, :func:`_resolve`) accept a small set of plausible
*names* for a value the contract requires to exist, and fail loudly listing what
they looked for when none is present. They never adapt an expected value.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any

import pytest
from opik_rigor.judge import SCORE_MAX, SCORE_MIN

from model_migration_kit.contracts import Completion, RunHeader, Verdict, utc_now
from model_migration_kit.errors import ArtifactError, JudgeConfigError
from model_migration_kit.judging import JudgedArtifact, JudgeRecord, Thresholds
from model_migration_kit.runner import RunArtifact

try:  # The module is written in parallel with this file; absence is a finding,
    from model_migration_kit import comparison as _comparison  # not a reason to skip.
except Exception as exc:  # pragma: no cover - exercised only while it is missing
    _comparison = None
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None

# --------------------------------------------------------------------------- #
# Fixed identities. Two artifacts are comparable only if these match.
# --------------------------------------------------------------------------- #

GOLDENSET_HASH = "a" * 64
OTHER_GOLDENSET_HASH = "c" * 64
JUDGES_HASH = "b" * 64
OTHER_JUDGES_HASH = "d" * 64
RUBRIC_HASH = "e" * 64
JUDGE_MODEL = "claude-sonnet-4-5-20250929"

#: The single judge used wherever the judge panel is not the thing under test.
J = "helpfulness"

BASELINE_MODEL = "model-a-20260101"
CANDIDATE_MODEL = "model-b-20260101"

DEFAULTS = Thresholds()
VERDICTS = frozenset({Verdict.GO, Verdict.NO_GO, Verdict.REVIEW})

_MISSING = object()
#: A second sentinel, so ``_search`` can ask ``_get`` for "absent" without that
#: request being mistaken for "no default given".
_ABSENT = object()


# --------------------------------------------------------------------------- #
# Independent oracles.
# --------------------------------------------------------------------------- #


def _required_n(baseline_rate: float, *, effect: float = 0.10, alpha: float = 0.05,
                power: float = 0.80) -> float:
    """The contract's two-proportion normal approximation, from stdlib alone.

    ``n ~= (z_a + z_b)^2 (p1(1-p1) + p2(1-p2)) / d^2`` with a one-sided z for
    alpha, because the regression test rigor runs is one-sided
    (``alternative="less"``). Sanity check on the reading: this returns 108.19 at
    a 0.95 baseline and 228.75 at 0.80, and session-2-contract.md §2 states the
    approximation "gives 108-229 per side across plausible baseline rates".
    """
    z_alpha = NormalDist().inv_cdf(1.0 - alpha)
    z_beta = NormalDist().inv_cdf(power)
    p1 = baseline_rate
    p2 = baseline_rate - effect
    return (z_alpha + z_beta) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / effect**2


def test_required_n_oracle_reproduces_the_contracts_stated_band() -> None:
    """Guards the oracle itself: the formula must reproduce the contract's band.

    If this fails, every power expectation below is derived from a misreading of
    the formula rather than from the module being wrong.
    """
    assert round(_required_n(0.95)) == 108
    assert round(_required_n(0.80)) == 229


# --------------------------------------------------------------------------- #
# Builders: judged artifacts, completion by completion.
# --------------------------------------------------------------------------- #


def _item_ids(count: int, prefix: str = "q") -> list[str]:
    return [f"{prefix}{index:03d}" for index in range(count)]


def _passes(item_ids: Sequence[str], k: int) -> dict[str, int]:
    """Every item passing ``k`` of its draws."""
    return {item_id: k for item_id in item_ids}


def _artifact(
    model_id: str,
    per_judge: Mapping[str, Mapping[str, int]],
    *,
    n: int = 5,
    imputed: frozenset[str] | tuple[str, ...] = (),
    goldenset_hash: str = GOLDENSET_HASH,
    judges_hash: str = JUDGES_HASH,
    source: str = "",
) -> JudgedArtifact:
    """A judged artifact with ``k`` of ``n`` draws passing per item.

    A passing draw scores ``SCORE_MAX``; a failing one scores ``SCORE_MIN``.
    Items named in ``imputed`` fail because the *completion* failed, so their
    records carry ``imputed=True`` and an error string, exactly as
    ``judging._grade`` writes them for a completion with no output. That is the
    only difference between the crashing candidate and the badly-answering one in
    the headline regression below.
    """
    records: list[JudgeRecord] = []
    for judge, passes in per_judge.items():
        for item_id, k in passes.items():
            for index in range(n):
                ok = index < k
                is_imputed = (not ok) and item_id in imputed
                records.append(
                    JudgeRecord(
                        judge=judge,
                        item_id=item_id,
                        sample_index=index,
                        passed=ok,
                        score=SCORE_MAX if ok else SCORE_MIN,
                        imputed=is_imputed,
                        error="completion failed: timeout after 30s" if is_imputed else None,
                    )
                )
    return JudgedArtifact(
        model_id=model_id,
        goldenset_hash=goldenset_hash,
        judges_hash=judges_hash,
        n_per_item=n,
        records=tuple(records),
        judges=tuple(
            {
                "name": judge,
                "model": JUDGE_MODEL,
                "adapter_class": "FakeAdapter",
                "rubric_hash": RUBRIC_HASH,
            }
            for judge in per_judge
        ),
        source=source,
    )


def _without(artifact: JudgedArtifact, predicate: Any) -> JudgedArtifact:
    """The same artifact with some records removed -- a truncated run on disk."""
    kept = tuple(record for record in artifact.records if not predicate(record))
    return dataclasses.replace(artifact, records=kept)


def _write_run(path: Any, model_id: str, durations: Mapping[str, Sequence[float]]) -> RunArtifact:
    """A run artifact on disk in runner.py's format, with chosen durations."""
    n = max(len(values) for values in durations.values())
    header = RunHeader(
        model_id=model_id,
        goldenset_hash=GOLDENSET_HASH,
        goldenset_path="goldenset.jsonl",
        n_per_item=n,
        created=utc_now(),
        adapter="FakeAdapter",
        notes={"goldenset_items": len(durations)},
    )
    lines: list[Mapping[str, Any]] = [{"record": "header", **header.to_dict()}]
    for item_id, values in durations.items():
        for index, value in enumerate(values):
            completion = Completion(
                item_id=item_id, sample_index=index, output="an answer", duration=value
            )
            lines.append({"record": "completion", **completion.to_dict()})
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )
    return RunArtifact.load(path)


# --------------------------------------------------------------------------- #
# Accessors. These adapt to *names*, never to values.
# --------------------------------------------------------------------------- #


def _module() -> Any:
    if _comparison is None:
        raise AssertionError(
            f"model_migration_kit.comparison could not be imported: {_IMPORT_ERROR!r}"
        )
    return _comparison


def _surface(obj: Any) -> list[str]:
    if isinstance(obj, Mapping):
        return sorted(str(key) for key in obj)
    return sorted(name for name in dir(obj) if not name.startswith("_"))


def _get(obj: Any, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    if default is not _MISSING:
        return default
    raise AssertionError(
        f"the contract requires one of {list(names)} on {type(obj).__name__}; "
        f"it exposes {_surface(obj)}"
    )


def _compare(baseline: JudgedArtifact, candidate: JudgedArtifact, **kwargs: Any) -> Any:
    """``compare(baseline, candidate, *, thresholds, ...)`` per session-2 §2.

    Run artifacts are passed only if the implementation declares a parameter for
    them: the contract fixes the latency source (``Completion.duration``) but not
    whether ``compare`` is handed the run artifacts or loads them from the judged
    artifact's recorded ``source``.
    """
    compare = _get(_module(), "compare")
    kwargs.setdefault("thresholds", DEFAULTS)
    runs = kwargs.pop("runs", None)
    if runs is not None:
        declared = inspect.signature(compare).parameters
        for value, names in (
            (runs[0], ("baseline_run", "baseline_artifact", "baseline_completions")),
            (runs[1], ("candidate_run", "candidate_artifact", "candidate_completions")),
        ):
            for name in names:
                if name in declared:
                    kwargs[name] = value
                    break
    return compare(baseline, candidate, **kwargs)


def _verdict(report: Any) -> str:
    verdict = _get(report, "verdict")
    assert verdict in VERDICTS, f"{verdict!r} is not one of {sorted(VERDICTS)}"
    return verdict


def _judge_row(report: Any, judge: str = J) -> Any:
    rows = _get(report, "judges", "judge_rows", "rows", "per_judge")
    if isinstance(rows, Mapping):
        assert judge in rows, f"no row for judge {judge!r} in {sorted(rows)}"
        return rows[judge]
    for row in rows:
        if _get(row, "name", "judge", default=None) == judge:
            return row
    raise AssertionError(f"no row for judge {judge!r} among {[_surface(r) for r in rows]}")


def _side(row: Any, side: str) -> Any:
    return _get(row, side, f"{side}_stats", f"{side}_rate")


def _p_value(row: Any) -> float:
    regression = _get(row, "regression", "regression_report", default=None)
    if regression is not None:
        found = _get(regression, "p_value", "p", default=None)
        if found is not None:
            return float(found)
    return float(_get(row, "p_value", "p", "pvalue"))


def _rows_ids(rows: Any) -> list[str]:
    out: list[str] = []
    for row in rows:
        out.append(row if isinstance(row, str) else _get(row, "item_id", "id", "item"))
    return out


def _list_ids(report: Any, *names: str) -> list[str]:
    return _rows_ids(_get(report, *names))


def _imputed(report: Any, row: Any, side: str) -> int:
    """How many of ``side``'s completions were imputed at the score floor."""
    return int(
        _search(
            [_side(row, side), row, report],
            "imputed",
            "n_imputed",
            "imputed_count",
            f"imputed_{side}",
        )
    )


def _required_n_reported(report: Any, row: Any) -> float:
    """The n the report says this judge would need for the configured effect."""
    power = _get(row, "power", "power_estimate", default=None)
    return float(
        _search(
            [power, row, report],
            "required_n",
            "n_required",
            "runs_required",
            "required_runs",
        )
    )


def _mw_powered(row: Any) -> bool:
    power = _get(row, "power", "power_estimate", default=None)
    return bool(_search([row, power], "mw_powered", "powered", "power_ok"))


def _item_counts(report: Any, row: Any, side: str, judge: str = J) -> dict[str, int]:
    """The three item counts for one side: passing, failing, unstable.

    build-plan §6 as amended 2026-08-13: a three-state classification does not
    reduce to one fraction without smuggling the ambiguous items into a bucket,
    so the report publishes counts and never an item-level rate.
    """
    counts = _get(report, "item_counts", default=None)
    per_judge = _get(counts, "per_judge", default=counts) if counts is not None else None
    found: Any = None
    if isinstance(per_judge, Mapping):
        scoped = per_judge.get(judge, per_judge)
        if isinstance(scoped, Mapping) and side in scoped:
            found = scoped[side]
    if found is None:
        found = _search([row, _side(row, side)], f"item_counts_{side}", "item_counts")
    return {state: int(_get(found, state)) for state in ("passing", "failing", "unstable")}


def _dictish(obj: Any) -> Mapping[str, Any]:
    """``to_dict()`` where there is one -- the shape the report renders from."""
    method = _get(obj, "to_dict", default=None)
    return method() if callable(method) else {}


def _all_keys(value: Any) -> set[str]:
    """Every key anywhere inside a nested payload."""
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, inner in value.items():
            keys.add(str(key))
            keys |= _all_keys(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            keys |= _all_keys(inner)
    return keys


def _search(sources: Sequence[Any], *names: str) -> Any:
    """First of ``names`` found on any of ``sources``, or a loud failure."""
    for source in sources:
        if source is None:
            continue
        found = _get(source, *names, default=_ABSENT)
        if found is not _ABSENT:
            return found
    raise AssertionError(
        f"the contract requires one of {list(names)} to be reported; looked on "
        f"{[_surface(source) for source in sources if source is not None]}"
    )


# --------------------------------------------------------------------------- #
# The resolution function, located by name and called by shape.
# --------------------------------------------------------------------------- #

_RESOLVER_NAMES = (
    "resolve_verdict",
    "resolve",
    "decide_verdict",
    "verdict_for",
    "resolve_flags",
    "_resolve_verdict",
)

#: Canonical flag names, taken from build-plan §6's own wording: "compute
#: ``regressed``, the floor result from rigor including its ``underpowered``
#: flag, and ``mw_powered``".
_FLAGS = ("regressed", "floor_cleared", "underpowered", "mw_powered")

_FLAG_ALIASES: dict[str, tuple[str, ...]] = {
    "regressed": ("regressed", "regression", "is_regressed", "has_regression"),
    "floor_cleared": ("floor_cleared", "cleared_floor", "floor_ok", "floor_passed"),
    "underpowered": ("underpowered", "floor_underpowered", "rate_underpowered"),
    "mw_powered": ("mw_powered", "powered", "power_ok", "regression_powered"),
}
#: Parameters that are the negation of a canonical flag.
_FLAG_NEGATIONS: dict[str, str] = {
    "floor_missed": "floor_cleared",
    "missed_floor": "floor_cleared",
    "mw_underpowered": "mw_powered",
}


def _flag_dict(**flags: bool) -> dict[str, bool]:
    assert set(flags) == set(_FLAGS), f"expected {_FLAGS}, got {sorted(flags)}"
    return dict(flags)


def _resolver() -> Any:
    module = _module()
    for name in _RESOLVER_NAMES:
        found = getattr(module, name, None)
        if callable(found):
            return found
    raise AssertionError(
        "build-plan §6 requires the verdict resolution to be a separate pure "
        f"function of the per-judge flags, tested directly. model_migration_kit."
        f"comparison exports none of {list(_RESOLVER_NAMES)}; it exposes "
        f"{_surface(module)}"
    )


def _flags_class(module: Any) -> Any:
    """A class in the module whose constructor is exactly the four flags."""
    for name in _surface(module):
        candidate = getattr(module, name)
        if not inspect.isclass(candidate):
            continue
        try:
            params = [
                p.name
                for p in inspect.signature(candidate).parameters.values()
                if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
            ]
        except (TypeError, ValueError):
            continue
        if _map_params(params) is not None:
            return candidate
    return None


def _map_params(params: Sequence[str]) -> dict[str, tuple[str, bool]] | None:
    """Map constructor parameter names onto the four canonical flags.

    Returns ``param -> (canonical, negated)`` when the parameters cover all four
    flags and nothing else, otherwise ``None``.
    """
    mapping: dict[str, tuple[str, bool]] = {}
    for param in params:
        for canonical, aliases in _FLAG_ALIASES.items():
            if param in aliases:
                mapping[param] = (canonical, False)
                break
        else:
            if param in _FLAG_NEGATIONS:
                mapping[param] = (_FLAG_NEGATIONS[param], True)
            else:
                return None
    if {canonical for canonical, _ in mapping.values()} != set(_FLAGS):
        return None
    return mapping


def _build(cls: Any, flags: Mapping[str, bool]) -> Any:
    params = [
        p.name
        for p in inspect.signature(cls).parameters.values()
        if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    mapping = _map_params(params)
    assert mapping is not None
    return cls(
        **{
            param: (not flags[canonical]) if negated else flags[canonical]
            for param, (canonical, negated) in mapping.items()
        }
    )


def _verdict_of(result: Any) -> str:
    if isinstance(result, str):
        assert result in VERDICTS, f"{result!r} is not one of {sorted(VERDICTS)}"
        return result
    if isinstance(result, Mapping) or hasattr(result, "verdict"):
        return _verdict(result)
    if isinstance(result, Sequence):
        for part in result:
            if isinstance(part, str) and part in VERDICTS:
                return part
    raise AssertionError(f"cannot read a verdict out of {result!r}")


def _resolve(*flags: Mapping[str, bool]) -> str:
    """Drive the resolution function on per-judge flags, whatever shape it takes."""
    resolver = _resolver()
    payloads: list[Any] = []
    cls = _flags_class(_module())
    if cls is not None:
        payloads.append(tuple(_build(cls, one) for one in flags))
    payloads.append([dict(one) for one in flags])
    payloads.append(tuple(dict(one) for one in flags))
    attempts: list[str] = []
    for payload in payloads:
        try:
            return _verdict_of(resolver(payload))
        except TypeError as exc:
            attempts.append(f"{type(payload).__name__} of {type(payload[0]).__name__}: {exc}")
    if len(flags) == 1:
        try:
            return _verdict_of(resolver(**flags[0]))
        except TypeError as exc:
            attempts.append(f"keywords {sorted(flags[0])}: {exc}")
    raise AssertionError(
        f"{resolver.__name__} rejected every call shape tried: " + "; ".join(attempts)
    )


def _expected(flags: Mapping[str, bool]) -> str:
    """build-plan §6's precedence chain, transcribed for one judge.

    (1) regressed -> NO-GO; (2) floor missed and not underpowered -> NO-GO;
    (3) floor missed while underpowered -> REVIEW; (4) not mw_powered -> REVIEW;
    (5) GO.
    """
    if flags["regressed"]:
        return Verdict.NO_GO
    if not flags["floor_cleared"] and not flags["underpowered"]:
        return Verdict.NO_GO
    if not flags["floor_cleared"] and flags["underpowered"]:
        return Verdict.REVIEW
    if not flags["mw_powered"]:
        return Verdict.REVIEW
    return Verdict.GO


def _all_flag_tuples() -> list[dict[str, bool]]:
    out: list[dict[str, bool]] = []
    for bits in range(16):
        out.append(
            _flag_dict(
                regressed=bool(bits & 1),
                floor_cleared=bool(bits & 2),
                underpowered=bool(bits & 4),
                mw_powered=bool(bits & 8),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 1. The headline regression: a crasher must not beat a bad answerer.
# --------------------------------------------------------------------------- #

ITEMS_40 = _item_ids(40)


def _headline_candidate(model_id: str, *, imputed: bool) -> JudgedArtifact:
    passes = _passes(ITEMS_40, 5)
    passes["q000"] = 0
    passes["q001"] = 0
    return _artifact(
        model_id,
        {J: passes},
        imputed=("q000", "q001") if imputed else (),
    )


def test_crashing_and_badly_answering_candidates_do_not_get_opposite_verdicts() -> None:
    """The regression this whole amendment exists for (build-plan §6, review §1).

    40 items x n=5, floor 0.90, baseline 5s throughout. One candidate times out on
    two items, the other answers those same two items badly. Both post 190/200
    passed, both have Wilson lower bound 0.9181081670817905, both clear the floor.
    Before the fix the crasher's ten missing scores vanished from the Mann-Whitney
    array, giving p=1.0 and GO, while the bad answerer scored p=0.00069 and
    NO-GO -- a tool that prefers a model which crashes to one which answers badly.
    With failures imputed at SCORE_MIN both sides feed identical arrays.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    crasher = _headline_candidate("model-crash-20260101", imputed=True)
    bad_answerer = _headline_candidate("model-bad-20260101", imputed=False)

    crash_report = _compare(baseline, crasher)
    bad_report = _compare(baseline, bad_answerer)

    assert _verdict(crash_report) == _verdict(bad_report)
    # Both arrays are 190 fives and ten ones against 200 fives: p = 0.00069 < 0.05
    # at k=1 judge, so Holm changes nothing and rule 1 fires.
    assert _verdict(crash_report) == Verdict.NO_GO
    assert _p_value(_judge_row(crash_report)) == pytest.approx(0.0006944255237317142, rel=1e-6)
    assert _p_value(_judge_row(bad_report)) == pytest.approx(0.0006944255237317142, rel=1e-6)


def test_both_headline_candidates_post_the_same_pass_rate() -> None:
    """The premise of the headline test, asserted rather than assumed.

    If these two did not post identical counts, the test above would prove
    nothing. 190 of 200, observed 0.95, one-sided Wilson lower bound
    0.9181081670817905 from ``opik_rigor.assert_pass_rate``.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    for candidate in (
        _headline_candidate("model-crash-20260101", imputed=True),
        _headline_candidate("model-bad-20260101", imputed=False),
    ):
        row = _judge_row(_compare(baseline, candidate))
        side = _side(row, "candidate")
        assert _get(side, "successes", "passes", "passed") == 190
        assert _get(side, "n", "total") == 200
        assert _get(side, "rate", "pass_rate") == pytest.approx(0.95)
        assert _get(side, "lower_bound", "wilson_lower_bound") == pytest.approx(
            0.9181081670817905, rel=1e-9
        )


def test_imputed_completions_are_counted_and_reported() -> None:
    """"The count of imputed scores is reported" -- build-plan §6.

    Ten of the crasher's 200 completions are imputed; none of the bad answerer's
    are, though their scores are identical.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    crash_report = _compare(baseline, _headline_candidate("model-crash-20260101", imputed=True))
    bad_report = _compare(baseline, _headline_candidate("model-bad-20260101", imputed=False))

    assert _imputed(crash_report, _judge_row(crash_report), "candidate") == 10
    assert _imputed(bad_report, _judge_row(bad_report), "candidate") == 0


# --------------------------------------------------------------------------- #
# 2. The resolution, as a total function of its flags.
# --------------------------------------------------------------------------- #


def _flag_id(flags: Mapping[str, bool]) -> str:
    return "-".join(f"{name}={int(value)}" for name, value in flags.items())


@pytest.mark.parametrize("flags", _all_flag_tuples(), ids=_flag_id)
def test_resolution_is_total_over_every_flag_combination(flags: dict[str, bool]) -> None:
    """All 16 tuples of (regressed, floor_cleared, underpowered, mw_powered).

    Table-tested directly rather than through end-to-end runs, because
    combinations unreachable through the current statistics -- two of the draft's
    eight rows were, per review §8 -- still have to resolve correctly when a later
    change makes them reachable. ``floor_cleared and underpowered`` is exactly
    such a row: rigor only ever sets ``underpowered`` on a failed gate.
    """
    assert _resolve(flags) == _expected(flags)


def test_no_go_outranks_review() -> None:
    """"NO-GO outranks REVIEW because a regression that reached significance was,
    for that question, powered enough" -- build-plan §6."""
    regressed = _flag_dict(
        regressed=True, floor_cleared=True, underpowered=False, mw_powered=False
    )
    review_only = _flag_dict(
        regressed=False, floor_cleared=False, underpowered=True, mw_powered=False
    )
    assert _resolve(regressed, review_only) == Verdict.NO_GO
    assert _resolve(review_only, regressed) == Verdict.NO_GO


def test_no_go_outranks_review_when_the_floor_was_missed_outright() -> None:
    """Rule 2 (floor missed, not underpowered) also outranks any REVIEW."""
    hard_fail = _flag_dict(
        regressed=False, floor_cleared=False, underpowered=False, mw_powered=True
    )
    underpowered = _flag_dict(
        regressed=False, floor_cleared=True, underpowered=False, mw_powered=False
    )
    assert _resolve(hard_fail, underpowered) == Verdict.NO_GO
    assert _resolve(underpowered, hard_fail) == Verdict.NO_GO


def test_review_outranks_go() -> None:
    """Invariant 5: "we cannot tell" is never converted into "ship it"."""
    clean = _flag_dict(regressed=False, floor_cleared=True, underpowered=False, mw_powered=True)
    unpowered = _flag_dict(
        regressed=False, floor_cleared=True, underpowered=False, mw_powered=False
    )
    assert _resolve(clean) == Verdict.GO
    assert _resolve(clean, unpowered) == Verdict.REVIEW
    assert _resolve(unpowered, clean) == Verdict.REVIEW


def test_there_is_no_path_from_cannot_tell_to_go() -> None:
    """Over every pair of judges: GO requires every judge clean and powered.

    256 combinations, including the unreachable ones. A GO returned for any judge
    that missed the floor or could not have detected the effect is invariant 5
    defeated.
    """
    tuples = _all_flag_tuples()
    for first in tuples:
        for second in tuples:
            verdict = _resolve(first, second)
            cannot_tell = any(
                not one["floor_cleared"] or not one["mw_powered"] for one in (first, second)
            )
            if cannot_tell:
                assert verdict != Verdict.GO, f"GO from {first} + {second}"
            if verdict == Verdict.GO:
                assert all(
                    not one["regressed"] and one["floor_cleared"] and one["mw_powered"]
                    for one in (first, second)
                )


# --------------------------------------------------------------------------- #
# 3. Multiplicity: Holm-Bonferroni across the judge family.
# --------------------------------------------------------------------------- #


def test_holm_bonferroni_is_applied_across_four_judges() -> None:
    """Review §4: uncorrected, four judges gave a false NO-GO on ~9% of runs.

    Four judges over 40 items x n=5. Three see nothing (identical arrays, p=1.0);
    the fourth sees four low scores out of 200, which is
    p = 0.022478743471552588 -- below the raw alpha of 0.05, so an uncorrected
    rule calls it a regression and returns NO-GO, and above Holm's first
    threshold alpha/4 = 0.0125, so the corrected rule does not reject and the
    verdict stands at GO. Everything else clears: 196/200 has lower bound
    0.9561965428329557 against the 0.90 floor, and 200 completions exceeds the
    required 55.64 for a ten-point drop off a baseline of 1.0.
    """
    judges = ("helpfulness", "accuracy", "tone", "safety")
    baseline = _artifact(BASELINE_MODEL, {judge: _passes(ITEMS_40, 5) for judge in judges})
    weakened = _passes(ITEMS_40, 5)
    for item_id in ("q000", "q001", "q002", "q003"):
        weakened[item_id] = 4
    candidate = _artifact(
        CANDIDATE_MODEL,
        {judge: (weakened if judge == "safety" else _passes(ITEMS_40, 5)) for judge in judges},
    )

    report = _compare(baseline, candidate)
    row = _judge_row(report, "safety")

    assert _p_value(row) == pytest.approx(0.022478743471552588, rel=1e-6)
    assert _p_value(row) < DEFAULTS.alpha  # the uncorrected decision would reject
    assert _get(row, "regressed") is False  # the corrected one does not
    assert _verdict(report) == Verdict.GO


def test_holm_still_rejects_a_p_value_below_the_corrected_threshold() -> None:
    """The other side of the correction: Holm must not become a rubber stamp.

    Same four-judge family, but the fourth judge sees ten low scores out of 200:
    p = 0.0006944255237317142, well under alpha/4 = 0.0125, so it is rejected and
    the verdict is NO-GO.
    """
    judges = ("helpfulness", "accuracy", "tone", "safety")
    baseline = _artifact(BASELINE_MODEL, {judge: _passes(ITEMS_40, 5) for judge in judges})
    weakened = _passes(ITEMS_40, 5)
    for item_id in ITEMS_40[:2]:
        weakened[item_id] = 0
    candidate = _artifact(
        CANDIDATE_MODEL,
        {judge: (weakened if judge == "safety" else _passes(ITEMS_40, 5)) for judge in judges},
    )

    report = _compare(baseline, candidate)
    assert _get(_judge_row(report, "safety"), "regressed") is True
    assert _verdict(report) == Verdict.NO_GO


# --------------------------------------------------------------------------- #
# 4. Guards, before any statistic.
# --------------------------------------------------------------------------- #


def test_mismatched_goldenset_hash_is_an_artifact_error() -> None:
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    candidate = _artifact(
        CANDIDATE_MODEL, {J: _passes(ITEMS_40, 5)}, goldenset_hash=OTHER_GOLDENSET_HASH
    )
    with pytest.raises(ArtifactError):
        _compare(baseline, candidate)


def test_mismatched_judges_hash_is_a_judge_config_error() -> None:
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    candidate = _artifact(
        CANDIDATE_MODEL, {J: _passes(ITEMS_40, 5)}, judges_hash=OTHER_JUDGES_HASH
    )
    with pytest.raises(JudgeConfigError):
        _compare(baseline, candidate)


def test_a_baseline_covering_fewer_items_is_an_artifact_error() -> None:
    """Review §9: a truncated baseline passes both hash checks.

    Invariant 2 guarantees truncated artifacts exist -- a crashed run leaves one.
    Compared unguarded, this is 150 values against 200 with ten items silently
    dropped from the flip analysis.
    """
    baseline = _without(
        _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)}),
        lambda record: record.item_id in set(ITEMS_40[30:]),
    )
    candidate = _artifact(CANDIDATE_MODEL, {J: _passes(ITEMS_40, 5)})
    with pytest.raises(ArtifactError):
        _compare(baseline, candidate)


def test_a_baseline_covering_fewer_samples_per_item_is_an_artifact_error() -> None:
    """Same items on both sides, but one item was drawn four times, not five."""
    baseline = _without(
        _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)}),
        lambda record: record.item_id == "q007" and record.sample_index == 4,
    )
    candidate = _artifact(CANDIDATE_MODEL, {J: _passes(ITEMS_40, 5)})
    with pytest.raises(ArtifactError):
        _compare(baseline, candidate)


def test_comparing_a_model_against_itself_is_refused_by_default() -> None:
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    candidate = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    with pytest.raises(ArtifactError):
        _compare(baseline, candidate)


def test_identical_models_produce_go_under_the_calibration_flag() -> None:
    """The A/A calibration the same-model guard exists to permit (§2, review D5).

    Both sides 200/200: lower bound 0.9866528393452243 clears the 0.90 floor,
    identical score arrays give p=1.0, and 200 completions exceeds the 55.64
    required for a ten-point drop off a baseline rate of 1.0. GO, with no false
    alarm.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    candidate = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    report = _compare(baseline, candidate, allow_same_model=True)
    assert _verdict(report) == Verdict.GO


# --------------------------------------------------------------------------- #
# 5. Flips, gains, and the items that are merely unstable.
# --------------------------------------------------------------------------- #

#: One item per behaviour, n=5. Pass at >=80% (4 or 5 of 5), fail at <=20%
#: (0 or 1 of 5), unstable in between -- build-plan §6 and review §5(d).
FLIP_ITEMS = {
    "flip-hard": (5, 0),
    "flip-margin": (4, 1),
    "gain-hard": (0, 5),
    "gain-margin": (1, 4),
    "borderline": (3, 3),
    "worse-but-unstable": (5, 2),
    "stable-pass": (5, 5),
}


def _flip_pair() -> tuple[JudgedArtifact, JudgedArtifact]:
    baseline = _artifact(
        BASELINE_MODEL, {J: {item: pair[0] for item, pair in FLIP_ITEMS.items()}}
    )
    candidate = _artifact(
        CANDIDATE_MODEL, {J: {item: pair[1] for item, pair in FLIP_ITEMS.items()}}
    )
    return baseline, candidate


def test_flip_and_gain_lists_match_the_constructed_cases_exactly() -> None:
    """Flips require a margin, not a majority.

    ``flip-margin`` sits exactly on both boundaries (4/5 = 0.80 and 1/5 = 0.20)
    and counts, because the contract says an item passes *at* >=80% and fails
    *at* <=20%. ``worse-but-unstable`` drops from 5/5 to 2/5 -- a real
    deterioration, but 0.40 is not failing, so it is named rather than counted as
    a flip. Gains never offset flips.
    """
    report = _compare(*_flip_pair())
    assert sorted(_list_ids(report, "flips")) == ["flip-hard", "flip-margin"]
    assert sorted(_list_ids(report, "gains")) == ["gain-hard", "gain-margin"]
    assert sorted(_list_ids(report, "unstable", "unstable_items")) == [
        "borderline",
        "worse-but-unstable",
    ]


def test_a_borderline_item_is_unstable_not_a_flip() -> None:
    """Review §5: majority-vote flipping manufactured ~5 spurious flips per run.

    An item genuinely 50/50 under both models appeared as a flip with probability
    0.2544 and as a gain with probability 0.2460, giving a different list on every
    rerun. Here it is 3/5 against 3/5 -- no movement at all -- and it must land in
    ``unstable``.
    """
    report = _compare(*_flip_pair())
    assert "borderline" in _list_ids(report, "unstable", "unstable_items")
    assert "borderline" not in _list_ids(report, "flips")
    assert "borderline" not in _list_ids(report, "gains")


def _three_fifths_pair() -> tuple[JudgedArtifact, JudgedArtifact]:
    """Review §5(b)'s constructible case: ten items each passing 3 of 5."""
    items = _item_ids(10, prefix="b")
    return (
        _artifact(BASELINE_MODEL, {J: _passes(items, 3)}),
        _artifact(CANDIDATE_MODEL, {J: _passes(items, 3)}),
    )


def test_item_counts_and_the_completion_rate_are_both_reported() -> None:
    """The constructible case, as build-plan §6 reads after the 2026-08-13 amendment.

    Ten items each passing 3/5 give a pooled completion rate of 0.60 -- NO-GO
    territory against a 0.90 floor -- an empty flip list, an empty gain list, and
    ten unstable items. Both units are printed side by side, because a reader
    given only one will assume it answers both, and the three item counts are what
    tells this reader the truth: this migration cannot be judged from this
    evidence at this n.
    """
    report = _compare(*_three_fifths_pair())
    row = _judge_row(report)

    assert _list_ids(report, "flips") == []
    assert _list_ids(report, "gains") == []
    assert sorted(_list_ids(report, "unstable", "unstable_items")) == _item_ids(10, prefix="b")

    # The completion-level unit.
    assert float(_get(_side(row, "candidate"), "rate", "pass_rate")) == pytest.approx(0.60)
    # The item-level unit, as three counts rather than one fraction.
    expected = {"passing": 0, "failing": 0, "unstable": 10}
    assert _item_counts(report, row, "candidate") == expected
    assert _item_counts(report, row, "baseline") == expected


def test_no_item_level_rate_is_reported_anywhere() -> None:
    """The amendment forbids the number, not just this reading of it.

    "There is no single item-level *rate*, because a three-state classification
    does not reduce to one fraction without smuggling the ambiguous items into one
    bucket or the other." Re-introducing one anywhere -- on the judge row, in the
    dict the report renders from, or in the evidence payload -- is the regression
    the amendment exists to prevent, so every surface is checked rather than the
    one that happened to carry it before.
    """
    forbidden = {
        "item_rate",
        "item_rates",
        "item_pass_rate",
        "item_level_rate",
        "item_pass_rate_baseline",
        "item_pass_rate_candidate",
        "item_rate_baseline",
        "item_rate_candidate",
    }
    report = _compare(*_three_fifths_pair())
    row = _judge_row(report)
    payload = _get(report, "comparison_payload", default=None)
    if callable(payload):
        payload = payload()

    surfaces = (
        set(_surface(report))
        | set(_surface(row))
        | _all_keys(_dictish(report))
        | _all_keys(_dictish(row))
        | _all_keys(payload if isinstance(payload, Mapping) else {})
    )
    assert not surfaces & forbidden, f"item-level rate reported as {sorted(surfaces & forbidden)}"


# --------------------------------------------------------------------------- #
# 6. Power: a question that could not be asked is never answered with GO.
# --------------------------------------------------------------------------- #


def test_a_sample_too_small_for_the_effect_is_review_and_carries_the_required_n() -> None:
    """Ten items at n=5 -- the contract's own worked example.

    Both sides 50/50: the floor is cleared (lower bound 0.9486668142137298) and
    there is no regression to find, so nothing except power can decide this. At a
    baseline rate of 1.0 the required n for a ten-point drop at 80% power is
    55.643015 per side; 50 completions is short of it, so the verdict is REVIEW
    and the report carries the number. build-plan §6: "a ten-item golden set at
    n=5 will usually say REVIEW rather than GO".
    """
    items = _item_ids(10, prefix="s")
    baseline = _artifact(BASELINE_MODEL, {J: _passes(items, 5)})
    candidate = _artifact(CANDIDATE_MODEL, {J: _passes(items, 5)})
    report = _compare(baseline, candidate)
    row = _judge_row(report)

    assert _verdict(report) == Verdict.REVIEW
    # The contract fixes the formula, not a rounding rule: 55.643015 -> 56.
    assert _required_n_reported(report, row) == pytest.approx(_required_n(1.0), abs=1.0)
    assert _mw_powered(row) is False


def test_an_adequate_sample_is_powered_and_reports_the_required_n() -> None:
    """40 items at n=5, both sides at exactly 0.95: 190/200 with ten 4-of-5 items.

    Required n at a baseline rate of 0.95 is 108.194752 per side, which 200
    clears, so power stops being the binding constraint and the verdict is GO.
    Identical arrays give p = 0.5004570456994857, and the lower bound
    0.9181081670817905 clears the floor.
    """
    passes = _passes(ITEMS_40, 5)
    for item_id in ITEMS_40[:10]:
        passes[item_id] = 4
    baseline = _artifact(BASELINE_MODEL, {J: passes})
    candidate = _artifact(CANDIDATE_MODEL, {J: dict(passes)})
    report = _compare(baseline, candidate)
    row = _judge_row(report)

    assert _required_n_reported(report, row) == pytest.approx(_required_n(0.95), abs=1.0)
    assert _mw_powered(row) is True
    assert _verdict(report) == Verdict.GO


# --------------------------------------------------------------------------- #
# 7. The floor clause defers to rigor's own verdict on power.
# --------------------------------------------------------------------------- #


def test_a_floor_miss_on_an_underpowered_sample_is_review_not_no_go() -> None:
    """Review §3, verbatim numbers: 38/40 against a 0.90 floor.

    Lower bound 0.8596681784340271, so the floor is missed; but observed 0.9500
    clears it, which is rigor's definition of an underpowered sample, and
    ``assert_pass_rate`` reports ``underpowered=True`` with ``runs_needed=113``
    and says in its own message that this is "not a demonstrated failure". The
    draft called it NO-GO and told the user to fix the model; the correct advice
    is "collect 113 completions". Two low scores out of 40 gives
    p = 0.07994233688696428, so rule 1 does not fire.
    """
    items = _item_ids(8, prefix="u")
    passes = _passes(items, 5)
    passes["u000"] = 4
    passes["u001"] = 4
    baseline = _artifact(BASELINE_MODEL, {J: _passes(items, 5)})
    candidate = _artifact(CANDIDATE_MODEL, {J: passes})
    report = _compare(baseline, candidate)
    row = _judge_row(report)

    assert _verdict(report) != Verdict.NO_GO
    assert _verdict(report) == Verdict.REVIEW
    assert _get(row, "floor_cleared", "cleared_floor") is False
    assert _get(row, "underpowered", "floor_underpowered") is True
    needed = _search(
        [_side(row, "candidate"), row, report], "runs_needed", "runs_required", "needed_runs"
    )
    assert int(needed) == 113


def test_a_model_exactly_at_the_floor_is_not_no_go() -> None:
    """Review §3: at observed == min_rate the bound never reaches the floor.

    36/40 is exactly 0.90 against a 0.90 floor: lower bound 0.7950094153296186,
    and no sample size ever clears it, so a rule that called this NO-GO would call
    it NO-GO forever. rigor reports ``underpowered=True`` with no achievable
    ``runs_needed``, and rule 3 turns that into REVIEW. Both sides carry the same
    counts, so there is no regression to confound the reading.
    """
    items = _item_ids(8, prefix="f")
    passes = _passes(items, 5)
    for item_id in ("f000", "f001", "f002", "f003"):
        passes[item_id] = 4
    baseline = _artifact(BASELINE_MODEL, {J: passes})
    candidate = _artifact(CANDIDATE_MODEL, {J: dict(passes)})
    report = _compare(baseline, candidate)

    assert _verdict(report) != Verdict.NO_GO
    assert _verdict(report) == Verdict.REVIEW
    assert _get(_judge_row(report), "underpowered", "floor_underpowered") is True


# --------------------------------------------------------------------------- #
# 8. Latency: descriptive, never a gate.
# --------------------------------------------------------------------------- #


def test_latency_median_and_p90_come_out_right(tmp_path) -> None:
    """Hand-constructed durations, chosen so no p90 convention can disagree.

    Baseline: ten draws at 1.0s and ten at 2.0s -> median 1.5, p90 2.0. Candidate:
    ten at 3.0s and ten at 5.0s -> median 4.0, p90 5.0. Stdlib ``quantiles``
    exclusive and inclusive and plain nearest-rank all return the same p90 on
    these, so the assertion is about the statistic and not about a tie-break.
    """
    items = _item_ids(4, prefix="l")
    baseline_durations = {
        items[0]: [1.0] * 5,
        items[1]: [1.0] * 5,
        items[2]: [2.0] * 5,
        items[3]: [2.0] * 5,
    }
    candidate_durations = {
        items[0]: [3.0] * 5,
        items[1]: [3.0] * 5,
        items[2]: [5.0] * 5,
        items[3]: [5.0] * 5,
    }
    baseline_run = _write_run(tmp_path / "baseline.jsonl", BASELINE_MODEL, baseline_durations)
    candidate_run = _write_run(tmp_path / "candidate.jsonl", CANDIDATE_MODEL, candidate_durations)

    baseline = _artifact(
        BASELINE_MODEL, {J: _passes(items, 5)}, source=str(baseline_run.path)
    )
    candidate = _artifact(
        CANDIDATE_MODEL, {J: _passes(items, 5)}, source=str(candidate_run.path)
    )
    report = _compare(baseline, candidate, runs=(baseline_run, candidate_run))

    latency = _get(report, "latency", "latencies")
    for side, expected_median, expected_p90 in (
        ("baseline", 1.5, 2.0),
        ("candidate", 4.0, 5.0),
    ):
        stats = _get(latency, side)
        assert float(_get(stats, "median", "p50")) == pytest.approx(expected_median)
        assert float(_get(stats, "p90", "p_90")) == pytest.approx(expected_p90)


def test_the_verdict_maps_onto_the_documented_exit_code() -> None:
    """Invariant 7: exit codes 0/1/2/3 are the CI contract.

    Asserted through ``contracts.Verdict`` rather than a literal, since that
    mapping is the frozen half.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _passes(ITEMS_40, 5)})
    candidate = _headline_candidate("model-bad-20260101", imputed=False)
    report = _compare(baseline, candidate)
    assert Verdict.exit_code(_verdict(report)) == 1
