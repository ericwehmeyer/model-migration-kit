# Session 2 — module contract for `judging.py` and `comparison.py`

Frozen before any code is written against it, as `contracts.py` was before Session
1. The *verdict logic* is not here: it is [build-plan.md §6](build-plan.md), because
changing it requires editing the plan, and this document must never be the place
someone quietly redefines what GO means.

This contract is the second draft. The first was adversarially stress-tested with
simulation against the installed opik-rigor before implementation, which confirmed
ten defects — recorded verbatim in
[session-2-verdict-review.md](session-2-verdict-review.md), including the two
findings that did *not* reproduce, since a review is evidence only if its negative
results are reported too.

## 0. Verified facts about the dependency

Introspected from `opik-rigor 0.1.0`, not assumed:

- `PinnedJudge(adapter, rubric_path, evidence, *, name="default",
  accept_rubric_change=False)`. Construction calls `require_pinned` on the judge's
  model id, so an alias is refused at construction rather than at analysis time,
  and compares the rubric hash against the last `judge.init` record **for that
  judge name in that evidence log**, raising `RubricDriftError` on a change.
- `evaluate(input, output) -> opik_rigor.Verdict` with `passed, score, raw,
  model_id, rubric_hash, reason`. Raises `JudgeOutputError` on an unparseable
  response, after recording the raw text.
- `judge.SCORE_MIN == 1.0`, `judge.SCORE_MAX == 5.0`.
- rigor's judge prompt instructs the model to emit `"score": null` when it cannot
  score, so a `None` score is normal output, not a malformed one.
- `assert_pass_rate(result, min_rate, *, confidence=0.95, ...)` gates on
  `wilson_lower_bound >= min_rate` and, on failure, carries `underpowered` and
  `runs_needed` on the exception's `stats`.
- `assert_no_regression(current, baseline, *, alpha=0.05, ...)` is
  `mannwhitneyu(current, baseline, alternative="less")`. **Argument order is
  (current, baseline)**; reversing it silently inverts the meaning. Its score
  coercion rejects `None` *and* rejects `bool`, so booleans must be passed as
  `float(v.passed)`.
- `wilson_interval` is two-sided (printing); `wilson_lower_bound` is one-sided
  (gating). `wilson_interval(0, 0)` raises `ValueError`, so "no data" is a
  rendering state, not a number.

**Name collision, frozen:** `opik_rigor.Verdict` is a graded response;
`contracts.Verdict` is GO/NO-GO/REVIEW. rigor's is imported as
`from opik_rigor import Verdict as JudgeVerdict`, never bare.

## 1. `judging.py`

### Config — one TOML file
```toml
[[judge]]
name   = "helpfulness"
model  = "claude-sonnet-4-5-20250929"
rubric = "rubrics/helpfulness.md"

[thresholds]
pass_rate_floor          = 0.90
alpha                    = 0.05
confidence               = 0.95
judge_failure_tolerance  = 0.05
min_detectable_effect    = 0.10
power_target             = 0.80
```

`JudgeConfig.load(path) -> JudgeConfig` is strict: unknown keys, an unpinned model
id, a missing rubric, a threshold outside its range, zero judges, or **two judges
sharing a name** are all `ConfigError`. Duplicate names are rejected rather than
tolerated because three separate things key on the name — the judges hash, the
resume key, and rigor's own rubric-drift lookup, which filters `judge.init`
records by name alone. Two judges called `x` make all three wrong at once.

`.judges_hash` = `hash_bytes(canonical_json([...]))` over
`{name, model, adapter_class, rubric_hash}` per judge, sorted by the **full
tuple** rather than by name. The adapter class is in the hash because
`AnthropicAdapter` and `OpenAICompatAdapter` pointed at one `model_id` otherwise
hash equal while being different instruments. Thresholds are excluded: they change
what the verdict concludes, not what the instrument measured, and they are echoed
into the report separately so a loosened gate still shows in the evidence.

`.build(evidence) -> tuple[PinnedJudge, ...]` constructs each judge **once** per
comparison, and both artifacts are judged in that same process against that same
evidence log. That is what makes "the same judges graded both sides" a structural
property rather than a promise, and it is also what lets rigor's drift detection
work at all.

### `judge_artifact(artifact, judges, config, *, evidence, out=None) -> JudgedArtifact`

Same on-disk discipline as `RunArtifact`: append-only JSONL, `"record"` key, one
header per part, `.load()`, resumable, `parts`. Header adds `judges_hash`, the
per-judge `{name, model_id, rubric_hash, adapter}` list, and the source artifact's
`model_id`, `goldenset_hash` and item counts. Records are
`{judge, item_id, sample_index, passed, score, imputed, reason, error}`.

Frozen behaviours:

1. **A failed completion is judged `passed=False` with `score = SCORE_MIN` and
   `imputed=True`.** Not `None`: rigor rejects `None` in a score array, so one
   failure would abort the comparison; and not omitted, because omission is what
   made a crashing model outscore a merely bad one. See build-plan §6.
2. **A judge that returns `passed` without a numeric score** — normal output, per
   §0 — is scored `float(v.passed)` for the regression test, and the judge's row
   in the report says the test ran on outcomes rather than scores.
3. **Parse-failure tolerance** counts `JudgeOutputError` per judge over **the
   completions judged in this comparison** for that judge, both sides pooled — a
   scope that must be stated, since counting per resumed run would abort a
   400-completion comparison on one failure in the last five. The pass completes
   before raising `JudgeReliabilityError`, so the count in the message is the real
   one rather than the count at the moment of the breach.
4. **Resume keys on `(judge, item_id, sample_index)`** and skips triples already
   judged. Because rigor writes one `judge.verdict` evidence record per call and
   the log has no dedupe, anything reading verdicts back from the log must dedupe
   on that same triple or a re-judged completion is counted twice.

## 2. `comparison.py`

`compare(baseline, candidate, *, thresholds, allow_same_model=False, evidence=None)
-> ComparisonReport`

### Guards, before any statistic
- `goldenset_hash` differs → `ArtifactError`.
- `judges_hash` differs → `JudgeConfigError`.
- **The two sides do not cover the same completions** — same item ids, same count
  per item → `ArtifactError`. Necessary because a truncated artifact passes both
  hash checks, and invariant 2 guarantees truncated artifacts exist.
- Same `model_id` on both sides → `ArtifactError` unless `allow_same_model`, which
  exists because §3's "identical models produce GO" calibration test needs exactly
  that comparison and an accidental self-comparison needs catching.

### Per judge
Pass counts and observed rate for each side over **all** completions, failures
included; `wilson_interval` for printing, rigor's `assert_pass_rate` for the floor
— including its `underpowered` and `runs_needed`, which the verdict consumes
rather than recomputing. Regression via `assert_no_regression(candidate_scores,
baseline_scores, alpha=...)` wrapped in `try/except RegressionError`, with p-values
across judges corrected by Holm-Bonferroni before any comparison to alpha.

Power is reported per judge: the minimum detectable effect at this n, and the n
required for the configured effect at the configured power. The estimate uses the
two-proportion normal approximation on the pass/fail proxy
(`n ≈ (z_α + z_β)² (p₁(1-p₁) + p₂(1-p₂)) / δ²`), which gives 108–229 per side
across plausible baseline rates and tracks the simulated Mann-Whitney figures
(~200 for a ten-point drop). It is an approximation to a different test and the
methodology appendix says so.

Latency: median and p90 per model from `Completion.duration` via stdlib
`statistics`, descriptive only, never a gate.

### Flips
An item passes at **≥80%** of its draws, fails at **≤20%**, and is **unstable**
between. `flips` (passed → failed), `gains` (failed → passed) and `unstable` are
three separate lists; flips print as `k/n → k'/n`. Gains never offset flips in the
verdict — netting them is how a bad migration gets shipped.

## 3. Test inventory
- mismatched `judges_hash` → `JudgeConfigError`; mismatched coverage →
  `ArtifactError`; duplicate judge names → `ConfigError`
- parse-failure tolerance aborts at the threshold with the true count
- a crashing candidate and a badly-answering candidate with identical pass counts
  do **not** produce opposite verdicts (the regression this whole amendment exists
  for)
- known-different scripted models at adequate n → NO-GO
- identical seeded models → GO across repeated seeds **and across four judges**,
  which is where the uncorrected rule failed one run in eleven
- underpowered → REVIEW, never GO; a model exactly at the floor is not NO-GO
- flip/gain/unstable lists exactly match constructed cases; a 50/50 item lands in
  `unstable`, not in `flips`
- the resolution function is table-tested as a total function of its flags,
  including combinations unreachable through the current statistics
- imputed failures are counted and reported
