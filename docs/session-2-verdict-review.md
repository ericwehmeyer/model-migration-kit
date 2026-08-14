# Session 2 contract — adversarial review

Reviewed against `docs/build-plan.md` §§1–4, frozen `contracts.py` / `errors.py`,
`PROGRESS.md` invariants 1/2/4/5/6, and the installed `opik-rigor 0.1.0`
(introspected at `.venv/Lib/site-packages/opik_rigor/`). Every number below was
computed with that interpreter; scratch scripts are in the scratchpad, nothing
under `migration-kit` was touched.

---

## 1. A model that CRASHES gets GO where a model that answers badly gets NO-GO — CONFIRMED

**(a)** Failed completions carry `score=None`, so they cannot enter the
Mann-Whitney arrays; dropping them removes exactly the hardest items from the
candidate's score distribution, which is the opposite of what Amendment 2 claims
to prevent.

**(b)** 40 items × n=5 = 200 completions, floor 0.90, baseline scores 5 throughout.
Candidate A times out on 2 items; candidate B answers those same 2 items badly
(score 1). Both are 190/200 passed, `wilson_lower_bound = 0.9181`, floor cleared.

| candidate | scores fed to `assert_no_regression` | p | verdict |
|---|---|---|---|
| A (crashes on 2 items) | 190 values, the 10 `None`s dropped | **1.0** | **GO** |
| B (answers 2 items badly) | 200 values incl. ten 1.0s | **0.00069** | **NO-GO** |

**(c)** Identical pass rates, opposite verdicts, and the crashing model wins. This
is invariant 6 inverted at the exact stage Amendment 2 says it is being carried
forward. The alternative implementation is no better: passing `None` through
raises `ValueError: current[2] must be a number, got NoneType` from rigor's
`_coerce_scores` (confirmed), so *any* failed completion anywhere makes the whole
comparison unable to produce a verdict.

**(d)** Add to §2: *"Completions with `error is not None` are imputed into the
score distribution at the rubric floor (`SCORE_MIN = 1.0`, read from
`opik_rigor.judge.SCORE_MIN`), not dropped and not passed through as `None`. A
judge that legitimately returns `score=None` on a completion that did succeed is a
missing observation and is excluded from the regression test only; the report row
states both counts (`n_imputed`, `n_missing`) and the comparison refuses to emit
GO if `n_missing / n > 0.05`."* Note that rigor's own prompt instructs the judge to
emit `"score": null` when the rubric gives no basis to score, so partial-`None` is
the normal case, not an edge case — Amendment 4 only covers all-`None`.

---

## 2. `underpowered` measures the wrong test — GO can rest on a regression test with 34% power — CONFIRMED

**(a)** `wilson_lower_bound(n, n, confidence) < floor` is a power statement about
the *pass-rate floor* only; it certifies nothing about Mann-Whitney, which is the
test that actually produces `regressed`.

**(b)** At floor 0.90 the draft calls a run powered from **n = 25** completions
upward (`wilson_lower_bound(25,25,0.95) = 0.9023`; n=24 gives 0.8987). Simulated
power of `assert_no_regression` on binary judge outcomes, alpha 0.05, 3000 trials
per cell:

| n per side | 0.95 → 0.85 | 0.95 → 0.90 | 0.90 → 0.70 |
|---|---|---|---|
| **25** | **33.9%** | **16.6%** | 57.0% |
| 50 | 52.3% | 25.6% | 81.4% |
| 100 | 78.4% | 38.8% | 97.9% |
| 200 | 96.6% | 59.9% | 100% |

**(c)** At n=25 the tool reports "no regression detected" for a real 10-point drop
two times out of three, is not flagged underpowered, and — if the floor is cleared —
emits **GO**. That is invariant 5 defeated by a side door: not REVIEW silently
becoming GO, but a question never asked being reported as answered.

**(d)** Add a second power clause: *"`underpowered` is true if either
(i) `wilson_lower_bound(n, n, confidence) < floor`, or (ii) `n < min_completions`,
or (iii) the regression test could not have detected a drop of
`min_detectable_effect` (new threshold, default 0.10 in pass-rate terms) at
`alpha` with power ≥ 0.80. Clause (iii) is evaluated from a table of pre-computed
n thresholds shipped with the package (n ≥ 200 per side for a 0.10 drop at
alpha=0.05) and the required n is printed in the REVIEW message."* If a full power
clause is out of scope for v0.1, the minimum acceptable fix is raising
`min_completions` to 200 and saying in the appendix what the regression test can
and cannot see below it. `min_completions = 20` (D2) is indefensible on these numbers.

---

## 3. Rule 2 converts rigor's own "underpowered" into NO-GO — CONFIRMED

**(a)** A candidate whose *observed* rate clears the floor but whose lower bound
does not is, by rigor's own definition, an underpowered sample; the draft's rule 2
calls it a demonstrated failure.

**(b)** Floor 0.90, candidate 38/40 (observed 0.950): `wilson_lower_bound = 0.8597`
→ `floor_cleared=False`; `wilson_lower_bound(40,40) = 0.9366` → `underpowered=False`
→ rule 2 fires → **NO-GO**. `assert_pass_rate((38,40), 0.90)` on the same numbers
raises with `exc.stats["underpowered"] = True` and `exc.stats["runs_needed"] = 113`
(verified) and says so in the message: *"this is an underpowered sample, not a
demonstrated failure. 40 runs cannot distinguish a system at 95.0% from one at
86.0%. At this observed rate roughly 113 runs would clear the bar."*

**(c)** The tool blocks a migration that is probably fine and tells the user to fix
the model, when the correct advice is "collect 113 completions". It also
reimplements a weaker version of a primitive rigor already exposes — the thing
invariant 1 and the plan's "every statistical primitive is imported, none
reimplemented" exist to prevent. Same defect for a model truly *at* the floor: at
observed == floor the bound never reaches it at any n, so it is NO-GO forever.

**(d)** Restate rule 2 as: *"else any judge with `observed_rate < floor` →
NO-GO (the bar was missed and more runs will not fix it); else any judge with
`floor_cleared == False` → REVIEW, carrying `runs_needed` from
`assert_pass_rate`'s report dict."* Obtain both `underpowered` and `runs_needed`
by calling `assert_pass_rate` inside `try/except PassRateError` and reading
`exc.stats["underpowered"]` / `exc.stats["runs_needed"]` (the failure branch is the
only place rigor populates those two keys), rather than deriving them.

---

## 4. No multiplicity correction across judges — false NO-GO rate grows with judge count — CONFIRMED

**(a)** Rule 1 is "any judge regressed", each tested at alpha=0.05 independently.

**(b)** Two *identical* models, 40 items × 5, k judges each measuring something
different, 3000 trials: false NO-GO rate **2.10% (k=1) → 4.73% (k=2) → 6.40% (k=3)
→ 9.07% (k=4)**. The nominal independent bound is `1 − 0.95^k` = 18.6% at k=4;
correlated judges land between.

**(c)** Plan §3 requires "identical seeded models → GO, no false alarm over
repeated seeds". With four judges that test fails roughly one run in eleven, and
it will be diagnosed as flakiness rather than as the design.

**(d)** Add to §2: *"`alpha` is the family-wise level across judges. The per-judge
p-values are compared using Holm-Bonferroni: sort ascending, reject the i-th iff
`p_i < alpha / (k - i + 1)`. Both the raw and the adjusted threshold appear in the
report row."*

---

## 5. The flip rule manufactures flips and contradicts the headline rate — CONFIRMED

**(a)** Strict-majority-of-5 turns per-item noise into a binary event, and it
defines "passed" differently from the pooled per-judge rate printed at the top of
the report.

**(b)** Two measurements, 20 000 trials each:
- An item that is genuinely 50/50 under **both** models appears as a flip with
  probability **0.2544** and as a gain with probability **0.2460**. A set with 20
  borderline items yields **~5.1 spurious flips and ~4.9 spurious gains** per run,
  and a different list on every rerun — there is no stability property, stated or
  achievable.
- 10 items, every item passing exactly 3 of 5 samples: pooled headline rate
  **0.60** (`wilson_lower_bound = 0.4838`, floor 0.90 missed → NO-GO) while the
  flip and gain lists are both **empty** and the item pass rate is **10/10**.

**(c)** The human-readable half of the report and the gate disagree about what
"passed" means, in both directions. The flip list is the artifact the plan says a
human actually reads.

**(d)** Add to §2: *"A flip requires a margin, not a majority: item `i` flips iff
`baseline_pass_count[i] >= ceil(0.8 * n)` and `candidate_pass_count[i] <=
floor(0.2 * n)`; gains mirror it. Items that move by less are listed separately as
`unstable` with both counts shown and are never presented as evidence. Every flip
row prints `k/n → k'/n`. The report states in one sentence that the headline rate
is per completion and the flip list is per item, and prints the item-level pass
rate alongside the completion-level one."*

---

## 6. `judges_hash` is order-dependent under duplicate judge names, which also collide the resume key — CONFIRMED

**(a)** Sorting by `name` alone is stable, so equal names keep file order; nothing
in the draft forbids two judges sharing a name.

**(b)** Two judges both named `"x"` with different models/rubrics: hashing
`sorted(entries, key=name)` gives `4cb1499964802c55` for one file order and
`765ba2e3970fefe0` for the reverse — same instruments, `JudgeConfigError`, wrongly
rejected. Worse, the same config breaks three other things at once: the
`JudgedArtifact` record key is `(judge, item_id, sample_index)` where `judge` is
the *name*, so the second judge's verdicts collide with the first's and
resumption skips them as already done; and rigor's `PinnedJudge.__init__` looks up
`evidence.last(EVENT_JUDGE_INIT, judge=name)` — filtered on the name only
(confirmed in source) — so constructing the second judge raises
`RubricDriftError` against the first judge's rubric hash. With distinct names,
reordering the file is hash-stable (verified).

**(c)** Either a spurious comparability failure or silent under-judging, depending
on which of the three fires first.

**(d)** Add to §1: *"Judge names must be unique; a duplicate is a `ConfigError` at
load, because the name is the key for the resume index, for rigor's rubric-drift
lookup, and for the report row. The hash sorts on the full tuple `(name, model,
rubric_hash)`."* Also add the judge **adapter class name** to the hashed tuple —
`AnthropicAdapter` and `OpenAICompatAdapter` serving the same `model_id` currently
hash equal but are not the same instrument.

---

## 7. Resumability, the tolerance denominator, and evidence-log double counting are unspecified — CONFIRMED (gap)

**(a)** Frozen behaviours 2–4 do not say what the parse-failure denominator is
scoped to, what happens to an already-recorded parse failure on resume, or which
evidence log each side is judged into.

**(b)** Three concrete breaks. (i) A run of 400 completions resumes with 5
remaining, 1 of which fails to parse: `failures/judged = 1/5 = 20% > 5%` →
`JudgeReliabilityError` on a healthy run — or, scoped the other way, a rubric
failing 30% passes because each resumed chunk is small. (ii) `EvidenceLog` exposes
only `append`, `last`, `read` (confirmed — no run scoping, no dedupe), and
`PinnedJudge.evaluate` writes one `judge.verdict` per call; invariant 2 says the
report renders from the log, so a resumed or re-run judging pass double-counts
every re-judged triple in the report even though the artifact is clean.
(iii) Nothing says whether baseline and candidate are judged into one log or two:
into one, `RubricDriftError` guards a mid-comparison rubric edit; into two, it
cannot fire at all and only `judges_hash` catches it.

**(c)** Either a false abort, a false green, or a report whose numbers disagree
with the artifact it was rendered from.

**(d)** Add to §1: *"Parse-failure tolerance is evaluated over the complete
`JudgedArtifact` for that judge (records already on disk plus records written this
pass), never over the current process's slice. A record with `error` set is
terminal: resume does not retry it, so counts are idempotent. Baseline and
candidate are judged into the **same** evidence log so rigor's rubric-drift check
spans both sides; the report deduplicates `judge.verdict` records by
`(judge, item_id, sample_index)`, keeping the last, and states the number of
superseded records."*

---

## 8. Two of the eight verdict rows are unreachable, and `min_completions` is inert — CONFIRMED

**(a)** `floor_cleared` and `underpowered` are not independent:
`wilson_lower_bound(s, n) <= wilson_lower_bound(n, n)`, so `floor_cleared=True`
forces clause (i) of `underpowered` to be False.

**(b)** Clause (ii) is the only way to reach `floor_cleared ∧ underpowered`, and it
requires `min_completions` above the clause-(i) threshold. Smallest n at which a
perfect record clears the floor, confidence 0.95: **floor 0.80 → n = 11**
(bound 0.8026), **floor 0.90 → n = 25** (0.9023), **floor 0.95 → n = 52** (0.9505).
At the drafted defaults (floor 0.90, `min_completions = 20`), 20 < 25, so clause
(ii) never fires and rows `(T,T,T)` and `(F,T,T)` are **unreachable** — 6 live rows,
not 8.

**(c)** The §3 test "verdict table matrix-tested over all 8 combinations" either
fails to construct two rows from real data or constructs them by injecting flags
directly, in which case it tests two states the system cannot enter and gives
false confidence about coverage.

**(d)** Add to §3: *"The matrix test drives the resolution function directly on all
8 flag tuples (it is a pure function of three booleans and must be total), and a
separate test asserts that `(floor_cleared and underpowered_clause_i)` is
unreachable. Config validation raises `ConfigError` if `min_completions` is below
the smallest n at which `wilson_lower_bound(n, n, confidence) >= floor`, so
`min_completions` is never inert; at the default floor 0.90 that minimum is 25."*

---

## 9. The guard list omits sample-set equality, and Mann-Whitney has a hard floor at tiny n — CONFIRMED

**(a)** The guards check `goldenset_hash`, `judges_hash`, `model_id`, `n_per_item`,
but never that the two artifacts contain the same completions.

**(b)** Invariant 2 guarantees partial artifacts exist. A baseline truncated at 5
of 40 items compared against a complete candidate passes every stated guard: same
golden set, same judges, same `n_per_item`. Mann-Whitney then runs on 25 vs 200
unpaired values, and the flip analysis silently ignores 35 items. Exact minimum
achievable p (complete separation, distinct values): **n1=n2=3 → p = 0.0500**,
which never satisfies `p < 0.05` — a 1-item golden set at `n_per_item=3` can never
report a regression. `n1=1` needs `n2 >= 20` (p = 0.048) before p<0.05 is reachable
at all. Separately, with tied data — which is exactly what Amendment 4 creates —
scipy drops to the asymptotic method: 3-vs-3 all-fail against all-pass returns
**p = 0.0234**, i.e. significance where the exact test's floor is 0.0500.

**(c)** Comparisons of unrelated sample sizes, silently-dropped items, and both a
blind spot and an anticonservative spot at small n.

**(d)** Add to §2 guards: *"the multiset of `(item_id, sample_index)` keys must be
identical on both sides → `ArtifactError` naming the count on each side; a partial
artifact is compared only via an explicit `--intersect` flag, which prints the
number of items dropped and forces REVIEW."* And to Amendment 4: *"where the
regression test runs on booleans, `n_current` and `n_baseline` must both be ≥ 25
or the judge is marked underpowered; below that the tied-data normal approximation
is not trustworthy."*

---

## 10. Amendment 4 as written raises `ValueError` inside rigor — CONFIRMED

**(a)** "runs on the boolean outcomes as 1.0/0.0" is only safe if the conversion is
explicit; rigor rejects bools by design.

**(b)** `assert_no_regression([True, False, True, True], [...])` →
`ValueError: current[0] must be a number, got bool True`. `_coerce_scores` special-cases
`isinstance(value, bool)` precisely to stop an outcome list being read as scores.

**(c)** A one-line implementation of the amendment fails at runtime rather than at
review.

**(d)** Word it as: *"...the regression test runs on `float(v.passed)` per
completion — the explicit cast is required, `opik_rigor` rejects a sequence of
bools with `ValueError` by design — and the report row says the test ran on
outcomes, not scores."*

---

# Checked and found sound

- **Precedence chain internal consistency.** Rules 1–4 are an if/elif chain over a
  total function of three booleans; no two can fire, all 8 tuples resolve, and
  there is **no path from REVIEW to GO** — GO requires every judge non-regressed,
  floor-cleared and powered. Invariant 5 holds structurally. My complaints are
  about how the three flags are *computed* (findings 2, 3), not about the ordering.
  "Regressed while underpowered → NO-GO" is right: significance reached is
  significance reached. "Floor missed while underpowered → REVIEW" (row F,F,T) is
  right and is the row that carries the plan's intent.
- **Every stated fact about `opik-rigor 0.1.0` in §0 is accurate.** Verified by
  introspection: `PinnedJudge` signature and its `require_pinned` at construction;
  drift detection via `evidence.last(EVENT_JUDGE_INIT, judge=name)`; `Verdict`
  fields; `JudgeOutputError` raised *after* the raw text is recorded (as a distinct
  `judge.parse_failure` event, which is worth naming in the contract so the count
  is recoverable from the log); `assert_pass_rate` gating on
  `wilson_lower_bound >= min_rate`; `assert_no_regression` = one-sided
  `mannwhitneyu(current, baseline, alternative="less")` raising iff `p < alpha`,
  argument order as stated; `wilson_interval` two-sided vs `wilson_lower_bound`
  one-sided. `JudgeReliabilityError(judge_name, failures, total, tolerance)` in the
  frozen `errors.py` matches the arity Amendment 3 assumes.
- **Rubric hashing is genuinely identical.** `opik_rigor.judge.hash_rubric_text`
  and `migration_kit.contracts.hash_bytes` return the same digest on plain, CRLF,
  and trailing-whitespace inputs (verified). A rubric differing only by trailing
  whitespace therefore hashes differently and is rejected — that is *correct*, since
  rigor would raise `RubricDriftError` on the same edit; consistency beats
  leniency here and Amendment 1's content-not-path rule is right.
- **Failed completions in the pass-rate denominator is correct for the Wilson
  gate.** A crashing candidate and a badly-answering one produce identical
  `successes/n` and identical lower bounds (verified 80/100, bound 0.7267). The
  distortion is confined to the regression test — finding 1.
- **Pooled completions do not break the Wilson bound.** I attacked the
  pseudo-replication angle and it did not reproduce: with 20 items × 5 samples and
  per-item difficulty from Beta(9,1), the pooled lower bound exceeded the
  fixed-set truth in **3.60%** of 4000 runs against a 5% budget (conservative,
  because a Poisson-binomial is less variable than a binomial of the same mean),
  while an item-majority bound breached at **5.97%**. Pooling per completion is the
  right choice for the gate; only the flip list needs the item as its unit
  (finding 5). Likewise the Mann-Whitney false-positive rate on clustered binary
  data measured **4.15%** against nominal 5% — clustering alone is not the problem,
  judge multiplicity is (finding 4).
- **The `Verdict` name-collision rule** (`from opik_rigor import Verdict as
  JudgeVerdict`) is correct and worth keeping — both classes are real and both are
  in scope in `judging.py`.
- **Amendments 1 (content hash, path-independent), 2 (failed completion is not a
  judge parse failure) and 3 (complete the pass before raising, so the count is
  true)** are each well-reasoned and I found no counterexample to their rationales.
- **D4 (gains do not offset regressions)** is right and should stay a "no".
- **D5**: the same-`model_id` guard is defensible but note it blocks the natural
  A/A calibration that plan §3's "identical seeded models → GO" test wants; a
  `--calibration` flag that keeps the guard on by default is the cheap answer.

**Note on the review baseline.** `contracts.py` and `errors.py` are marked frozen
but are currently modified in the working tree by the Session 1 agents (additive
only: a new `ReportError`, and additions to `contracts.py`). Nothing I relied on
changed — `Verdict`, `Completion`, `hash_bytes`, `canonical_json` and
`JudgeReliabilityError`'s arity are all as reviewed. I read only; the repo is
untouched by me.
