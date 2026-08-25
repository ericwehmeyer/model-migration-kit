"""Audit-only reproduction harness for AUDIT-verdict.md findings V1, V2 and V3.

The second machine's V1-V3 were a subagent's results, landed at a token limit and
never independently re-run. This script is the independent run, and it is written
to the evidence standard those findings claim for themselves: **no payload is
hand-edited anywhere below.** Every ``JudgeRecord`` this produces is written by
``judging._grade`` out of a response a scripted judge emitted, every
``JudgedArtifact`` is re-read from disk, ``comparison.compare`` is the real one,
the ``EvidenceLog`` is rigor's, and the report is rebuilt from that log by
``ReportModel.from_evidence`` -- the same reconstruction ``migkit report`` runs.

The one substitution is at rigor's ``Adapter`` seam, which is where
``model_migration_kit.demo`` already substitutes and the only place a keyless run
is allowed to differ from one that costs money. ``cli._judge_adapter`` refuses
``adapter = "fake"``, so the CLI cannot reach these cases without credentials;
this harness wires the same panel ``demo.run_demo`` wires, through
``JudgeConfig.build(evidence, adapter_for)``.

**What each knob does, so a third party can see there is no thumb on the scale.**
The two models are scripted mappings from prompt to response, exactly as
``demo.build_adapters`` builds them: the baseline answers each item's reference
verbatim, the candidate answers the reference plus a fixed marker phrase. Both
answers are *correct*, so every item passes on both sides in every case here --
which is the premise all three findings rest on. The judge reads the marker to
know which side it is grading and emits the score that case asks for. The
"declined to score" cases emit ``"score": null`` for the first ``null_count``
calls on one side, which is rigor's documented prompt contract (``score`` is
optional, ``pass`` is not) and is the field V2 and V3 are about.

Judging and sampling both run at ``concurrency=1``, so the judge's per-side call
counter is deterministic and the whole harness is reproducible.

Usage::

    python scripts/audit/v1_v3_repro.py --case all --out-dir <dir>
    python scripts/audit/v1_v3_repro.py --case v2-null --out-dir <dir> --keep

``--case`` takes any case name in :data:`CASES` or ``all``. Results print as one
JSON object per case on stdout; ``--keep`` leaves the work directory (evidence
log, artifacts, rendered HTML) behind for inspection instead of using a temporary
one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opik_rigor import EvidenceLog, FakeAdapter

from model_migration_kit.comparison import compare
from model_migration_kit.contracts import hash_file
from model_migration_kit.demo import _block, install_data
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, judge_artifact
from model_migration_kit.report import ReportModel, render_html_string
from model_migration_kit.runner import run_goldenset

#: rigor's own prompt delimiters, borrowed from ``demo`` rather than restated, so
#: a change to rigor's template breaks this harness the same way it breaks the
#: demo instead of silently mis-parsing.
_INPUT_OPEN = "=== INPUT GIVEN TO THE MODEL ==="
_INPUT_CLOSE = "=== END INPUT ==="
_OUTPUT_OPEN = "=== MODEL OUTPUT UNDER EVALUATION ==="
_OUTPUT_CLOSE = "=== END MODEL OUTPUT ==="

#: The candidate's answers carry this and the baseline's do not. It is how the
#: scripted judge tells the two sides apart -- it grades *text*, as the demo's
#: judge does, and never sees a model id.
CANDIDATE_MARK = "In other words, the answer stands as given."

BASELINE_MODEL_ID = "fake-baseline-v1"
CANDIDATE_MODEL_ID = "fake-candidate-v1"


@dataclass(frozen=True)
class Case:
    """One reproduction: a golden-set size, a draw count, and what the judge says.

    ``null_side``/``null_count`` are the only difference between a control and its
    variant in V2 and V3, and they change nothing about the completions: the two
    run artifacts are identical, so the pair isolates the ``score`` field the way
    the finding says it does.
    """

    name: str
    claim: str
    items: int
    n: int
    baseline_score: float
    candidate_score: float
    null_side: str = ""
    null_count: int = 0


CASES: tuple[Case, ...] = (
    Case("v1-n1", "V1 headline, low arm: 12 items x 1 draw", 12, 1, 5.0, 5.0),
    Case("v1-n5", "V1 headline, high arm: 12 items x 5 draws", 12, 5, 5.0, 5.0),
    Case("v1-extreme", "V1 extreme: 1 item x 60 draws", 1, 60, 5.0, 5.0),
    Case("v2-control", "V2 control: candidate scored 2.0 throughout", 20, 5, 5.0, 2.0),
    Case(
        "v2-null",
        "V2 variant: 99 of the candidate's 100 records carry score null",
        20,
        5,
        5.0,
        2.0,
        null_side="candidate",
        null_count=99,
    ),
    Case("v3-control", "V3 control: both sides scored 2.0 throughout", 20, 5, 2.0, 2.0),
    Case(
        "v3-null",
        "V3 variant: 99 of the baseline's 100 records carry score null",
        20,
        5,
        2.0,
        2.0,
        null_side="baseline",
        null_count=99,
    ),
)

BY_NAME = {case.name: case for case in CASES}


def write_goldenset(path: Path, items: int) -> Path:
    """A golden set of ``items`` distinct arithmetic items, in the bundled format.

    Distinct inputs are mandatory and not cosmetic: ``FakeAdapter`` matches on the
    prompt, and ``run_goldenset`` sends the item's input verbatim, so two items
    sharing an input would share one scripted answer.
    """
    lines = []
    for index in range(items):
        first, second = 100 + index, 7 + index
        lines.append(
            json.dumps(
                {
                    "id": f"sum-{index + 1:03d}",
                    "input": f"What is {first} + {second}? Answer with the number only.",
                    "reference": str(first + second),
                    "tags": ["arithmetic"],
                },
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_adapters(goldenset: GoldenSet) -> tuple[FakeAdapter, FakeAdapter]:
    """Two scripted models, both answering every item correctly.

    The candidate appends :data:`CANDIDATE_MARK`. Under the demo's own rubric that
    is a 4 ("correct, with harmless noise") and under this harness's judge it is
    whatever the case asks for -- the point of V2 and V3 is that the *score* moves
    while the pass/fail verdict does not, so the harness has to be able to set it.
    """
    baseline = FakeAdapter(
        model_id=BASELINE_MODEL_ID,
        responses={item.input: str(item.reference) for item in goldenset},
    )
    candidate = FakeAdapter(
        model_id=CANDIDATE_MODEL_ID,
        responses={
            item.input: f"{item.reference} {CANDIDATE_MARK}" for item in goldenset
        },
    )
    return baseline, candidate


def judge_script(case: Case):
    """The judge adapter's callable: read rigor's prompt, emit rigor's JSON.

    Everything passes. The score is the case's, except on the first
    ``null_count`` calls for ``null_side``, which come back as ``"score": null``
    -- the judge declining to give a number while still committing to a verdict.
    """
    seen = {"baseline": 0, "candidate": 0}

    def respond(prompt: str) -> str:
        _block(prompt, _INPUT_OPEN, _INPUT_CLOSE, "input")
        output = _block(prompt, _OUTPUT_OPEN, _OUTPUT_CLOSE, "model output")
        side = "candidate" if CANDIDATE_MARK in output else "baseline"
        seen[side] += 1
        score: float | None = (
            case.baseline_score if side == "baseline" else case.candidate_score
        )
        reason = "the answer matches the reference"
        if side == case.null_side and seen[side] <= case.null_count:
            score = None
            reason = "correct, but the rubric gives me no basis to put a number on it"
        return json.dumps({"pass": True, "score": score, "reason": reason})

    return respond


def run_case(case: Case, work_dir: Path) -> dict[str, Any]:
    """Sample, judge, compare and render one case. Returns what the finding claims."""
    work_dir.mkdir(parents=True, exist_ok=True)
    data = install_data(work_dir)
    config_path = data["demo.toml"]
    goldenset_path = write_goldenset(work_dir / "audit_goldenset.jsonl", case.items)

    loaded = GoldenSet.load(goldenset_path)
    config = JudgeConfig.load(config_path)
    evidence = EvidenceLog(work_dir / "evidence.jsonl")

    runs = []
    for adapter in build_adapters(loaded):
        runs.append(
            run_goldenset(
                loaded,
                adapter,
                out_dir=work_dir,
                n=case.n,
                evidence=evidence,
                concurrency=1,
            )
        )
    baseline_run, candidate_run = runs

    script = judge_script(case)
    panel = config.build(
        evidence, lambda spec: FakeAdapter(model_id=spec.model, responses=script)
    )
    judged = [
        judge_artifact(run, loaded, panel, evidence=evidence, out_dir=work_dir, concurrency=1)
        for run in (baseline_run, candidate_run)
    ]
    baseline_judged, candidate_judged = judged

    report = compare(
        baseline_judged,
        candidate_judged,
        thresholds=config.thresholds,
        evidence=evidence,
        baseline_run=baseline_run,
        candidate_run=candidate_run,
        goldenset_path=str(goldenset_path),
        config_path=str(config_path),
        config_hash=hash_file(config_path),
    )

    model = ReportModel.from_evidence(
        evidence.path, goldenset=goldenset_path, artifact_dir=work_dir
    )
    html = render_html_string(model, now="1970-01-01T00:00:00Z")
    (work_dir / "report.html").write_text(html, encoding="utf-8")

    judge = report.judges[0]
    rendered = model.judges[0] if model.judges else None
    return {
        "case": case.name,
        "claim": case.claim,
        "items": case.items,
        "n_per_item": case.n,
        "verdict": str(report.verdict),
        "rule": report.rule,
        "reason": report.reason,
        "decided_by": report.decided_by,
        "p_value": judge.p_value,
        "holm_threshold": judge.holm_threshold,
        "regressed": judge.regressed,
        "floor_cleared": judge.floor_cleared,
        "mw_powered": judge.mw_powered,
        "n_observed": judge.power.n_observed,
        "n_required": judge.power.n_required,
        "test_ran": judge.test_ran,
        "baseline_pass_rate": judge.baseline.get("pass_rate"),
        "candidate_pass_rate": judge.candidate.get("pass_rate"),
        "baseline_lower": judge.baseline.get("lower_bound"),
        "candidate_lower": judge.candidate.get("lower_bound"),
        "item_counts_baseline": dict(judge.item_counts_baseline),
        "item_counts_candidate": dict(judge.item_counts_candidate),
        "flips": len(report.flips),
        "gains": len(report.gains),
        "unstable": len(report.unstable),
        "imputed_row": [judge.imputed_baseline, judge.imputed_candidate],
        "missing_scores_row": [
            judge.missing_scores_baseline,
            judge.missing_scores_candidate,
        ],
        "rendered_imputed_row": (
            [rendered.imputed_baseline, rendered.imputed_candidate] if rendered else None
        ),
        "warnings": list(report.warnings),
        "work_dir": str(work_dir),
    }


def sweep_cases(shape: str, counts: Sequence[int]) -> list[Case]:
    """The V2 or V3 shape at each of ``counts`` unscored records on the silent side.

    The findings quote one point each -- 99 of 100 -- and a single point cannot
    say whether the route needs a judge that has gone almost entirely silent or
    only one that has gone somewhat quiet. That is the difference between a defect
    a reader would dismiss and one they would not, so it is measured rather than
    assumed.
    """
    template = BY_NAME[f"{shape}-null"]
    return [
        Case(
            name=f"{shape}-null-{count:03d}",
            claim=(
                f"{shape.upper()} shape with {count} of 100 records "
                f"unscored on the {template.null_side}"
            ),
            items=template.items,
            n=template.n,
            baseline_score=template.baseline_score,
            candidate_score=template.candidate_score,
            null_side=template.null_side,
            null_count=count,
        )
        for count in counts
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        default="all",
        help=f"one of: all, {', '.join(BY_NAME)}",
    )
    parser.add_argument(
        "--sweep",
        choices=("v2", "v3"),
        default=None,
        help="instead of --case, run that finding's shape at each --null-counts value",
    )
    parser.add_argument(
        "--null-counts",
        default="0,10,25,40,50,60,70,80,90,95,99,100",
        help="comma-separated unscored-record counts for --sweep",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="where to leave the work directories (default: a temporary directory)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="do not delete a temporary work directory when finished",
    )
    args = parser.parse_args(argv)

    if args.sweep:
        counts = [int(one) for one in args.null_counts.split(",") if one.strip()]
        chosen = sweep_cases(args.sweep, counts)
    elif args.case == "all":
        chosen = list(CASES)
    elif args.case in BY_NAME:
        chosen = [BY_NAME[args.case]]
    else:
        parser.error(f"unknown case {args.case!r}; expected all or one of {', '.join(BY_NAME)}")
        return 2

    root = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="mk-audit-"))
    results = []
    try:
        for case in chosen:
            result = run_case(case, root / case.name)
            results.append(result)
            print(json.dumps(result, indent=2, default=str))
    finally:
        if args.out_dir is None and not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
