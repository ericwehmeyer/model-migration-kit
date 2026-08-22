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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import NamedTuple

from opik_rigor import EvidenceRecord

from .contracts import EVENT_COMPLETION, EVENT_JUDGING_COMPLETED, GoldenItem

__all__ = ["DimensionCounts", "TagCount", "dimension_counts"]

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
    """

    passes: int
    n: int
    items: int


@dataclass(frozen=True)
class DimensionCounts:
    """The per-tag matrix, or a sentence saying why there is not one.

    A refusal is ``available=False`` with a ``reason`` a reader can act on, rather
    than an empty ``by_model``: a missing matrix has several causes, they call for
    different fixes, and ``{}`` is not a sentence anyone can print.
    """

    available: bool
    reason: str
    by_model: Mapping[str, Mapping[str, TagCount]]


@dataclass
class _Acc:
    """One tag's running totals for one model, before it is frozen into a count."""

    passes: int = 0
    n: int = 0
    item_ids: set[str] = field(default_factory=set)


def _declined(reason: str) -> DimensionCounts:
    return DimensionCounts(available=False, reason=reason, by_model={})


def _shown(text: object) -> str:
    if not isinstance(text, str):
        return repr(text)
    return repr(text if len(text) <= _INPUT_SHOWN else text[:_INPUT_SHOWN] + "...")


def dimension_counts(
    records: Iterable[EvidenceRecord],
    items: Mapping[str, GoldenItem],
    *,
    judge: str,
) -> DimensionCounts:
    """Per-tag pass counts for every model in the log, from the log alone.

    ``records`` is what :func:`evidence.stream_records` yields, and it is consumed
    as a stream. A ``judge.verdict`` embeds the input, the output and the judge's
    raw reply for every completion, and holding a log was measured at 5.0-5.8
    times the log's own bytes resident; nothing here materialises the records, and
    the largest thing held is one ``(item id, passed)`` pair per verdict in the
    currently open judging group. ``items`` is ``gs_view["by_id"]``, id -> item.

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
    join to an item at all -- so a dimension view that agrees with the gate above
    it needs no branch here, only this paragraph. Imputed records stay in, from the
    same source: a model that times out has told us something.

    Every failure is a refusal carrying a reason, never a silent approximation.
    Two items sharing an input, a verdict whose input joins to nothing, a judging
    group short of what the log says was written, verdicts left open at the end of
    the stream, and a judge that produced nothing anywhere are all declines.

    The expected size of a judging group is ``graded - imputed - parse_failures``
    for this judge, not ``graded``. ``graded`` counts every ``JudgeRecord``
    written, and an imputed or unparseable record never calls ``evaluate()`` and so
    emits no ``judge.verdict``; comparing against ``graded`` alone would decline
    every run in which a single completion failed.

    Args:
        records: the evidence log, streamed.
        items: golden-set items by id, from ``gs_view["by_id"]``.
        judge: whose verdicts to count. A panel writes one verdict per judge per
            completion, so mixing two judges would multiply every denominator by
            the panel size.

    Returns:
        Counts for every model the log names, or ``available=False`` and a reason.
    """
    by_input: dict[str, str] = {}
    tag_universe: set[str] = set()
    for item_id, item in items.items():
        assert len(set(item.tags)) == len(item.tags), (
            f"golden-set item {item_id!r} carries duplicate tags {item.tags!r}; "
            f"goldenset._parse_tags returns a duplicate-free tuple, so this mapping "
            f"did not come from a parsed golden set"
        )
        first = by_input.get(item.input)
        if first is not None:
            return _declined(
                f"golden-set items {first!r} and {item_id!r} share the same input "
                f"text. A verdict joins to an item by its input -- judge.verdict "
                f"carries no item id -- and a golden set enforces unique ids and not "
                f"unique inputs, so these two cannot be told apart and every verdict "
                f"for either would be attributed to whichever was seen first."
            )
        by_input[item.input] = item_id
        tag_universe.update(item.tags or (UNTAGGED,))

    counts: dict[str, dict[str, _Acc]] = {}
    group: list[tuple[str, bool]] = []
    verdicts_seen = 0
    closes_seen = 0

    def attribute(model_id: str, item_id: str, passed: bool) -> None:
        per_tag = counts.setdefault(model_id, {})
        for tag in items[item_id].tags or (UNTAGGED,):
            acc = per_tag.get(tag)
            if acc is None:
                acc = per_tag[tag] = _Acc()
            acc.n += 1
            if passed:
                acc.passes += 1
            acc.item_ids.add(item_id)

    for record in records:
        payload = record.payload if isinstance(record.payload, Mapping) else {}

        if record.event_type == _EVENT_JUDGE_VERDICT:
            if payload.get("judge") != judge:
                continue
            verdicts_seen += 1
            text = payload.get("input")
            item_id = by_input.get(text) if isinstance(text, str) else None
            if item_id is None:
                return _declined(
                    f"a judge.verdict for judge {judge!r} carries an input that is in "
                    f"no golden-set item: {_shown(text)}. The golden set's hash was "
                    f"already checked against the one the run used, so an unjoinable "
                    f"input means the log and the set disagree in a way that hash did "
                    f"not catch."
                )
            # The id rather than the input: this group is held until the next
            # migkit.judging_completed, and the input is the largest string in the
            # payload.
            group.append((item_id, bool(payload.get("passed"))))

        elif record.event_type == EVENT_JUDGING_COMPLETED:
            closes_seen += 1
            model_id = payload.get("model_id")
            if not isinstance(model_id, str) or not model_id:
                return _declined(
                    f"a migkit.judging_completed record names no model, so the "
                    f"{len(group)} verdict(s) for judge {judge!r} that it closes "
                    f"cannot be attributed to a side."
                )
            graded = payload.get("graded")
            imputed = payload.get("imputed")
            failures = payload.get("parse_failures")
            n_graded = graded.get(judge, 0) if isinstance(graded, Mapping) else 0
            n_imputed = imputed.get(judge, 0) if isinstance(imputed, Mapping) else 0
            n_failed = failures.get(judge, 0) if isinstance(failures, Mapping) else 0
            expected = n_graded - n_imputed - n_failed
            if len(group) != expected:
                return _declined(
                    f"judging for {model_id!r} closed a group of {len(group)} "
                    f"judge.verdict record(s) for judge {judge!r}, but that record "
                    f"says {expected} were written ({n_graded} graded - {n_imputed} "
                    f"imputed - {n_failed} unparseable, since an imputed or "
                    f"unparseable record never reaches evaluate() and so writes no "
                    f"verdict). Judging skips already-graded records on a resume, so "
                    f"a resumed pass writes fewer verdicts to this log than the "
                    f"judged artifact holds, and counting them anyway would "
                    f"under-report every tag by an amount nothing in the log reveals."
                )
            for verdict_item_id, passed in group:
                attribute(model_id, verdict_item_id, passed)
            counts.setdefault(model_id, {})
            group.clear()

        elif record.event_type == EVENT_COMPLETION and payload.get("ok") is False:
            model_id = payload.get("model_id")
            failed_item_id = payload.get("item_id")
            if not isinstance(model_id, str) or not model_id:
                return _declined(
                    "a failed migkit.completion names no model, so the completion it "
                    "records cannot be counted against a side, and dropping it would "
                    "take a failure out of the denominator."
                )
            if not isinstance(failed_item_id, str) or failed_item_id not in items:
                return _declined(
                    f"a failed migkit.completion for {model_id!r} names item "
                    f"{failed_item_id!r}, which is in no golden-set item. Guessing "
                    f"which item failed would move a failure to the wrong tag."
                )
            attribute(model_id, failed_item_id, passed=False)

    if closes_seen == 0:
        return _declined(
            f"the log holds no migkit.judging_completed record, so no verdict can be "
            f"attributed to a model and judge {judge!r} has no side to count under. A "
            f"judging pass either did not run or did not finish."
        )
    if group:
        return _declined(
            f"{len(group)} judge.verdict record(s) for judge {judge!r} are still open "
            f"at the end of the log: no migkit.judging_completed follows them, so "
            f"nothing names which model they belong to. A judging pass did not "
            f"complete."
        )
    if verdicts_seen == 0:
        return _declined(
            f"judge {judge!r} produced no judge.verdict records anywhere in this log. "
            f"Either no judging pass ran under that name, or the panel spells it "
            f"differently there."
        )

    by_model = {
        model_id: {
            # A tag that was in the golden set and produced nothing for this model
            # is a finding rather than an absence, so the key is present and zero.
            tag: (
                TagCount(per_tag[tag].passes, per_tag[tag].n, len(per_tag[tag].item_ids))
                if tag in per_tag
                else TagCount(0, 0, 0)
            )
            for tag in sorted(tag_universe)
        }
        for model_id, per_tag in counts.items()
    }
    return DimensionCounts(available=True, reason="", by_model=by_model)
