"""Four defects found in `comparison.py` after the acceptance suite went green.

Each was confirmed by computation before a line was changed, and each has a test
here that was red on the module as it stood and is green on the module as it is.
The four, in the order they appear below:

1. **WRONG-VERDICT, and it is the project's signature failure returning through a
   second door.** build-plan §6 records a draft rule that gave GO to a model which
   crashes and NO-GO to one which merely answered badly. `judging.py` imputes
   `SCORE_MIN` for *failed completions* only -- but rigor's judge prompt tells a
   judge to answer `"score": null` when the rubric gives it no basis, while still
   requiring the `pass` boolean, so a **successful** completion can arrive scored
   `None`. `_counted` kept that record, putting it in the pass rate; `_scores`
   dropped it, taking it out of the Mann-Whitney array. Measured at 40 items x
   n=5, floor 0.90, baseline all-pass at 5.0, candidate failing all five draws on
   two items:

   ===========================  ==========  =========  ===========  ==========  =======  ====
   candidate                    pass count  pass_rate  lower bound  p           verdict  exit
   ===========================  ==========  =========  ===========  ==========  =======  ====
   the 10 bad answers at `1.0`  190/200     0.95       0.91811      0.00069443  NO-GO    1
   the same 10 at `null`        190/200     0.95       0.91811      1.0         GO       0
   ===========================  ==========  =========  ===========  ==========  =======  ====

   Identical pass counts, opposite verdicts, favouring the model whose judge went
   quiet -- and silent, because 10/200 is a share of exactly 0.0500 against a
   `judge_failure_tolerance` of exactly 0.05 tested with a strict `>`.

2. **WRONG-VERDICT (latent): the guard against this class was dead code.**
   `_pass_rate` ran `setdefault("underpowered", False)` before `_floor_power`
   tested `if "underpowered" not in floor_stats`, so the key was always present
   and `DependencyContractError` could not be raised through `compare()`. With the
   key genuinely absent, 38/40 both sides against a 0.90 floor takes clause 2
   instead of clause 3: NO-GO where rigor's own answer is REVIEW.

3. **MISLEADING: the power check ignored its own multiplicity correction.**
   `regressed` is decided at Holm's threshold -- alpha/k for the one regressing
   judge among k -- while `required_sample_size` was called with the raw alpha.
   At the n=109 that certified as adequate, baseline 0.95, the empirical power of
   a four-judge panel is 59.7% against a `power_target` of 0.80.

4. **MISLEADING (latent): NaN was an order-dependent Holm barrier.** `sorted` has
   no total order over NaN: `[nan, .001, .001, .001]` rejected nothing and
   `[.001, .001, .001, nan]` rejected three. rigor's `distribution.py` documents
   that it can return NaN and `compare` passes the p-value straight through.

**Where the numbers come from.** The p-values, bounds and required-n figures are
the acceptance suite's own published constants (`tests/test_comparison.py`
module docstring), which were derived outside this package from
`scipy.stats.mannwhitneyu(current, baseline, alternative="less")` and
`opik_rigor.assert_pass_rate`. The power and FWER figures below are measured
here, by seeded Monte Carlo against `opik_rigor.assert_no_regression` -- the same
call `comparison._regression` makes -- because a claim about power that is
asserted rather than measured is the defect being fixed, not a test of it.

Every RNG is seeded from a literal. Nothing here touches the network or a key.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

import pytest
from opik_rigor import PassRateError, RegressionError, assert_no_regression
from opik_rigor.judge import SCORE_MAX, SCORE_MIN

from model_migration_kit import comparison as C
from model_migration_kit.contracts import Verdict
from model_migration_kit.errors import DependencyContractError
from model_migration_kit.judging import JudgedArtifact, JudgeRecord, Thresholds

GOLDENSET_HASH = "a" * 64
JUDGES_HASH = "b" * 64
RUBRIC_HASH = "e" * 64
JUDGE_MODEL = "claude-sonnet-4-5-20250929"
J = "helpfulness"
BASELINE_MODEL = "model-a-20260101"

ITEMS_40 = [f"q{index:03d}" for index in range(40)]
DEFAULTS = Thresholds()

#: 190 fives and ten ones against 200 fives, one-sided Mann-Whitney. The
#: acceptance suite's published constant; the whole of defect 1 is that two
#: candidates which both deserve this number did not both get it.
P_TEN_LOW = 0.0006944255237317142
#: One-sided Wilson lower bound on 190/200 at the default confidence, from
#: ``opik_rigor.assert_pass_rate``.
LOWER_BOUND_190_OF_200 = 0.9181081670817905


# --------------------------------------------------------------------------- #
# Fixtures. Built completion by completion; no RNG anywhere in this section.
# --------------------------------------------------------------------------- #


def _artifact(
    model_id: str,
    per_judge: Mapping[str, Mapping[str, int]],
    *,
    n: int = 5,
    unscored: Sequence[str] = (),
    unscored_passes: bool = False,
) -> JudgedArtifact:
    """A judged artifact, ``k`` of ``n`` draws passing per item.

    A scored draw carries ``SCORE_MAX`` when it passed and ``SCORE_MIN`` when it
    did not. Items named in ``unscored`` instead carry ``score=None`` with
    ``imputed=False`` and ``parse_failure=False`` -- which is precisely what
    ``judging._grade`` writes for a completion that *succeeded* and whose judge
    answered ``{"pass": false, "score": null}``. That combination is the defect:
    it is neither of the two holes the module was written to handle.

    ``unscored_passes`` makes the unscored draws *passing* ones instead, which is
    equally legal under rigor's prompt (``pass`` is required, ``score`` is not)
    and is the case that decides which way an unscored record may be imputed.
    """
    records: list[JudgeRecord] = []
    for judge, passes in per_judge.items():
        for item_id, k in passes.items():
            for index in range(n):
                ok = index < k
                blank = item_id in unscored and ok is unscored_passes
                records.append(
                    JudgeRecord(
                        judge=judge,
                        item_id=item_id,
                        sample_index=index,
                        passed=ok,
                        score=None if blank else (SCORE_MAX if ok else SCORE_MIN),
                        reason=None if blank else "graded",
                    )
                )
    return JudgedArtifact(
        model_id=model_id,
        goldenset_hash=GOLDENSET_HASH,
        judges_hash=JUDGES_HASH,
        n_per_item=n,
        records=tuple(records),
        judges=tuple(
            {
                "name": judge,
                "model": JUDGE_MODEL,
                "adapter_class": "FakeAdapter",
                "rubric_hash": RUBRIC_HASH,
            }
            for judge in per_judge
        ),
    )


def _all_pass(items: Sequence[str] = ITEMS_40, n: int = 5) -> dict[str, int]:
    return {item_id: n for item_id in items}


def _two_items_failed() -> dict[str, int]:
    """Every item passing 5 of 5 except ``q000`` and ``q001``, which pass none."""
    passes = _all_pass()
    passes["q000"] = 0
    passes["q001"] = 0
    return passes


def _row(report: object, judge: str = J) -> object:
    for one in report.judges:  # type: ignore[attr-defined]
        if one.name == judge:
            return one
    raise AssertionError(f"no row for judge {judge!r}")


# --------------------------------------------------------------------------- #
# Defect 1. The headline table: identical pass counts must not produce opposite
# verdicts, whichever hole the missing scores came out of.
# --------------------------------------------------------------------------- #


def test_ten_bad_answers_and_ten_unscored_failures_get_the_same_verdict() -> None:
    """The two-row table from the defect report, asserted row against row.

    Both candidates fail every draw on ``q000`` and ``q001`` and pass everything
    else, so both post 190/200 with a 0.9181081670817905 lower bound and both
    clear the 0.90 floor. The only difference is what the judge said about the ten
    failing draws: ``1.0`` for one candidate, ``null`` for the other.

    Before the fix the second candidate's ten records counted against its pass
    rate and vanished from the Mann-Whitney array, leaving 190 fives against 200
    fives -- p=1.0, **GO**, exit 0 -- against the first candidate's p=0.00069,
    **NO-GO**, exit 1. That is build-plan §6's crasher-beats-bad-answerer failure
    with a different hole in the same place.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _all_pass()})
    scored = _artifact("model-low-20260101", {J: _two_items_failed()})
    unscored = _artifact(
        "model-null-20260101", {J: _two_items_failed()}, unscored=("q000", "q001")
    )

    low = C.compare(baseline, scored, thresholds=DEFAULTS)
    null = C.compare(baseline, unscored, thresholds=DEFAULTS)

    # The premise: the table's first three columns really are identical.
    for report in (low, null):
        side = _row(report).candidate
        assert (side["successes"], side["n"]) == (190, 200)
        assert side["pass_rate"] == pytest.approx(0.95)
        assert side["lower_bound"] == pytest.approx(LOWER_BOUND_190_OF_200, rel=1e-9)

    # The finding: the last three columns must now be identical too.
    assert _row(null).p_value == pytest.approx(P_TEN_LOW, rel=1e-12)
    assert _row(low).p_value == pytest.approx(P_TEN_LOW, rel=1e-12)
    assert null.verdict == low.verdict == Verdict.NO_GO
    assert null.rule == low.rule == 1
    assert null.exit_code == low.exit_code == 1


def test_an_unscored_record_stays_in_both_populations_or_neither() -> None:
    """The invariant underneath the table, stated directly.

    Whatever a record's score, it may not be counted in the pass rate and dropped
    from the score array: one record, two populations, two memberships. Every
    record ``_counted`` keeps arrives in the array, so the two lengths agree.

    Asserted through ``_scores`` and ``_counted`` rather than through the verdict
    because it is the property, not one of its consequences -- the verdict flip
    above is what it costs when it fails.
    """
    unscored = _artifact(
        "model-null-20260101", {J: _two_items_failed()}, unscored=("q000", "q001")
    )
    counted = C._counted(unscored.for_judge(J))
    baseline = C._counted(_artifact(BASELINE_MODEL, {J: _all_pass()}).for_judge(J))

    base_scores, cand_scores, test_ran, _, missing = C._scores(baseline, counted)

    assert len(counted) == 200  # nothing left the pass rate ...
    assert len(cand_scores) == len(counted)  # ... and nothing left the score array
    assert len(base_scores) == len(baseline)
    assert missing == (0, 10)  # the ten are still counted and still reported
    assert test_ran == C.TEST_SCORES


def test_an_unscored_record_is_imputed_on_the_side_its_own_verdict_took() -> None:
    """Direction, not just presence: the fill may never cross the pass/fail line.

    Imputing every unscored record at ``SCORE_MIN`` would close the asymmetry and
    open a new one, in the other direction: a judge that answers ``{"pass": true,
    "score": null}`` on ten of a candidate's *passing* completions would drive
    those to the bottom of the scale and manufacture a regression out of the
    judge's silence -- the same conflation that keeps parse failures out of the
    pass rate. So the fill follows the verdict the judge did render.

    Here the candidate is identical to the baseline except that ten of its
    passing draws went unscored. It passes 200/200 and must not read as a
    regression at all.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _all_pass()})
    candidate = _artifact(
        "model-quiet-20260101",
        {J: _all_pass()},
        unscored=("q000", "q001"),
        unscored_passes=True,
    )

    report = C.compare(baseline, candidate, thresholds=DEFAULTS)
    row = _row(report)

    assert row.candidate["successes"] == 200
    assert row.regressed is False
    assert report.verdict == Verdict.GO
    # And the ten fills are the ceiling, not the floor: the arrays are identical.
    _, cand_scores, _, _, _ = C._scores(
        C._counted(baseline.for_judge(J)), C._counted(candidate.for_judge(J))
    )
    assert sorted(cand_scores) == [SCORE_MAX] * 200


def test_the_disclosure_fires_at_the_tolerance_and_not_only_above_it() -> None:
    """``>=``, not ``>``: the silent run sat on exactly 0.0500 against 0.05.

    Two thresholds, two jobs. ``judging.py``'s parse-failure gate is a
    *permission* -- how much unreliability is tolerated before refusing to run --
    and tolerating up to and including the tolerated amount is what the word
    means, so it stays strict. This one is a *disclosure*: how much of the verdict
    rests on imputation. A disclosure silent at its own documented number
    misreports when it speaks, and under strict ``>`` the effective trigger is not
    even documentable -- 10/200 and 1/20 are exactly 0.05 and silent, 3/61 is
    0.0492 and silent, 4/61 is 0.0656 and loud, so whether a run "at 5%" warns
    depends on whether n makes the fraction representable.

    Ten of 200 is the share the GO-instead-of-NO-GO run carried.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _all_pass()})
    candidate = _artifact(
        "model-null-20260101", {J: _two_items_failed()}, unscored=("q000", "q001")
    )
    report = C.compare(baseline, candidate, thresholds=DEFAULTS)

    assert C._share(10, 200) == DEFAULTS.judge_failure_tolerance  # exactly on it
    spoke = [one for one in report.warnings if "no numeric score" in one]
    assert spoke, f"nothing disclosed at the tolerance; warnings were {report.warnings}"
    assert "5.0%" in spoke[0]

    # And it stays quiet when there is nothing to disclose, including at a
    # tolerance of 0.0, which ``Thresholds`` admits.
    clean = C.compare(
        baseline,
        _artifact("model-clean-20260101", {J: _two_items_failed()}),
        thresholds=Thresholds(judge_failure_tolerance=0.0),
    )
    assert not [one for one in clean.warnings if "no numeric score" in one]


# --------------------------------------------------------------------------- #
# Defect 2. A guard that cannot fire on the path it protects is not a guard.
# --------------------------------------------------------------------------- #


def _floor_miss_pair() -> tuple[JudgedArtifact, JudgedArtifact]:
    """38 of 40 on both sides, one draw per item -- review §3's own fixture.

    Against a 0.90 floor rigor reports the lower bound at 0.8597 with
    ``underpowered=True`` and ``runs_needed=113``, so the honest answer is clause
    3, REVIEW. The two sides are identical, so no regression is available to
    outrank it.
    """
    passes = {item_id: 1 for item_id in ITEMS_40}
    passes["q000"] = 0
    passes["q001"] = 0
    baseline = _artifact(BASELINE_MODEL, {J: passes}, n=1)
    candidate = _artifact("model-b-20260101", {J: dict(passes)}, n=1)
    return baseline, candidate


def test_rigor_dropping_the_underpowered_flag_is_an_error_through_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard, exercised on the path a user actually takes.

    ``_floor_power`` refuses to guess when rigor's failed pass-rate report carries
    no ``underpowered`` flag. It could not refuse anything through ``compare()``:
    ``_pass_rate`` had already run ``setdefault("underpowered", False)`` on the
    same dict, so the key the guard looks for was always there. The guard fired
    only when ``_floor_power`` was called directly, which nothing but a test does.

    rigor is at 0.1.1 with an ``[Unreleased]`` section that is moving, so this is
    a dependency contract being actively edited, not a hypothetical.
    """
    real = C.assert_pass_rate

    def without_the_flag(*args: object, **kwargs: object) -> object:
        try:
            return real(*args, **kwargs)  # type: ignore[arg-type]
        except PassRateError as exc:
            stats = {k: v for k, v in exc.stats.items() if k != "underpowered"}
            raise PassRateError(str(exc), **stats) from None

    baseline, candidate = _floor_miss_pair()
    monkeypatch.setattr(C, "assert_pass_rate", without_the_flag)

    with pytest.raises(DependencyContractError) as caught:
        C.compare(baseline, candidate, thresholds=Thresholds(pass_rate_floor=0.90))
    assert "underpowered" in str(caught.value)


def test_the_verdict_the_dead_guard_was_hiding_is_review_not_no_go() -> None:
    """The positive control, and the reason the guard is worth having.

    Same fixture, real rigor: clause 3, REVIEW, exit 2. With the flag absent and
    silently defaulted to ``False`` the row took clause 2 and this read NO-GO,
    exit 1, with nothing raised anywhere -- a blocked migration nobody could trace
    to evidence.
    """
    baseline, candidate = _floor_miss_pair()
    report = C.compare(baseline, candidate, thresholds=Thresholds(pass_rate_floor=0.90))

    assert report.verdict == Verdict.REVIEW
    assert report.rule == 3
    assert report.exit_code == 2
    assert _row(report).underpowered is True


def test_the_rendered_row_still_carries_the_power_keys_it_always_did() -> None:
    """Splitting the dicts must not change the shape the report renders.

    ``_pass_rate`` now returns rigor's report untouched for the guard to inspect
    *and* a filled-out copy for the row, so every row still has the same keys
    whichever way the gate went. Losing that would move the defect into
    ``report.py`` instead of fixing it.
    """
    baseline, candidate = _floor_miss_pair()
    report = C.compare(baseline, candidate, thresholds=Thresholds(pass_rate_floor=0.90))
    side = _row(report).candidate

    assert side["underpowered"] is True
    assert side["runs_needed"] == 113


# --------------------------------------------------------------------------- #
# Defect 3. Power has to be measured against the threshold that will be used.
# --------------------------------------------------------------------------- #


def _mw_p(passes_baseline: int, passes_candidate: int, n: int, memo: dict) -> float:
    """One-sided Mann-Whitney p through rigor, memoised on the two counts.

    The arrays are two-valued -- ``SCORE_MAX`` for a pass, ``SCORE_MIN`` for a
    failure, exactly what ``_artifact`` builds -- so the p-value is a function of
    the two pass counts alone and a memo makes 20 000 trials affordable. The call
    is ``assert_no_regression(current, baseline, ...)`` in that order, which is
    the call ``comparison._regression`` makes; reversing it would silently invert
    the question.
    """
    key = (passes_baseline, passes_candidate)
    hit = memo.get(key)
    if hit is not None:
        return hit
    base = [SCORE_MAX] * passes_baseline + [SCORE_MIN] * (n - passes_baseline)
    cand = [SCORE_MAX] * passes_candidate + [SCORE_MIN] * (n - passes_candidate)
    try:
        stats = assert_no_regression(cand, base, alpha=0.05)
    except RegressionError as exc:
        stats = exc.stats
    memo[key] = float(stats["p_value"])
    return memo[key]


def _empirical_power(
    n: int, threshold: float, *, baseline_rate: float, effect: float, seed: int,
    trials: int = 4000,
) -> float:
    """Fraction of seeded trials in which a real ``effect`` drop is rejected."""
    rng = random.Random(seed)
    memo: dict = {}
    hits = 0
    for _ in range(trials):
        base = sum(rng.random() < baseline_rate for _ in range(n))
        cand = sum(rng.random() < baseline_rate - effect for _ in range(n))
        hits += _mw_p(base, cand, n, memo) < threshold
    return hits / trials


def test_required_sample_size_is_computed_at_the_threshold_holm_will_use() -> None:
    """The fix, as arithmetic: the sizing alpha is ``alpha / judges``.

    ``required_sample_size`` is a pure function and does not know about judges, so
    the correction lives at its call site; this pins the numbers that call site
    must produce. At a 0.95 baseline and a ten-point effect: 109 per side at
    alpha=0.05, and 138 / 155 / 167 at alpha/2, alpha/3, alpha/4.
    """
    sized = {
        k: C.required_sample_size(
            0.95, min_detectable_effect=0.10, power_target=0.80, alpha=0.05 / k
        )
        for k in (1, 2, 3, 4)
    }
    assert sized == {1: 109, 2: 138, 3: 155, 4: 167}


@pytest.mark.parametrize("judges", [1, 2, 3, 4])
def test_a_panel_is_only_called_powered_at_a_sample_that_reaches_the_target(
    judges: int,
) -> None:
    """Through ``compare()``: 109 completions a side is adequate only at one judge.

    Every judge in the family sees the same identical arrays, so nothing here
    regresses and nothing misses the floor; ``mw_powered`` is the only flag that
    can move, which makes the verdict a direct readout of it. Before the fix all
    four panels reported ``mw_powered=True`` at 109 -- and, measured, 59.7% real
    power at four judges against a target of 0.80.

    110 completions a side: 22 items at n=5, all passing, so the baseline rate is
    1.0 and the required n is 56 at alpha and 86 at alpha/4. Both are cleared, so
    this panel is honestly powered at every family size -- the case below is the
    one that moves.
    """
    items = [f"p{index:03d}" for index in range(22)]
    names = [f"judge-{index}" for index in range(judges)]
    baseline = _artifact(BASELINE_MODEL, {name: _all_pass(items) for name in names})
    candidate = _artifact(
        "model-b-20260101", {name: _all_pass(items) for name in names}
    )
    report = C.compare(baseline, candidate, thresholds=DEFAULTS)

    row = _row(report, names[0])
    assert row.power.judges == judges
    assert row.power.alpha == pytest.approx(0.05 / judges)
    assert row.power.alpha_uncorrected == pytest.approx(0.05)
    assert row.mw_powered is True
    assert report.verdict == Verdict.GO


def test_a_four_judge_panel_at_the_old_adequate_sample_is_no_longer_certified() -> None:
    """The row that flips: 80 completions a side, baseline 1.0, four judges.

    At a baseline rate of 1.0 a ten-point drop needs 56 completions a side at the
    raw alpha and 86 at alpha/4, so 80 sits between the two: it cleared the
    uncorrected bar -- which is why this panel used to be certified as powered --
    and misses the one Holm will actually judge it at. Nothing regresses and
    nothing misses the floor, so ``mw_powered`` is the only flag that moves, and
    the verdict moves with it from GO to REVIEW: towards "we cannot tell", which
    is the only direction invariant 5 permits.

    The same 80 completions under a single judge stay GO, which is what stops this
    from being a blanket tightening of the power rule.
    """
    items = [f"r{index:03d}" for index in range(16)]
    names = [f"judge-{index}" for index in range(4)]
    baseline = _artifact(BASELINE_MODEL, {name: _all_pass(items) for name in names})
    candidate = _artifact(
        "model-b-20260101", {name: _all_pass(items) for name in names}
    )

    report = C.compare(baseline, candidate, thresholds=DEFAULTS)
    row = _row(report, names[0])

    assert row.power.n_observed == 80
    assert row.power.n_required == 86  # alpha/4
    assert C.required_sample_size(
        1.0, min_detectable_effect=0.10, power_target=0.80, alpha=0.05
    ) == 56  # what the uncorrected rule asked for, and 80 cleared
    assert row.mw_powered is False
    assert report.verdict == Verdict.REVIEW
    assert report.rule == 4

    solo = C.compare(
        _artifact(BASELINE_MODEL, {J: _all_pass(items)}),
        _artifact("model-b-20260101", {J: _all_pass(items)}),
        thresholds=DEFAULTS,
    )
    assert _row(solo).power.n_required == 56
    assert solo.verdict == Verdict.GO


@pytest.mark.parametrize(
    ("judges", "seed"), [(1, 4242001), (2, 4242002), (3, 4242003), (4, 4242004)]
)
def test_the_corrected_sample_size_actually_reaches_the_power_target(
    judges: int, seed: int
) -> None:
    """Measured, not assumed -- assuming it is what produced the defect.

    A real 0.95 -> 0.85 drop, at the n the corrected rule asks for, rejected at
    the Holm threshold that judge would face. 4 000 seeded trials, whose standard
    error at p=0.8 is 0.0063, so a 0.75 floor is roughly eight standard errors
    below the target and cannot be reached by noise.

    At 20 000 trials the same cells measure 0.819 / 0.809 / 0.808 / 0.808. The
    uncorrected sizing measures 0.818 / 0.725 / 0.657 / 0.596 on the identical
    seeds -- which is the defect, and which the last assertion pins from the
    other side so that a silent reversion cannot pass this test.
    """
    threshold = 0.05 / judges
    corrected = C.required_sample_size(
        0.95, min_detectable_effect=0.10, power_target=0.80, alpha=threshold
    )
    power = _empirical_power(
        corrected, threshold, baseline_rate=0.95, effect=0.10, seed=seed
    )
    assert power >= 0.75, f"{judges} judges: {power:.3f} at n={corrected}"

    if judges > 1:
        uncorrected = C.required_sample_size(
            0.95, min_detectable_effect=0.10, power_target=0.80, alpha=0.05
        )
        was = _empirical_power(
            uncorrected, threshold, baseline_rate=0.95, effect=0.10, seed=seed
        )
        assert was < power
        assert was < 0.75, f"the defect is not reproduced at {judges} judges"


def test_the_single_judge_rule_stays_calibrated() -> None:
    """What must not break: k=1 is unchanged, and it was already well calibrated.

    Three baseline rates at the n the rule asks for, measured against the 0.80
    target: 0.95 -> 109, 0.90 -> 155, 0.85 -> 195. At 20 000 trials these read
    0.819 / 0.805 / 0.796.
    """
    for baseline_rate, seed in ((0.95, 5150095), (0.90, 5150090), (0.85, 5150085)):
        n = C.required_sample_size(
            baseline_rate, min_detectable_effect=0.10, power_target=0.80, alpha=0.05
        )
        power = _empirical_power(
            n, 0.05, baseline_rate=baseline_rate, effect=0.10, seed=seed
        )
        assert 0.75 <= power <= 0.87, f"baseline {baseline_rate}: {power:.3f} at n={n}"


# --------------------------------------------------------------------------- #
# Defect 4. NaN is not a sort key.
# --------------------------------------------------------------------------- #


def test_a_non_finite_p_value_does_not_block_the_rest_of_the_family() -> None:
    """The two orderings of the same family must reach the same decisions.

    ``sorted`` is not a total order over NaN, so before the guard the NaN's
    position in the *input* decided the outcome: at the front it tripped
    ``still_rejecting`` on the first step and rejected nothing, at the back it
    rejected the three real findings. Same p-values, one reordering, NO-GO
    against GO.

    A non-finite p is read as 1.0: it is never rejected, and it never shrinks the
    family, so the three genuine findings are still tested at alpha/4, alpha/3 and
    alpha/2 rather than at a quietly loosened ladder.
    """
    nan = float("nan")
    front = C.holm_bonferroni([nan, 0.001, 0.001, 0.001], alpha=0.05)
    back = C.holm_bonferroni([0.001, 0.001, 0.001, nan], alpha=0.05)

    assert [rejected for rejected, _ in front] == [False, True, True, True]
    assert [rejected for rejected, _ in back] == [True, True, True, False]
    assert sorted(threshold for _, threshold in front) == pytest.approx(
        sorted(threshold for _, threshold in back)
    )
    # The family is still four wide: the smallest p is tested at alpha/4.
    assert min(threshold for _, threshold in front) == pytest.approx(0.0125)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_every_non_finite_p_value_is_read_as_no_rejection(bad: float) -> None:
    """Including the infinities, which sort fine and are still not p-values.

    ``-inf`` is the dangerous one: it orders below every real p-value and would
    otherwise be rejected at the most stringent threshold in the family, turning
    a broken test into the finding that decides the verdict.
    """
    decisions = C.holm_bonferroni([bad, 0.5, 0.5], alpha=0.05)
    assert decisions[0][0] is False


def test_a_non_finite_p_value_is_disclosed_rather_than_silently_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading NaN as 1.0 is the safe answer, and it is not the same as an answer.

    rigor's ``distribution.py`` anticipates NaN explicitly. Whichever way it is
    handled, a judge whose regression test returned nothing must not read to a
    compliance reviewer exactly like a judge that looked and found nothing.
    """

    def nan_regression(*args: object, **kwargs: object) -> dict[str, object]:
        return {"p_value": float("nan")}

    baseline = _artifact(BASELINE_MODEL, {J: _all_pass()})
    candidate = _artifact("model-b-20260101", {J: _two_items_failed()})
    monkeypatch.setattr(C, "_regression", nan_regression)

    report = C.compare(baseline, candidate, thresholds=DEFAULTS)

    assert _row(report).regressed is False
    assert [one for one in report.warnings if "non-finite" in one]


# --------------------------------------------------------------------------- #
# What must not have broken. Every one of these was green before the fixes and
# is asserted here rather than trusted, because three of the four fixes move
# numbers that these properties are built out of.
# --------------------------------------------------------------------------- #


def test_the_imputed_and_low_score_candidates_are_still_byte_identical() -> None:
    """build-plan §6's own equivalence, unchanged: p == 0.0006944255237317142.

    A completion that failed (``judging.py`` imputes ``SCORE_MIN``,
    ``imputed=True``) and one the judge scored 1.0 must produce the same number,
    to the last bit. The defect-1 fix adds a third route to that same array and
    must not have perturbed the two that were already correct.
    """
    baseline = _artifact(BASELINE_MODEL, {J: _all_pass()})
    scored = _artifact("model-low-20260101", {J: _two_items_failed()})
    imputed = _artifact("model-crash-20260101", {J: _two_items_failed()})
    imputed = JudgedArtifact(
        model_id=imputed.model_id,
        goldenset_hash=imputed.goldenset_hash,
        judges_hash=imputed.judges_hash,
        n_per_item=imputed.n_per_item,
        records=tuple(
            record
            if record.passed
            else JudgeRecord(
                judge=record.judge,
                item_id=record.item_id,
                sample_index=record.sample_index,
                passed=False,
                score=SCORE_MIN,
                imputed=True,
                error="completion failed: timeout after 30s",
            )
            for record in imputed.records
        ),
        judges=imputed.judges,
    )

    low = _row(C.compare(baseline, scored, thresholds=DEFAULTS))
    crash = _row(C.compare(baseline, imputed, thresholds=DEFAULTS))

    assert low.p_value == crash.p_value == P_TEN_LOW
    assert crash.imputed_candidate == 10
    assert low.imputed_candidate == 0


def test_holm_still_controls_the_family_wise_error_rate() -> None:
    """Two identical models, four judges, 2 000 seeded trials.

    The correction's entire purpose. An independent run measured 17.67%
    uncorrected against 4.77% under Holm at four judges, nominal 5%; this fixture
    -- four independent draws of the same 0.95-against-0.95 comparison at 60
    completions a side -- measures 22.45% and 3.85% on its own seed. The two
    disagree on the exact rate because the rate depends on n and on the baseline,
    and agree on the only thing being asserted: Holm holds the family at or under
    nominal while the uncorrected rule is several times above it.

    2 000 trials give a standard error of 0.005 at p=0.05, so the 0.08 ceiling is
    six standard errors clear of the measured 3.85%.
    """
    trials, n, judges = 2000, 60, 4
    rng = random.Random(31415926)
    memo: dict = {}
    uncorrected = 0
    corrected = 0
    for _ in range(trials):
        family = []
        for _ in range(judges):
            base = sum(rng.random() < 0.95 for _ in range(n))
            cand = sum(rng.random() < 0.95 for _ in range(n))
            family.append(_mw_p(base, cand, n, memo))
        uncorrected += any(p < 0.05 for p in family)
        corrected += any(
            rejected for rejected, _ in C.holm_bonferroni(family, alpha=0.05)
        )
    assert uncorrected / trials > corrected / trials
    assert corrected / trials <= 0.08, corrected / trials


def test_the_precedence_table_is_unchanged_over_all_sixteen_flag_combinations() -> None:
    """§6's resolution as a total function, re-derived from the plan's own words.

    Every combination of the four flags for a single judge, resolved against a
    table written from build-plan §6 rather than from the module: (1) regressed ->
    NO-GO; (2) else floor missed and not underpowered -> NO-GO; (3) else floor
    missed while underpowered -> REVIEW; (4) else not powered -> REVIEW; (5) else
    GO. None of the four fixes touches ``explain_verdict``, and this is what says
    so.
    """
    seen = {}
    for bits in range(16):
        regressed = bool(bits & 1)
        floor_cleared = bool(bits & 2)
        underpowered = bool(bits & 4)
        mw_powered = bool(bits & 8)
        if regressed:
            expected, rule = Verdict.NO_GO, 1
        elif not floor_cleared and not underpowered:
            expected, rule = Verdict.NO_GO, 2
        elif not floor_cleared:
            expected, rule = Verdict.REVIEW, 3
        elif not mw_powered:
            expected, rule = Verdict.REVIEW, 4
        else:
            expected, rule = Verdict.GO, 5
        decision = C.explain_verdict(
            [
                C.JudgeFlags(
                    name=J,
                    regressed=regressed,
                    floor_cleared=floor_cleared,
                    underpowered=underpowered,
                    mw_powered=mw_powered,
                )
            ]
        )
        seen[bits] = (decision.verdict, decision.rule)
        assert (decision.verdict, decision.rule) == (expected, rule), bits
    assert len(seen) == 16


def test_no_combination_of_flags_turns_cannot_tell_into_go() -> None:
    """Invariant 5, over the same sixteen rows plus every two-judge pairing.

    A judge that cannot tell must never be out-voted into a GO by one that can.
    Sixteen single rows give 256 ordered pairs, and any pair containing a row that
    resolves to REVIEW or NO-GO on its own must not resolve to GO together.
    """
    rows = [
        C.JudgeFlags(
            name=f"judge-{bits}",
            regressed=bool(bits & 1),
            floor_cleared=bool(bits & 2),
            underpowered=bool(bits & 4),
            mw_powered=bool(bits & 8),
        )
        for bits in range(16)
    ]
    alone = {row.name: C.explain_verdict([row]).verdict for row in rows}
    for first in rows:
        for second in rows:
            together = C.explain_verdict([first, second]).verdict
            if Verdict.GO in (together,):
                assert alone[first.name] == alone[second.name] == Verdict.GO, (
                    f"{first.name} ({alone[first.name]}) and {second.name} "
                    f"({alone[second.name]}) together read GO"
                )


def test_every_p_value_this_module_hands_to_holm_is_a_real_number() -> None:
    """The guard's own precondition, so it cannot rot into a no-op.

    ``_finite_p`` is what makes the sort well defined; if it ever stopped being
    applied, defect 4 would come back with no test noticing, because the NaN case
    is unreachable on scipy 1.18 and only the ordering property above would fail.
    """
    assert C._finite_p(float("nan")) == 1.0
    assert C._finite_p(float("-inf")) == 1.0
    assert C._finite_p(0.0) == 0.0
    assert C._finite_p(1.0) == 1.0
    assert math.isfinite(C._finite_p(float("inf")))
