# JOB-7 — the verdict logic (issue #6)

JOB-3 asked whether a **gate** could be satisfied by something other than what it checks. This
asks it of the thing the product exists to produce.

> **Can each verdict be satisfied by something other than the state it claims?**

**Yes — including two GOs.**

**Evidence standard, stated up front:** every case below was produced by building real
`JudgedArtifact`s, running the real `compare()` into a real `EvidenceLog`, and rendering the real
report. **No payload was hand-edited** — so the reachability caveat that weakened most of the
first audit's Tier 2 does not apply here. Tree under test: `8f39878`.

**Provenance caveat:** these are a subagent's results, landed at this session's token limit. I
did **not** independently re-run them, and say so rather than implying I did. The methodology
above is the agent's own statement. **Windows should reproduce findings 1, 2 and 4 before acting
on them** — they are the three that move a verdict to GO or manufacture a NO-GO.

---

## V1. A GO purchased with duplicate draws

Twelve items. Both models pass all of them, every time. **The only thing that changes is `--n`:**

```
12 items x 1 draw   -> REVIEW  rule 3   floor_cleared=False  mw_powered=False  n_obs=12
12 items x 5 draws  -> GO      rule 5   floor_cleared=True   mw_powered=True   n_obs=60
```

The two pages agree on the item-level truth and disagree on the verdict:

```
                        12 x 1 draw              12 x 5 draws
banner                  REVIEW                   GO
Wilson lower bound      0.8160 | 0.8160          0.9569 | 0.9569
items pass/fail/unst.   12 / 0 / 0               12 / 0 / 0     (identical)
powered for the effect  no (12 obs, ~56 req)     yes (60 obs, ~56 req)
```

`n_observed = min(len(base_counted), len(cand_counted))` counts **completions**. Nothing divides
by `n_per_item`, while `required_sample_size`'s own docstring derives 56 from **independent
Bernoulli trials**. The extreme: **a one-item golden set asked 60 times is a GO**, and the
appendix says *"60 completions per side observed against roughly 56 required … so this judge is
powered for the question."*

> **SURVIVES.** This is **not** the known demo thread — that was the p-value at rule 1. This is
> the **floor gate and the power flag**, at rules 2/3 and 4/5: the pair that separates REVIEW
> from GO. `goldenset.py` accepts a one-item set and `runner.py:327` requires only `n >= 1`.

## V2. A GO bought by the judge declining to give a number

20 items × 5 draws, all passing both sides. **The only field changed is the candidate's `score`:**

```
control        (score 2.0)                -> NO-GO  rule 1  p = 1.76e-45
99 of 100 with "score": null              -> GO     rule 5  p = 1.0
```

`_impute_unscored` fills each unscored *passed* record at `SCORE_MAX`, and Mann-Whitney then runs
on an array **the tool wrote**. The page prints `p-value 1.000000` and `test that actually ran |
mann-whitney-u`.

> **SURVIVES, and it is fully reachable on the default config.** `judging.py:762` records a null
> score as a normal record, never a parse failure, so `judge_failure_tolerance` never sees it —
> and a null score is **rigor's documented prompt contract**, quoted in `comparison.py`'s own
> docstring.

## V3. The mirror — silence on the *baseline* manufactures a NO-GO

`_impute_unscored`'s docstring claims the design prevents *"manufacturing a regression out of the
judge's silence."* It closes the floor-fill route and leaves the **ceiling-fill route on the
baseline** open:

```
control                    -> GO,    p = 1.0
99 baseline scores null    -> NO-GO, p = 1.28e-44
```

Rendered: **100/100 passing on both sides, 20/0/0 items both sides, zero flips, `imputed | 0 |
0`** — and a NO-GO. The `imputed` row, the one a reader checks to ask *how much rests on
imputation*, counts `record.imputed` and **not** the 99 records `_impute_unscored` filled.

## V4. Five judges agreeing on a regression → GO

Each judge sees byte-identical evidence, p = 0.012086519075099146:

```
1 judge -> NO-GO    2 -> NO-GO    3 -> NO-GO    4 -> NO-GO    5 -> GO
```

The GO page says *"No judge regressed"* — and four rows below reads, verbatim:

```
Mann-Whitney p-value (alpha 0.050, Holm threshold 0.0125) |  0.012087 |
regressed / floor cleared / underpowered |  no / yes / no |
```

**A p-value below the threshold printed on its own row, reported as `no`.**

> **WEAKENED.** The Holm implementation is correct and the FWER intent is deliberate — this is
> not a statistics bug. The defect is that the family is **five readings of one migration**, so
> the decision is non-monotone in *confirming* evidence, and the page states a falsehood in the
> codebase's private sense of "regressed".

## V5. Rule 1 outranks its own mitigation

`_scores` accepts an anticonservative small-n outcome test on the grounds that *"the small-n
region is already REVIEW rather than GO."* **That mitigation is rule 4. Rule 1 runs first** and
tests `regressed` alone:

```
n_obs=3, n_req=56, mw_powered=False  ->  NO-GO, rule 1, p = 0.0234
```

The exact p-value the module names as too small, decided by the one rule with **no power
precondition**. *(Floor lowered to isolate rule 1 — stated, not hidden.)*

## V6. The known threads, traced to a rendered sentence

`no_data: True` → `_pass_rate` short-circuits at `n == 0` **without calling rigor**, fabricates
`underpowered: True`, and rule 3 renders:

> *Judge 'style' missed the pass-rate floor, but rigor reports an underpowered sample rather than
> a demonstrated failure: collect more completions.*

**Two sentences, both false**, in the highest-visibility line of the document, above a table that
correctly renders the pass rate as `—` and `0 / 0` completions. That judge satisfies rule 3 *and*
rule 4 — **and rule 4's sentence would have been true.** The precedence picked the false reason.

**New, same shape: `missing_scores` is a second dead disclosure field.** Written to the log, read
by **no renderer** — and `_impute_unscored`'s docstring cites it as the reason a design decision
is safe. Worse: on the outcome-fallback path `_scores` returns a literal `(0, 0)`, so a candidate
with **all 100** scores absent logs `missing_scores: {"baseline": 0, "candidate": 0}` — **an
absence recorded as a measured zero, inside the disclosure field.**

## V7. The Holm family shrink

`holm_bonferroni`'s docstring says a test with no answer *"must not quietly shrink the family and
loosen every other judge's threshold either."* `comparison.py:851` drops `p_value is None` judges
from the family — **the forbidden move, three lines away.** `judge-1`'s evidence is byte-identical
across both runs:

```
all five judges ok      -> GO     judge-1 holm threshold 0.01
four judges broken      -> NO-GO  judge-1 holm threshold 0.05
```

And the appendix says *"With 5 judges the family … is corrected by Holm-Bonferroni"* over a family
that held **one** p-value, tested at the **uncorrected** alpha.

> **WEAKENED on reachability** — needs `judge_failure_tolerance` raised, since `cmd_compare`
> always re-judges.

## V8. Two things that could not be broken — well-evidenced negatives

**`explain_verdict` is a faithful, total implementation of its documented precedence.** Driven
over every combination of the four flags at 1, 2 and 3 judges — **4,368 cases** — against an
independent restatement of the docstring: **zero mismatches.**

> **The precedence table is not where the defects are. Every finding above is in what feeds it.**

**A GO can never contain a judge whose regression test did not run** — proven, not sampled:
`p_value is None` ⟹ one side's counted records empty ⟹ `n_observed = 0` ⟹ `powered` always
`False` ⟹ rule 4 fires before rule 5.

**Dropped so nobody retries them:** NaN p-values (correctly handled, unreachable without stubbing
rigor); `p_value or 0.0`; the `_flag` aliases; and **rule 2 firing while `mw_powered=False`** —
its sentence is about the floor and is true about the floor. **REFUTED.**

---

# JOB-8 — `series.py` as logic

## L1. "the 3 candidates in this field" over a six-row table — and the repair never reaches the page

`_untested_clause`'s own docstring: *"Named in **every** note, applied or refused, because a
family size printed beside a table with more rows than that reads as a miscount."* `_applied_note`
calls it. **`_multiplicity_caveats` does not — and it is the only one of the two a reader sees.**

Six comparisons, three with a `p_value` and three with `null`, through the real renderer:

```
rows=6, family_size=3
three caveats each saying "Holm-Bonferroni across the 3 candidates in this field"
grep -c "were not tested" -> 0
```

> **SURVIVES, and reachable:** `JudgeComparison.p_value` is `None` on any judge whose regression
> test did not run. The reader concludes either that the table is miscounted, or that three
> candidates were corrected and survived — **they were never tested.**

## L2. Smaller, confirmed

- **Two concurrent writers put one run's NO-GO reason on another run's row** — documented at
  length in `read_series` and deliberately not detected; what survives is that **no field carries
  a marker**, so the mis-attribution renders indistinguishable from a measurement. Undocumented:
  `_decided` `replace`s unconditionally, so a second verdict record with a partial payload
  **erases** a recorded verdict, yielding a reason with no verdict. **WEAKENED** — not producible
  by `comparison.py`, and gated on concurrent `compare`s, which could not be settled.
- **The assumed-lineage caveat names the opposite order to the line it qualifies** —
  `assumed_from` orders by first appearance, `trend` sorts by `created`, so on a log not in clock
  order the caveat and `Trend.successions` disagree in the same object. **WEAKENED.**
- **`_delta_pp` guards one operand and not the other** — the argument `_baseline_pass_rate` makes
  for refusing impossible counts is exactly as true of the unguarded candidate rate. Not
  reachable; recorded as a contract inconsistency, not a patch.

## L3. Sound, with the coverage named

- **`is_identifying` ⇄ `_incomparable`**: exhaustive over **256 keys**, both drift directions —
  **zero disagreements.** The contract the docstring says a test exists to protect, holds.
- **`spot_check`**: recomputed as an exact `Fraction` product over **9,151 (N,F,k) cases** up to
  N=5000 — **zero mismatches**, declines correctly at `N==k`, `N<k`, `F==0`, `N==0`, and the demo
  figure is the hypergeometric rather than the with-replacement error.
- **`_baseline_pass_rate`'s reconstruction claim holds** — rigor writes both from the same
  integers, so it really is the rate rather than an approximation.
- **`candidate_field` accounting**: 4,000 randomised hostile logs — every point in exactly one of
  `candidates`/`excluded`, **zero holes**.
- **`report.py` closes the `candidate_field is None` hole well**, verbatim: *"Read this as not
  known, and never as 'nothing was excluded'."* **REFUTED on the headline.**

---

# JOB-7, upstream: `judging.py`, `runner.py`, `goldenset.py`

Same evidence standard — real `run_goldenset` → real `PinnedJudge` → real `compare` → real
report. Only rigor's `Adapter` seam is scripted. Tree `8f39878`.

## U1. A judge writing a score outside the rubric range turns NO-GO into GO

40 items × 5. Candidate answers `"garbage"` on two items — 10 of 200 completions. Two runs,
identical **except the number the judge writes beside a verdict it has already reached**:

```
judge says {"pass": false, "score": 1}  -> NO-GO  rule 1  190/200  p=0.00069  flips 2
judge says {"pass": false, "score": 0}  -> GO     rule 5  190/190  p=1.0     flips 0
                                                    parse_failures cand: 10   warnings: []
```

`score: 0` is a judge on a 1–5 rubric expressing total failure. rigor refuses it **before**
returning any part of the verdict, so `_grade` files the whole record — `pass: false` included —
as `parse_failure=True`, and `_counted` drops it from numerator **and** denominator. Rendered:

```
GO — No judge regressed, every judge cleared the pass-rate floor…
candidate accuracy: pass rate 100.0%, interval 98.0% to 100.0%, floor 90.0%
Exit code a CI system would have received: 0
```

**`_impute_unscored`'s own docstring names this number** as the catastrophe it exists to prevent:
*"the candidate's ten unscored failures would leave the numerator **and** the denominator,
posting 190/190 = 1.00 and turning the very NO-GO this rule exists to protect into a GO."*
Reached through `_counted`'s parse-failure door instead.

**And nothing warns.** 10/200 = 0.0500 against a tolerance of 0.0500 — measured boundary: 2 bad
items pass silently, 3 abort. The sibling threshold spends 26 lines explaining why the
*unscored-record* disclosure fires at `>=` rather than `>`, *"a disclosure that is silent at its
own documented number tells the reader something false."* The parse-failure path — which removes
a record from **both** populations — has no disclosure below the abort.

> **SURVIVES, correctly scoped.** The uncorrelated case was tested: parse failures on items the
> candidate answered *correctly* do **not** flip the verdict. So it requires the judge's
> malfunction to correlate with the model's failures — which is the most natural failure of an
> LLM judge shown a catastrophically bad answer, and the direction the tool says it must never
> err in.

## U2. An item whose every draw was unparseable has no state anywhere

```
items passing / failing / unstable |  40 / 0 / 0  |  38 / 0 / 0  |     beside  items: 40
```

38 + 0 + 0 = 38. **Two items left the histogram without entering any bucket**, on a page whose
`Flips (0)` section reads `None.` Three lines produce it: `_item_states` `continue`s on
`parse_failure`; `_classify_items` skips an item absent from the candidate, so there is **no
fourth list**; and `items` is the *baseline's* count printed for both sides.

**Imputed accounting is coherent in all eleven places on the page. Parse-failure accounting is
coherent in one** — the `judge parse failures` cell. *(The earlier audit's imputed/dimension
divergence did **not** reproduce at `8f39878`; recorded as not-reproducing rather than
re-reported.)*

## U3. `parts per run` reports the *judging* pass's parts, in both directions

When the run artifact is unreadable — the documented cross-machine case — `parts` falls through
to the **judged** artifact's header count and is rendered under `parts per run`:

```
runs completed in 1 part each, judging resumed  ->  "completed in 2 parts"   (false seam)
runs really resumed, judging ran once           ->  "completed in 1 part"    (hidden seam)
```

`runner.py`'s docstring: *"A resumed run is a perfectly good run; hiding the seam would be the
only dishonest option."* Direction B hides it.

## U4. `--timeout` makes latency the strictest gate in the tool

Candidate returns the **exact reference answer on every draw**; two items take ~40 ms against a
20 ms budget:

```
NO-GO, rule 1 — "Judge 'accuracy' shows a statistically significant regression"
flips: item-005 5->0, item-011 5->0    imputed cand: 10
```

rigor *keeps* the answer (`Run(value=value, error=SampleTimeout)`); `_completion_from_run`
branches on `error` first and **discards `run.value`**, so `_grade` imputes `SCORE_MIN`. The same
page says twice: *"Latency is never a gate — a migration that is slower per call is a product
decision, not a quality regression."*

> **SURVIVES.** The operator did ask for a timeout — but the report states the opposite in terms,
> on the same page, twice, and **the correct answer is destroyed**: `Completion` has both an
> `output` and an `error` field and could have carried both.

## U5–U7. The input contract

- **Unicode normalisation is checked nowhere** (`grep -rn "unicodedata\|normalize\|NFC"` → no
  matches). Two items whose ids are NFC and NFD of the same string **load clean and both appear
  in the flip list**, past a duplicate check whose own message says *"two items sharing one id
  make that list wrong rather than incomplete."* And the same golden set authored NFC vs NFD is
  **not comparable** — macOS is the platform that produces NFD, so a baseline that cost real
  money is invalidated by a difference no reader can see. *Mitigated where the golden set travels
  with the log.*
- **Ids are not stripped while tags are.** `_parse_tags` strips before its duplicate check, with a
  comment giving the argument; `_required_text` twelve lines earlier returns the unstripped value.
  **The module contains its own refutation.**
- **The comparability hash covers `reference` and `metadata`, which nothing in a real run reads
  and nothing in the report prints.** Adding `"metadata": {"reviewed_by": "alice"}` to **one item
  of forty** invalidates the paid baseline — and `ALLOWED_KEYS`' comment *invites* you to use
  metadata. Taken with the NFC/NFD case the hash is both **under-** and **over-inclusive**.
- **`file_hash` is written once and read by nobody** — one line in the repo, the write. Its
  docstring says *"Both go into the report."* Searching the rendered report for it: **0 hits.**

## U8–U10, and the sound list

- **Resuming an *older*-schema artifact is performed, not refused** — 100 new completions written
  and fsynced, *then* the read-back raises, leaving the file unreadable by its own loader with
  the paid work of both passes inside it. **Latent today** (needs a hand-edited file); it arms on
  the day the constant is bumped.
- **`fresh=True` cannot rescue a newer-schema artifact** — `load` runs before `fresh` is
  consulted, so the remedy the error messages recommend cannot be applied.
- **The FAKE band and the latency suppression ride the same `"Fake"` class-name prefix.** An
  honest sub-millisecond adapter (local model, cache, replay harness) prints `0.000 | 0.000`
  **without** the sentence the suppression branch carries. *The class-name limit itself is
  already documented — the prefix coupling is not.*

**Sound, with what was tried.** The **imputation promise is true**: a crasher and a bad answerer
posting the same 190/200 differ in exactly one field (`imputed`), and are distinguishable in
three places on the page; structurally it cannot invert, since `SCORE_MIN` is 1.0 and rigor
refuses anything below it. **Resume accounting is honest** — no double-count, no loss, and
`parts` correctly does not increment on a re-run. **`--goldenset` verifies the content hash and
refuses loudly**, leaving the verdict untouched. **No content-hash collision or spurious
difference could be constructed** across nine variants — trailing newline, CRLF, BOM, key order,
item order, blank lines, whitespace all correctly ignored. **Golden-set validation refuses every
malformed id and input the brief listed**, and **the judge-reply taxonomy is right** across
fourteen shapes.

---

# JOB-9 — determinism: the renderer holds, the *claim* does not

**The negative result first, because it is the finding.** ~600 renders across 5 fixtures, 2
interpreters, 300+ `PYTHONHASHSEED` values, 8 timezones and 6 locales produced **one byte-string
per fixture**. Not one row moved, not one digit changed, not one verdict flipped.

```
A  65 renders -> 1 hash    wide 65 -> 1    hostile 65 -> 1    floats 65 -> 1    unsorted 65 -> 1
3.12 vs 3.10: byte-identical.   21 demo runs at seeds 0-20: 21 of 21 NO-GO, one payload hash.
```

**`series.py`'s stated hashing hazard is absent in practice** — the decisive test being a log
whose `thresholds` object is *in the bytes* reverse-alphabetical: it renders following the file,
exactly, on all 108 renders. **The document is a total function of the log bytes**, which is the
property the claim actually needs.

## D1. But a second operator cannot re-render the file they were sent

`report.py:4837` promises: *"a reviewer can re-render a stored evidence log and get the file they
were sent."*

```
$ grep -rn -- '"--now"' src/ scripts/
(no output)
```

**Verified independently.** `render_html_string` and `from_evidence` both accept `now`; `cli.py`
passes `goldenset`, `artifact_dir`, `max_output_chars`, `max_report_chars` — **and not `now`**.
20 CLI renders of one unchanged log: **20 byte-distinct**; blank the timestamp and there is
exactly **one** hash, differing on two lines.

> **The first clause of that sentence is true and achievable — "a test can render twice and
> diff". The second names a *reviewer*, and no shipped code path gives that reviewer the same
> `now`. The hedge and the promise are in one sentence and only the hedge is implementable.**

Three more, all disclosed on the page and none of them hashing: a **moved** log renders *partial*
— gains a "This report is partial" banner, loses the tag distribution, turns `60 / 60` into
`0 / ?` — which **a Windows→Mac transfer trips every time**, because a `C:\` path is never inside
a `/Users/` tree; the overrides that restore the substance print their own provenance lines, so
the nearest achievable re-render **says on its face that it was reconstructed differently**; and
the two version strings and the log path are machine-dependent.

> **What this means for "verified on Windows" in `AUDIT-VERDICTS.md`: those claims hold.** The
> differences are enumerable, disclosed, and confined to four fields. **No statistic, verdict,
> table row, ordering or printed number moved in ~600 renders.** The procedure note is narrow:
> when re-rendering a transferred log, pass the overrides and check the provenance block says so.

**One documentation inconsistency:** the thresholds table has no `| num` filter, so it prints raw
`str(float)` — `0.43448246478317465` beside four-place columns — against `COMPATIBILITY.md`'s
blanket *"the report renders to 4 decimal places"*.
