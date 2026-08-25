# JOB-16 — the statistics themselves

The job the hibernation lost, restarted 2026-08-25 05:50Z. It was flagged in
`AUDIT-HANDOFF-macbook.md` §7a as *"the highest-value one, whose first check is
confirming the Mann-Whitney operands are not transposed"* — because a transposition
there inverts every regression verdict in the product with no error anywhere, and
nothing downstream could notice.

**Headline: they are not transposed. The first check comes back clean, and the
coverage that makes that worth saying is named below.**

This project's own instruction on negative results is the reason this file exists
at all: *"A negative result that names its coverage is worth having; a negative
result that does not is worth nothing."*

---

## S1 — the Mann-Whitney operand order. **REFUTED as a defect — the orientation is correct**

### Why it would matter

`comparison.py:1403` states the stake plainly, and is right to:

> rigor's one-sided Mann-Whitney, in ``(current, baseline)`` order. The order is
> the whole meaning of the test: ``alternative="less"`` asks whether the
> *candidate* is stochastically smaller than the baseline, and reversing the
> arguments inverts that silently — **a regression would read as an improvement,
> with no error anywhere.**

A comment asserting the order is not evidence of the order. Checked two ways.

### Check 1 — the static chain, end to end

```
comparison.py:1037   base_scores, cand_scores, ... = _scores(base_counted, cand_counted)
comparison.py:1041   regression = _regression(cand_scores, base_scores, thresholds, evidence, name)
comparison.py:1396   def _regression(candidate_scores, baseline_scores, ...)
comparison.py:1417       assert_no_regression(list(candidate_scores), list(baseline_scores), ...)
```

and `_scores` returns baseline-first on **both** of its return paths, matching the
unpacking at the call site:

```python
        return base_filled, cand_filled, TEST_SCORES, note, missing
    return (
        [float(one.passed) for one in base],
        [float(one.passed) for one in cand],
```

Four links, no swap. Note that the *names* alone would not settle it — the risk is
a chain whose names stay right while the values cross — which is why the arrays are
traced back to `base`/`cand` parameters rather than to their labels.

### Check 2 — the sign of the answer, from outside the code under audit

The decisive test. Drive rigor directly with a candidate that is unambiguously
worse and one that is unambiguously better, and look at which way `p` moves:

```
$ .venv/bin/python -c '<assert_no_regression, alpha=0.05, 12 values a side>'
as called by comparison.py -- _regression(candidate, baseline):
  candidate WORSE  -> p = 2.6336842274600676e-06  (expect SMALL = regression detected)
  candidate BETTER -> p = 0.9999984919056873      (expect LARGE  = no regression)

if the operands were transposed, you would instead see:
  candidate WORSE  -> p = 0.9999980583937971
  candidate BETTER -> p = 2.059313963442551e-06
```

The orientation is correct, and the transposed run confirms the docstring's claim
about the failure mode: **the same four numbers, the opposite verdict, and nothing
raises.** The two rows are near-mirror images, which is also the reason no
downstream sanity check could ever catch it — a transposed p-value is a perfectly
well-formed p-value.

**Verdict: REFUTED.** There is no defect here. Recorded because the check is
expensive to re-derive and cheap to re-run, and because *"the operands are fine"*
is only useful if the next person can see how far the claim reaches.

---

## S2 — Holm-Bonferroni. **Correct, including the two things most implementations get wrong**

`comparison.py:224`. Read against the procedure rather than against its docstring:

| requirement | in the code | verdict |
|---|---|---|
| sort ascending | `order = sorted(range(len(values)), key=lambda i: values[i])` | correct |
| i-th smallest tested at `alpha / (k - i + 1)` | `threshold = alpha / (k - rank)`, `rank` 0-indexed | correct |
| **step down** — once one fails, no larger p is rejected | `still_rejecting` latch | correct |
| results returned in *input* order, not sorted order | `out[index] = (rejected, threshold)` | correct |
| an empty family is not a verdict | returns `()` | correct |

The step-down latch and the return-in-input-order are the two an implementation
usually drops, and dropping either is invisible in the common case where every
p-value is tiny.

### The NaN guard is the good part, and it is guarded for the right reason

```python
values = [_finite_p(p) for p in p_values]
```

The docstring's measurement of what happens without it is worth keeping visible:

> ``[nan, .001, .001, .001]`` rejects nothing at all, while ``[.001, .001, .001,
> nan]`` rejects three — the same four p-values, one reordering, and the
> difference between NO-GO and GO.

That is a genuine order-dependence in a decision that must not have one, and it is
closed. Reading NaN as `1.0` rather than dropping it is also the right call for
this project's rule: dropping would shrink `k` and silently loosen every other
judge's threshold, and `_compare_one_judge` warns separately so *"read as no
regression"* never renders as *"no regression"*.

### S2b — the rejection boundary is strict `<` where the procedure is `≤`. **Real, and NOT REACHABLE here. Reported as a note, not a finding**

```
$ holm_bonferroni([0.05], alpha=0.05)     -> ((False, 0.05),)      # not rejected
$ holm_bonferroni([0.049999999], alpha=0.05) -> ((True, 0.05),)    # rejected
```

Holm rejects at `p ≤ alpha/(k-i+1)`; this rejects at `p <`. The deviation matters
only at exact float equality, and it errs toward **not** declaring a regression —
the unsafe direction for this product, which is why it is worth chasing rather
than shrugging at.

**It does not reach.** An exact one-sided Mann-Whitney p-value is
`count / C(n+m, n)`, so `p = 0.05` exactly requires `1/20`, i.e. `C(6,3)` — a
3-vs-3 comparison with **six distinct values**. This pipeline cannot produce one:
scores are a 1–5 integer rubric, `_impute_unscored` fills at a rubric bound rather
than at a new value, and any tie in the combined sample sends scipy to the
asymptotic method, which returns irrational-looking floats. Measured:

```
3 vs 3, candidate all-worse: 0.023427088801936884     # not 0.05 -- ties -> asymptotic
4 vs 4, candidate all-worse: 0.006561903392185358
```

`comparison.py:1308` already documents this exact corner from the other side
(*"3-vs-3 all-fail against all-pass returns p = 0.0234 where the exact test's floor
is 0.0500"*), which is independent confirmation that the tied path is the one
that runs.

*Adversarial:* attempted refutation — *"alpha is configurable, so a user could set
one that lands exactly on a p-value."* **Succeeds only in theory.** It requires the
user to choose an alpha equal to a specific irrational-looking float such as
`0.023427088801936884`, or to `alpha/2` at two judges. Not a path anyone reaches by
accident, and not one worth hardening against. **Left as a note so the next reader
does not spend the hour I spent on it**, not filed as a defect.

---

## S3 — coverage: what this file does and does not cover

Named, because a negative result without its coverage is worth nothing.

**Checked:** the Mann-Whitney operand order (statically and by sign); the
Holm-Bonferroni procedure against its five requirements; the NaN guard; the
rejection boundary and its reachability.

**Not checked here, and still open:**

- **The `n` the test is run over.** Already a landed finding elsewhere, not a gap:
  `scripts/audit/recompute.py` reports the demo's p-value **twice** — `p = 0.0078`
  over the 60 completions the payload reports, against `p = 0.153` over the 12
  independent items the demo actually has, with a Wilson interval roughly half the
  width the independent data supports. That is the largest statistical finding on
  this project and it is about the *unit*, not the test.
- **The Wilson interval arithmetic** — `recompute.py` re-derives it from the
  formula rather than calling `opik_rigor.wilson_interval`, deliberately, so the
  recomputation does not test the call site of the code under audit.
- **The power calculation** (`_floor_power`, `min_detectable_effect`,
  `runs_needed`). Untouched. The module docstring's claim of *"33.9% simulated
  power at n=25 per side"* is unverified by me.
- **`assert_pass_rate` and the floor gate's own statistics.** Untouched here;
  `AUDIT-verdict.md` covers the decision rules that consume them.
- **Ties, and scipy's exact-vs-asymptotic switch, as a class.** Only probed at the
  3-vs-3 and 4-vs-4 corners above.

**Read S1 as exactly what it says:** the operands are in the right order and the
sign of the answer proves it. It is not a statement that the number is computed
over the right population — and on this project, that second question already has
a finding against it.
