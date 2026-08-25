"""Evidence logs to render an audit against, built from the test suite's own builders.

**This module imports ``tests/test_report.py`` on purpose, and that dependency is
the point of it.** An audit fixture written from scratch is an audit of a fixture
nobody else uses: if it disagrees with the suite's fixtures, a finding may be an
artifact of the disagreement rather than a defect in the report, and proving
which costs more than the finding is worth. Building on ``_scenario``,
``_write_evidence`` and ``_record`` means every log here is the shape the suite
already asserts against, so a difference in the rendered page is a difference in
the *renderer*.

The cost of that choice, stated plainly: this module breaks if those three
private helpers are renamed. That is a trade the audit accepts, and it is why
:func:`test_report_module` is one function with one import in it -- when it
breaks, it breaks in one place with a message saying so.

Two fixtures are provided.

``standard_scenario``
    The suite's own single-run scenario: 12 items x 5 draws a side, one judge,
    two flips and one gain. Everything a one-comparison report reads.

``candidate_field_log``
    Ten comparisons in one log, which the single-run fixture cannot exercise at
    all: three genuinely comparable candidates, three runs that **must** be
    excluded from the candidate table (a foreign golden-set hash, a foreign
    judges hash, an empty model id), three absences of different kinds (no pass
    rate, no floor anywhere, no adapter), and a newest run that supersedes an
    earlier run of the same candidate. The exclusions and the absences are there
    because the rule under audit -- *an absence must not render as a
    measurement* -- has nothing to bite on in a log where every field is present.

**Every statistic in these payloads is deliberately inconsistent with what a
recomputation would give** (``ODD_SUCCESSES=17`` over ``ODD_N=20`` is exactly
0.85, while the recorded ``pass_rate`` is 0.4242). That is inherited from the
suite and it is load-bearing: it makes "the report never recomputes a statistic"
testable rather than assumed. If 0.85 shows up on a rendered page, something
recomputed.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import ModuleType

from model_migration_kit.contracts import EVENT_COMPARISON, EVENT_VERDICT, Verdict

REPO = Path(__file__).resolve().parents[2]

_MODULE: ModuleType | None = None


def test_report_module() -> ModuleType:
    """Import ``tests/test_report.py`` and hand back the module.

    Not a top-level import: the tests directory is not a package and is not on
    the path of an installed checkout, so it has to be put there first. Cached,
    because the module builds a fair amount at import time.
    """
    global _MODULE
    if _MODULE is None:
        tests = str(REPO / "tests")
        if tests not in sys.path:
            sys.path.insert(0, tests)
        import test_report

        _MODULE = test_report
    return _MODULE


def standard_scenario(root: Path, **kwargs):
    """The suite's standard single-run scenario, written under ``root``.

    Keyword arguments are passed straight through to ``_scenario``: notably
    ``baseline_adapter``, ``candidate_adapter``, ``verdict``, ``hostile``,
    ``with_verdict``, ``flips`` and ``gains``.
    """
    root.mkdir(parents=True, exist_ok=True)
    return test_report_module()._scenario(root, **kwargs)


def write_evidence(path: Path, records) -> Path:
    """Write records as an evidence log, with the timestamps we chose.

    Goes through the suite's ``_write_evidence`` rather than ``EvidenceLog``
    because ``append`` stamps wall-clock time, and the completeness strip names
    the last timestamp in the log -- a fixture that cannot choose its timestamps
    cannot assert on that strip.
    """
    return test_report_module()._write_evidence(path, records)


def record(event_type: str, payload, ts: str) -> dict:
    """One evidence record in rigor's on-disk shape."""
    return test_report_module()._record(event_type, payload, ts)


def _run(
    root: Path,
    sub: str,
    *,
    candidate: str,
    created: str,
    verdict: str,
    rate: float | None,
    lower: float | None,
    interval: tuple[float, float] | None,
    floor: float = 0.87,
    judges_hash: str | None = None,
    goldenset_hash: str | None = None,
    n_per_item: int = 5,
    baseline: str = "model-a-20260101",
    cand_adapter: str = "OpenAICompatAdapter",
    base_adapter: str = "AnthropicAdapter",
    min_rate: object = "keep",
    p_value: float | None = 0.012345,
    regressed: bool = True,
    floor_cleared: bool = False,
    underpowered: bool = False,
    items: int = 12,
    drop_adapter: bool = False,
    drop_floor_everywhere: bool = False,
) -> tuple[dict, dict]:
    """One comparison/verdict pair for the candidate field, with one thing varied.

    The knobs exist so the log can be a *varied* fixture rather than ten copies
    of one run. This project's standing rule is that a fixture where the broken
    and the correct implementation agree tests nothing, and that varying every
    field individually still leaves a monoculture in combinations -- so these are
    meant to be combined, not used one at a time.
    """
    module = test_report_module()
    scenario = module._scenario(
        root / sub,
        baseline_adapter=base_adapter,
        candidate_adapter=cand_adapter,
        verdict=verdict,
    )
    comparison = copy.deepcopy(scenario.comparison)
    decision = copy.deepcopy(scenario.verdict)

    comparison["created"] = created
    comparison["baseline"]["model_id"] = baseline
    comparison["candidate"]["model_id"] = candidate
    if drop_adapter:
        comparison["candidate"]["adapter"] = ""
        comparison["candidate"]["adapters"] = []
    if judges_hash:
        comparison["judges_hash"] = judges_hash
    if goldenset_hash:
        comparison["goldenset_hash"] = goldenset_hash
    comparison["n_per_item"] = n_per_item

    judge = comparison["judges"][0]
    gate = judge["candidate"]
    gate["pass_rate"] = rate
    gate["lower_bound"] = lower
    if interval is None:
        gate["interval_lower"] = None
        gate["interval_upper"] = None
    else:
        gate["interval_lower"], gate["interval_upper"] = interval
    gate["min_rate"] = floor if min_rate == "keep" else min_rate
    judge["baseline"]["min_rate"] = floor if min_rate == "keep" else min_rate

    if drop_floor_everywhere:
        comparison["thresholds"].pop("pass_rate_floor", None)
    else:
        comparison["thresholds"]["pass_rate_floor"] = floor

    judge["regressed"] = regressed
    judge["floor_cleared"] = floor_cleared
    judge["underpowered"] = underpowered
    judge["p_value"] = p_value
    if p_value is None:
        judge["regression"] = None
    judge["item_counts"]["items"] = items

    decision["verdict"] = verdict
    decision["exit_code"] = Verdict.exit_code(verdict)
    decision["baseline_model"] = baseline
    decision["candidate_model"] = candidate
    decision["judges"] = [{"name": "accuracy", "regressed": regressed}]
    return comparison, decision


def candidate_field_log(root: Path) -> Path:
    """Write the ten-comparison candidate-field log and return the evidence path.

    Layout of the field, and why each run is in it:

    ==================== =========================================================
    ``r1`` ``r2`` ``r3`` comparable: same golden set, judges, depth and baseline,
                         one NO-GO / one GO / one REVIEW-and-underpowered
    ``x1``               foreign golden-set hash -- must be excluded
    ``x2``               foreign judges hash -- must be excluded
    ``x3``               empty candidate model id -- must be excluded
    ``n1``               no pass rate, no bounds, no p-value: a comparison that
                         could not be made
    ``n2``               no floor at all, anywhere in the payload
    ``n3``               no adapter recorded on the candidate
    ``r4``               newest, and the same candidate as ``r1``: supersedes it
    ==================== =========================================================

    The three ``x`` runs all carry the *best* pass rates in the log (0.97-0.99),
    deliberately. A renderer that forgets to exclude them does not merely show an
    extra row; it shows a winner.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    def add(pair, ts):
        comparison, decision = pair
        records.append(record(EVENT_COMPARISON, comparison, ts))
        if decision is not None:
            records.append(record(EVENT_VERDICT, decision, ts))

    add(
        _run(root, "r1", candidate="cand-alpha-20260501",
             created="2026-08-01T10:00:00+00:00", verdict=Verdict.NO_GO,
             rate=0.7200, lower=0.6100, interval=(0.6000, 0.8100)),
        "2026-08-01T10:00:01+00:00",
    )
    add(
        _run(root, "r2", candidate="cand-beta-20260601",
             created="2026-08-08T10:00:00+00:00", verdict=Verdict.GO,
             rate=0.9450, lower=0.8901, interval=(0.8800, 0.9800),
             regressed=False, floor_cleared=True, p_value=0.4400),
        "2026-08-08T10:00:01+00:00",
    )
    add(
        _run(root, "r3", candidate="cand-gamma-20260701",
             created="2026-08-15T10:00:00+00:00", verdict=Verdict.REVIEW,
             rate=0.8800, lower=0.8000, interval=(0.7700, 0.9300),
             regressed=False, floor_cleared=False, underpowered=True,
             p_value=0.2100),
        "2026-08-15T10:00:01+00:00",
    )
    add(
        _run(root, "x1", candidate="cand-delta-20260801",
             created="2026-08-16T10:00:00+00:00", verdict=Verdict.GO,
             rate=0.9900, lower=0.9500, interval=(0.9400, 0.9990),
             goldenset_hash="d" * 64, regressed=False, floor_cleared=True),
        "2026-08-16T10:00:01+00:00",
    )
    add(
        _run(root, "x2", candidate="cand-epsilon-20260802",
             created="2026-08-17T10:00:00+00:00", verdict=Verdict.GO,
             rate=0.9800, lower=0.9400, interval=(0.9300, 0.9950),
             judges_hash="f" * 64, regressed=False, floor_cleared=True),
        "2026-08-17T10:00:01+00:00",
    )
    add(
        _run(root, "x3", candidate="",
             created="2026-08-18T10:00:00+00:00", verdict=Verdict.GO,
             rate=0.9700, lower=0.9300, interval=(0.9200, 0.9900),
             regressed=False, floor_cleared=True),
        "2026-08-18T10:00:01+00:00",
    )
    add(
        _run(root, "n1", candidate="cand-zeta-noRate",
             created="2026-08-19T10:00:00+00:00", verdict=Verdict.REVIEW,
             rate=None, lower=None, interval=None, underpowered=True,
             regressed=False, p_value=None),
        "2026-08-19T10:00:01+00:00",
    )
    add(
        _run(root, "n2", candidate="cand-eta-noFloor",
             created="2026-08-20T10:00:00+00:00", verdict=Verdict.REVIEW,
             rate=0.8300, lower=0.7600, interval=(0.7500, 0.8900),
             min_rate=None, drop_floor_everywhere=True, regressed=False),
        "2026-08-20T10:00:01+00:00",
    )
    add(
        _run(root, "n3", candidate="cand-theta-noAdapter",
             created="2026-08-21T10:00:00+00:00", verdict=Verdict.NO_GO,
             rate=0.6100, lower=0.5200, interval=(0.5000, 0.7100),
             drop_adapter=True),
        "2026-08-21T10:00:01+00:00",
    )
    add(
        _run(root, "r4", candidate="cand-alpha-20260501",
             created="2026-08-22T10:00:00+00:00", verdict=Verdict.NO_GO,
             rate=0.7550, lower=0.6600, interval=(0.6400, 0.8400)),
        "2026-08-22T10:00:01+00:00",
    )
    return write_evidence(root / "evidence.jsonl", records)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: fixtures.py <output-directory>", file=sys.stderr)
        return 2
    out = Path(args[0]).resolve()
    single = standard_scenario(out / "single")
    field = candidate_field_log(out / "field")
    print("single-run evidence :", single.evidence)
    print("candidate field     :", field)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
