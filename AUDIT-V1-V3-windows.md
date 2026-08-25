# Windows reproduction of AUDIT-verdict.md V1, V2 and V3

The macbook branch's V1–V3 were a subagent's results, landed at that session's
token limit and never independently re-run; its own provenance note asked
Windows to reproduce them before anyone acted. This is that run. **Nothing in
`src/` was changed and nothing was fixed** — a fix written by the reasoning that
found the defect is not independent confirmation of it.

**Verdicts: V1 CONFIRMED. V2 PARTIAL. V3 CONFIRMED, and understated.**

---

## What was run, and whether it met the standard

| | |
|---|---|
| Tree under test | `9599aa0` (branch `audit/v1-v3-repro`, worktree `mk-v1v3-repro`) |
| Original's tree | `8f39878` — 42 commits behind, and **`comparison.py`, `judging.py`, `runner.py` and `goldenset.py` are byte-identical between the two** (`git diff --quiet 8f39878 HEAD -- src/model_migration_kit/<file>.py`). `report.py` differs by 1267 insertions, but the `imputed` row at `report.py:5452-5454` is character-identical to the same row at `8f39878:report.py:4359-4361`. |
| Interpreter | `C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe`, CPython 3.14.4 |
| Harness | `scripts/audit/v1_v3_repro.py` |

**The no-hand-edited-payload standard is met.** Every case below is built the way
the original claims its cases were built, and the claim is checkable line by
line in the harness:

* two scripted `FakeAdapter` models, keyed by prompt, exactly as
  `demo.build_adapters` builds them;
* the real `runner.run_goldenset` at `concurrency=1`;
* a real `PinnedJudge` panel built by `JudgeConfig.build(evidence, adapter_for)`
  over the bundled `demo.toml` and `demo_rubric.md` — rubric hashing, drift
  detection and rigor's strict JSON parse all run;
* the real `judging.judge_artifact`, so **every `JudgeRecord` is written by
  `judging._grade` out of a response the judge emitted**, and every
  `JudgedArtifact` is re-read from disk;
* the real `comparison.compare` into a real `opik_rigor.EvidenceLog`;
* the real report, rebuilt from that log by `ReportModel.from_evidence` — the
  same reconstruction `migkit report` runs — and rendered by
  `render_html_string`.

The one substitution is at rigor's `Adapter` seam, which is where
`model_migration_kit.demo` already substitutes and the only place a keyless run
is allowed to differ from one that costs money. It is not a loophole here: it is
forced. `cli._judge_adapter` refuses `adapter = "fake"` outright, so **`migkit
compare` cannot reach any of these cases without provider credentials**; the
harness wires the same panel `demo.run_demo` wires. No JSONL line, no artifact
and no evidence record was edited after it was written.

Two properties worth stating because they are what make the numbers quotable.
The judge distinguishes the two sides by reading a marker phrase out of the
*model output text* — it never sees a model id, as the demo's judge never does.
And the whole harness is deterministic: `--case all` was run twice into separate
directories and every field of all seven results but `work_dir` was identical.

**One convention note.** The brief said to read `scripts/audit/README.md` first.
**That file does not exist** on this branch or on `main` — `scripts/audit/`
contains `netguard.py` and `shuffle_order.py` and nothing else. Conventions were
taken from those two: an `"""Audit-only ..."""` module docstring that says what
the script proves and what it deliberately does not touch, stdlib plus the
project's own imports only, and nothing installed into the shared venv.

### Re-running it

```bash
cd C:/Users/ewehm/repos/mk-v1v3-repro
PY=C:/Users/ewehm/repos/migration-kit/.venv/Scripts/python.exe

# the seven headline cases
$PY scripts/audit/v1_v3_repro.py --case all --out-dir <dir>

# one case, keeping the evidence log, artifacts and rendered HTML
$PY scripts/audit/v1_v3_repro.py --case v3-null --out-dir <dir>

# how far each route reaches
$PY scripts/audit/v1_v3_repro.py --sweep v2 --out-dir <dir>
$PY scripts/audit/v1_v3_repro.py --sweep v3 --null-counts 1,2,3,4,5,6,7,8,9 --out-dir <dir>
```

`PYTHONPATH=<worktree>/src` was set on every invocation. It should be
unnecessary since the `.pth` fix, and it was set anyway because a reproduction
that imports another checkout's code is worthless.

---

## V1 — a GO purchased with duplicate draws: **CONFIRMED**

Twelve items, both models passing every item every time, only `--n` varied.

| | claimed | observed |
|---|---|---|
| 12 x 1 — verdict / rule | REVIEW, rule 3 | **REVIEW, rule 3** |
| 12 x 1 — `floor_cleared` / `mw_powered` / `n_obs` | False / False / 12 | **False / False / 12** |
| 12 x 1 — Wilson lower bound | 0.8160 \| 0.8160 | **0.8160188 \| 0.8160188** |
| 12 x 5 — verdict / rule | GO, rule 5 | **GO, rule 5** |
| 12 x 5 — `floor_cleared` / `mw_powered` / `n_obs` | True / True / 60 | **True / True / 60** |
| 12 x 5 — Wilson lower bound | 0.9569 \| 0.9569 | **0.9568532 \| 0.9568532** |
| items pass/fail/unstable, both arms, both sides | 12 / 0 / 0 | **12 / 0 / 0** |
| `n_required` | ~56 | **56** |

Zero flips, zero gains, pass rate 1.0000 on both sides in both arms, `p = 1.0`
and `test that actually ran = mann-whitney-u` in both. Every digit the original
printed is reproduced, including both Wilson bounds.

The item-level truth is identical across the two arms and the verdict is not.
The only thing that moved is how many times each of the same twelve items was
asked.

### The extreme, tested separately: **CONFIRMED**

A **one-item golden set asked 60 times is a GO.**

```
1 item x 60 draws -> GO  rule 5  floor_cleared=True  mw_powered=True  n_obs=60
                     Wilson lower 0.9568532 | 0.9568532   items 1 / 0 / 0
```

and the rendered methodology appendix says, in the report of a run whose entire
evidence is one question:

> **60** completions per side observed against roughly 56 required for that
> effect at that power, so this judge is powered for the question.

The bounds are identical to the 12 x 5 arm's, digit for digit, because both are
60 completions — which is the finding.

### Mechanism: **confirmed, with one wording correction**

* `comparison.py:1132` — `n_observed = min(len(base_counted), len(cand_counted))`.
  `_counted` (`comparison.py:1184`) drops parse failures and nothing else, so
  these are completions. `grep -n n_per_item src/model_migration_kit/comparison.py`
  finds it only in the artifact-provenance passthrough: **nothing anywhere on
  this path divides by draws per item.**
* `comparison.py:1133` — `powered = bool(n_required is not None and n_observed >= n_required)`.
* `required_sample_size` (`comparison.py:163`) computes the 56 from
  `p1*(1-p1) + p2*(1-p2)`, the two-proportion normal approximation's variance
  term — which is the variance of a binomial proportion and therefore assumes
  independent trials. Five draws of one item are not five independent trials.
* `runner.py:327` — `if not isinstance(n, int) or isinstance(n, bool) or n < 1`.
  The only bound on `--n` is `>= 1`.
* `goldenset.py:128-132` — refuses only the *empty* set; one item loads.

**The correction.** The original writes that `required_sample_size`'s "own
docstring derives 56 from **independent Bernoulli trials**." The word
*Bernoulli* appears nowhere in `src/` (`grep -rn "Bernoulli\|bernoulli" src/`
returns nothing), and the docstring's own noun is "**Completions** per side
needed". The independence assumption is real and is in the formula; the
attribution to the docstring's wording is a paraphrase presented as a quote. The
substance of the mechanism holds; that sentence should not be quoted as written.

---

## V2 — a GO bought by the judge declining to give a number: **PARTIAL**

20 items x 5 draws, all passing on both sides, the candidate's `score` field the
only thing that changes between the two runs.

**The isolation is measured, not asserted.** Comparing the control's artifacts
against the variant's, field by field: all 200 completions (`item_id`,
`sample_index`, `output`, `ok`) are identical; all 200 judged records are
identical on `passed`, `imputed` and `parse_failure`; and exactly **99 of the
200 `score` cells differ, all of them on the candidate side.** The same
comparison for V3 gives 99 differing score cells, all on the baseline side. The
artifact files themselves are not byte-identical only because each case ran in
its own directory and the header records that path and the wall-clock timings.

| | claimed | observed |
|---|---|---|
| control — verdict / rule | NO-GO, rule 1 | **NO-GO, rule 1** |
| control — p | 1.76e-45 | **1.7608079411973e-45** |
| 99 of 100 null — verdict / rule | GO, rule 5 | **GO, rule 5** |
| 99 of 100 null — p | **1.0** | **0.1610870595108309** |
| 99 of 100 null — page prints | `p-value 1.000000` | **`0.161087`** |
| 99 of 100 null — `test that actually ran` | `mann-whitney-u` | **`mann-whitney-u`** |

**The headline holds exactly and the p-value does not.** A judge that declined
to put a number on 99 of the candidate's 100 completions converts a NO-GO into a
GO, on the default config, with the item-level picture unchanged — 20/0/0 both
sides, zero flips, pass rate 1.0000 both sides. The control's p reproduces to
three significant figures. But the variant's p is **0.161087**, not 1.0, and the
page prints 0.161087.

That is not a rounding disagreement, and the arithmetic says it cannot be one.
With 99 candidate records filled at `SCORE_MAX` and one real 2.0 against 100
baseline fives, U is 5050 against a null mean of 5000 with a tie-corrected sigma
of 50 — one standard deviation off, which is a p in the tenths. A p of exactly
1.0 is unreachable in that configuration.

**Where the 1.0 comes from — the original conflated two adjacent cases.** Push
one record further, to *all 100* candidate records unscored, and the p is
exactly 1.0:

```
100 of 100 null -> GO  rule 5  p = 1.000000  test = mann-whitney-u-on-outcomes
```

At 100 the candidate has no numeric score at all, `_scores`
(`comparison.py:1314`) fails its `base_numeric and cand_numeric` test, and the
comparison falls out of the imputation branch into the pass/fail-outcomes
branch. So the original's V2 row states the verdict and the test name of the
99-null case beside the p-value of the 100-null case. Both cases are real, both
are GO, and they are not the same case.

**The 100-null case is the worse of the two and the original does not mention
it.** Because the imputation branch is never entered, `missing_scores` is
`0 | 0`, **no imputation warning fires at all**, and the page's note reads:

> scores absent; tested on pass/fail outcomes

on a run where the baseline supplied 100 perfectly good scores. A GO with every
numeric disclosure reading zero.

### How far the 99-null route reaches — narrower than one might assume

`--sweep v2`, unscored candidate records out of 100:

| nulls | 0 | 10 | 25 | 50 | 80 | 90 | 95 | **99** | 100 |
|---|---|---|---|---|---|---|---|---|---|
| verdict | NO-GO | NO-GO | NO-GO | NO-GO | NO-GO | NO-GO | NO-GO | **GO** | GO |
| p | 1.8e-45 | 1.4e-37 | 4.3e-28 | 1.9e-16 | 1.3e-06 | 6.1e-04 | 1.2e-02 | **0.161** | 1.000 |

The route needs a judge that has gone essentially completely silent. The
original quoted the one point that works and did not overstate the reach.

### Mechanism: **confirmed**

* `comparison.py:1336` `_impute_unscored`, filling at `comparison.py:1380` —
  `scores.append(SCORE_MAX if one.passed else SCORE_MIN)`. `SCORE_MIN`/`SCORE_MAX`
  are imported from `opik_rigor` at `comparison.py:89-90` and are 1.0 / 5.0.
* `comparison.py:1314-1318` — the branch that reaches the fill, gated on both
  sides having at least one numeric score. This is the gate the 100-null case
  falls out of.
* `judging.py:762` — `score=None if verdict.score is None else float(verdict.score)`.
  A null score is recorded as a normal record with `parse_failure=False`, so
  `judge_failure_tolerance` never sees it and judging does not abort. Confirmed
  as stated, at the stated line.
* Reachable on the default config: the harness ran the bundled `demo.toml`
  thresholds unmodified, and a null `score` is rigor's documented prompt
  contract — `pass` required, `score` optional (`opik_rigor/judge.py:413-422`
  accepts `"score": null` without complaint).
* The disclosure that does fire at 99: `judge 'accuracy': 99.0% of completions
  on one side carried no numeric score and were imputed at the rubric bound
  their pass/fail verdict implies. The verdict rests on that assumption.`

---

## V3 — silence on the baseline manufactures a NO-GO: **CONFIRMED, and understated**

Same 20 items x 5 draws, both sides scored 2.0 in the control, all passing.

| | claimed | observed |
|---|---|---|
| control — verdict | GO | **GO, rule 5** |
| control — p | 1.0 | **1.0** (page: `1.000000`) |
| 99 baseline null — verdict | NO-GO | **NO-GO, rule 1** |
| 99 baseline null — p | 1.28e-44 | **1.2753108447030764e-44** |
| completions passing, both sides | 100/100 | **100/100** |
| items pass/fail/unstable, both sides | 20 / 0 / 0 | **20 / 0 / 0** |
| flips | 0 | **0** |

Both the verdict flip and the p-value reproduce.

### The `imputed | 0 | 0` row, tested separately: **CONFIRMED**

The rendered HTML of the 99-baseline-null run carries, verbatim:

```html
<tr><td>imputed (failed completions scored at the floor)</td>
    <td class="num">0</td>
    <td class="num">0</td></tr>
```

on the page whose NO-GO rests entirely on 99 records `_impute_unscored` filled.

Mechanism, at file and line:

* `comparison.py:1172-1173` — `imputed_baseline=sum(1 for one in base_records if one.imputed)`.
  `JudgeRecord.imputed` is set in exactly one place, `judging.py:734-740`, for a
  completion that *failed and has no output to grade*. `_impute_unscored` sets
  nothing on the record; it returns a bare `list[float]`.
* `report.py:5452-5454` renders that field, through `report.py:3316/3341-3342`,
  which read the evidence payload's `imputed` dict written at
  `comparison.py:647-649`.

**One thing sharper than the original says it.** The count `_impute_unscored`
actually produced *is* computed and *is* written to the evidence log —
`missing_scores` at `comparison.py:655-657`, from `comparison.py:1176-1177`. But
`grep -n missing_scores src/model_migration_kit/report.py` returns **nothing**:
the renderer never reads it. So there is no numeric row anywhere on the page for
the 99. The only rendered trace is one prose sentence in the `note` row, beside
a numeric row labelled `imputed` that says `0 | 0`. A reader scanning the
numbers is not merely reading the wrong count — the right count is not on the
page in numeric form at all.

### Reachability: 3 records, not 99 — the original understated this by a factor of 33

`--sweep v3`, unscored **baseline** records out of 100:

| nulls | 1 | 2 | **3** | 4 | 5 | 10 | 99 |
|---|---|---|---|---|---|---|---|
| verdict | GO | GO | **NO-GO** | NO-GO | NO-GO | NO-GO | NO-GO |
| p | 0.161 | 0.0792 | **0.0414** | 0.0222 | 0.0121 | 6.1e-04 | 1.3e-44 |
| warning fires | no | no | **no** | **no** | yes | yes | yes |

**Three unscored baseline records out of 200 — 3% of one side — turn a GO into a
NO-GO.** The models are identical to the ones the GO was issued for: 100/100
passing on both sides, 20/0/0 items, zero flips, and the same 2.0 everywhere the
judge did give a number.

And at 3 and at 4, **the `judge_failure_tolerance` warning does not fire**,
because 3/100 and 4/100 are under the 0.05 default. What the page carries at
that point is: `imputed | 0 | 0`, no warning banner, and one `note` sentence
reading *"3 baseline and 0 candidate record(s) carried no numeric score and were
imputed at the rubric bound their own pass/fail verdict implies."* That sentence
is the entire disclosure standing behind a NO-GO that the evidence does not
support.

The original chose 99 to make the p-value dramatic. The number that should be
carried forward is **3**, because a judge going 3% quiet is an ordinary Tuesday
and a judge going 99% quiet is not.

---

## Summary of what the original over- and understated

| | |
|---|---|
| **V1** | Reproduced digit for digit, including the extreme. One wording defect: `required_sample_size`'s docstring does not say "independent Bernoulli trials" and the word does not occur in `src/` at all — the assumption is in the formula, not in the prose the original quoted. |
| **V2, overstated** | `p = 1.0` is wrong for the case described. The 99-null run gives **0.161087** and the page prints 0.161087. The verdict flip, the rule, the test name and the control's 1.76e-45 are all exact. |
| **V2, understated** | The 100-null case is a **second, cleaner** route to the same GO — `p = 1.000000`, and every numeric disclosure reads zero because the imputation branch is never entered, with the note claiming "scores absent" on a run whose baseline scored all 100. The original does not mention it, and it is the case its own quoted p-value belongs to. |
| **V3, understated** | The threshold is **3 unscored baseline records**, not 99 — and at 3 and 4 no warning fires, because they sit under `judge_failure_tolerance`. |
| **V3, understated** | The count `_impute_unscored` produced reaches the evidence log as `missing_scores` and is **never rendered as a number** anywhere. `report.py` does not contain the string. |

No claim in V1, V2 or V3 was found to be unreachable, and no symptom failed to
reproduce. Nothing in `src/` was modified: `git status` is clean apart from this
document and `scripts/audit/v1_v3_repro.py`.
