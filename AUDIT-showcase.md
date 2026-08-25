# JOB-4 — the showcase and the seed generator

MacBook, against issue #3. Ranked by **what a prospective user would wrongly believe** — the
showcase is the artifact a stranger is most likely to see.

## The job as briefed could not be run, and here is what was done instead

**`build_showcase` does not exist.** There is no end to end.

```
$ grep -rn "build_showcase" --exclude-dir=.git .
docs/superpowers/plans/…-report-plan.md:1628:def build_showcase(work_dir, *, nights=14) -> Path: ...
scripts/showcase.py:1226:# specifies `build_showcase(...)`, and no such function
```

Both of C17's named tests are absent — including the one the plan calls **"the point of the whole
phase"** — and **no test in the repo renders a showcase evidence log** (`grep -n "from_evidence\|
render_html" tests/test_showcase.py` → nothing). The only driver is
`tests/test_showcase.py::_drive`, which drives **four** nights, never fourteen, and never renders.

So a driver was written (~95 lines, in the scratchpad) mirroring `_drive` call-for-call plus the
14-night loop and C17's contracted `utc_now` patch and work-dir layout. It touches nothing in
`src/` or `tests/`. **Everything below is measured against that driver**, and every finding that
depends on a driver decision says so. It ran in **10.15 s** for 56 runs / 26,880 completions / 42
comparisons.

## Q1 — is anything hand-assembled? **No.** The spec's rule held.

`scripts/showcase.py` writes no file and constructs no evidence record; every artifact comes from
`run_goldenset` → `judge_artifact` → `compare`, with one `EvidenceLog` passed through. Every
headline number in its docstring reproduces to the digit (`0.2740022107544701`, `931`,
`0.8935079527787327`, `375/480`, `2.426401761042508e-12`).

> **SURVIVES.** The honest qualification: the pipeline is real, but it is a deterministic
> re-derivation of numbers that already exist as literals — `python scripts/showcase.py` prints
> every run's pass count *before any pipeline runs*, and the measured run matches exactly. It is
> **not a mockup; it is a machine faithfully recomputing a story**, and both `showcase.py` and
> the rendered document say so. Recorded as a distinction, not scored as a defect.

## S1. Zero within-item variance — and the night-14 GO depends on it

Worse than "two demos and one shape": the showcase does not merely inherit the demo's
pseudo-replication, **its own gate depends on it.**

```
DEMO:     (item,run) cells=24    cells with >1 distinct output across draws = 0
SHOWCASE: (item,run) cells=5376  cells with >1 distinct output across draws = 0
judged cells whose 5 draws differ in (score,passed,reason): 0    histogram {1: 5376}
runs whose passing-completion count is a multiple of 5: 56 of 56
```

**(a) The gate flips on it.**

```
as the report prints it (completions): 445/480  one-sided Wilson lower = 0.9051  -> clears 0.90
at the independent unit (items):        89/96   one-sided Wilson lower = 0.8708  -> misses it
```

The banner reads *"No judge regressed, **every judge cleared the pass-rate floor**"*. **At the
sample's real degrees of freedom the baseline does not clear it.** This is the demo's defect
reproduced at 96 items — harder to notice, and now load-bearing for a **GO**.

**(b) "Unstable items (0)" renders a structural impossibility as a measured zero.** With a
`Mapping` adapter an item's pass fraction is in {0, 1}; an unstable item **cannot exist**. The
prose says *"Listed even when nothing moved"*, asserting instability was looked for.

**(c) The appendix asserts the opposite of what happened:** *"A migration decision needs a
distribution per item rather than a single shot."* There is no distribution per item anywhere in
this document.

> **SURVIVES on (a) and (c); WEAKENED on (b)-as-deception.** The item counts (89/7/0) are
> printed, so a reader can recover 96 — but **the gate uses the 480-based bound.** The
> `all 5 draws identical` disclosure appears six times, only in flips/gains, only for the 3
> changed items; never near the intervals, never near "Unstable items (0)", never in the
> appendix. `showcase.py` chose the `Mapping` deliberately and justifies it on REVIEW being
> reachable — it never states what the choice costs the document, and the document never states
> it either.

## S2. The headline verdict and the CI exit code are decided by which candidate the driver compares last

Driving in `showcase.py`'s **own declared candidate order**, the last comparison is candidate C:

```
FAKE MODELS — GO — synthetic-baseline-v1 to synthetic-candidate-c-v1
  Exit code a CI system would have received: 0   — decided by rule 5
```

Change **one line** — the compare-loop order, so B is last:

```
seed2  Counter({'GO':40,'REVIEW':1,'NO-GO':1})  LAST: candidate-c  ->  VERDICT: GO (exit 0)
seed4  Counter({'GO':40,'REVIEW':1,'NO-GO':1})  LAST: candidate-b  ->  VERDICT: NO-GO (exit 1)
       sorted multiset identical: True
```

**Same 42 verdicts. Exit 0 or exit 1.** All three night-14 comparisons carry the same `created`,
so "the newest" cannot break the tie, and C17's contract pins no order.

> **SURVIVES; WEAKENED on "hidden".** Run history *does* say *"The banner above … report the
> last comparison this log records"* — but that sits ~500 lines below the banner, and **the exit
> code is consumed by a machine that reads no prose.**

## S3. There is no driver, no committed showcase artifact, and no test that renders one

`git ls-files | grep -i showcase` returns two scripts, three data files and two test files. **No
evidence log. No HTML.** `python scripts/showcase.py` prints a schedule table and exits.

> **SURVIVES as confirmation, not discovery** — `showcase.py:1226` and the plan's R29.3 both say
> so. Nothing gates on it, while `COMPATIBILITY.md`, `JOBS.md` and the plan all describe the
> showcase as what a stranger reads first. **The most likely way this ships wrong is that
> someone writes the driver in an afternoon and inherits S1, S2 and S4 without noticing any of
> them.**

## S4. Driven the only way the repo currently drives it, the dimension matrix does not render at all

`_drive` and `showcase.py` both point `goldenset_path` at `src/model_migration_kit/data/…`, which
is outside the evidence log's directory, so containment refuses it:

```
There is no per-dimension table. the golden set is recorded as /…/data/showcase_goldenset.jsonl,
which is outside the directory holding the evidence log…
```

**And `#refusal` going 85/85 → 5/85 for candidate B — the entire night-14 argument — is absent.**
Copying the golden set into `work_dir`, which C17's layout *does* specify, restores it.

> **SURVIVES.** Not the driver's bug: the repo's only driver reproduces the failure and the
> contract reproduces the fix. The finding is that **the layout clause is load-bearing and not
> marked as such** — listed as file layout, not as a rendering precondition — while the two path
> constants a driver author reaches for are exactly the ones that break it. The refusal itself is
> explicit and reasoned, which is the standing rule done right. **WEAKENED** on one clause: *"a
> path recorded on another machine is not followed"* — it was recorded on *this* machine minutes
> earlier and is readable.

## S5. 58% of the document is 38 near-copies of three sentences

```
showcase words: 7131   exclusion notices: 38   their words: 4104  = 58%
distinct exclusion paragraphs (normalised on dates): 3
demo words: 2729       its exclusion notices: 0
```

A direct answer to *"does the showcase exercise shapes the demo cannot"* — **yes, and this is
one**: the demo has one comparison and can never produce an exclusion notice.

> **SURVIVES as scale, REFUTED as correctness.** Each notice is accurate and its rationale sound.
> At n=1 a virtue; at n=38 it is the document.

## S6. The one real difference from the demo — the rubric — appears nowhere but as a hash

All six thresholds are **byte-identical** to the demo's. The substantive difference is the rubric,
whose adoption moved two published p-values. `grep -ni "showcase_rubric\|demo_rubric"` over the
rendered page: **no output**. Provenance names the evidence log, golden set, judges and config
**by path**, and the rubric **by hash alone** — so the one artifact whose identity is the reason
`showcase.toml` exists is the one a reader cannot name from the page.

## S7. The fourteen-night series exists only as 42 unlabelled rectangles

```
elements: {'rect': 42, 'line': 43, 'text': 0, 'title': 1, 'desc': 0}
text contents: []    aria: []
```

**Zero `<text>` elements** — and the SVG ships a dead CSS rule for them. The numbers live only in
`data-*` attributes: machine-readable, invisible to a sighted reader *and* to a screen reader.
The prose beneath spends ~120 words on the axis's integrity — *"The axis is time, not run
number, so a three-week gap … is drawn as three weeks"* — describing an axis with no tick, no
date and no scale.

> **SURVIVES, scoped.** A sighted reader still gets green/amber/red against a dashed floor line,
> so they are not misled about *what happened*, only unable to read *when* or *by how much*.

## S8. Candidates A and C render as identical rows, which `showcase.py` explicitly asked the renderer to prevent

```
synthetic-candidate-a-v1 | 2026-08-25T03:00:00Z | 93.8% | +1.0 pp | 0.0 days
synthetic-candidate-c-v1 | 2026-08-25T03:00:00Z | 93.8% | +1.0 pp | 0.0 days
```

Identical in every printed field, with nothing on the page explaining it. The docstring
anticipated exactly this: *"a reader diffing two comparison records that agree to sixteen
significant figures has found something that looks exactly like a duplicated record. It is not
one. **Anything rendering this log should say so where the two rows appear.**"* It does not.

> **One sub-claim dropped honestly:** the docstring says "9 of 99 leaf fields"; I measure 8 of
> 170. The flatteners almost certainly enumerate different things (JSON payload vs dataclass), so
> the counts are not comparable — **dropped**, while the substantive claim reproduces exactly.

## S9. "A document seeded one night at a time" — WEAKENED, and the detection is exemplary

The clock-asymmetry sentence *"39 of the 42 comparisons record a created date on a different UTC
day"* is **correct, and this is its first-ever live trigger** — the plan records that path as
having no trigger in the repo. It fires exactly as specified.

> **WEAKENED, a phrasing complaint on an otherwise exemplary disclosure.** This document was
> seeded in **10.15 s in one process**. The gap exists because the generator patches `utc_now` —
> the honest thing the plan asked to be disclosed — and *"seeded one night at a time"* reads like
> a fortnight of CI. The surrounding text does say the dates "are the seed's".

## Checked and sound

**No hand-written record anywhere.** **Determinism:** two independent 14-night runs give the same
42 verdicts and per-run pass counts. **`--check` on the golden set is byte-exact** (96 items, 64
with a reference, 102 tag-slots over 96 items — the two-tag arithmetic renders). **The C18
synthetic band fires and cannot be missed.** **Latency is correctly absent, not zeroed** — the
standing rule done right. **Every threshold echoes with its source file.** **The seed is fast**,
so it will not rot.

---

# Second pass: the document read independently

A second agent read the rendered showcase without seeing the first's provenance trace. It
confirmed S1–S4 from the other direction and found four things the first pass did not. Both
had to reconstruct the document, since no shipped command produces it.

## S10. The dimension table's parts sum to **510 of 480**

The prose claims it *"breaks the banner's own number down by tag"*. Column totals parsed from
the rendered table:

```
                 baseline   cand-c    cand-a    cand-b-v2
column totals    475/510    480/510   480/510   400/510
items                 102        102       102        102
```

against the banner's `445 / 480` and `450 / 480` on the same page. **Every denominator inflated
by exactly 30 completions, every item count by exactly 6.** Verified against the shipped golden
set directly:

```
items: 96   tag slots: 102   distinct tags: 6   per-tag counts: [17,17,17,17,17,17]
multi-tagged items: 6
  synthetic-extract-06     [extraction, instruction-following]
  synthetic-summarise-09   [summarisation, refusal]
  synthetic-summarise-13   [summarisation, multi-step]
  synthetic-format-16      [instruction-following, classification]
  synthetic-multistep-05   [multi-step, extraction]
  synthetic-refuse-04      [refusal, summarisation]
-> 6 rows x 17 items = 102 item-slots over 96 real items; at 5 draws, 510 vs 480 completions
```

Nothing discloses the overlap. The same page prints *"96 items, 64 with a reference, **0
untagged**"* directly above a tag distribution summing to **102**.

> **CONFIRMED — and the project already knows.** `synthetic-summarise-09` is the exact item its
> own test names: `test_the_refusal_collapse_bottoms_out_at_five_of_eighty_five_because_one_item_borrows_the_tag`.
> **Understood in the tests, undisclosed on the page.** The demo's version of this is 65/70 vs
> 55/60; at 96 items it is 510 vs 480 and correspondingly harder to catch by eye.

## S11. "runs rigor says would clear the floor: 931" is a **completion** count labelled as runs

Only reachable in a REVIEW render, so the demo can never exercise it. Recomputed independently:

```
n=  480 (successes 440)  one-sided 95% Wilson lower = 0.893508   -> misses the 0.90 floor
n=  931 (successes 853)  one-sided 95% Wilson lower = 0.900050   -> clears it
```

**931 is the number of completions.** The document uses "run" consistently for a 480-completion
invocation — *"Run history"*, *"run artifacts"*, *"the newest **run** in this field"* — so 931
runs of this golden set would be **446,880 completions**, a factor of 480 out. The banner on the
same page uses the right unit (*"collect more **completions**"*), and the project's second pinned
value behaves identically (435/480 → 6,364 completions, not runs).

> **CONFIRMED.** The value is correct; the label is wrong. This is **the only actionable number a
> REVIEW gives a reader** — what to do next — and it is off by the size of the golden set.

## S12. The completeness sentence over-counts model text by 677 characters, 508 of them prompts

> *"Every one of the 3 changed item(s) carries its full outputs: **1,212 characters of quoted
> model text**… **The figure above counts what the models produced.**"*

```
model outputs, all 30 draws, both sides      535
golden-set input text for the 3 items        508     <- no model produced these
candidate-side judge reasons, deduplicated   169
                                            1212  ✓   (and 107 + 508 + 169 = 784 ✓)
```

**44% is model output; 42% is prompts.** Two further inconsistencies inside one number: it counts
every one of 30 output draws but only *deduplicated* judge reasons, and includes candidate-side
reasons (169) while excluding baseline-side (164). The demo's over-count is 721; here 677.

## S13. The NO-GO and the REVIEW exist on the page only as coloured 7×7 squares

In the GO document, the words "NO-GO" and "REVIEW" appear **nowhere as verdicts** — three hits
total, all in the appendix's rule definitions. The only rendering is inside the SVG:

```
<rect class="review" … data-verdict="REVIEW"/>   <rect class="nogo" … data-verdict="NO-GO"/>
.go{fill:#1a7f37}  .nogo{fill:#b3261e}  .review{fill:#a76b00}
```

Checked against the raw HTML: **no `aria-*`, no `<desc>`, no per-marker `<title>`, zero `<text>`
elements** in the timeline. The chart's single accessible name never says one marker is a NO-GO.

> **CONFIRMED — JOB-6's finding inverted.** That one was *rendered, but only to a screen reader*;
> this is **rendered, but only to a sighted reader, and only as a hue.** With no tick anywhere,
> *"a three-week gap … is drawn as three weeks"* renders identically to a 7.1-second gap.

## S14. Smaller, confirmed

- **13 of 42 chart markers are drawn exactly on top of another** — 42 markers, 29 distinct
  (x, y). Three comparisons share a night's timestamp hence its x, and two models are scripted to
  the same failure count on 13 of 14 nights. A reader counting squares against the stated 42
  finds 29. *(The NO-GO and REVIEW markers are not among the hidden ones — checked — so this does
  not compound S13.)*
- **The REVIEW document says the sample is underpowered and powered in adjacent rows.**
  `regressed / floor cleared / underpowered | no / no / yes` sits directly above `powered for the
  configured effect | yes (480 observed per side, roughly 131 required)`. Two genuinely different
  questions, no number wrong, and **nothing says they are different quantities**.
- **The showcase never demonstrates the report's central design rule.** Across all 42
  comparisons: `parse_failures 0, imputed 0, missing_scores 0, unstable 0, warnings 0, parts>1 0`.
  **Every zero on the page is a measured zero.** The one artifact built to show the tool off
  never exercises *"an absence must not render as a measurement"*.

## S15. What the second pass expected to find wrong and did not

**The timestamp asymmetry is disclosed, accurately, unprompted — REFUTED as a defect, and it is
the strongest thing in the document.** With the plan's dating applied, the synthetic band gains a
sentence that is absent without it: *"All 42 comparisons record a created date on a different UTC
day from the evidence record carrying each… A document seeded one night at a time has that gap by
construction… and it is disclosed here rather than left for a reader to find and distrust."* So
the fourteen-night arc **is** scripted with fabricated dates, and the document says so on its own
initiative. Only quibble: the top band carries the adapter count but not the clock sentence.

**Also refuted:** *"no multiplicity correction across the 42 comparisons"* — the page never claims
one, and Holm within a comparison is correct (identity at one judge). *"The candidate table
double-counts candidate B (v1 and v2)"* — distinct model ids under one key, and `age in this
field` discloses that b-v1's row is a night stale.

**Recomputed and exact:** all eight Wilson intervals; Mann-Whitney `0.739639` → scipy
`0.7396387635627311`; night-14 candidate B → `2.426401761042508e-12`; *"roughly 131 required"* →
130.2 → 131; item counts `89/7/0` and `90/6/0` summing to 96; every delta against its own night's
baseline; `4 + 38 = 42`; every `(N)` heading against its contents. **The REVIEW whisker crosses
the floor line and no GO whisker does — checked across all 42.**
