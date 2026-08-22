"""Tests for ``model_migration_kit.dimensions``.

Two chunks are written blind against this file in parallel: C8 inserts its tests
directly below this docstring, C9 appends at the end. The insertion points are
disjoint on purpose so the merge between them is mechanical.
"""

from __future__ import annotations


# =========================================================================== #
# C9 (amended by R9) -- the cell, the refusal, and the two floors.
#
# Appended at EOF per the C9 contract. C8 inserts above; nothing here reorders
# or re-headers the module.
#
# Every assertion below traces to the amended C9 section of the build plan, not
# to the superseded C9 at line 923. Where the two disagree, the amended one wins.
# =========================================================================== #

import dataclasses
import re

import pytest
from opik_rigor import wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

from model_migration_kit.dimensions import (
    MIN_ITEMS_FOR_A_VERDICT,
    MIN_N_FOR_A_VERDICT,
    DimensionCell,
    dimension_cell,
)


def _cell(**overrides: object) -> DimensionCell:
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


def test_the_completions_floor_is_still_twenty_because_r9_amended_r3_rather_than_reversing_it() -> None:
    assert MIN_N_FOR_A_VERDICT == 20


def test_the_item_floor_is_ten_so_no_single_golden_item_is_worth_more_than_a_tenth_of_a_verdict() -> None:
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
    cell = _cell(passes=15, n=20, items=4)
    assert cell.verdict_refused is True
    assert cell.needed == 6
    assert cell.needed_unit == "items"


def test_the_four_item_refusal_names_items_in_the_sentence_the_spec_wrote() -> None:
    """The spec's sentence with the unit made honest, quoted verbatim from C9.

    Keeping "20 items needed" over a completions count of 20 would print a note
    that contradicts the number it is refusing over. This is the corrected form.
    """
    cell = _cell(passes=15, n=20, items=4)
    assert cell.note == "10 items needed for a verdict here; you have 4."


def test_raising_n_per_item_cannot_talk_a_four_item_tag_past_the_floor() -> None:
    """R9's substantive argument, as a test: more draws multiply the same four questions.

    At n_per_item=10 the same four items reach 40 completions. A bigger
    completions floor would not have caught this; the item floor does.
    """
    for n_per_item in (5, 10, 25):
        cell = _cell(passes=0, n=4 * n_per_item, items=4)
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
    cell = _cell(passes=9, n=12, items=12)
    assert cell.verdict_refused is True
    assert cell.needed == 8
    assert cell.needed_unit == "completions"


def test_the_completions_refusal_sentence_names_completions_and_both_of_its_numbers() -> None:
    cell = _cell(passes=9, n=12, items=12)
    assert "completions" in cell.note
    assert "items" not in cell.note
    assert "20" in cell.note
    assert "12" in cell.note


# --------------------------------------------------------------------------- #
# 4. The floors are floors: at them a cell clears, one below it does not.
#    These three are what makes the two floors independently mutation-visible.
# --------------------------------------------------------------------------- #


def test_exactly_twenty_completions_from_exactly_ten_items_clears_both_floors() -> None:
    """``<`` and not ``<=``: the constants are the smallest acceptable counts."""
    cell = _cell(passes=15, n=20, items=10)
    assert cell.verdict_refused is False
    assert cell.needed is None
    assert cell.needed_unit == ""


def test_one_completion_short_of_the_completions_floor_is_refused_in_completions() -> None:
    cell = _cell(passes=15, n=19, items=10)
    assert cell.verdict_refused is True
    assert cell.needed == 1
    assert cell.needed_unit == "completions"


def test_one_item_short_of_the_item_floor_is_refused_in_items_even_with_completions_to_spare() -> None:
    """Nine items at 80 completions: the completions floor is cleared eightfold."""
    cell = _cell(passes=64, n=80, items=9)
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
    cell = _cell(passes=1, n=4, items=4, floor=0.60)
    assert cell.verdict_refused is True
    assert cell.needed_unit == "items"
    assert cell.needed == 6


def test_a_refused_cell_still_computes_and_carries_its_interval() -> None:
    """Refusing a verdict is not refusing to measure. n=4, passes=1 from the table."""
    cell = _cell(passes=1, n=4, items=4)
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
    cell = _cell(passes=4, n=4, items=4, floor=0.10)
    assert cell.verdict_refused is True


def test_a_cell_that_clears_both_floors_is_not_refused_even_when_it_misses_its_floor_badly() -> None:
    """``verdict_refused`` is not "did it pass". It is "may we say".

    Zero of eighty against a floor of 0.90 is a resounding failure, and a
    resounding failure is a verdict. Refusing here would hide it.
    """
    cell = _cell(passes=0, n=80, items=16, floor=0.90)
    assert cell.verdict_refused is False


def test_a_cell_that_clears_both_floors_is_not_refused_even_when_it_clears_its_floor_outright() -> None:
    cell = _cell(passes=80, n=80, items=16, floor=0.10)
    assert cell.verdict_refused is False


def test_the_floor_never_moves_the_refusal_in_either_direction() -> None:
    """Neither sample-size floor depends on the pass-rate floor. Sweep it and see."""
    refused = {_cell(passes=15, n=20, items=4, floor=f).verdict_refused for f in (None, 0.0, 0.5, 1.0)}
    assert refused == {True}
    allowed = {_cell(passes=64, n=80, items=16, floor=f).verdict_refused for f in (None, 0.0, 0.5, 1.0)}
    assert allowed == {False}


def test_a_floor_of_none_still_renders_a_cell_with_its_rate_and_interval() -> None:
    cell = _cell(passes=64, n=80, items=16, floor=None)
    assert cell.floor is None
    assert cell.verdict_refused is False
    assert cell.rate == pytest.approx(0.80)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, 0.95))


# --------------------------------------------------------------------------- #
# 7. The showcase, unchanged by R9. C15's headline tests stand on this cell.
# --------------------------------------------------------------------------- #


def test_the_showcase_of_sixteen_items_and_eighty_completions_renders_a_verdict() -> None:
    cell = _cell(passes=64, n=80, items=16, floor=0.60)
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
    cell = _cell(passes=64, n=80, items=16, confidence=0.95)
    assert cell.note == ""


def test_the_cell_echoes_the_inputs_it_was_given() -> None:
    cell = _cell(tag="tone", passes=64, n=80, items=16, floor=0.60)
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
    cell = _cell(passes=0, n=0, items=0)
    assert cell.rate is None
    assert cell.interval is None
    assert cell.verdict_refused is True


def test_a_tag_with_nothing_measured_says_so_rather_than_reporting_a_rate() -> None:
    cell = _cell(passes=0, n=0, items=0)
    assert cell.note != ""
    assert "0.0" not in cell.note
    assert "0%" not in cell.note


# --------------------------------------------------------------------------- #
# 9. Confidence: used when given, disclosed when defaulted.
# --------------------------------------------------------------------------- #


def test_an_explicit_confidence_is_the_one_the_interval_is_built_from() -> None:
    """A different confidence must produce a different interval, not a relabelled one."""
    narrow = _cell(passes=64, n=80, items=16, confidence=0.80)
    wide = _cell(passes=64, n=80, items=16, confidence=0.99)
    assert narrow.interval == pytest.approx(wilson_interval(64, 80, 0.80))
    assert wide.interval == pytest.approx(wilson_interval(64, 80, 0.99))
    assert narrow.interval != wide.interval


def test_a_missing_confidence_falls_back_to_rigors_default_of_ninety_five() -> None:
    assert DEFAULT_CONFIDENCE == 0.95
    cell = _cell(passes=64, n=80, items=16, confidence=None)
    assert cell.interval == pytest.approx(wilson_interval(64, 80, DEFAULT_CONFIDENCE))


def test_the_fallback_confidence_is_disclosed_and_never_taken_silently() -> None:
    """"never silently" is the words the contract uses. The reader must be told.

    Asserted on a cell that renders, so the disclosure cannot be smuggled in on
    the back of a refusal sentence that would have been printed anyway.
    """
    cell = _cell(passes=64, n=80, items=16, confidence=None)
    assert cell.note != ""
    assert re.search(r"0\.95|95\s*%|\b95\b", cell.note), cell.note


def test_a_refused_cell_that_defaults_its_confidence_still_says_why_it_refused() -> None:
    """The disclosure is additional to the refusal sentence, not instead of it."""
    cell = _cell(passes=15, n=20, items=4, confidence=None)
    assert "10 items needed for a verdict here; you have 4." in cell.note
    assert re.search(r"0\.95|95\s*%|\b95\b", cell.note), cell.note


# --------------------------------------------------------------------------- #
# 10. Corrupt counts must not render.
# --------------------------------------------------------------------------- #


def test_more_passes_than_completions_raises_rather_than_rendering() -> None:
    with pytest.raises(ValueError):
        _cell(passes=21, n=20, items=10)


def test_more_distinct_items_than_completions_raises_because_the_caller_mispaired_two_numbers() -> None:
    """items > n is not a small sample, it is an impossible one."""
    with pytest.raises(ValueError):
        _cell(passes=0, n=5, items=6)


def test_the_impossible_pairing_is_caught_before_the_floors_get_a_chance_to_refuse() -> None:
    """n=5, items=6 would refuse on both floors. A refusal would hide the bug."""
    with pytest.raises(ValueError):
        _cell(passes=5, n=5, items=6)


# --------------------------------------------------------------------------- #
# 11. The cell is a frozen record with the fields the contract names.
# --------------------------------------------------------------------------- #


def test_the_cell_is_frozen_so_a_renderer_cannot_talk_it_out_of_its_refusal() -> None:
    cell = _cell(passes=15, n=20, items=4)
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
    cell = _cell(passes=15, n=20, items=4, min_items=4)
    assert cell.verdict_refused is False


def test_lowering_the_completions_floor_alone_lets_the_twelve_completion_tag_through() -> None:
    cell = _cell(passes=9, n=12, items=12, min_n=12)
    assert cell.verdict_refused is False


def test_lowering_one_floor_does_not_lower_the_other() -> None:
    """min_items=0 must not excuse a completions shortfall, and vice versa."""
    assert _cell(passes=9, n=12, items=12, min_items=0).verdict_refused is True
    assert _cell(passes=15, n=20, items=4, min_n=0).verdict_refused is True
