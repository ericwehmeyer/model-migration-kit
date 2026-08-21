"""One comparison, flattened to one point on a timeline.

The report has always rendered the *last* comparison in an evidence log. A log
that holds thirteen nightly runs and one real one therefore renders as a single
verdict with no history, and the twelve nights that would have told a reader
whether this candidate is drifting or merely noisy are on disk and unread. This
module is the seam that changes that: it turns one ``migkit.comparison`` payload,
plus the ``migkit.verdict`` payload that followed it, into one flat
:class:`RunPoint`, so a series is a tuple of points rather than a tree of nested
dictionaries every renderer has to walk again.

**The inputs are payload mappings, never live objects.** No function here accepts
a ``ComparisonReport``, a ``JudgedArtifact`` or a ``RunArtifact``, and nothing
here imports ``report``. That is the rule the renderer already keeps for the same
reason (invariant 2): a series is reconstructed from a log written by an earlier
process, frequently on another machine, and a path that only runs after a crash
is a path that has never run when you need it. Taking mappings means every green
run exercises the reconstruction.

**Nothing here computes a statistic.** Every rate, bound, interval and p-value is
lifted out of the gate dict the verdict was measured against, verbatim. A point
that re-derived a number could disagree with the banner printed beside it, and a
timeline that contradicts its own verdicts is worse than no timeline.

**Two numbers that look interchangeable are not.** ``floor`` and ``confidence``
come from the *gate that was applied* -- the candidate side of the widest judge.
``alpha`` comes from that same judge, but from the judge mapping itself: the gate
dicts carry ``min_rate`` and ``confidence`` and no ``alpha`` at all, so an
``alpha`` read from the candidate side would be a read that could never succeed.
All three fall back to ``comparison["thresholds"]``, which is what was
*configured*. On a run where the two differ the gate is the truth, and the
failure this ordering prevents is a timeline drawing a 0.90 floor rule across a
night that was gated at 0.85, which misattributes every verdict on the chart.
Where neither records the number it stays ``None``: a substituted default is the
same lie with nothing left to show that it was substituted. For ``floor``, which
of the two supplied the number travels beside it in ``floor_source``, because the
fallback is permitted and a later chunk still has to know that it happened.

**Nothing here raises.** A payload is other people's JSON, read long after the
process that wrote it exited. Every coercion below has an absent value to fall
to, and the module's one hard rule is that a single unreadable record costs that
record and not the report.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["RunPoint", "run_point"]

#: ``floor_source``'s three values. The word "unrecorded" is
#: ``report.THRESHOLD_SOURCE_UNRECORDED``'s, deliberately: the report already has
#: a vocabulary for "the evidence does not say", and a second one would make two
#: parts of the same page describe the same absence in different words.
_FLOOR_FROM_GATE = "gate"
_FLOOR_FROM_THRESHOLDS = "thresholds"
_FLOOR_UNRECORDED = "unrecorded"


@dataclass(frozen=True)
class RunPoint:
    """One comparison as one row: everything a timeline needs, nothing nested.

    Frozen because a point is a reading. A series that can be edited in place is
    a series whose chart can be made to disagree with the table beneath it.
    """

    #: RFC3339 exactly as recorded -- not normalised, not re-formatted. ``""``
    #: when neither the payload nor the envelope carried a parseable one.
    created: str
    #: ``"payload"``, ``"envelope"`` or ``"unknown"``. Which of the two clocks
    #: placed this point is a fact the reader is entitled to: the envelope's
    #: belongs to a package this project does not own.
    created_source: str
    #: ``None`` when no verdict record was paired with this comparison, which is
    #: what a run that died between the two records looks like.
    verdict: str | None
    reason: str | None
    baseline_model: str
    candidate_model: str
    adapter_baseline: str
    adapter_candidate: str
    goldenset_hash: str
    judges_hash: str
    config_hash: str
    config_path: str
    n_per_item: int
    #: Golden-set items the widest judge covered, ``0`` when unrecorded.
    items: int
    #: Completions the widest judge *graded*, per side -- the gate's own ``n``.
    #: Not the number of completions the run produced: a completion whose judge
    #: reply would not parse is produced and never graded, so a side that sampled
    #: 60 and lost 3 to parse failures records 60 completions and 57 judged. The
    #: report's "completions" row counts the first of those; this counts the
    #: second, and on any run with a parse failure they are different numbers.
    judged_baseline: int
    judged_candidate: int
    #: Completions the judge graded and *failed*, per side -- the gate's own
    #: ``failures``, which is ``n - successes``. Not adapter errors: the report's
    #: "failed completions" row counts completions the adapter never returned, and
    #: on the demo run the two readings are 15 and 0. Naming these plain
    #: ``failures`` invited a later chunk to render one row from the other.
    judge_failures_baseline: int
    judge_failures_candidate: int
    #: Candidate side of the widest judge. ``None`` -- never ``0.0`` -- when
    #: nothing was measured.
    pass_rate: float | None
    interval: tuple[float, float] | None
    lower_bound: float | None
    #: The gate's own ``min_rate``, not the configured threshold.
    floor: float | None
    #: ``"gate"``, ``"thresholds"`` or ``"unrecorded"`` -- where ``floor`` came
    #: from, on the pattern ``created``/``created_source`` sets two fields above.
    #: The contract permits the fallback to ``comparison["thresholds"]`` and
    #: requires it not to be silent: the number the run was *held to* and the
    #: number that was *configured* are the same float and different claims, and
    #: this is the only field that tells a later chunk which one it has.
    floor_source: str
    confidence: float | None
    alpha: float | None
    #: Which judge the numbers above came from, so a two-judge row can be read
    #: without guessing which half of the panel it quotes.
    judge_name: str
    judge_model_id: str
    #: One per judge, sorted. Not de-duplicated: two judges sharing a rubric hash
    #: is a fact about the panel, and collapsing it would hide a mis-wired config.
    rubric_hashes: tuple[str, ...]
    p_value: float | None
    latency_median_candidate: float | None
    runs_needed: int | None
    n_required: int | None
    warnings: tuple[str, ...]


def run_point(
    comparison: Mapping[str, Any],
    verdict: Mapping[str, Any] | None,
    *,
    envelope_ts: str = "",
) -> RunPoint:
    """Flatten one comparison payload, and the verdict payload that followed it.

    Args:
        comparison: The ``migkit.comparison`` payload mapping. Not the envelope
            around it, and not a live ``ComparisonReport``.
        verdict: The ``migkit.verdict`` payload mapping, or ``None`` when the log
            ends after the comparison record. A missing verdict is a state a
            crashed run reaches routinely and is never an error here.
        envelope_ts: The ``ts`` of the envelope the comparison arrived in, used
            only when the payload's own ``created`` is absent or unparseable. A
            payload written by a future writer that dropped the field would
            otherwise sort as the epoch and put that run at the far left.

    Returns:
        One :class:`RunPoint`. Always -- an empty judge list, a zero denominator
        and a missing verdict are rendering states, not failures.
    """
    judges = [_mapping(one) for one in comparison.get("judges") or ()]
    judge = _widest_judge(judges)
    baseline_gate = _mapping(judge.get("baseline"))
    candidate_gate = _mapping(judge.get("candidate"))
    thresholds = _mapping(comparison.get("thresholds"))
    baseline = _mapping(comparison.get("baseline"))
    candidate = _mapping(comparison.get("candidate"))
    latency = _mapping(_mapping(comparison.get("latency")).get("candidate"))
    power = _mapping(judge.get("power"))
    decision = _mapping(verdict)
    created, created_source = _created(comparison.get("created"), envelope_ts)
    rate, interval, lower_bound = _candidate_rate(candidate_gate)
    floor, floor_source = _gated(candidate_gate.get("min_rate"), thresholds.get("pass_rate_floor"))

    return RunPoint(
        created=created,
        created_source=created_source,
        verdict=_text_or_none(decision.get("verdict")),
        reason=_text_or_none(decision.get("reason")),
        baseline_model=_text(baseline.get("model_id")),
        candidate_model=_text(candidate.get("model_id")),
        adapter_baseline=_text(baseline.get("adapter")),
        adapter_candidate=_text(candidate.get("adapter")),
        goldenset_hash=_text(comparison.get("goldenset_hash")),
        judges_hash=_text(comparison.get("judges_hash")),
        config_hash=_text(comparison.get("config_hash")),
        config_path=_text(comparison.get("config_path")),
        n_per_item=_count(comparison.get("n_per_item")),
        items=_count(_mapping(judge.get("item_counts")).get("items")),
        judged_baseline=_count(baseline_gate.get("n")),
        judged_candidate=_count(candidate_gate.get("n")),
        judge_failures_baseline=_count(baseline_gate.get("failures")),
        judge_failures_candidate=_count(candidate_gate.get("failures")),
        pass_rate=rate,
        interval=interval,
        lower_bound=lower_bound,
        floor=floor,
        floor_source=floor_source,
        confidence=_gated(candidate_gate.get("confidence"), thresholds.get("confidence"))[0],
        alpha=_gated(judge.get("alpha"), thresholds.get("alpha"))[0],
        judge_name=_text(judge.get("name")),
        judge_model_id=_text(judge.get("model_id")),
        rubric_hashes=tuple(sorted(_text(one.get("rubric_hash")) for one in judges)),
        p_value=_number(judge.get("p_value")),
        latency_median_candidate=_number(latency.get("median")),
        runs_needed=_count_or_none(judge.get("runs_needed")),
        n_required=_count_or_none(power.get("n_required")),
        warnings=_warnings(comparison.get("warnings")),
    )


# --------------------------------------------------------------------------- #
# which judge the point speaks for
# --------------------------------------------------------------------------- #


def _widest_judge(judges: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The judge that graded the most candidate completions; ties to config order.

    The rule ``report._per_judge_counts`` already applies, for its reason: two
    judges grading the same 60 completions are 120 records and 60 completions, so
    summing across the panel would double every count on the chart. Taking
    ``judges[0]`` instead is correct on every single-judge log -- including the
    demo and every fixture in this repository -- and wrong on the first two-judge
    log anyone runs, which is why the choice is written out here rather than
    inlined as an index.

    ``>`` rather than ``>=`` is what breaks the tie on the payload's own judge
    order, which ``comparison._judge_names`` guarantees is the config order. A
    panel whose judges all graded nothing still selects the first: an empty
    denominator blanks the rates, but the judge's name, rubric and gate remain
    facts about the run. An empty panel selects nothing and every field derived
    from one falls to its absent value.
    """
    widest: Mapping[str, Any] = {}
    best = -1
    for one in judges:
        graded = _count(_mapping(one.get("candidate")).get("n"))
        if graded > best:
            best = graded
            widest = one
    return widest


def _candidate_rate(
    gate: Mapping[str, Any],
) -> tuple[float | None, tuple[float, float] | None, float | None]:
    """The candidate side's rate, interval and one-sided bound, or three ``None``s.

    ``n == 0`` is a state a truncated run reaches routinely, and rigor raises
    rather than returning a rate over zero runs. Passing ``0.0`` up instead would
    plot a point on the floor of the chart for a run that measured nothing, which
    reads as a total collapse rather than as an absence -- and on a timeline that
    is a line drawn through a fact that was never observed.

    A one-ended interval is refused for the same reason: half a pair is not an
    interval, and a renderer that filled the missing end from the other one would
    have invented the number a reviewer is signing against.
    """
    if _count(gate.get("n")) <= 0:
        return None, None, None
    lower = _number(gate.get("interval_lower"))
    upper = _number(gate.get("interval_upper"))
    interval = None if lower is None or upper is None else (lower, upper)
    return _number(gate.get("pass_rate")), interval, _number(gate.get("lower_bound"))


def _gated(applied: Any, configured: Any) -> tuple[float | None, str]:
    """The number the gate used, else the one configured, else nothing -- and which.

    The order is the whole point of the function. ``comparison["thresholds"]`` is
    what the config asked for; the gate dict is what rigor was actually called
    with, and on a run where they differ the second is the one the verdict rests
    on. The fallback exists because a payload may record only the run-level
    mapping, and it stops at ``None``: where neither recorded the number, a point
    records nothing rather than a plausible default, because a default drawn as a
    rule across a chart is indistinguishable from a measurement.

    The source is returned alongside because the fallback is permitted and must
    not be silent -- a floor that came from the configuration is a weaker claim
    than one that came from the gate, and by the time a renderer holds the bare
    float the difference is unrecoverable. Only ``floor`` keeps its source today;
    the tuple is returned for all three so that keeping a second is an edit to one
    caller rather than to this function.
    """
    lifted = _number(applied)
    if lifted is not None:
        return lifted, _FLOOR_FROM_GATE
    fallback = _number(configured)
    if fallback is not None:
        return fallback, _FLOOR_FROM_THRESHOLDS
    return None, _FLOOR_UNRECORDED


# --------------------------------------------------------------------------- #
# when the run happened
# --------------------------------------------------------------------------- #


def _created(recorded: Any, envelope_ts: str) -> tuple[str, str]:
    """``(timestamp, source)``, preferring the payload's clock to the envelope's.

    ``payload["created"]`` is a recorded fact about the comparison; the envelope
    ``ts`` is a fact about when a line was written, and it survives neither a
    concatenated log nor a copied one. So the payload wins, the envelope is the
    fallback, and which of the two was used travels with the value.

    An unparseable timestamp is treated as an absent one deliberately. Carrying
    the raw string forward with ``created_source == "payload"`` would push the
    same parse failure into every chunk downstream and invite one of them to plot
    the point at an invented position. A point with a known verdict and an unknown
    date has to be named beneath the timeline rather than drawn on it; deciding
    that once, here, is what lets the rest of the pipeline rely on ``created``
    being either a date or nothing.
    """
    payload_value = _text(recorded)
    if _is_timestamp(payload_value):
        return payload_value, "payload"
    stamped = _text(envelope_ts)
    if _is_timestamp(stamped):
        return stamped, "envelope"
    return "", "unknown"


def _is_timestamp(value: str) -> bool:
    """Whether ``datetime.fromisoformat`` will accept this, on 3.10 as on 3.13.

    A trailing ``Z`` is normalised for the check and deliberately not for the
    stored value. ``fromisoformat`` learned to accept ``Z`` only in 3.11, and this
    package supports 3.10, so without the normalisation one evidence log would
    yield a dated point on one interpreter and an undated one on another.
    """
    if not value:
        return False
    normalised = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalised)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# coercion -- each of these exists because a payload is JSON, not a type
# --------------------------------------------------------------------------- #


def _mapping(value: Any) -> Mapping[str, Any]:
    """Something to ``.get`` from, whatever the payload actually held there.

    ``None`` is routine: ``regression`` and ``power`` are written as ``null`` on a
    judge that never ran a test. The ``isinstance`` rather than a bare ``or {}``
    is the cheap insurance -- a malformed log would otherwise raise
    ``AttributeError`` out of a reader whose whole job is surviving malformed logs.
    """
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _text_or_none(value: Any) -> str | None:
    """``None`` when the key was absent, which is not the same as an empty string.

    A verdict record that never arrived and a verdict recorded as ``""`` are
    different failures, and the series has to be able to say which one it saw.
    """
    return None if value is None else str(value)


def _number(value: Any) -> float | None:
    """A *finite* float, or ``None`` -- and a numeric string is not one.

    Three exclusions, each with a failure behind it.

    ``bool`` is excluded because ``True`` is an ``int`` to Python, and a flag that
    leaked into a numeric field would otherwise be plotted as a rate of 1.0.

    ``NaN`` and the infinities are excluded because they arrive: ``json.loads``
    accepts bare ``NaN`` and ``Infinity`` by default, and ``comparison.py`` keeps
    its own note about a degenerate test handing back ``NaN``. Neither is a
    quantity a chart can draw, and a ``NaN`` floor is worse than undrawable --
    every comparison against it is ``False``, so a run would silently fail a gate
    it was never really held to.

    A numeric string is excluded to match ``report._number``, which refuses one
    too, and the agreement is the point rather than a coincidence: these two
    readers render into the same document. On a log with a quoted ``pass_rate`` a
    string-tolerant series would draw 0.75 on the timeline while the table beside
    it printed an em-dash for the same number, and "a timeline that contradicts
    its own verdicts" is the failure this module exists to prevent. The place to
    repair a quoted rate is the writer, not one of its two readers. Counts are the
    deliberate exception, for the reason given in :func:`_count`.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _numeric(value: Any) -> float | None:
    """:func:`_number`, extended to a string that spells a finite number.

    Used only by the count coercions below. Kept separate so that widening it
    cannot widen ``pass_rate``, ``floor`` or ``p_value`` by accident.
    """
    if isinstance(value, str):
        try:
            return _number(float(value))
        except ValueError:
            return None
    return _number(value)


def _warnings(value: Any) -> tuple[str, ...]:
    """The run's warnings -- and never the letters of one.

    A ``str`` is iterable, so the obvious comprehension turns a payload that
    recorded ``"careful"`` into seven one-character warnings: seven rows of noise
    in the one place a reader looks to find out why a difference should not be
    trusted. A writer that recorded a single warning as a bare string meant one
    warning, so it becomes one. Dropping it instead would be silent evidence loss,
    which is the same failure from the other side. Anything that is neither a
    string nor a sequence yields nothing, because iterating a mapping would list
    its keys as though they were warnings.
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(_text(one) for one in value)
    return ()


def _count(value: Any) -> int:
    """A count, ``0`` for anything that will not coerce.

    ``"5"`` becomes ``5``, and this is the only place a numeric string is read.
    A writer that quoted its integers is a real thing to survive, and reading a
    quoted ``n_per_item`` as zero would split one series into two on the grouping
    key of section 4.4 -- two short lines on the chart where there was one long
    one. What makes the string safe here and not in :func:`_number` is the type:
    these fields are ``int``, not ``int | None``, so a count has no way to say
    "unavailable" the way a rate does. Its only alternative to reading the string
    is ``0``, which is a claim about the run rather than an admission about the
    record.

    Anything genuinely uninterpretable becomes ``0``, the same value an absent key
    gives, because both are the same statement. ``NaN`` and infinity are
    uninterpretable in exactly this way, and they are why this goes through
    :func:`_numeric` rather than calling ``int`` on whatever it was handed:
    ``int(float("nan"))`` raises ``ValueError`` and ``int(float("inf"))`` raises
    ``OverflowError``, out of a function whose entire contract is not raising.
    """
    number = _numeric(value)
    return 0 if number is None else int(number)


def _count_or_none(value: Any) -> int | None:
    """As :func:`_count`, except that ``None`` survives.

    ``runs_needed`` and ``power.n_required`` are ``null`` whenever the sizing
    could not be computed, which is not the statement "zero further runs are
    needed" that a coerced ``0`` would make. A non-finite value is ``None`` here
    for the same reason it is ``0`` in :func:`_count`: unreadable, not zero.
    """
    number = _numeric(value)
    return None if number is None else int(number)
