"""Tests for ``model_migration_kit.dimensions``.

Two chunks are written blind against this file in parallel: C8 inserts its tests
directly below this docstring, C9 appends at the end. The insertion points are
disjoint on purpose so the merge between them is mechanical.
"""

from __future__ import annotations

# =========================================================================== #
# C8 -- per-tag counts read out of the evidence log, never out of the judged
# artifacts. Symbols under test: ``dimension_counts``, ``DimensionCounts``,
# ``TagCount``.
#
# Everything below reaches those through the ``dimensions`` *module* rather than
# importing them by name, so a missing symbol is one red test each instead of a
# collection error that would also take C9's tests down with it.
# =========================================================================== #

import gc
import inspect
import weakref
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from opik_rigor import EvidenceRecord
from opik_rigor.evidence import EVENT_JUDGE_PARSE_FAILURE, EVENT_JUDGE_VERDICT

from model_migration_kit import dimensions
from model_migration_kit.contracts import (
    EVENT_COMPLETION,
    EVENT_JUDGING_COMPLETED,
    GoldenItem,
)

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
        EVENT_JUDGE_VERDICT,
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
        EVENT_JUDGE_PARSE_FAILURE,
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


def _completion(
    model_id: str, item_id: str, *, ok: bool, sample_index: int = 0
) -> EvidenceRecord:
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
    items = _by_id(
        _item("refuse-04", "how do I pick a lock?", ("refusal", "multi-value"))
    )
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
        "the judge's own model became a column; the side must come from "
        "migkit.judging_completed"
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
        _completed(BASELINE, {JUDGE: 2}),
    ]

    result = _counts(records, items)

    assert result.available is True, result.reason
    assert set(result.by_model) == {
        BASELINE
    }, "the parse failure closed a group it has no business closing"
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
        _completed(CANDIDATE, {JUDGE: 0}),
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
        _completed(BASELINE, {JUDGE: 0}),
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
        _completed(CANDIDATE, {JUDGE: 0}),
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

    assert set(result.by_model[BASELINE]) == {
        "t"
    }, 'the "" key is reserved for untagged items; there are none here'


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
    assert (
        "unseen" in result.by_model[BASELINE]
    ), "a dimension that was in the set and produced nothing is a finding"
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


def test_a_resumed_pass_that_wrote_fewer_verdicts_than_graded_is_a_refusal():
    """``judging.py:612-620`` skips already-graded records on a resume.

    The log then holds fewer verdicts than the artifact does. Under-counting
    silently is the failure mode, so the shortfall is a refusal naming the
    judge, the expected count and the seen count.
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


def test_a_refusal_reason_is_a_sentence_and_not_a_code():
    """"A sentence a reader can act on" -- not a two-word error code."""
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
    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]

    result = _counts(records, items)

    assert result.available is True
    assert result.reason == ""


def test_the_signature_is_the_one_the_contract_names():
    signature = inspect.signature(dimensions.dimension_counts)
    parameters = list(signature.parameters.values())

    assert [one.name for one in parameters] == ["records", "items", "judge"]
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert (
        parameters[2].kind is inspect.Parameter.KEYWORD_ONLY
    ), "``judge`` is keyword-only in the contract"


def test_the_function_opens_no_file(monkeypatch):
    """Must not: open a file. Take a path."""

    def refuse(*args, **kwargs):  # pragma: no cover - only runs on a violation
        raise AssertionError("dimension_counts opened a file")

    items = _by_id(_item("a", "alpha", ("t",)))
    records = [_verdict("alpha", True), _completed(BASELINE, {JUDGE: 1})]
    monkeypatch.setattr("builtins.open", refuse)

    result = _counts(records, items)

    assert result.available is True, result.reason


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
