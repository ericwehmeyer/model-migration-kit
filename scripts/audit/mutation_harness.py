"""Break the renderer one line at a time and see whether the suite notices.

This project's standing rule:

    *A fixture where the broken and the correct implementation agree is a fixture
    that tests nothing.*

A mutation harness is how that stops being an opinion. Change one line of shipped
source so the rendered document says something false; run the whole suite; if it
still passes, the mutant **survived** and there is a fixture -- or a missing
assertion -- that cannot tell the two apart. On the run this catalogue comes
from: **30 mutations, 17 survived, 13 killed.**

A survivor is only a finding if it changes the *document*
----------------------------------------------------------
A mutation of dead code survives trivially and means nothing. So every survivor
must be shown to change the rendered page: :func:`render_pair` renders the demo
on clean source and on mutated source and diffs the two, and a survivor with an
empty diff is discarded rather than reported. That is what separates "the suite
has a gap" from "this line does not matter".

Safety, which is not optional here
-----------------------------------
This harness **edits shipped source files**. Three rules, all of which this
project has paid for:

1. **Never in the main checkout, never in a tree another agent is using.** Make a
   detached worktree of your own::

       git worktree add --detach /tmp/mk-mutation HEAD

   :func:`prepare` refuses a worktree that is the repository root.
2. **Restore from a byte-verified backup, never ``git checkout --``.** ``prepare``
   copies each target file and records its sha1; every mutation is undone by
   copying the backup back and re-checking the hash, in a ``finally``. A restore
   that does not verify is not a restore.
3. **Confirm ``git status`` is clean before reporting anything.**

The interpreter trap on this machine
-------------------------------------
The venv lives in the main checkout, and its import hook resolved ``model_migration_kit``
to the **main tree** rather than to the worktree -- so the suite ran against
unmutated code and every mutant "survived"::

    $ cd <worktree> && .venv/bin/python -c "import model_migration_kit as m; print(m.__file__)"
    /Users/.../model-migration-kit/src/model_migration_kit/__init__.py     # WRONG TREE

``CLAUDE.md`` documents a ``.pth`` fix for worktrees; it did not take effect for
a worktree outside the repository root on macOS. So this harness sets
``PYTHONPATH=<worktree>/src`` on every subprocess it launches, and
:func:`prepare` asserts that the interpreter actually resolves to the worktree
before a single mutation is applied. **A harness that cannot prove which tree it
is testing produces a page of survivors and no information.**

::

    python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --list
    python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --run M1 M2
    python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --all
    python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --render M7
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from masking import mask_run_output  # noqa: E402
from page_text import html_to_text  # noqa: E402

REPO = _HERE.parent.parent


@dataclasses.dataclass(frozen=True)
class Mutation:
    """One exact-string substitution in one shipped module.

    Exact strings rather than regexes or line numbers: a mutation that silently
    matches nothing, or matches twice, is worse than no mutation at all, because
    it reports a survivor. :attr:`count` is asserted before anything is written.
    """

    name: str
    summary: str
    module: str
    old: str
    new: str
    count: int = 1


#: The modules this catalogue touches. Backed up before anything runs.
TARGETS = ("report.py", "series.py", "dimensions.py")

CATALOGUE: tuple[Mutation, ...] = (
    Mutation("M1", "RateStat.from_gate stops reading `successes`", "report.py",
             'passes=int(gate.get("successes") or 0),', "passes=0,"),
    Mutation("M2", "the judge-level underpowered roll-up is always False", "report.py",
             'underpowered=bool(raw.get("underpowered", False)),', "underpowered=False,"),
    Mutation("M3", "the baseline pass rate in the series is always 1.0", "series.py",
             "    if point.judged_baseline <= 0:\n"
             "        return None\n"
             "    if not 0 <= point.judge_failures_baseline <= point.judged_baseline:\n"
             "        return None\n"
             "    return (point.judged_baseline - point.judge_failures_baseline)"
             " / point.judged_baseline",
             "    return 1.0"),
    Mutation("M4", "the exit code a CI system would receive is always 0", "report.py",
             "    def exit_code(self) -> int:\n"
             "        return Verdict.exit_code(self.verdict or Verdict.ERROR)",
             "    def exit_code(self) -> int:\n        return 0"),
    Mutation("M5", "the 'these are scripted models' sentence is dropped", "report.py",
             "            opening,\n            _counted_paragraph(provenance),\n"
             "            _MACHINERY_IS_REAL,\n            _dated_sentence(provenance),",
             "            opening,\n            _counted_paragraph(provenance),\n"
             "            _dated_sentence(provenance),"),
    Mutation("M6", "the FAKE MODELS title prefix is never added", "report.py",
             "    if not model.is_demo or head.upper().startswith(_FAKE_TITLE_PREFIX):\n"
             "        return head\n"
             '    return f"{_FAKE_TITLE_PREFIX} {EM_DASH} {head}"',
             "    return head"),
    Mutation("M7", "latency is never suppressed -- the table always renders", "report.py",
             "{% if model.baseline.is_fake and model.candidate.is_fake %}",
             "{% if False %}"),
    Mutation("M8", "the banner bar is drawn from judges[0] instead of the series", "report.py",
             "    point = model.series[-1] if model.series else None\n"
             "    if point is None:\n"
             "        return interval_bar_svg(rate=None, interval=None, floor=None,"
             ' label="candidate")\n'
             '    label = f"candidate {point.judge_name}".strip()\n'
             "    return interval_bar_svg(\n"
             "        rate=point.pass_rate,\n"
             "        interval=point.interval,\n"
             "        floor=point.floor,\n"
             "        label=label,\n"
             "    )",
             "    row = model.judges[0] if model.judges else None\n"
             "    if row is None:\n"
             "        return interval_bar_svg(rate=None, interval=None, floor=None,"
             ' label="candidate")\n'
             '    label = f"candidate {row.name}".strip()\n'
             "    return interval_bar_svg(\n"
             "        rate=row.candidate.rate,\n"
             "        interval=row.candidate.interval,\n"
             '        floor=model.thresholds.floor if hasattr(model.thresholds, "floor")'
             " else None,\n"
             "        label=label,\n"
             "    )"),
    Mutation("M9", "every change section (flips, gains, unstable) renders empty", "report.py",
             'entries = [dict(one) for one in payload.get(name, ()) or ()]',
             "entries = []"),
    Mutation("M10", "a multi-tagged item is counted under its first tag only",
             "dimensions.py",
             "            for tag in by_id[item_id].tags or (UNTAGGED,):",
             "            for tag in (by_id[item_id].tags or (UNTAGGED,))[:1]:"),
    Mutation("M11", "every set of draws is reported as all-identical", "report.py",
             "    if len(seen) == 1 and len(outputs) > 1:\n"
             "        return _Draws((seen[0],), len(outputs), 1)\n"
             "    return _Draws(tuple(outputs), len(outputs), len(seen))",
             "    return _Draws((seen[0],) if seen else (), len(outputs), 1)"),
    Mutation("M12", "warnings: a null becomes an empty list instead of crashing",
             "report.py",
             'warnings: list[str] = [str(one) for one in payload.get("warnings", ())]',
             'warnings: list[str] = [str(one) for one in payload.get("warnings") or ()]'),
    Mutation("M13", "draws per item is hardcoded to 5 rather than read", "report.py",
             'n_per_item=int(payload.get("n_per_item", 0) or 0),', "n_per_item=5,"),
    Mutation("M14", "the HTML judge table renders only the first judge", "report.py",
             "{% for judge in model.judges %}", "{% for judge in model.judges[:1] %}"),
    Mutation("M16", "the two scripted-model openings are swapped", "report.py",
             "    if provenance.headline_scripted:", "    if not provenance.headline_scripted:"),
    Mutation("M18", "the candidate table renders only its first row", "report.py",
             "  {% for row in candidate_field.candidates %}",
             "  {% for row in candidate_field.candidates[:1] %}"),
    Mutation("M19", "the dimension matrix drops every candidate column", "report.py",
             "{% set columns = [matrix.baseline] + (matrix.candidates | list) %}",
             "{% set columns = [matrix.baseline] %}"),
    Mutation("M20", "every confidence interval becomes absent", "report.py",
             "    interval = None if lower is None or upper is None"
             " else (float(lower), float(upper))",
             "    interval = None"),
    Mutation("M21", "imputed completions on the baseline are always 0", "report.py",
             '        imputed_baseline=int(imputed.get("baseline", 0) or 0),',
             "        imputed_baseline=0,"),
    Mutation("M22", "parse failures on the candidate are always 0", "report.py",
             '        parse_failures_candidate=int(parse_failures.get("candidate", 0) or 0),',
             "        parse_failures_candidate=0,"),
    Mutation("M23", "the Holm threshold is always absent", "report.py",
             '        holm_threshold=_number(raw.get("holm_threshold")),',
             "        holm_threshold=None,"),
    Mutation("M24", "'was the rank test powered' is always unknown", "report.py",
             '        mw_powered=_bool_or_none(raw.get("mw_powered")),',
             "        mw_powered=None,"),
    Mutation("M25", "the runs-needed figure is always absent", "report.py",
             '        runs_needed=None if raw.get("runs_needed") is None'
             ' else int(raw["runs_needed"]),',
             "        runs_needed=None,"),
    Mutation("M26", "the item count is always 0", "report.py",
             '        items=int(counts.get("items", 0) or 0),', "        items=0,"),
    Mutation("M27", "latency is suppressed if EITHER side is scripted", "report.py",
             "{% if model.baseline.is_fake and model.candidate.is_fake %}",
             "{% if model.baseline.is_fake or model.candidate.is_fake %}"),
    Mutation("M29", "the FAKE MODELS prefix test becomes a substring test", "report.py",
             "    if not model.is_demo or head.upper().startswith(_FAKE_TITLE_PREFIX):",
             "    if not model.is_demo or _FAKE_TITLE_PREFIX in head.upper():"),
    Mutation("M30", "a missing adapter name renders as empty, not 'unknown'", "report.py",
             "            f\"({model.baseline.adapter or 'unknown'} for the baseline, \"\n"
             "            f\"{model.candidate.adapter or 'unknown'} for the candidate).\"",
             '            f"({model.baseline.adapter} for the baseline, "\n'
             '            f"{model.candidate.adapter} for the candidate)."'),
    Mutation("M31", "a failed completion renders as an empty string", "report.py",
             "        if completion.output is None:\n"
             "            out.append(f\"[no output - {completion.error_type or 'error'}:"
             ' {completion.error}]")\n'
             "            continue",
             "        if completion.output is None:\n"
             '            out.append("")\n'
             "            continue"),
    Mutation("M32", "the truncation flag is never set", "report.py",
             "        truncated = truncated or cut", "        pass"),
    Mutation("M33", "a failed completion loses its error type", "report.py",
             "            out.append(f\"[no output - {completion.error_type or 'error'}:"
             ' {completion.error}]")',
             '            out.append("[no output - error: none]")'),
)

BY_NAME = {mutation.name: mutation for mutation in CATALOGUE}


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _source(worktree: Path, module: str) -> Path:
    return worktree / "src" / "model_migration_kit" / module


def _env(worktree: Path) -> dict[str, str]:
    """The environment every subprocess gets. See the interpreter trap above."""
    return {**os.environ, "PYTHONPATH": str(worktree / "src")}


def prepare(worktree: Path, backup: Path) -> dict[str, str]:
    """Refuse an unsafe tree, byte-back-up the targets, and prove the imports resolve.

    Returns the backup digests. Everything after this point restores against them.
    """
    worktree = worktree.resolve()
    if worktree == REPO.resolve():
        raise SystemExit(
            "refusing to mutate the checkout this script lives in -- "
            "make a detached worktree: git worktree add --detach <dir> HEAD"
        )
    if not (worktree / "src" / "model_migration_kit").is_dir():
        raise SystemExit(f"{worktree} does not look like a checkout of this project")

    backup.mkdir(parents=True, exist_ok=True)
    digests = {}
    for module in TARGETS:
        source = _source(worktree, module)
        shutil.copyfile(source, backup / module)
        digests[module] = _sha1(backup / module)

    probe = subprocess.run(
        [sys.executable, "-c",
         "import model_migration_kit as m; print(m.__file__)"],
        cwd=str(worktree), env=_env(worktree), capture_output=True, text=True, check=True,
    )
    resolved = Path(probe.stdout.strip()).resolve()
    if worktree not in resolved.parents:
        raise SystemExit(
            f"the interpreter resolves model_migration_kit to {resolved}, which is NOT "
            f"under {worktree}. Every mutant would survive against unmutated code. "
            "Fix the path before running anything."
        )
    print(f"[audit] imports resolve to {resolved}")
    return digests


def run_mutation(mutation: Mutation, worktree: Path, backup: Path, digests, workers: int = 4):
    """Apply one mutation, run the suite, restore, and return ``(killed, tail)``.

    ``killed`` is ``None`` when the pattern did not match the expected number of
    times -- a mutation that did not apply is neither a survivor nor a kill, and
    reporting it as either would be a lie.
    """
    source = _source(worktree, mutation.module)
    expected = digests[mutation.module]
    if _sha1(source) != expected:
        raise SystemExit(f"{mutation.module} is dirty before {mutation.name} -- stop")

    text = source.read_text(encoding="utf-8")
    found = text.count(mutation.old)
    if found != mutation.count:
        return None, f"pattern matched {found} times, expected {mutation.count}"

    source.write_text(text.replace(mutation.old, mutation.new), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-n", str(workers), "--tb=no"],
            cwd=str(worktree), env=_env(worktree), capture_output=True, text=True,
        )
        output = proc.stdout + proc.stderr
    finally:
        shutil.copyfile(backup / mutation.module, source)
        if _sha1(source) != expected:
            raise SystemExit(f"RESTORE FAILED for {mutation.module} -- do not continue")

    tail = [line for line in output.splitlines() if line.strip()][-1:]
    return proc.returncode != 0, " | ".join(tail)


def render_pair(mutation: Mutation, worktree: Path, backup: Path, digests, out: Path):
    """Render the demo clean and mutated, and return the unified diff of the two pages.

    An empty diff means the mutant does not change the document, so a surviving
    mutant here is a mutation of dead code rather than a gap in the suite.

    Both renders go through :func:`masking.mask_run_output`, because two separate
    ``migkit demo`` invocations differ in their evidence hash, their timestamp
    and their temporary directory -- without masking the diff is entirely noise.
    """
    import difflib

    out.mkdir(parents=True, exist_ok=True)
    source = _source(worktree, mutation.module)
    expected = digests[mutation.module]
    if _sha1(source) != expected:
        raise SystemExit(f"{mutation.module} is dirty before {mutation.name} -- stop")

    def render(tag: str) -> str:
        html = out / f"{tag}.html"
        subprocess.run(
            [sys.executable, "-m", "model_migration_kit.cli", "demo", "--out", str(html),
             "--keep", "--work-dir", str(out / f"{tag}-work")],
            cwd=str(worktree), env=_env(worktree), capture_output=True, text=True,
        )
        text = html_to_text(html.read_text(encoding="utf-8"))
        evidence = out / f"{tag}-work" / "evidence.jsonl"
        return mask_run_output(text, evidence_path=evidence if evidence.exists() else None)

    clean = render("clean")
    text = source.read_text(encoding="utf-8")
    if text.count(mutation.old) != mutation.count:
        return f"pattern matched {text.count(mutation.old)} times, expected {mutation.count}"
    source.write_text(text.replace(mutation.old, mutation.new), encoding="utf-8")
    try:
        mutated = render(mutation.name)
    finally:
        shutil.copyfile(backup / mutation.module, source)
        if _sha1(source) != expected:
            raise SystemExit(f"RESTORE FAILED for {mutation.module} -- do not continue")

    return "\n".join(
        difflib.unified_diff(
            clean.splitlines(), mutated.splitlines(), "clean", mutation.name,
            lineterm="", n=1,
        )
    )


def green_baseline(worktree: Path, workers: int) -> str:
    """Run the suite with **no mutation applied** and refuse to continue if it is red.

    Without this the harness reports a lie in the most reassuring possible
    direction. ``run_mutation`` calls a mutant *killed* when pytest exits
    non-zero, so on a tree that is already failing every mutant is killed and the
    catalogue comes back with a perfect score -- from the one state in which it
    has measured nothing at all.

    That is not hypothetical. On 2026-08-25 this harness reported **29 killed, 0
    survived** against ``main`` at ``f887b31``. The same tree, unmutated, was
    ``7 failed, 2287 passed``: the schema-guard tests call
    ``Path.read_text(newline=...)``, which is Python 3.13 only, and this venv is
    3.12. Re-run once those seven were deselected, the real answer was **17
    killed, 12 survived** -- twelve gaps in the suite that the red tree had
    hidden behind a perfect score.

    A green baseline is the only thing that makes a "killed" mean anything, so it
    is enforced here rather than advised, like every other safety rule in this
    module. ``PYTEST_ADDOPTS`` is inherited by :func:`_env`, so a tree that is red
    for a reason you have already diagnosed can be deselected into green
    deliberately -- which is a decision the operator makes and the output records,
    not one the harness makes silently.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", str(workers), "--tb=no"],
        cwd=str(worktree), env=_env(worktree), capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    tail = " | ".join([line for line in output.splitlines() if line.strip()][-1:])
    if proc.returncode != 0:
        raise SystemExit(
            "REFUSING TO RUN: the suite is red before any mutation is applied.\n"
            f"    {tail}\n"
            "Every mutant would be reported 'killed' by a suite that fails on its "
            "own, which is a perfect score from a measurement that did not "
            "happen. Fix the tree, or deselect the known failures explicitly with "
            "PYTEST_ADDOPTS and re-run so the deselection is on the record."
        )
    return tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worktree", help="a DETACHED worktree of your own -- not the checkout")
    parser.add_argument("--backup", default=None, help="where to keep the byte-verified copies")
    parser.add_argument("--workers", type=int, default=4, help="pytest -n (use 4 on a busy board)")
    parser.add_argument("--list", action="store_true", help="print the catalogue and stop")
    parser.add_argument("--run", nargs="+", metavar="NAME", help="run these mutations")
    parser.add_argument("--all", action="store_true", help="run the whole catalogue")
    parser.add_argument("--render", metavar="NAME", help="diff the rendered page for one mutation")
    args = parser.parse_args(argv)

    if args.list:
        for mutation in CATALOGUE:
            print(f"{mutation.name:4} {mutation.module:14} {mutation.summary}")
        return 0

    if not args.worktree:
        parser.error("--worktree is required for anything that edits source")
    worktree = Path(args.worktree).resolve()
    backup = Path(args.backup) if args.backup else worktree.parent / (worktree.name + "-backup")
    digests = prepare(worktree, backup)

    if args.render:
        print(render_pair(BY_NAME[args.render], worktree, backup, digests,
                          backup.parent / "render"))
        return 0

    names = [m.name for m in CATALOGUE] if args.all else (args.run or [])
    if not names:
        parser.error("nothing to do: pass --list, --run, --all or --render")
    baseline = green_baseline(worktree, args.workers)
    print(f"baseline (no mutation applied): {baseline}")
    if os.environ.get("PYTEST_ADDOPTS"):
        print(f"PYTEST_ADDOPTS in force: {os.environ['PYTEST_ADDOPTS']}")

    survived = killed = skipped = 0
    for name in names:
        result, tail = run_mutation(BY_NAME[name], worktree, backup, digests, args.workers)
        mutation = BY_NAME[name]
        if result is None:
            verdict, skipped = "DID NOT APPLY", skipped + 1
        elif result:
            verdict, killed = "killed", killed + 1
        else:
            verdict, survived = "SURVIVED", survived + 1
        print(f"{name:4} {verdict:14} {mutation.summary}\n     {tail}")
    print(f"\n{killed} killed, {survived} survived, {skipped} did not apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
