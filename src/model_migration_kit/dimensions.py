"""The per-dimension view: how a tag did, and when a tag cannot be judged.

Separate from ``series.py`` for two reasons, and only the first is about size.

``series.py`` is past 600 lines, which the build plan named as the trigger for
splitting this out. That alone would be a filing decision. The one that matters
is what each module is allowed to depend on: a series is a sequence of *runs*,
and a dimension is a slice across the *golden set*, so this module needs
``goldenset`` where ``series`` does not, and a dependency the series does not
need is a dependency the series should not carry.

What both share is the rule from ``evidence.py``: the log is read as a stream and
never as a list. ``judge.verdict`` embeds the input, the output and the judge's
raw reply for every completion, and holding one measured 5.0-5.8 times the log's
own bytes resident. Everything here consumes an iterator and holds counters.

This module computes counts and cells. It renders nothing, reads no file, takes
no path, and does not import ``report`` -- ``report`` imports this.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from opik_rigor import EvidenceRecord, wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

from .contracts import (
    EVENT_COMPARISON,
    EVENT_COMPLETION,
    EVENT_JUDGING_COMPLETED,
    GoldenItem,
)

__all__ = [
    "DimensionCell",
    "DimensionCounts",
    "DimensionTally",
    "MIN_ITEMS_FOR_A_VERDICT",
    "MIN_N_FOR_A_VERDICT",
    "TagCount",
    "UNTAGGED",
    "dimension_cell",
    "dimension_counts",
]

#: rigor's event type for a graded verdict. Typed here rather than imported,
#: which is the opposite of the rule ``contracts.py`` sets for the ``migkit.``
#: events and is deliberate: ``EVENT_JUDGE_VERDICT`` is not in
#: ``opik_rigor.__all__``, and invariant 1 says this package reads rigor's
#: *public* surface. Importing a private constant for tidiness is exactly the
#: reach ``COMPATIBILITY.md`` exists to catch, so the string lives at the single
#: place that reads it.
_EVENT_JUDGE_VERDICT = "judge.verdict"

#: The reserved tag for items carrying none. Empty rather than the word
#: "untagged", because "untagged" is a legal tag: a set that used it would
#: collide with this bucket, and the collision would read as a larger slice
#: rather than as an error. The caller renders this key and never drops it.
UNTAGGED = ""

#: How much of an unjoinable input a refusal quotes. Long enough to recognise the
#: item, short enough that the sentence stays a sentence.
_INPUT_SHOWN = 80


class TagCount(NamedTuple):
    """How one tag did for one model.

    ``n`` counts *verdicts*, not items: a golden set sampled ``n_per_item`` times
    contributes that many verdicts per item, and a failed completion contributes
    one non-pass with no verdict behind it. ``items`` counts the distinct
    golden-set items that contributed at all, which is the floor a reader needs in
    order to tell a slice from an anecdote -- ``passes/n`` over four verdicts
    drawn from one item is not a four-item measurement.

    An item carrying three tags contributes to all three, so a model's columns sum
    to more than its item count. That is the arithmetic rather than a bug in it,
    and the caller has to say so on the page.

    **A failed completion's item joins the distinct-item set.** The contract's
    counting paragraph names ``items`` only for an attributed verdict, and the
    failed-completion rule is silent, so the literal reading would produce a cell
    of ``n=1, items=0``. That is refused here for three reasons. ``items <= n`` is
    an invariant of the pair -- one is the same population as the other with
    repeats collapsed -- and a cell that breaks it is not a smaller measurement
    but an incoherent one. The floor ``items`` feeds asks how many distinct
    golden-set *questions* stand behind a cell, and a question the model failed to
    answer was still asked. And under the literal reading a model that timed out
    on a whole tag would report ``items=0``, so the tag could never clear the item
    floor and would decline as "not enough data" -- turning the model's worst
    result into an absence of results, which is the failure ``R5`` already
    recorded once in this codebase.
    """

    passes: int
    n: int
    #: Named ``items`` on purpose, matching ``comparison.py:615``,
    #: ``report.py:515``, ``report.py:580`` and ``series.py:106``, which all
    #: already carry an ``items: int`` meaning exactly this. The rename that
    #: ``report.py:1206`` performed does not transfer: it protected a *dict-like*
    #: view, where a template writing ``goldenset.items`` silently reaches
    #: ``dict.items`` and renders a bound method. A ``NamedTuple`` has no
    #: ``items`` attribute of its own, so ``cell.items`` can only ever be this
    #: int. The live version of that hazard is on ``by_model`` instead, and the
    #: warning is filed there.
    items: int


@dataclass(frozen=True)
class DimensionCounts:
    """The per-tag matrix, or a sentence saying why there is not one.

    A refusal is ``available=False`` with a ``reason`` a reader can act on, rather
    than an empty ``by_model``: a missing matrix has several causes, they call for
    different fixes, and ``{}`` is not a sentence anyone can print.

    **On a refusal ``by_model`` is empty, and that is a promise rather than an
    accident.** Every guard here is global: a duplicated input poisons the join
    for every model, an unjoinable verdict means the log and the set disagree, and
    a short group means an unknown number of verdicts are missing from a column.
    None of them leaves a subset of cells that happen to be sound, so handing back
    the partial matrix would only offer a caller something it must be disciplined
    enough not to render -- and rendering part of a matrix as if it were the
    matrix is the "missing data stated as zero" failure this codebase has already
    shipped once. Emptiness removes the temptation at the type level.

    **Which model keys exist.** Exactly the models a ``migkit.judging_completed``
    named, and each column carries every tag in the golden set. A side that was
    judged and produced nothing is a column of zeros rather than a missing column:
    the two are rendered next to each other, and a vanishing column would turn a
    comparison into a single reading with no sentence explaining where the other
    one went. No model reaches this mapping by any other route -- a model known
    only from failed completions means the log stops before its judging pass, and
    that is a refusal rather than a column of failures.
    """

    available: bool
    reason: str
    #: Note for whoever renders this: the *outer* and *inner* mappings have a real
    #: ``.items()``, and ``TagCount.items`` is an int, so ``column.items`` and
    #: ``cell.items`` are one keystroke apart and both "work" -- one is a number,
    #: the other a bound method printed into the page. This is the hazard
    #: ``report.py:1206`` names; there it was fixed by renaming, and here the
    #: mapping is the thing that cannot be renamed away.
    by_model: Mapping[str, Mapping[str, TagCount]]


@dataclass
class _Acc:
    """One tag's running totals for one model, before it is frozen into a count."""

    passes: int = 0
    n: int = 0
    item_ids: set[str] = field(default_factory=set)


def _declined(reason: str) -> DimensionCounts:
    return DimensionCounts(available=False, reason=reason, by_model={})


def _excerpt(text: str) -> str:
    """The most of an input this module ever *holds*: enough to quote, never more.

    One character past the limit, so :func:`_shown_excerpt` can still tell a text
    that was truncated from one that happened to end exactly on the limit.
    """
    return text[: _INPUT_SHOWN + 1]


def _shown_excerpt(excerpt: str) -> str:
    if len(excerpt) > _INPUT_SHOWN:
        return repr(excerpt[:_INPUT_SHOWN] + "...")
    return repr(excerpt)


def _shown(text: object) -> str:
    if not isinstance(text, str):
        return repr(text)
    return _shown_excerpt(_excerpt(text))


#: Bytes of digest standing in for an input while the golden set is not yet in
#: hand. Sixteen rather than thirty-two because a digest here is ever compared
#: only against digests of the golden set's *own* inputs -- hundreds or thousands
#: of them, where 128 bits is not a collision anybody meets -- and because the
#: store this sits in is the one thing that has to stay small.
_DIGEST_BYTES = 16


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=_DIGEST_BYTES).digest()


#: What a verdict is filed under: a golden-set item id once the join can be made,
#: and a digest of the input until then.
_Key = str | bytes


@dataclass(frozen=True)
class _Index:
    """The golden set inverted for the join, or the sentence refusing to invert it."""

    by_input: Mapping[str, str]
    by_digest: Mapping[bytes, str]
    tags: frozenset[str]
    reason: str


def _index(items: Mapping[str, GoldenItem]) -> _Index:
    """Invert the golden set once: input -> id, digest -> id, and the tag universe.

    The duplicate-input guard lives here, which makes it a *precondition on
    ``items``* rather than something that fires only when a verdict lands on the
    ambiguous input. Three reasons. The join runs set -> input, so the collision is
    discovered here or not at all; a stream cannot be rewound to re-ask the
    question later. Deferring it would make availability depend on which items
    happened to be sampled, so one golden set would render on Tuesday and refuse
    on Wednesday with nothing in the document to say why. And the defect is in the
    set, not in the run: the fix -- give the two items distinct inputs -- is the
    same whether or not a verdict landed on them. The cost is refusing a matrix
    that would have been arithmetically correct; the price of the other reading is
    a refusal nobody can reproduce.

    ``by_digest`` is built whether or not anything will read it. It is one hash per
    golden-set item, against a log the rest of this module exists to avoid holding,
    and the alternative -- building it only on the deferred path -- would make the
    two phases disagree about what the golden set is, which is the one thing an
    index must not do.
    """
    by_input: dict[str, str] = {}
    by_digest: dict[bytes, str] = {}
    tags: set[str] = set()
    for item_id, item in items.items():
        assert len(set(item.tags)) == len(item.tags), (
            f"golden-set item {item_id!r} carries duplicate tags {item.tags!r}; "
            f"goldenset._parse_tags returns a duplicate-free tuple, so this mapping "
            f"did not come from a parsed golden set"
        )
        first = by_input.get(item.input)
        if first is not None:
            return _Index(
                by_input={},
                by_digest={},
                tags=frozenset(),
                reason=(
                    f"golden-set items {first!r} and {item_id!r} share the same input "
                    f"text. A verdict joins to an item by its input -- judge.verdict "
                    f"carries no item id -- and a golden set enforces unique ids and "
                    f"not unique inputs, so these two cannot be told apart and every "
                    f"verdict for either would be attributed to whichever was seen "
                    f"first."
                ),
            )
        by_input[item.input] = item_id
        by_digest[_digest(item.input)] = item_id
        # The key set is the golden set's own tag universe, so ``UNTAGGED``
        # appears exactly when some item carries no tags and is absent otherwise.
        # This is not in tension with the zero-cells below: a tag that exists in
        # the set and produced nothing is a finding, while an "untagged" row for a
        # set in which every item is tagged is a category that does not exist.
        tags.update(item.tags or (UNTAGGED,))
    return _Index(by_input=by_input, by_digest=by_digest, tags=frozenset(tags), reason="")


def _unjoinable(judge: str, shown: str) -> str:
    return (
        f"a judge.verdict for judge {judge!r} carries an input that is in "
        f"no golden-set item: {shown}. The golden set's hash was "
        f"already checked against the one the run used, so an unjoinable "
        f"input means the log and the set disagree in a way that hash did "
        f"not catch."
    )


def _unknown_item(model_id: str, item_id: object) -> str:
    return (
        f"a failed migkit.completion for {model_id!r} names item "
        f"{item_id!r}, which is in no golden-set item. Guessing "
        f"which item failed would move a failure to the wrong tag."
    )


def _for_judge(mapping: object, judge: str) -> int:
    return mapping.get(judge, 0) if isinstance(mapping, Mapping) else 0


def _judges_named(*sources: object) -> set[str]:
    """Every judge the sources between them mention.

    A judge with an open group and no entry in ``graded`` is a group of verdicts
    the closing record says were never written, and a judge in ``graded`` with no
    open group is the same disagreement seen from the other side. Both are the
    shortfall the group-size check exists to catch, so the check runs over the
    union rather than over either side alone.
    """
    names: set[str] = set()
    for source in sources:
        if isinstance(source, Mapping):
            names.update(str(key) for key in source)
    return names


@dataclass(frozen=True)
class _NoModelClose:
    """A ``migkit.judging_completed`` that named no model, held until a judge asks.

    The sentence names a judge and how many of *that judge's* verdicts the record
    was closing, and neither is known when the record goes past: a tally reads
    every judge the panel wrote and is told which one the document wants
    afterwards. So the two facts that vary are captured and the sentence written on
    demand, rather than a sentence being written about the wrong judge.
    """

    sizes: Mapping[str, int]

    def text(self, judge: str) -> str:
        return (
            f"a migkit.judging_completed record names no model, so the "
            f"{self.sizes.get(judge, 0)} verdict(s) for judge {judge!r} that it "
            f"closes cannot be attributed to a side."
        )


@dataclass
class _Run:
    """One run's tally: everything a single ``migkit.comparison`` closes over.

    Every field here is bounded by the golden set and the panel rather than by the
    log. ``closed`` and ``group`` are keyed by *distinct input*, so a set sampled
    fifty times per item costs what the same set sampled once costs; ``excerpts``
    holds at most :data:`_INPUT_SHOWN` characters per distinct input, which is the
    one thing a refusal has to be able to quote.
    """

    #: judge -> model -> key -> [n, passes]. A model reaches this only once a
    #: ``migkit.judging_completed`` has named it.
    closed: dict[str, dict[str, dict[_Key, list[int]]]] = field(default_factory=dict)
    #: judge -> key -> [n, passes], for the group no ``judging_completed`` has
    #: closed yet. The side comes from ordering, and this is what waits for it.
    group: dict[str, dict[_Key, list[int]]] = field(default_factory=dict)
    #: judge -> verdict *records* in the open group. Not ``len(group)``: the group
    #: is keyed by distinct input and the group-size check counts records.
    group_size: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    closes: int = 0
    closed_models: set[str] = field(default_factory=set)
    #: model -> item id -> failed completions. Judge-independent by construction: a
    #: completion that failed never reached ``evaluate()`` under any judge.
    failed: dict[str, dict[str, int]] = field(default_factory=dict)
    #: ``(the judge this refusal is about or None for every judge, the sentence)``,
    #: in the order the stream produced them -- so the first entry a judge matches
    #: is the one a function returning at the first refusal would have returned.
    refusals: list[tuple[str | None, str | _NoModelClose]] = field(default_factory=list)
    refused: set[str] = field(default_factory=set)
    refused_all: bool = False
    #: digest -> the first :data:`_INPUT_SHOWN` characters of the input behind it.
    #: Populated only while the join is deferred; the joined phase files under the
    #: item id and has the input in hand at the moment it needs to quote it.
    excerpts: dict[bytes, str] = field(default_factory=dict)

    def refuse(self, judge: str | None, reason: str | _NoModelClose) -> None:
        self.refusals.append((judge, reason))
        if judge is None:
            self.refused_all = True
        else:
            self.refused.add(judge)

    def reason_for(self, judge: str) -> str | None:
        for scope, reason in self.refusals:
            if scope is None or scope == judge:
                return reason if isinstance(reason, str) else reason.text(judge)
        return None


class DimensionTally:
    """Per-tag counting in two phases: read the log, then join it to the golden set.

    :func:`dimension_counts` is this class with both phases run back to back, and is
    the shape to reach for whenever the golden set is already in hand. This one
    exists for the caller that cannot have it yet.

    **Why the phases had to come apart.** ``report.from_evidence`` rebuilds the
    document in *one* streaming pass over the evidence log -- C3 requires it and
    ``tests/test_report.py`` counts the text-mode opens of the file -- and the
    golden set's path and hash live in the ``migkit.comparison`` payload, which is
    written *after* judging and is therefore among the last records that pass sees.
    So the verdicts arrive before the thing they join to. Reading the log again is
    the one-pass rule; buffering the verdicts is the memory claim ``evidence.py``
    measured at 5.0-5.8 times the log's own bytes resident, since a ``judge.verdict``
    embeds the input, the output and the judge's raw reply for every completion.
    Both roads were closed, and this is the third: hold neither the records nor the
    inputs, only what a join will need.

    **What survives the pass is bounded by the golden set, not by the log.** A
    verdict is filed under a 16-byte digest of its input, and repeated draws of one
    item collapse onto one key -- so a thousand items judged fifty times each costs
    a thousand entries per side and per judge, not fifty thousand. Beside each key
    sits at most :data:`_INPUT_SHOWN` characters of the input. That excerpt is the
    reason the deferred phase can still write the *same* refusal sentence the
    joined phase writes: the disclosure did not have to be traded away for the
    memory bound, because an excerpt is bounded for exactly the reason the digest
    is.

    **Measured, because "bounded" is a claim and not an argument.** One entry costs
    317 bytes: a 16-byte digest as a ``bytes`` object, the ``[n, passes]`` list, the
    excerpt, and the two dict slots holding them. Against records carrying a 4 KB
    input, an output and a raw reply -- rigor's real shape -- buffering costs 5,211
    bytes *per verdict*. So a thousand items drawn fifty times holds 311 KiB here
    against 248 MB buffered, and the 311 KiB does not move between one draw and
    fifty. That flatness is the property; the ratio is a consequence of it, and
    ``test_repeated_draws_of_one_item_cost_the_deferred_store_nothing_extra`` pins
    it.

    **Where the bound is the golden set's, and where it is not.** Entries are keyed
    by distinct *input*, and a real judging pass draws its inputs from the golden
    set, so distinct inputs are the set's size and the paragraph above holds. An
    input that joins to no item is the exception, and the two phases are not
    symmetric about it. The joined phase recognises it immediately, latches the
    refusal, and every later verdict for that judge returns at the top of
    :meth:`_verdict` -- so the store stops growing at the first one. The deferred
    phase cannot recognise it at all: that is what "deferred" means, and the golden
    set that would settle it has not arrived. So it files the digest and the
    excerpt like any other, and a log of inputs that join to nothing grows the
    store linearly at those 317 bytes, with no bound but the log.

    This is not hypothetical -- it is exactly what ``_inflate`` in
    ``tests/test_evidence_scale.py`` writes, one distinct synthetic input per
    record, and therefore what the peak-allocation guard there measures. It passes
    with room: 2.68 MB peak on a 24 MB log against that test's 8 MB ceiling, where
    the same guard measured 2.28 MB before this module was wired in. The ceiling is
    not reached until roughly 18,000 non-joining verdicts, about 220 MB of log; the
    86 MB log that motivated ``stream_records`` lands near 4.5 MB. It is recorded
    rather than fixed because no fix preserves the semantics: the entries are
    needed if a later ``migkit.comparison`` closes the run they are accumulating
    into, and nothing in a stream can see that coming.

    An input that is not a string is refused where it is read rather than filed. It
    can join to no golden set at all, so nothing is learned by keeping it, and
    ``repr`` of an arbitrary payload value has no length this module controls.

    **Every judge on the panel is tallied, and the judge is named at the end.**
    ``report`` learns which judge to count from the ``judges`` list on that same
    ``migkit.comparison`` record, so the filter cannot be applied on the way past
    either. A panel writes one verdict per judge per completion, so this multiplies
    the store by the panel size -- three or four, against a log of hundreds of
    thousands of records.

    **The matrix is one run's, and the run is the one the last
    ``migkit.comparison`` closed.** See :meth:`add`.
    """

    def __init__(self, items: Mapping[str, GoldenItem] | None = None) -> None:
        """Args:
        items: the golden set, ``gs_view["by_id"]``, when it is already known.
            Passing it makes the join happen as each verdict goes past, which is
            what :func:`dimension_counts` does; leaving it out defers the join to
            :meth:`counts`, which is what a single pass over an evidence log needs.
        """
        self._by_id = items
        self._index: _Index | None = None if items is None else _index(items)
        self._joined = self._index is not None and not self._index.reason
        self._run = _Run()
        self._closed_run: _Run | None = None

    def add(self, record: EvidenceRecord) -> None:
        """Read one record of the evidence log, holding none of it.

        **A ``migkit.comparison`` ends a run rather than being ignored, and that is
        the answer to "is the matrix per-run or cumulative".** It is per-run.

        A log of fourteen nightly runs holds fourteen judging passes, and summing
        them would print a matrix of fourteen nights directly beneath a banner
        reporting the last one, with nothing on the page able to reconcile the two
        numbers. Everything else ``ReportModel.from_evidence`` reads -- the verdict,
        the judges, the thresholds, the flips, the completeness strip -- comes from
        the *last* ``migkit.comparison``. The timeline is the one deliberately
        cumulative thing in the document and its own docstring says so. The matrix
        sits under the banner and breaks the banner's number down by tag, so it is
        the banner's run or it is a second, unlabelled claim about a different
        population.

        Cumulative is also the unsound reading, not merely the confusing one. A
        golden set can change between nights; the hash check that guards this one is
        against the last comparison's ``goldenset_hash`` alone. Summing across nights
        would add verdicts taken against sets nobody checked against each other,
        over a tag universe that is not the same universe -- and would do it
        silently, because a tag that exists in both sets looks like one column.

        So the tally is snapshotted and reset at every ``migkit.comparison``, and
        :meth:`counts` reports the last snapshot. Two consequences worth stating.
        Verdicts *after* the last comparison belong to a judging pass that was never
        compared -- a night still running, or one that died before deciding -- and
        are read and dropped rather than counted. And a log holding no
        ``migkit.comparison`` at all is one run, which is what a caller handing over
        a hand-built stream of judging records means and is what
        :func:`dimension_counts` has always done.
        """
        payload = record.payload if isinstance(record.payload, Mapping) else {}

        if record.event_type == EVENT_COMPARISON:
            # A comparison closes a run only if there was a run under it. An empty
            # stretch between two comparisons is not a night that judged nothing;
            # it is the tail of the night before, and treating it as a run of its
            # own would let a `migkit.comparison` appended to a log erase a matrix
            # that is still the right one -- which is the same field-invariance
            # `test_prepending_an_earlier_run_changes_no_field_but_the_series`
            # asserts for every other field on the model.
            if self._run.closes or self._run.verdicts or self._run.failed:
                self._closed_run = self._run
            self._run = _Run()
            return

        run = self._run
        if run.refused_all:
            # The refusal is global and already latched; no later record can change
            # the sentence, and accumulating past it would grow a store whose only
            # reader has already declined.
            return

        if record.event_type == _EVENT_JUDGE_VERDICT:
            self._verdict(run, payload)
        elif record.event_type == EVENT_JUDGING_COMPLETED:
            self._close(run, payload)
        elif record.event_type == EVENT_COMPLETION and payload.get("ok") is False:
            self._failure(run, payload)

    def _verdict(self, run: _Run, payload: Mapping[str, Any]) -> None:
        judge = payload.get("judge")
        if not isinstance(judge, str) or judge in run.refused:
            return
        run.verdicts[judge] = run.verdicts.get(judge, 0) + 1

        text = payload.get("input")
        key: _Key
        if not isinstance(text, str):
            run.refuse(judge, _unjoinable(judge, _shown(text)))
            return
        if self._joined:
            assert self._index is not None
            joined = self._index.by_input.get(text)
            if joined is None:
                run.refuse(judge, _unjoinable(judge, _shown(text)))
                return
            # The id rather than the input: this group is held until the next
            # migkit.judging_completed, and the input is the largest string in the
            # payload.
            key = joined
        else:
            key = _digest(text)
            if key not in run.excerpts:
                run.excerpts[key] = _excerpt(text)

        group = run.group.setdefault(judge, {})
        slot = group.get(key)
        if slot is None:
            slot = group[key] = [0, 0]
        slot[0] += 1
        slot[1] += 1 if payload.get("passed") else 0
        run.group_size[judge] = run.group_size.get(judge, 0) + 1

    def _close(self, run: _Run, payload: Mapping[str, Any]) -> None:
        run.closes += 1
        model_id = payload.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            run.refuse(None, _NoModelClose(dict(run.group_size)))
            return

        graded = payload.get("graded")
        imputed = payload.get("imputed")
        failures = payload.get("parse_failures")
        for judge in _judges_named(graded, imputed, failures, run.group):
            if judge in run.refused:
                continue
            n_graded = _for_judge(graded, judge)
            n_imputed = _for_judge(imputed, judge)
            n_failed = _for_judge(failures, judge)
            expected = n_graded - n_imputed - n_failed
            written = run.group_size.get(judge, 0)
            if written != expected:
                run.refuse(
                    judge,
                    f"judging for {model_id!r} closed a group of {written} "
                    f"judge.verdict record(s) for judge {judge!r}, but that record "
                    f"says {expected} were written ({n_graded} graded - {n_imputed} "
                    f"imputed - {n_failed} unparseable, since an imputed or "
                    f"unparseable record never reaches evaluate() and so writes no "
                    f"verdict). Judging skips already-graded records on a resume, so "
                    f"a resumed pass writes fewer verdicts to this log than the "
                    f"judged artifact holds, and counting them anyway would "
                    f"under-report every tag by an amount nothing in the log reveals.",
                )
                continue
            per_model = run.closed.setdefault(judge, {}).setdefault(model_id, {})
            for key, counted in run.group.get(judge, {}).items():
                slot = per_model.get(key)
                if slot is None:
                    slot = per_model[key] = [0, 0]
                slot[0] += counted[0]
                slot[1] += counted[1]
        # A side that was judged gets a column even if it produced nothing: zeros
        # are a finding, a missing column is a silence. Recorded on the run rather
        # than under a judge, because which judge the document will ask for is not
        # known here and a side was judged by the whole panel or by none of it.
        run.closed_models.add(model_id)
        run.group.clear()
        run.group_size.clear()

    def _failure(self, run: _Run, payload: Mapping[str, Any]) -> None:
        model_id = payload.get("model_id")
        item_id = payload.get("item_id")
        if not isinstance(model_id, str) or not model_id:
            run.refuse(
                None,
                "a failed migkit.completion names no model, so the completion it "
                "records cannot be counted against a side, and dropping it would "
                "take a failure out of the denominator.",
            )
            return
        if not isinstance(item_id, str) or (self._joined and not self._knows(item_id)):
            # Refused where it is read when the golden set is already in hand, and
            # at the join when it is not. Either way the item id itself is small, so
            # the deferred path holds it and refuses with the same sentence.
            run.refuse(None, _unknown_item(model_id, item_id))
            return
        per_item = run.failed.setdefault(model_id, {})
        per_item[item_id] = per_item.get(item_id, 0) + 1

    def _knows(self, item_id: str) -> bool:
        return self._by_id is not None and item_id in self._by_id

    def counts(
        self, items: Mapping[str, GoldenItem] | None = None, *, judge: str
    ) -> DimensionCounts:
        """Join what was tallied to the golden set, for one judge.

        The refusals are returned in the order the single-pass form would have
        reached them: whatever the stream latched first, then the join, then the
        four questions only the end of a run can answer -- no judging pass at all,
        a group still open, a judge that wrote nothing, and a model known only from
        the completions that failed.

        Args:
            items: the golden set, ``gs_view["by_id"]``. Required unless the tally
                was built with it; passing it again re-indexes the set, so the
                caller that already handed it over passes nothing.
            judge: whose verdicts to count. A panel writes one verdict per judge per
                completion, so mixing two judges would multiply every denominator by
                the panel size.

        Returns:
            Counts for every model the run named, or ``available=False`` and a
            reason. A refusal never arrives with a partial matrix -- see
            :class:`DimensionCounts`.

        Raises:
            ValueError: if no golden set was given here or at construction. The
                alternative is an empty matrix built against an empty set, which is
                a plausible-looking table of zeros.
        """
        by_id = self._by_id if items is None else items
        index = self._index if items is None else _index(items)
        if by_id is None or index is None:
            raise ValueError(
                "DimensionTally.counts needs the golden set: it was not given one at "
                "construction, so it has to be handed the items it joins against"
            )
        if index.reason:
            return _declined(index.reason)

        run = self._closed_run if self._closed_run is not None else self._run

        latched = run.reason_for(judge)
        if latched is not None:
            return _declined(latched)

        counts: dict[str, dict[str, _Acc]] = {}

        def attribute(model_id: str, item_id: str, n: int, passes: int) -> None:
            per_tag = counts.setdefault(model_id, {})
            for tag in by_id[item_id].tags or (UNTAGGED,):
                acc = per_tag.get(tag)
                if acc is None:
                    acc = per_tag[tag] = _Acc()
                acc.n += n
                acc.passes += passes
                acc.item_ids.add(item_id)

        for model_id, keyed in run.closed.get(judge, {}).items():
            counts.setdefault(model_id, {})
            for key, counted in keyed.items():
                if isinstance(key, str):
                    item_id: str | None = key
                else:
                    item_id = index.by_digest.get(key)
                    if item_id is None:
                        return _declined(
                            _unjoinable(judge, _shown_excerpt(run.excerpts[key]))
                        )
                attribute(model_id, item_id, counted[0], counted[1])

        for model_id, per_item in run.failed.items():
            counts.setdefault(model_id, {})
            for item_id, how_many in per_item.items():
                if item_id not in by_id:
                    return _declined(_unknown_item(model_id, item_id))
                attribute(model_id, item_id, how_many, 0)

        for model_id in run.closed_models:
            counts.setdefault(model_id, {})

        if run.closes == 0:
            return _declined(
                f"the log holds no migkit.judging_completed record, so no verdict can be "
                f"attributed to a model and judge {judge!r} has no side to count under. A "
                f"judging pass either did not run or did not finish."
            )
        if run.group.get(judge):
            return _declined(
                f"{run.group_size.get(judge, 0)} judge.verdict record(s) for judge "
                f"{judge!r} are still open at the end of the log: no "
                f"migkit.judging_completed follows them, so nothing names which model "
                f"they belong to. A judging pass did not complete."
            )
        if run.verdicts.get(judge, 0) == 0:
            return _declined(
                f"judge {judge!r} produced no judge.verdict records anywhere in this log. "
                f"Either no judging pass ran under that name, or the panel spells it "
                f"differently there."
            )
        unclosed = sorted(set(counts) - run.closed_models)
        if unclosed:
            return _declined(
                f"the log names {', '.join(repr(one) for one in unclosed)} only in failed "
                f"migkit.completion records: no migkit.judging_completed ever closes a "
                f"group for that model, so its judging pass did not run or did not "
                f"finish. Counting it anyway would publish a column built entirely out "
                f"of the completions that failed -- a full, plausible matrix in which a "
                f"truncated run reads as a model that got everything wrong."
            )

        by_model = {
            model_id: {
                # A tag that was in the golden set and produced nothing for this
                # model is a finding rather than an absence, so the key is present
                # and zero.
                tag: (
                    TagCount(per_tag[tag].passes, per_tag[tag].n, len(per_tag[tag].item_ids))
                    if tag in per_tag
                    else TagCount(0, 0, 0)
                )
                for tag in sorted(index.tags)
            }
            for model_id, per_tag in counts.items()
        }
        return DimensionCounts(available=True, reason="", by_model=by_model)


def dimension_counts(
    records: Iterable[EvidenceRecord],
    items: Mapping[str, GoldenItem],
    *,
    judge: str,
) -> DimensionCounts:
    """Per-tag pass counts for every model in the log, from the log alone.

    ``records`` is what :func:`evidence.stream_records` yields, and it is consumed
    as a stream. A ``judge.verdict`` embeds the input, the output and the judge's
    raw reply for every completion, and holding a log was measured at 5.0-5.8 times
    the log's own bytes resident; nothing here materialises the records, and the
    largest thing held is one ``(item id, passes, n)`` entry per *distinct input* in
    the currently open judging group. ``items`` is ``gs_view["by_id"]``, id -> item.

    This is :class:`DimensionTally` with both of its phases run back to back, and it
    is the shape to reach for whenever the golden set is already in hand. The caller
    that cannot have it yet -- ``report.from_evidence``, whose single pass meets the
    verdicts long before it meets the ``migkit.comparison`` record that names the
    golden set -- drives the two phases itself.

    **The join is by input text, because a verdict carries no item id.** Judging
    passes the golden-set input through to the judge verbatim, so an item is
    recovered by inverting ``items`` on ``input``.

    **The side comes from ordering, not from the record.** The ``model_id`` on a
    ``judge.verdict`` is the *judge's* model, not the candidate's; using it would
    produce a full, plausible matrix in which both columns held the same numbers.
    Verdicts accumulate instead until a ``migkit.judging_completed`` closes the
    group and names whose they were, which is structural rather than incidental --
    ``compare`` judges the two runs strictly in sequence.

    Failed completions need no such trick. They never reach ``evaluate()`` and so
    write no verdict, and without them the denominator would silently lose exactly
    the completions the model failed, which is the one bucket a pass rate must not
    lose. ``migkit.completion`` carries both the item id and the sampled model on
    the record itself, so each ``ok=false`` is one non-pass under that model.

    **Parse failures are absent by construction, and that is the correct
    behaviour.** ``comparison.py`` drops them from both the numerator and the
    denominator of the pass rate, on the ground that an unparseable answer is the
    *judge* having been unintelligible, and that conflating the two would let an
    unreliable judge read as an unreliable model. In the log they are
    ``judge.parse_failure`` records, which carry no ``input`` and therefore cannot
    join to an item at all -- so a dimension view that agrees with the gate above it
    needs no branch here, only this paragraph. Imputed records stay in, from the
    same source: a model that times out has told us something.

    Every failure is a refusal carrying a reason, never a silent approximation. Two
    items sharing an input, a verdict whose input joins to nothing, a judging group
    short of what the log says was written, verdicts left open at the end of the
    stream, a model seen only in failed completions, and a judge that produced
    nothing anywhere are all declines.

    **"No judging_completed at all" and "this judge produced no verdicts" stay two
    refusals, checked in that order.** They fire together on an empty log, so it is
    tempting to merge them, and merging them would cost the reader the only thing a
    refusal is for. The first says judging never ran, and the fix is to run it. The
    second says judging ran and wrote nothing under this name, and the fix is to
    check how the panel spells the judge. Answering the second question to someone
    whose real problem is the first sends them hunting a name that was never wrong,
    so the no-judging check goes first. The distinguisher is the sentence and
    deliberately not a machine-readable code: the caller prints ``reason``, and a
    code that is only ever printed is a second home for a fact that already has one.

    The expected size of a judging group is ``graded - imputed - parse_failures`` for
    this judge, not ``graded``. ``graded`` counts every ``JudgeRecord`` written, and
    an imputed or unparseable record never calls ``evaluate()`` and so emits no
    ``judge.verdict``; comparing against ``graded`` alone would decline every run in
    which a single completion failed.

    A log holding more than one ``migkit.comparison`` is more than one run, and what
    comes back is the last complete one rather than the sum -- see
    :meth:`DimensionTally.add`. A stream with no comparison record in it, which is
    what a hand-built list of judging records is, is one run and is counted whole.

    Args:
        records: the evidence log, streamed.
        items: golden-set items by id, from ``gs_view["by_id"]``.
        judge: whose verdicts to count. A panel writes one verdict per judge per
            completion, so mixing two judges would multiply every denominator by the
            panel size.

    Returns:
        Counts for every model the log names, or ``available=False`` and a reason.
    """
    tally = DimensionTally(items)
    # The golden set is checked before a single record is read, and the stream is
    # not touched at all when that check fails: see :func:`_index` for why the
    # duplicate-input guard is a precondition rather than a lazy trigger.
    refusal = "" if tally._index is None else tally._index.reason
    if refusal:
        return _declined(refusal)
    for record in records:
        tally.add(record)
    return tally.counts(judge=judge)

# --- C9: the cell, the refusal, and the two floors ---------------------------
#
# ``DEFAULT_CONFIDENCE`` is imported from ``opik_rigor.distribution`` rather than
# from the package root because the root does not re-export it, and the root is
# where ``wilson_interval`` comes from two lines up. It is in
# ``distribution.__all__``, so it is rigor's public surface and invariant 1 holds
# -- but it is the first submodule import anywhere in ``src/``, and the cheaper
# fix lives upstream: rigor re-exporting the constant beside the function that
# defaults to it.
#
# Two floors, and a cell must clear both.
#
# ``MIN_N_FOR_A_VERDICT`` counts completions and is the older of the two. It does
# not do the job on its own. At ``n_per_item=5`` a tag carrying four items
# produces exactly twenty completions, ``20 < 20`` is ``False``, and the cell
# renders a verdict -- so the effective floor was four items. Four is the number
# in the spec's own refusal sentence, the showpiece example of a cell that must
# decline, which means the completions floor as written passed the one case it
# exists to fail.
#
# ``MIN_ITEMS_FOR_A_VERDICT`` counts distinct items and is the fix. Twenty
# completions drawn from four items are not twenty observations: they are four
# questions asked five times each, correlated by construction because every draw
# within an item shares a prompt, a reference and a rubric clause. A dimension
# verdict generalises over *questions*, so the sample size that matters here is
# nearer four than twenty. That is also why a larger completions floor is not the
# fix: at ``n_per_item=10`` the same four questions would clear a floor of forty
# just as easily.
#
# Ten is a judgement, not a derivation, and it is worth saying so plainly. The
# two constraints that are forced -- refuse the spec's four-item example, clear
# the showcase at sixteen -- narrow the number to the band 5-16 and no further.
# Ten is a choice inside that band, and the choice is a leverage tolerance: no
# single golden-set item should move a published dimension claim by more than a
# tenth. Argue with the tolerance and you have argued with the constant, which is
# the right way round.
#
# The floors are independent on purpose. Neither subsumes the other -- twelve
# items at one draw each clears the item floor and fails the completions floor --
# and collapsing them into one number loses whichever case the survivor cannot
# see.

MIN_N_FOR_A_VERDICT: int = 20
MIN_ITEMS_FOR_A_VERDICT: int = 10


@dataclass(frozen=True)
class DimensionCell:
    """One tag's row: what was measured, and whether it may be read as a verdict.

    ``rate``, ``interval`` and ``floor`` are the numbers a renderer draws.
    ``verdict_refused`` is the only field that decides whether it may draw them as
    a judgement, and it is settled by sample size alone -- never by how the
    interval happens to sit against ``floor``. A refused cell still shows its
    interval; what it does not do is colour it.

    ``needed`` and ``needed_unit`` travel as a pair and answer "what would make
    this cell answerable", in the one unit the reader can act on. They are
    ``None`` and ``""`` together, never one without the other, and they are empty
    in two different situations: the cell is not refused, or nothing was measured
    at all and there is no shortfall to quantify.

    **Why one number and a unit rather than ``needed_items`` and
    ``needed_completions``.** Ruled at review; the case for two typed fields was
    good and is recorded here because it will be made again.

    It runs: a unit beside a single number can express only one floor, so when
    both bind the second fact has to be smuggled into prose. Two nullable ints
    would carry both at the type level, delete the stringly-typed unit, and
    reduce "which one is actionable" to ``needed_items or needed_completions``.

    That last clause is why the answer is no. Which floor to name when both bind
    is not a formatting detail, it is the whole substance of the two-floor
    ruling: naming the completions floor while items are short is advice that
    does not work, because more draws multiply the same few questions. Under this
    shape the module decides once and no consumer can get it wrong. Under two
    fields the decision moves into every renderer, where ``needed_completions or
    needed_items`` is a one-token slip that reproduces exactly the defect the
    ruling exists to prevent -- and it is a slip no type would catch, because
    both spellings type-check.

    The premise that the second fact lives only in prose is also not quite true.
    A cell carries ``n`` and ``items``; the floors it was judged against travel
    with the matrix that holds it, because a document that refuses a cell has to
    be able to say what it refused against. A renderer that wants the completions
    shortfall as a number subtracts two fields it already has. The note is a
    convenience for a human reader, not the only carrier.

    One correction to the ground this was argued on: the both-bind case is not
    rare. It was said to be reachable only at one draw per item or on a ragged
    tag, and that is the region where the *completions* floor binds **alone**.
    Both floors bind together across 50 uniform (items, draws) pairs -- every
    tag with nine or fewer items and fewer than twenty completions, so four items
    at three draws each lands there. (Fifty, enumerated over the whole grid; an
    earlier pass through this docstring said 33, which was this reviewer making
    the same class of error it had just corrected in R11.6.)

    That is the ordinary shape of a young golden set, which cuts toward this
    shape rather than away from it: a decision taken on most refused cells is
    worth taking once, correctly, inside the module.

    ``note`` is the cell's disclosure line rather than only its refusal sentence.
    It carries whichever of these apply: that nothing was measured, that a floor
    is unmet, that a *second* floor is also unmet, and that no confidence level
    was supplied so rigor's default stands behind the printed interval.

    ``floor`` is echoed from the input on every cell, including one where nothing
    was measured. It is not a derived field and the ``n == 0`` rule does not
    reach it.
    """

    tag: str
    passes: int
    n: int
    items: int
    rate: float | None
    interval: tuple[float, float] | None
    floor: float | None
    verdict_refused: bool
    needed: int | None
    needed_unit: str
    note: str


def dimension_cell(
    tag: str,
    passes: int,
    n: int,
    items: int,
    *,
    confidence: float | None,
    floor: float | None,
    min_n: int = MIN_N_FOR_A_VERDICT,
    min_items: int = MIN_ITEMS_FOR_A_VERDICT,
) -> DimensionCell:
    """One :class:`DimensionCell`, refusing the verdict when the sample cannot carry it.

    **Takes four plain integers rather than a counts object.** The counting and
    the cell are written against each other's contract and not against each
    other's types, so nothing here imports what the counter defines.

    **The refusal rule is sample size, and only sample size.** ``verdict_refused``
    is ``True`` when ``n < min_n`` or ``items < min_items``, however the interval
    sits against ``floor``. The tempting alternative -- "refuse when the interval
    is too wide to decide" -- is a different and worse rule, because a narrow
    interval can be produced by a tiny sample that happens to be unanimous: four
    passes out of four would answer, on four draws of one question. Declining is
    the differentiator this whole document is built on, and a rule a narrow
    interval can talk out of refusing does not decline.

    **When both floors bind, ``needed`` names items**, which is not a style
    preference. It is the only one of the two a reader can act on. "You need more
    completions" sends someone to raise ``n_per_item``, and raising ``n_per_item``
    cannot fix an item shortfall -- it multiplies the same few questions. So the
    actionable floor is the one the pair reports.

    **The note still names the other floor when it also binds**, because the pair
    cannot and someone has to. A reader at four items and four completions who is
    told only "six more items" adds six items at one draw each, arrives at ten
    items and ten completions, and is refused a second time on a floor nobody
    mentioned -- having done exactly what the note asked. For a tool whose whole
    claim is that it declines honestly, that is the worst available second
    impression.

    **``n == 0`` is a rendering state, not a computation.** ``wilson_interval(0,
    0)`` raises ``ValueError("a rate over zero runs is not a rate")``, which is
    correct of it and useless here: a tag that was in the golden set and produced
    nothing judged is a finding to display, not an exception to propagate. So the
    zero case calls nothing and every derived field is ``None``.

    **``floor`` is carried, not consulted.** It arrives from the run's gate for
    the renderer's benefit and is echoed on every cell including the empty one;
    nothing in this function compares an interval to it, which is the cheapest way
    to guarantee that no refused cell is quietly judged against it.

    **A defaulted confidence is disclosed whether or not the cell is refused**,
    because the alternative is a printed interval whose confidence level the
    reader cannot know, and that misleads in a way an extra sentence does not. It
    is disclosed only where an interval was actually computed: at ``n == 0``
    nothing consumed the default, and claiming otherwise would be a disclosure of
    something that did not happen.

    Args:
        confidence: ``None`` falls back to rigor's ``DEFAULT_CONFIDENCE``, and the
            fallback is recorded in ``note``. It is never silent.
        min_n: Completions floor. Overridable so a caller -- or a mutation test --
            can move it independently of ``min_items``.
        min_items: Distinct-items floor. Independent of ``min_n`` in both
            directions; moving one must not change what the other refuses.

    Raises:
        ValueError: ``passes > n``, a corrupt count that must not render.
            ``items > n``, which is impossible and means the caller mispaired two
            numbers. Any negative count, which nothing downstream would catch --
            ``items`` in particular is validated nowhere else.
    """
    if passes < 0 or n < 0 or items < 0:
        raise ValueError(
            f"counts for {tag!r} cannot be negative: passes={passes}, n={n}, items={items}"
        )
    if passes > n:
        raise ValueError(f"more passes than completions for {tag!r}: {passes} > {n}")
    if items > n:
        raise ValueError(f"more items than completions for {tag!r}: {items} > {n}")

    level = DEFAULT_CONFIDENCE if confidence is None else confidence

    rate: float | None = None
    interval: tuple[float, float] | None = None
    if n > 0:
        rate = passes / n
        interval = wilson_interval(passes, n, level)

    short_items = items < min_items
    short_n = n < min_n

    needed: int | None
    refusals: list[str] = []
    if n == 0:
        # No shortfall to quantify. "Six more items" implies you have some, and at
        # zero the honest statement is different in kind -- so the note says
        # nothing was measured and neither floor is named as a shortfall.
        needed, needed_unit = None, ""
    elif short_items:
        needed, needed_unit = min_items - items, "items"
        refusals.append(f"{min_items} items needed for a verdict here; you have {items}.")
        if short_n:
            # The floor that is not actionable is still named, because a reader
            # who is told only about items adds six single-draw items, lands at
            # ten items and ten completions, and is refused a second time on a
            # floor nobody mentioned -- after doing exactly what the note asked.
            refusals.append(f"The {min_n}-completion floor is also unmet: you have {n}.")
    elif short_n:
        needed, needed_unit = min_n - n, "completions"
        refusals.append(f"{min_n} completions needed for a verdict here; you have {n}.")
    else:
        needed, needed_unit = None, ""

    sentences: list[str] = []
    if n == 0:
        sentences.append(f"Nothing was measured for {tag}.")
    sentences.extend(refusals)
    if confidence is None and interval is not None:
        sentences.append(
            f"No confidence level was given, so rigor's default of {level:.0%} was used."
        )

    return DimensionCell(
        tag=tag,
        passes=passes,
        n=n,
        items=items,
        rate=rate,
        interval=interval,
        floor=floor,
        verdict_refused=short_items or short_n,
        needed=needed,
        needed_unit=needed_unit,
        note=" ".join(sentences),
    )
