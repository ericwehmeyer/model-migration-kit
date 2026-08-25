"""Recompute the demo's statistics from the raw responses, independently of the tool.

The report's own discipline is that it never recomputes a statistic: it echoes
what ``comparison.py`` recorded. That makes it faithful, and it also means the
report cannot notice when the recorded number is computed over the wrong ``n``.
Somebody has to do the arithmetic from outside. This is that.

It re-derives, from the demo's scripted responses and golden set alone:

* each item's judge score on both sides, through ``demo._grade``;
* the Mann-Whitney U p-value **twice** -- once over the 60 completions per side
  the payload reports, and once over the **12 independent items** the demo
  actually has -- because the demo's adapters are ``Mapping[str, str]`` and its
  judge is a pure function of the text, so the five draws per item are one sample
  printed five times, not five samples. On the shipped demo those two are
  p = 0.0078 and p = 0.153: the first fires the NO-GO rule and the second does
  not;
* the Wilson interval and one-sided lower bound at both of those ``n``, for the
  same reason -- the printed interval is roughly half the width the independent
  data supports.

Independence is the point, so it is enforced rather than assumed
----------------------------------------------------------------
The Wilson arithmetic here is written out from the formula and **deliberately
does not call** ``opik_rigor.wilson_interval``, even though that is the function
the tool used and importing it would be one line. A recomputation that calls the
code under audit checks that the caller passed the right arguments and nothing
else -- and this audit's finding is about the arguments.

There is a second, mechanical reason. ``scripts/`` is one of the three
directories ``scripts/dependency_surface.py`` walks, so an ``import opik_rigor``
here would add a row to ``COMPATIBILITY.md``'s dependency-surface table and the
merge gate would fail until somebody regenerated it. An audit tool should not
change the surface of the thing it audits.

``migkit demo`` deletes its own work directory
-----------------------------------------------
``cmd_demo`` removes ``work_dir`` in a ``finally`` unless ``--keep`` or
``--work-dir`` was passed, and it does so *after* writing the HTML -- so the
shipped page names six absolute paths that no longer exist by the time anyone
reads it. :func:`rebuild_demo` calls ``run_demo`` into a directory you control,
producing the identical evidence log (same golden-set, config and judges hashes;
only timestamps and paths differ), so the payload can actually be quoted.

::

    python scripts/audit/recompute.py
    python scripts/audit/recompute.py --rebuild /tmp/demo-work
"""

from __future__ import annotations

import argparse
import json
from importlib.resources import files
from math import sqrt
from pathlib import Path

from scipy.stats import mannwhitneyu, norm

from model_migration_kit.demo import (
    BASELINE_RESPONSES,
    CANDIDATE_RESPONSES,
    DATA_PACKAGE,
    GOLDENSET_FILE,
    _grade,
    run_demo,
)

#: The judge's pass bar. ``demo.judge_script`` scores 1-5 and the gate counts a
#: completion as passing at 4 or better.
PASS_SCORE = 4

#: The demo's draws per item -- the multiplier under audit.
DRAWS_PER_ITEM = 5


class _Item:
    """The two golden-set fields ``_grade`` reads, and the tags, for the dimensions."""

    def __init__(self, raw):
        self.id = raw["id"]
        self.input = raw["input"]
        self.reference = raw.get("reference")
        self.tags = raw.get("tags", [])


def golden_items() -> list[_Item]:
    """The bundled demo golden set, read from the installed package data."""
    raw = files(DATA_PACKAGE).joinpath(GOLDENSET_FILE).read_text()
    return [_Item(json.loads(line)) for line in raw.splitlines() if line.strip()]


def demo_scores():
    """Return ``(items, baseline_scores, candidate_scores)``, one score per item.

    Derived by pushing the scripted responses back through the demo's own judge,
    which is what makes the numbers comparable with the recorded ones without
    trusting the recorded ones.
    """
    items = golden_items()
    by_input = {item.input: item for item in items}
    baseline, candidate = [], []
    for item in items:
        baseline.append(_grade(item.input, BASELINE_RESPONSES[item.id], by_input)[0])
        candidate.append(_grade(item.input, CANDIDATE_RESPONSES[item.id], by_input)[0])
    return items, baseline, candidate


def mann_whitney(candidate, baseline, repeats: int = 1):
    """One-sided Mann-Whitney U (candidate *less* than baseline), each score repeated.

    ``repeats=5`` reproduces what the tool computed over 60 completions a side;
    ``repeats=1`` is the same test over the 12 observations the demo has. The
    difference between the two is not a rounding difference -- replicating each
    observation five times multiplies the rank sums without adding one bit of
    information, and the U test has no way to know.
    """
    left = [score for score in candidate for _ in range(repeats)]
    right = [score for score in baseline for _ in range(repeats)]
    result = mannwhitneyu(left, right, alternative="less")
    return float(result.statistic), float(result.pvalue)


def wilson_interval(successes: int, total: int, confidence: float = 0.95):
    """Two-sided Wilson score interval. Written from the formula, on purpose.

    See the module docstring: calling the project's own implementation would make
    this a test of the call site rather than of the number.
    """
    if total <= 0:
        raise ValueError("a rate over zero runs is not a rate")
    z = norm.ppf(1 - (1 - confidence) / 2)
    return _wilson(successes, total, z)


def wilson_lower_bound(successes: int, total: int, confidence: float = 0.95) -> float:
    """One-sided Wilson lower bound -- the number a pass-rate floor is compared to."""
    if total <= 0:
        raise ValueError("a rate over zero runs is not a rate")
    return _wilson(successes, total, norm.ppf(confidence))[0]


def _wilson(successes: int, total: int, z: float):
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    half = (z / denominator) * sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return max(0.0, center - half), min(1.0, center + half)


def rebuild_demo(destination: Path) -> Path:
    """Run the demo into a directory that survives, and return the evidence path."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    return Path(run_demo(destination).evidence)


def report(confidence: float = 0.95) -> None:
    """Print the whole recomputation, both ways, side by side."""
    items, baseline, candidate = demo_scores()
    passes_b = sum(1 for score in baseline if score >= PASS_SCORE)
    passes_c = sum(1 for score in candidate if score >= PASS_SCORE)

    print(f"{'item':14} {'tags':30} base cand")
    for item, one_b, one_c in zip(items, baseline, candidate, strict=True):
        print(f"{item.id:14} {','.join(item.tags):30} {one_b:4} {one_c:4}")
    print()
    print("baseline scores  :", baseline)
    print("candidate scores :", candidate)
    print(f"baseline  passes : {passes_b}/{len(items)} items"
          f"  = {passes_b * DRAWS_PER_ITEM}/{len(items) * DRAWS_PER_ITEM} completions")
    print(f"candidate passes : {passes_c}/{len(items)} items"
          f"  = {passes_c * DRAWS_PER_ITEM}/{len(items) * DRAWS_PER_ITEM} completions")
    print()

    u_wide, p_wide = mann_whitney(candidate, baseline, repeats=DRAWS_PER_ITEM)
    u_true, p_true = mann_whitney(candidate, baseline, repeats=1)
    print("Mann-Whitney U, candidate < baseline")
    wide_label = f"each item x{DRAWS_PER_ITEM} (what the payload records)"
    true_label = f"{len(items)} independent items"
    print(f"  {wide_label:44} : U={u_wide:>8.1f}  p={p_wide!r}")
    print(f"  {true_label:44} : U={u_true:>8.1f}  p={p_true!r}")
    print()

    print(f"Wilson at confidence {confidence}")
    for label, successes, total in (
        ("candidate, 5x", passes_c * DRAWS_PER_ITEM, len(items) * DRAWS_PER_ITEM),
        ("candidate, items", passes_c, len(items)),
        ("baseline, 5x", passes_b * DRAWS_PER_ITEM, len(items) * DRAWS_PER_ITEM),
        ("baseline, items", passes_b, len(items)),
    ):
        low, high = wilson_interval(successes, total, confidence)
        one_sided = wilson_lower_bound(successes, total, confidence)
        print(f"  {label:18} {successes:>3}/{total:<3} "
              f"[{low:.4f}, {high:.4f}]  width {high - low:.4f}  "
              f"one-sided lower {one_sided:.4f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument(
        "--rebuild",
        default=None,
        metavar="DIR",
        help="also run the demo into DIR, which -- unlike `migkit demo` -- keeps it",
    )
    args = parser.parse_args(argv)
    report(args.confidence)
    if args.rebuild:
        print()
        print("rebuilt demo evidence:", rebuild_demo(Path(args.rebuild)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
