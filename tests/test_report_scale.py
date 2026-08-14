"""What the HTML report does when the golden set stops being twelve items.

Every measurement this project publishes comes from the bundled demo: 12 items at
n=5, 60 completions per side, a 25,760-byte report. Its own methodology says the
statistically adequate sample is roughly 200 completions per side and up to 229 at
a 0.80 baseline rate, so the configuration users are told to run had never been
rendered. These tests render it, and the sizes above it, and pin what happens.

The finding they were written to hold in place was a *size law*, not a bug in any
one function::

    html_bytes  >=  changed_items  x  2n  x  max_output_chars

Every changed item became one ``<details>`` block carrying **all n draws from both
sides** plus the item input, each block cut to ``max_output_chars`` (4000 by
default) and marked as cut. Nothing bounded the number of blocks, nothing bounded
the number of draws inside one block, and nothing looked at the total. The
per-block truncation was announced; the document-level consequence was not.

Measured on this law before it was bounded, with 4000-character outputs and every
item changed:

===================  ==========  ================  ===========
golden set           draws (n)   rendered report   peak RSS
===================  ==========  ================  ===========
40 items             5           1.65 MB           129 MB
200 items            5           8.18 MB           200 MB
200 items            20          32.4 MB           441 MB
1000 items           20          161.8 MB          1740 MB
===================  ==========  ================  ===========

The 161.8 MB document was self-contained, carried all 1000 truncation notices, and
returned ``external_urls() == ()``. It held 41,000 ``<pre>`` blocks in one file.
Every guard this project owns passed on it. That is the shape of failure the
project says it cares most about: not a crash, but an artifact that is generated,
valid, attested, and unopenable.

**The bound now exists, and this module is its specification.** The previous
version of this file ended with an assertion written to fail on the commit that
fixed the problem, so that the fix had to say so. It did fail, and this is what it
was replaced with. Two things are asserted, and the first is the reason the second
can be trusted:

1. **The size law still holds in the uncapped region.** Below the budget the
   document is exactly what it always was -- every row, every draw, every
   character -- so a future change that quietly starts abridging small reports
   breaks these tests rather than changing what the report contains.
2. **Above the budget, what is bounded is the quoted text and never the row.**
   Every changed item still has its row, its id, its tags, its judges and its
   margins. What a row past the budget loses is the quotations, and it says so
   where they would have been, in a document that also says so in a band above the
   change sections, in ``ReportModel.warnings``, and in the terminal render.

The allocation rule is stated in :func:`model_migration_kit.report._change_sections`
and re-implemented independently in :func:`_expected` below, so that the expected
values here come from the rule rather than from what the implementation returned.

Kept small on purpose: 30 items at n=5 is 1.2 MB of report in about two seconds,
which is enough to demonstrate a law that is linear in both factors. The
multi-hundred-megabyte end is documented in the table above rather than rendered
in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from opik_rigor import EvidenceLog, FakeAdapter
from rich.console import Console

from model_migration_kit.comparison import compare
from model_migration_kit.contracts import hash_file
from model_migration_kit.demo import judge_script
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, judge_artifact
from model_migration_kit.report import (
    DEFAULT_MAX_REPORT_CHARS,
    ReportModel,
    external_urls,
    render_html,
    render_html_string,
    render_terminal,
)
from model_migration_kit.runner import run_goldenset

#: The report's per-block cut. ``cli.Settings`` exposes it and, since the bound
#: below exists, ``max_report_chars`` beside it -- and nothing else.
MAX_OUTPUT_CHARS = 4000

#: Small enough to render in about two seconds, large enough that the law is
#: visible: 30 items x (2*5 + 1) blocks x 4000 chars is already over a megabyte.
ITEMS = 30
N_PER_ITEM = 5

#: 30 items at n=5 embeds roughly 1.3 M characters, comfortably inside the
#: 10 M-character default, so the fixture below renders in the uncapped region and
#: the size law is still the thing it demonstrates.
UNCAPPED_ROWS = ITEMS

RUBRIC = """# Scale rubric

Score 5 when the answer is exactly the reference.
Score 4 when the reference appears inside a longer answer.
Score 2 when the answer is not supported by the source.
4 and 5 pass; below that fails.
"""

JUDGES_TOML = """
[[judge]]
name   = "accuracy"
model  = "fake-judge-v1"
rubric = "rubric.md"

[thresholds]
pass_rate_floor = 0.90
alpha = 0.05
confidence = 0.95
judge_failure_tolerance = 0.05
min_detectable_effect = 0.10
power_target = 0.80
"""

_SECTIONS = ("flips", "gains", "unstable")


# --------------------------------------------------------------------------- #
# the rule, re-implemented from its own statement
# --------------------------------------------------------------------------- #


def _expected(costs: dict[str, list[int]], budget: int) -> dict[str, int]:
    """How many rows of each section the documented rule embeds, given their costs.

    Written from the prose in ``_change_sections`` and not from its code: rows are
    visited round-robin across flips, gains and unstable; a row is embedded whole
    while the running total stays within the budget; and the first row that does
    not fit stops embedding for the rest of the document. ``budget <= 0`` is no
    bound.

    Taking the per-row costs as given is the point. The rule is about *allocation*,
    and an oracle that also recomputed what a row costs would be re-deriving the
    truncation rather than checking the budget.
    """
    embedded = {name: 0 for name in _SECTIONS}
    if budget <= 0:
        return {name: len(costs.get(name, [])) for name in _SECTIONS}
    spent = 0
    longest = max((len(costs.get(name, [])) for name in _SECTIONS), default=0)
    for index in range(longest):
        for name in _SECTIONS:
            section = costs.get(name, [])
            if index >= len(section):
                continue
            if spent + section[index] > budget:
                return embedded  # stops for the whole document, not just this row
            spent += section[index]
            embedded[name] += 1
    return embedded


def _costs(model: ReportModel) -> dict[str, list[int]]:
    """Per-row quoted-character costs, in document order, from an uncapped render."""
    return {
        "flips": [row.quoted_chars for row in model.flips],
        "gains": [row.quoted_chars for row in model.gains],
        "unstable": [row.quoted_chars for row in model.unstable],
    }


def _embedded(model: ReportModel) -> dict[str, int]:
    return {
        name: sum(1 for row in getattr(model, name) if row.detail_embedded)
        for name in _SECTIONS
    }


# --------------------------------------------------------------------------- #
# fixtures: one pipeline per shape, rendered at several budgets
# --------------------------------------------------------------------------- #


def _write_goldenset(
    path: Path, items: int, *, answer_chars: int, prefix: str = "scale"
) -> None:
    """``items`` cases whose reference answers are ``answer_chars`` long.

    Long references are the realistic case, not an adversarial one: a golden set
    of summarisation or extraction cases has answers in the kilobytes, and the
    report embeds the *outputs*, which are the same size.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for index in range(items):
            answer = f"answer-{index} " + "w" * answer_chars
            record = {
                "id": f"item-{index:04d}",
                "input": f"[{prefix}] What is the canonical answer for case {index}?",
                "reference": answer,
                "tags": ["scale"],
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _responses(goldenset: GoldenSet, *, wrong: bool) -> dict[str, str]:
    """Baseline answers every reference exactly; the candidate answers none of
    them, so every item changes state and every item earns a report row."""
    out: dict[str, str] = {}
    for index, item in enumerate(goldenset):
        assert item.reference is not None
        if wrong:
            out[item.input] = f"answer-{index + 10**7} " + "w" * (len(item.reference) - 20)
        else:
            out[item.input] = item.reference
    return out


def _run_pipeline(
    root: Path,
    goldenset_path: Path,
    baseline: Any,
    candidate: Any,
    *,
    n: int,
) -> Path:
    """One full pipeline run, keyless, through the production path.

    ``FakeAdapter`` at the provider seam and the demo's own judge script -- the
    same substitution ``migkit demo`` makes -- and everything below it is the code
    a paying run executes: ``run_goldenset``, ``judge_artifact``, ``compare``, and
    a report rebuilt from the evidence log on disk.
    """
    (root / "rubric.md").write_text(RUBRIC, encoding="utf-8")
    (root / "judges.toml").write_text(JUDGES_TOML, encoding="utf-8")
    goldenset = GoldenSet.load(goldenset_path)
    evidence = EvidenceLog(root / "evidence.jsonl")
    runs = [
        run_goldenset(
            goldenset,
            FakeAdapter(model_id=f"fake-{side}-v1", responses=script),
            out_dir=root,
            n=n,
            evidence=evidence,
        )
        for side, script in (("baseline", baseline), ("candidate", candidate))
    ]
    config = JudgeConfig.load(root / "judges.toml")
    script = judge_script(goldenset)
    panel = config.build(
        evidence, lambda spec: FakeAdapter(model_id=spec.model, responses=script)
    )
    judged = [
        judge_artifact(run, goldenset, panel, evidence=evidence, out_dir=root)
        for run in runs
    ]
    compare(
        judged[0],
        judged[1],
        thresholds=config.thresholds,
        evidence=evidence,
        baseline_run=runs[0],
        candidate_run=runs[1],
        goldenset_path=str(goldenset_path),
        config_path=str(root / "judges.toml"),
        config_hash=hash_file(root / "judges.toml"),
    )
    return root / "evidence.jsonl"


def _model(evidence: Path, goldenset_path: Path, **kwargs: Any) -> ReportModel:
    return ReportModel.from_evidence(
        evidence,
        goldenset=str(goldenset_path),
        max_output_chars=MAX_OUTPUT_CHARS,
        now="2026-01-01T00:00:00Z",
        **kwargs,
    )


@pytest.fixture(scope="module")
def flips_only(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """30 items, every one of which stops working. The size-law fixture."""
    root = tmp_path_factory.mktemp("report-scale")
    goldenset_path = root / "goldenset.jsonl"
    _write_goldenset(goldenset_path, ITEMS, answer_chars=MAX_OUTPUT_CHARS)
    goldenset = GoldenSet.load(goldenset_path)
    evidence = _run_pipeline(
        root,
        goldenset_path,
        _responses(goldenset, wrong=False),
        _responses(goldenset, wrong=True),
        n=N_PER_ITEM,
    )
    return {"root": root, "evidence": evidence, "goldenset": goldenset_path}


@pytest.fixture(scope="module")
def rendered(flips_only: dict[str, Any]) -> dict[str, Any]:
    """The uncapped render: the default budget, which 30 items at n=5 sits under."""
    model = _model(flips_only["evidence"], flips_only["goldenset"])
    html_path = render_html(
        model, flips_only["root"] / "report.html", now="2026-01-01T00:00:00Z"
    )
    return {
        "model": model,
        "path": html_path,
        "html": html_path.read_text(encoding="utf-8"),
        "bytes": html_path.stat().st_size,
        "costs": _costs(model),
    }


@pytest.fixture(scope="module")
def capped(flips_only: dict[str, Any], rendered: dict[str, Any]) -> dict[str, Any]:
    """The same evidence at a budget that admits exactly seven rows.

    The budget is the measured cost of the first seven rows: under the stated rule
    the seventh fits exactly and the eighth cannot, which is the boundary worth
    testing rather than a round number somewhere in the middle of a row.
    """
    costs = rendered["costs"]["flips"]
    budget = sum(costs[:7])
    model = _model(
        flips_only["evidence"], flips_only["goldenset"], max_report_chars=budget
    )
    html_path = render_html(
        model, flips_only["root"] / "capped.html", now="2026-01-01T00:00:00Z"
    )
    return {
        "model": model,
        "budget": budget,
        "path": html_path,
        "html": html_path.read_text(encoding="utf-8"),
        "bytes": html_path.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# 1. the uncapped region: the size law, unchanged
# --------------------------------------------------------------------------- #


def test_every_changed_item_becomes_a_row_and_no_row_is_dropped(
    rendered: dict[str, Any]
) -> None:
    """The row count is the changed-item count, and the bound does not touch it.

    This is the first factor of the size law. A report that showed the worst
    twenty and said "and 980 more" would be a different document; it shows all of
    them, capped or not, and this is where that stops being true if it ever does.
    """
    model = rendered["model"]
    changed = len(model.flips) + len(model.gains) + len(model.unstable)
    assert changed == ITEMS, (
        f"expected all {ITEMS} items to change state and appear as rows, got "
        f"{changed} ({len(model.flips)} flips, {len(model.gains)} gains, "
        f"{len(model.unstable)} unstable)"
    )
    assert rendered["html"].count("<details>") == ITEMS


def test_every_row_carries_all_n_draws_from_both_sides(rendered: dict[str, Any]) -> None:
    """The second factor: 2n output blocks per row, plus the input.

    Keeping every draw is deliberate and correct -- printing one of five would
    hide the distribution the whole tool is built to show. It is also what makes
    the document grow with n as well as with the golden set, which is what the
    budget below accounts for and nothing used to.
    """
    model = rendered["model"]
    for row in model.flips:
        assert row.detail_embedded, row.item_id
        assert len(row.baseline_outputs) == N_PER_ITEM, row.item_id
        assert len(row.candidate_outputs) == N_PER_ITEM, row.item_id
    blocks = rendered["html"].count('<pre class="output">')
    expected = ITEMS * (2 * N_PER_ITEM + 1)  # both sides' draws, plus the input
    assert blocks == expected, (
        f"expected {expected} output blocks ({ITEMS} rows x (2 x {N_PER_ITEM} draws "
        f"+ 1 input)), got {blocks}"
    )


def test_the_document_grows_as_rows_times_draws_times_the_cut(
    rendered: dict[str, Any]
) -> None:
    """The size law itself, asserted as a floor rather than an estimate.

    A floor, because markup and the rest of the report only add: the point is
    that the embedded text alone already accounts for the file. At the sizes in
    this module's docstring that lower bound is 8 MB, 32 MB and 161 MB, and it is
    still exactly what an under-budget report does.
    """
    # Draws only. The input block is a (2n + 1)-th block per row and is cut to the
    # same limit, so it can only add; leaving it out keeps this a true floor for a
    # golden set whose inputs are short.
    embedded_floor = ITEMS * 2 * N_PER_ITEM * MAX_OUTPUT_CHARS
    assert rendered["bytes"] >= embedded_floor, (
        f"{rendered['path']} is {rendered['bytes']} bytes, under the "
        f"{embedded_floor} bytes its own embedded outputs should account for"
    )
    assert rendered["bytes"] > 1_000_000, (
        f"{ITEMS} items at n={N_PER_ITEM} rendered {rendered['bytes']} bytes; this "
        f"test exists because that number is over a megabyte from a golden set "
        f"smaller than the one the methodology asks for"
    )
    assert rendered["model"].detail.embedded >= embedded_floor


def test_the_truncation_that_is_announced_is_the_per_block_one(
    rendered: dict[str, Any]
) -> None:
    """Per-block cutting is marked on every row. That part was always honest."""
    model = rendered["model"]
    assert all(row.truncated for row in model.flips)
    assert rendered["html"].count("truncated at") == ITEMS


def test_an_unbounded_report_says_that_it_is_unbounded(rendered: dict[str, Any]) -> None:
    """Silence is not a disclosure. A complete document states that it is complete.

    A reader who only ever sees a warning when something was left out has to know
    that the warning exists in order to trust its absence, and the reader of a
    change-control document is by construction someone who has not read this
    source file.
    """
    model = rendered["model"]
    detail = model.detail
    assert detail.limit == DEFAULT_MAX_REPORT_CHARS
    assert detail.capped is False
    assert detail.rows == ITEMS
    assert detail.rows_embedded == ITEMS
    assert detail.embedded <= detail.limit
    assert detail.sentence not in model.warnings
    assert model.warnings == ()
    assert "carries its full outputs" in detail.sentence
    assert 'id="detail-budget"' in rendered["html"]
    assert detail.sentence in rendered["html"]


# --------------------------------------------------------------------------- #
# 2. the capped region: what is bounded, and what is not
# --------------------------------------------------------------------------- #


def test_the_budget_embeds_the_rows_the_stated_rule_says_it_embeds(
    capped: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Seven rows in, twenty-three summarised, and the count comes from the rule.

    ``_expected`` re-implements the allocation from its prose. If the two disagree
    the implementation has stopped matching its own documentation, which is the
    failure this comparison exists to catch -- an assertion on ``rows_embedded ==
    7`` alone would pass just as happily on a rule that took the seven cheapest.
    """
    model = capped["model"]
    expected = _expected(rendered["costs"], capped["budget"])
    assert expected["flips"] == 7, (
        f"the fixture's budget was built to admit exactly seven rows; the rule "
        f"says {expected['flips']}"
    )
    assert _embedded(model) == expected
    assert model.detail.rows_embedded == 7
    assert model.detail.rows == ITEMS
    assert model.detail.embedded <= capped["budget"], (
        f"embedded {model.detail.embedded} characters against a budget of "
        f"{capped['budget']}"
    )


def test_the_budget_bounds_the_document_it_produces(capped: dict[str, Any]) -> None:
    """The rendered file, not just the accounting, comes down.

    The budget counts quoted characters; the file adds markup, the statistics
    tables and the methodology appendix, which are a fixed cost. Asserting the
    file against the budget plus that fixed cost is the claim a reader cares about
    -- and asserting it is *much* smaller than the uncapped render is the claim the
    finding was about.
    """
    assert capped["bytes"] < capped["budget"] * 2, (
        f"{capped['path']} is {capped['bytes']} bytes against a "
        f"{capped['budget']}-character budget; the markup around the quoted text "
        f"should be a fixed cost, not a multiplier"
    )
    assert capped["bytes"] < 500_000


def test_no_row_is_dropped_when_the_budget_binds(
    capped: dict[str, Any], rendered: dict[str, Any]
) -> None:
    """Every changed item is still in the document, with its id and its margin.

    This is the whole design decision. Dropping rows to fit a byte budget would
    remove findings -- and would remove them in golden-set order, which is
    unrelated to how bad they are -- where dropping quotations removes only the
    illustration of a finding that is still named. The item ids and the margins
    come from the comparison payload and cost a few hundred bytes each.
    """
    model = capped["model"]
    assert [row.item_id for row in model.flips] == [
        row.item_id for row in rendered["model"].flips
    ]
    assert len(model.flips) == ITEMS
    assert capped["html"].count("<details>") == ITEMS
    for row in model.flips:
        assert row.item_id in capped["html"]
        assert row.judges == ("accuracy",)
        assert row.labels, f"{row.item_id} lost the margin that makes it readable"
    # And the counts the verdict rests on are untouched by a rendering decision.
    assert model.item_counts == rendered["model"].item_counts
    assert [judge.name for judge in model.judges] == [
        judge.name for judge in rendered["model"].judges
    ]
    assert model.verdict == rendered["model"].verdict


def test_a_summarised_row_carries_no_quotations_at_all(capped: dict[str, Any]) -> None:
    """A row past the budget has no input, no draws and no judge reasons.

    All of them, because all of them are attacker-influenced free text of
    unbounded length; a bound that stopped at the draws and let the reasons
    through would be a bound on four fifths of the problem.
    """
    model = capped["model"]
    summarised = [row for row in model.flips if not row.detail_embedded]
    assert len(summarised) == ITEMS - 7
    for row in summarised:
        assert row.input is None
        assert row.baseline_outputs == ()
        assert row.candidate_outputs == ()
        assert row.reasons == {}
        assert row.quoted_chars == 0
        assert row.truncated is False
    blocks = capped["html"].count('<pre class="output">')
    assert blocks == 7 * (2 * N_PER_ITEM + 1), (
        f"expected quoted blocks for seven rows only, got {blocks}"
    )


def test_the_bound_is_disclosed_in_the_model_the_page_and_the_terminal(
    capped: dict[str, Any]
) -> None:
    """One sentence, three surfaces, and the same words in all of them.

    A truncated report that does not say it was truncated is worse than a large
    one. So the disclosure is in ``warnings`` where a library caller reads it, in
    a band above the change sections where a reviewer opening the file reads it,
    and in the terminal where CI reads it -- generated from one property so that
    three copies cannot drift.
    """
    model = capped["model"]
    sentence = model.detail.sentence
    assert model.detail.capped is True
    assert sentence in model.warnings
    assert "No row was dropped." in sentence
    assert "max_report_chars" in sentence
    assert f"{model.detail.rows_embedded} of {ITEMS} changed item(s)" in sentence

    assert 'class="note" id="detail-budget"' in capped["html"]
    assert sentence in capped["html"]
    assert "Outputs not embedded" in capped["html"]

    console = Console(file=__import__("io").StringIO(), width=200, no_color=True)
    render_terminal(model, console=console)
    printed = console.file.getvalue()
    assert "budget for quoted model text" in printed
    # Said once. The warnings loop skips the sentence it already printed beside
    # the change tables, because a terminal that repeats itself teaches the reader
    # to skim the repeat.
    assert printed.count("No row was dropped.") == 1


def test_the_capped_document_still_passes_every_guard_the_project_owns(
    capped: dict[str, Any]
) -> None:
    """Self-contained, well-formed, attested -- and now also openable.

    The uncapped 161.8 MB document passed all three of these, which was the
    finding. Passing them is necessary and was never sufficient; the size is now
    a fact the document states rather than one nothing measures.
    """
    assert external_urls(capped["html"]) == ()
    assert capped["html"].lstrip().lower().startswith("<!doctype html>")
    assert re.search(r"VERDICT|NO-GO|verdict", capped["html"]) is not None


def test_the_render_is_still_deterministic_under_the_bound(
    capped: dict[str, Any], flips_only: dict[str, Any]
) -> None:
    """Same evidence, same budget, same bytes. A cap that shuffled would be worse
    than no cap: two reviewers would be reading different documents."""
    again = _model(
        flips_only["evidence"], flips_only["goldenset"], max_report_chars=capped["budget"]
    )
    assert render_html_string(again, now="2026-01-01T00:00:00Z") == render_html_string(
        capped["model"], now="2026-01-01T00:00:00Z"
    )


# --------------------------------------------------------------------------- #
# 3. the two properties of the rule that a row count cannot show
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def staircase(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Four flipping items whose answers get shorter, so the rows do too.

    Needed because every row in the main fixture costs the same, and a rule that
    embedded whichever rows happened to fit would be indistinguishable from the
    documented one on rows of equal size.
    """
    root = tmp_path_factory.mktemp("report-staircase")
    goldenset_path = root / "goldenset.jsonl"
    with open(goldenset_path, "w", encoding="utf-8", newline="\n") as handle:
        for index, chars in enumerate((MAX_OUTPUT_CHARS, MAX_OUTPUT_CHARS, MAX_OUTPUT_CHARS, 40)):
            handle.write(
                json.dumps(
                    {
                        "id": f"item-{index:04d}",
                        "input": f"[stair] canonical answer for case {index}?",
                        "reference": f"answer-{index} " + "w" * chars,
                        "tags": ["scale"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    goldenset = GoldenSet.load(goldenset_path)
    evidence = _run_pipeline(
        root,
        goldenset_path,
        _responses(goldenset, wrong=False),
        _responses(goldenset, wrong=True),
        n=N_PER_ITEM,
    )
    return {"root": root, "evidence": evidence, "goldenset": goldenset_path}


def test_the_first_row_that_does_not_fit_stops_the_rest_rather_than_being_skipped(
    staircase: dict[str, Any]
) -> None:
    """A cheap row after an expensive one is *not* embedded. Deliberately.

    Best-fit would embed it, and would produce a report whose quoted evidence is
    whichever items happened to have short answers. A reviewer could not say what
    the document contains without knowing the length distribution of every model's
    output, and the shortest answers are not the interesting ones. First-fit-then-
    stop gives a rule a reader can state: the first N rows in document order.
    """
    full = _model(staircase["evidence"], staircase["goldenset"])
    costs = _costs(full)["flips"]
    assert costs[3] < costs[2], (
        f"the fixture is meant to end with a cheap row; costs are {costs}"
    )
    # Enough for the first two rows, not the third -- and the fourth would fit in
    # what is left over, which is exactly the temptation being refused.
    budget = costs[0] + costs[1] + costs[3]
    assert budget < costs[0] + costs[1] + costs[2]

    model = _model(
        staircase["evidence"], staircase["goldenset"], max_report_chars=budget
    )
    assert [row.detail_embedded for row in model.flips] == [True, True, False, False]
    assert _embedded(model) == _expected(_costs(full), budget)
    assert model.detail.embedded == costs[0] + costs[1]


@pytest.fixture(scope="module")
def mixed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Nine items: three that flip, three that gain, three unstable on both sides.

    Built because the round-robin rule is invisible on a document with one
    populated section, and "flips crowd out gains" is precisely the failure the
    rule exists to prevent -- the same failure the report's own prose calls out
    when it refuses to net gains against flips.
    """
    root = tmp_path_factory.mktemp("report-mixed")
    goldenset_path = root / "goldenset.jsonl"
    kinds = ["flip"] * 3 + ["gain"] * 3 + ["swing"] * 3
    with open(goldenset_path, "w", encoding="utf-8", newline="\n") as handle:
        for index, kind in enumerate(kinds):
            handle.write(
                json.dumps(
                    {
                        "id": f"item-{index:04d}",
                        "input": f"[{kind}] canonical answer for case {index}?",
                        "reference": f"answer-{index} " + "w" * MAX_OUTPUT_CHARS,
                        "tags": [kind],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    goldenset = GoldenSet.load(goldenset_path)
    evidence = _run_pipeline(
        root,
        goldenset_path,
        _mixed_script(goldenset, side="baseline"),
        _mixed_script(goldenset, side="candidate"),
        n=N_PER_ITEM,
    )
    return {"root": root, "evidence": evidence, "goldenset": goldenset_path}


def _mixed_script(goldenset: GoldenSet, *, side: str):
    """A scripted model that flips, gains or wobbles depending on the item.

    Deterministic without an RNG: ``run_goldenset`` samples one item at a time and
    this fixture leaves ``concurrency`` at its default of 1, so the per-prompt
    draw counter below is called in a fixed order. A test in a project about
    statistical gates does not get to be flaky.
    """
    references = {item.input: item.reference or "" for item in goldenset}
    counters: dict[str, int] = {}

    def respond(prompt: str) -> str:
        reference = references[prompt]
        index = counters.get(prompt, 0)
        counters[prompt] = index + 1
        wrong = "z" * len(reference)
        if prompt.startswith("[flip]"):
            return reference if side == "baseline" else wrong
        if prompt.startswith("[gain]"):
            return wrong if side == "baseline" else reference
        # Three of five draws right, on both sides: neither 80% passing nor 20%
        # failing, which is the definition of unstable the report prints.
        return reference if index % 2 == 0 else wrong

    return respond


def test_the_fixture_populates_all_three_change_sections(mixed: dict[str, Any]) -> None:
    """Guards the two tests below: they say nothing if a section came out empty."""
    model = _model(mixed["evidence"], mixed["goldenset"])
    assert len(model.flips) == 3
    assert len(model.gains) == 3
    assert len(model.unstable) == 3


def test_no_section_can_crowd_out_another_when_the_budget_binds(
    mixed: dict[str, Any]
) -> None:
    """Flips do not spend the budget before gains and unstable see any of it.

    A rule that filled flips first would produce a document illustrating only the
    items that got worse, from a tool whose own report says gains are shown
    "because their absence would make this report an argument rather than a
    measurement". Round-robin is how that sentence stays true under a budget.
    """
    full = _model(mixed["evidence"], mixed["goldenset"])
    costs = _costs(full)
    # Six rows' worth, which under round-robin is two rounds: two from each
    # section rather than six flips and nothing else.
    budget = sum(sorted(costs["flips"] + costs["gains"] + costs["unstable"])[:6])
    model = _model(mixed["evidence"], mixed["goldenset"], max_report_chars=budget)

    embedded = _embedded(model)
    assert embedded == _expected(costs, budget)
    assert min(embedded.values()) >= 1, (
        f"a section was starved: {embedded}. The budget was two rounds' worth."
    )
    assert model.detail.capped is True
    assert sum(len(getattr(model, name)) for name in _SECTIONS) == 9


def test_the_budget_is_a_document_budget_and_not_a_per_section_one(
    mixed: dict[str, Any]
) -> None:
    """Three sections share one allowance; they do not each get one.

    Worth pinning because the obvious implementation -- call the row builder once
    per section, each with the budget -- would pass every other test here and
    render three times the intended document.
    """
    full = _model(mixed["evidence"], mixed["goldenset"])
    costs = _costs(full)
    budget = costs["flips"][0] + costs["gains"][0] + costs["unstable"][0]
    model = _model(mixed["evidence"], mixed["goldenset"], max_report_chars=budget)
    assert model.detail.rows_embedded == 3, (
        f"one row from each section should exhaust a budget of exactly three rows; "
        f"got {model.detail.rows_embedded}"
    )
    assert model.detail.embedded <= budget
    assert _embedded(model) == {"flips": 1, "gains": 1, "unstable": 1}


def test_a_budget_of_zero_or_less_means_no_bound(mixed: dict[str, Any]) -> None:
    """Matching ``_truncate``'s convention for ``max_output_chars``.

    One convention for "no limit" across both knobs, so a caller does not have to
    remember which of them treats 0 as "embed nothing" -- which is the reading
    that would silently produce an evidence-free report.
    """
    model = _model(mixed["evidence"], mixed["goldenset"], max_report_chars=0)
    assert model.detail.capped is False
    assert model.detail.rows_embedded == 9
    assert all(row.detail_embedded for row in model.flips)


# --------------------------------------------------------------------------- #
# 4. the settings that reach it
# --------------------------------------------------------------------------- #


def test_the_report_section_takes_the_bound_and_the_cli_validates_it(
    tmp_path: Path,
) -> None:
    """``[report]`` accepts exactly the two knobs, and refuses a nonsense bound.

    The previous version of this test asserted that ``_REPORT_KEYS`` held
    ``max_output_chars`` alone and said, in as many words, that the commit adding a
    document-level bound should replace it with an assertion that the bound
    exists. This is that assertion.
    """
    from model_migration_kit import cli

    assert frozenset({"max_output_chars", "max_report_chars"}) == cli._REPORT_KEYS
    assert cli.Settings().max_report_chars == DEFAULT_MAX_REPORT_CHARS

    config = tmp_path / "migkit.toml"
    config.write_text(
        "[report]\nmax_output_chars = 500\nmax_report_chars = 123456\n", encoding="utf-8"
    )
    settings = cli.load_settings(config)
    assert settings.max_report_chars == 123456
    assert settings.source("max_report_chars") == str(config)

    config.write_text("[report]\nmax_report_chars = 0\n", encoding="utf-8")
    with pytest.raises(cli.ConfigError, match="max_report_chars"):
        cli.load_settings(config)
