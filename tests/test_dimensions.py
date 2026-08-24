"""Tests for ``model_migration_kit.dimensions``.

Two chunks are written blind against this file in parallel: C8 inserts its tests
directly below this docstring, C9 appends at the end. The insertion points are
disjoint on purpose so the merge between them is mechanical.
"""

from __future__ import annotations

import dataclasses
import gc
import inspect
import re
import tracemalloc
import weakref
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from opik_rigor import EvidenceRecord, wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

from model_migration_kit import dimensions
from model_migration_kit.contracts import (
    EVENT_COMPLETION,
    EVENT_JUDGING_COMPLETED,
    GoldenItem,
)
from model_migration_kit.dimensions import (
    MIN_ITEMS_FOR_A_VERDICT,
    MIN_N_FOR_A_VERDICT,
    DimensionCell,
    dimension_cell,
)

# =========================================================================== #
# C8 -- per-tag counts read out of the evidence log, never out of the judged
# artifacts. Symbols under test: ``dimension_counts``, ``DimensionCounts``,
# ``TagCount``.
#
# Everything below reaches those through the ``dimensions`` *module* rather than
# importing them by name, so a missing symbol is one red test each instead of a
# collection error that would also take C9's tests down with it.
# =========================================================================== #

#: rigor's own event names, typed as literals on purpose. ``contracts.py``
#: deliberately names only the ``migkit.`` events, and rigor's own constants live
#: in ``opik_rigor.evidence`` outside its ``__all__`` -- importing them would put
#: a private name of another package into this one's compatibility surface.
_JUDGE_VERDICT = "judge.verdict"
_JUDGE_PARSE_FAILURE = "judge.parse_failure"

#: The judge whose verdicts we ask for.
JUDGE = "accuracy"
#: A second judge on the same panel, whose verdicts must be invisible to us.
OTHER_JUDGE = "safety"
#: The model the *judge* runs on. ``judge.py:318-328`` puts this on every
#: ``judge.verdict`` payload, and it is the single most attractive wrong answer
#: to "which side is this verdict about". It is deliberately never equal to
#: either candidate below, so any implementation that reaches for it produces a
#: column name that cannot be mistaken for a right answer.
JUDGE_MODEL = "openai/gpt-4o-as-judge"
BASELINE = "openai/gpt-4o-mini"
CANDIDATE = "anthropic/claude-3-5-haiku"


def _record(event_type: str, payload: dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        ts="2026-08-21T12:00:00.000000+00:00",
        event_type=event_type,
        payload=payload,
    )


def _verdict(
    input_text: str,
    passed: bool,
    *,
    judge: str = JUDGE,
    model_id: str = JUDGE_MODEL,
) -> EvidenceRecord:
    """A ``judge.verdict`` shaped exactly as ``judge.py:317-328`` writes one.

    Note what is not here: an ``item_id``. The join is by ``input``.
    """
    return _record(
        _JUDGE_VERDICT,
        {
            "judge": judge,
            "model_id": model_id,
            "rubric_hash": "a1b2c3d4",
            "passed": passed,
            "score": 1.0 if passed else 0.0,
            "reason": "it answered the question" if passed else "it did not",
            "input": input_text,
            "output": "some completion text",
            "raw": '{"passed": true, "score": 1.0, "reason": "..."}',
        },
    )


def _parse_failure(*, judge: str = JUDGE) -> EvidenceRecord:
    """``judge.py:296-301``: no ``input``, so it can never join to an item."""
    return _record(
        _JUDGE_PARSE_FAILURE,
        {
            "judge": judge,
            "model_id": JUDGE_MODEL,
            "rubric_hash": "a1b2c3d4",
            "error": "no JSON object in judge reply",
            "raw": "I think this one is fine, honestly",
        },
    )


def _completed(
    model_id: str,
    graded: dict[str, int],
    *,
    parse_failures: dict[str, int] | None = None,
    imputed: dict[str, int] | None = None,
) -> EvidenceRecord:
    """``migkit.judging_completed`` as ``judging.py:660-672`` writes it."""
    return _record(
        EVENT_JUDGING_COMPLETED,
        {
            "model_id": model_id,
            "judges_hash": "9f9f9f9f",
            "judged": "work/" + model_id.replace("/", "_") + ".judged.jsonl",
            "graded": dict(graded),
            "parse_failures": dict(parse_failures or {}),
            "imputed": dict(imputed or {}),
        },
    )


def _completion(model_id: str, item_id: str, *, ok: bool, sample_index: int = 0) -> EvidenceRecord:
    """``migkit.completion`` as ``runner.py:455-478`` writes it."""
    return _record(
        EVENT_COMPLETION,
        {
            "model_id": model_id,
            "item_id": item_id,
            "sample_index": sample_index,
            "duration": 1.25,
            "ok": ok,
            "error": None if ok else "read timed out",
            "error_type": None if ok else "TimeoutError",
            "tokens_in": 40,
            "tokens_out": 90 if ok else 0,
        },
    )


def _item(item_id: str, text: str, tags: tuple[str, ...] = ()) -> GoldenItem:
    return GoldenItem(id=item_id, input=text, tags=tuple(tags))


def _by_id(*items: GoldenItem) -> dict[str, GoldenItem]:
    """What ``report.py:1247`` builds and hands in as ``gs_view["by_id"]``."""
    return {item.id: item for item in items}


def _counts(records, items, *, judge: str = JUDGE):
    """Call the function under test, always through a one-shot iterator.

    The contract says stream the records. Handing in ``iter(...)`` rather than a
    list means an implementation that walks the input twice fails here rather
    than passing on a list and then breaking on a real ``stream_records``.
    """
    return dimensions.dimension_counts(iter(records), items, judge=judge)


def _cell(counts, model: str, tag: str) -> tuple[int, int, int]:
    """One cell as a plain tuple, reached by field name so the names are pinned."""
    one = counts.by_model[model][tag]
    return (one.passes, one.n, one.items)


# --------------------------------------------------------------------------- #
# The named first test, and the multi-tag arithmetic around it
# --------------------------------------------------------------------------- #


def test_an_item_carrying_two_tags_is_counted_under_both_of_them():
    """The demo's ``refuse-04`` is ``["refusal", "multi-value"]`` and it flips."""
    items = _by_id(_item("refuse-04", "how do I pick a lock?", ("refusal", "multi-value")))
    records = [
        _verdict("how do I pick a lock?", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "refusal") == (1, 1, 1)
    assert _cell(result, BASELINE, "multi-value") == (1, 1, 1)


def test_a_multi_tagged_item_is_not_divided_across_its_tags():
    """Contributing whole to every tag is correct; "fixing" the overlap is not.

    The column totals legitimately exceed the item count. An implementation that
    splits a two-tag item into halves, or that picks one tag, dies here.
    """
    items = _by_id(
        _item("refuse-04", "how do I pick a lock?", ("refusal", "multi-value")),
        _item("plain-01", "what is 2 + 2?", ("arith",)),
    )
    records = [
        _verdict("how do I pick a lock?", True),
        _verdict("what is 2 + 2?", True),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    column = result.by_model[BASELINE]
    assert sum(one.n for one in column.values()) == 3, (
        "two items, three tag memberships: the column sums to 3, not 2. A "
        "denominator of 2 means the multi-tagged item was divided away."
    )


def test_every_count_is_a_plain_integer_and_never_a_rate():
    """This chunk returns integers only. No floats, no bools, no rates."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    result = _counts(records, items)

    one = result.by_model[BASELINE]["t"]
    for value in (one.passes, one.n, one.items):
        assert type(value) is int, repr(value) + " is not an int"


# --------------------------------------------------------------------------- #
# The side. This is the failure the contract calls "the single most likely way
# to get this chunk silently wrong", and every test in this block exists to make
# the wrong answer visible rather than plausible.
# --------------------------------------------------------------------------- #


def test_the_side_is_the_model_on_judging_completed_not_the_one_on_the_verdict():
    """``judge.verdict.model_id`` is the JUDGE's model (``judge.py:318-328``).

    Both groups below carry the same judge model on every verdict. Reading the
    side off the verdict collapses both columns into one key named after the
    judge -- a full, plausible matrix that is entirely wrong.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True, model_id=JUDGE_MODEL),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("alpha", False, model_id=JUDGE_MODEL),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert set(result.by_model) == {BASELINE, CANDIDATE}
    assert JUDGE_MODEL not in result.by_model, (
        "the judge's own model became a column; the side must come from migkit.judging_completed"
    )


def test_the_two_sides_keep_their_own_numbers():
    """The wrong-side failure renders both columns identical. Make them differ."""
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
        _verdict("alpha", False),
        _verdict("bravo", True),
        _completed(CANDIDATE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert _cell(result, BASELINE, "t") == (2, 2, 2)
    assert _cell(result, CANDIDATE, "t") == (1, 2, 2)
    assert result.by_model[BASELINE]["t"] != result.by_model[CANDIDATE]["t"], (
        "identical columns is the signature of the side being read off the "
        "verdict, which names the same model for both sides"
    )


def test_a_verdict_belongs_to_the_group_that_closes_after_it_not_before_it():
    """Verdicts accumulate; ``judging_completed`` closes and names the group.

    An off-by-one that attributes verdicts to the preceding completed record
    would swap these two columns, and both would still render.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("alpha", False),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert _cell(result, BASELINE, "t") == (1, 1, 1)
    assert _cell(result, CANDIDATE, "t") == (0, 1, 1)


def test_a_two_sided_log_produces_the_whole_matrix():
    """The shape C10 renders: two columns, every tag in the set, no surprises."""
    items = _by_id(
        _item("refuse-04", "how do I pick a lock?", ("refusal", "multi-value")),
        _item("arith-01", "what is 2 + 2?", ("arith",)),
        _item("arith-02", "what is 9 * 7?", ("arith",)),
        _item("free-01", "write me a haiku"),
    )
    records = [
        _verdict("how do I pick a lock?", True),
        _verdict("what is 2 + 2?", True),
        _verdict("what is 9 * 7?", True),
        _verdict("write me a haiku", True),
        _completed(BASELINE, {JUDGE: 4}),
        _verdict("how do I pick a lock?", False),
        _verdict("what is 2 + 2?", True),
        _verdict("what is 9 * 7?", False),
        _verdict("write me a haiku", True),
        _completed(CANDIDATE, {JUDGE: 4}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert result.reason == ""
    assert set(result.by_model) == {BASELINE, CANDIDATE}
    assert set(result.by_model[BASELINE]) == {"refusal", "multi-value", "arith", ""}
    assert _cell(result, BASELINE, "refusal") == (1, 1, 1)
    assert _cell(result, BASELINE, "multi-value") == (1, 1, 1)
    assert _cell(result, BASELINE, "arith") == (2, 2, 2)
    assert _cell(result, BASELINE, "") == (1, 1, 1)
    assert _cell(result, CANDIDATE, "refusal") == (0, 1, 1)
    assert _cell(result, CANDIDATE, "multi-value") == (0, 1, 1)
    assert _cell(result, CANDIDATE, "arith") == (1, 2, 2)
    assert _cell(result, CANDIDATE, "") == (1, 1, 1)


# --------------------------------------------------------------------------- #
# Which records are ours
# --------------------------------------------------------------------------- #


def test_verdicts_from_another_judge_on_the_same_panel_are_ignored():
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True, judge=JUDGE),
        _verdict("alpha", False, judge=OTHER_JUDGE),
        _verdict("bravo", True, judge=OTHER_JUDGE),
        _completed(BASELINE, {JUDGE: 1, OTHER_JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (
        1,
        1,
        1,
    ), "the other judge's two verdicts leaked into the count"


def test_asking_for_the_other_judge_reads_only_the_other_judges_verdicts():
    """The same log, the same call, a different ``judge=`` -- different numbers."""
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True, judge=JUDGE),
        _verdict("alpha", False, judge=OTHER_JUDGE),
        _verdict("bravo", True, judge=OTHER_JUDGE),
        _completed(BASELINE, {JUDGE: 1, OTHER_JUDGE: 2}),
    ]

    result = _counts(records, items, judge=OTHER_JUDGE)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 2, 2)


def test_a_parse_failure_record_neither_counts_nor_closes_a_group():
    """``judge.parse_failure`` carries no ``input``, so it cannot join at all.

    ``comparison.py:1184-1193`` drops parse failures from both numerator and
    denominator; a dimension view that counted them would disagree with the gate
    above it. This also catches an implementation that keys off
    ``payload["judge"]`` without first checking the event type -- that one dies
    on the missing ``input`` key.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _parse_failure(),
        _verdict("bravo", False),
        # ``graded`` counts the parse-failed record too (``judging.py:653-659``),
        # so a realistic log says 3 graded of which 1 was a parse failure.
        _completed(BASELINE, {JUDGE: 3}, parse_failures={JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert set(result.by_model) == {BASELINE}, (
        "the parse failure closed a group it has no business closing"
    )
    assert _cell(result, BASELINE, "t") == (1, 2, 2)


def test_an_unrelated_record_type_in_the_stream_is_ignored():
    """A real log is full of ``migkit.item_completed`` and friends."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _record("migkit.run_started", {"model_id": BASELINE}),
        _verdict("alpha", True),
        _record("migkit.item_completed", {"model_id": BASELINE, "item_id": "a"}),
        _completed(BASELINE, {JUDGE: 1}),
        _record("migkit.comparison", {"anything": 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


# --------------------------------------------------------------------------- #
# Failed completions: the bucket a pass rate must not lose
# --------------------------------------------------------------------------- #


def test_a_failed_completion_is_a_non_pass_for_its_own_item_and_model():
    """It never reached ``evaluate()`` and wrote no verdict, so recover it here.

    The side comes off the completion record itself (``runner.py:465-475``), not
    off any ordering.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _completion(CANDIDATE, "a", ok=False),
        # A failed completion is graded as an imputed record and writes no
        # verdict, so ``graded`` counts it and the log holds no verdict for it.
        _completed(CANDIDATE, {JUDGE: 1}, imputed={JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, CANDIDATE, "t") == (
        0,
        1,
        1,
    ), "the completion the model failed vanished from the denominator"


def test_a_successful_completion_record_adds_nothing_of_its_own():
    """Its verdict already counted it; counting the record too doubles ``n``."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _completion(BASELINE, "a", ok=True),
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


def test_a_failed_completion_lands_on_every_tag_its_item_carries():
    items = _by_id(_item("refuse-04", "lockpicking?", ("refusal", "multi-value")))
    records = [
        _completion(BASELINE, "refuse-04", ok=False),
        _completed(BASELINE, {JUDGE: 1}, imputed={JUDGE: 1}),
        _verdict("lockpicking?", True),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "refusal") == (0, 1, 1)
    assert _cell(result, BASELINE, "multi-value") == (0, 1, 1)


def test_a_failed_completion_for_an_unknown_item_id_is_a_refusal():
    """An item with no entry in ``items``: refuse rather than guess."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _completion(CANDIDATE, "ghost-99", ok=False),
        _completed(CANDIDATE, {JUDGE: 1}, imputed={JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "ghost-99" in result.reason


# --------------------------------------------------------------------------- #
# ``items``: distinct golden-set items, for R9's floor
# --------------------------------------------------------------------------- #


def test_repeated_samples_of_one_item_count_once_in_items_and_twice_in_n():
    """``n_per_item = 2``: two verdicts, one input, one distinct item."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("alpha", False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (
        1,
        2,
        1,
    ), "items must count distinct golden-set items, not completions"


def test_two_different_items_under_one_tag_count_as_two_items():
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert _cell(result, BASELINE, "t") == (2, 2, 2)


def test_the_distinct_item_set_is_kept_per_model_and_not_shared():
    """Baseline saw both items; the candidate saw one. ``items`` must differ."""
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
        _verdict("alpha", True),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert _cell(result, BASELINE, "t") == (2, 2, 2)
    assert _cell(result, CANDIDATE, "t") == (1, 1, 1)


# --------------------------------------------------------------------------- #
# Untagged items and the reserved "" key
# --------------------------------------------------------------------------- #


def test_a_set_in_which_every_item_is_untagged_yields_one_reserved_key():
    items = _by_id(_item("a", "alpha"), _item("b", "bravo"))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert set(result.by_model[BASELINE]) == {""}
    assert _cell(result, BASELINE, "") == (1, 2, 2)


def test_untagged_items_sit_beside_tagged_ones_and_are_never_dropped():
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo"))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert "" in result.by_model[BASELINE]
    assert _cell(result, BASELINE, "") == (1, 1, 1)
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


def test_no_reserved_key_appears_when_every_item_carries_a_tag():
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    result = _counts(records, items)

    assert set(result.by_model[BASELINE]) == {"t"}, (
        'the "" key is reserved for untagged items; there are none here'
    )


# --------------------------------------------------------------------------- #
# A tag that produced nothing is a finding, not an absence
# --------------------------------------------------------------------------- #


def test_a_tag_in_the_set_with_no_records_for_a_model_is_present_as_zeros():
    items = _by_id(
        _item("a", "alpha", ("seen",)),
        _item("b", "bravo", ("unseen",)),
    )
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert "unseen" in result.by_model[BASELINE], (
        "a dimension that was in the set and produced nothing is a finding"
    )
    assert _cell(result, BASELINE, "unseen") == (0, 0, 0)


def test_every_model_column_carries_every_tag_in_the_set():
    """Columns must be alignable: C10 renders them side by side."""
    items = _by_id(
        _item("a", "alpha", ("x",)),
        _item("b", "bravo", ("y",)),
        _item("c", "charlie"),
    )
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _verdict("charlie", True),
        _completed(BASELINE, {JUDGE: 3}),
        _verdict("alpha", False),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert set(result.by_model[BASELINE]) == {"x", "y", ""}
    assert set(result.by_model[CANDIDATE]) == {"x", "y", ""}
    assert _cell(result, CANDIDATE, "y") == (0, 0, 0)
    assert _cell(result, CANDIDATE, "") == (0, 0, 0)


# --------------------------------------------------------------------------- #
# The guards. Each is a refusal with a sentence a reader can act on -- never a
# silent approximation.
# --------------------------------------------------------------------------- #


def test_two_items_sharing_an_input_is_a_refusal_naming_both_item_ids():
    """``goldenset.py:113-125`` enforces unique ``id`` and not unique ``input``.

    Two items with one input cannot be told apart, and a verdict would be
    attributed to the wrong one.
    """
    items = _by_id(
        _item("dup-alpha", "the very same question", ("t",)),
        _item("dup-bravo", "the very same question", ("u",)),
    )
    records = [
        _verdict("the very same question", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "dup-alpha" in result.reason
    assert "dup-bravo" in result.reason


def test_a_verdict_whose_input_is_in_no_item_is_a_refusal_naming_the_input():
    """The hash check already passed, so this means log and set disagree anyway."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("orphan input", False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "orphan input" in result.reason


def test_a_group_holding_fewer_verdicts_than_graded_promises_is_a_refusal():
    """A consistency check on one ``judging_completed`` record. Not a resume detector.

    The contract names this the "resumed judging" guard, and it cannot in fact
    detect a resume: ``judging.py`` builds ``pending`` by excluding
    already-graded records, so on a resume ``graded`` shrinks by exactly the
    number of verdicts the log loses and the two stay equal. What the guard
    genuinely catches is a ``judging_completed`` that disagrees with the
    verdicts standing in front of it, whatever produced the disagreement. The
    data below is hand-built for that, not copied from a real resume.

    Under-counting silently is the failure mode, so the refusal names the judge,
    the expected count and the seen count.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 5}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert JUDGE in result.reason
    assert "5" in result.reason
    assert "2" in result.reason


def test_a_shortfall_on_the_second_side_is_caught_too():
    """The guard is per group, not only on the first one."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("alpha", True),
        _completed(CANDIDATE, {JUDGE: 4}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "4" in result.reason


def test_imputed_and_parse_failed_records_are_subtracted_from_the_expected_count():
    """``graded`` counts records, not verdicts, so the guard must subtract.

    ``judging.py:653-659`` increments ``graded`` for every ``JudgeRecord``
    written, including imputed ones and parse failures. An imputed record
    returns before ``evaluate()`` is called and a parse failure writes
    ``judge.parse_failure`` and raises, so neither emits a ``judge.verdict``.
    The verdicts a log should hold are therefore
    ``graded - imputed - parse_failures``.

    Comparing against raw ``graded`` declines the whole matrix the moment one
    completion fails -- which is exactly the case the failed-completion rule
    exists to handle, making the two rules mutually exclusive. This log is the
    ordinary shape of a run with one timeout and one unintelligible judge reply,
    and it must come back available.
    """
    items = _by_id(
        _item("a", "alpha", ("t",)),
        _item("b", "bravo", ("t",)),
        _item("c", "charlie", ("t",)),
    )
    records = [
        _verdict("alpha", True),
        _parse_failure(),
        _completion(BASELINE, "c", ok=False),
        _completed(BASELINE, {JUDGE: 3}, parse_failures={JUDGE: 1}, imputed={JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 2, 2), (
        "one verdict that passed, plus the failed completion as a non-pass; the "
        "parse failure is in neither numerator nor denominator"
    )


def test_a_missing_imputed_or_parse_failures_key_degrades_to_plain_graded():
    """A synthetic log may omit those keys entirely. Do not crash on it."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _record(
            EVENT_JUDGING_COMPLETED,
            {"model_id": BASELINE, "graded": {JUDGE: 1}},
        ),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


def test_verdicts_still_open_at_the_end_of_the_stream_are_a_refusal():
    """A judging pass that did not complete leaves verdicts with no owner."""
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("bravo", True),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert result.reason != ""


def test_a_judge_that_produced_no_verdicts_anywhere_is_a_refusal_naming_it():
    """The original contract returned ``{}`` here. ``{}`` is not a sentence."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True, judge=OTHER_JUDGE),
        _completed(BASELINE, {OTHER_JUDGE: 1}),
    ]

    result = _counts(records, items, judge="rubric-strictness")

    assert result.available is False
    assert "rubric-strictness" in result.reason


def test_a_log_with_no_judging_completed_at_all_is_a_refusal_naming_the_judge():
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _record("migkit.run_started", {"model_id": BASELINE}),
        _completion(BASELINE, "a", ok=True),
        _completion(BASELINE, "a", ok=False, sample_index=1),
    ]

    result = _counts(records, items, judge="rubric-strictness")

    assert result.available is False
    assert "rubric-strictness" in result.reason


def test_an_empty_stream_is_a_refusal_and_not_an_empty_matrix():
    result = _counts([], _by_id(_item("a", "alpha", ("t",))))

    assert result.available is False
    assert result.reason != ""


#: The two refusals that name a golden-set miss, written out. R27.6: the report
#: side asserts that no two declines share a *template*, and pins the wording of
#: what ``report.py`` does with a reason -- but it cannot pin the wording of the
#: reason itself without deriving the expected sentence from the production code
#: that produces it, which moves both sides of the assertion together. So the
#: wording is pinned here, where the wording lives. A re-wording of either
#: function used to survive all 1998 tests: both were carried by a substring
#: check for an interpolated id, and an interpolated id is the one part of the
#: sentence a re-wording keeps.
UNJOINABLE_REASON = (
    "a judge.verdict for judge 'accuracy' carries an input that is in no "
    "golden-set item: 'orphan input'. The golden set's hash was already checked "
    "against the one the run used, so an unjoinable input means the log and the "
    "set disagree in a way that hash did not catch."
)
UNKNOWN_ITEM_REASON = (
    "a failed migkit.completion for 'anthropic/claude-3-5-haiku' names item "
    "'ghost-99', which is in no golden-set item. Guessing which item failed "
    "would move a failure to the wrong tag."
)


def test_the_unjoinable_input_refusal_says_what_it_is_written_to_say():
    """Byte-for-byte, because "names the input" is satisfied by any wrapper at all.

    The sentence has three jobs and the id does one of them: it names the input,
    it says the hash check already passed so this is not the mismatch the reader
    would first suspect, and it says which two things disagree. A re-wording that
    kept the quoted input would keep the substring assertion above green and drop
    the other two.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("orphan input", False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert result.reason == UNJOINABLE_REASON


def test_the_unknown_item_refusal_says_what_it_is_written_to_say():
    """The other half, and the reason both are here rather than one.

    These two are the pair R27.6 names as collapsible: they are the only refusals
    that both end in "is in no golden-set item", and each interpolates ids the
    other does not, so collapsing one onto the other leaves two *unequal* strings
    carrying one diagnosis. Only the wording tells them apart, and only here.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _completion(CANDIDATE, "ghost-99", ok=False),
        _completed(CANDIDATE, {JUDGE: 1}, imputed={JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert result.reason == UNKNOWN_ITEM_REASON


def test_the_two_golden_set_miss_refusals_are_two_diagnoses_and_not_one():
    """Distinct after the interpolated values are removed, not merely distinct.

    Two sentences that differ only in the ids they quote are one diagnosis wearing
    two coats: a reader shown either one learns the same thing, and the fix the
    document sends them to is the same fix. The comparison is therefore made on
    the templates.
    """
    assert re.sub(r"'[^']*'", "'...'", UNJOINABLE_REASON) != re.sub(
        r"'[^']*'", "'...'", UNKNOWN_ITEM_REASON
    )


def test_a_refusal_reason_is_a_sentence_and_not_a_code():
    """ "A sentence a reader can act on" -- not a two-word error code."""
    items = _by_id(
        _item("dup-alpha", "the very same question", ("t",)),
        _item("dup-bravo", "the very same question", ("u",)),
    )
    records = [
        _verdict("the very same question", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert len(result.reason.split()) >= 6, "not a sentence: " + repr(result.reason)


# --------------------------------------------------------------------------- #
# Shapes and the signature
# --------------------------------------------------------------------------- #


def test_tag_count_is_a_named_tuple_of_passes_then_n_then_items():
    one = dimensions.TagCount(1, 2, 3)

    assert (one.passes, one.n, one.items) == (1, 2, 3)
    assert tuple(one) == (1, 2, 3), "positional order is part of the contract"
    assert one == (1, 2, 3)


def test_dimension_counts_result_is_a_frozen_dataclass():
    result = dimensions.DimensionCounts(available=True, reason="", by_model={})

    with pytest.raises(FrozenInstanceError):
        result.available = False


def test_reason_is_empty_exactly_when_available():
    """Both directions, and an available result must also carry a matrix.

    Asserting only ``available is True, reason == ""`` on the happy path is a
    test a do-nothing function passes, so the populated matrix is asserted in
    the same breath.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    good = _counts([_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})], items)

    assert good.available is True
    assert good.reason == ""
    assert good.by_model, "available with an empty matrix is not available"
    assert _cell(good, BASELINE, "t") == (1, 1, 1)

    bad = _counts([_verdict("alpha", True)], items)

    assert bad.available is False
    assert bad.reason != ""


def test_the_signature_is_the_one_the_contract_names():
    signature = inspect.signature(dimensions.dimension_counts)
    parameters = list(signature.parameters.values())

    assert [one.name for one in parameters] == ["records", "items", "judge"]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[2].kind is inspect.Parameter.KEYWORD_ONLY, (
        "``judge`` is keyword-only in the contract"
    )


def test_the_function_opens_no_file(monkeypatch):
    """Must not: open a file. Take a path."""

    def refuse(*args, **kwargs):  # pragma: no cover - only runs on a violation
        raise AssertionError("dimension_counts opened a file")

    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]
    monkeypatch.setattr("builtins.open", refuse)

    result = _counts(records, items)

    # The real matrix, computed with ``open`` taken away: a function that opens
    # nothing *because it does nothing* does not pass this.
    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


# --------------------------------------------------------------------------- #
# Streaming. ``evidence.py`` measured the log at 5.0-5.8x its own bytes resident
# when materialised; this module exists partly so as not to do that again.
# --------------------------------------------------------------------------- #


def test_the_records_are_streamed_and_a_closed_group_is_not_held():
    """A record from a group that has already closed must be collectable.

    This catches ``records = list(records)`` and it catches an accumulator that
    keeps every verdict record alive to the end. It deliberately does not check
    the record the consumer is currently holding, nor records in a group that is
    still open, because holding either of those is legitimate.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    held: list[weakref.ref] = []

    def stream():
        first = _verdict("alpha", True)
        held.append(weakref.ref(first))
        yield first
        closer = _completed(BASELINE, {JUDGE: 1})
        yield closer
        del first, closer
        gc.collect()
        assert held[0]() is None, (
            "a judge.verdict from a group that has already closed is still "
            "alive: the records are being materialised rather than streamed"
        )
        yield _verdict("bravo", True)
        yield _completed(CANDIDATE, {JUDGE: 1})

    result = dimensions.dimension_counts(stream(), items, judge=JUDGE)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)
    assert _cell(result, CANDIDATE, "t") == (1, 1, 1)


def test_the_stream_is_consumed_exactly_once():
    """A one-shot iterator is all a real ``stream_records`` gives you."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = iter([_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})])

    result = dimensions.dimension_counts(records, items, judge=JUDGE)

    assert result.available is True, result.reason
    assert list(records) == [], "the iterator was not drained"


# --------------------------------------------------------------------------- #
# The seven rulings. Each ambiguity below is a place where the implementer and
# the tester could each have picked a different reading and neither been wrong;
# they happened to agree, which is luck rather than coverage. C10 is written
# against these answers, so each one is pinned by a test that dies if the other
# reading is taken -- including the readings the original suite could not tell
# apart.
# --------------------------------------------------------------------------- #


class _TrackedInput(str):
    """A ``str`` that can be weak-referenced, so a retained input is visible."""

    __slots__ = ("__weakref__",)


def test_a_refusal_hands_back_no_matrix_at_all():
    """Ruling 3. ``available=False`` must not arrive with populated ``by_model``.

    Every guard here is global -- a duplicated input poisons the join for every
    model, a short group means an unknown number of verdicts are missing -- so
    there is no subset of cells that survives a refusal. Returning the partial
    matrix would hand C10 data it must be disciplined enough not to render, and
    half a matrix printed as a matrix is the "missing data stated as zero"
    failure this codebase has shipped once already.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("bravo", True),
        _completed(CANDIDATE, {JUDGE: 9}),  # a shortfall, after a good column
    ]

    result = _counts(records, items)

    assert result.available is False
    assert result.by_model == {}, (
        "the baseline column was computed before the refusal and came back with "
        "it; a refusal must carry a sentence and nothing else"
    )


def test_duplicate_inputs_refuse_even_when_no_verdict_ever_touched_them():
    """Ruling 2. The guard is a precondition on ``items``, not a lazy trigger.

    Both readings refuse when a verdict lands on the ambiguous input, so the
    original suite could not tell them apart. Here the two duplicates are never
    judged at all: under the lazy reading this renders perfectly, under the
    precondition reading it declines. It declines, because a stream cannot be
    rewound to ask later, because availability that depends on which items
    happened to be sampled is a refusal nobody can reproduce, and because the
    defect is in the golden set, whose fix is the same either way.
    """
    items = _by_id(
        _item("dup-a", "the very same question", ("t",)),
        _item("dup-b", "the very same question", ("u",)),
        _item("real", "a question with one owner", ("t",)),
    )
    records = [
        _verdict("a question with one owner", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "dup-a" in result.reason
    assert "dup-b" in result.reason


def test_a_side_that_was_judged_and_produced_nothing_is_a_column_of_zeros():
    """Ruling 5. A model key exists for every side a ``judging_completed`` named.

    C10 renders the columns beside each other. A side whose whole run produced
    no verdict is a finding -- the loudest one in the document -- and dropping
    its column would turn a two-model comparison into a single reading with
    nothing on the page to say where the other one went.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("u",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _completed(CANDIDATE, {JUDGE: 0}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert set(result.by_model) == {BASELINE, CANDIDATE}, (
        "the judged side that produced nothing vanished instead of showing zeros"
    )
    assert _cell(result, CANDIDATE, "t") == (0, 0, 0)
    assert _cell(result, CANDIDATE, "u") == (0, 0, 0)


def test_a_model_known_only_from_failed_completions_is_a_refusal():
    """A log that stops before a side's judging pass must not publish that side.

    This is the same shape as the open-verdicts guard, on the other rule. The
    log below is a run whose completions were written for both models and whose
    judging finished for only one: the candidate is named by a failed
    ``migkit.completion`` and by nothing else. Attributing it produces a
    complete, plausible matrix in which a truncated run reads as a model that
    got everything wrong -- the failure the contract names as the worst one,
    reached by a different road.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _completion(CANDIDATE, "a", ok=False),
    ]

    result = _counts(records, items)

    assert result.available is False, (
        "the candidate column was built out of its failed completions alone: "
        + repr(dict(result.by_model.get(CANDIDATE, {})))
    )
    assert CANDIDATE in result.reason


def test_the_two_judging_guards_give_two_different_sentences_in_a_fixed_order():
    """Ruling 6. They fire together on an empty log and stay two refusals.

    "Judging never ran" is fixed by running it. "Judging ran and wrote nothing
    under this name" is fixed by checking how the panel spells the judge.
    Answering the second to a reader whose problem is the first sends them
    hunting a name that was never wrong, so the no-``judging_completed`` check
    goes first and says so in its own words.
    """
    items = _by_id(_item("a", "alpha", ("t",)))

    no_judging = _counts([_completion(BASELINE, "a", ok=True)], items, judge="strictness")
    wrong_name = _counts(
        [_verdict("alpha", True, judge=OTHER_JUDGE), _completed(BASELINE, {OTHER_JUDGE: 1})],
        items,
        judge="strictness",
    )

    assert no_judging.available is False
    assert wrong_name.available is False
    assert no_judging.reason != wrong_name.reason
    assert "migkit.judging_completed" in no_judging.reason, (
        "the empty-of-judging log fell through to the wrong guard: its reason "
        "must say that no judging pass closed a group, not that the judge is "
        "spelled wrong"
    )
    assert "migkit.judging_completed" not in wrong_name.reason
    assert "strictness" in no_judging.reason
    assert "strictness" in wrong_name.reason


def test_a_judging_completed_that_names_no_model_is_a_refusal():
    """There is no side to attribute the group to, and guessing invents one."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _record(EVENT_JUDGING_COMPLETED, {"graded": {JUDGE: 1}}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "migkit.judging_completed" in result.reason
    assert "names no model" in result.reason


def test_a_failed_completion_that_names_no_model_is_a_refusal():
    """Dropping it would take a failure out of a denominator it belongs in."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _record(EVENT_COMPLETION, {"item_id": "a", "ok": False}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "failed migkit.completion names no model" in result.reason


def test_a_completion_record_with_no_ok_key_is_not_read_as_a_failure():
    """A malformed record is missing data, and a non-pass is a measurement.

    ``ok`` is tested identically to ``False`` rather than for falsiness, so a
    record that never says whether it succeeded contributes nothing instead of
    inventing a failure against the model.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _record(EVENT_COMPLETION, {"model_id": BASELINE, "item_id": "a"}),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1), (
        "a completion record with no ``ok`` key was counted as a non-pass"
    )


def test_imputed_and_parse_failures_are_read_for_this_judge_not_for_the_panel():
    """``graded``, ``imputed`` and ``parse_failures`` are all keyed by judge.

    The other judge on the panel here had one timeout and one unparseable reply
    and ours had neither. Summing any of the three across the panel moves the
    expected count and declines a run that is entirely healthy.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", False),
        _verdict("alpha", True, judge=OTHER_JUDGE),
        _parse_failure(judge=OTHER_JUDGE),
        _completed(
            BASELINE,
            {JUDGE: 2, OTHER_JUDGE: 3},
            imputed={OTHER_JUDGE: 1},
            parse_failures={OTHER_JUDGE: 1},
        ),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 2, 2)


def test_an_unjoinable_input_is_truncated_and_quoted_in_the_reason():
    """The reason is printed into a document, so it quotes an excerpt, not a prompt.

    An unjoinable input can be an entire golden-set question. Naming it whole
    would put arbitrary untrusted text into the sentence a reader is shown, and
    the sentence stops being a sentence.
    """
    long_input = "tell me at length about " + ("the sorrows of young werther " * 20)
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict(long_input, False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert long_input not in result.reason, "the whole input was pasted into the reason"
    assert repr(long_input[:80] + "...") in result.reason, (
        "the excerpt must be quoted and elided, so a reader can see where the "
        "quoted text starts and stops"
    )
    assert len(result.reason) < len(long_input)


# --------------------------------------------------------------------------- #
# The boundary the excerpt was deliberately built one character wider for
#
# ``_excerpt`` holds ``_INPUT_SHOWN + 1`` characters and says why: "so
# ``_shown_excerpt`` can still tell a text that was truncated from one that
# happened to end exactly on the limit". That extra character buys exactly one
# distinction, at exactly one length, and nothing tested it -- ``>`` mutated to
# ``>=`` survived, which spends the character and then lies with it, quoting an
# input that is complete and marking it elided.
#
# A trailing ``...`` is the reader's only signal that what they are looking at is
# not the whole value. Both directions are asserted, at the boundary and either
# side of it, because a bound that is only checked from one side is half a bound.
# --------------------------------------------------------------------------- #


def _quoted_input(text: str) -> str:
    """The refusal a completely unjoinable input of exactly this text produces."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict(text, True), _completed(BASELINE, {JUDGE: 1})]
    result = _counts(records, items)
    assert result.available is False, "the input was meant to join to nothing"
    return result.reason


@pytest.mark.parametrize("length", [78, 79, 80])
def test_an_input_that_fits_is_quoted_whole_and_not_marked_elided(length):
    """At eighty characters the input ends *on* the limit and nothing was cut."""
    text = "x" * length

    reason = _quoted_input(text)

    assert repr(text) in reason, "the whole input fits and must be quoted whole"
    assert repr(text + "...") not in reason, (
        f"an input of exactly {length} characters was quoted with a trailing "
        f"'...', which tells the reader something was cut when nothing was. The "
        f"excerpt is held one character past the limit for the sole purpose of "
        f"telling these two cases apart"
    )


@pytest.mark.parametrize("length", [81, 82, 200])
def test_an_input_one_character_past_the_limit_is_marked_elided(length):
    """The other side: eighty-one characters is the first that loses something."""
    text = "x" * length

    reason = _quoted_input(text)

    assert repr("x" * 80 + "...") in reason, (
        f"an input of {length} characters was cut without saying so"
    )
    assert repr(text) not in reason


def test_eighty_and_eighty_one_characters_do_not_quote_to_the_same_sentence():
    """The distinction the extra held character exists for, stated as itself.

    Both tests above could drift the same way and still agree with each other.
    This one says the thing that must never stop being true whatever the limit
    becomes: an input that ended on the limit and one that ran past it are not
    shown to the reader as the same input.
    """
    limit = dimensions._INPUT_SHOWN
    ends_on_the_limit = _quoted_input("y" * limit)
    runs_past_it = _quoted_input("y" * (limit + 1))

    assert ends_on_the_limit != runs_past_it, (
        "an input that was cut and one that was not produced the same sentence, "
        "so the '...' no longer means anything to whoever reads it"
    )


# --------------------------------------------------------------------------- #
# ... and so is every *other* value a refusal names
#
# The test above pins the one path that was always bounded: an input that is a
# string. Every other value a refusal quotes comes out of the same log and was
# not bounded at all. ``tests/test_report_untrusted_input.py`` opens with the
# premise -- an evidence log is designed to be shared, so a reviewer renders one
# they did not write -- and that premise is about a value's length as much as
# about its content.
#
# Measured on the build before the fix, through the deferred path:
#
#   a 2 MB JSON list as one verdict's `input`   ->  1,500,253-char reason
#   a 500,000-char `model_id` on a failure      ->    500,145-char reason
#   10,000 models known only from failures      ->    139,248-char reason
#   a 2,000,000-char *string* input             ->        335-char reason
#
# The last line is the control: the string path truncated correctly the whole
# time, which is what made the others easy to miss. A `reason` is carried on the
# run, handed out on `ReportModel.dimension_counts`, and printed by whatever
# renders it.
#
# The ceiling below is generous on purpose. It is not a formatting assertion --
# the sentences here run to a few hundred characters and are allowed to grow --
# it is the assertion that the length is a property of *this module* and not of
# the log.
# --------------------------------------------------------------------------- #

#: What no refusal sentence may exceed, however large the log's strings are.
#: Roughly four times the longest sentence this module writes.
_REASON_CEILING = 2000

#: Big enough that an untruncated value is unmistakable in the failure message.
_HUGE = "H" * 500_000


def _reason_for(records, items=None, *, judge=JUDGE) -> str:
    """Drive the deferred phase and hand back the refusal it declined with."""
    if items is None:
        items = _by_id(_item("a", "alpha", ("t",)))
    result = _tallied(records, items, judge=judge)
    assert result.available is False, "expected a refusal, got a matrix"
    return result.reason


def _bounded(reason: str, needle: str, what: str) -> None:
    assert needle not in reason, (
        f"the whole {what} was pasted into the reason ({len(reason):,} characters)"
    )
    assert len(reason) < _REASON_CEILING, (
        f"the reason naming a huge {what} is {len(reason):,} characters; its length "
        f"is a property of the log rather than of this module"
    )


def test_a_verdict_input_that_is_not_a_string_is_quoted_at_the_same_width():
    """The headline: `repr` of an arbitrary payload value, previously kept whole.

    A JSON log can put anything in the ``input`` slot. A list of 500,000 integers
    is a two-megabyte payload whose ``repr`` is longer still, and it reached the
    reason untouched because the truncation only ran on the ``str`` branch.
    """
    payload_list = list(range(500_000))
    records = [
        _record(_JUDGE_VERDICT, {"judge": JUDGE, "passed": True, "input": payload_list}),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    reason = _reason_for(records)

    _bounded(reason, repr(payload_list), "payload value")
    assert "in no golden-set item" in reason, "and it is still the right sentence"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"prompt": "H" * 500_000}, id="a dict"),
        pytest.param(["H" * 500_000], id="a list"),
        pytest.param(500_000 * 10**9, id="a very large int"),
        pytest.param(None, id="null"),
        pytest.param(True, id="a bool"),
    ],
)
def test_no_shape_of_non_string_input_escapes_the_bound(value):
    """One branch, so one bound -- but JSON has more than one non-string shape."""
    records = [
        _record(_JUDGE_VERDICT, {"judge": JUDGE, "passed": True, "input": value}),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    assert len(_reason_for(records)) < _REASON_CEILING


def test_a_huge_model_id_on_a_failed_completion_does_not_reach_the_reason_whole():
    """``_unknown_item`` interpolated both of its arguments with a bare ``!r``."""
    records = [_record(EVENT_COMPLETION, {"model_id": _HUGE, "item_id": 7, "ok": False})]

    _bounded(_reason_for(records), _HUGE, "model_id")


@pytest.mark.parametrize(
    ("what", "item_id"),
    [
        pytest.param("string item_id", _HUGE, id="a string"),
        pytest.param("non-string item_id", [_HUGE], id="wrapped in a list"),
        pytest.param("non-string item_id", {_HUGE: 1}, id="wrapped in a dict"),
    ],
)
def test_a_huge_item_id_on_a_failed_completion_is_bounded_too(what, item_id):
    """The other half of ``_unknown_item``, in both of the shapes that reach it.

    A string item id that is simply unknown takes the deferred branch and is
    refused at the join; a non-string one is refused where it is read. Both write
    the same sentence, so both have to bound the same value.
    """
    records = [
        _record(EVENT_COMPLETION, {"model_id": BASELINE, "item_id": item_id, "ok": False}),
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    _bounded(_reason_for(records), _HUGE, what)


def test_a_huge_judge_name_does_not_reach_the_reason_whole():
    """``judge`` comes off the log too, and four refusals name it."""
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    _bounded(_reason_for(records, judge=_HUGE), _HUGE, "judge name")


def test_a_huge_model_id_on_judging_completed_does_not_reach_the_reason_whole():
    """The group-shortfall refusal names the model the close was for."""
    records = [_verdict("alpha", True), _completed(_HUGE, {JUDGE: 99})]

    _bounded(_reason_for(records), _HUGE, "model_id")


def test_a_huge_model_id_known_only_from_failures_does_not_reach_the_reason_whole():
    records = [
        _completion(_HUGE, "a", ok=False),
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    _bounded(_reason_for(records), _HUGE, "model_id")


def test_the_refusal_that_lists_models_bounds_how_many_it_lists():
    """The one path whose length is unbounded in *count* rather than in width.

    Bounding each name leaves the sentence proportional to how many models the log
    names, and nothing bounds that: the models here reach the tally from failed
    ``migkit.completion`` records, which a log may hold any number of. Ten
    thousand of them measured 139,248 characters with every individual name short.
    """
    records = [_completion(f"model-{n}", "a", ok=False) for n in range(10_000)]
    records += [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    reason = _reason_for(records)

    assert len(reason) < _REASON_CEILING, (
        f"the reason is {len(reason):,} characters: each name is bounded but the "
        f"list is not, so its length is still a property of the log"
    )
    assert "more" in reason, "and it says how many it did not name"


def test_a_huge_golden_set_item_id_in_the_duplicate_input_refusal_is_bounded():
    """The golden set arrives by a path the log names, so it is bounded here too."""
    items = _by_id(_item(_HUGE, "alpha"), _item("b", "alpha"))

    _bounded(_reason_for([_verdict("alpha", True)], items), _HUGE, "item id")


def test_a_short_value_is_quoted_exactly_as_it_always_was():
    """The bound is a ceiling and not a reformatting: nothing short changes shape."""
    records = [_record(EVENT_COMPLETION, {"model_id": BASELINE, "item_id": 7, "ok": False})]

    reason = _reason_for(records)

    assert f"for {BASELINE!r} names item 7," in reason


def test_a_golden_set_item_with_duplicate_tags_trips_the_invariant():
    """``goldenset._parse_tags`` returns a duplicate-free tuple, so this is a bug.

    The contract says assert the invariant rather than defend against it: a
    mapping carrying ``("t", "t")`` did not come from a parsed golden set, and
    counting it would double one tag's denominator with no way to see it.
    """
    items = {"a": GoldenItem(id="a", input="alpha", tags=("t", "t"))}
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    with pytest.raises(AssertionError, match="duplicate tags"):
        _counts(records, items)


def test_the_open_group_holds_the_item_id_and_not_the_input_string():
    """The reason ``evidence.py`` exists: 5.0-5.8x amplification, measured.

    The weakref test above proves the *records* are not materialised. This one
    proves the group that outlives them holds ``(item_id, passed)`` pairs rather
    than the inputs -- the input is the largest string on a ``judge.verdict``
    payload, and keeping it for the length of a judging group would put the
    amplification straight back while every record still looked streamed.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    held: list[weakref.ref] = []

    def stream():
        tracked = _TrackedInput("alpha")
        held.append(weakref.ref(tracked))
        yield _verdict(tracked, True)
        del tracked
        # A second verdict, so the consumer's own view of the last input is
        # rebound; then two records it ignores, so the verdict record itself has
        # been dropped by the time the check runs. The group is still open.
        yield _verdict("bravo", True)
        yield _record("migkit.item_completed", {"item_id": "a"})
        yield _record("migkit.item_completed", {"item_id": "b"})
        gc.collect()
        assert held[0]() is None, (
            "the input text of a verdict is still alive while its group is open: "
            "the group is holding input strings rather than item ids"
        )
        yield _completed(BASELINE, {JUDGE: 2})

    result = dimensions.dimension_counts(stream(), items, judge=JUDGE)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (2, 2, 2)

# Renamed from ``_cell`` when C8 and C9 were merged into one file: C8's half
# defines a ``_cell(counts, model, tag)`` that reads a cell out of a result,
# and the later definition silently shadowed it. Two blind chunks appending to
# one new test file collide on helper names with no conflict marker to show it.
def _make_cell(**overrides: object) -> DimensionCell:
    """A cell that clears both floors, so a test can move one thing at a time.

    The defaults are the showcase: 16 items x 5 draws = 80 completions, which R9
    explicitly preserved when it added the item floor.
    """
    kwargs: dict[str, object] = {
        "tag": "refusal",
        "passes": 64,
        "n": 80,
        "items": 16,
        "confidence": 0.95,
        "floor": 0.60,
    }
    kwargs.update(overrides)
    tag = kwargs.pop("tag")
    passes = kwargs.pop("passes")
    n = kwargs.pop("n")
    items = kwargs.pop("items")
    return dimension_cell(tag, passes, n, items, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. The two floors are two numbers, and R9 fixed both of them.
# --------------------------------------------------------------------------- #


def test_the_completions_floor_is_still_twenty_because_r9_amended_r3_not_reversed_it() -> None:
    assert MIN_N_FOR_A_VERDICT == 20


def test_the_item_floor_is_ten_so_no_golden_item_is_worth_more_than_a_tenth() -> None:
    """R9's stated reason for ten, pinned as a number so a drift is visible.

    Below ten items one badly written golden-set item moves a published claim by
    more than a tenth. Ten is the smallest count where it cannot.
    """
    assert MIN_ITEMS_FOR_A_VERDICT == 10


def test_the_two_floors_are_two_distinct_constants_and_not_an_alias_for_one_number() -> None:
    """"Collapse the two floors into one number" is on the must-not list."""
    assert MIN_N_FOR_A_VERDICT != MIN_ITEMS_FOR_A_VERDICT


# --------------------------------------------------------------------------- #
# 2. R9's whole case. This is the test the contract names as failing first.
# --------------------------------------------------------------------------- #


def test_a_tag_with_four_items_and_twenty_completions_declines_the_verdict() -> None:
    """The spec's showpiece refusal, which the pre-R9 rule rendered a verdict on.

    Four items at n_per_item=5 is exactly 20 completions, and ``20 < 20`` is
    False, so the completions floor alone lets this cell through. R9 exists to
    stop it. If this cell renders a verdict the chunk is wrong.
    """
    cell = _make_cell(passes=15, n=20, items=4)
    assert cell.verdict_refused is True
    assert cell.needed == 6
    assert cell.needed_unit == "items"


def test_the_four_item_refusal_names_items_in_the_sentence_the_spec_wrote() -> None:
    """The spec's sentence with the unit made honest, quoted verbatim from C9.

    Keeping "20 items needed" over a completions count of 20 would print a note
    that contradicts the number it is refusing over. This is the corrected form.
    """
    cell = _make_cell(passes=15, n=20, items=4)
    assert cell.note == "10 items needed for a verdict here; you have 4."


def test_raising_n_per_item_cannot_talk_a_four_item_tag_past_the_floor() -> None:
    """R9's substantive argument, as a test: more draws multiply the same four questions.

    At n_per_item=10 the same four items reach 40 completions. A bigger
    completions floor would not have caught this; the item floor does.
    """
    for n_per_item in (5, 10, 25):
        cell = _make_cell(passes=0, n=4 * n_per_item, items=4)
        assert cell.verdict_refused is True, f"cleared at n_per_item={n_per_item}"
        assert cell.needed_unit == "items"
        assert cell.needed == 6


# --------------------------------------------------------------------------- #
# 3. The completions floor still binds on its own, in its own unit.
# --------------------------------------------------------------------------- #


def test_twelve_items_drawn_once_each_is_refused_on_completions_not_on_items() -> None:
    """Twelve items clears the item floor; twelve completions does not clear 20.

    The unit named here must be completions, because that is the one the reader
    can act on: these twelve questions only need asking more times.
    """
    cell = _make_cell(passes=9, n=12, items=12)
    assert cell.verdict_refused is True
    assert cell.needed == 8
    assert cell.needed_unit == "completions"


def test_the_completions_refusal_sentence_is_the_items_one_with_the_unit_swapped() -> None:
    """Verbatim per R10.3. One sentence: the item floor is not also unmet here."""
    cell = _make_cell(passes=9, n=12, items=12)
    assert cell.note == "20 completions needed for a verdict here; you have 12."


# --------------------------------------------------------------------------- #
# 4. The floors are floors: at them a cell clears, one below it does not.
#    These three are what makes the two floors independently mutation-visible.
# --------------------------------------------------------------------------- #


def test_exactly_twenty_completions_from_exactly_ten_items_clears_both_floors() -> None:
    """``<`` and not ``<=``: the constants are the smallest acceptable counts."""
    cell = _make_cell(passes=15, n=20, items=10)
    assert cell.verdict_refused is False
    assert cell.needed is None
    assert cell.needed_unit == ""
    # Also pins that a cell was computed rather than waved through: an
    # implementation that refuses nothing satisfies the three lines above.
    assert cell.rate == pytest.approx(0.75)
    assert cell.interval == pytest.approx(wilson_interval(15, 20, 0.95))


def test_one_completion_short_of_the_completions_floor_is_refused_in_completions() -> None:
    cell = _make_cell(passes=15, n=19, items=10)
    assert cell.verdict_refused is True
    assert cell.needed == 1
    assert cell.needed_unit == "completions"


def test_one_item_short_of_the_item_floor_is_refused_with_completions_to_spare() -> None:
    """Nine items at 80 completions: the completions floor is cleared eightfold."""
    cell = _make_cell(passes=64, n=80, items=9)
    assert cell.verdict_refused is True
    assert cell.needed == 1
    assert cell.needed_unit == "items"


# --------------------------------------------------------------------------- #
# 5. When both floors bind the note names items, because it is the actionable one.
# --------------------------------------------------------------------------- #


def test_when_both_floors_bind_the_shortfall_is_reported_in_items() -> None:
    """Four items, four completions: short by 16 completions and by 6 items.

    Naming completions here is advice that does not work -- R9 is the proof that
    raising n_per_item cannot fix an item shortfall.
    """
    cell = _make_cell(passes=1, n=4, items=4, floor=0.60)
    assert cell.verdict_refused is True
    assert cell.needed_unit == "items"
    assert cell.needed == 6


def test_a_note_naming_only_the_item_floor_would_walk_the_reader_into_a_second_refusal() -> None:
    """R10.5. The bad second impression, written down as the thing that must not happen.

    Told only "10 items needed; you have 4", the honest reader adds six
    single-draw items. That lands them at 10 items and 10 completions -- refused
    again, on a floor nobody mentioned, having done exactly what they were asked.
    So the note names the completions floor as well, even though ``needed``
    reports the item shortfall because that is the one they must act on first.
    """
    cell = _make_cell(passes=1, n=4, items=4)
    assert cell.needed == 6
    assert cell.needed_unit == "items"
    assert cell.note == (
        "10 items needed for a verdict here; you have 4. "
        "The 20-completion floor is also unmet: you have 4."
    )

    followed_the_advice = _make_cell(passes=1, n=10, items=10)
    assert followed_the_advice.verdict_refused is True
    assert followed_the_advice.needed_unit == "completions"


def test_the_second_sentence_appears_only_when_the_second_floor_actually_binds() -> None:
    """Twenty completions from four items clears the completions floor exactly.

    There is no second floor to warn about, so warning about one would be noise
    -- and worse, would tell the reader to raise a number that is already fine.
    """
    cell = _make_cell(passes=15, n=20, items=4)
    assert cell.note == "10 items needed for a verdict here; you have 4."
    assert "completion" not in cell.note

    only_completions = _make_cell(passes=9, n=12, items=12)
    assert only_completions.note == "20 completions needed for a verdict here; you have 12."
    assert "item" not in only_completions.note


def test_a_refused_cell_still_computes_and_carries_its_interval() -> None:
    """Refusing a verdict is not refusing to measure. n=4, passes=1 from the table."""
    cell = _make_cell(passes=1, n=4, items=4)
    assert cell.verdict_refused is True
    assert cell.rate == pytest.approx(0.25)
    assert cell.interval == pytest.approx(wilson_interval(1, 4, 0.95))


# --------------------------------------------------------------------------- #
# 6. The reviewer's trap: refusal is about sample size, never about the interval.
# --------------------------------------------------------------------------- #


def test_four_out_of_four_passing_is_still_refused_not_answered() -> None:
    """The tempting rule -- "refuse when the interval is too wide to decide" --
    answers here, because a perfect 4/4 clears a low floor outright. The rule in
    the contract is about counts, so this cell refuses no matter how it looks.
    """
    cell = _make_cell(passes=4, n=4, items=4, floor=0.10)
    assert cell.verdict_refused is True
    assert cell.rate == pytest.approx(1.0)
    assert cell.interval == pytest.approx(wilson_interval(4, 4, 0.95))
    assert cell.interval[0] > 0.10


def test_a_cell_clearing_both_floors_is_not_refused_when_it_misses_its_floor_badly() -> None:
    """``verdict_refused`` is not "did it pass". It is "may we say".

    Zero of eighty against a floor of 0.90 is a resounding failure, and a
    resounding failure is a verdict. Refusing here would hide it.
    """
    cell = _make_cell(passes=0, n=80, items=16, floor=0.90)
    assert cell.verdict_refused is False
    assert cell.rate == pytest.approx(0.0)
    assert cell.interval == pytest.approx(wilson_interval(0, 80, 0.95))


def test_a_cell_clearing_both_floors_is_not_refused_when_it_clears_its_floor() -> None:
    cell = _make_cell(passes=80, n=80, items=16, floor=0.10)
    assert cell.verdict_refused is False


def test_the_floor_never_moves_the_refusal_in_either_direction() -> None:
    """Neither sample-size floor depends on the pass-rate floor. Sweep it and see."""
    floors = (None, 0.0, 0.5, 1.0)
    refused = {_make_cell(passes=15, n=20, items=4, floor=f).verdict_refused for f in floors}
    assert refused == {True}
    allowed = {_make_cell(passes=64, n=80, items=16, floor=f).verdict_refused for f in floors}
    assert allowed == {False}


def test_a_floor_of_none_still_renders_a_cell_with_its_rate_and_interval() -> None:
    cell = _make_cell(passes=64, n=80, items=16, floor=None)
    assert cell.floor is None
    assert cell.verdict_refused is False
    assert cell.rate == pytest.approx(0.80)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, 0.95))


# --------------------------------------------------------------------------- #
# 7. The showcase, unchanged by R9. C15's headline tests stand on this cell.
# --------------------------------------------------------------------------- #


def test_the_showcase_of_sixteen_items_and_eighty_completions_renders_a_verdict() -> None:
    cell = _make_cell(passes=64, n=80, items=16, floor=0.60)
    assert cell.verdict_refused is False
    assert cell.rate == pytest.approx(0.80)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, 0.95))
    assert cell.needed is None
    assert cell.needed_unit == ""


def test_an_unrefused_cell_carries_no_refusal_sentence() -> None:
    """``note`` is the refusal sentence; there is nothing to say when it renders.

    Confidence is passed explicitly here, so the fallback disclosure that the
    confidence-is-None case requires has no reason to appear either.
    """
    cell = _make_cell(passes=64, n=80, items=16, confidence=0.95)
    assert cell.verdict_refused is False
    assert cell.note == ""
    assert cell.rate == pytest.approx(0.80)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, 0.95))


def test_the_cell_echoes_the_inputs_it_was_given() -> None:
    cell = _make_cell(tag="tone", passes=64, n=80, items=16, floor=0.60)
    assert cell.tag == "tone"
    assert cell.passes == 64
    assert cell.n == 80
    assert cell.items == 16
    assert cell.floor == pytest.approx(0.60)


# --------------------------------------------------------------------------- #
# 8. n == 0 is a rendering state, not a computation.
# --------------------------------------------------------------------------- #


def test_a_tag_with_nothing_measured_does_not_raise() -> None:
    """The proof that nothing was called: ``wilson_interval(0, 0)`` raises.

    Pinned in this repo at ``tests/test_report.py`` -- ``ValueError("n must be
    >= 1, got 0; a rate over zero runs is not a rate")``. If the cell had passed
    the zero through, this test would fail with that error rather than pass.
    """
    cell = _make_cell(passes=0, n=0, items=0, floor=0.60)
    assert cell.rate is None
    assert cell.interval is None
    assert cell.verdict_refused is True
    assert cell.note != ""
    assert cell.floor == pytest.approx(0.60)


def test_a_tag_with_nothing_measured_says_so_rather_than_reporting_a_rate() -> None:
    cell = _make_cell(passes=0, n=0, items=0)
    assert cell.note != ""
    assert "0.0" not in cell.note
    assert "0%" not in cell.note


def test_nothing_measured_quotes_no_shortfall_because_a_shortfall_implies_a_start() -> None:
    """R10.2. "You need 6 more items" implies you have some; at zero you have none.

    Nothing was measured is different in kind from not enough was measured, and
    the two states are not told apart by a number that is merely larger.
    """
    cell = _make_cell(passes=0, n=0, items=0)
    assert cell.needed is None
    assert cell.needed_unit == ""

    measured_one_thing = _make_cell(passes=1, n=1, items=1)
    assert measured_one_thing.needed == 9
    assert measured_one_thing.needed_unit == "items"


def test_the_floor_is_echoed_even_where_there_is_no_interval_to_compare_it_to() -> None:
    """R10.4: ``floor`` is an input the cell carries, not a field it derives."""
    for f in (None, 0.0, 0.60, 1.0):
        assert _make_cell(passes=0, n=0, items=0, floor=f).floor == f


# --------------------------------------------------------------------------- #
# 9. Confidence: used when given, disclosed when defaulted.
# --------------------------------------------------------------------------- #


def test_an_explicit_confidence_is_the_one_the_interval_is_built_from() -> None:
    """A different confidence must produce a different interval, not a relabelled one."""
    narrow = _make_cell(passes=64, n=80, items=16, confidence=0.80)
    wide = _make_cell(passes=64, n=80, items=16, confidence=0.99)
    assert narrow.interval == pytest.approx(wilson_interval(64, 80, 0.80))
    assert wide.interval == pytest.approx(wilson_interval(64, 80, 0.99))
    assert narrow.interval != wide.interval


def test_a_missing_confidence_falls_back_to_rigors_default_of_ninety_five() -> None:
    assert DEFAULT_CONFIDENCE == 0.95
    cell = _make_cell(passes=64, n=80, items=16, confidence=None)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, DEFAULT_CONFIDENCE))


def test_the_fallback_confidence_is_disclosed_and_never_taken_silently() -> None:
    """"never silently" is the words the contract uses. The reader must be told.

    Asserted on a cell that renders, so the disclosure cannot be smuggled in on
    the back of a refusal sentence that would have been printed anyway.
    """
    cell = _make_cell(passes=64, n=80, items=16, confidence=None)
    assert cell.verdict_refused is False
    assert cell.note != "", "a cell that renders still owes the reader this"
    assert re.search(r"0\.95|95\s*%|\b95\b", cell.note), cell.note


def test_the_disclosure_is_the_only_thing_that_puts_a_note_on_a_rendering_cell() -> None:
    """R10.1 resolved the contract against itself: "never silently" beats the
    field comment that called ``note`` the refusal sentence and nothing else.

    Both cells here render. The only difference is who chose the confidence.
    """
    told = _make_cell(passes=64, n=80, items=16, confidence=None)
    asked = _make_cell(passes=64, n=80, items=16, confidence=0.95)
    assert told.interval == pytest.approx(asked.interval)
    assert asked.note == ""
    assert told.note != ""


def test_a_refused_cell_that_defaults_its_confidence_still_says_why_it_refused() -> None:
    """The disclosure is additional to the refusal sentence, not instead of it."""
    cell = _make_cell(passes=15, n=20, items=4, confidence=None)
    assert "10 items needed for a verdict here; you have 4." in cell.note
    assert re.search(r"0\.95|95\s*%|\b95\b", cell.note), cell.note


# --------------------------------------------------------------------------- #
# 10. Corrupt counts must not render.
# --------------------------------------------------------------------------- #


def test_more_passes_than_completions_raises_rather_than_rendering() -> None:
    with pytest.raises(ValueError, match="more passes than completions"):
        _make_cell(passes=21, n=20, items=10)


def test_more_distinct_items_than_completions_raises_a_mispaired_two_numbers() -> None:
    """items > n is not a small sample, it is an impossible one."""
    with pytest.raises(ValueError, match="more items than completions"):
        _make_cell(passes=0, n=5, items=6)


def test_the_impossible_pairing_is_caught_before_the_floors_get_a_chance_to_refuse() -> None:
    """n=5, items=6 would refuse on both floors. A refusal would hide the bug."""
    with pytest.raises(ValueError, match="more items than completions"):
        _make_cell(passes=5, n=5, items=6)


# --------------------------------------------------------------------------- #
# 11. The cell is a frozen record with the fields the contract names.
# --------------------------------------------------------------------------- #


def test_the_cell_is_frozen_so_a_renderer_cannot_talk_it_out_of_its_refusal() -> None:
    cell = _make_cell(passes=15, n=20, items=4)
    assert dataclasses.is_dataclass(DimensionCell)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cell.verdict_refused = False  # type: ignore[misc]


def test_the_cell_carries_every_field_the_contract_names() -> None:
    names = [f.name for f in dataclasses.fields(DimensionCell)]
    assert names == [
        "tag",
        "passes",
        "n",
        "items",
        "rate",
        "interval",
        "floor",
        "verdict_refused",
        "needed",
        "needed_unit",
        "note",
    ]


# --------------------------------------------------------------------------- #
# 12. The floors are parameters, so a caller can move one without the other.
#     This is the reviewer's mutation check, executable.
# --------------------------------------------------------------------------- #


def test_lowering_the_item_floor_alone_lets_the_four_item_tag_through() -> None:
    cell = _make_cell(passes=15, n=20, items=4, min_items=4)
    assert cell.verdict_refused is False


def test_lowering_the_completions_floor_alone_lets_the_twelve_completion_tag_through() -> None:
    cell = _make_cell(passes=9, n=12, items=12, min_n=12)
    assert cell.verdict_refused is False


def test_lowering_one_floor_does_not_lower_the_other() -> None:
    """min_items=0 must not excuse a completions shortfall, and vice versa."""
    only_completions_left = _make_cell(passes=9, n=12, items=12, min_items=0)
    assert only_completions_left.verdict_refused is True
    assert only_completions_left.needed_unit == "completions"
    assert only_completions_left.needed == 8

    only_items_left = _make_cell(passes=15, n=20, items=4, min_n=0)
    assert only_items_left.verdict_refused is True
    assert only_items_left.needed_unit == "items"
    assert only_items_left.needed == 6


# --------------------------------------------------------------------------- #
# 13. Added in review. Each of these was written against a mutant that survived
#     the 40 tests above -- the implementation was already right in every case
#     but one, and the suite was permitting the wrong one.
# --------------------------------------------------------------------------- #


def test_a_corrupt_pass_count_is_refused_here_and_not_by_rigor_two_calls_later() -> None:
    """The ``passes > n`` guard, pinned by *this* module's message.

    The original test asserted a bare ``ValueError`` on ``passes=21, n=20``, and
    that ValueError arrives whether or not this module checks anything: rigor's
    ``_validate_counts`` rejects 21 successes in 20 runs on its own. So deleting
    the guard entirely left all forty tests green -- the test passed for a reason
    that had nothing to do with the code it was aiming at.

    It matters, because the one input where rigor never gets a look is exactly
    the one that renders: ``passes=5, n=0`` skips the interval branch, and an
    unguarded cell comes back saying "Nothing was measured" while carrying five
    passes. A corrupt count must not render, and at n == 0 nothing downstream
    will stop it.
    """
    with pytest.raises(ValueError, match="more passes than completions"):
        _make_cell(passes=21, n=20, items=10)

    with pytest.raises(ValueError, match="more passes than completions"):
        _make_cell(passes=5, n=0, items=0)


def test_a_negative_count_raises_rather_than_quietly_inflating_a_shortfall() -> None:
    """``items`` is validated nowhere else; rigor never sees it.

    A negative item count would sail past both other guards -- it is not greater
    than ``n`` and it is not a pass count -- and would come out the far side as a
    shortfall of eleven items, which is a number, and wrong.
    """
    with pytest.raises(ValueError, match="cannot be negative"):
        _make_cell(passes=0, n=20, items=-1)
    with pytest.raises(ValueError, match="cannot be negative"):
        _make_cell(passes=0, n=-1, items=0)


def test_the_refusal_sentences_quote_the_floors_given_not_the_ones_defaulted_to() -> None:
    """Every number in both sentences is interpolated, and this is what proves it.

    The suite above pins all three sentences only at ``min_items=10`` and
    ``min_n=20``, where an implementation that typed "10" and "20" as literals is
    indistinguishable from one that reads its arguments. A caller passing
    ``min_n=30`` would then be told about a 20-completion floor that does not
    exist -- a sentence that is not merely unhelpful but false, and false about
    the one number the reader is being asked to act on.

    R10.5's second sentence is the worst of the three to get wrong, because it is
    the one that exists specifically to stop a reader being refused twice.
    """
    both = _make_cell(passes=1, n=5, items=5, min_items=12, min_n=30)
    assert both.needed == 7
    assert both.needed_unit == "items"
    assert both.note == (
        "12 items needed for a verdict here; you have 5. "
        "The 30-completion floor is also unmet: you have 5."
    )

    completions_only = _make_cell(passes=20, n=25, items=15, min_items=12, min_n=30)
    assert completions_only.needed == 5
    assert completions_only.needed_unit == "completions"
    assert completions_only.note == "30 completions needed for a verdict here; you have 25."

    items_only = _make_cell(passes=20, n=40, items=5, min_items=12, min_n=30)
    assert items_only.needed == 7
    assert items_only.needed_unit == "items"
    assert items_only.note == "12 items needed for a verdict here; you have 5."


def test_the_confidence_disclosure_trails_the_refusal_rather_than_leading_it() -> None:
    """Ruled in review: the disclosure is always the last sentence of ``note``.

    Nothing in C9 or R10 said where it sits, and the tests above used ``in``, so
    a note that opened with the disclosure passed. It should not: ``note`` is
    read top-down by someone who has just been declined, the refusal is what they
    are owed first, and R10.5's two sentences are an ordered pair -- what to do,
    then what will still be wrong when they have done it. A footnote about which
    confidence level was assumed belongs after both, not wedged between them and
    not ahead of them.

    Pinned as an exact string rather than a prefix check, because "it starts with
    the refusal" is also true of an order that puts the disclosure in the middle.
    """
    cell = _make_cell(passes=1, n=4, items=4, confidence=None)
    assert cell.note == (
        "10 items needed for a verdict here; you have 4. "
        "The 20-completion floor is also unmet: you have 4. "
        "No confidence level was given, so rigor's default of 95% was used."
    )


def test_nothing_measured_discloses_no_confidence_it_never_consumed() -> None:
    """The one place "never silently" does not reach, ruled in review as correct.

    R10.1 says a defaulted confidence is disclosed whether or not the cell is
    refused. At ``n == 0`` there is no interval, nothing consumed the default,
    and disclosing it would describe a computation that did not happen. R10.2
    already establishes that nothing-measured is different in kind from
    not-enough-measured; this is the same seam. Pinned so the behaviour is a
    decision rather than a side effect of the branch it happens to sit in.
    """
    cell = _make_cell(passes=0, n=0, items=0, confidence=None)
    assert cell.note == "Nothing was measured for refusal."


# =========================================================================== #
# C21 -- the two-phase form. ``dimension_counts`` needs the golden set to join a
# verdict to an item by input text, and ``report.from_evidence`` does not have
# the golden set until the ``migkit.comparison`` record at the *end* of its one
# streaming pass. ``DimensionTally`` splits the work: read the log, then join.
#
# The two roads that were closed before it are both guarded by merged tests and
# neither is weakened here: ``test_report.py`` still counts exactly one text-mode
# open of the log, and ``test_evidence_scale.py`` still asserts peak allocation
# is flat in the log's size. What follows pins the third road.
# =========================================================================== #


def _tallied(records, items, *, judge: str = JUDGE):
    """The same counting, driven as two phases instead of one.

    The tally is built with no golden set at all, so every verdict goes past it
    while the join is still impossible -- which is exactly the position
    ``report.from_evidence`` is in.
    """
    tally = dimensions.DimensionTally()
    for record in records:
        tally.add(record)
    return tally.counts(items, judge=judge)


def _two_run_log():
    """Two nights in one log: an earlier run, its comparison, then a later run."""
    return [
        _verdict("alpha", True),
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 2}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
        _verdict("alpha", False),
        _completed(BASELINE, {JUDGE: 1}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
    ]


def test_the_deferred_join_agrees_with_the_join_that_had_the_golden_set_all_along():
    """The two phases are one function split, not a second implementation.

    Everything else here pins a property of the deferred phase. This pins the
    only thing that makes those properties worth having: that a caller who could
    not have the golden set in time gets the same matrix as one who could.
    """
    items = _by_id(
        _item("a", "alpha", ("t",)),
        _item("b", "bravo", ("t", "u")),
        _item("c", "charlie", ()),
    )
    records = [
        _verdict("alpha", True),
        _verdict("alpha", False),
        _verdict("bravo", True),
        _completion(BASELINE, "c", ok=False),
        _completed(BASELINE, {JUDGE: 3}),
        _verdict("bravo", False),
        _verdict("charlie", True),
        _completed(CANDIDATE, {JUDGE: 2}),
    ]

    one_phase = _counts(records, items)
    two_phase = _tallied(records, items)

    assert one_phase.available is True, one_phase.reason
    assert two_phase.available is True, two_phase.reason
    assert two_phase.by_model == one_phase.by_model
    assert _cell(two_phase, BASELINE, "t") == (2, 3, 2)
    assert _cell(two_phase, BASELINE, dimensions.UNTAGGED) == (0, 1, 1)
    assert _cell(two_phase, CANDIDATE, "u") == (0, 1, 1)


def test_the_deferred_join_refuses_an_unjoinable_input_in_the_same_words():
    """The excerpt survives the deferral, so the disclosure was not traded away.

    A digest cannot be turned back into the text a reader has to recognise, so
    the obvious deferred implementation refuses with something weaker -- an
    ordinal, or nothing. Eighty characters of the input is bounded for exactly
    the reason the digest is, so it is kept and the sentence does not change.
    """
    long_input = "tell me at length about " + ("the sorrows of young werther " * 20)
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict(long_input, False),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    two_phase = _tallied(records, items)

    assert two_phase.available is False
    assert two_phase.reason == _counts(records, items).reason, (
        "the deferred join refused in different words than the immediate one"
    )
    assert long_input not in two_phase.reason
    assert repr(long_input[:80] + "...") in two_phase.reason


def test_the_deferred_phase_holds_a_digest_of_the_input_and_never_the_input():
    """The amplification ``evidence.py`` measured, from the one angle left open.

    The immediate join files a verdict under its item id and is already pinned
    not to hold the input. The deferred join has no item id to file under, so
    this is the test that says what it files under instead: not the input, and
    not a projection of the record that quietly contains it.
    """
    held: list[weakref.ref] = []
    tally = dimensions.DimensionTally()

    tracked = _TrackedInput("alpha" + "x" * 4000)
    held.append(weakref.ref(tracked))
    tally.add(_verdict(tracked, True))
    del tracked
    gc.collect()

    assert held[0]() is None, (
        "the input text of a verdict is still alive after the deferred tally read "
        "it: the tally is holding inputs rather than digests of them"
    )


def test_a_second_run_in_the_log_replaces_the_first_rather_than_adding_to_it():
    """The multi-run ruling: the matrix is one run's, and it is the last one's.

    A log of fourteen nightly runs holds fourteen judging passes. Summing them
    would print a matrix of fourteen nights under a banner reporting the last,
    with nothing on the page able to reconcile the two numbers -- and it would
    add verdicts taken against golden sets nobody checked against each other,
    since the hash gate above this only ever checks the last comparison's.
    """
    items = _by_id(_item("a", "alpha", ("t",)))

    result = _tallied(_two_run_log(), items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (0, 1, 1), (
        "the matrix summed both nights; it is the last run's alone"
    )


def test_the_per_run_ruling_holds_for_the_one_phase_form_too():
    """One implementation, so one ruling. ``dimension_counts`` resets the same way."""
    items = _by_id(_item("a", "alpha", ("t",)))

    assert _cell(_counts(_two_run_log(), items), BASELINE, "t") == (0, 1, 1)


def test_a_log_with_no_comparison_in_it_is_one_run_and_is_counted_whole():
    """Which is what a hand-built stream of judging records is, and always was."""
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _verdict("alpha", True),
        _completed(CANDIDATE, {JUDGE: 1}),
    ]

    result = _tallied(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)
    assert _cell(result, CANDIDATE, "t") == (1, 1, 1)


def test_a_comparison_with_no_run_under_it_does_not_erase_the_run_before_it():
    """An empty stretch between two comparisons is the tail of the night before.

    Read the other way, a ``migkit.comparison`` appended to a log would erase a
    matrix that is still the right one -- and ``test_report.py`` asserts, for
    every other field on the model, that history in front of a run changes
    nothing but the series. The matrix is the last run that actually judged.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
        _record("migkit.verdict", {"verdict": "GO"}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
    ]

    result = _tallied(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1)


# --------------------------------------------------------------------------- #
# A run that produced nothing *but* a refusal is still a run
#
# The four payloads below all make ``_failure`` refuse globally, which latches
# ``refused_all`` and returns at the top of ``add`` for every later record. A real
# log writes completions before judging, so such a run reaches its
# ``migkit.comparison`` with no close, no verdict and no failure filed -- and the
# "was there a run under this comparison" guard above used to read that as an
# empty stretch and discard the run *with its refusal inside it*.
#
# Measured before the fix, through the deferred path ``report.from_evidence``
# uses: with an earlier night in the log the result was ``available=True``
# carrying the *previous* night's cells under tonight's banner, with no refusal
# and no note; with no earlier night it was the "the log holds no
# migkit.judging_completed record ... A judging pass either did not run or did not
# finish" refusal, which is false -- judging ran, finished, and closed a group --
# and which sends the reader after a fix for a problem they do not have.
#
# That is the "missing data presented as data" failure this codebase has shipped
# once. One test per trigger, because each reaches ``refuse(None, ...)`` down its
# own branch and a fix that only covers one of them would leave three live.
# --------------------------------------------------------------------------- #


def _refusing_night(bad_completion: EvidenceRecord) -> list[EvidenceRecord]:
    """One night in the order a real run writes it: completions, then judging.

    The bad completion goes first because that is where a runner puts it, and it
    is the ordering that makes the defect reachable: nothing has been filed on the
    run yet when the global refusal latches.
    """
    return [
        bad_completion,
        _completion(BASELINE, "b", ok=False),
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
    ]


def _earlier_night() -> list[EvidenceRecord]:
    """A night that judged cleanly, and its comparison. Whatever follows is tonight."""
    return [
        _verdict("alpha", False),
        _verdict("bravo", False),
        _completed(BASELINE, {JUDGE: 2}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
    ]


def _two_items() -> dict[str, GoldenItem]:
    return _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))


#: The four failed-``migkit.completion`` payloads that refuse for *every* judge.
#: Named by what is wrong with them, so a failure here reads as a sentence.
_GLOBAL_REFUSERS = {
    "no model_id at all": ({"item_id": "a", "ok": False}, "names no model"),
    "an empty model_id": (
        {"model_id": "", "item_id": "a", "ok": False},
        "names no model",
    ),
    "no item_id at all": ({"model_id": BASELINE, "ok": False}, "names item None"),
    "a non-string item_id": (
        {"model_id": BASELINE, "item_id": 7, "ok": False},
        "names item 7",
    ),
}


@pytest.mark.parametrize(
    ("what", "payload", "expected"),
    [(what, payload, expected) for what, (payload, expected) in _GLOBAL_REFUSERS.items()],
)
def test_a_comparison_does_not_discard_a_run_whose_only_record_was_a_refusal(
    what, payload, expected
):
    """Tonight refused. The comparison must not hand back last night instead."""
    records = _earlier_night() + _refusing_night(_record(EVENT_COMPLETION, payload))

    result = _tallied(records, _two_items())

    assert result.available is False, (
        f"a failed completion with {what} refused for every judge, and the "
        f"migkit.comparison then discarded the run that refusal was on -- so the "
        f"matrix rendered is the *previous* night's: {dict(result.by_model)}. "
        f"Missing data presented as data, under a banner reporting tonight"
    )
    assert expected in result.reason
    assert result.by_model == {}


@pytest.mark.parametrize(
    ("what", "payload", "expected"),
    [(what, payload, expected) for what, (payload, expected) in _GLOBAL_REFUSERS.items()],
)
def test_the_refusal_survives_when_there_is_no_earlier_night_to_fall_back_to(
    what, payload, expected
):
    """The other half: with nothing behind it the discarded run refused *falsely*.

    A fresh ``_Run`` has ``closes == 0``, so the reason reaching the reader was
    "the log holds no migkit.judging_completed record ... A judging pass either
    did not run or did not finish". Judging did run and did finish -- the log
    holds the record that says so -- and the sentence names a fix for a problem
    the reader does not have.
    """
    records = _refusing_night(_record(EVENT_COMPLETION, payload))

    result = _tallied(records, _two_items())

    assert result.available is False
    assert expected in result.reason, (
        f"a failed completion with {what} produced the wrong refusal: "
        f"{result.reason!r}"
    )
    assert "no migkit.judging_completed record" not in result.reason, (
        "the run carrying the refusal was discarded, so an empty run answered "
        "instead and reported that judging never ran -- which the "
        "migkit.judging_completed record in this very log contradicts"
    )


@pytest.mark.parametrize(
    ("what", "payload", "expected"),
    [(what, payload, expected) for what, (payload, expected) in _GLOBAL_REFUSERS.items()],
)
def test_the_one_phase_form_refuses_the_same_way(what, payload, expected):
    """One implementation, so one ruling -- the joined phase reaches it too."""
    records = _earlier_night() + _refusing_night(_record(EVENT_COMPLETION, payload))

    result = _counts(records, _two_items())

    assert result.available is False, dict(result.by_model)
    assert expected in result.reason


def test_a_comparison_with_a_refusal_under_it_beats_an_earlier_clean_run():
    """Stated as the property rather than as the four payloads, so it cannot rot.

    The control is the same night with the bad completion removed: it counts, and
    it counts *tonight's* numbers. That is what makes the assertion above a
    swapped matrix and not merely a stricter one.
    """
    items = _two_items()
    clean = _earlier_night() + [
        _completion(BASELINE, "b", ok=False),
        _verdict("alpha", True),
        _verdict("bravo", True),
        _completed(BASELINE, {JUDGE: 2}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
    ]

    control = _tallied(clean, items)
    last_night = _tallied(_earlier_night(), items)

    assert control.available is True, control.reason
    assert _cell(control, BASELINE, "t") == (2, 3, 2), "tonight's own numbers"
    assert _cell(last_night, BASELINE, "t") == (0, 2, 2), (
        "and they are not last night's, which is what a discarded run handed back"
    )


def test_verdicts_after_the_last_comparison_belong_to_a_run_nobody_compared():
    """A night still running, or one that died before deciding. Read and dropped.

    Dropped from the *result*. An earlier version of this docstring said the reset
    was "what keeps the deferred store bounded on a log whose tail is a judging
    pass with no close and no comparison behind it", and that was measured and is
    false: resetting at a comparison bounds nothing about what arrives after it.
    The fresh ``_Run`` accumulates one entry per distinct input exactly as the
    previous one did, at a measured 317 bytes each, and only stops when the stream
    does. See :class:`~model_migration_kit.dimensions.DimensionTally`, which now
    records where the golden set is the bound and where it is not.

    What the reset does buy is correctness, which is what this test asserts: the
    tail is not merged into the run the last comparison closed.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 1}),
        _record("migkit.comparison", {"goldenset_hash": "aaaa"}),
        _verdict("bravo", True),
        _verdict("bravo", True),
    ]

    result = _tallied(records, items)

    assert result.available is True, result.reason
    assert _cell(result, BASELINE, "t") == (1, 1, 1), (
        "verdicts written after the last comparison were counted into the run "
        "that comparison had already closed"
    )


def test_the_tally_counts_every_judge_and_is_told_which_one_at_the_end():
    """``report`` reads the judge's name off the same record that names the set.

    So the panel filter cannot be applied on the way past either, and one pass
    has to serve whichever judge the document turns out to want.
    """
    items = _by_id(_item("a", "alpha", ("t",)), _item("b", "bravo", ("t",)))
    records = [
        _verdict("alpha", True),
        _verdict("bravo", False, judge=OTHER_JUDGE),
        _completed(BASELINE, {JUDGE: 1, OTHER_JUDGE: 1}),
    ]
    tally = dimensions.DimensionTally()
    for record in records:
        tally.add(record)

    assert _cell(tally.counts(items, judge=JUDGE), BASELINE, "t") == (1, 1, 1)
    assert _cell(tally.counts(items, judge=OTHER_JUDGE), BASELINE, "t") == (0, 1, 1)


def test_asking_for_counts_with_no_golden_set_anywhere_raises():
    """The alternative is a matrix built against an empty set: a table of zeros.

    Which is the "missing data stated as zero" failure this codebase has shipped
    once, arrived at by a caller who simply forgot an argument.
    """
    tally = dimensions.DimensionTally()
    tally.add(_verdict("alpha", True))

    with pytest.raises(ValueError, match="needs the golden set"):
        tally.counts(judge=JUDGE)


def test_a_tally_built_with_the_golden_set_does_not_need_it_again():
    """Which is what ``dimension_counts`` does, and it re-indexes nothing."""
    items = _by_id(_item("a", "alpha", ("t",)))
    tally = dimensions.DimensionTally(items)
    for record in (_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})):
        tally.add(record)

    assert _cell(tally.counts(judge=JUDGE), BASELINE, "t") == (1, 1, 1)


def test_the_deferred_join_refuses_a_duplicated_input_as_a_precondition_too():
    """Ruling 2 survives the split. The guard is on the set, not on the sampling.

    It moves from "before a record is read" to "before a count is produced",
    which is the earliest the deferred phase can ask the question -- and the
    answer does not depend on which items were sampled either way.
    """
    items = _by_id(
        _item("dup-a", "the very same question", ("t",)),
        _item("dup-b", "the very same question", ("u",)),
        _item("real", "a question with one owner", ("t",)),
    )
    records = [
        _verdict("a question with one owner", True),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _tallied(records, items)

    assert result.available is False
    assert "dup-a" in result.reason
    assert "dup-b" in result.reason


def test_a_failed_completion_for_an_unknown_item_is_refused_by_the_deferred_join():
    """The membership check the immediate join makes on the way past.

    Deferred, the item id is all that was held -- which is small, so the sentence
    is the same one and names the same two things.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _verdict("alpha", True),
        _completion(BASELINE, "item-nobody-has", ok=False),
        _completed(BASELINE, {JUDGE: 1}),
    ]

    result = _tallied(records, items)

    assert result.available is False
    assert result.reason == _counts(records, items).reason
    assert "item-nobody-has" in result.reason


def _held_by_a_tally(records) -> int:
    """Bytes still reachable from a ``DimensionTally`` after it has read ``records``.

    ``tracemalloc`` current rather than peak: the question is what the tally is
    *holding*, not what building one record cost transiently. The baseline is taken
    after tracing starts so the records' own allocations are inside the window,
    which is what makes an implementation that keeps them show up here.
    """
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tally = dimensions.DimensionTally()
        for record in records:
            tally.add(record)
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    del tally
    return held


def test_repeated_draws_of_one_item_cost_the_deferred_store_nothing_extra():
    """The flatness claim, which is the one that makes a single pass affordable.

    The digest is not interesting because it is small; it is interesting because
    fifty draws of one item collapse onto one key. A store that grew per *verdict*
    would be the buffering this design exists to avoid, only with a smaller
    constant -- and a smaller constant on an 86 MB log is still a slope.

    So this varies the draws by fifty and holds the items fixed, and asserts the
    store does not notice. Asserted as a ratio and with a wide margin: the real
    figures are 311 KiB against 311 KiB, and anything under 1.5x is a store that
    is keyed by item rather than by record.
    """
    inputs = [f"input-{n}" for n in range(100)]

    def stream(draws: int):
        return [_verdict(text, True) for _ in range(draws) for text in inputs]

    once = _held_by_a_tally(stream(1))
    fifty = _held_by_a_tally(stream(50))

    assert fifty < once * 1.5, (
        f"the deferred store grew {once} -> {fifty} bytes when the draws per item "
        f"went 1 -> 50 and the item count did not move; it is keyed by record "
        f"rather than by distinct input, which is the amplification evidence.py "
        f"measured with a smaller constant"
    )


def test_the_deferred_store_is_keyed_by_distinct_input_and_so_grows_with_items():
    """The other side of the same property, stated so nobody reads flatness as free.

    Entries are bounded by *distinct inputs*, not by nothing. A real judging pass
    draws from the golden set, so that bound is the set's size -- but a log of
    inputs that join to no item has no such bound, and this is the test that says
    the growth is real and linear rather than absent. ``DimensionTally``'s
    docstring carries the measured constant and the ceiling it implies.
    """
    def stream(n_items: int):
        return [_verdict(f"input-{n}", True) for n in range(n_items)]

    small = _held_by_a_tally(stream(200))
    large = _held_by_a_tally(stream(2000))

    assert large > small * 5, (
        f"ten times the distinct inputs held {small} -> {large} bytes. If this ever "
        f"stops growing, the store has started dropping inputs it will need at the "
        f"join, and the refusal that should name one will name nothing"
    )


# --------------------------------------------------------------------------- #
# The joined phase latches, and that is the half of the docstring nothing pinned
#
# ``DimensionTally``'s docstring draws the asymmetry between the two phases: the
# deferred one cannot recognise an input that joins to nothing and files it like
# any other, "and a log of inputs that join to nothing grows the store linearly
# ... with no bound but the log", while the joined phase "recognises it
# immediately, latches the refusal, and every later verdict for that judge returns
# at the top of ``_verdict`` -- so the store stops growing at the first one".
#
# The second half had no test. Two mutations survived because of it: dropping
# ``(self._joined and not self._knows(item_id))`` from ``_failure``, and setting
# ``self._joined = False`` unconditionally in ``__init__``. Both send every caller
# down the deferred path -- the one that does *not* stop growing -- and both left
# the returned matrix identical, because the join at the end reaches the same
# refusal by a longer road.
# --------------------------------------------------------------------------- #


def _held_by_a_joined_tally(records, items) -> int:
    """``_held_by_a_tally``, but for a tally that had the golden set all along."""
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tally = dimensions.DimensionTally(items)
        for record in records:
            tally.add(record)
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - base
    finally:
        tracemalloc.stop()
    del tally
    return held


def test_the_joined_phase_stops_growing_at_the_first_unjoinable_verdict():
    """The claim in words, measured. The deferred phase's own test is above.

    A tally holding the golden set knows at once that an input joins to nothing.
    It refuses for that judge, and every later verdict under that judge returns at
    the top of ``_verdict`` without allocating -- so ten times the unjoinable
    records cost the same. A tally that had quietly stopped being joined would
    file all of them at the measured 317 bytes each.
    """
    items = _by_id(_item("a", "alpha", ("t",)))

    def stream(n_records: int):
        return [_verdict(f"joins-to-nothing-{n}", True) for n in range(n_records)]

    small = _held_by_a_joined_tally(stream(200), items)
    large = _held_by_a_joined_tally(stream(2000), items)

    assert large < small + 20_000, (
        f"ten times the unjoinable verdicts held {small} -> {large} bytes. The "
        f"joined phase is meant to latch on the first one and allocate nothing "
        f"after it; this is the deferred phase's unbounded growth, reached by a "
        f"tally that was handed the golden set and did not use it"
    )


def test_the_joined_phase_stops_growing_at_the_first_unknown_failed_item():
    """The same latching, down ``_failure``'s branch rather than ``_verdict``'s.

    A failed ``migkit.completion`` naming an item the set does not hold refuses
    for *every* judge, so nothing after it may be accumulated at all.
    """
    items = _by_id(_item("a", "alpha", ("t",)))

    def stream(n_records: int):
        # Distinct unknown ids on purpose. One id repeated would collapse onto one
        # entry even without the latch, and the growth this measures would vanish
        # into the store's own keying rather than into the refusal.
        return [
            _completion(BASELINE, f"item-nobody-has-{n}", ok=False)
            for n in range(n_records)
        ]

    small = _held_by_a_joined_tally(stream(200), items)
    large = _held_by_a_joined_tally(stream(2000), items)

    assert large < small + 20_000, (
        f"ten times the failed completions naming unknown items held {small} -> "
        f"{large} bytes. The first one refuses for every judge, `add` returns at "
        f"the top for everything after it, and the store must not have noticed "
        f"the difference -- this is a tally filing item ids the golden set in its "
        f"own hand says do not exist"
    )


def test_an_unknown_failed_item_latches_before_a_later_refusal_can_answer_for_it():
    """Latching is not only a memory property: it decides *which* sentence wins.

    ``reason_for`` hands back the first refusal the stream produced. When the
    unknown item id latches where it is read, that is the sentence. When it is
    filed instead -- which is what both surviving mutants do -- the run keeps
    accumulating, a later group-shortfall refusal lands in front of it, and the
    reader is told judging was resumed when the real defect is a log naming an
    item the golden set does not hold.
    """
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [
        _completion(BASELINE, "item-nobody-has", ok=False),
        _verdict("alpha", True),
        _completed(BASELINE, {JUDGE: 99}),
    ]

    result = _counts(records, items)

    assert result.available is False
    assert "item-nobody-has" in result.reason, (
        f"the unknown item id did not latch where it was read, so a refusal "
        f"raised three records later answered in its place: {result.reason!r}"
    )
    assert "closed a group of" not in result.reason


# --------------------------------------------------------------------------- #
# What the sixteen bytes buy
#
# ``_DIGEST_BYTES = 16`` -> ``15`` and ``-> 1`` both survived every correctness
# test; ``1`` died only on a memory test, which is the wrong reason. A narrow
# digest does not make the store bigger, it makes the *counts wrong*: the digest
# is the key of the tally store and of ``_Index.by_digest``, so two inputs that
# collide are one item as far as the join is concerned, and one of them silently
# takes the other's tags. No refusal is raised, because nothing has noticed.
# --------------------------------------------------------------------------- #

#: Two inputs whose blake2b digests are equal at one byte and differ at two. Found
#: by search, and pinned by the first assertion of the test below so that a change
#: of hash function fails loudly here rather than quietly weakening the
#: demonstration into a test of nothing.
_COLLIDES_AT_ONE_BYTE = ("question-20", "question-32")


def test_a_digest_collision_attributes_verdicts_to_the_wrong_item(monkeypatch):
    """The failure mode the width exists to prevent, shown by narrowing the width.

    Two colliding inputs are one key. ``_index`` builds ``by_digest`` by
    assignment, so the second item silently overwrites the first's entry rather
    than refusing -- and every verdict for *either* input then joins to whichever
    item was seen last. The matrix that comes back is available, complete and
    wrong, which is the one shape this module exists not to produce.
    """
    first, second = _COLLIDES_AT_ONE_BYTE
    items = _by_id(_item("a", first, ("alpha-tag",)), _item("b", second, ("bravo-tag",)))
    records = [
        _verdict(first, True),
        _verdict(first, True),
        _completed(BASELINE, {JUDGE: 2}),
    ]

    sound = _tallied(records, items)

    assert sound.available is True, sound.reason
    assert _cell(sound, BASELINE, "alpha-tag") == (2, 2, 1), "two draws of item 'a'"
    assert _cell(sound, BASELINE, "bravo-tag") == (0, 0, 0), "and none of item 'b'"

    monkeypatch.setattr(dimensions, "_DIGEST_BYTES", 1)
    assert dimensions._digest(first) == dimensions._digest(second), (
        f"{_COLLIDES_AT_ONE_BYTE} no longer collide at one byte, so this test is "
        f"demonstrating nothing. Find a new pair rather than deleting the test"
    )

    collided = _tallied(records, items)

    assert collided.available is True, (
        "a collision is not detected and cannot be: this is the point"
    )
    assert _cell(collided, BASELINE, "alpha-tag") == (0, 0, 0)
    assert _cell(collided, BASELINE, "bravo-tag") == (2, 2, 1), (
        "both draws of item 'a' were counted against item 'b', under a tag item "
        "'a' does not carry, and the matrix says nothing about it"
    )


def test_the_digest_is_sixteen_bytes_wide():
    """Not a restatement of the constant: the width is the only guard there is.

    A collision produces the wrong counts silently -- the test above is what that
    looks like -- so there is no runtime check to fall back on and no test at any
    realistic scale can tell fifteen bytes from sixteen. The width *is* the
    control, so it is asserted directly. See the constant's own comment for what
    the two populations are and why the margin holds by roughly thirty orders of
    magnitude at both of them.
    """
    assert dimensions._DIGEST_BYTES == 16
    assert len(dimensions._digest("alpha")) == 16
