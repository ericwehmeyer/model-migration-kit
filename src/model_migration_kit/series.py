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

:func:`read_series` is the one function here that names a file, and it obeys the
same rule from the other side: it takes a *path* and nothing else, and the reader
it reads through is ``evidence.stream_records`` -- the log reader ``report`` uses,
which moved one module down rather than being copied, because two readers of one
format is how a reader and a writer drift apart.

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
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opik_rigor import EvidenceRecord

from .contracts import EVENT_COMPARISON, EVENT_VERDICT
from .evidence import resolve_evidence, stream_records

__all__ = [
    "RunPoint",
    "SeriesBuilder",
    "SpotCheck",
    "parse_created",
    "read_series",
    "run_point",
    "spot_check",
]

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
    #: ``None`` when no verdict record followed this comparison, which is what a
    #: run that died between the two records looks like.
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
# a log, read as a series
# --------------------------------------------------------------------------- #


class SeriesBuilder:
    """:func:`read_series`' pairing rule, as state a caller drives record by record.

    A caller that only wants a series calls :func:`read_series`, which is a thin
    driver over this class. This exists for the one caller that cannot:
    ``ReportModel.from_evidence`` is already streaming the same log for the
    records its headline is built from, and calling :func:`read_series` beside
    that loop would read the log a second time. That is a different cost from the
    one :func:`~model_migration_kit.evidence.stream_records` removed -- bytes off
    the disk rather than records held in memory -- and the evidence log is the
    largest artifact this pipeline writes, so it is worth a class.

    What is *not* worth it is a second copy of the pairing rule. Two
    implementations of "which comparison a verdict belongs to" is the arrangement
    in which the report and the timeline printed beside it start disagreeing
    about which run a NO-GO belongs to, which is the failure this module was
    written to prevent. So the rule lives here once, and every reason behind it
    -- which point a verdict updates, a verdict arriving before any comparison,
    records passed over untouched, a payload that is not an object, and why
    nothing is sorted -- is documented on :func:`read_series`.
    """

    __slots__ = ("_points",)

    def __init__(self) -> None:
        self._points: list[RunPoint] = []

    def add(self, record: EvidenceRecord) -> None:
        """Open a point, update the most recently opened one, or pass the record over.

        Safe to call on every record of a log, including the ones this class has
        no interest in, so a caller that is already looping for another reason
        adds one line rather than a second condition it has to keep in step.
        """
        if record.event_type == EVENT_COMPARISON:
            self._points.append(
                run_point(_mapping(record.payload), None, envelope_ts=_text(record.ts))
            )
        elif record.event_type == EVENT_VERDICT and self._points:
            # The most recently opened point is always the last row: points are
            # appended when their comparison is read, and never removed or
            # reordered. A second verdict overwrites the first rather than being
            # dropped -- see :func:`read_series` for why that is not "close the
            # most recent point still open".
            self._points[-1] = _decided(self._points[-1], _mapping(record.payload))

    def points(self) -> tuple[RunPoint, ...]:
        """The points added so far, in the order their comparisons were written.

        Cheap to call more than once and never a final step: a point takes its
        place the moment its comparison is read, so this is a copy of a list and
        not a build.
        """
        return tuple(self._points)


def read_series(evidence: str | Path) -> tuple[RunPoint, ...]:
    """Every comparison in one evidence log, in the order it was written.

    One streaming pass, through the same reader the renderer uses, driving the
    pairing rule :class:`SeriesBuilder` holds -- the renderer drives that same
    rule from its own loop rather than calling this, so the log is read once.
    What this replaces is not another function but a habit: the report has always
    kept the *last* comparison in a log and discarded the rest, so fourteen
    nightly runs appended to one file rendered as one verdict.

    **A verdict belongs to the comparison it follows, and that is the whole of
    the difficulty.** A comparison record opens a point; every verdict record
    updates the *most recently opened* point, overwriting a value already there.

    The rule turns on one fact about this repository: there is exactly one writer
    of ``migkit.comparison`` and ``migkit.verdict``, at ``comparison.py:907-908``
    -- two ``evidence.append`` calls back to back inside one ``if``. So a log
    holding two comparisons before either verdict is not a shape this pipeline
    writes, and that shape is the only one first-in-first-out pairing gets right.
    First-in-first-out was the rule here until C19, chosen against a log the
    writer cannot produce.

    The shape a **crash** produces is the shape first-in-first-out gets wrong, and
    that one is routine: a comparison with no verdict after it, then the next
    night appended to the same file. The surviving verdict closes the *dead* run,
    and every later verdict shifts with it::

        log:  C(night-1)   C(night-2) V(night-2)   C(night-3) V(night-3)
              night-1  verdict=GO     reason='night 2 was fine'
              night-2  verdict=NO-GO  reason='night 3 regressed'
              night-3  verdict=None

    One crashed night moves every later verdict by one, permanently, in a file
    that only ever grows -- a NO-GO drawn on a green night, which is verbatim the
    failure this module exists to prevent, produced by the rule chosen to prevent
    it. Under the rule above the same log reads night-1 as ``verdict is None`` and
    leaves every later night's verdict exactly where it was written.

    *Updates*, and deliberately not "closes the most recent point still open": a
    log reading ``C V1 V2`` carries V2. The headline reduction in
    ``report.ReportModel.from_evidence`` takes the last verdict record it sees, so
    a close-once rule would drop V2 here and put the timeline back into
    disagreement with the banner printed beside it -- which is the one thing a
    single shared pairing rule exists to make impossible.

    A verdict arriving before any comparison is ignored: it belongs to a
    comparison this log does not contain, which is what the tail of a log rotated
    mid-run looks like. It opens no point and overwrites nothing. Records
    *between* a comparison and its verdict -- rigor's own ``judge.verdict`` lines,
    a ``migkit.judging_completed`` -- are passed over untouched, so a future
    writer that interleaves more than ``compare`` does today still reads
    correctly.

    Two processes appending to one log is the case no rule reads correctly.
    rigor's evidence log interleaves whole records rather than tearing them, and
    ``cli.DEFAULT_EVIDENCE`` makes one shared path the default, so
    ``C_A C_B V_A V_B`` and ``C_A C_B V_B V_A`` are equally likely and neither is
    distinguishable from a crash. Detecting the shape and refusing it was weighed
    at C19's review and rejected: the only signature the interleave leaves --
    two comparisons standing before either verdict -- is the crashed night's
    signature too, so a detector keyed on it would refuse the log this rule was
    rewritten to read. It would also have nothing to say about *which* of the two
    orderings it saw. So this rule does not detect it; it declines to be wrong on
    the single-writer log the pipeline actually produces, and says here that it is
    guessing on any other. The same limitation is recorded in ``report``'s module
    docstring, at more length and with what a future chunk would need -- a writer
    identity on each record -- because the person it costs is reading a banner
    that disagrees with the timeline, not this function.

    A record whose payload is not a JSON object still opens a point, or updates
    one, with the payload read as empty. ``EvidenceRecord.from_json`` checks the
    envelope and not the payload, so this is reachable, and the choice is between
    a point that says nothing and a series shorter than the log it was read from.
    A blank row can be seen and asked about; a missing run cannot.

    Nothing is sorted and nothing is de-duplicated. File order *is* the series
    order: the timestamps are written by whichever machine ran each night, a
    sorted series would silently reorder a log whose clock stepped backwards over
    a daylight-saving boundary, and two runs of the same config are two runs
    rather than one measurement recorded twice. The returned order is free too,
    though no longer for the reason that used to be given for it: it is not that
    the oldest open point is always the one that closes next, but that a verdict
    never appends, reorders or removes a row -- it writes back into one a
    comparison already put in place. That argument holds whatever the pairing
    rule is, and it falls out of the eager construction argued for in the last
    paragraph below.

    Args:
        evidence: The log, or a directory holding ``evidence.jsonl``. Resolved by
            :func:`~model_migration_kit.evidence.resolve_evidence`, so a
            directory and a missing path mean here exactly what they mean to
            ``ReportModel.from_evidence``.

    Returns:
        One :class:`RunPoint` per ``migkit.comparison`` record, in log order. A
        comparison with no verdict after it yields a point with ``verdict is
        None`` rather than being dropped -- a run that died between the two
        records happened, and a timeline that omits it is a timeline that
        under-reports crashes.

        A log with no comparison at all yields ``()``. That is deliberately not
        an error at this layer: ``ReportModel.from_evidence`` refuses such a log
        because a *report* needs something to report on, while a series of zero
        runs is a legible answer to "what has run so far".

    Raises:
        ArtifactError: if the path names no file. rigor reads a missing log as an
            empty one, so a typo would otherwise be indistinguishable from an
            empty series and would render as a valid report of a run that never
            happened.
        EvidenceError: if the log is malformed anywhere but its final line. A
            torn last line is dropped -- see
            :func:`~model_migration_kit.evidence.stream_records`.

    **A point is built when its comparison is read, not when its verdict is.**
    :class:`SeriesBuilder` therefore holds nothing but the finished points, and
    the reader never has more than one payload alive at a time. The obvious
    alternative --
    hold each comparison payload until its verdict turns up, then build the point
    once from both -- is a payload queue, and on a log of 5,000 comparisons whose
    verdicts all arrive at the end it peaked at 114 MB against a 31 MB log, where
    the points alone cost 17 MB. That is the same amplification
    :func:`~model_migration_kit.evidence.stream_records` exists to remove,
    reintroduced one layer up. It also removes an argument: because a point takes
    its place in the list the moment its comparison is read, the returned order is
    the comparisons' order by construction rather than by reasoning about when
    each one took its verdict.
    """
    builder = SeriesBuilder()
    for record in stream_records(resolve_evidence(evidence)):
        builder.add(record)
    return builder.points()


def _decided(point: RunPoint, verdict: Mapping[str, Any]) -> RunPoint:
    """``point``, with the verdict record's two fields filled in.

    A verdict payload feeds exactly two fields of a point and nothing else reads
    it, so taking a verdict is a substitution rather than a rebuild -- which is
    what lets :func:`read_series` build points eagerly and hold no payloads. It
    is also what makes a second verdict cheap to honour: the two fields are
    written again, over whatever the first one left there.

    The two values are lifted from a throwaway point rather than read out of the
    mapping here, so that this function knows *which* fields the verdict feeds and
    :func:`run_point` remains the only code that knows *how* to read them. An
    absent key, an empty string and a non-mapping payload are then decided in one
    place, and a change to that decision cannot land in the report while leaving
    the series behind.
    """
    decided = run_point({}, verdict)
    return replace(point, verdict=decided.verdict, reason=decided.reason)


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


def parse_created(value: str) -> datetime | None:
    """The instant a ``created`` string names, or ``None`` when it names none.

    Public, and the only timestamp parser in this package, because the timeline
    needs the parsed instant rather than merely the predicate below -- an x-axis
    that is time has to subtract two of these. A second parser would be free to
    disagree with this one about either normalisation, and the disagreement would
    surface as a run that is dated in the table and undated in the chart.

    A trailing ``Z`` is normalised for the parse and deliberately not for the
    stored value. ``fromisoformat`` learned to accept ``Z`` only in 3.11, and this
    package supports 3.10, so without the normalisation one evidence log would
    yield a dated point on one interpreter and an undated one on another.

    A timestamp carrying no offset is read as UTC. Every timestamp this package
    writes carries one (``contracts.utc_now``), so this is about logs written by
    something else -- and the alternative is worse than a guess: Python refuses to
    compare a naive datetime with an aware one, so a single offsetless record in a
    log full of ``+00:00`` ones would raise ``TypeError`` out of a sort rather
    than misplace one marker.
    """
    if not value:
        return None
    normalised = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _is_timestamp(value: str) -> bool:
    """Whether :func:`parse_created` can place this value on a timeline."""
    return parse_created(value) is not None


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


# --------------------------------------------------------------------------- #
# C11: the counterfactual spot check
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SpotCheck:
    """What an engineer sampling ``k`` prompts by hand would probably have seen.

    The report's other numbers say what this run measured. This one says what a
    cheaper method would have *missed*, which is the argument for having run the
    harness at all -- and it is therefore the number a sceptical reader will
    check first, so every choice behind it is written down rather than inferred.

    Frozen, and carrying its own inputs beside its answer, for the reason
    :class:`RunPoint` is: the sentence and the counts it was computed from must
    travel together. A reader who wants to redo the arithmetic can, from this
    object alone, without trusting the prose.
    """

    #: How many prompts the hypothetical spot check tries.
    k: int
    #: ``N`` -- every item in the set, failing and unstable ones included.
    items: int
    #: ``F`` -- items failing under the candidate. Only these can be "found".
    failing: int
    #: Items that were neither reliably passing nor reliably failing. Counted
    #: into ``items`` and *not* into ``failing``; see :func:`spot_check`.
    unstable: int
    #: ``P(a k-item draw contains none of the F failing items)``.
    probability: float
    #: The same fact in one sentence, with its assumption named in it.
    sentence: str


def spot_check(
    items_passing: int, items_failing: int, items_unstable: int, *, k: int = 12
) -> SpotCheck | None:
    """The chance a ``k``-prompt hand check of this set would have seen nothing.

    ``comb(N - F, k) / comb(N, k)``: the probability that ``k`` items drawn
    without replacement miss every failing one. Hypergeometric, and no more than
    that -- it is not a power calculation, needs no effect size and no target
    power, and the ``n_required`` the record already carries does not appear in
    it and cannot be made to.

    **The unit is items, never completions.** ``k`` prompts is ``k`` *decisions*,
    not ``k`` samples from a completion-level pass rate. At temperature 0 -- and
    under the fake adapter, where a mapped prompt returns one fixed string -- all
    ``n`` draws of an item are identical, so 60 completions are 12 decisions.
    Writing this as ``rate ** k`` is the obvious implementation and it is wrong,
    but not by the margin this docstring used to claim. Two different errors get
    conflated here and only one of them is the error actually on offer:

    * **With replacement, at the same item rate.** ``(88 / 96) ** 12 == 0.3520``
      against the correct ``0.3288``. That *over*states by about 7%, in the
      direction that flatters this tool -- a higher number is a blinder spot
      check. This is the one a real implementer commits, and it is the one the
      plan itself committed: 0.351 sat in C11's own edge table as the expected
      value, which is ``(88 / 96) ** 12`` and not the hypergeometric the same
      contract specifies.
    * **A completion rate of 0.75.** ``0.75 ** 12 == 0.0317``, an order of
      magnitude below 0.3288. This is the figure usually quoted as the danger
      and it cannot arise here: the determinism above forbids it. If all ``n``
      draws of an item are identical then the completion pass rate *equals* the
      item pass rate, so a run whose item rate is 88/96 cannot have a completion
      rate of 0.75, and the two readings cannot diverge that far.

    The 7% is what makes the real error dangerous, not a factor of ten. 35% and
    33% both look plausible printed in a sentence, so nothing in the output says
    which arithmetic produced it -- a "must not" is not self-enforcing, and the
    place to check whether it was obeyed is the expected value, not the prose.
    A tool whose whole argument is that naive methods are blind must not compute
    its own headline number by a naive method.

    **Unstable items are excluded from ``F``, and that raises this number.** An
    item that fails on some draws and not others has not been *established* as a
    regression, and ``F`` is the count of regressions this run established. The
    tool does not claim regressions it has not established, so an unstable item
    does not enter ``F``. That is an honesty claim about ``F``, and it is the
    only ground this rule stands on.

    Say plainly which way it runs, because it does not run the way a reader
    expects. A smaller ``F`` means a *larger* probability: the spot check looks
    blinder, and a blinder spot check is a stronger argument for having run this
    harness. So the rule raises the quoted number and strengthens the tool's own
    case. It is not a restraint on the number and must not be described as one.
    Counting unstable items as failures would take ``F`` from 8 to 11 on the
    demo's set and the probability from 33% to 21% -- a *weaker* claim, not a
    more quotable one. The reason it is not done is that it would assert eight
    plus three regressions on evidence for eight.

    **The sentence names the assumption it made.** A real spot check is not a
    random draw: an engineer picks twelve prompts they believe are
    representative, and nobody can model that. So the sentence says "drawn at
    random" out loud and lets the reader discount it, rather than quietly
    claiming to have modelled a human's judgement. What it counts is *checks*
    and never *runs*: nothing here is distributed over runs, and a director who
    reads "in X% of runs" and asks what a run is has found a hole.

    Two further things the wording has to get right. It ends "in X% **of such
    checks**", not "of spot checks", because "a spot check ... in X% of spot
    checks" puts a singular subject inside its own plural denominator and eats
    its own tail. And it names ``F`` in place -- "these 96 items, 8 of which
    failed" -- because the first question a director asks the sentence is "out
    of how many?", and a line whose answer lives only in a neighbouring field is
    a line that cannot be checked where it is read.

    Returns ``None`` -- no sentence at all -- rather than a weaker one, when:

    * ``F == 0``. There was nothing to miss, so "a spot check would have found
      nothing" is true and vacuous. The temptation is to print the line anyway
      because it is the most quoted line in the document; a line that is
      unfalsifiable on this run is worth less than the silence it replaces.
    * ``N <= k``. The check would read every item, and a draw that takes the
      whole set is a **census, not a spot check**. The sentence's entire force is
      that you only looked at a few; calling a census a spot check is an
      overclaim, in the one function written to prevent overclaiming. At
      ``N == k`` the arithmetic is not even wrong -- ``comb(N - F, k)`` is 0 and
      the probability is a true 0.0 -- which is what makes it worth excluding
      explicitly: it would render a confident, correct-looking sentence about a
      procedure nobody would call a spot check. The contract said ``N < k``; this
      is a deliberate amendment out of review, and the blind suite's assertion
      that ``N == k`` is *not* excluded was changed with it.
    * ``N == 0``. There is no set to sample.

    ``k == 0`` is a caller's bug, not a run without failures, so it raises rather
    than joining the ``None`` cases: a zero-prompt spot check trivially sees
    nothing, and silently returning ``None`` would hide a miswired caller behind
    a result the report already knows how to render as an absence.
    """
    if k <= 0:
        raise ValueError(f"k must be a positive number of prompts, got {k!r}")
    if items_passing < 0 or items_failing < 0 or items_unstable < 0:
        raise ValueError(
            "item counts cannot be negative, got "
            f"passing={items_passing!r}, failing={items_failing!r}, "
            f"unstable={items_unstable!r}"
        )

    items = items_passing + items_failing + items_unstable
    if items == 0 or items <= k or items_failing == 0:
        return None

    # math.comb, not a float product: the exact integer arithmetic costs nothing
    # here and cannot drift, and a reviewer checking this line should find the
    # textbook expression rather than something they have to re-derive.
    probability = math.comb(items - items_failing, k) / math.comb(items, k)
    sentence = (
        f"A {k}-prompt spot check drawn at random from these {items} items, "
        f"{items_failing} of which failed, would have shown no failures at all "
        f"in {_percent(probability)} of such checks."
    )
    return SpotCheck(
        k=k,
        items=items,
        failing=items_failing,
        unstable=items_unstable,
        probability=probability,
        sentence=sentence,
    )


def _percent(probability: float) -> str:
    """A probability as a whole-percent phrase, with both ends kept honest.

    Whole percent because the sentence is read aloud in a review, and 32.9% is
    a precision this estimate does not have: it assumes a random draw that no
    engineer actually performs.

    "Less than 1%", not "fewer than": a probability is a proportion, not a count
    of things, and "fewer" wants a count noun. This phrase can land in the
    most-quoted sentence in the document, where a grammatical slip reads as a
    mistake in the arithmetic beside it.

    The two guards exist because both ends of this scale assert a **certainty
    the arithmetic did not compute**, and that is the whole of the reason. A
    genuinely non-zero probability rounded down to "0%" says a spot check would
    *always* have caught this; a probability below 1 rounded up to "100%" says it
    could *never* have. Neither is a thing ``comb(N - F, k) / comb(N, k)``
    returned, and a sentence that is quoted in a review must not put a certainty
    in the reader's mouth that the calculation stopped short of.

    Note that the two ends do not flatter the same party -- this docstring used
    to claim they did. "0%" undercuts the tool, since a spot check that always
    catches the regression is an argument against having run the harness; "100%"
    flatters it. They are wrong in opposite directions, which is exactly why the
    symmetric reason is the one that holds: it is not about which way the error
    leans, it is that neither end was computed.

    ``probability == 1`` is unreachable from :func:`spot_check` -- it needs
    ``F == 0``, which returns ``None`` -- so the second guard is a plain
    ``percent == 100`` and carries no dead test for it.
    """
    percent = round(probability * 100)
    if percent == 0 and probability > 0:
        return "less than 1%"
    if percent == 100:
        return "more than 99%"
    return f"{percent}%"
