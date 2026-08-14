"""Comparing two judged artifacts, and resolving the verdict they imply.

This is the analytical core, and it is deliberately thin: every statistic comes
from opik-rigor. What lives here is the arrangement -- which numbers are fed to
which gate, in which order, and how the resulting flags become GO / NO-GO /
REVIEW. The arrangement is where a tool like this gets migration decisions
backwards, so each rule below is written out with the measurement that forced it.
The authority is ``docs/build-plan.md`` §6 (Amendment 1); the numbers behind it
are in ``docs/session-2-verdict-review.md``.

Six rules are load-bearing.

**Failed completions are already imputed at the judge's minimum score by
``judging.py``, and this module must not undo that.** Scores are read as they
come, ``None`` is never passed to rigor, and a record is never dropped for having
failed. Measured consequence of dropping them, at 40 items x n=5 against a 0.90
floor: a candidate that times out on two items and a candidate that answers those
same two items badly both post 190/200 passed, but the crasher's ten missing
scores leave the Mann-Whitney array with p=1.0 (GO) while the bad answerer scores
p=0.00069 (NO-GO). A tool that prefers the model which crashes to the one which
answers poorly is worse than no tool.

**Parse failures are not model failures.** A record with ``parse_failure`` set is
the *judge* having been unintelligible, so it leaves both the numerator and the
denominator of the pass rate. An imputed record -- a completion that failed --
stays in both. Conflating the two would let an unreliable judge read as an
unreliable model.

**The floor clause defers to rigor's own verdict on power.** ``assert_pass_rate``
reports ``underpowered`` and ``runs_needed`` on its exception, and this module
consumes them rather than deriving a weaker version. On 38/40 against a 0.90
floor, the derived version says NO-GO where rigor says "underpowered, roughly 113
runs would clear the bar" -- opposite advice on the same input, and the derived
one blocks a migration that is probably fine.

**"Underpowered" also means the regression test could not have seen the drop being
asked about.** Simulated Mann-Whitney power at n=25 per side is 33.9% against a
0.95 -> 0.85 drop and 16.6% against 0.95 -> 0.90; roughly 200 completions a side
buy 80% power on a ten-point drop. Reporting "no regression detected" from 25
completions is a question never asked, reported as answered, so a sample that
cannot reach ``power_target`` for ``min_detectable_effect`` yields REVIEW and
never GO.

**Regression tests across judges are corrected for multiplicity.** Uncorrected, on
two *identical* models over 3000 trials, the false NO-GO rate climbs 2.10% (one
judge) -> 4.73% -> 6.40% -> 9.07% (four judges). Holm-Bonferroni over the family
of judges is applied to the p-values before any of them meets alpha.

**An item flips only when it crosses a margin.** Majority-of-five manufactures
flips: an item genuinely 50/50 under both models is reported as a flip with
probability 0.2544 and as a gain with probability 0.2460, so twenty borderline
items yield about five spurious flips per run and a different list every rerun.
An item passes at >=80% of its draws, fails at <=20%, and is *unstable* between --
named in its own list rather than counted as evidence.

The verdict resolution itself is :func:`resolve_verdict`, a pure function of the
per-judge flags with no statistics anywhere near it, so the precedence table can
be tested as a total function over combinations the current statistics cannot
even reach.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import NormalDist
from statistics import median as _median
from typing import Any

from opik_rigor import (
    EvidenceLog,
    PassRateError,
    RegressionError,
    assert_no_regression,
    assert_pass_rate,
    wilson_interval,
)

from .contracts import EVENT_COMPARISON, EVENT_VERDICT, Verdict, utc_now
from .errors import ArtifactError, DependencyContractError, JudgeConfigError
from .judging import JudgedArtifact, JudgeRecord, Thresholds
from .runner import RunArtifact

__all__ = [
    "ComparisonReport",
    "ItemChange",
    "JudgeComparison",
    "JudgeFlags",
    "LatencyStat",
    "PowerEstimate",
    "VerdictDecision",
    "compare",
    "explain_verdict",
    "holm_bonferroni",
    "item_state",
    "required_sample_size",
    "resolve_verdict",
]

#: An item is called *passing* at or above this fraction of its draws and
#: *failing* at or below its complement; between the two it is unstable and is
#: named rather than counted. Majority-of-n was measured manufacturing roughly
#: five flips and five gains per run out of twenty genuinely 50/50 items, with a
#: different list on every rerun -- see the module docstring.
PASS_MARGIN = 0.80
FAIL_MARGIN = 0.20

#: The three states an item can be in under one judge.
STATE_PASS = "pass"
STATE_FAIL = "fail"
STATE_UNSTABLE = "unstable"

#: What the regression test actually ran on, echoed into the report row. A reader
#: who is told "no regression" is entitled to know whether that was measured on a
#: 1-5 ordinal scale or on pass/fail outcomes, which carry far less information.
TEST_SCORES = "mann-whitney-u"
TEST_OUTCOMES = "mann-whitney-u-on-outcomes"
TEST_NOT_RUN = "not-run"


# --------------------------------------------------------------------------- #
# small pure helpers -- each one is independently table-testable
# --------------------------------------------------------------------------- #


def item_state(passes: int, n: int) -> str:
    """Classify one item under one judge from its ``passes`` of ``n`` draws.

    ``pass`` at ``passes >= ceil(0.8n)``, ``fail`` at ``passes <= floor(0.2n)``,
    ``unstable`` in between. The margin is the whole point: a strict majority
    turns per-item noise into a binary event and produces a flip list that is
    different on every rerun, which is worthless as the artifact a human reads.
    """
    if n <= 0:
        return STATE_UNSTABLE
    if passes >= math.ceil(PASS_MARGIN * n):
        return STATE_PASS
    if passes <= math.floor(FAIL_MARGIN * n):
        return STATE_FAIL
    return STATE_UNSTABLE


def required_sample_size(
    baseline_rate: float,
    *,
    min_detectable_effect: float,
    power_target: float,
    alpha: float,
) -> int | None:
    """Completions per side needed to see a drop of ``min_detectable_effect``.

    The two-proportion normal approximation::

        n >= (z_alpha + z_beta)**2 * (p1*(1-p1) + p2*(1-p2)) / delta**2

    with ``p2 = p1 - delta``, a one-sided ``z_alpha`` (the regression test is
    one-sided: an improvement is not a regression) and ``z_beta`` at the power
    target. It gives 108-229 per side across plausible baseline rates, which
    tracks the simulated Mann-Whitney figures (~200 for a ten-point drop at
    alpha=0.05).

    **It approximates a different test.** The gate that actually produces
    ``regressed`` is Mann-Whitney U on judge scores, not a two-proportion z-test
    on pass/fail. This is a stand-in with the right order of magnitude, and the
    methodology appendix says so rather than implying the number is exact. It is
    still incomparably better than the alternative it replaced, which measured the
    power of the *pass-rate floor* and certified a run as powered at n=25, where
    the regression test's real power against a ten-point drop is 16.6%.

    ``NormalDist().inv_cdf`` from the stdlib is used deliberately. scipy and numpy
    are both installed here, but only transitively through opik-rigor; importing
    either in this package would be an undeclared dependency that breaks the day
    rigor stops needing it.

    Returns:
        The required n per side, or ``None`` when the effect cannot be defined at
        this baseline rate (``p1 <= delta``, i.e. the drop would take the rate to
        zero or below, so there is no two-proportion comparison to power).
    """
    p1 = float(baseline_rate)
    delta = float(min_detectable_effect)
    if not 0.0 < delta < 1.0:
        raise ValueError(f"min_detectable_effect must be in (0, 1), got {delta!r}")
    if not 0.0 < power_target < 1.0:
        raise ValueError(f"power_target must be in (0, 1), got {power_target!r}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    p2 = p1 - delta
    if p2 < 0.0:
        return None
    normal = NormalDist()
    z_alpha = normal.inv_cdf(1.0 - alpha)
    z_beta = normal.inv_cdf(power_target)
    variance = p1 * (1.0 - p1) + p2 * (1.0 - p2)
    return math.ceil((z_alpha + z_beta) ** 2 * variance / (delta * delta))


def holm_bonferroni(
    p_values: Sequence[float], *, alpha: float
) -> tuple[tuple[bool, float], ...]:
    """Holm-Bonferroni over a family of p-values, in the input's own order.

    Sort ascending; the i-th smallest (1-indexed) is tested against
    ``alpha / (k - i + 1)``; the procedure **steps down**, so once one test fails
    to reject, no larger p-value is rejected either. Dropping the step-down would
    make the family non-monotone -- a judge with a larger p-value rejected while a
    smaller one was not -- which is not the Holm procedure and is not defensible
    as anything else.

    ``alpha`` is the family-wise level across judges, which is the correction's
    entire purpose: at four judges the uncorrected rule flags a regression between
    two identical models in 9.07% of runs, against a nominal 5%.

    Returns:
        One ``(rejected, threshold)`` pair per input position. An empty family
        returns an empty tuple: no judges, no comparisons, and no verdict evidence
        either way.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    k = len(p_values)
    out: list[tuple[bool, float]] = [(False, alpha)] * k
    still_rejecting = True
    for rank, index in enumerate(order):
        threshold = alpha / (k - rank)
        rejected = still_rejecting and float(p_values[index]) < threshold
        if not rejected:
            still_rejecting = False
        out[index] = (rejected, threshold)
    return tuple(out)


# --------------------------------------------------------------------------- #
# the verdict resolution -- pure, total, and testable without any statistics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class JudgeFlags:
    """The four booleans one judge contributes to the verdict.

    Deliberately separated from everything that computed them. The precedence
    table has to be exercised over combinations the statistics cannot currently
    reach -- two of the draft's eight rows were unreachable -- and a table test
    that has to construct a whole ``JudgedArtifact`` to reach a row will simply
    not cover it.
    """

    name: str = "judge"
    #: Holm-adjusted p below the family-wise alpha.
    regressed: bool = False
    #: rigor's ``assert_pass_rate`` gate on the candidate: lower bound >= floor.
    floor_cleared: bool = True
    #: rigor's own ``underpowered`` flag from the failing gate. Meaningful only
    #: when the floor was missed; rigor populates it on that branch alone.
    underpowered: bool = False
    #: The sample reaches ``power_target`` for ``min_detectable_effect``.
    mw_powered: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "regressed": self.regressed,
            "floor_cleared": self.floor_cleared,
            "underpowered": self.underpowered,
            "mw_powered": self.mw_powered,
        }


@dataclass(frozen=True)
class VerdictDecision:
    """Which rule fired, on which judge, and the sentence that says so."""

    verdict: str
    rule: int
    reason: str
    judge: str | None = None

    @property
    def decided_by(self) -> str:
        return f"rule {self.rule}"

    @property
    def exit_code(self) -> int:
        return Verdict.exit_code(self.verdict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "rule": self.rule,
            "judge": self.judge,
        }


_FLAG_ALIASES = {
    "regressed": ("regressed",),
    "floor_cleared": ("floor_cleared",),
    "underpowered": ("underpowered", "floor_underpowered"),
    "mw_powered": ("mw_powered", "powered"),
}


def _flag(row: Any, key: str) -> bool:
    """Read one flag from a row, whether it is an object or a mapping.

    Mappings are accepted so a table test can write its cases as literals rather
    than as constructor calls; the aliases exist so that the two names the
    documents use for rigor's power flag both resolve.
    """
    names = _FLAG_ALIASES[key]
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return bool(row[name])
        raise KeyError(f"verdict row {row!r} has no {key!r} flag")
    for name in names:
        if hasattr(row, name):
            return bool(getattr(row, name))
    raise AttributeError(f"verdict row {row!r} has no {key!r} flag")


def _row_name(row: Any, position: int) -> str:
    if isinstance(row, Mapping):
        return str(row.get("name", f"judge#{position}"))
    return str(getattr(row, "name", f"judge#{position}"))


def explain_verdict(rows: Iterable[Any]) -> VerdictDecision:
    """Resolve the verdict and say which rule and judge produced it.

    Precedence, exactly as build-plan §6 states it:

    1. any judge regressed -> **NO-GO**
    2. else any judge missed the floor and rigor did *not* call it underpowered ->
       **NO-GO**
    3. else any judge missed the floor while underpowered -> **REVIEW**
    4. else any judge is not ``mw_powered`` -> **REVIEW**
    5. else **GO**

    NO-GO outranks REVIEW because a regression that reached significance was, for
    that question, powered enough. REVIEW outranks GO because invariant 5 forbids
    converting "we cannot tell" into "ship it" -- there is no path from REVIEW
    back to GO anywhere in this function, and that is the property worth testing.

    An **empty** family resolves to REVIEW, not GO. Nothing was measured, and the
    one thing this tool may never do is turn an absence of evidence into a green
    light; ``compare`` refuses the case upstream, but the function is total and
    has to answer.
    """
    ordered = list(rows)
    if not ordered:
        return VerdictDecision(
            verdict=Verdict.REVIEW,
            rule=0,
            reason=(
                "No judge produced a comparable row, so nothing was measured. An "
                "absence of evidence is not a passing grade."
            ),
        )
    for position, row in enumerate(ordered):
        if _flag(row, "regressed"):
            name = _row_name(row, position)
            return VerdictDecision(
                verdict=Verdict.NO_GO,
                rule=1,
                judge=name,
                reason=(
                    f"Judge {name!r} shows a statistically significant regression "
                    f"after Holm-Bonferroni correction across judges."
                ),
            )
    for position, row in enumerate(ordered):
        if not _flag(row, "floor_cleared") and not _flag(row, "underpowered"):
            name = _row_name(row, position)
            return VerdictDecision(
                verdict=Verdict.NO_GO,
                rule=2,
                judge=name,
                reason=(
                    f"Judge {name!r} missed the pass-rate floor and the sample is "
                    f"not underpowered: the bar was missed on the evidence, and "
                    f"more runs will not fix it."
                ),
            )
    for position, row in enumerate(ordered):
        if not _flag(row, "floor_cleared") and _flag(row, "underpowered"):
            name = _row_name(row, position)
            return VerdictDecision(
                verdict=Verdict.REVIEW,
                rule=3,
                judge=name,
                reason=(
                    f"Judge {name!r} missed the pass-rate floor, but rigor reports "
                    f"an underpowered sample rather than a demonstrated failure: "
                    f"collect more completions."
                ),
            )
    for position, row in enumerate(ordered):
        if not _flag(row, "mw_powered"):
            name = _row_name(row, position)
            return VerdictDecision(
                verdict=Verdict.REVIEW,
                rule=4,
                judge=name,
                reason=(
                    f"Judge {name!r} has too few completions to detect the "
                    f"configured minimum effect at the configured power, so "
                    f"'no regression detected' would be a question never asked."
                ),
            )
    return VerdictDecision(
        verdict=Verdict.GO,
        rule=5,
        reason=(
            "No judge regressed, every judge cleared the pass-rate floor, and "
            "every judge had enough completions to have seen the configured "
            "minimum effect."
        ),
    )


def resolve_verdict(rows: Iterable[Any]) -> str:
    """The verdict alone, as a string. See :func:`explain_verdict` for the rules.

    A pure function of the per-judge flags and nothing else: it runs no
    statistics, touches no artifact, and can be driven over every combination of
    its inputs -- including the ones the statistics cannot currently produce,
    which still have to resolve correctly the day a change makes them reachable.
    """
    return explain_verdict(rows).verdict


# --------------------------------------------------------------------------- #
# report shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LatencyStat:
    """Median and p90 of one side's completion durations. Descriptive only.

    Never a gate. A migration that is 30ms slower per call is a product decision,
    not a quality regression, and putting latency behind the verdict would let a
    faster-but-worse model pass. It is printed beside the verdict, not inside it.
    """

    n: int = 0
    median: float | None = None
    p90: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "median": self.median, "p90": self.p90}


@dataclass(frozen=True)
class PowerEstimate:
    """What sample this judge would need to notice the effect being asked about."""

    n_observed: int
    n_required: int | None
    powered: bool
    baseline_rate: float | None
    min_detectable_effect: float
    power_target: float
    alpha: float
    method: str = "two-proportion-normal-approximation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_observed": self.n_observed,
            "n_required": self.n_required,
            "powered": self.powered,
            "baseline_rate": self.baseline_rate,
            "min_detectable_effect": self.min_detectable_effect,
            "power_target": self.power_target,
            "alpha": self.alpha,
            "method": self.method,
            "approximates": (
                "a two-proportion z-test, not the Mann-Whitney U that produces "
                "the regressed flag"
            ),
        }


@dataclass(frozen=True)
class ItemChange:
    """One item whose state moved between the two models, under one judge.

    Carries both counts so the margin is visible: a 4/5 -> 1/5 flip and a
    5/5 -> 0/5 flip are different findings, and printing only "flipped" hides
    which one this is.
    """

    item_id: str
    judge: str
    baseline_passes: int
    baseline_n: int
    candidate_passes: int
    candidate_n: int
    baseline_state: str
    candidate_state: str

    @property
    def label(self) -> str:
        return (
            f"{self.baseline_passes}/{self.baseline_n} -> "
            f"{self.candidate_passes}/{self.candidate_n}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "judge": self.judge,
            "baseline_passes": self.baseline_passes,
            "baseline_n": self.baseline_n,
            "candidate_passes": self.candidate_passes,
            "candidate_n": self.candidate_n,
            "baseline_state": self.baseline_state,
            "candidate_state": self.candidate_state,
            "label": self.label,
        }


@dataclass(frozen=True)
class JudgeComparison:
    """Everything one judge contributed, statistics and flags together."""

    name: str
    model_id: str = ""
    rubric_hash: str = ""
    baseline: Mapping[str, Any] = field(default_factory=dict)
    candidate: Mapping[str, Any] = field(default_factory=dict)
    regression: Mapping[str, Any] | None = None
    p_value: float | None = None
    holm_threshold: float | None = None
    regressed: bool = False
    floor_cleared: bool = False
    underpowered: bool = False
    runs_needed: int | None = None
    mw_powered: bool = False
    power: PowerEstimate | None = None
    test_ran: str = TEST_NOT_RUN
    note: str = ""
    imputed_baseline: int = 0
    imputed_candidate: int = 0
    parse_failures_baseline: int = 0
    parse_failures_candidate: int = 0
    missing_scores_baseline: int = 0
    missing_scores_candidate: int = 0
    item_counts_baseline: Mapping[str, int] = field(default_factory=dict)
    item_counts_candidate: Mapping[str, int] = field(default_factory=dict)
    items: int = 0

    @property
    def flags(self) -> JudgeFlags:
        """The four booleans, and only those, for the resolution function."""
        return JudgeFlags(
            name=self.name,
            regressed=self.regressed,
            floor_cleared=self.floor_cleared,
            underpowered=self.underpowered,
            mw_powered=self.mw_powered,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model_id": self.model_id,
            "rubric_hash": self.rubric_hash,
            "baseline": dict(self.baseline),
            "candidate": dict(self.candidate),
            "regression": None if self.regression is None else dict(self.regression),
            "p_value": self.p_value,
            "holm_threshold": self.holm_threshold,
            "alpha": None if self.regression is None else self.regression.get("alpha"),
            "regressed": self.regressed,
            "floor_cleared": self.floor_cleared,
            "underpowered": self.underpowered,
            "runs_needed": self.runs_needed,
            "mw_powered": self.mw_powered,
            "power": None if self.power is None else self.power.to_dict(),
            "test_ran": self.test_ran,
            "note": self.note,
            "imputed": {
                "baseline": self.imputed_baseline,
                "candidate": self.imputed_candidate,
            },
            "parse_failures": {
                "baseline": self.parse_failures_baseline,
                "candidate": self.parse_failures_candidate,
            },
            "missing_scores": {
                "baseline": self.missing_scores_baseline,
                "candidate": self.missing_scores_candidate,
            },
            "item_counts": {
                "baseline": dict(self.item_counts_baseline),
                "candidate": dict(self.item_counts_candidate),
                "items": self.items,
            },
        }


@dataclass(frozen=True)
class ComparisonReport:
    """The whole comparison: what was measured, what it implies, and why.

    Held in memory only long enough to be written to the evidence log. Nothing
    downstream renders from this object -- ``report.py`` reads the log, so a run
    that dies after the comparison record still produces a document (invariant 2).
    """

    verdict: str
    reason: str
    decided_by: str
    rule: int
    baseline_model: str
    candidate_model: str
    goldenset_hash: str
    judges_hash: str
    n_per_item: int
    thresholds: Thresholds
    judges: tuple[JudgeComparison, ...] = ()
    flips: tuple[ItemChange, ...] = ()
    gains: tuple[ItemChange, ...] = ()
    unstable: tuple[ItemChange, ...] = ()
    latency: Mapping[str, LatencyStat] = field(default_factory=dict)
    completion_rates: Mapping[str, Any] = field(default_factory=dict)
    item_counts: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    created: str = ""

    @property
    def exit_code(self) -> int:
        return Verdict.exit_code(self.verdict)

    def judge(self, name: str) -> JudgeComparison:
        for one in self.judges:
            if one.name == name:
                return one
        raise KeyError(f"no judge named {name!r} in this comparison")

    def comparison_payload(self) -> dict[str, Any]:
        """The ``migkit.comparison`` evidence payload.

        Every threshold that produced the verdict is in here, because the report
        renders from this record and a threshold that only existed in memory is a
        gate nobody can audit. The per-gate dicts from rigor are passed through
        verbatim rather than re-derived, so the report can never print a number
        that disagrees with the one the verdict used.
        """
        return {
            "created": self.created,
            "goldenset_hash": self.goldenset_hash,
            "goldenset_path": self.provenance.get("goldenset_path", ""),
            "judges_hash": self.judges_hash,
            "config_hash": self.provenance.get("config_hash", ""),
            "config_path": self.provenance.get("config_path", ""),
            "baseline": dict(self.provenance.get("baseline", {})),
            "candidate": dict(self.provenance.get("candidate", {})),
            "thresholds": self.thresholds.to_dict(),
            "judges": [one.to_dict() for one in self.judges],
            "flips": _grouped(self.flips),
            "gains": _grouped(self.gains),
            "unstable": _grouped(self.unstable),
            "latency": {side: stat.to_dict() for side, stat in self.latency.items()},
            "completion_rates": dict(self.completion_rates),
            "item_counts": dict(self.item_counts),
            "n_per_item": self.n_per_item,
            "warnings": list(self.warnings),
        }

    def verdict_payload(self) -> dict[str, Any]:
        """The ``migkit.verdict`` evidence payload.

        Carries the thresholds a second time, deliberately. This is the record a
        reader looks at first, and a verdict quoted without the gate it was
        measured against is a colour rather than a finding.
        """
        return {
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "decided_by": self.decided_by,
            "rule": self.rule,
            "thresholds": self.thresholds.to_dict(),
            "judges": [one.flags.to_dict() for one in self.judges],
            "baseline_model": self.baseline_model,
            "candidate_model": self.candidate_model,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.comparison_payload()
        payload.update(self.verdict_payload())
        return payload


def _grouped(changes: Sequence[ItemChange]) -> list[dict[str, Any]]:
    """Item-major view of a change list: one entry per item, judges named.

    The report needs ``{item_id, judges: [...]}`` to build its flip list, and the
    per-judge counts have to travel with it or the margin is lost.
    """
    order: list[str] = []
    grouped: dict[str, list[ItemChange]] = {}
    for change in changes:
        if change.item_id not in grouped:
            grouped[change.item_id] = []
            order.append(change.item_id)
        grouped[change.item_id].append(change)
    return [
        {
            "item_id": item_id,
            "judges": [one.judge for one in grouped[item_id]],
            "changes": [one.to_dict() for one in grouped[item_id]],
        }
        for item_id in order
    ]


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #


def compare(
    baseline: JudgedArtifact,
    candidate: JudgedArtifact,
    *,
    thresholds: Thresholds,
    allow_same_model: bool = False,
    evidence: EvidenceLog | None = None,
    baseline_run: RunArtifact | None = None,
    candidate_run: RunArtifact | None = None,
    goldenset_path: str = "",
    config_path: str = "",
    config_hash: str = "",
) -> ComparisonReport:
    """Compare two judged artifacts and resolve the migration verdict.

    Args:
        baseline: The judged artifact for the model being migrated *from*.
        candidate: The judged artifact for the model being migrated *to*. It is
            the candidate that is gated; the baseline is the reference.
        thresholds: Every number that can change the verdict, echoed into the
            evidence log beside the verdict it produced.
        allow_same_model: Permit both sides to carry the same ``model_id``. Off by
            default because an accidental self-comparison is a real mistake with a
            reassuring result; on, because the plan's "identical models produce
            GO" calibration test needs exactly that comparison.
        evidence: rigor's evidence log. When given, every gate records itself and
            this module writes ``migkit.comparison`` and ``migkit.verdict``.
        baseline_run: The run artifact behind ``baseline``, for latency only. When
            omitted it is loaded from the path the judged artifact records; if
            that file is gone, latency is reported as unavailable rather than
            invented, and the comparison proceeds -- latency is never a gate.
        candidate_run: As ``baseline_run``, for the candidate.
        goldenset_path: Recorded into the evidence payload for the report's
            provenance block. Passed in rather than guessed: a judged artifact does
            not carry it, and inventing a path the report would then read from
            would be worse than leaving it empty.
        config_path: As ``goldenset_path``, for the judge/threshold config file.
        config_hash: Content hash of that config file.

    Returns:
        The :class:`ComparisonReport`, whose ``exit_code`` is the CI contract.

    Raises:
        ArtifactError: On a golden-set mismatch, a coverage mismatch, an
            accidental self-comparison, or a pair with nothing judged in it.
        JudgeConfigError: On a judge-config hash mismatch.
    """
    _require_comparable(baseline, candidate, allow_same_model=allow_same_model)

    warnings: list[str] = []
    names = _judge_names(baseline, candidate)

    # Every judge's statistics are computed first; `regressed` cannot be decided
    # until every p-value in the family is known, because Holm's threshold for one
    # judge depends on how many other judges were tested.
    drafts = [
        _compare_one_judge(name, baseline, candidate, thresholds, evidence, warnings)
        for name in names
    ]
    tested = [index for index, draft in enumerate(drafts) if draft.p_value is not None]
    decisions = holm_bonferroni(
        [drafts[index].p_value or 0.0 for index in tested], alpha=thresholds.alpha
    )
    judges: list[JudgeComparison] = list(drafts)
    for position, index in enumerate(tested):
        rejected, threshold = decisions[position]
        judges[index] = _replace(
            judges[index], regressed=rejected, holm_threshold=threshold
        )

    decision = explain_verdict([one.flags for one in judges])

    flips: list[ItemChange] = []
    gains: list[ItemChange] = []
    unstable: list[ItemChange] = []
    for name in names:
        _classify_items(name, baseline, candidate, flips, gains, unstable)

    baseline_run = _run_for(baseline, baseline_run, "baseline", warnings)
    candidate_run = _run_for(candidate, candidate_run, "candidate", warnings)
    latency = {
        "baseline": _latency(baseline_run),
        "candidate": _latency(candidate_run),
    }

    report = ComparisonReport(
        verdict=decision.verdict,
        reason=decision.reason,
        decided_by=decision.decided_by,
        rule=decision.rule,
        baseline_model=baseline.model_id,
        candidate_model=candidate.model_id,
        goldenset_hash=baseline.goldenset_hash,
        judges_hash=baseline.judges_hash,
        n_per_item=baseline.n_per_item,
        thresholds=thresholds,
        judges=tuple(judges),
        flips=tuple(flips),
        gains=tuple(gains),
        unstable=tuple(unstable),
        latency=latency,
        completion_rates=_completion_rates(judges),
        item_counts=_item_counts_by_judge(judges),
        provenance={
            "goldenset_path": goldenset_path or _recorded_goldenset_path(baseline_run),
            "config_path": config_path,
            "config_hash": config_hash,
            "baseline": _side(baseline, baseline_run),
            "candidate": _side(candidate, candidate_run),
        },
        warnings=tuple(warnings),
        created=utc_now(),
    )

    if evidence is not None:
        evidence.append(EVENT_COMPARISON, report.comparison_payload())
        evidence.append(EVENT_VERDICT, report.verdict_payload())
    return report


def _replace(judge: JudgeComparison, **changes: Any) -> JudgeComparison:
    """Rebuild one row with the Holm decision filled in; the dataclass is frozen."""
    return replace(judge, **changes)


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


def _require_comparable(
    baseline: JudgedArtifact, candidate: JudgedArtifact, *, allow_same_model: bool
) -> None:
    """Refuse anything that is not a like-for-like comparison, before any maths.

    The hash checks are necessary and were once thought sufficient. They are not:
    a truncated baseline -- which invariant 2 guarantees exists, since a crashed
    run still produces one -- carries the right golden-set and judge hashes and
    would yield an unpaired comparison of 25 completions against 200 with 35 items
    silently dropped, flattering whichever side finished. So coverage is checked
    key by key.
    """
    if baseline.goldenset_hash != candidate.goldenset_hash:
        raise ArtifactError(
            f"these artifacts were judged against different golden sets "
            f"({baseline.goldenset_hash[:16]} vs {candidate.goldenset_hash[:16]}). "
            f"Comparing model A on one set to model B on another is not a migration "
            f"decision, it is two unrelated numbers side by side."
        )
    if baseline.judges_hash != candidate.judges_hash:
        raise JudgeConfigError(
            f"these artifacts were graded by different judge panels "
            f"({baseline.judges_hash[:16]} vs {candidate.judges_hash[:16]}). Scores "
            f"from two panels are readings from two instruments; the difference "
            f"between them would measure the judges, not the models."
        )

    left, right = baseline.coverage(), candidate.coverage()
    if not left and not right:
        raise ArtifactError(
            "neither artifact contains a judged completion, so there is nothing to "
            "compare. An empty comparison must not resolve to a verdict."
        )
    if left != right:
        missing = sorted(set(left) - set(right))
        extra = sorted(set(right) - set(left))
        differing = sorted(
            key for key in set(left) & set(right) if left[key] != right[key]
        )
        detail: list[str] = []
        if missing:
            detail.append(
                f"{len(missing)} (judge, item) key(s) only in the baseline, "
                f"e.g. {missing[0]}"
            )
        if extra:
            detail.append(
                f"{len(extra)} only in the candidate, e.g. {extra[0]}"
            )
        if differing:
            key = differing[0]
            detail.append(
                f"{len(differing)} judged a different number of times, e.g. {key}: "
                f"{left[key]} vs {right[key]}"
            )
        raise ArtifactError(
            f"the two artifacts do not cover the same completions "
            f"({sum(left.values())} judged records on the baseline against "
            f"{sum(right.values())} on the candidate): "
            f"{'; '.join(detail)}. Matching golden-set and judge hashes do not make "
            f"a truncated artifact comparable -- the missing items are usually the "
            f"slow or hard ones, so the shortfall flatters whichever side finished."
        )

    if baseline.model_id == candidate.model_id and not allow_same_model:
        raise ArtifactError(
            f"both sides are {baseline.model_id!r}. A model compared against itself "
            f"always looks safe, which is exactly why an accidental self-comparison "
            f"has to be caught. Pass allow_same_model=True if this is the A/A "
            f"calibration run."
        )


def _judge_names(baseline: JudgedArtifact, candidate: JudgedArtifact) -> tuple[str, ...]:
    """Judge names in the baseline's own record order, which is the config order."""
    names = list(baseline.judge_names())
    for name in candidate.judge_names():
        if name not in names:  # unreachable while coverage matches; cheap insurance
            names.append(name)
    return tuple(names)


# --------------------------------------------------------------------------- #
# per-judge statistics
# --------------------------------------------------------------------------- #


def _compare_one_judge(
    name: str,
    baseline: JudgedArtifact,
    candidate: JudgedArtifact,
    thresholds: Thresholds,
    evidence: EvidenceLog | None,
    warnings: list[str],
) -> JudgeComparison:
    """Every statistic for one judge, with ``regressed`` left for Holm to decide."""
    base_records = baseline.for_judge(name)
    cand_records = candidate.for_judge(name)
    base_counted = _counted(base_records)
    cand_counted = _counted(cand_records)

    base_gate, _, _ = _pass_rate(base_counted, thresholds, evidence, f"{name}:baseline")
    cand_gate, floor_cleared, floor_stats = _pass_rate(
        cand_counted, thresholds, evidence, f"{name}:candidate"
    )
    underpowered, runs_needed = _floor_power(floor_stats, floor_cleared, name)

    base_scores, cand_scores, test_ran, note, missing = _scores(base_counted, cand_counted)
    regression: Mapping[str, Any] | None = None
    p_value: float | None = None
    if base_scores and cand_scores:
        regression = _regression(cand_scores, base_scores, thresholds, evidence, name)
        p_value = float(regression["p_value"])
        if regression.get("degenerate"):
            warnings.append(
                f"judge {name!r}: the two score samples are fully tied, so the "
                f"regression test carries no rank information."
            )
        # The review that produced Amendment 1 proposed refusing GO outright when
        # more than 5% of a judge's completions carried no score. That refusal is
        # *not* implemented here: build-plan §6 supersedes the review and does not
        # contain it, and adding a sixth clause to a frozen precedence table in
        # passing is the exact move the plan forbids. It is surfaced as a warning
        # instead, so the assumption is visible to the reader and to whoever
        # decides whether to amend the plan.
        share = max(
            _share(missing[0], len(base_counted)), _share(missing[1], len(cand_counted))
        )
        if share > thresholds.judge_failure_tolerance:
            warnings.append(
                f"judge {name!r}: {share:.1%} of completions on one side carried no "
                f"numeric score and were excluded from the regression test. The "
                f"verdict rests on the remainder."
            )
    else:
        test_ran = TEST_NOT_RUN
        note = note or (
            "no scores on one or both sides; the regression test could not run"
        )
        warnings.append(
            f"judge {name!r}: no comparable scores, so no regression test ran. A "
            f"verdict that never asked the question is not a verdict about quality."
        )

    baseline_rate = base_gate.get("pass_rate")
    n_required = (
        None
        if baseline_rate is None
        else required_sample_size(
            float(baseline_rate),
            min_detectable_effect=thresholds.min_detectable_effect,
            power_target=thresholds.power_target,
            alpha=thresholds.alpha,
        )
    )
    n_observed = min(len(base_counted), len(cand_counted))
    powered = bool(n_required is not None and n_observed >= n_required)
    power = PowerEstimate(
        n_observed=n_observed,
        n_required=n_required,
        powered=powered,
        baseline_rate=None if baseline_rate is None else float(baseline_rate),
        min_detectable_effect=thresholds.min_detectable_effect,
        power_target=thresholds.power_target,
        alpha=thresholds.alpha,
    )
    if not powered and n_required is not None:
        warnings.append(
            f"judge {name!r}: {n_observed} completions per side cannot detect a "
            f"{thresholds.min_detectable_effect:.0%} drop at "
            f"{thresholds.power_target:.0%} power; roughly {n_required} are needed."
        )

    base_items = _item_states(base_records)
    cand_items = _item_states(cand_records)
    identity = _judge_identity(baseline, name)
    return JudgeComparison(
        name=name,
        model_id=str(identity.get("model", identity.get("model_id", ""))),
        rubric_hash=str(identity.get("rubric_hash", "")),
        baseline=base_gate,
        candidate=cand_gate,
        regression=regression,
        p_value=p_value,
        holm_threshold=None,
        regressed=False,  # Holm decides this once the whole family is known.
        floor_cleared=floor_cleared,
        underpowered=underpowered,
        runs_needed=None if runs_needed is None else int(runs_needed),
        mw_powered=powered,
        power=power,
        test_ran=test_ran,
        note=note,
        imputed_baseline=sum(1 for one in base_records if one.imputed),
        imputed_candidate=sum(1 for one in cand_records if one.imputed),
        parse_failures_baseline=sum(1 for one in base_records if one.parse_failure),
        parse_failures_candidate=sum(1 for one in cand_records if one.parse_failure),
        missing_scores_baseline=missing[0],
        missing_scores_candidate=missing[1],
        item_counts_baseline=_item_counts(base_items),
        item_counts_candidate=_item_counts(cand_items),
        items=len(base_items),
    )


def _counted(records: Sequence[JudgeRecord]) -> tuple[JudgeRecord, ...]:
    """Records that say something about the *model*.

    Parse failures are dropped here and only here: the judge was unintelligible,
    so the record is missing data about the model rather than evidence against it,
    and counting it as a failure would let a flaky judge read as a bad model.
    Imputed records -- completions that failed and were scored at the rubric floor
    -- are kept, because a model that times out has told us something.
    """
    return tuple(one for one in records if not one.parse_failure)


def _pass_rate(
    records: Sequence[JudgeRecord],
    thresholds: Thresholds,
    evidence: EvidenceLog | None,
    label: str,
) -> tuple[dict[str, Any], bool, Mapping[str, Any]]:
    """rigor's pass-rate gate, with its own power verdict carried out intact.

    ``underpowered`` and ``runs_needed`` are read off the raised ``PassRateError``
    rather than recomputed. rigor populates them only on the failure branch, and a
    reimplementation of the same idea reached the opposite conclusion on the same
    input: 38/40 against a 0.90 floor is NO-GO under the derived version and
    "underpowered, roughly 113 runs would clear the bar" under rigor's.
    """
    n = len(records)
    successes = sum(1 for one in records if one.passed)
    if n == 0:
        # rigor raises on a rate over zero runs, correctly. No data is a rendering
        # state, and for the verdict it is REVIEW territory: the floor was not
        # cleared, and no sample size was ever taken, so it is underpowered rather
        # than a demonstrated failure.
        return (
            {
                "gate": "pass_rate",
                "label": label,
                "passed": False,
                "n": 0,
                "successes": 0,
                "failures": 0,
                "pass_rate": None,
                "lower_bound": None,
                "interval_lower": None,
                "interval_upper": None,
                "min_rate": thresholds.pass_rate_floor,
                "confidence": thresholds.confidence,
                "method": "wilson-one-sided",
                "underpowered": True,
                "runs_needed": None,
                "no_data": True,
            },
            False,
            {"underpowered": True, "runs_needed": None},
        )
    try:
        report = assert_pass_rate(
            (successes, n),
            thresholds.pass_rate_floor,
            confidence=thresholds.confidence,
            evidence=evidence,
            label=label,
        )
    except PassRateError as exc:
        stats = dict(exc.stats)
        stats.setdefault("underpowered", False)
        stats.setdefault("runs_needed", None)
        return stats, False, stats
    # The gate passed, so rigor never populated its power keys. They are filled in
    # here with the only values they can have on this branch, so that every row of
    # the report has the same shape whichever way the gate went.
    report = dict(report)
    report["underpowered"] = False
    report["runs_needed"] = None
    two_sided = wilson_interval(successes, n, thresholds.confidence)
    report["interval_lower"], report["interval_upper"] = two_sided
    return report, True, report


def _scores(
    base: Sequence[JudgeRecord], cand: Sequence[JudgeRecord]
) -> tuple[list[float], list[float], str, str, tuple[int, int]]:
    """The two arrays handed to the regression test, and what they are made of.

    Two rules, and both are there because rigor rejects the obvious shortcut.

    ``None`` is never passed through: ``_coerce_scores`` raises on it, so a single
    unscored record would abort the entire comparison. A record whose judge
    declined to score -- rigor's own prompt tells a judge to emit ``"score": null``
    when the rubric gives it no basis -- is missing data and is excluded from this
    test only, counted and reported.

    When a judge produced no numeric score anywhere on either side, the test falls
    back to the pass/fail outcomes as ``float(record.passed)``. The cast is
    mandatory: ``_coerce_scores`` rejects ``bool`` by design, precisely so an
    outcome list cannot be silently read as scores. Both sides switch together --
    comparing 1-5 scores against 0/1 outcomes would be a comparison of two
    different scales, and the resulting p-value would be meaningless in whichever
    direction the scales happened to differ.

    One caveat the report carries rather than gates on: outcome data is heavily
    tied, and with ties scipy falls back to the asymptotic method, which at very
    small n is anticonservative (3-vs-3 all-fail against all-pass returns p =
    0.0234 where the exact test's floor is 0.0500). No separate n >= 25 rule is
    imposed for it, because build-plan §6 defines this judge's power flag solely
    through ``min_detectable_effect`` at ``power_target`` -- and at the default
    ten-point effect that requires at least 56 completions a side at *any*
    baseline rate, so the small-n region is already REVIEW rather than GO.
    """
    base_numeric = [float(one.score) for one in base if _is_number(one.score)]
    cand_numeric = [float(one.score) for one in cand if _is_number(one.score)]
    if base_numeric and cand_numeric:
        missing = (
            len(base) - len(base_numeric),
            len(cand) - len(cand_numeric),
        )
        note = ""
        if any(missing):
            note = (
                f"{missing[0]} baseline and {missing[1]} candidate record(s) carried "
                f"no numeric score and are excluded from the regression test only"
            )
        return base_numeric, cand_numeric, TEST_SCORES, note, missing
    return (
        [float(one.passed) for one in base],
        [float(one.passed) for one in cand],
        TEST_OUTCOMES,
        "scores absent; tested on pass/fail outcomes",
        (0, 0),
    )


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _regression(
    candidate_scores: Sequence[float],
    baseline_scores: Sequence[float],
    thresholds: Thresholds,
    evidence: EvidenceLog | None,
    name: str,
) -> Mapping[str, Any]:
    """rigor's one-sided Mann-Whitney, in ``(current, baseline)`` order.

    The order is the whole meaning of the test: ``alternative="less"`` asks
    whether the *candidate* is stochastically smaller than the baseline, and
    reversing the arguments inverts that silently -- a regression would read as an
    improvement, with no error anywhere.

    The exception is caught rather than allowed to propagate because significance
    against the raw alpha is not the verdict: ``regressed`` is decided from the
    Holm-adjusted threshold once every judge's p-value is known. rigor's own
    ``assertion.evaluated`` record therefore reflects the uncorrected gate, and
    the corrected decision travels in the comparison record beside it.
    """
    try:
        return assert_no_regression(
            list(candidate_scores),
            list(baseline_scores),
            alpha=thresholds.alpha,
            evidence=evidence,
            label=name,
        )
    except RegressionError as exc:
        return dict(exc.stats)


def _judge_identity(artifact: JudgedArtifact, name: str) -> Mapping[str, Any]:
    for one in artifact.judges:
        if one.get("name") == name:
            return one
    return {}


# --------------------------------------------------------------------------- #
# item-level analysis
# --------------------------------------------------------------------------- #


def _item_states(records: Sequence[JudgeRecord]) -> dict[str, tuple[int, int, str]]:
    """Per item: passes, draws, and the margin state. Parse failures excluded.

    The unit here is the item, not the completion, and the report prints both
    rates side by side because they answer different questions. Ten items each
    passing 3 of 5 draws is a pooled completion rate of 0.60 -- NO-GO territory --
    with an empty flip list and every item passing at item level. A reader shown
    only one of those two numbers will assume it answered both.
    """
    counts: dict[str, list[int]] = {}
    for one in records:
        if one.parse_failure:
            continue
        entry = counts.setdefault(one.item_id, [0, 0])
        entry[1] += 1
        if one.passed:
            entry[0] += 1
    return {
        item_id: (passes, n, item_state(passes, n))
        for item_id, (passes, n) in counts.items()
    }


def _floor_power(
    floor_stats: Mapping[str, Any], floor_cleared: bool, judge: str
) -> tuple[bool, int | None]:
    """rigor's own power verdict on the floor gate, or a refusal to guess.

    This reads two keys off a *failed* ``assert_pass_rate``: ``underpowered`` and
    ``runs_needed``. Both are rigor's, not ours, and that is the point -- the
    alternative was a derived version that called 38/40 against a 0.90 floor a
    demonstrated failure where rigor calls it an underpowered sample needing about
    113 runs.

    The refusal matters more than the read. A plain ``.get("underpowered", False)``
    is a silent wrong answer if a future rigor release stops setting the key: the
    absent flag reads as "powered", the row takes clause 2 instead of clause 3, and
    a REVIEW becomes a NO-GO with nothing raised anywhere. A migration blocked by a
    verdict nobody can trace is worse than a tool that stops and says why, so a
    missing key is an error rather than a default. The weekly drift canary exists
    to find this on a Monday rather than in someone's pipeline.

    Nothing is read when the gate passed: rigor only reports these on failure, and
    a cleared floor is not an underpowered one.
    """
    if floor_cleared:
        return False, None
    if "underpowered" not in floor_stats:
        raise DependencyContractError(
            f"opik-rigor's failed pass-rate report for judge {judge!r} carries no "
            f"'underpowered' flag. This build reads that flag to tell a demonstrated "
            f"failure (NO-GO) from a sample too small to judge (REVIEW), and guessing "
            f"either way would produce a verdict that cannot be traced to evidence. "
            f"Keys present: {sorted(floor_stats)}."
        )
    return bool(floor_stats["underpowered"]), floor_stats.get("runs_needed")


def _item_counts(states: Mapping[str, tuple[int, int, str]]) -> dict[str, int]:
    """Items in each of the three states. Deliberately not a rate.

    A three-state classification does not reduce to one fraction without
    smuggling the ambiguous items into one bucket or the other, and whichever
    bucket you pick, the number lies in that direction. Ten items each passing
    3/5 are not "10/10 passing" and not "0/10 passing"; they are ten items this
    evidence cannot classify, and that is what the report says.
    """
    counts = {STATE_PASS: 0, STATE_FAIL: 0, STATE_UNSTABLE: 0}
    for _, _, state in states.values():
        counts[state] = counts.get(state, 0) + 1
    return {
        "passing": counts[STATE_PASS],
        "failing": counts[STATE_FAIL],
        "unstable": counts[STATE_UNSTABLE],
    }


def _classify_items(
    name: str,
    baseline: JudgedArtifact,
    candidate: JudgedArtifact,
    flips: list[ItemChange],
    gains: list[ItemChange],
    unstable: list[ItemChange],
) -> None:
    """Split one judge's items into flips, gains and unstable.

    Gains are collected and never netted against flips. Netting is how a bad
    migration ships: two items that started working do not undo two that stopped,
    because the two that stopped are the ones a user will hit tomorrow.
    """
    base = _item_states(baseline.for_judge(name))
    cand = _item_states(candidate.for_judge(name))
    for item_id in base:
        if item_id not in cand:
            continue
        base_passes, base_n, base_state = base[item_id]
        cand_passes, cand_n, cand_state = cand[item_id]
        change = ItemChange(
            item_id=item_id,
            judge=name,
            baseline_passes=base_passes,
            baseline_n=base_n,
            candidate_passes=cand_passes,
            candidate_n=cand_n,
            baseline_state=base_state,
            candidate_state=cand_state,
        )
        if base_state == STATE_PASS and cand_state == STATE_FAIL:
            flips.append(change)
        elif base_state == STATE_FAIL and cand_state == STATE_PASS:
            gains.append(change)
        elif STATE_UNSTABLE in (base_state, cand_state):
            # Named, not counted -- and named even when nothing moved. The first
            # version of this line also required base_state != cand_state, which
            # meant an item sitting at 3/5 under *both* models appeared in no list
            # at all: not a flip, not a gain, not unstable. That item is the most
            # interesting row in the report, because its verdict is a coin toss on
            # both sides of the migration and no rerun will agree with this one.
            # Naming it is the entire reason a third state exists.
            unstable.append(change)


def _completion_rates(judges: Sequence[JudgeComparison]) -> dict[str, Any]:
    """Pooled per-completion counts across judges, for the headline line."""
    return {
        "baseline": {
            "passes": sum(int(one.baseline.get("successes", 0)) for one in judges),
            "n": sum(int(one.baseline.get("n", 0)) for one in judges),
        },
        "candidate": {
            "passes": sum(int(one.candidate.get("successes", 0)) for one in judges),
            "n": sum(int(one.candidate.get("n", 0)) for one in judges),
        },
        "unit": "completion",
    }


def _item_counts_by_judge(judges: Sequence[JudgeComparison]) -> dict[str, Any]:
    """Per-judge item counts in three states, printed beside the completion rates.

    Both units are printed because they answer different questions and a reader
    given only one will assume it answers both: the completion rate says how often
    the model is right, and the item counts say how many cases it is reliable on.
    Ten items at 3/5 make that gap concrete -- a 0.60 completion rate, and not one
    item anybody should call settled.
    """
    return {
        "unit": "item",
        "per_judge": {
            one.name: {
                "baseline": dict(one.item_counts_baseline),
                "candidate": dict(one.item_counts_candidate),
                "items": one.items,
            }
            for one in judges
        },
    }


# --------------------------------------------------------------------------- #
# latency -- descriptive only
# --------------------------------------------------------------------------- #


def _run_for(
    judged: JudgedArtifact,
    given: RunArtifact | None,
    side: str,
    warnings: list[str],
) -> RunArtifact | None:
    """The run artifact behind a judged one, for durations and adapter names.

    Loaded from the recorded path when the caller did not pass it. Failure here is
    never fatal: latency is descriptive and the adapter name is provenance, so a
    missing run artifact degrades those two sections and is named in the warnings
    rather than stopping a verdict the judged artifacts can perfectly well support.
    """
    if given is not None:
        return given
    source = judged.source
    if not source:
        return None
    try:
        if not Path(source).is_file():
            raise ArtifactError(f"no run artifact at {source}")
        return RunArtifact.load(source)
    except (ArtifactError, OSError) as exc:
        warnings.append(
            f"{side} run artifact at {source!r} could not be read ({exc}); latency "
            f"and adapter provenance are unavailable for that side."
        )
        return None


def _latency(run: RunArtifact | None) -> LatencyStat:
    """Median and p90 of one side's durations, from stdlib ``statistics``.

    Failed completions are included: the time a call spent before timing out is
    real time a user waited, and excluding it would make the side that fails
    slowly look fastest.
    """
    if run is None or not run.completions:
        return LatencyStat()
    values = sorted(float(one.duration) for one in run.completions)
    return LatencyStat(n=len(values), median=_median(values), p90=_p90(values))


def _p90(ordered: Sequence[float]) -> float:
    """Linear-interpolated 90th percentile, matching numpy's default definition.

    Written out rather than taken from ``statistics.quantiles`` alone because that
    function raises on a single data point, and one completion is a state a
    truncated run reaches.
    """
    if len(ordered) == 1:
        return float(ordered[0])
    position = 0.9 * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def _side(judged: JudgedArtifact, run: RunArtifact | None) -> dict[str, Any]:
    return {
        "model_id": judged.model_id,
        "adapter": "" if run is None else (run.header.adapter or ""),
        "adapters": [] if run is None else list(run.adapters),
        "artifact": judged.source,
        "judged_artifact": judged.path or "",
        "n_per_item": judged.n_per_item,
        "parts": judged.parts,
        "run_parts": None if run is None else run.parts,
        "records": len(judged.records),
        "imputed": sum(1 for one in judged.records if one.imputed),
        "parse_failures": sum(1 for one in judged.records if one.parse_failure),
    }


def _recorded_goldenset_path(run: RunArtifact | None) -> str:
    return "" if run is None else (run.header.goldenset_path or "")
