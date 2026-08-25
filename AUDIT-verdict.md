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
