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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from opik_rigor import EvidenceRecord

from .comparison import holm_bonferroni
from .contracts import EVENT_COMPARISON, EVENT_VERDICT
from .evidence import resolve_evidence, stream_records

__all__ = [
    "NO_PREVIOUS_RUN",
    "UNRECORDED",
    "Candidate",
    "CandidateField",
    "CandidateLineage",
    "Caveat",
    "ComparabilityKey",
    "Exclusion",
    "Multiplicity",
    "ParameterChange",
    "Partition",
    "RunPoint",
    "SeriesBuilder",
    "SpotCheck",
    "SpotCheckSubject",
    "Succession",
    "Trend",
    "candidate_field",
    "comparability_key",
    "correct_field",
    "parameter_strip",
    "parse_created",
    "partition_comparable",
    "read_series",
    "run_point",
    "spot_check",
    "trend",
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


# --------------------------------------------------------------------------- #
# comparability: which points may share a table
# --------------------------------------------------------------------------- #

#: Both hashes are rendered at this width, and the number is not free: it is the
#: width ``comparison._require_comparable`` already uses in the two errors it
#: raises on the same two fields. A report that truncated at 12 while the
#: exception truncated at 16 would print two different-looking prefixes of one
#: hash on one page, and a reader comparing them would conclude the run changed.
_HASH_WIDTH = 16

#: What any of the key's fields reads as when the run never recorded one -- a
#: hash, a draw count, a baseline model id. Spelled out rather than left blank
#: because these strings are read by a person: "against the group's " with
#: nothing after it looks like a formatting bug, not like a missing fact. One
#: word for all three so that a page cannot describe the same absence three ways.
#:
#: **Public, and it was private until R24.6.** A template that wants to style an
#: unrecorded cell differently from a recorded one -- greyed, italic, given a
#: title attribute -- had two options, importing a private name or hard-coding
#: the literal, and the paragraph above forbids the second: the whole point of
#: one word for every absence is lost the moment a second file spells it itself
#: and the two drift. ``report.py`` sets the opposite precedent deliberately with
#: ``THRESHOLD_SOURCE_UNRECORDED`` and ``INTERVAL_BAR_NO_RATE``, and R7 ruled the
#: general case: import the constant, never hard-code its value. Promoted before
#: C14 types against it, because a rename after that is a rename across chunks.
UNRECORDED = "unrecorded"


@dataclass(frozen=True)
class ComparabilityKey:
    """The four fields that decide whether two runs may be read against each other.

    A strict subset of what ``comparison._require_comparable`` checks, and the
    subset is forced rather than chosen: that guard takes two live
    :class:`~model_migration_kit.artifacts.JudgedArtifact` objects, the report has
    payloads, and the artifacts are usually gone by the time anything renders. So
    this is a second, narrower predicate over what a payload actually carries.

    The gap is coverage, and it is named here rather than papered over: two runs
    with matching hashes and matching ``n_per_item`` can still have judged
    different numbers of completions, because a truncated run carries the right
    hashes. That difference cannot exclude -- nothing in a payload proves it --
    so :func:`partition_comparable` raises it as a :class:`Caveat` instead.

    The key is also silent about two refusals ``_require_comparable`` makes and
    :func:`partition_comparable` has to make elsewhere: a comparison in which
    neither side was graded at all (:func:`_ungraded`), and a run whose two sides
    are the same model, which is excluded there and merely named here because the
    A/A calibration run is logged deliberately.

    **Equality here is deliberately naive, and that is a trap for callers who
    group on it.** Two keys with empty hashes compare equal, because a frozen
    dataclass compares field by field and nothing else would be a sane ``__eq__``.
    An unrecorded field is not a match -- see :attr:`is_identifying` and
    :func:`partition_comparable`, which is where that rule is enforced.
    """

    goldenset_hash: str
    judges_hash: str
    n_per_item: int
    baseline_model: str

    @property
    def is_identifying(self) -> bool:
        """Whether this key identifies a group at all, over all four of its fields.

        A key with an unrecorded field identifies nothing: it says only that two
        logs were equally silent there. Anything that groups on
        :class:`ComparabilityKey` needs this, because dataclass equality alone
        will happily merge every run that recorded nothing into one
        confident-looking group -- ``"" == ""`` and ``0 == 0`` both read
        perfectly and both mean "neither of us said".

        **This tracks the exclusion rules in :func:`_incomparable`, and it has to
        be changed whenever they are.** It is not an independent opinion about
        what a key needs; it is the same four "was this recorded" questions the
        partition asks, answered ahead of time for a caller that has to *build*
        groups before it can partition them. The two must give one answer, so
        they share one emptiness test -- :func:`_recorded`, ``.strip()``
        included -- rather than each spelling out its own. If they can drift they
        will, and the drift is silent: a group vouched for here whose every
        member the partition then removes is a table that renders empty with no
        sentence anywhere saying why.

        There is a test whose whole job is to catch that drift, over every
        combination of the four fields, and adding a fifth ground to
        :func:`_incomparable` without adding it here is meant to turn it red.

        **Named for the question and not for two of its four fields.** It was
        ``hashes_recorded`` while only the hashes could exclude. Once
        ``n_per_item`` and ``baseline_model`` could, that name described the
        implementation of a stale version of the rule while the docstring
        described the question -- and a property that answers a question nobody
        is asking, sitting beside four rules that answer the real one, invites
        precisely the reading that makes it a lie.

        This says nothing about :func:`_ungraded`, which is not a rule about the
        key: a key cannot see what a run graded, which is why that check takes a
        whole :class:`RunPoint`. A caller still has to partition. What this
        promises is that grouping on an identifying key is not futile, not that
        every member survives.
        """
        return (
            _recorded(self.goldenset_hash)
            and _recorded(self.judges_hash)
            and self.n_per_item > 0
            and _recorded(self.baseline_model)
        )


@dataclass(frozen=True)
class Exclusion:
    """One point kept out of a comparison, with the sentence that says why.

    The reason is carried beside the point rather than logged or dropped because
    of the failure this whole chunk exists to prevent: "a table that quietly
    compares a 60-item run against a 40-item run is worse than no table". A point
    that vanishes with no sentence is exactly that table. The reason names the
    field *and both values*, so a reader can act on it -- "not comparable" is a
    verdict, and the reader needed the evidence.
    """

    point: RunPoint
    reason: str


@dataclass(frozen=True)
class Caveat:
    """A note attached to a point that is *kept*, not a reason for removing it.

    Some differences are worth printing and not worth excluding on. A run that
    graded fewer completions on one side than the other may have been truncated,
    or may simply have lost a few judge replies to parse failures -- the payload
    cannot tell those apart, and excluding on a suspicion would silently shrink
    the field on evidence that does not support it. A run whose two sides are the
    same model is the A/A calibration run the pipeline explicitly permits
    (``allow_same_model=True``), and excluding it would remove the one row that
    tells a reader what "no difference" looks like on this panel. So the point
    stays in ``kept`` and carries this instead. A point carrying a caveat appears
    in both returned tuples; anything that treats one as a removal will drop a
    run twice over. One point may carry more than one.

    **Named ``Caveat`` and deliberately not ``Flag``.** ``Flag`` collides with
    ``enum.Flag``, which a rendering chunk downstream could import and shadow this
    with in silence, and it would share a page with ``spread_flagged`` and
    ``RunPoint.warnings`` -- three concepts and one word. ``Exclusion`` names an
    outcome, so its twin should too: removed, versus kept with a note.

    **``point`` is ``None`` when the note is about the comparison as a whole**,
    which R21.5's assumed-lineage note is and which every note C4 raises is not.
    The widening is small and the alternative was worse: a claim about *how the
    line was assembled* pinned to whichever run happened to anchor it renders as
    a note about that run, and a reader would take a statement about the whole
    chart for a measurement of one night. That is this document's central rule --
    an absence must not render as a measurement -- reached from the rendering
    side, and a field that can say "no one run" is what keeps it. The field stays
    required and stays first, so no note loses its point by being built with an
    argument forgotten; a note with no point is one written that way on purpose.
    A renderer that walks caveats into rows must therefore ask, and print a
    ``point``-less note where the line is described rather than where a night is.
    """

    #: The run this note is about, or ``None`` when it is about the line itself.
    point: RunPoint | None
    reason: str


class Partition(NamedTuple):
    """What :func:`partition_comparable` returns: two outcomes and the notes.

    A ``NamedTuple`` rather than a bare three-tuple because the third element is
    the one a caller forgets. A contract that tells the caller about an *absence*
    -- a run kept only with a caveat, a run removed -- through a return type with
    room only for presences is a contract whose warning is unpacked into
    ``_flagged`` and dropped, and this is the third place in this plan that shape
    turned up. Naming the fields costs nothing at the call sites that already
    unpack positionally: this compares equal to the plain tuple, passes
    ``isinstance(x, tuple)``, has ``len() == 3`` and unpacks in the same order.
    """

    kept: tuple[RunPoint, ...]
    excluded: tuple[Exclusion, ...]
    caveats: tuple[Caveat, ...]


def comparability_key(point: RunPoint) -> ComparabilityKey:
    """The comparability key of one point, extracted and not judged.

    Deliberately total: every point has a key, including one whose hashes are
    empty. Refusing to build a key for an unrecorded hash would move the
    empty-hash rule into four callers instead of one, and the one place it
    belongs is :func:`partition_comparable`, which has both sides to name.
    """
    return ComparabilityKey(
        goldenset_hash=point.goldenset_hash,
        judges_hash=point.judges_hash,
        n_per_item=point.n_per_item,
        baseline_model=point.baseline_model,
    )


def partition_comparable(points: Sequence[RunPoint], *, against: ComparabilityKey) -> Partition:
    """Split ``points`` into those that may be tabled against ``against``, and why not.

    Args:
        points: The candidate points, in the order they should render.
        against: The key the group is defined by -- normally
            :func:`comparability_key` of whichever point anchors the table.

    Returns:
        A :class:`Partition`. ``kept`` and ``excluded`` are disjoint and each
        preserves input order; ``caveats`` annotates points that are *in*
        ``kept``, in that order, and one point may carry more than one. Three
        tuples rather than two because a caveat has nowhere else to live:
        :class:`RunPoint` is frozen and has no field for one, and adding one
        would mean editing the producer while its consumers are in flight.

    Every returned sequence is a tuple built by appending, never a set or a dict
    view. The rendered list has to be stable between renders of the same log, and
    set iteration order is stable only by accident of hashing. Nothing is
    de-duplicated on either side, for the reason ``read_series`` does not
    de-duplicate either: two identical runs are two runs, and three identical
    exclusions are three rows missing from the table rather than one.

    **An unrecorded value never matches, including another unrecorded one.** Two
    logs that both failed to record a golden set have equal keys under ``==`` and
    are not comparable: equality there means "both silent", not "both the same
    set". The same is true of a depth nobody recorded and a baseline nobody
    recorded -- ``0 == 0`` and ``"" == ""`` read perfectly and say nothing -- and
    it is true a third time of :func:`_ungraded`, over a field the key does not
    carry at all. Those are the lines a naive ``==`` gets wrong while looking
    right, and each is checked before the comparison beside it.

    The two questions are asked in that order: whether the *group* may hold this
    point, then whether the point compares anything at all. That is
    ``_require_comparable``'s own order -- it checks the hashes before it looks at
    coverage -- so a run that is wrong in both ways is blamed here for the same
    thing the CLI blames it for.
    """
    kept: list[RunPoint] = []
    excluded: list[Exclusion] = []
    caveats: list[Caveat] = []
    for point in points:
        reason = _incomparable(comparability_key(point), against) or _ungraded(point)
        if reason is None:
            kept.append(point)
            caveats.extend(_caveats(point))
        else:
            excluded.append(Exclusion(point=point, reason=reason))
    return Partition(tuple(kept), tuple(excluded), tuple(caveats))


def _incomparable(key: ComparabilityKey, against: ComparabilityKey) -> str | None:
    """Why ``key`` may not be tabled against ``against``, or ``None`` if it may.

    Fields are tested in ``_require_comparable``'s own order -- golden set, then
    judges, then how much was drawn -- so that when a run differs in several ways
    at once the two guards blame the same one, and a reader who has seen the CLI
    error recognises the report's sentence.

    **"Unrecorded" and "differs" are asked field by field, not in two sweeps.**
    An earlier arrangement checked *both* hashes for absence before testing
    *either* for a mismatch, and the docstring above was false while it did: a run
    against an edited golden set, written by a pipeline version that recorded no
    panel hash, was blamed here for the judges and by ``_require_comparable`` for
    the golden set. That is not a contrived pair; it is what upgrading the
    pipeline mid-week looks like. So each field is finished before the next is
    begun.
    """
    if not _recorded(key.goldenset_hash) or not _recorded(against.goldenset_hash):
        return _unrecorded_hash("golden-set", key.goldenset_hash, against.goldenset_hash)
    if key.goldenset_hash != against.goldenset_hash:
        return (
            f"excluded: golden set {_hash(key.goldenset_hash)} against the group's "
            f"{_hash(against.goldenset_hash)}. Model A on one set and model B on another "
            f"are two unrelated numbers, not a migration decision."
        )
    if not _recorded(key.judges_hash) or not _recorded(against.judges_hash):
        return _unrecorded_hash("judges", key.judges_hash, against.judges_hash)
    if key.judges_hash != against.judges_hash:
        return (
            f"excluded: judge panel {_hash(key.judges_hash)} against the group's "
            f"{_hash(against.judges_hash)}. Two panels are two instruments, and the "
            f"difference between them would measure the judges rather than the models."
        )
    if key.n_per_item <= 0 or against.n_per_item <= 0:
        return (
            f"excluded: no draws per item recorded for "
            f"{_silent_side(key.n_per_item > 0, against.n_per_item > 0)} -- "
            f"{_depth(key.n_per_item)} against the group's {_depth(against.n_per_item)}. "
            f"``n_per_item`` is 0 when the payload never carried one, so a run whose "
            f"depth nobody recorded and a run that really drew nothing are the same "
            f"number here. Two unknown depths presented as one is the 60-item run "
            f"tabled against the 40-item run, which is the table this refuses."
        )
    if key.n_per_item != against.n_per_item:
        return (
            f"excluded: {key.n_per_item} draws per item against the group's "
            f"{against.n_per_item}. The two runs sampled to different depths, so the "
            f"quieter number is the smaller sample and not the steadier model."
        )
    if not _recorded(key.baseline_model) or not _recorded(against.baseline_model):
        return (
            f"excluded: no baseline model recorded for "
            f"{_silent_side(_recorded(key.baseline_model), _recorded(against.baseline_model))}"
            f" -- {key.baseline_model.strip() or UNRECORDED} against the group's "
            f"{against.baseline_model.strip() or UNRECORDED}. Two runs whose baseline "
            f"nobody wrote down were not shown to have been measured from the same one, "
            f"and a column of deltas is a column only if they were."
        )
    if key.baseline_model != against.baseline_model:
        return (
            f"excluded: baseline {key.baseline_model} against the group's "
            f"{against.baseline_model}. A column of deltas measured from "
            f"two different baselines is not a column."
        )
    return None


def _unrecorded_hash(label: str, mine: str, theirs: str) -> str:
    """The hole the reviewer hunts for, closed in one place.

    ``"" == ""`` is ``True`` and means nothing here. Either side missing a hash
    ends the question: no evidence exists that the two runs saw the same golden
    set or the same panel, and absence of evidence rendered as a match is the
    table this chunk exists to prevent.

    The closing sentence is chosen from the case, because the two cases are
    different claims and only one of them used to be printed. "Two runs that both
    failed to record one are equally silent" is a statement *about this pair*, and
    on the commoner case -- one hash recorded, one not -- it is simply false.
    """
    if not _recorded(mine) and not _recorded(theirs):
        side = "either run"
        closing = (
            "Two runs that both failed to record one are equally silent, which is not "
            "the same fact as having been judged against the same one."
        )
    else:
        side = "this run" if not _recorded(mine) else "the group"
        closing = (
            "A hash on one side and none on the other is no evidence that the two were "
            "judged against the same one, which is what sharing a table claims."
        )
    return (
        f"excluded: no {label} hash recorded for {side} -- {_hash(mine)} against the "
        f"group's {_hash(theirs)}. {closing}"
    )


def _ungraded(point: RunPoint) -> str | None:
    """Why a point that *matches* the group still compares nothing, or ``None``.

    The one refusal ``_require_comparable`` makes on a field the key cannot carry
    at all: "neither artifact contains a judged completion, so there is nothing to
    compare. An empty comparison must not resolve to a verdict." A point with
    ``judged_baseline == judged_candidate == 0`` is that comparison, and it slips
    past every ``!=`` in this module because ``0 != 0`` is false -- the empty-hash
    hole in a third costume, where ``0 == 0`` means "both sides silent" rather
    than "both sides the same". Left alone it renders as an ordinary row: no pass
    rate, no floor, and nothing on the page saying so.

    **One side at zero is excluded too, and for a reason of wording as much as of
    principle.** The coverage caveat's own sentence says the gap "may be lost
    judge replies rather than a truncated side". A side that graded nothing did
    not lose a few replies, so keeping this as a caveat would print a reason that
    is not true of the run it is printed beside. ``_require_comparable`` refuses
    the same pair, by the other route: a side with no judged completion cannot
    have the same coverage map as one that has them.
    """
    if point.judged_baseline <= 0 and point.judged_candidate <= 0:
        return (
            "excluded: neither side of this run holds a judged completion, so there is "
            "nothing to compare. An empty comparison must not resolve to a verdict, and "
            "a row with no pass rate and no floor behind it is a verdict drawn from "
            "nothing at all."
        )
    if point.judged_baseline <= 0 or point.judged_candidate <= 0:
        return (
            f"excluded: {point.judged_baseline} graded on the baseline against "
            f"{point.judged_candidate} on the candidate, and a side that graded nothing "
            f"has nothing for the other to be compared against. This is not the "
            f"shortfall the coverage caveat describes: lost judge replies cost a run a "
            f"few completions, not all of them."
        )
    return None


def _caveats(point: RunPoint) -> tuple[Caveat, ...]:
    """Every note a *kept* point carries, in a fixed order, possibly none.

    Coverage first, then self-comparison: the first is a doubt about the numbers
    on the row and the second is a statement about what the row is for, and a
    reader wants the doubt first.
    """
    notes: list[Caveat] = []
    if point.judged_baseline != point.judged_candidate:
        notes.append(Caveat(point=point, reason=_uneven_coverage(point)))
    if point.baseline_model == point.candidate_model:
        notes.append(Caveat(point=point, reason=_self_comparison(point)))
    return tuple(notes)


def _uneven_coverage(point: RunPoint) -> str:
    """A run whose two sides were not graded the same number of times.

    This is the one check ``_require_comparable`` makes that a key cannot: it
    compares the baseline artifact against the candidate artifact of *one*
    comparison, key by key, because matching hashes do not make a truncated
    artifact comparable -- a baseline that died at 25 completions against a
    candidate that finished 200 carries exactly the right hashes and flatters
    whichever side finished. A payload cannot reproduce that key-by-key check,
    but it does record how many completions each side was graded on, and a
    difference between those two numbers is the shortfall showing through.

    **It is a caveat rather than an exclusion**, because the payload cannot tell a
    truncated run from a run that simply lost a few judge replies, and a
    shortfall is already surfaced by ``Completeness``. Excluding a run on a
    suspicion the evidence does not settle would silently shrink the field. That
    argument holds only while both sides graded *something*, which is why
    :func:`_ungraded` runs first and takes the zeroes away.

    These are ``judged_*``, the judge's own ``n``, and the wording below says
    *graded* for the reason that field's docstring exists: a completion that was
    produced and whose judge reply would not parse is counted by neither, so
    calling this "completions" would re-commit the exact conflation the point
    type went out of its way to separate.
    """
    return (
        f"flagged: {point.judged_baseline} graded on the baseline against "
        f"{point.judged_candidate} on the candidate. Graded is the judge's own "
        f"count, not the completions the run produced, so the gap may be lost "
        f"judge replies rather than a truncated side -- the point is kept "
        f"because a shortfall is already surfaced by Completeness."
    )


def _self_comparison(point: RunPoint) -> str:
    """A run whose two sides are the same model.

    ``_require_comparable`` refuses this outright unless it is told not to, and
    the reason it gives is the whole of it: a model compared against itself always
    looks safe. But it *is* told not to -- ``allow_same_model=True`` is how the A/A
    calibration run is logged, and that run is legitimate, deliberate, and the one
    row on the page that shows what "no difference" measures like on this panel.
    Excluding it would delete the control.

    So it is named and kept. What must not happen is the third thing: admitting it
    silently, as an ordinary row whose flat delta a reader takes for a result.
    Which of the two it is -- calibration or a mis-wired config -- is not in the
    payload, so this says both and leaves the choice to whoever is rendering.
    """
    return (
        f"flagged: both sides of this run are {point.baseline_model}. A model compared "
        f"against itself always looks safe, which is why the pipeline refuses one "
        f"unless it is passed allow_same_model=True. This row is therefore either the "
        f"A/A calibration run, which is worth reading and worth labelling, or a "
        f"mis-wired config -- the payload does not record which, so the point is kept "
        f"and named rather than dropped."
    )


def _recorded(value: str) -> bool:
    """Whether a field holds a value rather than an admission that it holds none.

    ``.strip()`` because a writer that padded the field recorded nothing either,
    and this is the one place the difference between ``""`` and ``"  "`` decides
    whether an absence is read as a match. Essentially unreachable and one call
    wide.
    """
    return bool(value.strip())


def _depth(draws: int) -> str:
    """``n_per_item`` as it is printed, with ``0`` spelled out as the absence it is."""
    return str(draws) if draws > 0 else UNRECORDED


def _silent_side(mine: bool, theirs: bool) -> str:
    """Which of the two sides failed to record the field, named for a reader."""
    if not mine and not theirs:
        return "either run"
    return "this run" if not mine else "the group"


def _hash(value: str) -> str:
    """One hash as it is printed: 16 characters, or the word for having none."""
    return value[:_HASH_WIDTH] if _recorded(value) else UNRECORDED


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
# C5: several candidates, one baseline, one table
# --------------------------------------------------------------------------- #

#: How far apart a field's oldest and newest run may sit before the field is
#: flagged, in days. It is the *default* of a parameter and never a literal in
#: the comparison below, because where the line falls is a property of a team's
#: cadence rather than of this arithmetic. Seven days is the short end of the
#: spec's own judgement -- runs "compared three weeks apart are not a fair
#: field" -- and one week is about as long as a hosted model, a prompt file and
#: a golden set stay plausibly the same three things. A nightly pipeline is
#: entitled to tighten it and a quarterly one to widen it; neither should have
#: to edit this module to do so.
_STALE_AFTER_DAYS = 7.0

#: Seconds in a day, named because every duration this section reports is in
#: days and ``86400`` written inline four times is four chances to write 84600.
_SECONDS_PER_DAY = 86_400.0

#: Where a run with no readable ``created`` sorts: before every dated run, so
#: that "the newest point" never resolves to a point that is not on the timeline
#: at all. It is a sort position and never an operand -- an age measured from
#: here would be two thousand years, and two thousand years is a number a
#: renderer would print.
#:
#: **:func:`_anchor` ranks undated runs the other way -- last -- and the two do
#: not disagree.** This decides *display order in a table*, where a reader can
#: see the blank ``stale_days`` cell beside the row and position asserts nothing;
#: that decides *which run defines a line's axis*, and an undated run winning
#: that election excludes every dated night in the log and draws nothing. Sort it
#: oldest; never let it anchor. R24.4, recorded in both places because an
#: asymmetry with no note is an asymmetry the next reader flattens.
_UNDATED = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Candidate:
    """One row of the candidate table: a run, its delta, and how stale it is.

    Frozen for :class:`RunPoint`'s reason -- a row that can be edited after the
    chart beside it was drawn is a row that can be made to disagree with it.

    The two derived numbers live here rather than on :class:`RunPoint` because
    both are statements about a *field* and not about a run: a delta needs the
    baseline that run measured, and an age is measured against the newest run in
    the table. Neither exists until the field does, and the same point moved into
    a different field has the same reading and a different age.
    """

    point: RunPoint
    #: Candidate pass rate minus baseline pass rate, in percentage points --
    #: ``(candidate - baseline) * 100``. ``None`` when either side is ``None``.
    #:
    #: **This is a subtraction and not a statistic.** Both operands were measured
    #: by rigor and recorded; nothing here estimates, pools or smooths them, and
    #: no interval belongs on this number -- an interval would be a claim about a
    #: sampling distribution that nothing in this module has, and it would be
    #: read as one.
    #:
    #: Both rates are read off the *same run* -- one judge, one golden set, one
    #: night. Subtracting this candidate's rate from the field's summary baseline
    #: instead would measure it against a baseline observed three weeks away from
    #: it, which is precisely the drift this chunk exists to expose rather than
    #: to commit. It follows that
    #: :attr:`CandidateField.baseline_pass_rate` **must not be added back to this
    #: number** to recover a pass rate: the two were measured on different runs
    #: whenever the baseline moved, and the sum is then a rate no run recorded.
    #:
    #: **Never rounded.** Rounding is a rendering decision and this is the model:
    #: a renderer that wants one decimal place can take one, and a model that has
    #: already rounded cannot give back what it dropped. Expect the ordinary
    #: binary-float residue -- ``9.999999999999998`` where the arithmetic says
    #: ten -- and format it at the edge.
    delta_pp: float | None
    #: This run's age against the newest run in the field, in days; ``0.0`` for
    #: the newest itself. ``None`` when this run carries no readable ``created``,
    #: and ``None`` for every row when no run in the field carries one. An
    #: undated run is neither fresh nor stale, and ``0.0`` here would say it was
    #: measured alongside the newest.
    stale_days: float | None

    @property
    def model(self) -> str:
        """The candidate model this row is about -- ``point.candidate_model``.

        A row of this table *is* a candidate model: it is what
        :func:`_newest_per_model` keys on, what the rows are ordered by, and what
        a run has to record to have a row at all -- see
        :func:`_unnamed_candidate`. Every consumer that joins anything onto a row
        joins it on this string, so reaching two attributes deep for the one
        field that identifies the row is a reach every call site would otherwise
        repeat.

        It is a property and not a dataclass field on purpose. There is exactly
        one candidate model on a row and it lives on the point, so copying it
        into a second slot would create a pair that can be made to disagree --
        the same reason :class:`Candidate` is frozen. This reads through.
        """
        return self.point.candidate_model


@dataclass(frozen=True)
class CandidateField:
    """Several candidates, one baseline, one key: everything one table renders from.

    The failure this type is shaped against is the one the spec names: "three
    candidates measured three weeks apart render as a fair field, with the
    baseline having drifted underneath them". Every field below is part of the
    answer. :attr:`key` says what the rows share; :attr:`excluded` says who is
    not in the table and why; :attr:`caveats` says which rows are in it under
    protest; :attr:`spread_days` and :attr:`spread_flagged` say whether the rows
    were measured close enough together to be read side by side at all, and
    :attr:`stale_after_days` says what "close enough" was taken to mean.
    """

    #: The key every candidate shares, and the reason this group was the one
    #: chosen. All four of its fields are recorded -- see
    #: :attr:`ComparabilityKey.is_identifying` -- so it identifies a group rather
    #: than merely collecting the runs that were equally silent.
    key: ComparabilityKey
    #: One row per candidate model, **ordered by** ``candidate_model``. Never by
    #: pass rate, delta or date: a table sorted by its result invites the reading
    #: that position *is* the result, and this is a set of measurements taken
    #: under one key, not a ranking. Alphabetical is also the same order on two
    #: renders of one log, which a sort by float stops being the moment two
    #: candidates tie.
    candidates: tuple[Candidate, ...]
    #: Every point in the log this table does not hold, each with the sentence
    #: saying why -- see :class:`Exclusion`. Runs under *other* keys are in here
    #: too, not only runs under this one: the whole log was partitioned against
    #: :attr:`key`, because a field that silently omitted a third of the log is
    #: the quietly-shrunk table :func:`partition_comparable` exists to prevent.
    excluded: tuple[Exclusion, ...]
    #: Notes on rows that *are* in the table -- see :class:`Caveat`. Carried up
    #: rather than dropped, because a caveat that reaches nobody is the same as a
    #: caveat never computed. Only notes on rendered rows: a note whose point is
    #: not in :attr:`candidates` has no row to be printed against.
    caveats: tuple[Caveat, ...]
    #: Newest minus oldest candidate in days, over the rows that carry a date.
    #: ``None`` unless **at least two** rows carry one -- not ``0.0``, which would
    #: claim the field was measured in a single sitting. One dated row and one
    #: undated one is a single observation, and a single observation cannot make
    #: that claim any more than no observation can.
    #:
    #: **Never rounded**, for :attr:`Candidate.delta_pp`'s reason: two runs eleven
    #: hours apart are not "0 days apart" on a page whose subject is whether they
    #: were measured close enough together to be read side by side, and a renderer
    #: that wants whole days can round while a model that rounded cannot undo it.
    spread_days: float | None
    #: Whether :attr:`spread_days` exceeds :attr:`stale_after_days`.
    #: ``False`` when :attr:`spread_days` is ``None``: an unmeasurable spread is
    #: not a measured narrow one, and what says so honestly is the absent number
    #: beside this flag rather than a ``False`` that reads as an all-clear.
    spread_flagged: bool
    #: The baseline's own pass rate on the *newest* run in the field -- see
    #: :func:`_baseline_pass_rate`, which reconstructs it, because
    #: :class:`RunPoint` records only the candidate side. ``None`` when that run's
    #: baseline-side counts do not describe a rate.
    #:
    #: **This is a header number and not an operand.** It is one reading, taken
    #: from one run; every :attr:`Candidate.delta_pp` below it is computed against
    #: the baseline *its own* run measured, so adding this number back to a delta
    #: does not recover that row's candidate pass rate -- it produces a rate no
    #: run recorded, whenever the baseline moved between the two nights. The
    #: newest is quoted because it is the most recent reading of the baseline this
    #: field has and a header has to come from some row; where the rows disagree
    #: about it, a caveat against this run says so in words -- see
    #: :func:`_drifted_baselines`, which is the check that makes the header safe
    #: to print beside deltas it is not the baseline of.
    baseline_pass_rate: float | None
    #: The window this field was built with, in days: the ``stale_after_days``
    #: :func:`candidate_field` was called with, default included.
    #:
    #: Carried rather than left on the call, because :attr:`spread_flagged` is a
    #: bare ``bool`` and a renderer holding only this field would otherwise have
    #: to name a number it cannot see. Both halves of "measured more than 7 days
    #: apart" would then be true of a field built with ``stale_after_days=30.0``
    #: and the sentence false -- which is the failure this chunk is shaped
    #: against, one layer down: a plausible line of prose no number on the page
    #: supports. The sentence has to be writable where the number is.
    stale_after_days: float


def candidate_field(
    points: Sequence[RunPoint], *, stale_after_days: float = _STALE_AFTER_DAYS
) -> CandidateField | None:
    """The one table a log can render: the widest field of candidates sharing a key.

    Args:
        points: Every point in the log, in the order it was read.
        stale_after_days: How far apart the field's oldest and newest run may sit
            before :attr:`CandidateField.spread_flagged`. Carried through onto
            :attr:`CandidateField.stale_after_days`, so that a renderer holding
            the field can name the window it is reporting against instead of
            printing this module's default beside somebody else's number.

    Returns:
        A :class:`CandidateField`, or ``None`` when no group holds two distinct
        candidate models. ``None`` rather than a one-row field is deliberate: the
        spec says a single candidate "collapses the table to a single row and it
        is not rendered as a table at all", and an absence in the model cannot be
        forgotten downstream the way a template ``{% if %}`` can.

    Points are grouped by :func:`comparability_key`. The key carries
    ``goldenset_hash``, ``judges_hash``, ``n_per_item`` and ``baseline_model``
    and has never carried ``candidate_model``, which is the only reason grouping
    on it produces a field at all: a key holding the candidate would make every
    group a group of one, this function would return ``None`` on every log ever
    written, and the table would never render.

    Only groups whose key :attr:`~ComparabilityKey.is_identifying` are eligible.
    A key with an unrecorded field says two logs were equally silent, not that
    they measured the same thing. **The guard does not change the answer, and its
    docstring used to claim it did.** Every member of such a group is removed by
    :func:`partition_comparable`, so the group's rank is ``(0, 0, _UNDATED)`` and
    it loses to any group that keeps a single named candidate; where *every*
    group is non-identifying, dropping the guard would elect one, keep nothing
    from it, and return ``None`` -- which is what happens with the guard too. It
    is kept because it is cheap, because it saves a partition pass over the whole
    log per silent key, and because it states at the point of selection which
    groups are groups at all. It is not what stops the biggest pile of silence in
    the log from winning; the rank is.

    Within the winning group the newest run per distinct ``candidate_model``
    survives, so a candidate compared twice is one row and not two. The run that
    lost leaves through :func:`_superseded` with a sentence, and a run with no
    recorded candidate model is excluded rather than merged -- see
    :func:`_unnamed_candidate`. Both are in :attr:`~CandidateField.excluded`:
    a run that was in the log and is in none of the three tuples has vanished
    silently, which is the quietly-shrunk table this pair of chunks exists to
    prevent.

    **Nothing this returns is rounded.** :attr:`Candidate.delta_pp` and
    :attr:`CandidateField.spread_days` are the unrounded results of the
    arithmetic, binary-float residue and all, because rounding is a rendering
    decision and a model that has already rounded cannot give back what it
    dropped.

    **The selection is total, and that is not a detail.** Largest field first,
    then most rows, then the group holding the newest point, then the key itself
    in sorted order. Every earlier rule can tie: two groups can hold the same
    number of runs *and* no dated run between them, and at that point the winner
    would fall out of dict insertion order over hash strings -- stable on one
    machine and not guaranteed across a rebuild. A stable arbitrary answer is
    worth more than a principled unstable one here, because the failure is a
    document that differs between two renders of one log.
    """
    chosen = _widest_field(points)
    if chosen is None:
        return None
    key, partition = chosen
    rendered = _newest_per_model(partition.kept)
    if len(rendered) < 2:
        return None
    dated = [moment for moment in map(_instant, rendered) if moment is not None]
    newest = max(dated) if dated else None
    spread = _days(max(dated), min(dated)) if len(dated) > 1 else None
    shown = {id(point) for point in rendered}
    anchor = _latest(rendered)
    return CandidateField(
        key=key,
        candidates=tuple(
            Candidate(
                point=point,
                delta_pp=_delta_pp(point),
                stale_days=_stale_days(point, newest),
            )
            for point in rendered
        ),
        excluded=_excluded(points, partition, rendered),
        caveats=(
            tuple(note for note in partition.caveats if id(note.point) in shown)
            + _drifted_baselines(anchor, rendered)
        ),
        spread_days=spread,
        spread_flagged=spread is not None and spread > stale_after_days,
        baseline_pass_rate=_baseline_pass_rate(anchor),
        stale_after_days=stale_after_days,
    )


def _widest_field(points: Sequence[RunPoint]) -> tuple[ComparabilityKey, Partition] | None:
    """The group that should be tabled, with the whole log partitioned against it.

    Each eligible key is partitioned against *every* point rather than against
    its own group, which costs a pass per key and buys the thing the field
    actually needs: an :class:`Exclusion` sentence for every run in the log that
    did not make the table. ``kept`` is identical either way -- a point survives
    exactly when its key equals this one and it graded something -- so the extra
    passes change nothing but what a reader is told.

    Ranking is by number of distinct candidate models first and row count second.
    The contract says "largest group", and on every group where each run is a
    different candidate the two readings are the same number. Where they differ,
    counting distinct models is the one that is not a trap: thirteen nightly runs
    of one candidate are the largest group in the log and collapse to a single
    row, so ranking by run count alone would hand the table to a group that
    cannot be a table and return ``None`` for a log with a perfectly good field
    beside it. Distinct-models-first has the property that matters -- if any
    eligible group can render a table, the group chosen here renders one -- and
    row count as the second term still gives the literal reading whenever the
    first term ties.

    **The :attr:`~ComparabilityKey.is_identifying` filter below is a pre-filter
    and not a rule.** A non-identifying key keeps nothing -- every member of its
    group is excluded by :func:`partition_comparable` on the very field that made
    the key non-identifying -- so it ranks ``(0, 0, _UNDATED)`` and is beaten by
    any group with one named candidate in it. Removing the filter changes no
    answer this function gives. It stays because it costs one property call and
    saves a whole-log partition pass per silent key, and because a selection that
    said nothing about which groups are groups would invite the next reader to
    add the check somewhere it *would* change an answer.
    """
    eligible: list[ComparabilityKey] = []
    for point in points:
        key = comparability_key(point)
        if key.is_identifying and key not in eligible:
            eligible.append(key)
    if not eligible:
        return None
    fields = [(key, partition_comparable(points, against=key)) for key in eligible]
    # Two stable sorts rather than one composite: the last tiebreak runs
    # ascending and everything above it runs descending, and `list.sort` keeps
    # the order of equal elements even under `reverse=True`.
    fields.sort(key=lambda field: _key_order(field[0]))
    fields.sort(key=lambda field: _field_rank(field[1]), reverse=True)
    return fields[0]


def _field_rank(partition: Partition) -> tuple[int, int, datetime]:
    """How good a candidate field this partition would make, largest first.

    The third element is the group's newest point, with an undated group sorting
    below every dated one. It is a tiebreak and not a measurement: a field is not
    better for being recent, it is merely the one to prefer when two are the same
    size, because the newer of two equal readings is the one a reader meant.
    """
    kept = partition.kept
    return (
        len({point.candidate_model for point in kept if _recorded(point.candidate_model)}),
        len(kept),
        max((_instant(point) or _UNDATED for point in kept), default=_UNDATED),
    )


def _key_order(key: ComparabilityKey) -> tuple[str, str, int, str]:
    """The key as something sortable, for the tiebreak that ends all tiebreaks.

    :class:`ComparabilityKey` is frozen and unordered -- ordering it would be a
    claim that one golden set precedes another, which is meaningless. This is
    the same four fields in declaration order, used for nothing but deciding
    deterministically between groups that are otherwise indistinguishable.
    """
    return (key.goldenset_hash, key.judges_hash, key.n_per_item, key.baseline_model)


def _newest_per_model(kept: Sequence[RunPoint]) -> tuple[RunPoint, ...]:
    """One run per candidate model -- the newest -- ordered by candidate model.

    "Newest" is by ``created``, with position in the log breaking ties. An
    undated run therefore loses to any dated one, and where nothing is dated the
    later record in an append-only log wins, which is the same claim "newer"
    makes with the dates missing.

    A run whose ``candidate_model`` is unrecorded is not here: ``"" == ""`` would
    fold two anonymous runs into one row and silently drop the other, which is
    the empty-value hole :func:`partition_comparable` closes over the key's four
    fields, in the one field the key does not carry. It leaves through
    :func:`_unnamed_candidate` with a sentence instead.
    """
    winners: dict[str, tuple[tuple[datetime, int], RunPoint]] = {}
    for index, point in enumerate(kept):
        if not _recorded(point.candidate_model):
            continue
        rank = (_instant(point) or _UNDATED, index)
        held = winners.get(point.candidate_model)
        if held is None or rank > held[0]:
            winners[point.candidate_model] = (rank, point)
    return tuple(winners[model][1] for model in sorted(winners))


def _excluded(
    points: Sequence[RunPoint], partition: Partition, rendered: Sequence[RunPoint]
) -> tuple[Exclusion, ...]:
    """Every run the table does not hold, in log order, whichever rule removed it.

    Three rules produce exclusions and they are asked in different places:
    :func:`partition_comparable` decides comparability, and this section decides
    twice over whether a comparable run can be a *row* -- once for a run that
    records no candidate model (:func:`_unnamed_candidate`) and once for a run
    beaten to its row by a newer run of the same model (:func:`_superseded`).
    Concatenating their outputs would put the whole of one before the whole of
    the other and read as three lists; a reader working through why a run is
    missing wants the log.

    **Together with ``candidates`` these account for every point handed in.** A
    run that is in the log and in neither tuple has disappeared without a
    sentence, which is exactly the quietly-shrunk table :class:`Exclusion` was
    minted to prevent -- and until the superseded rule was added here, the second
    run of a nightly candidate was such a run.
    """
    removed = {id(one.point): one for one in partition.excluded}
    winners = {point.candidate_model: point for point in rendered}
    shown = {id(point) for point in rendered}
    for point in partition.kept:
        if not _recorded(point.candidate_model):
            removed[id(point)] = Exclusion(point=point, reason=_unnamed_candidate(point))
        elif id(point) not in shown:
            reason = _superseded(point, winners[point.candidate_model])
            removed[id(point)] = Exclusion(point=point, reason=reason)
    return tuple(removed[id(point)] for point in points if id(point) in removed)


def _unnamed_candidate(point: RunPoint) -> str:
    """A run that is comparable to the group and still cannot be a row.

    The table's rows *are* candidate models, so a run that never recorded one has
    no row to be. It cannot be folded in with the other anonymous runs either:
    that is the coercion this module refuses over the golden-set hash, the judge
    hash, the draw count and the baseline model, and there is no reason it stops
    being a coercion at the fifth field. Two runs that both failed to say what
    they were testing are two unknowns, not one candidate measured twice, and
    merging them would print one row's numbers under both runs' authority while
    the other vanished with nothing said.

    It is excluded here rather than in :func:`_incomparable` because it is not a
    statement about comparability: such a run *is* comparable to the group, and
    a consumer that only asks "may these be read against each other" is right to
    keep it. Only a consumer building rows out of model names has a problem with
    it, and this is the only one.
    """
    return (
        f"excluded: this run records no candidate model, so it has no row in a table "
        f"whose rows are candidate models. It is comparable to the group -- "
        f"{_hash(point.goldenset_hash)} over {_depth(point.n_per_item)} draws per item "
        f"against {point.baseline_model or UNRECORDED} -- but an unrecorded candidate "
        f"cannot be folded in with another unrecorded one: that would print one run's "
        f"numbers under both runs' authority and lose the other run entirely."
    )


def _superseded(point: RunPoint, winner: RunPoint) -> str:
    """A run kept by the partition and beaten to its row by a newer run of itself.

    The table's rows are candidate models and this candidate has more than one
    run under the group's key, so only the newest is a row -- the older one's
    delta is a measurement of a different night, and printing both would invite a
    reader to compare a model against itself and read the difference as a result.

    **It is named here for :func:`_unnamed_candidate`'s reason, not for a new
    one.** That function refuses to let a run leave the table without a sentence
    because a run present in the log and absent from every tuple this field
    returns has vanished, and a reader cannot tell a run that was dropped from a
    run that was never written. This is the same disappearance one rule over: the
    nightly job that re-ran a candidate on Tuesday and Thursday is the commonest
    log this module will ever see, and until this sentence existed its Tuesday
    run was in no tuple at all.

    The winner's date is named rather than merely asserted so that the sentence
    is checkable against the log, on the precedent of every exclusion in
    :func:`_incomparable`: "not comparable" is a verdict, and the reader needed
    the evidence. Where nothing was dated the position in the log is what decided
    it, and the sentence says that instead of printing a date it does not have.
    """
    moment = _instant(winner)
    beaten_by = (
        f"this candidate's run of {moment.date().isoformat()}"
        if moment is not None
        else "a later record of this candidate in the same log, neither run having recorded a date"
    )
    return (
        f"excluded: superseded by {beaten_by}. The rows of this table are candidate models "
        f"and {point.candidate_model} has more than one run under the group's key, so the "
        f"newest of them is the row and this one is not: a stale delta printed beside a "
        f"fresh one compares two nights rather than two models. Its numbers were not "
        f"doubted and it was not incomparable -- it is simply not the most recent reading "
        f"of this candidate, and it is named here rather than dropped because a run that "
        f"is in the log and in none of this field's tuples has disappeared with nothing "
        f"said about it."
    )


def _drifted_baselines(anchor: RunPoint, rendered: Sequence[RunPoint]) -> tuple[Caveat, ...]:
    """A caveat when the rows were not all measured against the same baseline.

    :attr:`CandidateField.baseline_pass_rate` is one number taken from one run,
    and every :attr:`Candidate.delta_pp` beside it is computed against the
    baseline *its own* run measured. While those agree the header is a summary;
    the moment they do not, it is a number from one row printed above rows it is
    not the baseline of, and a reader who adds it back to a delta gets a pass
    rate no run recorded.

    **This is the chunk's named failure mode, reached from the type alone.**
    "Three candidates measured three weeks apart render as a fair field, with the
    baseline having drifted underneath them" is a claim about the baseline, and
    :attr:`~CandidateField.spread_flagged` answers it with a *proxy*: it reports
    that the runs are far apart in time, which is neither necessary nor
    sufficient. A baseline can move overnight -- a re-scored golden set, a
    provider silently changing a hosted model -- and ``spread_flagged`` is
    ``False`` for a one-day drift. Here the drift is read off the numbers
    themselves.

    It is a caveat and not an exclusion because nothing is wrong with any row:
    each delta is against its own baseline and is correct. What is unsafe is the
    header, so the note is attached to the run the header came from, which is the
    row a reader would otherwise take the number to be about.

    Rates are compared and the counts are printed. A ``None`` rate -- a baseline
    side whose counts do not describe one, see :func:`_baseline_pass_rate` -- is
    a value the others do not share, and it counts as disagreement for the same
    reason ``""`` never matches ``""`` in this module: not knowing is not the
    same fact as agreeing.
    """
    if len({_baseline_pass_rate(point) for point in rendered}) < 2:
        return ()
    seen = ", ".join(
        f"{point.candidate_model} {_graded_baseline(point)}" for point in rendered
    )
    return (
        Caveat(
            point=anchor,
            reason=(
                f"flagged: the field's baseline pass rate is this run's -- "
                f"{_graded_baseline(anchor)} graded on the baseline side -- and the rows do "
                f"not share it: {seen}. Every row's delta is against the baseline that row's "
                f"own run measured and each of those is sound, so this is a caveat and not an "
                f"exclusion; what it warns against is the header. Do not add it back to a "
                f"delta to recover a pass rate -- the sum would be a number no run measured. "
                f"A baseline that moved underneath the rows is the drift this field exists to "
                f"expose, and it can move overnight, which is why it is read off the counts "
                f"here rather than inferred from how far apart the runs are in time."
            ),
        ),
    )


def _graded_baseline(point: RunPoint) -> str:
    """The baseline side's counts as the fraction its pass rate was rebuilt from.

    Printed as ``passed/graded`` rather than as a rate because it is exact: a
    rate has to be rounded to be written into a sentence, and a sentence whose
    whole subject is that two numbers differ is the wrong place to round. It also
    shows an impossible pair -- ``-10/50``, ``60/50`` -- as the nonsense it is,
    where a refused rate would print only as an absence.
    """
    return f"{point.judged_baseline - point.judge_failures_baseline}/{point.judged_baseline}"


def _delta_pp(point: RunPoint) -> float | None:
    """Candidate minus baseline for one run, in percentage points, or ``None``.

    The multiplication by 100 is the whole of the unit conversion and there is
    nothing else in this function on purpose. It is not rounded: a rounded value
    is a rendering decision, and a renderer that wants one decimal place can take
    one, while a model that has already rounded cannot give back what it dropped.
    """
    baseline = _baseline_pass_rate(point)
    if point.pass_rate is None or baseline is None:
        return None
    return (point.pass_rate - baseline) * 100.0


def _baseline_pass_rate(point: RunPoint) -> float | None:
    """The baseline side's pass rate, reconstructed exactly, or ``None``.

    :class:`RunPoint` has no baseline rate: ``pass_rate`` is documented as the
    candidate side of the widest judge, and the only baseline-side numbers a
    point carries are ``judged_baseline`` and ``judge_failures_baseline``. Adding
    a field to the producer while its consumers are in flight is not available,
    and it is not needed, because those two numbers are what the recorded rate
    was computed from: ``judge_failures_baseline`` is the gate's own ``failures``,
    which *is* ``n - successes``. So this is not an approximation of the rate --
    it is the rate, rebuilt from its own operands, over the same denominator
    convention ``pass_rate`` uses on the candidate side. Two quantities measured
    the same way are the only two it is honest to subtract.

    ``None`` -- never ``0.0`` -- when nothing was graded, mirroring
    :func:`_candidate_rate` and for its reason: a zero would plot a point on the
    floor of the chart for a run that measured nothing, which reads as a total
    collapse rather than as an absence. A ``delta_pp`` of ``-100.0`` against a
    baseline that measured nothing is the same lie one subtraction later.

    **``None`` also when the two counts cannot both be true**, which is the one
    guard this module's other rates do not need. Every other number here is read
    off a payload and passed on; this one is *computed*, and the arithmetic is
    happy to hand back ``1.2`` from ``-10`` failures out of ``50`` or ``-0.2``
    from ``60`` out of ``50``. Those are pass rates outside ``[0, 1]``, and one of
    them is a candidate that beat its baseline by minus twenty points -- a number
    a reader would act on. Neither count is validated anywhere upstream:
    :func:`_count` reads whatever integer the JSON held, because a payload is
    JSON and not a type, and the same class of hole is what four of C4's
    exclusions exist to close. A rate that would be a lie is refused, and the
    counts themselves survive on the point for anyone who wants to see why.
    """
    if point.judged_baseline <= 0:
        return None
    if not 0 <= point.judge_failures_baseline <= point.judged_baseline:
        return None
    return (point.judged_baseline - point.judge_failures_baseline) / point.judged_baseline


def _stale_days(point: RunPoint, newest: datetime | None) -> float | None:
    """This run's age against the newest dated run in the field, in days."""
    moment = _instant(point)
    if newest is None or moment is None:
        return None
    return _days(newest, moment)


def _latest(points: Sequence[RunPoint]) -> RunPoint:
    """The newest of these runs, undated sorting oldest, position breaking ties.

    Deliberately total over a non-empty sequence: every caller here has already
    established there are at least two rows, and a field's header number has to
    come from *some* row. Which one is a determinate question and this answers it
    the same way :func:`_newest_per_model` answers it, so the run whose baseline
    the header quotes is the run a reader would point at.
    """
    return max(enumerate(points), key=lambda pair: (_instant(pair[1]) or _UNDATED, pair[0]))[1]


def _instant(point: RunPoint) -> datetime | None:
    """When this run happened, or ``None`` when it does not say."""
    return parse_created(point.created)


def _days(later: datetime, earlier: datetime) -> float:
    """The span between two instants in days, fractional and never rounded.

    "Span" and not "interval": in this one chunk ``interval`` is the word for the
    thing :attr:`Candidate.delta_pp` must never carry, and a helper that opens by
    calling its own result one is a helper somebody quotes back.

    Days rather than seconds because every consumer of this module's durations
    prints days, and fractional rather than whole because two runs eleven hours
    apart are not "0 days apart" on a page whose subject is whether they were
    measured close enough together to be read side by side.
    """
    return (later - earlier).total_seconds() / _SECONDS_PER_DAY


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
# C6: multiplicity, corrected at render and said out loud
# --------------------------------------------------------------------------- #

#: The method every :class:`Multiplicity` names, applied or refused. Written once
#: because a refusal names it too: a record whose ``method`` went empty when the
#: correction was declined would let a renderer print "no method" for "this
#: method, and here is why it was not run", which are different facts.
_HOLM_BONFERRONI = "holm-bonferroni"

#: How a level with no number is written into a note and compared for sameness. A
#: point that recorded no ``alpha`` has not agreed with one that recorded ``0.05``
#: -- not knowing is not the same fact as agreeing, which is the rule
#: :func:`_drifted_baselines` keeps about rates one section up.
_UNRECORDED_LEVEL = "unrecorded"


@dataclass(frozen=True)
class Multiplicity:
    """What correcting a field's p-values *across its candidates* did, and in words.

    Three candidates measured against one baseline are three tests, and three
    tests against one floor at a nominal 5% flag a regression between identical
    models far more often than 5% of the time. :func:`~.comparison.compare`
    already corrects across the *judges* within one run; nothing until here
    corrects across the *candidates* of one table, so a field of three renders
    three independently-significant p-values and a reader adds them up by eye.

    **This record is what makes the correction sayable rather than merely done.**
    The failure it is shaped against is not an uncorrected report -- it is a
    report that states the correction was applied while showing nothing it did,
    or that names a smaller set of affected candidates than the correction
    actually touched. Claiming a guard you did not apply is worse than applying
    none, and under-stating a guard you did apply is the quieter cousin of the
    same lie.

    Frozen for :class:`RunPoint`'s reason: a record that can be edited after the
    table beside it was rendered is a record that can be made to disagree with it.
    """

    #: Whether the correction ran. ``False`` is a refusal and never a silence:
    #: :attr:`note` says which refusal it was, in the sentence the report prints.
    applied: bool
    #: ``"holm-bonferroni"``, applied or refused -- see :data:`_HOLM_BONFERRONI`.
    method: str
    #: The family-wise level the correction ran at, or would have run at: the one
    #: level every member of the family records. ``None`` when the members do not
    #: agree on one, when none of them recorded one, or when the one they agree on
    #: is not a probability in ``(0, 1)`` and so is not a level at all.
    #:
    #: It is populated on a refusal wherever the family did agree -- a family of
    #: one has a perfectly good level and simply has nothing to correct against.
    alpha: float | None
    #: How many candidates were **in the family**: those whose ``point.p_value``
    #: is not ``None``. A candidate that recorded no p-value was not tested, so it
    #: is not a test to correct, and counting it would tighten every other
    #: candidate's threshold on the strength of a measurement nobody took.
    #:
    #: A ``NaN`` p-value *is* counted. It was tested and produced no answer, which
    #: is not the same fact as not having been tested -- see :attr:`changed`.
    family_size: int
    #: ``candidate_model`` -> the Holm threshold that model's p-value was held to.
    #: Keyed on :attr:`Candidate.model`, which is the join every consumer of a row
    #: already uses. Empty whenever :attr:`applied` is ``False``, and **never**
    #: empty when it is ``True``: "Holm-Bonferroni was applied" printed above no
    #: thresholds is precisely the overclaim this record exists to prevent, so the
    #: mapping is built before ``applied`` is set and asserted non-empty after.
    #:
    #: These are for **display and diagnosis**. A threshold is not a decision
    #: boundary once the step-down has stopped -- see :attr:`changed`, which is
    #: the only place significance is decided and does not read this mapping.
    thresholds: Mapping[str, float]
    #: The candidate models the correction took significance **away** from:
    #: ``p_value < alpha and not rejected``, with ``rejected`` taken from
    #: :func:`~.comparison.holm_bonferroni`'s own return. Ordered as
    #: :attr:`CandidateField.candidates` is, so two renders of one log agree.
    #:
    #: **A p-value is never compared against a threshold in :attr:`thresholds` to
    #: decide this.** Holm *steps down*: once a test fails to reject, nothing
    #: larger is rejected either, whatever its own threshold says. And the largest
    #: p-value in any family is tested against ``alpha / 1`` -- alpha itself -- so
    #: ``p_value >= threshold`` is false for it whenever ``p_value < alpha``, and
    #: the rule "significant uncorrected, not significant corrected" written as
    #: ``p >= threshold`` drops the largest sub-alpha p-value in every family.
    #: Measured on the real function at ``alpha=0.05`` over
    #: ``p = [0.03, 0.04, 0.045]``: none of the three is rejected, so all three
    #: belong here, and the threshold rule names two of them -- missing the
    #: largest, in the one set whose whole purpose is to make the correction's
    #: effect visible.
    #:
    #: A ``NaN`` p-value never appears here. ``holm_bonferroni`` reads it as 1.0
    #: before anything else happens, and ``NaN < alpha`` is ``False`` -- a test
    #: that produced no answer had no significance for the correction to remove.
    changed: tuple[str, ...]
    #: The sentence the report prints. Written here rather than in a template so
    #: that the terminal render and the HTML say the same words -- the discipline
    #: :attr:`~model_migration_kit.report.DetailBudget.sentence` already keeps,
    #: for the reason :attr:`CandidateField.stale_after_days` exists: a sentence
    #: assembled where the numbers are not is a sentence that goes stale against
    #: them, and two copies of a disclosure are two chances for one to be wrong.
    #:
    #: It always says that this is a *second* correction, on p-values already
    #: corrected across each run's own judges. A note that did not would let a
    #: reader take these thresholds for the whole of the multiplicity in the
    #: number, when they are one of two corrections stacked on it.
    note: str


def correct_field(field: CandidateField) -> tuple[CandidateField, Multiplicity]:
    """Correct a candidate field's p-values across its candidates, and say what that did.

    The family is the field's candidates that carry a ``point.p_value``; each
    contributes that one number. The level is read off the points and must be the
    same across them -- a family-wise level is not defined over members tested at
    different levels, so where they differ the correction is refused rather than
    run at whichever level happened to come first.

    **No point's ``verdict`` is touched, ever.** The correction changes what this
    *table* says about significance across the field; it does not retroactively
    overturn a verdict a gate recorded on the night it ran. "NO-GO as recorded;
    not significant once corrected across three candidates" is the honest cell,
    and it is more interesting than either half -- so both halves have to survive
    this function.

    What the returned field carries instead is one :class:`Caveat` per candidate
    in :attr:`Multiplicity.changed`, appended to :attr:`CandidateField.caveats`
    and attached to that candidate's own point. Appended, never filtered or
    re-worded: the drift caveat and the superseded exclusion C5 mints are notes
    this function has no standing to edit. A field whose correction changed
    nothing is returned unchanged, and so is one whose correction was refused --
    :class:`Multiplicity` is where a refusal is recorded, and a caveat saying
    nothing happened is a caveat that trains a reader to skip caveats.

    Args:
        field: The field to correct, from :func:`candidate_field`.

    Returns:
        The field with its multiplicity caveats appended, and the record of what
        the correction did or why it was declined.
    """
    family = [
        (candidate, candidate.point.p_value)
        for candidate in field.candidates
        if candidate.point.p_value is not None
    ]
    untested = len(field.candidates) - len(family)
    refusal = _refused(family, untested)
    if refusal is not None:
        return field, refusal

    alpha = _family_level(family)
    assert alpha is not None, "_refused declines every family without one usable level"
    decisions = holm_bonferroni([p_value for _, p_value in family], alpha=alpha)
    thresholds = {
        candidate.model: threshold
        for (candidate, _), (_, threshold) in zip(family, decisions, strict=True)
    }
    # ``not rejected`` and never ``p_value >= threshold`` -- see Multiplicity.changed.
    changed = tuple(
        candidate.model
        for (candidate, p_value), (rejected, _) in zip(family, decisions, strict=True)
        if p_value < alpha and not rejected
    )
    assert len(thresholds) == len(family) >= 2, (
        "one threshold per candidate, and never applied=True over an empty mapping: "
        "a report that says Holm-Bonferroni was applied while showing no thresholds "
        "is the overclaim this record exists to prevent"
    )
    multiplicity = Multiplicity(
        applied=True,
        method=_HOLM_BONFERRONI,
        alpha=alpha,
        family_size=len(family),
        thresholds=thresholds,
        changed=changed,
        note=_applied_note(len(family), alpha, changed, untested),
    )
    caveats = _multiplicity_caveats(field, changed, alpha, len(family))
    if not caveats:
        return field, multiplicity
    return replace(field, caveats=field.caveats + caveats), multiplicity


def _refused(
    family: Sequence[tuple[Candidate, float]], untested: int
) -> Multiplicity | None:
    """The refusal this family earns, or ``None`` when it earns none.

    Five grounds, each with a note naming it, because "the correction did not run"
    with no reason attached is the absence a reader fills in with the most
    flattering guess available.

    The order matters in one place: levels that *differ* are reported as differing
    before an unrecorded level is reported as missing, so a field where one point
    recorded ``0.05`` and another recorded nothing names both rather than claiming
    nobody recorded one.
    """
    levels = _levels(family)
    if not family:
        # ``untested`` is every row here, and :func:`_untested_clause` would print
        # the same fact a second time in the same sentence, so this branch names
        # the count itself and passes none on.
        return _no_correction(
            0,
            None,
            0,
            f"none of the {untested} candidate(s) in this field recorded a p-value, so there "
            f"is no family to correct",
        )
    if len(family) == 1:
        return _no_correction(
            1,
            _family_level(family),
            untested,
            "a family of one is a single test, and correcting one p-value against itself "
            "changes nothing -- its Holm threshold would be the uncorrected alpha",
        )
    if len(levels) > 1:
        return _no_correction(
            len(family),
            None,
            untested,
            f"the candidates carrying a p-value were tested at different levels "
            f"({', '.join(levels)}), and a family-wise level is not defined over members "
            f"tested at different levels",
        )
    if levels == [_UNRECORDED_LEVEL]:
        return _no_correction(
            len(family),
            None,
            untested,
            "no candidate carrying a p-value recorded the alpha it was tested at, and a "
            "family-wise level cannot be substituted for one nobody wrote down",
        )
    if _family_level(family) is None:
        return _no_correction(
            len(family),
            None,
            untested,
            f"the level every candidate carrying a p-value records, {levels[0]}, is not a "
            f"probability in (0, 1), so it is not a level to correct at",
        )
    return None


def _no_correction(
    family_size: int, alpha: float | None, untested: int, because: str
) -> Multiplicity:
    """A refusal, assembled so that no refusal can carry thresholds or a changed set.

    ``applied=False`` beside a populated :attr:`Multiplicity.thresholds` would be
    the mirror of the overclaim the applied branch asserts against: a mapping of
    thresholds printed under a sentence saying they were never used.
    """
    return Multiplicity(
        applied=False,
        method=_HOLM_BONFERRONI,
        alpha=alpha,
        family_size=family_size,
        thresholds={},
        changed=(),
        note=(
            f"Holm-Bonferroni was not applied across this field's candidates: "
            f"{because}{_untested_clause(untested)}."
        ),
    )


def _applied_note(
    family_size: int, alpha: float, changed: Sequence[str], untested: int
) -> str:
    """One sentence: what ran, over what, on top of what, and what it changed.

    The clause about the *second* correction is not decoration. Each p-value in
    this family was already Holm-corrected across its own run's judges by
    :func:`~.comparison.compare`, so every threshold here is family-wise over
    candidates and over nothing else, and a note that left that out would let a
    reader take these thresholds for the whole of the multiplicity in the number.
    """
    if changed:
        took = (
            f"the correction took significance from {', '.join(changed)} -- below the alpha "
            f"each was tested at, and not rejected once corrected across the family"
        )
    else:
        took = "the correction took significance from no candidate in the field"
    return (
        f"Holm-Bonferroni was applied across the {family_size} candidates in this field at a "
        f"family-wise alpha of {alpha!r}, a second correction on p-values that compare had "
        f"already corrected across each run's own judges, so these thresholds are family-wise "
        f"over candidates and not over judges{_untested_clause(untested)}; {took}, and no "
        f"recorded verdict was changed -- each stands as the gate that ran it recorded it."
    )


def _untested_clause(untested: int) -> str:
    """How many rows are in the table and not in the family, or nothing.

    Named in every note, applied or refused, because a family size printed beside
    a table with more rows than that reads as a miscount rather than as a
    statement that some rows were never tested.
    """
    if not untested:
        return ""
    return (
        f"; {untested} candidate(s) in the table recorded no p-value, were not tested, and "
        f"are not in the family"
    )


def _levels(family: Sequence[tuple[Candidate, float]]) -> list[str]:
    """Every distinct level the family records, in the rows' own order, as text.

    Text rather than floats because the comparison has to be *total*. ``NaN`` is
    an alpha a directly-built :class:`RunPoint` can carry, and ``NaN != NaN``, so
    a set of alphas would report a family of two identical ``NaN`` levels as two
    different levels and print "tested at different levels: nan, nan". Comparing
    ``repr`` makes sameness total, and gives the note the words it prints.

    Distinctness is exact, deliberately. ``0.05`` and ``0.05000000000000001`` are
    two levels and the correction is right to refuse them: a family-wise level is
    a number the whole family was held to, and these are two numbers.
    """
    labels: list[str] = []
    for candidate, _ in family:
        label = _level_label(candidate.point.alpha)
        if label not in labels:
            labels.append(label)
    return labels


def _level_label(alpha: float | None) -> str:
    """One level as it is compared and printed. ``repr``, so nothing is rounded away."""
    return _UNRECORDED_LEVEL if alpha is None else repr(alpha)


def _family_level(family: Sequence[tuple[Candidate, float]]) -> float | None:
    """The one usable family-wise level, or ``None``.

    Usable means three things at once: the family agrees on it, it was recorded,
    and it is a probability in ``(0, 1)``. The last is not pedantry --
    :func:`~.comparison.holm_bonferroni` raises ``ValueError`` outside that range,
    and this module's one hard rule is that nothing here raises on a payload.
    ``alpha`` reaches a point through :func:`_number` on a live read, but a
    :class:`RunPoint` is a public frozen dataclass anyone may build directly, so
    the guard is at the call and not at the constructor.
    """
    if len(_levels(family)) != 1:
        return None
    alpha = family[0][0].point.alpha
    if alpha is None or not 0.0 < alpha < 1.0:
        return None
    return float(alpha)


def _multiplicity_caveats(
    field: CandidateField, changed: Sequence[str], alpha: float, family_size: int
) -> tuple[Caveat, ...]:
    """One caveat per candidate the correction changed, attached to its own point.

    A caveat and not an exclusion, on :func:`_drifted_baselines`' reasoning:
    nothing is wrong with the row. Its p-value is what its run measured and its
    delta is against its own baseline. What changed is what the *field* can say
    about it, which is a note against the row and not a reason to remove it.

    Carried on the field rather than left in :class:`Multiplicity` alone because
    :attr:`CandidateField.caveats` is where a renderer looks for the sentence that
    belongs beside a row, and a caveat that reaches nobody is the same as a caveat
    never computed.
    """
    rows = {candidate.model: candidate for candidate in field.candidates}
    return tuple(
        Caveat(
            point=rows[model].point,
            reason=(
                f"flagged: this run's p-value is below the alpha it was tested at ({alpha!r}) "
                f"and Holm-Bonferroni across the {family_size} candidates in this field does "
                f"not reject it -- significant as recorded, not significant once corrected "
                f"across the field. The p-value was already corrected across this run's own "
                f"judges, so this is a second correction on an already-corrected number and "
                f"the threshold applied here is family-wise over candidates only. The verdict "
                f"this run recorded -- {rows[model].point.verdict or 'none'} -- is untouched: "
                f"the correction changes what this table says about significance across the "
                f"field and does not overturn a verdict a gate recorded on the night it ran."
            ),
        )
        for model in changed
    )


# --------------------------------------------------------------------------- #
# C11: the counterfactual spot check
# --------------------------------------------------------------------------- #

#: The two sides a spot check can be about, spelled the way the rest of the
#: report spells them. Closed rather than free: a side is not a label the caller
#: invents, it is one of the two columns every comparison in this document has,
#: so a third value is a miswired caller and never a new kind of side.
_SIDES = ("baseline", "candidate")

#: What the sentence says where a judge's name would go when the run recorded
#: none. Spelled out for :data:`_UNRECORDED`'s reason -- "under judge " with
#: nothing after it reads as a formatting bug rather than as a missing fact --
#: and said *inside the sentence*, because a reader who cannot see the absence
#: where they read the number will read the number as though it had a subject.
_JUDGE_UNNAMED = "a judge whose name the run did not record"


@dataclass(frozen=True)
class SpotCheckSubject:
    """Which judge's grading, and which side's items, a spot check speaks about.

    **Two fields rather than one string, and the reason is not tidiness.**

    The caller holds two separate facts -- a judge name lifted from
    ``item_counts["per_judge"]``, and the side whose counts it just passed in --
    and a single free-text label would make the caller turn those two facts into
    English. Composing this producer's prose anywhere but here is the thing
    R21.5 refused for ``trend``'s caveat and R26.4 refused again for this
    sentence: if the plumbing may write the producer's words once, nothing
    downstream is obliged to say them the same way twice, and no reviewer reads
    the wiring for claims about the data. So the caller supplies facts and this
    module supplies the wording. That is the whole of the split.

    Three consequences follow from the structure that a string could not give:

    * **The side can be checked.** ``side`` has exactly two legal values, so
      ``"cand"``, ``"the candidate model"`` or a judge name in the side slot is
      refused at construction. A free label is unfalsifiable -- whatever the
      caller passes is printed, and a sentence naming the wrong side is worse
      than one naming none.
    * **The two absences are not the same absence, and must not be handled
      alike.** A missing *side* is a wiring bug: the caller chose which side's
      counts to pass and therefore cannot fail to know which it chose, so it is
      refused. A missing *judge name* is a fact about the log -- ``report.py``
      reads ``counting_judge = judges[0].name if judges else ""`` and judge rows
      carry ``name=str(raw.get("name", "") or "")``, so a blank is reachable
      from real evidence -- and refusing it would take the sentence away from
      the reader to protect them from a gap the sentence can simply state. It is
      said out loud instead, in :data:`_JUDGE_UNNAMED`. One string could not tell
      those two cases apart, and so would have to get one of them wrong.
    * **The facts travel, not just the prose.** :class:`SpotCheck` carries this
      object, so a reader who wants to know what the sentence is about can read
      the fields rather than parse the sentence -- the same reason ``SpotCheck``
      carries the counts it computed from.

    Never inferred, on R15's rule: nothing here reads a model id, guesses a side
    from a count, or picks the judge when several graded. Which judge is C10's
    ``judges[0]`` decision and which side is R26.3's ruling, and both belong to
    the caller that has the panel in front of it.
    """

    #: The judge whose grading produced these counts. May be empty when the run
    #: recorded no name; see :data:`_JUDGE_UNNAMED`. Never guessed.
    judge: str
    #: ``"baseline"`` or ``"candidate"``. Anything else raises.
    side: str

    def __post_init__(self) -> None:
        """Refuse a side that is not one of the two, at construction.

        Here rather than in :func:`spot_check` so that a ``SpotCheckSubject``
        which exists is a ``SpotCheckSubject`` that can be printed. A validating
        function leaves a half-legal value sitting in a variable for anything
        else to read; a validating constructor does not.
        """
        if self.side not in _SIDES:
            raise ValueError(
                "side must be one of "
                + " or ".join(repr(one) for one in _SIDES)
                + f", got {self.side!r}. The caller chose which side's counts it "
                "passed, so a side it cannot name is a miswired caller and not a "
                "run that failed to record one."
            )


def _judge_phrase(judge: str) -> str:
    """``"judge accuracy"``, or a plain admission that the run named none.

    ``.strip()`` through :func:`_recorded`, sharing the module's one emptiness
    test, because a padded name recorded nothing either and this is a place where
    the difference between ``""`` and ``"  "`` would decide whether an absence
    renders as a name.
    """
    return f"judge {judge.strip()}" if _recorded(judge) else _JUDGE_UNNAMED


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

    ``subject`` is one of those inputs and is listed first, because a number
    computed per judge and per side identifies nothing until it says which. It is
    in the sentence as well as in this field, and the sentence is the copy that
    matters: a renderer must not caption around this record to supply a subject
    the sentence already carries. Two renderings deriving one fact two ways is
    how they come to disagree.
    """

    #: Which judge's grading and which side's items this is about. Supplied by
    #: the caller, never inferred; see :class:`SpotCheckSubject`.
    subject: SpotCheckSubject
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
    items_passing: int,
    items_failing: int,
    items_unstable: int,
    *,
    subject: SpotCheckSubject,
    k: int = 12,
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

    **The sentence names its subject, and the subject is required.** These
    counts are per judge and per side, so ``33%`` on its own is a number about
    nothing: a reader cannot tell whether it describes the candidate the decision
    turns on or the baseline it is measured against, nor which judge graded, and
    this is the number a sceptical reader checks first. So ``subject`` is a
    keyword argument with **no default**. Omitting it is a ``TypeError`` at the
    call site, for the reason ``k == 0`` raises rather than returning ``None``:
    an unlabelled sentence is a miswired caller, and a miswired caller must fail
    where it is written rather than reach a reader wearing a result's clothes.
    A subject that is not a :class:`SpotCheckSubject` is refused the same way and
    for the same reason -- a bare string is the shape of a caller composing this
    module's prose for it, which R26.4 ruled out.

    What the sentence must never do is *look* labelled when it is not. An
    unnamed judge is stated in words (:data:`_JUDGE_UNNAMED`) rather than left as
    a gap, because an absence rendered as blank space reads as a measurement with
    a formatting bug; and a side that is not one of the two is refused outright.
    Which judge and which side are the caller's facts and are never guessed here:
    R26.3 rules them to be the counting judge -- ``judges[0]``, C10's choice for
    the tag matrix, because one document must not select its judge two ways --
    and the candidate, whose failures are the ones the decision turns on.

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
    if not isinstance(subject, SpotCheckSubject):
        raise TypeError(
            "subject must be a SpotCheckSubject naming the judge and the side "
            f"these counts come from, got {type(subject).__name__}. A spot check "
            "is computed per judge and per side, so a sentence without one says "
            "nothing a reader can check."
        )
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
        f"A {k}-prompt spot check of the {subject.side} under "
        f"{_judge_phrase(subject.judge)}, drawn at random from these {items} "
        f"items, {items_failing} of which failed, would have shown no failures "
        f"at all in {_percent(probability)} of such checks."
    )
    return SpotCheck(
        subject=subject,
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


# --------------------------------------------------------------------------- #
# C7: the trend and the parameter strip
# --------------------------------------------------------------------------- #

#: How a caller came to hold a lineage, and there are exactly two ways. Either a
#: person wrote the succession down somewhere it can be reviewed and versioned,
#: or this module assembled one from the log under a stated policy. There is no
#: third value and in particular no "partly declared": a declaration that names
#: some of the ids is still a declaration, and the runs it leaves out are
#: :attr:`Trend.outside_lineage`'s business (R24.1), not a shade of provenance.
_LINEAGE_DECLARED = "declared"
_LINEAGE_ASSUMED = "assumed"
_LINEAGE_SOURCES = (_LINEAGE_DECLARED, _LINEAGE_ASSUMED)


@dataclass(frozen=True)
class CandidateLineage:
    """Which candidate ids are one succession, and how the caller came to know.

    **Two fields rather than a bare sequence of ids, for R26.4's reason applied
    to R21.5's ruling.** R15.1 made the lineage caller-declared and forbade
    inferring it, and R21.5 then ruled what happens when nobody declared one: the
    ids are assumed from the log, *and ``Trend`` says out loud that they were
    assumed.* Only the caller can know which of those two happened -- ``trend``
    is handed a list of ids and cannot tell a config's declaration from a list
    somebody built out of the very points it is about to draw. So the caller
    supplies the fact and this module supplies the words. Exactly the split
    :class:`SpotCheckSubject` makes, and made for the same reason: **plumbing
    that quietly patches a producer's honesty is the one shape of this defect
    nobody would find**, because no reviewer reads the wiring for claims about
    the data.

    **The two facts travel together because they must agree.** A ``bool`` beside
    a ``Sequence[str]`` was the smaller change and it puts the two halves in two
    parameters that can disagree without anything noticing -- a caller that
    assembles ids from the log and passes ``declared=True`` gets a page missing
    the one caveat this ruling exists to print, and nothing in the type system,
    the tests or the rendered document would object. Here the honest pairing is
    the only easy one: :meth:`assumed_from` is the sole place first-appearance
    order is computed and it stamps its own provenance, so labelling an assumed
    lineage as declared takes a deliberate lie rather than a slip.

    **Assembly lives here, not in the caller, and that was a real choice.**
    ``report.py`` could perfectly well have written the ``dict.fromkeys`` loop
    itself. Three reasons it does not:

    * **The policy must agree with ``trend``'s own selection, which is here.**
      An assumed lineage assembled over the *whole* log would name candidates
      that ran against some other baseline; ``trend`` does not select those, so
      they would appear in neither :attr:`Trend.points` nor
      :attr:`Trend.outside_lineage` nor :attr:`Trend.absent_models` -- a run in
      the log and on no part of the page, which is precisely R24.1. A caller
      reimplementing the loop cannot see that rule; the method sitting beside
      ``trend`` shares it.
    * **Two callers must not assume differently.** R15.4 wants one change said in
      three places because each is silent in a different failure, and observes
      that three renderings deriving one fact three ways is three chances to
      derive three different facts. A lineage is that fact for the whole chart.
    * **It is the one construction that cannot be mislabelled**, per the
      paragraph above.

    None of this is inference in R15.1's sense, and the distinction is the whole
    of R21.5: nothing here reads the *shape* of an id. Stripping ``-v2`` to
    decide it succeeds ``-v1`` stays forbidden. What :meth:`assumed_from` applies
    is a policy -- *treat the candidates measured against one baseline as one
    succession* -- which is a claim that can be wrong, and being wrong is why
    ``trend`` prints it rather than keeping it.
    """

    #: The candidate ids on this line, in declaration order or first-appearance
    #: order. Order carries no meaning to ``trend`` -- time comes from
    #: ``created`` (R15.1) -- but it is reported back in
    #: :attr:`Trend.absent_models`, so it is kept as the caller gave it.
    models: tuple[str, ...]
    #: ``"declared"`` or ``"assumed"``. Never guessed, and never defaulted:
    #: see :func:`trend`.
    source: str

    def __post_init__(self) -> None:
        """Refuse at construction what would otherwise be printed as a fact.

        :class:`SpotCheckSubject`'s reason: a validating function leaves a
        half-legal value in a variable for anything else to read, a validating
        constructor does not. Three refusals, and the first is the one that has
        already happened on this project -- C7's own keyword-only test exists
        because ``trend(points, baseline, "gpt-candidate-v2")`` would otherwise
        build a line from a model id's individual characters. ``tuple(models)``
        of a ``str`` is that same bug with a nicer error message nobody sees.
        """
        if isinstance(self.models, str):
            raise TypeError(
                f"models is a sequence of candidate ids, not one id: got {self.models!r}. "
                f"A single-candidate lineage is ({self.models!r},) -- passed as a bare "
                "string it is a line built from the id's individual characters."
            )
        models = tuple(self.models)
        if any(not isinstance(one, str) for one in models):
            raise TypeError(
                "models holds candidate ids, and one of these is not a string: "
                f"{[type(one).__name__ for one in models]}. A lineage assembled from "
                "RunPoints is CandidateLineage.assumed_from(points, baseline_model=...)."
            )
        object.__setattr__(self, "models", models)
        if self.source not in _LINEAGE_SOURCES:
            raise ValueError(
                "source must be one of "
                + " or ".join(repr(one) for one in _LINEAGE_SOURCES)
                + f", got {self.source!r}. It is how the caller came to hold this "
                "lineage, which only the caller knows, so trend must be told and "
                "cannot be left to decide (R21.5)."
            )
        if self.source == _LINEAGE_DECLARED and not models:
            raise ValueError(
                "a declared lineage names at least one candidate id. An empty "
                "declaration is a config that declared nothing, which is the case "
                "R21.5 rules is assumed and said out loud: build it with "
                "CandidateLineage.assumed_from(points, baseline_model=...)."
            )

    @classmethod
    def declared(cls, models: Sequence[str]) -> CandidateLineage:
        """The succession a config states, taken at its word.

        The ids are kept exactly as declared, duplicates and all: this is the
        operator's sentence and :attr:`Trend.absent_models` reports against it.
        Where this is used, :func:`trend` raises no caveat about the lineage --
        the declaration is the review path, and R21.5's caveat exists to mark its
        absence, not to second-guess its content.

        ``models`` is handed over uncoerced on purpose: ``tuple`` of a bare
        ``str`` is that string's characters, so converting here would turn one
        model id into a fourteen-member lineage before ``__post_init__`` ever saw
        a string to refuse. One place decides what a lineage is made of, and it
        is the constructor every path goes through.
        """
        return cls(models=models, source=_LINEAGE_DECLARED)  # type: ignore[arg-type]

    @classmethod
    def assumed_from(
        cls, points: Sequence[RunPoint], *, baseline_model: str
    ) -> CandidateLineage:
        """Every distinct candidate on this baseline, in first-appearance order.

        R21.5's part 2, and the order is the order the log was read in -- which
        is `read_series`' order, and is a fact about the file rather than about
        the ids. Nothing here parses an id, ranks a version or sorts.

        **Restricted to ``baseline_model``, which the ruling's "in the series"
        leaves open and ``trend``'s selection settles.** Assume across the whole
        log and a candidate that only ever ran against another baseline joins
        this lineage; ``trend`` then does not select it, and it lands in none of
        the seven fields of :class:`Trend` -- the R24.1 defect, rebuilt by the
        fix for a different one. Restricted this way the assumption covers
        exactly what ``trend`` would draw, so :attr:`Trend.outside_lineage` and
        :attr:`Trend.absent_models` come back empty for the honest reason that
        there is nothing outside the assumption and nothing declared that never
        ran -- not because the fields stopped working.

        **A run whose ``candidate_model`` is unrecorded is not a candidate id
        and does not enter the lineage.** ``""`` is an absence, and two runs that
        both recorded nothing are not thereby the same model -- C4's rule that an
        unrecorded value never matches, not even another unrecorded one, decided
        this everywhere else in the module and decides it here. Admitting it
        would draw a :class:`Succession` from ``""`` to a real id, which is an
        assertion that the model changed made out of a field nobody wrote. Left
        out, those runs come back in :attr:`Trend.outside_lineage` and the reader
        is told the line does not cover them. ``.strip()`` through
        :func:`_recorded`, because a padded field recorded nothing either.
        """
        return cls(
            models=tuple(
                dict.fromkeys(
                    point.candidate_model
                    for point in points
                    if point.baseline_model == baseline_model
                    and _recorded(point.candidate_model)
                )
            ),
            source=_LINEAGE_ASSUMED,
        )

    @property
    def is_assumed(self) -> bool:
        """Whether :func:`trend` owes the reader R21.5's caveat for this lineage."""
        return self.source == _LINEAGE_ASSUMED


def _assumed_lineage(models: tuple[str, ...]) -> str:
    """R21.5's caveat, in the words :func:`trend` owes for an assumed succession.

    Composed here rather than by whoever renders it, and that is the ruling
    rather than a preference: R21.5 forbids the wiring inventing this sentence
    and R26.4 refused the same shape a second time for ``spot_check``. If
    plumbing may write a producer's prose once, nothing downstream is obliged to
    say it the same way twice.

    It names the ids, on :class:`Exclusion`'s rule that a reason names the field
    *and both values* -- "the succession was assumed" is a verdict, and the
    reader needed the evidence. The ``flagged:`` opening is C4's, so a page that
    already prints caveats prints this one without a second vocabulary for the
    same idea.
    """
    if not models:
        return (
            "flagged: no candidate lineage was declared, and this baseline recorded "
            "no candidate the log could name, so there is no succession here -- "
            "neither a declared one nor an assumed one. An empty line and a line "
            "nobody declared are different pages; this is both."
        )
    if len(models) == 1:
        return (
            f"flagged: no candidate lineage was declared, so {models[0]} -- the one "
            "candidate this baseline recorded -- was assumed from the log to be the "
            "whole succession. Nothing states what should have run: a candidate that "
            "has not run yet, or ran against another baseline, cannot be reported "
            "missing from a lineage nobody wrote down."
        )
    return (
        "flagged: no candidate lineage was declared, so the "
        f"{len(models)} candidates this baseline recorded -- "
        + ", then ".join(models)
        + " -- were assumed from the log to be one succession, in the order they "
        "first appear in it. That is a policy and not a reading of the ids: two "
        "unrelated candidates measured into one log are drawn here as one line, "
        "and only a declaration in config tells them apart."
    )


class Succession(NamedTuple):
    """One model id giving way to the next, *inside* a single line.

    A line drawn across a succession is still one line -- the operator declared
    these ids one lineage, and joining them is the whole point of R15 -- but a
    reader who is not told will read an unbroken line as one unbroken model.
    That is the reading this report exists to prevent, so the join leaves
    :func:`trend` as data rather than being left for whoever draws the chart to
    re-derive. R15.4 wants the same change said in three places (the strip, a
    rule on the timeline, a caption in words) precisely because each is silent in
    a different failure; three renderings deriving the succession three ways is
    three chances to name three different instants.
    """

    #: Into :attr:`Trend.points`, of the **first** run under :attr:`after` -- not
    #: the last run under :attr:`before`. The succession is a property of the new
    #: run, and an index pointing at the old one would draw the mark a night early.
    index: int
    before: str
    after: str
    #: The new run's ``created``, verbatim, so a caption can date the change
    #: without indexing back into ``points`` and re-deciding what "at" means.
    #: **A raw string and it stays one**: normalising here would make this the
    #: second place in the package that decides what a timestamp is, and
    #: :func:`parse_created` is public precisely so the caption can parse it
    #: itself and get the same answer the line was sorted by.
    created: str


class Trend(NamedTuple):
    """One candidate lineage as one line, plus everything that did not make it.

    Seven fields rather than the bare ``tuple[RunPoint, ...]`` C7 first
    specified, because a bare tuple has room only for presences. C7's contract
    said the caller "learns of [undated points] separately" and then returned a
    type through which nothing whatever could be learned; R15.3 names that as
    the third instance of one defect class in this plan (C4's flag with no
    field, C13's counts, this). A contract that promises to report an absence
    needs somewhere to report it.

    :attr:`caveats` is the fourth instance, caught in the type R15.3 wrote to
    close the class: :func:`partition_comparable` returns three tuples and the
    first draft of this one had room for two, so the A/A-calibration and
    uneven-coverage notes were computed and dropped on the floor.

    :attr:`outside_lineage` and :attr:`absent_models` are the fifth and sixth,
    and R24.1 found them by declaring the lineage one character wrong: the run
    that fell out of the declaration appeared in *none* of the five fields
    above, so a fourteen-night log rendered as a clean thirteen-night line
    stating that nothing moved and night 14 was mentioned nowhere on the page.
    R15.1 created that hole and said so without noticing -- it replaced suffix
    inference with operator declaration and observed that a wrong split now
    "requires the operator to declare it wrong... precisely the case where a
    reader most needs to notice", and there was no field in which to notice it.

    Every field after the fourth is appended **last** as it arrives, and that is
    deliberate rather than incidental: this is a :class:`NamedTuple`, so prefix
    unpacking and every comparison written against an earlier shape still read,
    which is the same backward-compatible discipline R15.5 applies to
    ``timeline_svg``. Nothing is ever inserted in the middle.
    """

    #: Ascending by parsed ``created``; points sharing an instant keep input order.
    points: tuple[RunPoint, ...]
    successions: tuple[Succession, ...]
    #: Points that may not share a line with the rest, each with the sentence
    #: that says why -- C4's type, because joining two ids onto one axis is a
    #: comparability claim and C4 is what adjudicates those (R15.2).
    excluded: tuple[Exclusion, ...]
    #: How many otherwise-comparable points were dropped for a ``created`` no
    #: axis can place them on. A count, because a count is what the caller was
    #: promised and what C10/C14 render.
    undated: int
    #: Notes on points that were **kept**, not reasons for removing any -- C4's
    #: type again, and one point may carry more than one. Unfiltered: see
    #: :func:`trend`. **The first may be about the line rather than a point**: an
    #: assumed lineage is disclosed here (R21.5) and carries ``point=None``,
    #: because the succession is a property of the whole chart and pinning it to
    #: one night would read as a note about that night.
    caveats: tuple[Caveat, ...]
    #: Runs on **this baseline** whose ``candidate_model`` the operator did not
    #: declare, in log order. Deliberately not an :class:`Exclusion` and
    #: deliberately not in :attr:`excluded`: an exclusion is a comparability
    #: verdict on a run of this line, and these were never adjudicated because
    #: they were never selected. But neither are they somebody else's
    #: experiment, which is what a differently-*based* run is -- they are in
    #: this comparison family, measured against the same baseline, and their
    #: absence from the chart is a claim about the *declaration* rather than
    #: about the run. Keeping the two apart is the whole of R24.1.
    outside_lineage: tuple[RunPoint, ...]
    #: Declared candidate ids with **no run anywhere in the log**, in the order
    #: they were declared and de-duplicated. This is where a one-character typo
    #: in the declaration surfaces, which is the case most likely to be an
    #: operator error rather than a fact about the data: a model the operator
    #: named and the log has never heard of is not a quiet night, and a page
    #: that omits it says the lineage was drawn in full when it was not.
    absent_models: tuple[str, ...]


def trend(
    points: Sequence[RunPoint],
    *,
    baseline_model: str,
    lineage: CandidateLineage,
) -> Trend:
    """The one line for a candidate lineage, and what it left out.

    Args:
        points: Every point in the log, in the order it was read.
        baseline_model: The other side of the comparison. A point measured
            against a different baseline is not a run of this line at all, so it
            is simply not selected -- it is not "excluded", it is not
            :attr:`Trend.outside_lineage` either, and putting it in
            :attr:`Trend.excluded` would bury the exclusions that matter under
            every other experiment in the log.
        lineage: **Supplied by the caller, never inferred.** The ids the operator
            asserts are one candidate over time, *and* whether anyone actually
            asserted them -- see :class:`CandidateLineage`. Required, keyword-only
            and with no default, on :func:`spot_check`'s rule: a default would be
            this function deciding the very fact R21.5 says only the caller holds.
            Order carries no meaning; time comes from ``created``.

    **The lineage is a fact about the world, and no log records it.** Stripping a
    trailing ``-v2`` to decide it succeeds ``-v1`` is the obvious implementation
    and it is forbidden (R15.1): a wrong guess draws two unrelated models as one
    line, which is the "two unrelated numbers side by side" failure
    ``_require_comparable`` exists to prevent, reached from a new direction. The
    operator knows the lineage; the operator says so. Nothing here parses a model
    id.

    **When nobody said so, the line is still drawn and the assumption is
    printed.** R21.5: absent a declaration the ids are assumed from the log, and
    :attr:`Trend.caveats` carries a note saying the succession was assumed and
    not declared. Three things about that, each of which was ruled rather than
    chosen here:

    * **This function does not decide which case it is in.** It cannot: a
      ``Sequence[str]`` from a config and a ``Sequence[str]`` somebody built out
      of these very points are the same object. :class:`CandidateLineage` carries
      the fact, and a lineage that is not one is a ``TypeError`` at the call site
      rather than a silently declared-by-default line. The rejected defaults are
      worth naming, because both are one line of code: defaulting to *declared*
      claims a review path that does not exist, and defaulting to *assumed*
      prints a false caveat over a config that did the right thing.
    * **The caveat is raised here and nowhere downstream.** R21.5 forbids the
      wiring inventing it -- "plumbing that quietly patches a producer's honesty
      is the one shape of this defect nobody would find" -- and R26.4 refused the
      same shape again for ``spot_check``'s sentence. If plumbing may compose a
      producer's prose once, the rule is gone.
    * **It is raised whether or not there is a line to qualify.** The note is
      about the *lineage*, which exists (or was never written) independently of
      whether any run matched it, so it survives the empty early return below. A
      log with nothing in it and a log nobody declared a succession for are two
      different pages, and the second is the commoner one.

    It goes **first** in :attr:`Trend.caveats`, ahead of the partition's
    per-point notes, and it is the one entry there with no ``point``: it
    qualifies the chart, not a night. A renderer walking caveats into rows must
    ask before it indexes.

    **Rejected, and not to be reopened** (R21.5): defaulting an undeclared
    lineage to the headline candidate alone -- it rebuilds the exact defect R15
    removed, since filtering on the field that moves is what made the change
    invisible -- and inferring the succession from the shape of the ids.

    **This used to filter by the field that moves, and that is what hid the
    change.** With ``candidate_model`` in the filter, night 14 under ``-b-v2``
    landed in a different series from night 13 under ``-b-v1``, so
    :func:`parameter_strip` saw ``previous=None`` and reported the ``model_id``
    row as ``changed=False`` against an empty ``before`` -- the first run of a
    series changed nothing, because there was nothing to change from. The strip
    was always able to show the change and was prevented by its own caller. A
    single-element ``candidate_models`` is therefore the old behaviour, not a new
    one: this is a generalisation.

    **Joining ids onto one line asserts they are comparable, so it is checked.**
    The selected points are partitioned through :func:`partition_comparable`
    against the line's anchor key (see :func:`_anchor`), and the refusals travel
    out in :attr:`Trend.excluded`. A lineage whose members disagree on golden
    set, judges, ``n_per_item`` or baseline is not one line and must not be drawn
    as one -- and that is as true of one id measured across an edited golden set
    as it is of two ids, which is why the partition is not conditional on there
    being two.

    **Comparability is decided before datedness, and a point can only be lost
    once.** A run that is both incomparable and undated leaves through
    :attr:`Trend.excluded`, carrying the sentence that names the field and both
    values; :attr:`Trend.undated` therefore counts exactly the points that would
    otherwise have been drawn and could not be placed. Counting such a run in
    both would report one lost night as two, and counting it only as undated
    would trade a reason for a tally.

    **The caveats come out whole, and are not filtered to the drawn rows.** A
    point kept by the partition but dropped as undated has no row at all, which
    makes its caveat the *only* surviving trace of it -- :attr:`Trend.undated` is
    a bare count and names no point. Dropping it to tidy the tuple would be this
    plan's own defect class a fifth time. A :class:`Caveat` carries its own
    point, so a renderer that has no row for one can say so; it cannot invent one
    it was never handed. (The reason first given for carrying them all was that
    every point in :attr:`Trend.points` has a row for its note to print against,
    so there was nothing to filter on that count. That is true, does no work, and
    was retracted in ``0b84d52`` because it is silent about the kept-but-undated
    point, which is the whole case. It is recorded here only so that nobody
    reinstates it as the reason.)

    **A run this line did not draw is still a run, and the reader is told which
    kind.** Three fates are distinguished and they are three different claims.
    A run on another ``baseline_model`` is not selected at all and appears
    nowhere: it is somebody else's experiment, and listing it would bury the
    refusals that matter under every other comparison in the log. A run on
    *this* baseline whose candidate the operator did not declare leaves through
    :attr:`Trend.outside_lineage` -- it is in this comparison family and its
    absence is a claim about the declaration. A declared id with no run anywhere
    in the log leaves through :attr:`Trend.absent_models`, which is where a
    one-character typo in the declaration surfaces. Neither goes in
    :attr:`Trend.excluded`, which is reserved for the comparability verdicts C4
    actually reached (R24.1).

    Both are computed before the anchor and are returned even when there is no
    line at all: a lineage declared entirely wrong selects nothing, and that is
    exactly the case where a page saying "no runs" and a page saying "fourteen
    runs, none of them declared" must not look the same.

    Nothing is de-duplicated, here or anywhere else in this module: two identical
    runs are two runs.
    """
    if not isinstance(lineage, CandidateLineage):
        raise TypeError(
            "lineage must be a CandidateLineage saying which candidate ids are one "
            "succession and where that succession came from, got "
            f"{type(lineage).__name__}. Build it with CandidateLineage.declared(...) "
            "where a config declares it and CandidateLineage.assumed_from(...) where "
            "nothing does: a bare sequence of ids cannot say which, and R21.5 rules "
            "that trend must not decide."
        )
    declared = frozenset(lineage.models)
    mine: list[RunPoint] = []
    strangers: list[RunPoint] = []
    for point in points:
        if point.baseline_model != baseline_model:
            continue
        (mine if point.candidate_model in declared else strangers).append(point)
    outside_lineage = tuple(strangers)
    # Over the whole log and not over `mine`: "no run at all" is the claim, and a
    # declared id that ran only against some other baseline has run. `dict` and
    # not a `set` so the declaration's own order survives into the report.
    logged = {point.candidate_model for point in points}
    absent_models = tuple(dict.fromkeys(id_ for id_ in lineage.models if id_ not in logged))
    # Built before either return, because both owe it. The early return below is
    # the path a wholly undeclared log takes, which is the commonest way to reach
    # an assumed lineage in the first place -- disclosing it only on the path that
    # draws a line would lose it exactly where there is least else to read.
    assumed = (
        (Caveat(point=None, reason=_assumed_lineage(lineage.models)),)
        if lineage.is_assumed
        else ()
    )

    anchor = _anchor(mine)
    if anchor is None:
        return Trend((), (), (), 0, assumed, outside_lineage, absent_models)

    kept, excluded, caveats = partition_comparable(mine, against=comparability_key(anchor))
    dated = [
        (moment, point) for point in kept if (moment := parse_created(point.created)) is not None
    ]
    # Sorted on the parsed instant alone, never on the pair: RunPoint has no
    # ordering, so a tie would fall through to comparing two of them and raise.
    # `sorted` is stable, which is the whole of how "input order preserved" for
    # points sharing an instant is kept.
    ordered = tuple(point for _, point in sorted(dated, key=lambda pair: pair[0]))
    return Trend(
        ordered,
        _successions(ordered),
        excluded,
        len(kept) - len(dated),
        assumed + caveats,
        outside_lineage,
        absent_models,
    )


def _anchor(points: Sequence[RunPoint]) -> RunPoint | None:
    """The point whose comparability key defines the line, or ``None`` for no line.

    **The earliest run anchors, because a line is defined by its history.** If
    the newest run anchored instead, a night that changed the golden set would
    not join the series -- it would evict the thirteen nights that came before
    it, and the report would show one point and twelve refusals. The established
    series keeps the axis; the divergent newcomer is the one excluded.

    **The earliest run that identifies a group anchors, not merely the earliest.**
    :attr:`ComparabilityKey.is_identifying` exists for exactly this caller: a key
    with an unrecorded field identifies nothing, and under C4's rule an
    unrecorded value never matches -- not even another unrecorded one. Anchoring
    on such a point excludes every point in the log including itself, so a line
    whose oldest night predates the field being logged at all would render empty
    rather than render the nights that do agree. Skipping to the first key that
    can define a group excludes the silent run alone, which is the run the reader
    needs told about.

    Falling back to the earliest point when none identifies is deliberate and not
    a rescue: the partition then excludes everything, and an empty line with a
    reason per point beats an empty line with no reasons.

    **Undated points rank after every dated one** and cannot displace one, since
    a point with no instant has no claim to being first. Let one anchor and the
    whole line vanishes: its key becomes the group's, every dated night that
    disagrees is excluded, and the chart is empty with a refusal per night.

    **This is the opposite of :data:`_UNDATED`, and both are right.** C5 sorts a
    dateless row *oldest* and this ranks an undated run *last*, which reads like
    a contradiction and is not one, because the two answer different questions.
    C5's is display order in a table, where the reader can see the blank
    ``stale_days`` cell and position carries no ranking claim. This one is which
    run **defines the axis**, and an undated run that wins it takes the line with
    it. *Sort it oldest; never let it anchor.* R24.4: the cross-reference is here
    and on :data:`_UNDATED` so that the next reader who notices the asymmetry
    does not "fix" one of them.
    """
    if not points:
        return None
    moments = [parse_created(point.created) for point in points]
    dated = sorted((moment, index) for index, moment in enumerate(moments) if moment is not None)
    ranked = [points[index] for _, index in dated]
    ranked += [points[index] for index, moment in enumerate(moments) if moment is None]
    for point in ranked:
        if comparability_key(point).is_identifying:
            return point
    return ranked[0]


def _successions(points: tuple[RunPoint, ...]) -> tuple[Succession, ...]:
    """Every place the candidate id changes between adjacent points on the line.

    Adjacency in the *sorted* series, so a succession is a fact about the order a
    reader sees. Read off the finished tuple rather than computed alongside it so
    that :attr:`Succession.index` cannot index anything but
    :attr:`Trend.points` -- an index into a pre-sort list is an index into a
    sequence nobody is holding.
    """
    return tuple(
        Succession(
            index=index,
            before=points[index - 1].candidate_model,
            after=points[index].candidate_model,
            created=points[index].created,
        )
        for index in range(1, len(points))
        if points[index].candidate_model != points[index - 1].candidate_model
    )


@dataclass(frozen=True)
class ParameterChange:
    """One tracked parameter, as it stood before and after, and whether it moved.

    Frozen for :class:`RunPoint`'s reason: this row is the evidence licensing an
    attribution, and a row that can be edited after it is built is a row the
    chart beneath it can be made to disagree with.
    """

    name: str
    #: Rendered for a reader, never compared: hashes are truncated here, and both
    #: of the ways a value can be missing are spelled out in words rather than
    #: left blank.
    #:
    #: **Never *blank*, which is a stronger promise than "never ``""``".** The
    #: failure mode this field exists to prevent is a cell a reader takes for
    #: "held", and ``"   "`` reads as held quite as well as ``""`` does while
    #: satisfying an emptiness guarantee to the letter. So the guarantee is
    #: ``value.strip()`` -- :func:`_recorded`'s test, the same one
    #: :func:`_text_cell` and :func:`_hash` use to choose between a value and
    #: :data:`UNRECORDED` (R24.5).
    before: str
    after: str
    changed: bool


#: The tracked parameters, in the order they render. A tuple and not a set: the
#: strip's argument is that the *whole* list appears every time, and a list whose
#: order shifts between renders of one log is a list a reader cannot diff by eye.
#:
#: **Every one of them is identifier-safe, and ``"golden set"`` was not.** These
#: strings are keys, not labels: a template deriving a CSS class, an anchor id or
#: a dict key from ``row.name`` worked on five rows and broke on the sixth, which
#: is the worst shape a defect can have -- rare enough to ship and systematic
#: enough to be wrong every time. So the golden-set row is ``goldenset``, matching
#: ``RunPoint.goldenset_hash``, and the space is gone (R24.6). The contract fixed
#: these strings, so this is a ruling and not a drift; **the display label is the
#: template's job**, which is where labels belong, and
#: :class:`ParameterChange` stays at four fields.
_TRACKED_PARAMETERS = (
    "model_id",
    "n_per_item",
    "items",
    "judges",
    "goldenset",
    "config",
)


class _Cell(NamedTuple):
    """One side of one row, kept as two values because they answer two questions.

    ``value`` is what decides :attr:`ParameterChange.changed`: the **full**
    hash, never the display prefix. Two runs whose ``judges_hash`` agrees for 16
    characters and differs at the 17th changed panel, and a strip that compared
    what it printed would say they held. ``shown`` is what a reader sees, and it
    is where the truncation and the word for "unrecorded" live.

    **``value`` is empty *or blank* when the run recorded nothing, and the test
    is :func:`_recorded` -- never ``== ""``.** An earlier draft of this sentence
    claimed the two were interchangeable, "so one emptiness test decides for
    hashes, ids and counts alike", and it was false in the one place it mattered:
    a field a writer padded holds ``"   "``, recorded nothing, and is not ``""``.
    :func:`_parameter_change` has always guarded with :func:`_recorded`, so the
    code was right while the docstring invited the tidy that breaks it -- probed,
    the swap turns a padded hash against a real one into ``changed=True,
    "judges changed"`` from a padding artifact. What *is* uniform is the
    predicate, not the literal: one call to :func:`_recorded` decides for hashes,
    ids and counts alike, because :func:`_count_cell` spells a count's absence
    ``""`` on the way in.
    """

    value: str
    shown: str


#: What a ``before`` cell reads when there was no previous run at all. Display
#: text in a value position, on :data:`UNRECORDED`'s precedent and for its
#: reason: an absence a reader has to act on is spelled out in words, because a
#: blank cell in a table of values reads as "same as the row above" and this one
#: is not.
#:
#: **A distinct word, because these are two different absences.** "There was no
#: previous run" and "the value was not recorded" must not print the same word:
#: the first says the comparison could not be made, the second says one side of
#: it was never written down, and a reader who conflates them draws a conclusion
#: about the run from a fact about the log. **"Different from
#: :data:`UNRECORDED`" is not enough and the suite once asked only that**: a
#: marker reading "no previous run recorded" differs from "unrecorded" and still
#: prints both absences as one idea. The word to stay out of is *recorded*, and
#: there is now a test that says so (R24.3).
#:
#: The contract said ``""`` here, twice, and it was wrong -- ruled after the
#: blind suite pinned it. The defence was that on a genuine first run the blank
#: is *true*, and it is; what it is not is legible. Six blank cells are also
#: exactly what a wrongly-split series renders, since a split makes ``previous``
#: ``None`` too, so the top of a real series and the middle of a broken one are
#: the same six blanks and the same six ``changed=False``. That
#: indistinguishability is the whole of what R15 exists to kill. R15 made the
#: split need a wrong declaration from the operator rather than a version
#: suffix, which makes it rare -- and rare is exactly when a reader needs the
#: cell to say something rather than nothing.
#:
#: **Public for :data:`UNRECORDED`'s reason (R24.6).** A first-run cell and an
#: unrecorded cell are the two absences this constant exists to keep apart, and a
#: template cannot style them apart without naming both -- which, private, meant
#: reaching into a private name or hard-coding a literal the comment above
#: forbids. Promoted before C14 types against it.
NO_PREVIOUS_RUN = "no previous run"

#: The whole ``before`` side of a first run. ``value`` stays ``""`` so that
#: :func:`_parameter_change` reads it as nothing to compare: the marker is a
#: rendering and must never become a value two runs could be found equal on.
_BEFORE_FIRST_RUN = _Cell("", NO_PREVIOUS_RUN)


def parameter_strip(previous: RunPoint | None, current: RunPoint) -> tuple[ParameterChange, ...]:
    """Every tracked parameter of ``current``, against ``previous``.

    **One row per tracked parameter, always, including the ones that did not
    move.** That is the whole of the argument this function exists to make: when
    one row moved and everything else held, the drop beneath it is
    *attributable* rather than merely observed. A strip listing only what changed
    cannot make that claim, because the absence of a row is indistinguishable
    from the absence of a record -- and the reader supplies the more flattering
    of the two readings.

    **An unrecorded value must not render as unchanged.** A blank cell reads as
    "held", and a strip whose job is to license an attribution licenses a false
    one the moment it says "judges: held" about a run that never wrote a panel
    hash. So an absence is spelled out in the word this module already uses for
    it, and a row with an absence on either side is never ``changed=True``: what
    a run did not record is not evidence that it changed, and not evidence that
    it did not.

    ``previous is None`` yields every row with ``changed=False`` -- the first run
    of a line changed nothing because there was nothing to change from -- and a
    ``before`` of :data:`NO_PREVIOUS_RUN`, which is a *word* and not a blank.
    See that constant: a blank there is true and illegible, and it renders a
    first run identically to a wrongly-split one. After R15 that case is rare and
    means what it says; before R15 it fired on every model succession, which is
    exactly how the succession stayed invisible.

    Comparison is on full values and display is truncated to
    :data:`_HASH_WIDTH`, in that order and never the reverse.
    """
    before = (
        _cells(previous)
        if previous is not None
        else (_BEFORE_FIRST_RUN,) * len(_TRACKED_PARAMETERS)
    )
    return tuple(
        _parameter_change(name, was, now)
        for name, was, now in zip(_TRACKED_PARAMETERS, before, _cells(current), strict=True)
    )


def _cells(point: RunPoint) -> tuple[_Cell, ...]:
    """One point's tracked parameters, in :data:`_TRACKED_PARAMETERS`' order.

    The order is positional and shared with that tuple rather than being carried
    in a mapping, because the two must not be able to drift into naming one row
    and printing another's value; ``strict=True`` at the one call site turns a
    length mismatch into an error instead of a silently shorter strip.
    """
    return (
        _text_cell(point.candidate_model),
        _count_cell(point.n_per_item),
        _count_cell(point.items),
        _hash_cell(point.judges_hash),
        _hash_cell(point.goldenset_hash),
        _hash_cell(point.config_hash),
    )


def _parameter_change(name: str, before: _Cell, after: _Cell) -> ParameterChange:
    """One row, with ``changed`` answered only where both sides actually spoke.

    Three states are collapsed into one boolean here, so which two share a
    ``False`` matters: "the same" and "one side is silent" both give
    ``changed=False``, and only the rendered values tell them apart. That is why
    the absence has a word -- the boolean cannot carry the distinction and must
    not be asked to.
    """
    return ParameterChange(
        name=name,
        before=before.shown,
        after=after.shown,
        changed=_recorded(before.value) and _recorded(after.value) and before.value != after.value,
    )


def _text_cell(value: str) -> _Cell:
    """A recorded string, or the word for having none."""
    return _Cell(value, value if _recorded(value) else UNRECORDED)


def _count_cell(value: int) -> _Cell:
    """A count, with ``0`` read as the absence :class:`RunPoint` documents it to be.

    Both of the counted fields record ``0`` for "the payload did not say", so a
    zero printed as ``0`` would be a measurement this run never made -- and, sat
    beside a previous run's ``0``, would read as a parameter that held.
    """
    return _Cell(str(value), str(value)) if value > 0 else _Cell("", UNRECORDED)


def _hash_cell(value: str) -> _Cell:
    """A hash: compared whole, printed at :data:`_HASH_WIDTH`.

    The truncation lives here, at the display end, and nowhere near the
    comparison in :func:`_parameter_change`. A strip that compared 16-character
    prefixes would pass every test written against 16-character fixtures and be
    wrong only on real hashes, which is a mutant that has already survived a
    whole suite once on this module.
    """
    return _Cell(value, _hash(value))
