"""What the HTML report does when the golden set stops being twelve items.

Every measurement this project publishes comes from the bundled demo: 12 items at
n=5, 60 completions per side, a 25,760-byte report. Its own methodology says the
statistically adequate sample is roughly 200 completions per side and up to 229 at
a 0.80 baseline rate, so the configuration users are told to run has never been
rendered. These tests render it, and the sizes above it, and pin what happens.

The finding they exist to hold in place is a *size law*, not a bug in any one
function::

    html_bytes  >=  changed_items  x  2n  x  max_output_chars

Every changed item becomes one ``<details>`` block carrying **all n draws from
both sides** plus the item input, each block cut to ``max_output_chars`` (4000 by
default) and marked as cut. Nothing bounds the number of blocks, nothing bounds
the number of draws inside one block, and nothing looks at the total. The
per-block truncation is announced; the document-level consequence is not.

Measured on this law, with 4000-character outputs and every item changed:

===================  ==========  ================
golden set           draws (n)   rendered report
===================  ==========  ================
40 items             5           1.65 MB
200 items            5           8.18 MB
200 items            20          32.4 MB
1000 items           20          161.8 MB
===================  ==========  ================

The 161.8 MB document was produced in 247 s of pipeline, is self-contained,
carries all 1000 truncation notices, and returns ``external_urls() == ()``. It
holds 41,000 ``<pre>`` blocks in one file. Every guard this project owns passes on
it. That is the shape of failure the project says it cares most about: not a
crash, but an artifact that is generated, valid, attested, and unopenable.

These tests therefore assert two things:

1. the size law holds, so a future change that bounds the document breaks them
   loudly rather than quietly changing what the report contains; and
2. no bound exists today -- no row cap, no byte cap, no warning in the document
   and none in the model. **If you are adding one, this file is the test to
   update, and updating it is the point.** It is written to fail on the commit
   that fixes the problem, so that the fix has to say so.

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

from model_migration_kit.comparison import compare
from model_migration_kit.contracts import hash_file
from model_migration_kit.demo import judge_script
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, judge_artifact
from model_migration_kit.report import ReportModel, external_urls, render_html
from model_migration_kit.runner import run_goldenset

#: The report's default per-block cut, and the only size control the tool has.
#: ``cli.Settings`` exposes ``max_output_chars`` and nothing else; there is no
#: ``max_rows`` and no ``max_report_bytes``.
MAX_OUTPUT_CHARS = 4000

#: Small enough to render in about two seconds, large enough that the law is
#: visible: 30 items x (2*5 + 1) blocks x 4000 chars is already over a megabyte.
ITEMS = 30
N_PER_ITEM = 5

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


def _write_goldenset(path: Path, items: int, *, answer_chars: int) -> None:
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
                "input": f"What is the canonical answer for case {index}?",
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


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One full pipeline run, keyless, through the production path.

    ``FakeAdapter`` at the provider seam and the demo's own judge script -- the
    same substitution ``migkit demo`` makes -- and everything below it is the code
    a paying run executes: ``run_goldenset``, ``judge_artifact``, ``compare``, and
    a report rebuilt from the evidence log on disk.
    """
    root = tmp_path_factory.mktemp("report-scale")
    goldenset_path = root / "goldenset.jsonl"
    _write_goldenset(goldenset_path, ITEMS, answer_chars=MAX_OUTPUT_CHARS)
    (root / "rubric.md").write_text(RUBRIC, encoding="utf-8")
    (root / "judges.toml").write_text(JUDGES_TOML, encoding="utf-8")

    goldenset = GoldenSet.load(goldenset_path)
    evidence = EvidenceLog(root / "evidence.jsonl")
    runs = [
        run_goldenset(
            goldenset,
            FakeAdapter(
                model_id=f"fake-{side}-v1",
                responses=_responses(goldenset, wrong=side == "candidate"),
            ),
            out_dir=root,
            n=N_PER_ITEM,
            evidence=evidence,
        )
        for side in ("baseline", "candidate")
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

    model = ReportModel.from_evidence(
        root / "evidence.jsonl",
        goldenset=str(goldenset_path),
        max_output_chars=MAX_OUTPUT_CHARS,
        now="2026-01-01T00:00:00Z",
    )
    html_path = render_html(model, root / "report.html", now="2026-01-01T00:00:00Z")
    return {
        "model": model,
        "path": html_path,
        "html": html_path.read_text(encoding="utf-8"),
        "bytes": html_path.stat().st_size,
    }


def test_every_changed_item_becomes_a_row_and_no_row_is_dropped(
    rendered: dict[str, Any]
) -> None:
    """The row count is the changed-item count, uncapped.

    This is the first factor of the size law. A report that showed the worst
    twenty and said "and 980 more" would be a different document; today it shows
    all of them, and this is where that stops being true if it ever does.
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
    the document grow with n as well as with the golden set, which is the part
    nothing downstream accounts for.
    """
    model = rendered["model"]
    for row in model.flips:
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
    this module's docstring that lower bound is 8 MB, 32 MB and 161 MB.
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


def test_the_truncation_that_is_announced_is_the_per_block_one(
    rendered: dict[str, Any]
) -> None:
    """Per-block cutting is marked on every row. That part is honest.

    It is recorded here so that the next test's claim is precise: the tool does
    tell you it shortened each quotation, and does not tell you what the document
    as a whole became.
    """
    model = rendered["model"]
    assert all(row.truncated for row in model.flips)
    assert rendered["html"].count("truncated at") == ITEMS


def test_nothing_bounds_or_mentions_the_size_of_the_document(
    rendered: dict[str, Any]
) -> None:
    """No cap, no warning, no note -- in the model or in the page.

    **This test is written to fail when the problem is fixed.** If you have added
    a row cap, a byte budget, or a line in the report saying how large it is, this
    is the assertion to rewrite, and rewriting it is the deliberate act the
    finding asks for. Do not delete it: replace it with the assertion that your
    bound holds.
    """
    model = rendered["model"]
    assert model.warnings == (), (
        f"the report carries warnings {model.warnings!r}; if one of them is about "
        f"the size of this document, replace this test with an assertion on it"
    )
    assert model.completeness.missing == ()

    page = rendered["html"].lower()
    size_words = (
        "megabyte",
        " mb ",
        "too large",
        "too big",
        "showing the first",
        "row limit",
        "size limit",
    )
    found = [word for word in size_words if word in page]
    assert not found, (
        f"the rendered report mentions {found}; a document-level size bound now "
        f"exists and this test should assert it rather than its absence"
    )

    # The only knob is per-block. Nothing takes a number of rows or of bytes.
    from model_migration_kit import cli

    assert frozenset({"max_output_chars"}) == cli._REPORT_KEYS, (
        f"[report] now accepts {sorted(cli._REPORT_KEYS)}; if one of those bounds "
        f"the document, this test should assert that it does"
    )


def test_the_oversized_document_passes_every_guard_the_project_owns(
    rendered: dict[str, Any]
) -> None:
    """Self-contained, well-formed, attested -- and that is the whole problem.

    ``render_html`` refuses to write a document that would fetch anything, and
    this one fetches nothing. The size is invisible to every check that exists, so
    a report large enough to be unopenable ships with a clean bill of health.
    """
    assert external_urls(rendered["html"]) == ()
    assert rendered["html"].lstrip().lower().startswith("<!doctype html>")
    assert re.search(r"VERDICT|NO-GO|verdict", rendered["html"]) is not None
