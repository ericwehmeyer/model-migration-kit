# Second-operator audit brief — read the document, not the code

You are a **second operator** on a different machine from the one running this
project's build pipeline. Work autonomously. Do not wait for confirmation.

Your job is the one perspective this pipeline structurally lacks: **every role in
it reads the contract, and nobody has read the finished document as a sceptical
reader.**

## Setup

You have already cloned this repo and checked out this branch, or you would not
be reading this. Finish the setup:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

If `opik-rigor` does not resolve from PyPI, clone
`https://github.com/ericwehmeyer/opik-rigor.git` next to this repo,
`pip install -e ../opik-rigor`, then retry.

Verify, and **report the real number rather than the expected one**:

```bash
pytest -q          # ~2206 passed, zero failures at the time this was written
```

If the count differs or anything fails, say so before going further — a
divergence there is itself a finding.

## Render some documents

```bash
python -m model_migration_kit.cli demo --out /tmp/report.html
```

**The bundled demo is a single run and hides most of the document.** With one run
there is no candidate table, no multiplicity note, no spot-check sentence and a
one-point line, so four of the document's sections do not appear at all.

So build at least two more evidence logs of your own, with:

- **more than one run**, on more than one date;
- **more than one candidate model**, so the candidate table renders;
- **runs that should be excluded** — a different golden set, a different judge
  panel, an unrecorded candidate id;
- at least one run that recorded **no** pass rate, or no floor, or no adapter.

`tests/test_report.py` shows how evidence logs are constructed; grep it for
`_record(` and `_write_evidence(`. Render each one and open it in a browser.

## The job

Read the rendered HTML as a reader who wants to catch it lying. **Not the code —
the document.**

The project's central design rule, which five separate chunks have turned on:

> **An absence must not render as a measurement.** A value that was never
> recorded, a comparison that could not be made, and a measured zero must be
> distinguishable on the page.

Find every place the rendered document breaks that rule, or asserts something the
evidence does not support.

**The shape of the defect, from a real one found last week.** The methodology
paragraph printed *"At least one side of this comparison was produced by a Fake
adapter"* and then named two **real** adapters — because the sentence was scoped
to the headline run while the flag it read was scoped to the whole series. A true
sentence about the wrong thing, in the paragraph a sceptical reader goes to first.

Look for more of that shape:

- **Scope mismatches** — a sentence about the headline reading a series-level
  fact, or the reverse.
- **Hedges that hedge the wrong way** — a page saying "may have been excluded"
  where it could name them, or saying nothing where it cannot.
- **Counts of the wrong unit** — comparisons counted as runs, completions counted
  as judged completions, adapter mentions counted as runs.
- **Empty sections that read as findings** — a heading over nothing, a zero that
  looks measured, an empty list that reads as "nothing was excluded".
- **Precision the source does not support** — a number rendered to more decimal
  places than the evidence holds, or an interval printed where none was computed.
- **Anything a link points at that is not there**, and anything on the page that
  nothing links to.

## Rules

- **Do not fix anything.** Find, prove, report. Someone else fixes.
- **Every claim must carry the output that proves it**: the exact rendered text,
  the fixture that produced it, and what the evidence file actually said. A claim
  without its output is not a finding on this project — three of the
  orchestrator's own rulings have been wrong for exactly that reason.
- **If you cannot reproduce a suspicion, say so and drop it.** A plausible finding
  that does not reproduce costs more than it is worth.
- Read `docs/superpowers/plans/2026-08-21-migkit-report-plan.md` **only as
  needed** — grep it when you want to know what a section was supposed to claim.
  It is 300KB. Do not read it front to back.

## Report

Write `AUDIT-macbook.md` at the repo root, findings ranked by **how badly a reader
would be misled**, not by how clever the finding is. For each:

1. The rendered text, verbatim.
2. The fixture that produced it, reproducibly.
3. What the evidence actually holds.
4. Why the two differ, and what a reader would wrongly conclude.

Then:

```bash
git checkout -b audit/macbook-2026-08-24
git add AUDIT-macbook.md
git commit -m "Document audit from a second operator"
git push -u origin audit/macbook-2026-08-24
```

and tell the user the branch name.

## If you find nothing

Say so plainly, and say what you looked at and how. **A negative result that
names its coverage is worth having**; a negative result that does not is worth
nothing. Do not manufacture findings to justify the run — this project has
already learned that three consecutive blind pairs agreeing meant nothing about
defect-freedom, and the reverse error is just as available.
