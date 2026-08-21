# The report, redesigned: series, candidates and dimensions

Design agreed 2026-08-21. Not implemented. This document is the decision record;
the implementation plan is a separate artifact.

## Why

The HTML report is what sells this tool. It is the only part a reader who has
never run `migkit` will ever see, and it is what a decision gets defended with
after the fact. Reviewed today, it is about 80% of what it needs to be: the
numbers are right, the provenance is careful, and the document is honest about
its own gaps. What it cannot do is put a verdict in context.

Three things are missing, and all three are already latent in data the tool
records and then discards at render time.

## Job to be done

Two audiences, one document.

**The engineer who ran it** needs a decision and the evidence behind it. Served
adequately today.

**The person they must convince** — a staff engineer or a director — needs the
story in ten seconds and the proof underneath it when they push back. Not served
today: the report presents a number without the context that makes it mean
anything.

The document is also the showcase. It is linked from public writing, so it must
land for a stranger. It must do that *without* becoming a brochure, because the
same file is what a user sees on their fortieth nightly run. Explanatory material
earns its place on first read and must be cheap to skip on every read after.

**The showcase and the shipped report are the same artifact.** Not a mockup, not
a landing page: one rendered run of the real renderer. A demo the real output
cannot live up to is worse than no demo.

## Operating model

CI, on a schedule, accumulating runs unattended. This changes what the tool *is*:
run ad hoc it is a migration gate, asked once and finished; run nightly it is a
regression monitor, because the candidate is not static — providers ship updates
under the same name, the golden set grows, judges get tuned. The single
comparison stops being the whole story and becomes the latest point in a series.

## The document

### Zone 1 — the decision

The verdict, with the numbers that produced it, in a sentence a director reads
once: *"Candidate passes 87% (95% CI 79–92%) against a 90% floor. The interval no
longer covers it."*

The Wilson interval is drawn as an interval against the floor, not printed as
digits. The relationship between the band and the floor **is** the verdict, and a
reader should be able to see it without arithmetic.

Where the log holds several candidates against one baseline, zone 1 is a table:
one row per candidate, carrying pass rate with interval, delta against baseline
in percentage points, median latency, and verdict. One candidate collapses the
table to a single row and it is not rendered as a table at all.

### Zone 1b — by dimension

Golden-set items carry tags; `goldenset.py` already counts them and nothing
downstream uses the counts. This is where the aggregate lies. A candidate at 87%
overall can be 100% on arithmetic and extraction and **25% on refusal** — the
same number, and a completely different decision. The dimension matrix is rows of
tags against columns of baseline and each candidate.

This is the most actionable thing the report can say and it currently says
nothing.

### Zone 2 — the series

Every prior comparison in the log, rendered as a timeline: verdict per run, pass
rate as a line with its Wilson band, the floor as a rule across it.

Beneath it, aligned to the selected run, a **parameter strip**: what changed
between this run and the one before — `model_id`, `n_per_item`, item count, judge
identity. The strip is the argument. When one row moved and everything else held,
the drop is attributable rather than merely observed.

Then the existing per-item detail — flips, gains, unstable — as the evidence a
skeptic asks for.

The x-axis is **time, not run index**. Under CI a three-week gap is itself
information and evenly spaced dots hide it.

### Unchanged

Methodology appendix and provenance keep their current job and position.

## Data

No new instrumentation. No schema change. No migration.

`report.py:809-811` walks the evidence log for `EVENT_COMPARISON` and
`EVENT_VERDICT` records and keeps only the last of each. Every prior comparison
is already on disk. The change is to keep all of them.

`ReportModel` gains `series: tuple[RunPoint, ...]`. A `RunPoint` is assembled
only from what is already recorded — verdict, pass rate, interval, `model_id`,
`adapter`, `n_per_item`, `items`, `completions`, `failures`, timestamp.

The same series is sliced two ways:

| Slice | Grouped by | Renders as |
|---|---|---|
| Trend | same (baseline, candidate) pair, over time | zone 2 timeline |
| Field | same baseline, different candidates, one window | zone 1 candidate table |

One data change buys both views, and **no new CLI command is required**. Running
`compare` three times against one baseline into one log already produces the
three-candidate dataset. The report renders what the log happens to contain.

This preserves invariant 2 — `ReportModel` is built only from evidence on disk,
never from a live `ComparisonReport`. A series can come from nowhere else, so
zone 2 is a stricter test of that invariant than zone 1 is.

### Facts to confirm before implementation

Neither is assumed by this design; both change it if absent.

1. **Are thresholds recorded per comparison** — pass floor, confidence, alpha? If
   not, the floor cannot be drawn historically. Taking it from current config
   would be a lie on any run that used a different one. Fallback: draw the floor
   only where it is known, and say so on the runs where it is not.
2. **Are judge identity and golden-set identity recorded** (hashes)? Without
   them the parameter strip cannot claim "judges unchanged", and grouping cannot
   establish comparability.

## Honesty guards

These are requirements, not polish. The product's entire claim is that it does
not overclaim; a report that violates that costs more credibility than the
features gain.

**Per-dimension verdicts below a sample-size threshold are refused.** Four items
per tag gives an interval so wide it means nothing. Show the interval, state the
sample size needed, and decline the verdict: *"20 items needed for a verdict
here; you have 4."* Every dashboard in this market would happily colour that cell
red. Declining is the differentiator.

**Multiplicity is corrected at render time, and said out loud.** Three candidates
against one floor inflates false positives. Each `compare` ran without knowledge
of the others, so the correction belongs at the point the group is assembled.
opik-rigor's `holm_bonferroni` is the mechanism. The report states that it was
applied.

**Incomparable runs are never silently stacked.** Different `n_per_item`, a
changed golden set, or different judges must exclude a run from a table or flag
it visibly. `_require_comparable` exists; grouping must respect it. A table that
quietly compares a 60-item run against a 40-item run is worse than no table.

**Staleness is shown.** Candidates compared three weeks apart are not a fair
field; the baseline may have drifted underneath them. Each candidate carries its
run date and a spread is flagged.

**Synthetic data is labelled as synthetic.** The showcase needs a fabricated
history. A demo that implies twelve weeks of real production runs would undercut
precisely the credibility being built. The banner is a feature, not an apology.

## REVIEW is a designed state, not a colour

The differentiator is refusing to answer when the evidence is thin. In a layout
built around GO and NO-GO, REVIEW degrades into "NO-GO in amber", which throws
away the most honest thing the tool does.

When the headline run is REVIEW, zone 1 changes shape: the interval is drawn
*straddling* the floor, and the callout is actionable rather than a verdict —
*"collect 340 more completions"*. This is designed deliberately, not derived.

## The counterfactual line

The report shows a drop from 94% to 87%. It never says that this drop is
invisible to the method the reader would otherwise use — a dozen prompts through
the new model, read the answers, ship. That is the entire pitch, and the document
currently assumes the reader already believes it.

The tool can compute the counterfactual from numbers it already has:

> A 12-prompt spot check would have shown no failures at all in 34% of runs.

One line, arithmetic the tool already does, and it converts the report from
*reporting a number* into *demonstrating why the number needed a method*. It is
the most persuasive sentence available to a first-time reader and it is honest.

Requires a real power calculation. Not a hand-wave.

## Constraint: self-contained

`assert_self_contained` and the URL scanner in `report.py` forbid external
references, and the invariant is correct — a report is emailed, attached to a
ticket, and opened offline six months later.

Therefore: no web fonts, no CDN, no icon library, no charting library. System
font stack, hand-rolled inline SVG for the interval bars and the timeline, all
CSS inlined. This is a real limit on visual ambition and not a fatal one;
constraint-driven design reads as more serious than a template.

## Seed data

The demo's 12 items cannot carry a dimension table honestly — 4 items per tag is
exactly the sample size the design refuses to call.

The showcase needs:

- A synthetic golden set of roughly 60–120 items across 5–6 dimensions, so
  per-dimension n lands near 15–25: tight enough to be interesting, loose enough
  to still show real uncertainty.
- Three candidates against one baseline.
- Fourteen nightly runs, so the timeline is dense enough to read as CI.
- A deliberate narrative: thirteen green runs, then a provider point release on
  run 14 collapses one candidate's refusal dimension while every other parameter
  holds. A demo where everything is green proves nothing.
- At least one REVIEW earlier in the timeline, so the state is visible.
- Every artifact labelled synthetic.

### How the seed is produced, and why it matters

The seed must be generated by **running the real pipeline against deterministic
fake adapters**, not by writing an evidence log directly.

`FakeAdapter` already exists and `migkit demo` already uses it — keyless,
deterministic, no network. Extending that to a larger golden set, three
candidates and fourteen dated runs means the evidence log is produced by the same
`run` → `compare` path a real user takes: real judging, real statistics, real
verdicts, real evidence records. Only the models are fabricated.

This matters more than it looks. Hand-writing an evidence log would make the
showcase a mockup wearing the renderer's clothes, and the claim "this is the
tool's actual output" would be false in the one place it is load-bearing.
Generating it through the pipeline keeps the claim exactly true: **the models are
synthetic and everything else is real.** That is a sentence that survives
scrutiny, which is the only kind worth writing.

It also means the seed generator doubles as an end-to-end test of the whole
pipeline at a scale the 12-item demo never reaches.

## Out of scope

- A `migkit compare --candidates A,B,C` command. Grouping the log removes the
  need, and adding a command would make the showcase stop being real output.
- Cost. Migrations are usually driven by cost, and the real decision is "12%
  cheaper, 7 points worse" — but the tool has no business inventing prices it
  cannot know. Latency is recorded and stays; price does not enter.
- A sensitivity/parameter-sweep view ("would this verdict survive different
  knobs"). The most differentiated idea available and the largest build. Deferred
  to a later version deliberately, not forgotten.
- Any change to how verdicts are computed. This is a rendering change.

## Open decisions

Each has a recommended default so implementation is not blocked.

1. **Showcase headline: NO-GO or REVIEW?** Default **NO-GO** for the headline run
   with a REVIEW earlier in the timeline. NO-GO is the stronger opening frame for
   a stranger; REVIEW is the subtler state and still gets shown.
2. **Counterfactual line in v1?** Default **yes**. It is the sentence that makes
   the case, and deferring it means shipping a showcase that does not sell.
3. **Per-item detail placement.** Default **zone 2**, as detail about the latest
   run. The argument against: three flipped items are the first thing a skeptic
   asks for and may belong beside the verdict.

## Testing

The renderer is already tested by reconstruction from evidence, and that shape
extends: build an evidence log fixture holding many comparisons, render, assert
on the series.

Specific cases that must exist, because each is a way this design fails quietly:

- A log with one comparison renders zone 2 as a single point and no candidate
  table, rather than an empty chart or a crash.
- Runs with mismatched `n_per_item` are excluded or flagged, never stacked.
- A per-dimension cell below the sample threshold renders a refusal, not a
  verdict.
- The multiplicity correction changes a rendered verdict when candidates are
  added, and the report says it was applied.
- A synthetic-data banner cannot be suppressed by any input.
- `assert_self_contained` passes on the new template, with the timeline and
  interval SVGs inlined.
