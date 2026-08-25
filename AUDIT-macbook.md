# Second-operator document audit — model-migration-kit

**Operator:** second machine (macOS 15.5 / Darwin 25.5.0, arm64), 2026-08-24.
**Branch:** `review/2026-08-24` at `0dcb581`.
**Method:** read the *rendered document*, not the code. Every finding carries the rendered
text verbatim, a reproducible fixture, and what the evidence file actually held.

## Adversarial review — read this before the findings

An agent was tasked solely with **refuting** everything below, defaulting to REFUTED when
uncertain. It is the most useful thing in this audit, and it cost me a lot. Verdicts:

| | Findings |
|---|---|
| **REFUTED / already scheduled (9)** | 10, 11, 12, 18, 20i, 20l, 33, 41.9, 41.15 |
| **WEAKENED (25)** | 1, 3, 4, 5, 7, 9b, 14, 16, 17, 19, 20a-f, 20h, 20k, 21, 25, 27, 28, 30, 34, 38, 39, 41.1, 41.8, 41.13, Robustness |
| **SURVIVES (20)** | 2, 6, 8, 9a, 13(part), 15, 20g, 20j, 22, 23, 24, 26, 29, 31, 32, 35, 36, 37, 40, 41.2-7/10-12/14 |

**The structural criticism, which is fair and which I have not fully repaired below.** Tier 2
presents its breaches as *"confirmed"* over "176 leaf paths, 2,391 renders", which reads as if
these were states the tool reaches. **Most are not.** There is exactly one writer of the
comparison payload (`comparison.py:907` -> `comparison_payload()`), and it writes every key
unconditionally; rigor writes `failures`, `min_rate` and `alpha` on both branches. So most of
Tier 2 is an argument about robustness to a *foreign or future* writer — a position this report
takes explicitly **once**, in finding 35, and nowhere in Tier 2 itself. Sixteen items owe that
concession: 5, 14, 17, 19, 20a, 20c, 20d, 20e, 20f, 20h, 21, 29 (case A), 38, 41.1, 41.2 and
Robustness. Read them accordingly. **Without that caveat a fix pass would spend a chunk
hardening reads against a writer that does not exist** — and finding 35 (no schema guard on the
evidence log) is the finding that would actually make them reachable, so it should be read as
Tier 1 and the prerequisite for the rest.

**Demote out of Tier 1:** 1, 3, 4, 5, 7, 14 (to Tier 4); 16 folds into 22. **Move** 26 to Tier 4.
**Promote:** 20j (needs no payload edit at all — just delete two artifact files), 20g, and 35.

**Where the plan already speaks and I failed to cite it:** 4 (R31.4 records it open), 12 and 13
(R33 / C14c, committed the day before this audit), 24 (C14a deferred it and C14c does not pick it
up), 27 (R23.2, ratified), 34 (R20 created `stale_after_days` for exactly this), 39 (R5, known and
explicitly *not* scheduled).

**Five findings got stronger under attack:** 2 (the terminal prints the exact `0.000 / 0.000` row
the HTML says it omitted — better evidence than I gave), 6 (`quoted_chars`' own docstring says
*"the post-truncation size and **not** the size of what the models actually said"*, which is the
opposite of what the page prints), 8 (zero `<text>` elements confirms screen-reader-only, and
`_widest_judge`'s docstring shows the two selection rules are deliberately different), 9a
(re-derived independently, exact), and 20j.

**Corrections to specific numbers below:** finding 13 should read **4 of 9** plotted markers, not
"3 of the 10", and "one **line**" is wrong — the caption says *"no line joins the markers"*.
Finding 25's second half overreaches: the truncated prefixes visibly differ and the group's full
hash *is* on the page; only the excluded run's own hash is uncheckable. Finding 39's plan citation
is `:2101-2102`, not `:2095`.

**Status of this commit.** These are the findings that were confirmed and independently
re-verified at the time of writing. A second commit will follow on this branch with five
verification passes still running: an adversarial pass whose only job is to **refute** the
findings below (expect some to be downgraded or withdrawn), mutation testing of the suite for
fixture monoculture, the four project gates run on macOS, a full benchmark, and a
browser/accessibility render. **Read the ranking as provisional until that second commit
lands.**

---

## Setup, and the number the brief asked for

The brief's setup block does not work verbatim here: `python3` on this machine is Anaconda
**3.9.13**, and `pyproject.toml` sets `requires-python = ">=3.10"`, so `pip install -e
".[dev]"` fails. Used `/opt/homebrew/bin/python3.12`.

```
$ /opt/homebrew/bin/python3.12 -m venv .venv
$ .venv/bin/python -m pip install -e ".[dev]"      # opik-rigor 0.2.0 resolved from PyPI
$ .venv/bin/python -m pytest -q
2206 passed in 34.02s
```

**2206 passed, zero failures** — exactly the number the brief predicted. No divergence.

Rendered: the bundled demo, plus purpose-built evidence logs covering multi-run /
multi-date / multi-candidate fields, runs that must be excluded (foreign golden set,
foreign judge panel, unrecorded candidate id, superseded run), and runs recording no pass
rate, no floor and no adapter. Roughly 120 rendered documents across the audit.

`migkit demo` deletes its work directory, so where the demo's own evidence log is quoted it
was rebuilt via `model_migration_kit.demo.run_demo()` into a directory under my control —
identical golden-set, config and judges hashes, differing only in timestamps and paths.

---

# Tier 1 — the document's headline claim is not supported by its own evidence

## 1. The demo's NO-GO is manufactured by counting each of 12 answers five times

The document every new user reads reports a statistically significant regression. At the
unit the data actually has, there isn't one.

**Rendered:**

> "Judge 'accuracy' shows a statistically significant regression after Holm-Bonferroni
> correction across judges."
> "Mann-Whitney p-value (alpha 0.050, Holm threshold 0.0500) | **0.007843**"
> "rule 1: any judge shows a Holm-corrected significant regression -> NO-GO **<-- fired on this run**"
> "Exit code a CI system would have received: **1**"
> "5 draws per item over 12 items, giving 60 / 60 completions… **A migration decision needs
> a distribution per item rather than a single shot: one sample cannot separate 'the
> candidate is worse' from 'the candidate was unlucky once'.**"

and, eight separate times on the same page:

> "**all 5 draws identical**"

**What the evidence holds.** The demo's adapters are scripted `Mapping[str, str]` and the
judge is a pure function of the text, so there is no variance to sample:

```
fake-baseline-v1 : 12 items, items with >1 distinct output: 0
fake-candidate-v1: 12 items, items with >1 distinct output: 0
```

**Independent recomputation** (scipy, from the scripted responses):

```
each answer counted 5x (what the page reports): u=1450.0  p=0.007843
the 12 independent answers:                     u=58.0    p=0.152507
alpha = 0.05 -> rule 1 fires at n=60: True; at n=12: False
```

`u=1450.0, p=0.007843147236661034` reproduces the recorded payload to the last digit, so
the recomputation is faithful. The honest figure over the 12 independent observations is
**p = 0.153**, three times alpha. Rule 1 would not fire.

The same inflation runs through every interval: the banner prints `wilson_interval(45, 60)`
= `[0.6277, 0.8422]`; over items it is `wilson_interval(9, 12)` = `[0.4677, 0.9111]`. **The
printed interval is 48% of the width the data supports.**

**Wrong conclusion:** that a 60-versus-60 comparison detected a regression clearing a 5%
bar, and that exit code 1 was earned. The document holds every fact needed to see otherwise
— it prints "all 5 draws identical" eight times — and never connects them. The one sentence
that could have disclosed it asserts the **opposite**: it tells the reader the five draws
carry distributional information.

*Stated fairly:* the machinery is real and the report faithfully echoes what
`comparison.py` computed. The defect is the document's framing of *n* as evidential depth
beside its own statement that the draws are identical, with nothing reconciling them.

## 2. "Latency — Not measured", printed over 120 recorded timings

**Rendered:**

> "**Not measured.** Both sides of this comparison ran on scripted adapters, which return
> their answers without calling a provider, so every timing here **would be** a few
> microseconds of local dictionary lookup. The table is omitted rather than printed as
> zeros: a row that reads `0.000 / 0.000` is not a fast model, **it is the absence of a
> measurement**, and a reader should not have to work that out."

**What the evidence holds** — the demo's own `migkit.comparison` payload:

```json
"latency": {
  "baseline":  {"median": 4.169996827840805e-07,  "n": 60, "p90": 7.960989023558797e-07},
  "candidate": {"median": 4.1650037019280717e-07, "n": 60, "p90": 1.083100687537808e-06}
}
```

120 timings, 60 per side, median and p90 computed and recorded. The suppression is keyed on
the adapter's *name*, never on whether a measurement exists:

```jinja
{% if model.baseline.is_fake and model.candidate.is_fake %}
```

**This is the project's central rule failing in the mirror direction, in the section that
quotes the rule back at the reader.** The page hides a real measurement by describing it as
an absence. Two further errors inside the same paragraph: the counterfactual "would be a
few microseconds" is wrong by an order of magnitude (recorded medians are 0.42 µs, 0.42 µs
— sub-microsecond); and "a row that reads `0.000 / 0.000` … is the absence of a
measurement" is exactly inverted here, where it would be a real measurement that rounds to
`0.000` at three decimals.

## 3. The appendix tells a *real* comparison that its quality difference "was written into the script"

The brief's own exemplar defect, alive one sentence past its fix, in the paragraph the fix
was written for.

**Rendered** — two-comparison log whose headline is real:

> "This document draws scripted runs, and **the comparison in front of you is not one of
> them**: neither of its sides names a Fake adapter… **The only real thing in this document
> is the machinery**… **The quality difference they measure was written into the script.**"

**Evidence:** the headline record holds `"adapter": "AnthropicAdapter"` /
`"OpenAICompatAdapter"` on `claude-sonnet-4-5-20250929` → `claude-opus-4-5-20251101`.

**Why:** `_scripted_paragraph` (report.py:2852) correctly ships *two* openings — R29.1's fix
— then joins `_MACHINERY_IS_REAL` onto **both** unconditionally:

```python
return " ".join(one for one in (opening, _counted_paragraph(provenance),
                                _MACHINERY_IS_REAL, _dated_sentence(provenance)) if one)
```

"they" resolves to the headline comparison. The docstring's claim that this sentence "is
true in both of R29.1's cases" is false in the second. A reader is told the real migration
decision in front of them is a fabrication.

## 4. `<title>` shouts FAKE MODELS over two real production model ids

> `FAKE MODELS — NO-GO — claude-sonnet-4-5-20250929 to claude-opus-4-5-20251101 — model-migration-kit`

`_warned_title` (report.py:4901) keys on series-scoped `is_demo` and prefixes a
headline-scoped string. The band immediately below **gets it right** — it appends "; 1 of
the 2 comparisons…" — but the title carries no such clause. Per `_FAKE_TITLE_PREFIX`'s own
docstring this is the surface that "survives being pasted into a chat window as a link
preview", so the document asserts to everyone who does *not* open it that two real
production models are fake.

## 5. The recorded `exit_code` is discarded and silently recomputed

**Rendered:** "Exit code a CI system would have received: **1** — decided by rule 9".
**Evidence:** `{"verdict": "NO-GO", "exit_code": 0, "decided_by": "rule 9"}`.
The recorded `0` appears **nowhere** in the document.

```python
@property
def exit_code(self) -> int:
    return Verdict.exit_code(self.verdict or Verdict.ERROR)     # report.py:1923
$ grep -n '"exit_code"' src/model_migration_kit/report.py
(no match — the payload field is never read)
```

**A NO-GO that exited 0 is exactly the pipeline mis-wiring a gate exists to catch**, and the
report erases the only field that would show it. The row is labelled "Exit code a CI system
would have **received**" — a claim about what happened, answered with a recomputation of
what should have.

## 6. The completeness section certifies completeness that did not happen

Fixture: the standard scenario with `candidate_output="X"*50000`.

> "Every one of the 3 changed item(s) **carries its full outputs**: 60,408 characters of
> quoted model text against a budget of 10,000,000."
> "Of those, 12,408 characters are printed below … **The figure above counts what the models
> produced, which is what completeness is about.**"

and, three times further down: "… **truncated at 4000 characters**".

Counted off `candidate.jsonl` for those three items: **750,000 characters**. Outputs are
truncated at `max_output_chars=4000` *before* the budget sees them, so per-block truncation
is invisible to the sentence that certifies completeness. The figure the page calls "what
the models produced" is **12× short**, and comparing it to a 10,000,000 budget tells the
reader there was ample room — the truncation had nothing to do with the budget.

**Even in the demo, where nothing is truncated, the certificate is wrong.** It says 5,821
characters; the models produced 5,100. The difference is exactly `426` characters of
golden-set prompts (written by the golden-set author) plus `295` of judge reasons (written
by the judge, not the models under test): `426 + 5100 + 295 = 5821`. 12.4% of a figure the
page calls "what the models produced" is not model output.

Three further shapes: with both candidate artifacts deleted, the page says *"Every one of
the 3 changed item(s) carries its full outputs: 351 characters"* while all three rows read
`Candidate outputs (0) / No candidate outputs available.` A flip naming an item present in
no artifact has `quoted_chars == 0`, so it always "fits" and is always counted as embedded.
And with a truncated run, `item-11 · accuracy 0/5 -> 5/5` sits directly above
`Candidate outputs (0)`.

*(The **capped** branch is honest — verified at six budget levels over 8 rows, including
`0 of 8`. The false certification is in the uncapped default path.)*

## 6a. A truncated judge reason is disclosed nowhere — and the judge reason is why the item regressed

```python
def _reasons(...):
    ...
    text, _ = _truncate(chosen, limit)          # report.py:2811 — the cut flag is discarded
```
```python
truncated=bool(text_cut or base_cut or cand_cut),   # report.py:2760 — three flags, no reason_cut
```

`_truncate`'s own one-line docstring:

> `"""Cut to ``limit`` characters and say so. **Invisible truncation misquotes.**"""`

A 9,047-character judge reason renders as 4,000 characters, the row's `truncated` stays
`False`, and `"truncated at"` appears nowhere for it. The reviewer reads a cut-off sentence as
the judge's whole reason for the regression. This is the one quotation on the page whose entire
purpose is explaining *why*, and it is the one quotation whose truncation the page cannot say.

## 6b. Truncation manufactures "all 5 draws identical" out of five different draws

Fixture: `item-03`'s five candidate draws share a 4,160-character prefix and end in five
distinct answers.

```
the 5 draws in the artifact: FINAL ANSWER 0 … FINAL ANSWER 4
occurrences of 'FINAL ANSWER' in the rendered document: 0
the page, line 254:  all 5 draws identical
```

Cut to `max_output_chars = 4000`, the five become byte-identical, `_draws()` collapses them, and
the document makes an **affirmative false statement about candidate stability** — the very fact
`_Draws`' docstring says the collapse exists to show. The block's truncation *is* disclosed once,
at the foot of the row; the determinism claim above it is not qualified.

## 7. Every path in the demo's provenance block points inside a directory the demo has already deleted

> "A source that is a file is named by its filename here; its full path is shown once,
> whole, **and where it can be checked** — under `config` in 'What was compared', above."

```
$ .venv/bin/python -m model_migration_kit.cli --quiet demo --out demo.html
$ ls /var/folders/.../T/migkit-demo-6lnfd7mt
ls: ... No such file or directory
```

`cli.py:599-601` runs `shutil.rmtree(work_dir)` unless `--keep` or `--work-dir`, *after*
`_render`, so the HTML is written with live paths baked in and the directory is then
removed. Six absolute paths and three content hashes are printed as a verification trail;
every one dead-ends. The prose does not merely omit this — it makes the opposite promise.

## 8. The banner's bar is drawn for a different judge than the banner's verdict

Under `NO-GO` and *"judge 'accuracy' shows a significant regression"*, the inline SVG is

> `<title>candidate safety: pass rate 91.9%, interval 81.8% to 98.0%, floor 55.0%</title>`

with the rate line comfortably clear of the floor. `_banner_bar` reads `series[-1]`, which
selects `_widest_judge`; everything else selects `judges[0]`. report.py:1665 warns about
exactly this:

> "the tag matrix and the spot check must be about the same judge (R26.3): **one document
> selecting its judge in two places is one edit away from selecting two judges.**"

There are three selection sites. The SVG emits no visible text, so a sighted reader sees a
bar clearing its floor directly under NO-GO, and the only disclosure reaches screen readers.

## 9. "This breaks the banner's own number down by tag" — two production-reachable ways it does not

**(a) Multi-tagged items are counted twice — in the shipped demo.** Baseline column:
20/20, 15/20, 10/10, 20/20 → **65 / 70 = 92.9 %** against the per-judge table's
**55 / 60 = 91.7 %**. Candidate: **50 / 70 = 71.4 %** against **45 / 60 = 75.0 %**.

The demo golden set holds `extract-04 ["extraction","multi-value"]` and
`refuse-04 ["refusal","multi-value"]` — 14 tag slots over 12 items, 2 items × 5 draws = 10
completions counted twice. 70 − 10 = 60. 65 − 10 = 55. 50 − 5 = 45. Exact.

The double count is **correct**. The missing disclosure is the defect, and the plan required
it twice, in the Reviewer instructions (plan `:1004`, `:2621`):

> "…contributing to both tags is correct, and it means the column totals exceed the item
> count. Check the function does not 'fix' that by dividing, **and check the caller is told
> so the document can say it.**"

The function does not divide. The document never says it — `grep -i "more than one
tag|counted twice|counted in both|overlap|do not sum|partition"` over the rendered demo
returns **no match**. Directly above the table, "golden set — **12 items**" sits beside a
tag distribution summing to **14**, in a different section, unexplained.

**(b) Imputed completions are in the gate and invisible to the matrix** — true of **any run
with a timeout**. `comparison._counted` keeps imputed records in the gate's `n` ("a model
that times out has told us something"), while `dimensions.py:974-976` states that "an
imputed or unparseable record never calls `evaluate()` and so emits no" judge verdict.

## 10. "powered for the configured effect: yes (60 observed per side, roughly 137 required)"

> **REFUTED by adversarial review.** Fixture artifact, and **I misquoted the page.** `tests/test_report.py:626-635` sets `mw_powered: True` beside `power.powered: False` deliberately — SETUP.md's tripwire firing as designed. The real demo prints, correctly, `powered for the configured effect | no (60 observed per side, roughly 140 required)` and *"so this judge is **not** powered for the question"*. My quote of that sentence dropped the word **not**; the template is `{'powered' if one.mw_powered else 'not powered'}` (report.py:3098-3100). `comparison.py:1137` and `:1168` set both from one variable. **Delete.** The one-line residue worth keeping: `power.powered` is loaded at report.py:2606 and never rendered.

The word and the numbers beside it answer different questions from different fields.

| `mw_powered` | `power.powered` | `underpowered` | rendered word |
|---|---|---|---|
| false | true | false | **no** |
| true | false | true | **yes** |
| null | false | false | **—** |

The word is `judge["mw_powered"]` (report.py:2605 → template 4375); the numbers are
`power.n_observed` / `n_required`. **`power.powered` — the payload's own answer to the exact
question the row asks — is loaded at report.py:2606 and never rendered.** The appendix is
worse, because it makes the contradiction an explicit inference:

> "60 completions per side observed against roughly 137 required…, **so this judge is
> powered for the question.**"

*Honest caveat:* comparison.py sets both from one variable, so this tool's own payloads will
not disagree today. But `comparison._FLAG_ALIASES` accepts a top-level `"powered"` alias the
verdict rules honour, and with the flag recorded that way the report prints **"—"** for a
flag rule 4 acted on.

## 11. In the demo, one arm is underpowered and needs 931 more runs; the page prints "no"

> **REFUTED by adversarial review.** A misread of the mechanism, mine. `comparison.py:1032-1035` derives `floor_cleared`, `underpowered` and `runs_needed` from the **candidate** gate only — `_floor_power` reads a *failed* `assert_pass_rate` and nothing is read when the gate passed. The baseline's `underpowered: true / runs_needed: 931` are rigor's incidental output on its own gate and no rule reads them. So `colspan="2"` is **correct** (none of the three is a per-side fact), and my self-derivation argument was backwards: 0.8385 is the *baseline* bound, while rules 2/3 read the candidate's (0.6486 < 0.90, underpowered false) — **rule 2's condition genuinely holds and the page is self-derivable as printed.** **Delete.** Residue: the appendix says "any judge's one-sided lower bound" without saying *candidate's*.

```
baseline : underpowered=True   runs_needed=931   lower_bound=0.8385
candidate: underpowered=False  runs_needed=None  lower_bound=0.6486
judge-level roll-up printed by the page: underpowered=False
```

> "regressed / floor cleared / underpowered | yes / no / **no**"   ← `colspan="2"`

A cell spanning both model columns, under headers `baseline | candidate`, describes one arm.
The other arm's opposite state and its **931-runs** figure appear nowhere in the document.
This breaks self-derivation: rules 2 and 3 turn on this word plus the lower bound, and a
reader reading `0.8385 < 0.90` with "underpowered: no" concludes **rule 2** fires (NO-GO)
where rigor's record says **rule 3**'s condition holds (REVIEW). Rule 1 fires first, so the
verdict stands and the reasoning does not.

## 12. Five computed disclosure surfaces are wired to nothing — ALREADY KNOWN, SCHEDULED AS C14c

> **CORRECTION, and it demotes this finding.** I ranked this as a headline discovery. It is not
> a discovery: this project already knows it, counted it the same way I did, and has scheduled
> the fix. `e2b0614` ("R33: C14c's contract, and where the line's disclosures live"), committed
> the day before this audit, states: *"Four fields are still computed and unread, **counted
> inside the template rather than assumed**: spot_check, multiplicity, parameter_strip and
> trend, all 0."* And `c04bf15` ("C14b: fifteen tests for four elements that render as nothing
> today") says *"Six merged, reviewed, fully tested chunks are invisible to a reader, and every
> one of them is a value sitting on `ReportModel` that no `{{ }}` reaches."* The plan schedules
> it at line 5623-5624 and 5750. **Read this section as independent confirmation from a second
> operator, not as a new defect.** What follows is still worth keeping because it measures the
> *reader-facing consequence*, which the rulings record as a fix to schedule rather than as a
> document that currently lies.

```
$ .venv/bin/python -c "
import re, dataclasses, model_migration_kit.report as R
env = R._environment(); src = env.loader.get_source(env, R._TEMPLATE_NAME)[0]
for f in dataclasses.fields(R.ReportModel):
    if not re.search(r'\b(model\.%s|%s)\b' % (f.name, f.name), src): print(f.name)"
completion_rates
item_counts
dated_apart
spot_check
trend
parameter_strip
multiplicity
```
(`evidence_hash` also lists but is a false positive — it renders via `model.hashes.evidence`.)

Confirmed on the output side across four rendered documents: the multiplicity refusal note,
the trend caveat, the spot-check sentence and the parameter strip appear on **none**. All
five refusal grounds in `series._refused` are unreachable from the HTML. report.py:1421-1433
argues, about the trend caveat:

> "So **every report rendered today carries R21.5's caveat**… **That is correct and is not to
> be tuned down.** … Suppressing it here — a flag, a default declaration, a filter on the way
> out — would restore the silent default R21.5 rejected, and would do it **in the wiring,
> which R21.5 names as the one shape of this defect nobody would find.**"

No report rendered today carries that caveat. **This finding causes 13 and 22.**

## 12a. The suite tests the producer and the page, and never the seam between them

This is *why* finding 12 shipped, and it is checkable in one command.

```
$ .venv/bin/python - <<'PY'
import re
src = open("tests/test_report.py").read()
funcs = re.split(r'\ndef (test_\w+)', src)
pairs = list(zip(funcs[1::2], funcs[2::2]))
for kw in ("spot_check", "trend", "multiplicity", "parameter_strip"):
    hits = [(n, b) for n, b in pairs if kw in b]
    renders = [n for n, b in hits if "_html(" in b]
    print(f"{kw:16s}: {len(hits):2d} test(s) mention it, {len(renders)} of which render HTML")
PY
spot_check      : 11 test(s) mention it, 0 of which render HTML
trend           : 13 test(s) mention it, 0 of which render HTML
multiplicity    :  8 test(s) mention it, 0 of which render HTML
parameter_strip :  7 test(s) mention it, 0 of which render HTML
```

**39 tests across the four fields; not one of them renders a document.** The suite is not thin
— of 271 test functions in `tests/test_report.py`, 50 render HTML. The gap is exact: these four
fields are asserted on the *producer* (`ReportModel.from_evidence` returns them, correctly, with
the right contents) and never on the *page*. The defect lives in the template binding, which is
the one place nothing looks.

`report.py:1421-1433` predicted this failure mode by name — suppressing the caveat "in the
wiring, which R21.5 names as the one shape of this defect nobody would find". The suite's shape
is why nobody found it: a test that reads `model.trend.caveats` passes whether or not the
template ever mentions `model.trend`.

## 13. The chart draws incomparable runs as one rising series — ALSO ALREADY RULED ON (R33.2)

> **CORRECTION.** R33.2 in `e2b0614` is the ruling on exactly this: *"Trend carries seven fields
> and the timeline renders none of them -- it is model.series, every comparison in the log. So
> R21.5's assumed-lineage caveat exists on every model and reaches no reader: 'assumed' appears
> 0 times in the rendered document."* Known, ruled, scheduled. The one thing below that I have
> not found stated anywhere in the plan is the **measured** consequence: on my ten-run fixture,
> **3 of the 10 plotted markers are runs the same page's own exclusions section names.**

`{% set timeline = model.series | timeline %}` — the chart is fed **every comparison in the
log**, not `model.trend.points`. Fixture: two runs identical but for depth (`n_per_item=1`
vs `25`). The chart draws a NO-GO at 0.60 then a GO at 0.90 under one floor rule, titled
*"Candidate pass rate over 2 run(s)"*. `trend.points` holds **one** point; `trend.excluded`
holds the unrendered sentence *"excluded: 25 draws per item against the group's 1…"*.

In my multi-candidate fixture, **3 of the 10 plotted markers are runs the same page's "Runs
outside the candidate table" section explicitly excludes.** A reader sees a regression climb
into a pass. Markers carry no `data-candidate`, no `<desc>`, no legend, no per-marker
`<title>`: **eight distinct candidate models render as one line labelled "Candidate pass
rate."** There is also no axis — no line, tick, gridline, date label or y-scale in any
render — under body text reading *"The axis is time, not run number"*.

## 14. "Nothing changed" and "no flip analysis exists" render byte-identically

Two logs: one with `"flips": [], "gains": [], "unstable": []` recorded and empty; one with
those keys **absent**. Diffing the rendered documents yields **one differing line — the
evidence hash.** Both print:

> "Every one of the 0 changed item(s) carries its full outputs: 0 characters …"
> "**Flips — items that stopped working (0)**" … "None."

`_change_sections` reads `payload.get(name, ()) or ()`. **"(0) … None." reads as a finding —
nothing regressed.** In the second log, no flip analysis exists in the record.

## 15. "all 5 draws identical" over five completions that never came back

Candidate artifact rewritten with five `{"output": null, "error": "timeout after 30s"}`:

> **Candidate outputs (5)** / `[no output - SampleTimeout: timeout after 30s]` / **all 5 draws identical**

`_outputs()` correctly substitutes a placeholder; that synthesised string then flows into
`_draws()` as though it were a draw. Consequence: that row's "quoted model text" is 366
characters, of which **230 are report.py's own placeholder text** — so a candidate that
times out on every draw makes the completeness figure go *up*.

## 16. `--quiet` silences the FAKE MODELS disclosure entirely

```
$ .venv/bin/python -m model_migration_kit.cli --quiet demo --out q2.html
q2.html
VERDICT: NO-GO (exit 1)
```

That is the entire stdout and stderr. `cli.py:420` skips `render_terminal` wholesale, so the
scripted-models band, the reason sentence, all warnings, the completeness strip and every
truncation disclosure vanish. `--help` promises to silence "progress and the terminal
tables" — a provenance band is not a table and a warning is not an error. **The tool's
central claim is that you cannot get a clean-looking report out of scripted models;
`--quiet` produces one.**

---

# Tier 2 — an absence rendering as a measurement

Found by differential rendering: for each leaf path in the payload, five whole documents
were rendered differing **only** in that field — a plausible value, a genuine measured zero,
the key removed, the key set to `null`, and the parent object removed — then compared byte
for byte. **176 leaf paths, 2,391 renders.** Where the measured-zero and the absent variants
produce identical text, the rule is broken.

*(A methodological note worth keeping: the page prints an `evidence hash` over the whole
file, so a naive page diff finds every pair different and reports zero findings. The hash
must be masked. The first sweep did exactly that and came back empty.)*

## 17. An unrecorded gate floor is silently replaced by the configured one

When the gate recorded no `min_rate` — key removed **or** null — the banner substitutes
`thresholds.pass_rate_floor`. Proven by varying the config floor: the banner prints
`… floor 42.0%` for a gate that recorded nothing. With **both** gone it prints
`floor not recorded` — so the renderer owns the right vocabulary and does not reach it.

A reader concludes the candidate was held to a 42% floor *by the gate that produced this
verdict*. Nothing recorded what floor that gate used, and the config may have been edited
since. `series` already ships `floor_source` for exactly this distinction;
`grep -n floor_source report.py` returns **one** hit — inside a docstring claiming the bar
"can tell that apart from the number that was merely configured". The string never reaches
the page.

## 18. `alpha` removed borrows the regression block's alpha; `alpha: null` does not

> **REFUTED by adversarial review.** `comparison.py:638` is `"alpha": None if self.regression is None else self.regression.get("alpha")` — the key is always present and its value *is defined as* the regression's alpha. The fallback returns exactly what the writer would have written, and the producible spelling (regression None -> alpha null -> em dash) is the correct one. **Delete.**

Set `judges[0].regression.alpha = 0.09`, remove `judges[0].alpha`, and the page prints
`alpha 0.090`. Set `judges[0].alpha = null` and it correctly prints `—`. Two spellings of
the same absence, two different answers.

## 19. The rows that tell a reader what the pass rate excluded cannot say they were never recorded

Rendering a log with `imputed`, `parse_failures` and `item_counts` **removed entirely**,
against one with all three recorded as explicit zeros, the two documents differ in **one
row**:

```
177,178c177,178
<         0 / 0 / 0 |          (item counts, measured zero)
---
>         — |                  (item counts, never recorded — correct)
```

Everything else is byte-identical. So:

> "imputed (failed completions scored at the floor) | **0** | **0**"
> "judge parse failures (excluded from the rate) | **0** | **0**"

reads the same whether the run measured zero of each or never tracked them. Item counts get
this right; the two rows beside them do not. Both are load-bearing: imputation is how a
model that crashes is prevented from beating one that answers badly, and "excluded from the
rate" is the reader's only statement about the denominator.

## 20. Other confirmed absence-as-measurement breaches

* **`successes` absent or null → `0 / 20`.** `passes=int(gate.get("successes") or 0)` —
  `rate`, `interval` and `lower_bound` all carry explicit `None` guards in the same
  constructor; `passes` alone does not. Collides even on a single-comparison log.
* **`n` absent, null or zero → `0 / 0`,** and silently discards a recorded rate, interval
  and lower bound that are sitting in the payload.
* **An unrecorded baseline `failures` → a 100% baseline pass rate, and every delta moves.**
  Four runs differing *only* in how the failure count was recorded:
  ```
  cand-A-control        {"n":20,"successes":17,"failures":3,    "pass_rate":0.85}
  cand-B-zeroFailures   {"n":20,"successes":20,"failures":0,    "pass_rate":1.0 }
  cand-C-failuresAbsent {"n":20,"successes":17, <KEY ABSENT>,   "pass_rate":0.85}
  cand-D-failuresNull   {"n":20,"successes":17,"failures":null, "pass_rate":0.85}
  ```
  > "baseline pass rate **100.0%** — one reading, taken from the newest run in this field."
  > "… cand-A-control 17/20, cand-B-zeroFailures **20/20**, cand-C-failuresAbsent **20/20**,
  > cand-D-failuresNull **20/20**."

  and deltas of **−31.5 / −46.5 / −46.5 / −46.5 pp** for four candidates whose measured pass
  rate is *identical* (53.5%). `_baseline_pass_rate` computes `(n − failures)/n` through
  `_count`, which maps both absent and null to `0` — and for a *failure* count, 0 is the best
  possible score. It guards counts that are *impossible* (`not 0 <= failures <= n`) and not
  counts never recorded; `successes` is in the payload and never read. `failures` is a
  derived key (`n − successes`), so this needs no inconsistency anywhere. The same document
  then prints run D's baseline pass rate as **100.0%** in the candidate-table header and
  **85.0%** in the per-judge table.
* **`underpowered` is the one flag of three that coerces.** Three adjacent lines
  (report.py:2598-2600): `regressed` and `floor_cleared` use `_bool_or_none` (null → `—`);
  `underpowered=bool(raw.get("underpowered", False))` (null → `no`). They render side by
  side as `— / — / no`: two honest absences and one fabricated measurement, in one row, in
  both the terminal and the HTML. Decision-table rules 3 and 4 turn on it.
* **`unstable: []` and no `unstable` key** are indistinguishable, under prose promising
  *"Listed even when nothing moved"*.
* **`warnings: []` and no `warnings` key** are indistinguishable.
* **`NaN` prints as `nan%` and `[nan, nan]`.** report.py:3576-94 documents that a bare `NaN`
  survives `json.loads` and guards the SVG against it; `_number` passes it to `_pct` / `_num`
  unguarded, so the table prints `nan%` while the banner, for the same field, says "not
  recorded".
* **`power.n_observed` absent → `None`, and the conclusion is drawn over it:**
  *"Judge accuracy: **None** completions per side observed … **so this judge is powered for
  the question**."* Same for `item_counts.* = null` → `None / 1 / 2`, `thresholds.* = null` →
  `alpha | None |`, and `flips[*].judges[0] = null` → `item-03 · #arithmetic · None`.
* **An unrecorded `n_per_item` renders as "0 draws per item"** — `int(payload.get(
  "n_per_item", 0) or 0)`, absence *is* zero. Eleven words later the same sentence gets the
  expected count right, printing `?` "when nothing recorded the total".
* **"failed completions: candidate 0" for an artifact that could not be read.**
  `_run_summary`'s `else:` branch hard-codes `failures = 0` when both artifacts are `None`.
  `completions` is rescued by printing `0 / 60`; this row has no such pairing.
* **`no reason recorded`** is printed both for a judged artifact that was read and held
  `"reason": ""`, and for one that could not be opened at all.
* **A whitespace-only `command`** renders as an empty box — `"   "` is truthy, so the
  prepared "not recorded in the evidence" phrase is skipped.

## 21. And the mirror: a *recorded* zero rendered as an absence

`n_per_item: 0` recorded on a side renders as *"baseline run does not record how many
completions were expected"* — a statement that is false about the payload, inside the
completeness strip. Same for the demo's latency (finding 2). The rule cuts both ways and is
broken both ways.

---

# Tier 3 — a disclosure that exists, computed, and is never shown

## 22. The terminal never discloses that runs were excluded

On a 10-comparison log with 4 exclusions, the HTML carries `Run history — 10 comparison(s)
in this log`, a six-row candidate table, `Runs outside the candidate table` naming each
exclusion, and the multiplicity note. `grep -ci "exclud|superseded|comparison(s)|Holm|
protest"` on the terminal render: **0**; on the HTML: **12**. The terminal's closing line —
*"Full outputs, the flip list and the methodology appendix are in the HTML report"* — is
true but names three of twelve omissions. **An operator gating on the terminal reads a
document that presents itself as describing one comparison.**

Also terminal-only omissions: the `--goldenset` override (HTML warns, terminal prints the
substituted path unmarked and composes `<override path> (<recorded hash>)` as one pair,
while `--artifact-dir` *does* warn in both — the asymmetry is the finding); an empty
`thresholds` map (HTML says `no thresholds recorded in the evidence`, terminal renders
nothing); the timeline's counted gaps; the single-run disclaimer; and *"which is outside the
table above"* for an unaccounted-for rule.

## 23. "the 5 candidates in this field", printed under a table with six rows

```
candidate rows in the table: 6
the sentence says: across the 5 candidates in this field
```

`_multiplicity_caveats` receives `family_size = len(family)` — candidates *carrying a
p-value* — and calls it "the candidates in this field". `_untested_clause` **does** compute
the disambiguation — *"1 candidate(s) in the table recorded no p-value, were not tested, and
are not in the family"* — but only inside `Multiplicity.note`, which per finding 12 reaches
no reader. `grep -c "not in the family"` over the rendered page: **0**.

## 24. `REVIEW` is a colour and nothing else

The plan (line 1443) requires: *"REVIEW is a shape, not a colour… The template must select on
the verdict word, not on the CSS class."* `report.py:4013` is
`<section class="banner {{ verdict_class }}">`, and the only GO/NO-GO/REVIEW difference in the
whole document is `.banner.review{border-color:#8a6100;background:#fdf4e0}`. The interval bar
is byte-identical in structure across all three verdicts, and the actionable callout
(`n_required` minus observed) exists nowhere: `grep "too thin to decide" src/` returns nothing.

## 25. An exclusion the reader cannot check, and one that can be self-refuting

`_incomparable` compares the **full** 64-character hash; `_hash()` prints the first 16:

```
excluded: golden set 3f519e187067bcfb against the group's 3f519e187067bcfb.
```

An accidental 16-hex-char sha256 collision is not realistic — **but the second half is
certain**: the excluded run's full hash appears nowhere on the page (`grep`: 0 hits).
**No exclusion the page asserts can be verified by the reader it is written for.**

## 26. And when nothing *was* excluded, the section vanishes entirely

A clean two-candidate field with no exclusions renders **no section and no nav entry**
(`{% set excluded_shown = candidate_field is none or candidate_field.excluded %}`). So the
*no-table* path gets the careful "read this as not known" paragraph, and the
*nothing-was-excluded* path — the one case where a positive statement is available and
reassuring — says nothing at all. The two absences are inverted.

---

# Tier 4 — scope mismatches, wrong units, hedges that hedge the wrong way

## 27. "Read this as not known" on a log where it is known [SHIPPED DEMO]

> "…**Runs in this log may have been excluded** from a comparison without this page being
> able to name them. **Read this as not known**, and never as 'nothing was excluded'."

The evidence holds **one** `migkit.comparison` record, and the same document prints the count
100 lines lower: *"Run history — 1 comparison(s) in this log"*. The paragraph's first claim —
"no field could be assembled, so no exclusion pass ran" — is true and well put; the second is
false whenever the log holds one comparison. The brief's own named shape, with the error in
the conservative direction, which is why it has survived: it reads as caution.

## 28. The methodology appendix denies the two sections this whole plan added

Every render says:

> "This report contains **no cost model, no longitudinal trend**, and no claim about any item
> outside the golden set… It compares two models on one fixed set of cases under one panel of
> judges, **once**."

— in a document that prints `Run history — 10 comparison(s) in this log` above a six-candidate
table spanning 14 days.

## 29. Two floors on one page, and a floor the gate recorded printed as an em dash

Deleting **only** `thresholds["pass_rate_floor"]` — the gate keeps `min_rate: 0.87` — gives,
in one document:

> banner: "candidate accuracy: pass rate 53.5% … **floor 87.0%**"
> appendix: "The gate uses the one-sided Wilson lower bound against a **floor of —**"
> rule 2: "…misses the **—** floor…"

The banner goes through `series._gated(gate.min_rate, thresholds.pass_rate_floor)`;
`methodology_sections` does `thresholds.get("pass_rate_floor")` and never looks at a gate. The
paragraph whose subject is literally "the gate" prints an em dash for a floor the gate
recorded. The inverse (gate `0.95`, thresholds `0.87`) prints 95.0% once and 87.0% three times
with nothing reconciling them; the same family applies to `confidence`.

## 30. A log written out of time order: the banner and the chart disagree

Three runs written 08-01, 08-20, 08-10. Banner: **GO, 95.0%, exit 0**. Rightmost marker:
`data-created="2026-08-20…" data-rate="0.4" data-verdict="NO-GO"`. The chart, the "age in this
field" column and the field anchor all agree the NO-GO run is newest; the banner alone does
not, and prints no run date — only `generated`. The caption's hedge is backwards:

> "…a run appended with an older timestamp **sits to the left** of the run the banner describes."

It sends the reader left, to a reassuring 50%. The danger is on the right.

## 31. "Holm-Bonferroni correction across judges" on a panel of one

> "Judge 'accuracy' shows a statistically significant regression **after Holm-Bonferroni
> correction across judges**."

The demo's `judges` array has length 1, and `holm_threshold: 0.05` equals `alpha: 0.05`
exactly. Holm with m = 1 is the identity — no correction was applied because there was
nothing to correct. A reader takes the phrase as the standard reason to trust a significant
result on a multi-judge panel.

## 32. "the floor" means two different things one table apart

> per-judge table: "imputed (failed completions **scored at the floor**) | 0 | 0"
> appendix: "It is imputed **at the rubric's minimum** rather than dropped…"

"Floor" means `pass_rate_floor = 0.9` everywhere else on the page — the banner, rules 2 and 3,
the run-history prose, the dimension prose, and both SVGs (`data-rule="0.9"`). The rubric's
minimum is a **score of 1 on a 1-5 ordinal scale** — an unrelated quantity on an unrelated
scale. Harmless in the demo only because both counts are 0; on a real run with imputations the
two sentences give opposite pictures of how a crash is scored.

## 33. Every cell in the demo's dimension table is shaded, and no cell could ever be unshaded

> **REFUTED by adversarial review.** The "could ever" half is false — the repo ships a **second** golden set that unshades every cell: `migkit demo --goldenset src/model_migration_kit/data/showcase_goldenset.jsonl` gives `grep -c 'class="num refused"'` -> **0** of 33 cells. And plan R9 (:2427-2458) *designed* the demo's refusal: *"It refuses the spec's own 4-item example. The showcase clears it at 16 items and 80 completions."* Every cell also carries its own discriminator ("10 items needed for a verdict here; you have 4."). **Delete.**

> "**A cell is shaded where** the sample cannot support a verdict at all… The floors every cell
> here was judged against are 20 graded completions and **10 items**."

`grep -c 'class="num refused"'` → **8** — every cell. The demo's largest tag is 4 items against
a 10-item floor, so no tag in this golden set can reach the bar. The sentence is written as a
discriminator and discriminates nothing.

## 34. A renderer constant printed among evidence-derived facts

> "14.0 days between the oldest and the newest row, against a **window of 7.0 days** —
> **wider than the window**…"

`_STALE_AFTER_DAYS = 7.0` (series.py:973) is a module constant with one call site that passes
nothing; no config key, no CLI flag, no threshold entry. It renders in the same definition list
as the golden-set hash, on a page whose Thresholds block promises *every* threshold is echoed
from the record and that an unrecorded source says so.

## 35. The evidence log is the only input with no schema-version guard

```
runner.py:174   "was written with artifact schema {v}; this build understands up to
                 {ARTIFACT_SCHEMA_VERSION}. Refusing to read it rather than misinterpret it."
runner.py:564, judging.py:487   the same guard.

$ grep -n schema_version src/model_migration_kit/{report,series,evidence}.py
(no match)
```

A log with `schema_version: 99` on every record renders in full, exit 1, `VERDICT: NO-GO`, and
the page says nothing. `series._count`'s docstring already declares surviving a foreign writer
to be in scope — *"A writer that quoted its integers is a real thing to survive"* — and its
failure mode is silent coercion to `0`. **The one reader built to tolerate a foreign payload is
the only one that will not say it has one**, which is the mechanism that makes much of Tier 2
reachable.

## 36. A `min()` printed as "per side"

`power.n_observed` is `min(len(base_counted), len(cand_counted))` (comparison.py:1132) and
renders as *"(51 observed **per side**…)"* on a page whose rows two above read
`55 / 60 | 30 / 51`. False of the baseline. Three populations share the word "observed":
run-artifact records, the gate's `n` (completions minus parse failures), and this `min`.

## 37. Rounding erases the comparison the row exists to make

`lower_bound: 0.869994` against floor `0.87` renders `0.8700` / `87.0%` / "floor cleared:
**no**", and the banner reads *"pass rate 87.0% … floor 87.0%"*. Separately, deltas of `+0.04`,
`−0.04` and `+0.00` all print as `+0.0 pp` / `−0.0 pp`, above three distinct pass rates
(0.8504, 0.8496, 0.8500) that all print `85.0%`.

## 38. The thresholds `source` column asserts a file it cannot know

Every row prints `migkit.toml` — including an unknown key (`warp_factor | 9.5 | migkit.toml`)
— while the paragraph directly below says that provenance is not in the payload. And when the
verdict record's thresholds disagree with the comparison's, the comparison's win silently,
despite the sentence *"echoed from the evidence record that produced the verdict"*.

## 39. The degraded render still states missing data as zero

The plan (line 2095): *"Missing data stated as zero is worse than missing data stated as
missing."* With artifacts unresolvable, the page prints *"Every one of the 3 changed item(s)
carries its full outputs: **0 characters** of quoted model text"* and *"5 draws per item over
**0 items**"* — while the per-judge table four inches above reads `17 / 20` and `9 / 1 / 2`
items. The `0 / ?` half *was* fixed; the clarifier landed only on the demo path.

## 40. Three "1 run(s)" about one run, and a floor break that is not drawn

One run with `created="nonsense"`, no pass rate and no floor, in a log of three:

> "**1 run(s)** with no usable date, not plotted" / "**1 run(s)** recorded no pass rate" /
> "**1 run(s)** recorded no floor, so the rule is broken where they sit"

Three counts, one run, nothing saying they are the same run — and the third asserts a break
that does not exist, because undated points are dropped before `_floor_groups` runs. Separately,
`Timeline` returns `runs_without_floor` / `runs_without_rate` and **not** `undated`, so the
enumerating paragraph cannot count an undated run at all; the only mention is an SVG `<text>`
inside `role="img"`, invisible to the accessible name.

> **Withdrawn:** I originally added that the accessible name reads "over 4 run(s)" under a
> heading saying "5 comparison(s)". A browser render could not reproduce it — across four
> documents (1/1, 10/10, 201/201, 10/10) the two numbers agreed every time. Dropped.

## 41. Smaller, confirmed

* **A sentence naming the wrong cause.** A flip naming an item the golden set does not contain
  renders *"Input not shown: the golden set is unavailable or has changed"* while the same page
  reports the golden set loaded, hashed and intact. The true finding — the flip list names an
  item not in the golden set — is never stated.
* **The decision table can mark the GO rule "fired on this run" under NO-GO.** report.py:3138 is
  a bare string match; the very next line already knows how to say "which is outside the table
  above" and does not say it when the rule fits the wrong row.
* **The untagged dimension row has an empty `<th>`** (`UNTAGGED = ""`), so live numbers sit
  under no label, directly beneath a row of em dashes.
* **"1 judge(s) graded both sides"** is asserted for a judge whose candidate side is `0 / 0` and
  all dashes.
* **"5 draws per item … giving 60 / 36 on the candidate"** — draws is comparison-scoped,
  expected is per-side, so two draw counts appear in one sentence asserting a single one.
* **Earlier runs' warnings** are collected into `RunPoint.warnings` and rendered nowhere.
* **The pass/fail margins (80% / 20%) in the appendix are a second literal copy** of
  `comparison.PASS_MARGIN` / `FAIL_MARGIN`, not imported and not in the evidence — the one
  hardcoded pair in an appendix generated so it cannot go stale.
* **Provenance prints four hashes in four identical rows**; one is computed (verified correct)
  and three are unaudited payload strings. No judges path is recorded anywhere, so `judges_hash`
  can never be verified.
* **`h3 id="judge-1"`, `judge-2`, … are emitted per judge and nothing links to any of them.**
* **Envelope-clock runs are drawn identically to payload-clock runs.** The table discloses
  `(envelope clock)`; the chart, whose axis *is* that clock, does not.
* **"measured how far apart" silently drops undated rows** — the row's own cells are honest; the
  field-level sentence a reader uses to decide the table is safe is computed over dated rows
  only and says "row".
* **The chart caption publishes a run count** (`over {len(points)} run(s)`) where R29.4 ruled
  *"count comparisons, and say 'comparisons'. Never publish a run count this data cannot
  dedupe."* Flagged with the caveat that the plan is internally inconsistent here — this may be
  a ruling to make rather than a defect.
* **The three flips are `<details open>`; the one gain is collapsed.** The gains section's
  justification is "**Shown** because their absence would make this report an argument rather
  than a measurement", and the gain is the one changed item a reader must click to see.
* **`There is no per-dimension table.</strong> the log holds no…`** — a full stop before a
  producer reason written as a lowercase clause.
* **Nav says `Latency (descriptive only)`; the heading says `Latency`.**

---

## 42. Untrusted text impersonating the document's own voice

Everything on the page that came from a model, a judge, a golden set or a filesystem path is
untrusted. **Escaping is solid** — one render carrying, simultaneously, an injected
`<img>`/`<script>`, a `model_id` of `<img src=x onerror=alert(1)>`, an `adapter` of
`</td><td>GO`, a judge note `<iframe>`, a judge reason `<link rel=stylesheet>`, `warnings`
holding `<style>` and `<a href>`, and a `config_path` of `https://evil.example/migkit.toml`:

```
external_urls(): ()          assert_self_contained: PASS
raw '<script>' present? False    escaped forms present? True
```

The defects are semantic — untrusted strings that render as the document's own statements:

* **A judge reason of `Not measured.`** renders as `<dd>Not measured.</dd>` in the reasons
  list — the page's own absence marker. Sharper: the template fallback is
  `{{ row.reasons.get(name, 'no reason recorded') }}`, so a judge reason of exactly
  `no reason recorded` is **byte-identical, same element, same styling** to the page saying the
  judge recorded none.
* **`verdict.reason` prints verbatim in the banner.** `<p class="word">NO-GO</p>` immediately
  followed by `<p class="reason">VERDICT: GO — every judge cleared the floor.</p>`. The terminal
  path runs the reason through `_CONTROL_RE`; the HTML path applies nothing.
* **A `config_path` of `/etc/migkit/source not recorded in the evidence`** makes `_source_label`
  render all six threshold rows as `source not recorded in the evidence` — beneath prose reading
  *"where it says source not recorded in the evidence, that is a gap in the record"*.
* **`U+202E` in a golden-set tag** reaches `<summary>item-03 · #arithmetic‮ · accuracy 5/5 ->
  0/5</summary>` unterminated. `_CONTROL_RE` is `[\x00-\x1f\x7f]`, exempt on the HTML path, and
  does not cover bidi controls, so the existing control-character test cannot reach it. Per the
  Unicode Bidi Algorithm the margin should display reversed with the arrow mirrored — *a
  regression shaped like a gain*.
* **`model_id = "   "`** is truthy, so it bypasses `or "candidate"` → `<dd>   </dd>` and a title
  reading `to     `. The page cannot say the candidate was never named; it says nothing.
* **`test_ran: "not-run"` and the field being absent render identical bytes.** Same collision at
  `decided_by`, `command`, `model_id or 'unknown model'`, `created or 'no recorded date'`, and
  `candidate_model or 'unnamed candidate'`.

## 43. At scale the chart hides runs without saying so

200 comparisons inside one day plus one six months later: 7px markers at a **0.003px** median
spacing, with **199 of 201 markers ≥99% occluded** by later ones — 99 of the hidden ones NO-GO.
The heading correctly says `201 comparison(s)`; the chart shows two markers; nothing anywhere
mentions overlap.

Stated fairly: at an evenly-spread 220-runs-over-220-days spacing no marker exceeds 50%
coverage, so this is a clustering failure, not a general one.

**No silent caps anywhere else** — verified 40/40 candidates, 180/180 exclusions each with its
own reason, 24/24 judges, 61 matrix rows for 60 tags, 400 flip rows. The only count-bounding
slice is `dimensions._MAX_NAMED = 5`, which discloses itself. And the `DetailBudget` sentence
correctly stops claiming "every one" under load, reconciling 187 + 263 = 450.

# Robustness — one payload produces no document at all

`warnings: null` — well-formed JSON, one field — kills the render:

```
File "src/model_migration_kit/report.py", line 1622, in from_evidence
  warnings: list[str] = [str(one) for one in payload.get("warnings", ())]
TypeError: 'NoneType' object is not iterable
migkit: unexpected internal error; the traceback above is the whole of what we know.
exit=3            # and no HTML file is written
```

`payload.get("warnings", ())` supplies a default for a **missing** key but not for a **null**
one. Ranked here rather than in Tier 1 because it fails loudly and exits 3 — the documented
"could not produce a verdict" code — so it produces no document for a reader to be misled by.
It is the only render-killing payload in 2,391 differential renders.

---

# The prose documentation, checked against a real render

Separate from the rendered report, and stated separately because these are docs defects rather
than document defects.

* **The README's Install section is still one package short of the only real-model path it
  documents.** `grep -n 'model-migration-kit\[\|\[anthropic\]\|\[openai\]\|extras' README.md`
  returns **nothing**, while `README.md:452` documents `--adapter anthropic`. The *code* half of
  `pyproject.toml`'s claim is true and verified: the check runs at adapter construction and
  exits 3 before a single completion is sampled, naming the extra. The README never got the fix.
* **Two README transcripts show `migkit compare` spending a credential; neither can occur.**
  `README.md:317-339` says "it starts spending the credential on the fixtures" and quotes two
  progress lines before the error. Both blocks now produce byte-identical output — a
  `ConfigError` (not `AdapterError`) before any sampling. The class name is wrong and the claim
  about *money* points the wrong way about when the tool stops. True when written;
  `cli._require_sdk` did not exist at v0.1.0 or v0.1.1.
* **The README ships an error message the code deliberately replaced**, ending "Use `migkit
  demo` for the keyless path." `cli.py:357-361` records that this ending was a defect *because
  the remedy did not exist*; `demo` now takes `--goldenset` and `--n`, and the message says so.
  The README is the only place the dead end survives.
* **`CHANGELOG.md:3` says "All notable changes to this project are recorded here."** 92 source
  commits since the last entry are unrecorded, with no `## [Unreleased]` heading — including
  "put candidates, exclusions and the dimension matrix on the page". A demo render today carries
  four `<h2>` headings named nowhere in the CHANGELOG.
* **Stale figures:** `README.md:600` says "1101 passed" against **2206** here; `README.md:203`
  says "25,931 bytes" and `PROGRESS.md:42` "25,760-byte report" against **29,791**. All framed
  as historical transcripts, so not lies — but HANDOFF and the audit brief both treat a suite
  divergence as itself a finding.
* **Why these survived:** `docs/readme-scan-contract.md` defines the only automated check that
  the README does not lie, and it is narrow by design — `readme-pip-install` and
  `readme-commands`, both passing. **Nothing checks that a quoted transcript still reproduces.**
  Four stale transcripts in one README is a rate, not an accident.

---

# Verified sound — coverage, so this negative result is worth something

**The em-dash discipline is real where it was applied.** Of 161 comparison-payload leaf paths,
**29 are fully clean** across all five variants — both `pass_rate`s, every `lower_bound` and
`interval_*`, `p_value`, `holm_threshold`, `regressed`, `floor_cleared`, `mw_powered`,
`runs_needed`, `power.n_required`, all six `thresholds.*`, and all four latency `median`/`p90`.
46 paths are trivially unrendered and 37 reach the page only indirectly; those are named in the
harness output rather than counted as findings.

**The demo's arithmetic is correct wherever it is not the *unit* that is wrong.** Independently
recomputed: all 24 item-sides from the scripted responses through `judge_script`'s rules
(baseline 55/60, candidate 45/60, item counts 11/1/0 and 9/3/0, means 4.6667 / 4.0 — all
matching the evidence); all eight dimension cells; all seven Wilson intervals and both lower
bounds via `opik_rigor` at the page's own confidence, matching to four decimals; the "roughly
140" figure from a two-proportion normal approximation (139.8 → 140), consistent in all three
places it appears; "12 items, 8 with a reference, 0 untagged" line by line; "4 changed item(s)"
= 3 flips + 1 gain + 0 unstable, cross-checked as 11 − 3 + 1 = 9; every flip's `X/5 -> Y/5`
label against its outputs and judge reason; every `(N)` in a heading against what is listed
under it; both SVGs' geometry to five decimals.

**Self-containment and plumbing** — 32 rendered documents parsed with stdlib `html.parser`
(standard, hostile, no-verdict, zero-change, single-item, golden-set drift, multi-part, short
run, ghost item, deleted artifacts, artifact-dir override, five draw-collapse cases, six budget
levels, the multi-candidate field, and `cli demo`): no dangling anchors (validated against a
deliberate negative control), no duplicate ids, nav order matches document order in all 32,
`external_urls()` returns `()` and `assert_self_contained` never raises — including with hostile
`<img>`, `<script src>`, `@import url(...)` and an absolute `<a href>` in golden-set input,
candidate outputs, judge reasons **and an item_id**. `evil.example` appears 19× as escaped
visible text; `<script` appears 0× as a tag.

**Terminal against HTML:** they **never contradict each other on a number both print**. rich
markup in model output, model ids, judge names, notes, warnings and `decided_by` is printed
literally in both — no markup injection, no `MarkupError`, no hyperlink. Exit codes agree across
process, terminal and HTML for all four verdict states. `--no-terminal` leaves the HTML
byte-identical but for `generated`. **No number is silently truncated at COLUMNS 200/80/60/40** —
they wrap, and only unbreakable strings ellipsise, always visibly. Every terminal defect is an
omission, not a disagreement.

**Absence handled correctly** where reachable: `_ungraded`, `_uneven_coverage`,
`_self_comparison`, `_baseline_pass_rate`'s impossible-count guard, `_count_or_none` for
`runs_needed` / `n_required`, `0.0` vs `null` for pass rate / interval / lower bound, item counts
`0/0/0` vs absent, dimension cells with zero graded (*"Nothing was measured for X"*), a missing
or hash-mismatched golden set, `observed < expected` in both places, every latency path including
one-fake-one-real, no judge rows at all, `Candidate outputs (0) / No candidate outputs
available.`, the golden-set line's `— not available: <reason>`, an `n = 0` gate rendering em
dashes for the rate and both Wilson rows, `config_path=""` rendering `source not recorded in the
evidence` on all six threshold rows, and the empty-tag-universe branch — *"That is an empty tag
universe in the golden set, not a set of dimensions that every model scored zero on."*

**On the chart:** a measured `0.0` and a not-recorded rate **are** distinguishable
(`data-rate="0"` plus a rect, versus no rect); floor `0.0` and an absent floor **are**
distinguishable; a mid-series floor change breaks and steps at the correct midpoints; identical
timestamps and a zero span do not divide by zero; a one-run log asserts no direction or slope
anywhere; "N comparison(s) in this log" was correct on every fixture; an unparseable `created` is
omitted rather than drawn at the epoch.

**The capped detail-budget branch is honest** at 40,000 / 20,000 / 1,000 / 100 / 1 over 8 rows,
including `0 of 8`, with round-robin behaving as documented.

**Scope handled correctly** where the brief predicted it might not be: thresholds are
headline-scoped across runs; the judge count is headline-scoped (a 3-judge earlier run under a
1-judge headline still says "1 judge(s)"); warnings are headline-scoped; the scripted *opening*
is correct in both directions, including a blank-adapter headline under a scripted series. A
missing verdict record is clean throughout: "NO VERDICT", "the run ended before a verdict was
recorded", exit 3, "decided by no recorded rule". All thresholds absent renders em dashes, never
zeros, and the bar degrades to "floor not recorded" with no floor line drawn.

**The spot-check sentence is correct wherever it is computed** — it quotes the hypergeometric
probability (`comb(88,12)/comb(96,12) = 0.32877`), not the naive `(88/96)**12 = 0.35198`, names
its unit as items, and declines on a census. It simply never reaches a reader.

**Claims in the docs that I tried to break and could not:** `pyproject`'s opik-rigor 0.2.0 claim,
both halves (and `judging.py` refuses the same values *first*, so there is no window where a
config passes here and fails there); COMPATIBILITY.md's headline block value-for-value including
all eight `is_pinned` results; the removed `requires_network` marker (gone from both workflows,
both blanking the provider keys); every documented exit code; "the pin rule is checked before a
single API call is spent" — true, and it fires *before* the SDK check.

---

# Suspicions raised and dropped

* **"The candidate table recomputes the baseline pass rate, so 85.0% contradicts 42.4%."** Real
  in the *test fixture*, which deliberately writes an inconsistent `pass_rate`. Not reachable
  through any log this build writes — `assert_pass_rate` keeps `pass_rate == successes/n` on both
  its success and `PassRateError` branches, and `comparison.py`'s hand-built `n == 0` gate is
  already refused by the `judged_baseline <= 0` guard. Reported only in its absence form
  (finding 20), which needs no inconsistency.
* **"The 'powered' word comes from `judge["underpowered"]`."** My hypothesis; wrong. It comes
  from `mw_powered`. Corrected in finding 10.
* **"`_assumed_lineage` sorts candidate ids by name, so `v9` outranks `v10`."** It does not:
  `assumed_from` uses log order via `dict.fromkeys`, parses no id, and the caveat says so. Tested
  with `cand-v9` / `cand-v10` / `cand-v2`. No defect.
* **A suspected unstable sort at identical timestamps** — append order is preserved.
* **A suspected `_drifted_baselines` blind spot** — could not construct one with counts
  defensible as realistic.
* **An identical Holm threshold across three judges** — the fixture's own value, faithfully
  echoed. Not a defect. (Distinct from finding 31, which is about a one-judge panel.)
* **`migkit report` on a verdict-less log exiting 0** — it exits 3; the first probe read `$?`
  from a pipeline.
* **Anything about opik-rigor 0.1.1** — not installable here, so the 0.1.x half of
  COMPATIBILITY's version claim is unverified rather than confirmed.
* Could not produce a nav entry pointing at a section that did not render, and could not make
  `external_urls` miss a violation on any fixture, hostile input included.

---

# A note on the brief itself

`MACBOOK-AUDIT.md:39-42` says four sections do not appear with a single run. Its four named
absences are right, but the single-run demo **does** render "Runs outside the candidate table"
and "Results by dimension" — and two of this project's most load-bearing absence sentences live
in exactly those two sections. Findings 9 and 27 are both there. An auditor following the brief
literally would skip them.

---

---

# Rendered in a browser — what a source-reading auditor cannot see

Google Chrome 151.0.7922.174, `--headless=new`. Everything below was actually rasterised.

## 44. A bidi override in the judge name flips a regression into a gain, with no visible corruption

The prediction from static analysis was half right, and the half that failed is worth recording.

With `U+202E` at the end of a **tag**, the summary displays as
`▼ item-03 · #arithmetic5/0 <- 5/5 ycarucca ·` — reversal real, arrow mirrored — but
`accuracy` → `ycarucca` makes it read as **garbage, not as a gain**. That form is loud.

Move the same character one field over, into the **judge name**, and it is silent. One document,
two sections, side by side:

```
Flips  — items that STOPPED working:   ▼ item-03 · accuracy5/0 <- 5/5
Gains  — items that STARTED working:   ▶ item-11 · accuracy5/5 <- 5/0
```

**No visible corruption at all.** The two sections' margins display exactly backwards from each
other. `report.py:3260` scrubs `[\x00-\x1f\x7f]` only — no `dir`, no `<bdi>`, no isolation
anywhere — and the HTML path is exempt from even that.

## 45. Two disclosures exist only on hover, and one only in colour

* **The banner bar's `interval not recorded, floor not recorded` lives in `<svg><title>`** —
  hover and screen-reader only. On screen it is a bare unlabelled tick; **in print it does not
  exist.** A direct hit on this project's central rule: the absence marker is invisible to the
  audience reading the page.
* **The verdict is hue-only.** GO vs REVIEW is a **1.148:1** contrast ratio; a greyscale render
  shows nine identical grey squares, and there is no legend anywhere.
* **"A cell is shaded where the sample cannot support a verdict" is 1.0719:1** — sampled
  `#f6f7f9` against `#ffffff`. The sentence is written as a discriminator; at that ratio it
  discriminates nothing on screen. (Needed a custom mixed fixture to measure: the demo is 8
  refused / 0 plain, the multi-candidate log 0 / 43.)
* **Under user-forced dark mode** the rate line inverts to `#fbffff` while its interval box stays
  `#cfd4da`: 11.83:1 collapses to **1.48:1**.

## 46. Print drops the gains evidence, and the page scrolls sideways on a phone

* **11 pages default vs 12 with `<details>` forced open.** The PDF's entire Gains section is one
  collapsed line while all three flips print in full — so a report printed for a decision meeting
  silently omits the improvements, in a document whose own prose says gains are "shown because
  their absence would make this report an argument rather than a measurement".
* **No `overflow-wrap`.** One hostile token takes the page to `docScrollW=2646` at
  `clientW=1280`, and **the shipped demo already scrolls sideways at a phone width** (755 vs 500,
  36 elements).
* **No `<h1>` in either document**, and `h2 → h4` twice.

## 47. The chart's collisions are worse rasterised than predicted, and uncounted

201 comparisons render at **2 distinct x coordinates** — worse than the 0.003px spacing predicted
from the SVG source. Ten runs sharing one timestamp render as **ten evenly-spaced markers that
look like a ten-week trend**, while the visible caption asserts in bold *"The axis is time, not
run number"*; the correction is tooltip-only. The "gaps are counted rather than hidden" list does
not cover collisions at all.

**Checked clean on screen:** focus indicators (no `:focus` rule, so the UA default survives —
rendered proof captured), tab order, and the nine-markers-under-"10 comparison(s)" case, which
*is* correctly disclosed in visible prose.

---

# Mutation testing — where the suite would not have noticed

The reviewer method this project prescribes, run in a detached worktree with every restore made
from a byte-verified copy (never `git checkout --`). Baseline `2206 passed`; final `2206 passed`;
all four touched files restored sha1-identical; worktree removed.

**30 mutations, 17 survived.** Each survivor was also shown to change the *rendered document*,
so none is dead code. The suite is not weak overall — 13 mutants were killed, several by 20+
tests — but it is blind in exactly the places this audit found defects.

| Mutation | Result | What the document then says |
|---|---|---|
| Invert `if provenance.headline_scripted` | **SURVIVED** | the all-fake demo claims *"neither of its sides names a Fake adapter"* |
| Delete `_MACHINERY_IS_REAL` | **SURVIVED** | two disclosure sentences vanish |
| `_warned_title` prefix check -> substring check | **SURVIVED** | a title containing "FAKE MODELS" suppresses the real prefix |
| HTML judge table renders only `judges[0]` | **SURVIVED** | judge 2's whole table vanishes under prose saying "2 judge(s) graded both sides" |
| `RateStat.from_gate`: `passes` -> `0` | **SURVIVED** | `0 / 60` beside `pass rate 42.4%` |
| `mw_powered` lift dropped | **SURVIVED** | a **measured** "not powered" renders as the em dash reserved for "never recorded" |
| `underpowered` never read | **SURVIVED** | flag row flips rule 3 (REVIEW) into rule 2 (NO-GO), two screens above the printed rules |
| `imputed`, `parse_failures`, `holm_threshold`, `runs_needed`, `items` lifts dropped | **SURVIVED** (5) | rows silently zero or disappear |
| failed completion -> empty string | **SURVIVED** | timed-out draws become blank blocks under "carries its full outputs" |
| `n_per_item` hard-coded to 5 | **SURVIVED** | a log saying 7 renders "5 draws per item" |
| unrecorded-adapter fallback deleted | **SURVIVED** | `( for the baseline, ...)` |
| `warnings: null` crash fixed | **SURVIVED** | *no test noticed the crash existed* |
| `_baseline_pass_rate` -> `1.0` | killed by 21 tests | |
| `_change_sections` -> `()` | killed by 38 tests | |
| `exit_code` -> `0` | killed by 9 tests | |
| dimensions: first tag only | killed by 11 tests | the plan's named test **does** exist and fail (`tests/test_dimensions.py:185`) |
| `_draws` always-identical, latency both ways, `_banner_bar` judges[0], candidate table `[:1]`, dimension candidate columns, `from_gate` interval, truncation flag | killed | |

**The single most consequential line:** *R29.1's fix — the exemplar defect this audit brief is
built around — has zero coverage.* It can be inverted and the suite stays green.

## Fixture pairs that never vary together

The second half of this project's own standing rule ("vary it *in pairs*"). Uncovered
combinations, each naming the defect it would hide:

* **two judges x the rendered judge table** — hides the judges[0]-only render above.
* **two judges x a multi-tagged golden set** — vacuous, because `tests/test_report.py` has **no
  multi-tagged fixture at all**: every tag map assigns 0 or 1 tag. This is why finding 9(a)
  shipped in the demo.
* **a failed completion x a truncated output** — vacuous, because **no fixture in any of the 17
  test files ever writes a `null` output.** `_write_run` is typed `Sequence[str | None]` and has
  a live `SampleTimeout` branch **no caller reaches**. This is why finding 15 shipped.
* unrecorded adapter x scripted headline; `is_demo` x a caller-supplied title; judge-level
  `underpowered` x a missed floor.

**Cleared:** adapters vary, judge names vary, `tests/test_series.py` carries five distinct judge
shapes and is well pinned. And a suspicion of mine was **wrong**: the suite *does* render a
document containing a candidate table (`_every_element_model` builds 2 candidates and 3
exclusions, and mutations to both were killed). Finding 12 stands regardless — that helper
touches none of `spot_check`, `trend`, `multiplicity` or `parameter_strip`.

---

# The project's own gates, run on this machine

| Gate | Exit | Verdict |
|---|---|---|
| `check_merge.py` | 0 | **PASS**, 7/7 |
| `check_contract.py` (main plan) | 1 | **FAIL** — 4 citations into a sibling `opik-rigor` *source* checkout that does not exist here (it is installed as a wheel) |
| `check_contract.py docs/release-evidence.md` | 1 | **FAIL** — a real portability defect *in the gate*, see below |
| `check_contract.py` (8 other docs) | 0 | PASS each |
| `verify_release.py` | **2** | **SKIP — NOT A PASS.** 2 passed, 0 failed, **13 skipped** of 15 |
| `dependency_surface.py` (and `--check`) | 0 | PASS, 25 modules |
| `ruff check src tests` | 0 | PASS |
| suite, six configurations incl. two shuffled seeds and CI's netguard form | 0 | **2206 passed** in all six |

**A portability defect only a non-Windows operator can find.** `check_contract.py`'s citation
regex accepts `\` separators and drops the drive letter, so `root / candidate` yields a path
that **exists on Windows and is one nonsense filename component on macOS**. Proven on a scratch
file: a `\`-separated citation of a real, present file passes there and fails here. The Windows
box cannot find this, by construction.

**`verify_release.py` is the dangerous one, and it fails the project's own central rule.** The
wrong interpreter does not turn it red — it turns it *quieter*: bare `python` yields **14** skips
at the **same exit code 2**. An operator watching exit codes cannot see a check degrade from
PASS to SKIPPED. That is "an absence must not render as a measurement", occurring inside the
release gate rather than the report. Its remediation string is also hardcoded Windows
(`.\.venv\Scripts\python.exe ...`) and prints immediately after `platform : darwin`.

**Two gates CLAUDE.md names are in no CI workflow at all.** `check_merge.py` — called "the merge
gate" — is honour-system only: four of its seven checks (conflict markers, every file parses, no
shadowed top-level names, `__all__` completeness) run **nowhere** in CI. `check_contract.py` is
in no workflow either. Conversely CI runs two gates CLAUDE.md never mentions: a network-blocked
pytest (`-p netguard`) and a cold-venv 120-second `migkit demo` check.

**Two CLAUDE.md facts are stale, with corrected numbers:**

* "repo-wide format drift ... ~26 files" — it is **33** tracked Python files (37 including
  Markdown, since ruff 0.16 formats fenced code blocks). Every gate script itself drifts.
* "`ruff format --check tests/test_report.py` fails with ~50 hunks" — **confirmed** it fails and
  has never passed, but it is **60** hunks / 229 changed lines, and that file is one of 33 rather
  than a special case.

**And a correction to my own method.** I opened this audit with `pytest -q -p no:randomly`.
`pytest-randomly` is **not installed**, so that flag was a no-op and proved nothing about
ordering. Order-independence was established separately with this repo's own
`scripts/audit/shuffle_order.py` across two seeds — verified to actually shuffle (tests torn out
of file and class groupings, not merely permuted) — plus xdist and netguard forms: **2206 passed
in all six configurations.** The count was never in doubt; my flag was.

# Which machine to develop on

Asked for alongside the audit. Every figure for this Mac was measured here; every figure for
the Windows box is **quoted from CLAUDE.md**, not measured by me.

| Workload | Windows (repo) | This Mac (measured) | Speedup |
|---|---|---|---|
| Full suite, serial | ~136 s (CLAUDE.md:36) | **19.28 s** | 7.1x |
| Full suite, `-n 8` | ~97 s (CLAUDE.md:36) | **13.07 s** | 7.4x |
| `tests/test_report.py` | ~30 s / 371 tests (CLAUDE.md:111) | **5.64 s / 435 tests** | 6.2x per test |
| ...under agent contention | **365 s** (CLAUDE.md:40) | worst observed **5.99 s** | 61x |

Spec: **Apple M1 Max, 10 cores (8P+2E), 64 GB, macOS 26.5.1, arm64**, APFS SSD, 610 GiB free,
1,738 MB/s sustained write. Timings are min-of-3 (serial and `-n 8` min-of-6 across two
brackets ~15 min apart), **all taken under live contention** (load 4.4 -> 9.7, with another
agent running `pytest -n 4` against this repo).

**Read the multiplier as a range, not a point.** The Windows baseline is internally
inconsistent: CLAUDE.md:36 says ~136 s serial while CLAUDE.md:111-112 says ~4 minutes;
CLAUDE.md:111 says `test_report.py` has 371 tests and it has **435**; README.md:600 records a
Windows run of **"1101 passed in 38.34s"** against today's **2206**. Honest range **4x-12x,
most likely ~7x**. The direction is not in doubt; the multiplier is.

**RAM is ample; cores are the constraint.** Real application memory is **26 GB of 64 GB**,
**swap total 0.00 MB** (macOS has never created a swap file this boot), compressor 14 MB. The
"63G used" in `top` is 37 GB of evictable file cache. One `pytest -n 8` tree peaks at 2,045 MB;
a full agent is ~2.9 GB. RAM supports ~12 concurrent testing agents; **10 cores support 3-4.**
Default to `-n 4`. Note serial is already 19 s here and `-n 8` buys only ~6 s, because ~5.6 s of
it is per-worker `import scipy.stats` (0.632 s x 8) - xdist has little left to win on this
hardware.

**The Anaconda shadowing here is worse than the Windows `.pth` trap, because it fails green.**
`python`, `python3` and `pytest` all resolve to Anaconda 3.9.13 (pytest 7.1.2), below
`requires-python = ">=3.10"`. Measured: **bare `pytest` collects 247 of 2206 tests with 16
collection errors** - an agent that ran it would see a small green run and believe the suite
passed. Anaconda has no xdist, so every `-n N` fails, and `ruff` is not on PATH at all, so
HANDOFF.md:302's bare `ruff check src tests` errors.

**What keeps the Windows box alive:**

* It holds the **only Python 3.14 coverage in existence** (COMPATIBILITY.md:20, README.md:52).
  CI covers 3.10-3.13 on ubuntu + windows, with **no macOS runner anywhere** - so this box's
  green suite is evidence CI never collects.
* Every published transcript in README.md is a Windows transcript, including a literal
  `WindowsPath('rubric.md')` at README.md:465.
* One genuine code asymmetry: **`report.py:_contained` (2048-2059) uses `os.sep` and
  `os.path.abspath`/`normcase` - the *reading* platform's semantics - while every neighbouring
  function in that block deliberately parses both separators textually** because the writing
  platform may differ. It behaves differently on the two boxes and no test distinguishes them.
* `gh` is not installed here, so there is no PR workflow until `brew install gh`.

**A claim I made earlier and am withdrawing.** I wrote that numeric results are not
bit-identical across the two boxes, citing `power_at_runs_needed`. That was imprecise.
COMPATIBILITY.md:853-863's one-ULP discussion is about **rigor 0.1.1 vs 0.2.0 on the same
machine**, cause given as expression rearrangement - it is not a platform claim, and it says so.
`power_at_runs_needed` appears only in COMPATIBILITY.md, is new in rigor 0.2.0 (so has no prior
value to have moved from), and **no test in `src/`, `tests/` or `scripts/` reads it at any
tolerance**. The repo's cross-platform value guarantees are about **hashes**, not floats
(`contracts.py:11`, COMPATIBILITY.md:1063), and the one full-precision float assertion uses
`pytest.approx(rel=1e-9)` - six orders of magnitude of headroom over a 1-ULP move. Float
reproducibility is **not** a reason to prefer either box.

**Recommendation: develop on the Mac, verify releases on Windows** - but fix CLAUDE.md first.
Its interpreter section, worktree paths, core count, test counts and ".pth trap is fixed" claim
are all wrong here, and a bare `python scripts/check_merge.py` reports a **false red** on a
green tree while a bare `pytest` reports a **false green** on 11% of the suite.

**To close the comparison**, run this on the Windows laptop. The collection count matters most:
if Windows collects 2206 the 136 s figure is comparable and the speedup is ~7x; if it collects
~1101 those figures describe a suite half this size and the real speedup is **larger**.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --collect-only | Select-Object -Last 1
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -n 8
.\.venv\Scripts\python.exe -m pytest -q tests\test_report.py
.\.venv\Scripts\python.exe scripts\worktree_path.py --status
```

A portable single-core reference is left at `scratchpad/audit/bench/cpubench.py` (this Mac:
0.154 s) to run unchanged on both boxes.
