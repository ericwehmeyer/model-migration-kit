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

from opik_rigor import EvidenceRecord, wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

from .contracts import EVENT_COMPLETION, EVENT_JUDGING_COMPLETED, GoldenItem

__all__ = [
    "DimensionCell",
    "DimensionCounts",
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
    the stream, a model seen only in failed completions, and a judge that produced
    nothing anywhere are all declines.

    **"No judging_completed at all" and "this judge produced no verdicts" stay two
    refusals, checked in that order.** They fire together on an empty log, so it
    is tempting to merge them, and merging them would cost the reader the only
    thing a refusal is for. The first says judging never ran, and the fix is to
    run it. The second says judging ran and wrote nothing under this name, and the
    fix is to check how the panel spells the judge. Answering the second question
    to someone whose real problem is the first sends them hunting a name that was
    never wrong, so the no-judging check goes first. The distinguisher is the
    sentence and deliberately not a machine-readable code: the caller prints
    ``reason``, and a code that is only ever printed is a second home for a fact
    that already has one.

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
    # The golden set is checked before a single record is read, and the
    # duplicate-input guard below is therefore a *precondition on ``items``*
    # rather than something that fires only when a verdict lands on the ambiguous
    # input. Three reasons. The join runs set -> input, so the collision is
    # discovered here or not at all; a stream cannot be rewound to re-ask the
    # question later. Deferring it would make availability depend on which items
    # happened to be sampled, so one golden set would render on Tuesday and refuse
    # on Wednesday with nothing in the document to say why. And the defect is in
    # the set, not in the run: the fix -- give the two items distinct inputs -- is
    # the same whether or not a verdict landed on them. The cost is refusing a
    # matrix that would have been arithmetically correct; the price of the other
    # reading is a refusal nobody can reproduce.
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
        # The key set is the golden set's own tag universe, so ``UNTAGGED``
        # appears exactly when some item carries no tags and is absent otherwise.
        # This is not in tension with the zero-cells below: a tag that exists in
        # the set and produced nothing is a finding, while an "untagged" row for a
        # set in which every item is tagged is a category that does not exist.
        tag_universe.update(item.tags or (UNTAGGED,))

    counts: dict[str, dict[str, _Acc]] = {}
    closed_models: set[str] = set()
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
            # A side that was judged gets a column even if it produced nothing:
            # zeros are a finding, a missing column is a silence.
            counts.setdefault(model_id, {})
            closed_models.add(model_id)
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
    unclosed = sorted(set(counts) - closed_models)
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
