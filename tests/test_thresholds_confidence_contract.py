"""``Thresholds.confidence`` against opik-rigor 0.2.0's one-sided floor.

Written from the contract, not from `judging.py`. Per HANDOFF.md's working method
the implementer of the narrowing and the author of these tests are different
agents, and **no expected value in this file was obtained by running
``model_migration_kit``**. Every expectation is one of:

* a literal from the contract (the six defaults; the interval each threshold is
  validated on; ``ConfigError`` as the type on this path);
* a hand derivation (0.5 is *at* the floor and the interval is open, so 0.5 is
  refused; 0.3 is below it, so it is refused for a second and stronger reason);
* read out of the *dependency* -- ``opik_rigor.assert_pass_rate`` and
  ``wilson_lower_bound`` are called directly here as the oracle for where the
  boundary belongs. That is not the code under test; it is the reason the code
  under test is changing.

**Why this file exists at all.** rigor 0.2.0's ``_validate_gating_confidence``
refuses a one-sided confidence at or below 0.5, because ``z = ppf(c)`` is
non-positive there: the "lower bound" comes out at or *above* the observed rate
and gets worse as evidence accumulates, so a gate set at ``confidence=0.3`` reads
in a config file like an act of caution and is in fact looser than comparing the
raw rate. migkit validated ``confidence`` on ``(0, 1)`` and therefore accepted
0.3 -- accepted it at config load, in front of the operator, and then handed it
to ``assert_pass_rate`` at verdict time, hours and a provider bill later, where
it became a ``ValueError`` out of a dependency. The narrowing to ``(0.5, 1)``
moves that refusal back to the moment the operator is still looking at the file
they just edited. It is the same argument ``JudgeConfig.parse`` already makes
about ``require_pinned``.

**The test that matters most is not the one that hardcodes 0.5.** A constant in
two repositories is two constants. :class:`TestTheBoundaryIsWhereRigorsIs`
therefore *discovers* each side's floor by bisection and asserts the two are the
same float, so the pair fails if either side moves independently -- including if
rigor loosens in 0.3.0 and migkit is left refusing values its dependency would
now take.

**One value is deliberately left out of the agreement sweep**: confidences within
about one ULP of 1.0. rigor's own validator accepts ``math.nextafter(1.0, 0.0)``
as in-range and then ``NormalDist().inv_cdf`` raises ``StatisticsError`` on it, so
that corner is a wart in the dependency rather than a boundary either side is
claiming. It is noted here rather than pinned, because pinning it would make this
file red the day rigor fixes it.

Everything here is offline, keyless and deterministic: the only rigor call made is
a gate over a literal ``(20, 20)`` count pair with a floor of 0.0, which passes
whenever the confidence is legal and raises ``ValueError`` when it is not.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from opik_rigor import assert_pass_rate, wilson_lower_bound

from model_migration_kit.errors import ConfigError, MigrationKitError
from model_migration_kit.judging import JudgeConfig, Thresholds

# --------------------------------------------------------------------------- #
# Constants from the contract, not from the implementation.
# --------------------------------------------------------------------------- #

#: The six defaults, unchanged by this work. Quoted from session-2-contract.md §1.
CONTRACT_DEFAULTS = {
    "pass_rate_floor": 0.90,
    "alpha": 0.05,
    "confidence": 0.95,
    "judge_failure_tolerance": 0.05,
    "min_detectable_effect": 0.10,
    "power_target": 0.80,
}

#: The stated new floor for ``confidence``, open: 0.5 itself is not a confidence.
CONTRACT_CONFIDENCE_FLOOR = 0.5

#: The other five, with the interval each is validated on and whether that
#: interval includes its endpoints. Nothing in this table changes; it is here so
#: that a narrowing applied to the wrong row -- or to every row -- is loud.
#: ``pass_rate_floor`` and ``judge_failure_tolerance`` are closed because 0.0 and
#: 1.0 are both gates somebody might mean ("accept anything", "demand
#: perfection", "tolerate no parse failure", "tolerate every one"). The other
#: three are open because their endpoints are not settings: alpha 0 can never
#: reject, alpha 1 always rejects, power 1 needs infinite n, and a minimum
#: detectable effect of 0 asks for infinite n as well.
UNCHANGED_RANGES = {
    "pass_rate_floor": (0.0, 1.0, True),
    "alpha": (0.0, 1.0, False),
    "judge_failure_tolerance": (0.0, 1.0, True),
    "min_detectable_effect": (0.0, 1.0, False),
    "power_target": (0.0, 1.0, False),
}

#: A judge model string ``opik_rigor.is_pinned`` accepts, so that the config-file
#: path can be exercised without a credential or a network.
PINNED_JUDGE_MODEL = "fake-judge-v1"

RUBRIC_TEXT = "Pass the response if it answers the question asked.\n"


# --------------------------------------------------------------------------- #
# Helpers. Two predicates and a bisection; nothing here knows either boundary.
# --------------------------------------------------------------------------- #


def _migkit_accepts(confidence: float) -> bool:
    """Does ``Thresholds`` take this confidence? Nothing else about it is asserted."""
    try:
        Thresholds(confidence=confidence)
    except ConfigError:
        return False
    return True


def _rigor_accepts(confidence: float) -> bool:
    """Does rigor's gate take this confidence?

    The counts and the floor are chosen so that the *only* thing that can make
    this raise is the confidence: 20 successes out of 20 against a floor of 0.0
    clears any legal bound, so ``PassRateError`` cannot fire and a ``ValueError``
    can only have come from validating the confidence itself.
    """
    try:
        assert_pass_rate((20, 20), 0.0, confidence=confidence)
    except ValueError:
        return False
    return True


def _smallest_accepted(accepts, *, known_bad: float = 0.0, known_good: float = 0.95) -> float:
    """Bisect ``accepts`` for the least confidence it takes, assuming monotonicity.

    Deliberately given no candidate boundary to check: it starts from one value
    each side knows it refuses and one each side knows it takes, and converges on
    adjacent floats. That is what lets the two boundaries be compared without this
    file naming either of them.
    """
    assert not accepts(known_bad), f"{known_bad!r} was supposed to be refused"
    assert accepts(known_good), f"{known_good!r} was supposed to be accepted"
    lo, hi = known_bad, known_good
    while math.nextafter(lo, hi) != hi:
        mid = (lo + hi) / 2.0
        if mid in (lo, hi):  # no float strictly between them; already adjacent
            break
        if accepts(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _config_with_confidence(tmp_path: Path, confidence: float) -> Path:
    """The contract's config shape with one judge, rendered by hand so it is visible."""
    root = Path(tmp_path) / "cfg"
    rubric = root / "rubrics" / "helpfulness.md"
    rubric.parent.mkdir(parents=True, exist_ok=True)
    rubric.write_text(RUBRIC_TEXT, encoding="utf-8")
    path = root / "judges.toml"
    path.write_text(
        "[[judge]]\n"
        'name = "helpfulness"\n'
        f"model = {json.dumps(PINNED_JUDGE_MODEL)}\n"
        'rubric = "rubrics/helpfulness.md"\n'
        "\n"
        "[thresholds]\n"
        f"confidence = {json.dumps(confidence)}\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# The floor itself.
# --------------------------------------------------------------------------- #


class TestConfidenceAtOrBelowTheFloorIsRefused:
    """Contract: ``confidence`` is validated on the open interval ``(0.5, 1)``."""

    def test_exactly_the_floor_is_refused_because_the_interval_is_open(self):
        # At 0.5 the z is 0 and the "lower bound" *is* successes/n, so the gate
        # stops being a bound at all and becomes a raw comparison wearing the word
        # confidence. Open at the bottom for the same reason it is open at the top.
        with pytest.raises(ConfigError):
            Thresholds(confidence=CONTRACT_CONFIDENCE_FLOOR)

    def test_a_value_clearly_below_the_floor_is_refused(self):
        # 0.3 is the value that motivated the change: previously accepted here,
        # then rejected by rigor at verdict time, after the sampling was paid for.
        with pytest.raises(ConfigError):
            Thresholds(confidence=0.3)

    @pytest.mark.parametrize("value", [0.5, 0.4999, 0.3, 0.05, 1e-9])
    def test_every_value_in_the_inverted_half_is_refused(self, value):
        # The whole half-range below the floor, not just its named examples: below
        # 0.5 the bound sits above the observed rate and *falls* as n grows, so
        # every value here buys a gate looser than no gate.
        with pytest.raises(ConfigError):
            Thresholds(confidence=value)

    def test_zero_is_refused(self):
        with pytest.raises(ConfigError):
            Thresholds(confidence=0.0)

    def test_a_negative_confidence_is_refused(self):
        with pytest.raises(ConfigError):
            Thresholds(confidence=-0.25)

    def test_not_a_number_is_refused(self):
        # NaN fails every comparison, so a range check written as a chained
        # inequality rejects it without a special case -- but only if the check is
        # still a chained inequality after the narrowing.
        with pytest.raises(ConfigError):
            Thresholds(confidence=float("nan"))


class TestConfidenceAboveTheFloorIsStillAccepted:
    """The narrowing must not have cost the range that was always legal."""

    def test_the_default_is_accepted_and_is_still_the_contract_default(self):
        # 0.95 sits above the new floor, so the narrowing is invisible to every
        # operator who never touched the setting. If this is red the change did not
        # narrow the range, it moved it.
        assert Thresholds().confidence == CONTRACT_DEFAULTS["confidence"]
        assert Thresholds(confidence=0.95).confidence == 0.95

    def test_the_first_float_above_the_floor_is_accepted(self):
        # The interval is open at 0.5, which means the very next float is in it.
        # A guard written as `< 0.5` rather than `<= 0.5`, or one comparing against
        # a rounded constant, is caught here and nowhere else in this class.
        just_above = math.nextafter(CONTRACT_CONFIDENCE_FLOOR, 1.0)
        assert Thresholds(confidence=just_above).confidence == just_above

    @pytest.mark.parametrize("value", [0.51, 0.6, 0.8, 0.9, 0.95, 0.99, 0.999])
    def test_the_ordinary_range_of_confidences_is_accepted(self, value):
        assert Thresholds(confidence=value).confidence == value

    def test_an_accepted_confidence_is_carried_through_to_dict(self):
        # Thresholds are echoed into the report beside the verdict they produced;
        # a value that validates but does not survive the echo is a gate nobody can
        # see. Cheap to check, and the narrowing touches this class.
        assert Thresholds(confidence=0.99).to_dict()["confidence"] == 0.99


class TestTheUpperEndIsUnchanged:
    """The interval was already open at the top and stays that way."""

    def test_one_is_still_refused(self):
        # confidence = 1 is an infinite interval: the normal quantile diverges.
        with pytest.raises(ConfigError):
            Thresholds(confidence=1.0)

    @pytest.mark.parametrize("value", [1.0001, 1.5, 2.0, 100.0])
    def test_above_one_is_still_refused(self, value):
        with pytest.raises(ConfigError):
            Thresholds(confidence=value)


class TestTheBoundaryIsWhereRigorsIs:
    """The point of the change: migkit's floor and rigor's floor are one number.

    Nothing in this class names 0.5. Each test either discovers the boundary from
    both sides and compares them, or takes a value migkit accepts and puts it
    through rigor's gate. Both fail if either side moves on its own.
    """

    def test_the_two_floors_are_the_same_float(self):
        # Bisection rather than a constant: a constant here would be a third copy
        # of the number and would go on passing after a change to either side.
        assert _smallest_accepted(_migkit_accepts) == _smallest_accepted(_rigor_accepts)

    @pytest.mark.parametrize(
        "value",
        [
            -1.0,
            -0.001,
            0.0,
            1e-9,
            0.1,
            0.3,
            0.49,
            0.5,
            0.5000001,
            0.6,
            0.8,
            0.95,
            0.99,
            0.999999,
            1.0,
            1.5,
        ],
    )
    def test_the_two_sides_agree_on_every_value_across_the_range(self, value):
        # Not just at the boundary: the two validators must classify the whole
        # range identically, so a narrowing that fixed the floor while breaking
        # something else -- the top end, the zero end, a type check -- shows up.
        assert _migkit_accepts(value) == _rigor_accepts(value), (
            f"migkit and rigor disagree about confidence={value!r}: "
            f"migkit accepts={_migkit_accepts(value)}, rigor accepts={_rigor_accepts(value)}"
        )

    def test_the_first_float_migkit_accepts_is_one_rigor_gates_on(self):
        # The tightest statement of the contract: whatever the least accepted value
        # turns out to be, a real gate must run at it and produce a real bound.
        floor = _smallest_accepted(_migkit_accepts)
        assert Thresholds(confidence=floor).confidence == floor
        report = assert_pass_rate((190, 200), 0.90, confidence=floor)
        assert report["confidence"] == floor

    @pytest.mark.parametrize("value", [0.51, 0.6, 0.95, 0.99])
    def test_no_value_migkit_accepts_makes_rigors_bound_raise(self, value):
        # The failure this change exists to prevent, stated directly: a config that
        # loads must not blow up in the dependency hours later at verdict time.
        confidence = Thresholds(confidence=value).confidence
        wilson_lower_bound(190, 200, confidence)
        assert_pass_rate((190, 200), 0.90, confidence=confidence)

    def test_a_bound_at_an_accepted_confidence_is_not_above_the_observed_rate(self):
        # Why the floor is at 0.5 and not somewhere else, checked rather than
        # quoted: a *lower* bound that exceeds the rate it bounds is the inversion
        # rigor refuses, and no confidence migkit accepts may produce one.
        for value in (math.nextafter(CONTRACT_CONFIDENCE_FLOOR, 1.0), 0.6, 0.95, 0.999):
            if not _migkit_accepts(value):
                continue
            assert wilson_lower_bound(190, 200, value) <= 190 / 200


class TestTheOtherFiveThresholdsAreUnchanged:
    """A narrowing applied to the wrong row, or to every row, is caught here.

    ``confidence`` is the only threshold rigor gates one-sidedly, so it is the only
    one whose range moves. The other five are read off the validation table in
    `judging.py` as it stood and pinned; if all six were narrowed together every
    test in the classes above would still pass.
    """

    @pytest.mark.parametrize("name", sorted(UNCHANGED_RANGES))
    @pytest.mark.parametrize("value", [0.3, 0.5])
    def test_the_values_confidence_now_refuses_are_still_fine_elsewhere(self, name, value):
        # This is the test that catches a change made to the shared loop instead of
        # to one row. A judge_failure_tolerance of 0.3 is a bad idea; it is not a
        # ConfigError, and half the five have 0.5 well inside their useful range.
        assert getattr(Thresholds(**{name: value}), name) == value

    @pytest.mark.parametrize("name", sorted(UNCHANGED_RANGES))
    @pytest.mark.parametrize("value", [0.01, 0.05, 0.1, 0.8, 0.9, 0.99])
    def test_each_keeps_its_ordinary_range(self, name, value):
        assert getattr(Thresholds(**{name: value}), name) == value

    @pytest.mark.parametrize(
        "name", sorted(n for n, (_, _, inclusive) in UNCHANGED_RANGES.items() if inclusive)
    )
    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_the_closed_ones_still_accept_both_endpoints(self, name, value):
        # pass_rate_floor and judge_failure_tolerance: 0.0 and 1.0 are meaningful
        # settings at both ends, which is why their interval was closed to begin
        # with and why nothing about confidence should have closed or opened it.
        assert getattr(Thresholds(**{name: value}), name) == value

    @pytest.mark.parametrize(
        "name", sorted(n for n, (_, _, inclusive) in UNCHANGED_RANGES.items() if not inclusive)
    )
    @pytest.mark.parametrize("value", [0.0, 1.0])
    def test_the_open_ones_still_refuse_both_endpoints(self, name, value):
        with pytest.raises(ConfigError):
            Thresholds(**{name: value})

    @pytest.mark.parametrize("name", sorted(UNCHANGED_RANGES))
    @pytest.mark.parametrize("value", [-0.01, -1.0, 1.01, 2.0])
    def test_each_still_refuses_everything_outside_zero_to_one(self, name, value):
        with pytest.raises(ConfigError):
            Thresholds(**{name: value})

    @pytest.mark.parametrize("name", sorted(CONTRACT_DEFAULTS))
    @pytest.mark.parametrize("value", [True, False])
    def test_a_boolean_is_still_refused_everywhere_including_confidence(self, name, value):
        # bool is an int subclass: True would otherwise read as 1.0 and False as
        # 0.0, and a `confidence = true` in a TOML file is a plausible typo. The
        # type check must still run *before* the range check after the narrowing,
        # or False becomes "below the floor" rather than "not a number".
        with pytest.raises(ConfigError):
            Thresholds(**{name: value})

    @pytest.mark.parametrize("name", sorted(CONTRACT_DEFAULTS))
    @pytest.mark.parametrize("value", ["0.9", None, [0.9], "0.95"])
    def test_a_non_number_is_still_refused_everywhere(self, name, value):
        with pytest.raises(ConfigError):
            Thresholds(**{name: value})

    def test_all_six_defaults_are_untouched(self):
        # The narrowing changes which values are legal, not which value you get by
        # not choosing one. A default nudged to 0.96 "to be safe" would be a silent
        # change to every verdict this tool has ever produced.
        assert Thresholds().to_dict() == CONTRACT_DEFAULTS

    def test_setting_confidence_leaves_the_other_five_at_their_defaults(self):
        thresholds = Thresholds(confidence=0.99)
        assert thresholds.to_dict() == {**CONTRACT_DEFAULTS, "confidence": 0.99}


class TestTheRefusal:
    """The type on this path and what the message has to say."""

    def test_the_error_is_a_configerror(self):
        # Established type for a nonsense threshold. Not ValueError -- the CLI maps
        # migkit's hierarchy to exit codes, and a bare ValueError out of a config
        # load reads to CI as "the tool broke" rather than "fix your config".
        with pytest.raises(ConfigError) as exc:
            Thresholds(confidence=0.3)
        assert isinstance(exc.value, MigrationKitError)

    @pytest.mark.parametrize("value", [0.5, 0.3, 0.0, -0.25])
    def test_the_message_names_the_offending_value(self, value):
        # The operator has a TOML file open. The message has to point at the line.
        with pytest.raises(ConfigError) as exc:
            Thresholds(confidence=value)
        assert repr(value) in str(exc.value), str(exc.value)

    @pytest.mark.parametrize("value", [0.5, 0.3, 0.0, -0.25])
    def test_the_message_names_the_threshold(self, value):
        with pytest.raises(ConfigError) as exc:
            Thresholds(confidence=value)
        assert "confidence" in str(exc.value), str(exc.value)

    @pytest.mark.parametrize("value", [0.5, 0.3, 1e-9])
    def test_the_message_does_not_quote_a_range_it_is_not_enforcing(self, value):
        # A refusal of 0.3 that says "must be in (0.0, 1.0)" is worse than no
        # message: it tells the operator their value is inside the stated range and
        # was rejected anyway. Whatever wording the implementation chooses, the
        # number that actually decided the refusal has to appear in it.
        with pytest.raises(ConfigError) as exc:
            Thresholds(confidence=value)
        message = str(exc.value)
        assert "0.5" in message, f"the refusal does not mention its own floor: {message}"


class TestTheConfigFilePath:
    """The narrowing has to reach the place operators actually set the value.

    ``Thresholds`` is constructed from a TOML table by ``JudgeConfig.parse``, and
    that is the only route a real operator takes to it. A validation change that
    somehow bypassed this path would leave the original failure exactly where it
    was.
    """

    def test_a_below_floor_confidence_in_the_file_is_refused_at_load(self, tmp_path):
        with pytest.raises(ConfigError):
            JudgeConfig.load(_config_with_confidence(tmp_path, 0.3))

    def test_the_floor_itself_in_the_file_is_refused_at_load(self, tmp_path):
        with pytest.raises(ConfigError):
            JudgeConfig.load(_config_with_confidence(tmp_path, CONTRACT_CONFIDENCE_FLOOR))

    def test_the_message_from_the_file_path_still_names_the_value(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            JudgeConfig.load(_config_with_confidence(tmp_path, 0.3))
        assert "0.3" in str(exc.value), str(exc.value)

    def test_a_legal_confidence_in_the_file_still_loads(self, tmp_path):
        # The refusal must be a refusal of illegal values, not of the table.
        config = JudgeConfig.load(_config_with_confidence(tmp_path, 0.99))
        assert config.thresholds.confidence == 0.99
        assert config.thresholds.pass_rate_floor == CONTRACT_DEFAULTS["pass_rate_floor"]
