# Implementation plan: the report as a series, candidates and dimensions

Plans the design recorded in
`docs/superpowers/specs/2026-08-21-migkit-report-design.md` (committed 9ed0e4d).
Written against `main` at 9ed0e4d, clean.

The spec is treated as agreed. Where this document disagrees with it, it says so
in the open and gives the evidence — five such places are collected in
[§8, What the spec gets wrong](#8-what-the-spec-gets-wrong), and two of them change
the shape of the work rather than a detail of it.

Work is decomposed for a four-agent pipeline per chunk — implementer, tester,
reviewer, implementer — none of whom shares context. The consequence for this
document is that **the contract sections are the deliverable**. A tester holding
only a chunk's contract, with no sight of the code, must be able to write a test
that fails when the implementer got it wrong. Chunks that cannot be written that
precisely have been split until they could.

---

## 1. The two facts the spec flagged, settled

Both were determined twice: by reading the code that constructs the payloads
(`comparison.py:708-751`), and by generating a real log —
`migkit demo --work-dir ./work --keep` in a throwaway directory, on the repo's
own interpreter, 300 records — and dumping the keys.

The log's record envelope is rigor's, and it is worth writing down because the
spec's prose says `event`: the four envelope keys are
`event_type`, `payload`, `schema_version`, `ts`.

### (a) Thresholds are recorded per comparison, twice over

`migkit.comparison` and `migkit.verdict` each carry a `thresholds` mapping, and
the duplication is deliberate — `comparison.py:738-743` says the verdict record
carries them a second time because "a verdict quoted without the gate it was
measured against is a colour rather than a finding". Observed values from the
demo log, verbatim:

```json
"thresholds": {
  "alpha": 0.05, "confidence": 0.95, "judge_failure_tolerance": 0.05,
  "min_detectable_effect": 0.1, "pass_rate_floor": 0.9, "power_target": 0.8
}
```

Better than that: every per-judge, per-side gate dict carries the threshold it
was measured against *at the point of measurement* — `min_rate` 0.9,
`confidence` 0.95, `target_power` 0.8, `alpha` 0.05 — so the floor can be drawn
from the gate that used it rather than from the run-level mapping that
accompanied it.

**Consequence: the historical floor line can be drawn truthfully, per run.** The
spec's fallback ("draw it only where known") is not the plan; it survives only as
the degradation branch for a log whose `thresholds` is absent or empty.

One distinction must not be flattened. `ReportModel.threshold_sources`
(`report.py:903`, `:922`) already separates *which config file the number came from*
from `THRESHOLD_SOURCE_UNRECORDED` — "source not recorded in the evidence" — and
the appendix note is explicit that this "is a gap in the record and not a claim
about the default". A series makes this worse, not better: fourteen runs may name
fourteen `config_path` values, some empty. The timeline's floor rule must be able
to say *unknown source* for a run without saying *default*.

### (b) Judge identity and golden-set identity are recorded, as hashes

Observed in the `migkit.comparison` payload:

| Key | Observed | Meaning |
|---|---|---|
| `goldenset_hash` | `5fef50364057cad8…ba4e9a04` (64 hex) | content hash of the set |
| `goldenset_path` | `work\demo_goldenset.jsonl` | provenance, and the read path |
| `judges_hash` | `bb624f0ed1781d85…8b7dcbc2` | the whole panel |
| `config_hash` / `config_path` | `1ad89c46…`, `work\demo.toml` | thresholds' origin |
| `judges[i].model_id` | `fake-judge-v1` | the instrument |
| `judges[i].rubric_hash` | `cc39e4aa…f642c6268` | the rubric it read |

`migkit.run_started` and `migkit.run_completed` carry `goldenset_hash` too, and
`migkit.judging_completed` carries `judges_hash`.

**Consequence: the parameter strip may legitimately claim "judges unchanged"**,
and it can be specific about *how* unchanged — panel hash, per-judge model id,
per-judge rubric hash are three separate claims and the strip should make the
one it can. Comparability keys on `(goldenset_hash, judges_hash, n_per_item)`.

### (c) A third fact, which the spec did not flag, and which costs more than both

**Per-item, per-judge outcomes are not in the evidence log except for items that
changed state.**

The comparison payload carries `flips`, `gains` and `unstable` item-major with
margins, and `item_counts.per_judge.<judge>.{baseline,candidate}.{passing,
failing,unstable}` — aggregate counts, not per item. The obvious candidate for
the missing join, rigor's `judge.verdict` record, has these payload keys and no
others:

```
input, judge, model_id, output, passed, raw, reason, rubric_hash, score
```

There is **no `item_id`**. A `judge.verdict` can be joined back to an item only
through its `input` string, which is fragile in exactly the case that matters —
two golden-set items with the same input, which `goldenset.py` permits and
`demo._refuse_duplicate_inputs` has to guard against separately.

The join that does exist is on disk. The **judged artifact** carries
`{item_id, judge, passed, score, sample_index, imputed, parse_failure}` per
record, verified against
`fake-candidate-v1__5fef50364057cad8.judged.jsonl`. `report.py` already loads it
(`_load_side`, `report.py:1247`) and already counts through it
(`_per_judge_counts`, `report.py:1356`).

Three consequences follow, and they are the reason phase 3 is expensive:

1. **Zone 1b needs the judged artifacts**, which `_resolve` (`report.py:1097`)
   will legitimately refuse whenever the log has moved to another machine
   without `--artifact-dir`. That is the *ordinary* cross-machine case, not an
   error case. The dimension matrix must therefore be an availability-gated
   section with a stated reason, in the same idiom as
   `goldenset["available"]`/`["reason"]`.
2. **Zone 1b needs a hash-matching golden set** for the tags.
   `_load_goldenset` refuses to expose items when the recorded hash no longer
   matches (`report.py:1225-1234`) — "pairing today's file with last week's
   outputs would be a fabricated exhibit". Tags fall under that refusal, so the
   matrix has two independent ways of being unavailable.
3. **Per-dimension intervals are recorded nowhere and must be computed at render
   time.** This collides head-on with a stated invariant, and with a passing
   test. See §3.

The spec's "No new instrumentation. No schema change. No migration." is true of
the series and of the candidate table. It is **not** true of the dimension matrix
in the sense the sentence implies: no schema changes, but the matrix acquires a
dependency on two files outside the log, and inherits their failure modes.

---

## 2. Phasing

Six phases, eighteen chunks. My reading agrees with the coordinator's five-phase
sketch with one addition and one reordering.

| # | Phase | Chunks | One-line justification |
|---|---|---|---|
| 0 | The series seam | C1–C3 | Everything downstream reads `RunPoint`; get its shape wrong and four phases rework. Cheapest chunk, largest blast radius. |
| 1 | Grouping, comparability, multiplicity | C4–C7 | The honesty guards live here, not in the template; a table that silently stacks incomparable runs is the failure the spec calls worse than no table. |
| 2 | Dimensions | C8–C10 | The spec's central argument, and already demonstrable on the shipped demo — no seed data needed to prove it. Placed after phase 1 because it reuses the availability idiom, not before it. |
| 3 | The counterfactual | C11 | One function, isolated, and the one whose *premise* I dispute. Isolating it means the dispute can be settled without blocking anything. |
| 4 | Template and SVG | C12–C14 | Cannot start before its inputs exist; the only phase where the four-agent pipeline strains. |
| 5 | Seed generator | C15–C18 | Last because it is an end-to-end exercise of everything above, and because a seed built before the model settles gets rebuilt. |

**The addition** is phase 3. The spec files the counterfactual under zone 1
prose. It deserves its own chunk because its correctness is arithmetic over a
sampling assumption nobody has stated yet, and because it is the single sentence
most likely to be quoted back at us.

**The reordering** is putting dimensions after grouping rather than before.
The spec presents zone 1b as the most actionable thing the report can say, which
argues for doing it first. Against that: the dimension matrix needs a degradation
idiom (available / reason / partial) that phase 1 has to invent anyway for
excluded runs. Inventing it twice is how the two halves of the document end up
declining to answer in two different vocabularies.

**Phase 2 is where I would put the first demo.** The coordinator's observation is
right and worth recording: the shipped twelve-item demo already proves the
argument. Its flips are `extract-01` (#extraction), `refuse-02` (#refusal) and
`refuse-04` (#refusal #multi-value); its one gain is `extract-03` (#extraction).
Extraction nets to zero and refusal loses two of its four items, while the
aggregate moves 91.7% → 75.0% and the report never says which dimension moved.
Nothing needs to be seeded to show that. Phase 5 makes it *large*; phase 2 makes
it *true*.

---

## 3. The invariant collision, and where the statistics go

`report.py`'s module docstring states: "**No statistic is ever recomputed.** …
this module imports no statistical function at all." It is enforced structurally
by `tests/test_report.py:1333`, which walks report.py's AST and fails on any call
to a name in:

```
wilson_interval, wilson_lower_bound, assert_pass_rate, assert_no_regression,
mannwhitneyu, holm_bonferroni, required_sample_size, compare
```

Three of the spec's requirements need three of those names:

- the multiplicity correction needs `holm_bonferroni`;
- per-dimension intervals need `wilson_interval`, because no gate ever measured
  a per-tag rate and nothing recorded one;
- the REVIEW callout's "collect N more" needs a recorded `runs_needed` or
  `power.n_required` — this one is a *lookup*, not a computation, and is fine.

**The resolution is a new module, not an amended invariant.** Add
`src/model_migration_kit/series.py`. It may import `wilson_interval` from
`opik_rigor` and `holm_bonferroni` from `.comparison`; `report.py` imports plain
frozen dataclasses from it and continues to call no statistical primitive itself.
The AST test reads `inspect.getsource(_module())` for report.py alone
(`tests/test_report.py:977`), so it keeps passing, and it keeps meaning what it
says.

The distinction to write into `series.py`'s docstring, because it is the thing a
reviewer will want stated:

> Recomputing a number a gate already recorded is forbidden, because a renderer
> that disagrees with the verdict is a document that contradicts itself.
> Computing a number nothing recorded is a different act: it is a new
> measurement, it was never a gate, and it must be labelled as derived at render
> time. Per-dimension rates are the second kind. The guard that makes that
> honest is the refusal threshold — a cell below it shows its interval and
> declines the verdict.

Two knock-on obligations, both cheap and both easy to forget:

- `python scripts/dependency_surface.py` must be re-run and `COMPATIBILITY.md`
  updated, or CI's "Dependency surface matches the tree" step fails. `series.py`
  will be a new row.
- `series.py` must not be imported from `__init__.py`.
  `tests/test_import_purity.py:225` asserts the bare import loads **no** submodule
  of its own, and `tests/test_import_purity.py:248` asserts jinja2 and rich
  arrive only with the report module.

### The spec's `holm_bonferroni` attribution is wrong

The spec says "opik-rigor's `holm_bonferroni` is the mechanism". It is not
opik-rigor's. Verified on the installed 0.2.0:

```
>>> opik_rigor.holm_bonferroni
AttributeError: module 'opik_rigor' has no attribute 'holm_bonferroni'
```

It is migkit's own, `comparison.py:224`, exported in `comparison.__all__`, with
a NaN guard that exists because a reordering of the same four p-values was
measured flipping NO-GO to GO. Import it from `.comparison`. This matters beyond
pedantry: an implementer who trusts the spec will write
`from opik_rigor import holm_bonferroni`, get an ImportError, and is then one
step from writing a second implementation of Holm — at which point the
correction applied across candidates and the correction applied across judges are
two functions that can drift.

---

## 4. Cross-cutting decisions the chunks depend on

Settle these before C1, because every chunk downstream assumes them.

### 4.1 The series timestamp is `payload["created"]`, not the envelope `ts`

The comparison payload carries its own `created`, stamped by
`contracts.utc_now()` at `comparison.py:903`. The envelope `ts` is stamped inside
rigor, `opik_rigor/evidence.py:89`, by `datetime.now(timezone.utc)`.

`created` is the better field on the merits — it is a recorded fact about the
comparison rather than about when a line was written, and it survives a log that
was concatenated or copied. It is also the **only** one the seed generator can
control: `utc_now` is imported into `comparison.py`'s namespace and is therefore
monkeypatchable at `model_migration_kit.comparison.utc_now`, whereas rigor is
read-only for this work and its `datetime` is a module-level import inside a
package we do not own.

So: **`RunPoint.created` reads `payload["created"]`, falling back to the
envelope `ts` when it is absent or unparseable, and records which of the two it
used.** The fallback is not hypothetical — a payload written by a future writer
that drops the field would otherwise sort as the epoch and put a run at the far
left of the timeline.

### 4.2 The x-axis is time, and `created` is a string

Every `created` observed is RFC3339 with microseconds and an explicit `+00:00`.
Parse with `datetime.fromisoformat`; a value that will not parse is a point with
a known verdict and an unknown date. Such a point must be **excluded from the
timeline and named beneath it**, never dropped silently and never plotted at
index position — the spec's own reason for choosing time over run index is that
"a three-week gap is itself information", and a point placed at an invented time
is worse than a point placed at an invented index.

### 4.3 `is_demo` becomes a property of the whole series

`RunSummary.is_fake` keys off `adapter.startswith("Fake")` where `adapter` is
`type(adapter).__name__` recorded into the payload — derived from the artifacts
and never from a flag, deliberately (`report.py` module docstring). A series
breaks the current reading: `ReportModel.is_demo` looks only at the headline run,
so a log holding thirteen seeded nights and one real one would render with no
band at all.

**`is_demo` must become true when *any* point in the series is fake**, and the
band's wording must change from "these models are fake" to name how many of the
runs were. The spec's test "a synthetic-data banner cannot be suppressed by any
input" extends to: it cannot be suppressed by appending a real run either.

### 4.4 `_require_comparable` cannot be reused as the spec instructs

`comparison._require_comparable` (`comparison.py:922`) takes two live
`JudgedArtifact` objects and checks four things: golden-set hash, judges hash,
key-by-key coverage, and same-model self-comparison. The report has payloads, not
artifacts, and the artifacts are frequently absent. Calling it is not available.

What is available from the payloads is a strict subset — `goldenset_hash`,
`judges_hash`, `n_per_item`, `baseline.model_id`, `baseline.records` — and that
subset is what grouping must key on. So the plan writes a **second, narrower**
predicate in `series.py`, and C4's test asserts that on the fields the two share
they agree. Not "grouping respects `_require_comparable`", which cannot be done,
but "grouping never admits a pair `_require_comparable` would have refused on a
field grouping can see".

Coverage is the field grouping cannot see, and that gap must be stated in the
document rather than papered over: two runs with matching hashes and matching
`n_per_item` can still differ in completions if one was truncated. `records` is
recorded per side and is the available proxy; unequal `records` flags rather than
excludes, because a shortfall is already surfaced by `Completeness`.

---

## 5. Chunks

Notation per chunk: **files** → **contract** → **edges** → **must not** →
**failure mode** → **test that fails first** → **done** → **reviewer**.

Every chunk's done-check begins with the same two lines, for a reason recorded in
this project's memory: pytest run from a git worktree has silently tested the
main checkout and produced a false green.

```
.venv/Scripts/python -c "import model_migration_kit as m, sys; print(m.__file__); print(sys.prefix)"
# both paths MUST be under the worktree root; if not, stop — the venv is shadowing
.venv/Scripts/python -m pytest tests/<file> -q
```

No chunk may introduce a conditionally-skipped check into the release gate.
`scripts/verify_release.py:30` pushes the process exit code to **2** when
anything reports SKIPPED, and `tests/test_release_checks.py:627-630` asserts
SKIPPED and PASS are deliberately different statuses. This bites C14 directly,
where the obvious verification is a browser that may not be installed — see
[§7](#7-chunks-that-resist-the-four-agent-pipeline).

---

### Phase 0 — the series seam

#### C1 — `RunPoint`, and building one from two payloads

**Files.** New `src/model_migration_kit/series.py`. New
`tests/test_series.py`. `COMPATIBILITY.md` regenerated.

**Contract.**

```python
@dataclass(frozen=True)
class RunPoint:
    created: str                      # RFC3339 as recorded, "" when absent
    created_source: str               # "payload" | "envelope" | "unknown"
    verdict: str | None               # None when no verdict record was paired
    reason: str | None
    baseline_model: str
    candidate_model: str
    adapter_baseline: str
    adapter_candidate: str
    goldenset_hash: str
    judges_hash: str
    config_hash: str
    config_path: str
    n_per_item: int
    items: int                        # golden-set item count, 0 when unrecorded
    judged_baseline: int              # completions the judge GRADED, not the run produced
    judged_candidate: int
    judge_failures_baseline: int      # the JUDGE failed it; not an adapter error
    judge_failures_candidate: int
    pass_rate: float | None           # candidate side, widest judge
    interval: tuple[float, float] | None
    lower_bound: float | None
    floor: float | None               # the gate's own min_rate, not config
    floor_source: str                 # "gate" | "thresholds" | "unrecorded" (added by review)
    confidence: float | None
    alpha: float | None
    judge_name: str                   # which judge the four numbers above came from
    judge_model_id: str
    rubric_hashes: tuple[str, ...]    # sorted, one per judge
    p_value: float | None
    latency_median_candidate: float | None
    runs_needed: int | None
    n_required: int | None            # power.n_required
    warnings: tuple[str, ...]

def run_point(
    comparison: Mapping[str, Any],
    verdict: Mapping[str, Any] | None,
    *,
    envelope_ts: str = "",
) -> RunPoint: ...
```

`run_point` takes the two **payload mappings**, not records, not artifacts.

Behaviour, input to output:

- `floor`, `confidence`, `alpha` are lifted from the *candidate gate* —
  `comparison["judges"][i]["candidate"]["min_rate"]`, `["confidence"]`, and
  `comparison["judges"][i]["alpha"]` — not from `comparison["thresholds"]`.
  The gate is the number that was applied; the run-level mapping is the number
  that was configured, and on a run where they differ the gate is the truth.
  Where the gate omits one, fall back to `thresholds["pass_rate_floor"]` /
  `["confidence"]` / `["alpha"]` and leave `None` if that is absent too.
- `pass_rate`, `interval`, `lower_bound`, `p_value`, `judge_name`,
  `judge_model_id`, `runs_needed`, `n_required` come from **the judge with the
  largest candidate-side `n`**. Ties break on the payload's own judge order,
  which is the config order (`comparison._judge_names`). Rationale: the same rule
  `_per_judge_counts` already uses — two judges grading 60 completions are 120
  records and 60 completions, and summing across the panel would double.
- `items` is `comparison["judges"][i]["item_counts"]["items"]`, 0 when absent.
- `created` per §4.1.
- `verdict is None` when `verdict` is `None` or lacks the key.

**Edges, each with a required behaviour.**

| Input | Required |
|---|---|
| `comparison["judges"]` empty or absent | every judge-derived field `None` / `""`; `RunPoint` still returned |
| `judges[0]["candidate"]["n"] == 0` | `pass_rate`, `interval`, `lower_bound` all `None` — never 0.0 |
| `thresholds` absent **and** gate `min_rate` absent | `floor is None`; no default substituted |
| `created` absent, `envelope_ts` given | `created == envelope_ts`, `created_source == "envelope"` |
| both absent | `created == ""`, `created_source == "unknown"` |
| `interval_lower` present, `interval_upper` absent | `interval is None` — never a one-ended tuple |
| `n_per_item` a string `"5"` | coerced to `int`; a value that will not coerce yields `0` |
| `verdict=None` | `verdict`/`reason` `None`, everything else populated |

**Must not.** Touch the filesystem. Import `report`. Accept a `ComparisonReport`,
`JudgedArtifact` or `RunArtifact` — the signature takes mappings and a test
should assert the annotations say so. Substitute `thresholds["pass_rate_floor"]`
for a missing gate `min_rate` *silently* — the fallback is permitted, but the
next chunk needs to know it happened, so record nothing rather than record a
guess when neither is present.

**Failure mode when wrong.** The timeline draws a floor rule at 0.9 on a run
that was gated at 0.85, and every subsequent verdict on the chart is
misattributed. This is the exact lie the spec names: "taking it from current
config would be a lie on any run that used a different one."

**Test that fails first.** `test_a_run_point_takes_its_floor_from_the_gate_that_
was_applied_and_not_from_the_configured_thresholds` — construct a payload whose
`thresholds.pass_rate_floor` is 0.90 and whose candidate gate `min_rate` is 0.85,
assert `floor == 0.85`.

**Done.**
```
.venv/Scripts/python -m pytest tests/test_series.py -q          # all pass
.venv/Scripts/python scripts/dependency_surface.py --check      # exit 0
.venv/Scripts/python -m ruff check src tests                    # exit 0
```

**Reviewer, specifically.** The likely subtle error is reading the *baseline*
gate instead of the candidate one — both dicts have identical keys and the
verdict banner reads plausibly either way. Second: taking judge `[0]` rather than
the widest, which is correct on every single-judge log including the demo, and
wrong on the first two-judge log anyone runs.

---

#### C2 — `read_series`: keep all the comparisons, not the last

**Files.** `series.py`. `tests/test_series.py`.

**Contract.**

```python
def read_series(evidence: str | Path) -> tuple[RunPoint, ...]: ...
```

One streaming pass over the log, in file order. Each `migkit.comparison` record
opens a point; the **next** `migkit.verdict` record before the next
`migkit.comparison` closes it. A comparison with no following verdict yields a
point with `verdict is None`. Points are returned in **log order**, not sorted.

Reuse `report._stream_records` — do not write a second reader. The docstring at
`report.py:971` records why: `EvidenceLog.read()` was measured at 5.0–5.8× the
log's own bytes resident, an 86 MB log costing an extra 502 MB, and a torn final
line is dropped while anything malformed earlier is an error. A second reader is
how a reader and a writer of the same file drift apart. Import it, or promote it
— promoting it to a public `stream_records` in `report.py` is acceptable and
preferable to `series.py` importing a private name.

**Edges.**

| Input | Required |
|---|---|
| log with zero `migkit.comparison` records | `()` — **not** an exception. `ReportModel.from_evidence` keeps its own `ArtifactError` for that case; `read_series` is a lower layer. |
| log with one comparison and one verdict | one point, `verdict` populated |
| log with one comparison, no verdict | one point, `verdict is None` |
| verdict *before* any comparison | ignored; it belongs to no point |
| two comparisons then two verdicts | first verdict closes the first point; the second verdict closes the second. Comparison-then-verdict adjacency is what `compare` writes (`comparison.py:906-908`) and must not be assumed to be the *only* interleaving. |
| torn final line | dropped, per `_stream_records` |
| malformed line not at the end | `EvidenceError` propagates |
| path is a directory | `<dir>/evidence.jsonl`, matching `from_evidence` |
| path does not exist | `ArtifactError` with the same reasoning `from_evidence` gives: rigor reads a missing log as empty, so a typo must not render as a valid report of a run that never happened |

**Must not.** Hold the whole log. Sort. Deduplicate. Reach outside the given
path.

**Failure mode when wrong.** Fourteen nightly runs render as one point, or a
verdict from run 3 attaches to run 4's comparison and the timeline shows a
NO-GO on a green night.

**Test that fails first.** `test_a_log_holding_three_comparisons_yields_three_
points_in_the_order_they_were_written`.

**Done.** As C1, plus a memory assertion: build a log with 5,000 comparison
records via a tmp_path fixture and assert `read_series` completes without the
process resident set exceeding a bound — the shape of `tests/test_evidence_
scale.py` already in the tree.

**Reviewer.** The pairing rule is the trap. An implementation that keeps
`last_verdict` and attaches it to the *next* comparison, or that pairs by index
across two collected lists, passes the adjacent case and fails the interleaved
one. Ask for a test with a `migkit.judging_completed` record sitting between the
comparison and its verdict, which is not what `compare` writes today but is what
a future writer might.

---

#### C3 — hang the series off `ReportModel`, render nothing

**Files.** `report.py` (`ReportModel` fields; `from_evidence` around
`report.py:807-813`). `tests/test_report.py`.

**Contract.** `ReportModel` gains:

```python
series: tuple[RunPoint, ...] = ()
```

`from_evidence` populates it. **The headline run's every existing field is
unchanged** — the existing reduction keeps the last comparison and the last
verdict, and this chunk must not alter which record the banner, the judge table,
the flips or the provenance block come from.

Implementation note that is part of the contract, not an option: do **not** make
two passes over the log. Extend the single loop at `report.py:807-813` to
accumulate points as well as keeping the last two records. The 86 MB measurement
in `_stream_records`' docstring is about holding records; two passes is about
reading bytes twice, and the log is the largest artifact the pipeline writes.

`is_demo` (`report.py:945`) changes per §4.3: true when either headline side is
fake **or** any point in `series` names a `Fake*` adapter.

**Edges.**

| Input | Required |
|---|---|
| single-comparison log (every log today) | `len(series) == 1`; every other field byte-identical to before |
| log with no comparison | `ArtifactError`, unchanged wording |
| headline sides real, an earlier point fake | `is_demo is True` |
| series present | `series[-1]` describes the same run as the headline fields |

**Must not.** Change any existing field's value on any existing fixture. Add a
second read of the file. Accept a `ComparisonReport` — `tests/test_report.py:1014`
already asserts `from_evidence` takes paths and never a live object, and that
test must keep passing untouched.

**Failure mode when wrong.** Silent: every current test passes and the headline
quietly starts reflecting the *first* comparison instead of the last, which on a
single-comparison log is the same record.

**Test that fails first.** `test_a_log_holding_two_comparisons_still_reports_on_
the_last_one` — two comparisons with different verdicts, assert
`model.verdict` is the second and `model.series[-1].verdict` agrees.

**Done.** The **whole** report suite, not just the new tests:
`.venv/Scripts/python -m pytest tests/test_report.py tests/test_report_scale.py
tests/test_report_untrusted_input.py tests/test_stranger_path.py -q`. All must
pass with no new skips.

**Reviewer.** The regression risk is entirely in the loop at 807-813. Read that
loop character by character. Second: `is_demo` is a property with a stated
rationale ("you cannot obtain a clean-looking report from scripted models by
avoiding `migkit demo`") — check the new disjunct cannot be made false by an
input, including a series whose adapter strings are empty.

---

### Phase 1 — grouping, comparability, multiplicity

#### C4 — the comparability key and the partition

**Files.** `series.py`, `tests/test_series.py`.

**Contract.**

```python
@dataclass(frozen=True)
class ComparabilityKey:
    goldenset_hash: str
    judges_hash: str
    n_per_item: int
    baseline_model: str

def comparability_key(point: RunPoint) -> ComparabilityKey: ...

@dataclass(frozen=True)
class Exclusion:
    point: RunPoint
    reason: str          # a full sentence, naming both values

def partition_comparable(
    points: Sequence[RunPoint], *, against: ComparabilityKey
) -> tuple[tuple[RunPoint, ...], tuple[Exclusion, ...]]: ...
```

`partition_comparable` returns kept points in input order and excluded points
each with a sentence naming the field that differed and **both** values, e.g.
`"excluded: 3 draws per item against the group's 5"`.

**Edges.**

| Input | Required |
|---|---|
| all points share a key | all kept, no exclusions |
| one point differs in `n_per_item` | excluded, reason names 3 and 5 |
| one point differs in `goldenset_hash` | excluded, reason shows both hashes truncated to 16 chars, matching `_require_comparable`'s convention |
| one point differs in `judges_hash` | excluded |
| a point has `goldenset_hash == ""` | excluded, reason says the hash was not recorded — **not** treated as matching |
| empty input | `((), ())` |
| `records` differ but key matches | **kept**, and the sentence is a flag on the kept point, not an exclusion (§4.4) |

**Must not.** Coerce an empty hash to a match. Return excluded points in a set or
dict — ordering must be stable so the rendered list is stable. Import anything
from `report`.

**Failure mode when wrong.** The one the spec names outright: "a table that
quietly compares a 60-item run against a 40-item run is worse than no table."

**Test that fails first.** `test_a_run_with_a_different_n_per_item_is_excluded_
and_the_reason_names_both_values`.

**Second test, which is the real point of this chunk.**
`test_grouping_never_admits_a_pair_that_require_comparable_would_have_refused` —
build two `JudgedArtifact`s that `comparison._require_comparable` rejects on
golden-set hash, derive the two `RunPoint`s that a comparison of them would have
produced, and assert `partition_comparable` excludes one. This is the bridge
§4.4 describes and it must exist as code, not as a paragraph.

**Reviewer.** Look for the empty-string-matches-empty-string hole. Two logs that
both failed to record a golden-set hash have equal keys and are not comparable.

---

#### C5 — the candidate field

**Files.** `series.py`, `tests/test_series.py`.

**Contract.**

```python
@dataclass(frozen=True)
class Candidate:
    point: RunPoint
    delta_pp: float | None      # candidate pass_rate minus baseline pass_rate, in points
    stale_days: float | None    # this run's age against the newest in the field

@dataclass(frozen=True)
class CandidateField:
    key: ComparabilityKey
    candidates: tuple[Candidate, ...]
    excluded: tuple[Exclusion, ...]
    spread_days: float | None
    spread_flagged: bool
    baseline_pass_rate: float | None

def candidate_field(
    points: Sequence[RunPoint], *, stale_after_days: float = 7.0
) -> CandidateField | None: ...
```

Groups by `comparability_key` ignoring `candidate_model`; picks the **largest
group** (ties break on the group containing the newest point); within it keeps
the **newest point per distinct `candidate_model`**. Returns `None` when the
chosen group has fewer than two distinct candidate models — that is the
single-candidate case, and the spec is explicit that it "collapses the table to a
single row and it is not rendered as a table at all". Returning `None` rather
than a one-row field makes that structural rather than a template `{% if %}`.

`spread_days` is newest minus oldest across the kept candidates.
`spread_flagged` is `spread_days > stale_after_days`.

`delta_pp` is candidate minus baseline **in percentage points**, i.e.
`(cand - base) * 100`. `None` if either is `None`. It is subtraction of two
recorded rates, not a statistic — no interval is attached to it and none may be
invented.

**Edges.**

| Input | Required |
|---|---|
| one comparison in the log | `None` |
| three candidates, one baseline, all same day | 3 candidates, `spread_days == 0.0`, not flagged |
| same candidate compared twice | one entry, the newer point |
| a candidate whose `created` is `""` | it has no date: sorted last, `stale_days is None`, and it never sets `spread_days` |
| every point has `created == ""` | `spread_days is None`, `spread_flagged is False` |
| two equally large groups | the one holding the newest point |

**Must not.** Compute a confidence interval on `delta_pp`. Reorder candidates by
pass rate — order by `candidate_model` so the table is stable across renders and
a reader cannot mistake position for ranking.

**Failure mode when wrong.** Three candidates measured three weeks apart render
as a fair field, with the baseline having drifted underneath them.

**Test that fails first.** `test_a_log_holding_one_comparison_yields_no_
candidate_field_at_all`.

**Reviewer.** `stale_after_days=7.0` against a spec that says "compared three
weeks apart are not a fair field" — check the default is defensible and, more
importantly, that it is a parameter rather than a literal. Second: the tie-break
on equally large groups is the kind of thing that renders differently on two
machines if it falls back to dict ordering of hashes.

---

#### C6 — multiplicity, corrected at render and said out loud

**Files.** `series.py`, `tests/test_series.py`.

**Contract.**

```python
@dataclass(frozen=True)
class Multiplicity:
    applied: bool
    method: str                 # "holm-bonferroni"
    alpha: float | None         # family-wise level
    family_size: int
    thresholds: Mapping[str, float]      # candidate_model -> its Holm threshold
    changed: tuple[str, ...]    # candidate models whose significance changed
    note: str                   # the sentence the report prints

def correct_field(field: CandidateField) -> tuple[CandidateField, Multiplicity]: ...
```

Uses `from .comparison import holm_bonferroni` — migkit's own (§3), **not**
opik-rigor's, which does not exist. The family is the candidates in the field;
each contributes its `point.p_value`. `alpha` is taken from the field's points
and must be **the same across them**; where they differ, the correction is
refused (`applied=False`) with a note saying so, because a family-wise level is
not defined over members tested at different levels.

`changed` names candidates for which `p_value < alpha` but
`p_value >= holm_threshold` — significant uncorrected, not significant
corrected. That set is what makes the honesty guard demonstrable.

`note` is one sentence, written here rather than in the template so the terminal
render and the HTML say the same words — the same discipline `DetailBudget.
sentence` uses (`report.py:644`).

**Edges.**

| Input | Required |
|---|---|
| one candidate | `applied=False`, `family_size=1`, note explains a family of one needs no correction |
| three candidates, `alpha` uniform | `applied=True`, three thresholds, monotone: the largest p-value never has the smallest threshold |
| a candidate with `p_value is None` | it is not in the family; `family_size` counts only tested candidates; the note names how many were untested |
| a `p_value` of NaN | handled by `holm_bonferroni`'s own `_finite_p` guard — read as 1.0. Assert this rather than re-guarding: `comparison.py:245-249` records that `[nan,.001,.001,.001]` rejected nothing while `[.001,.001,.001,nan]` rejected three, the same p-values reordered, the difference between NO-GO and GO. |
| candidates with differing `alpha` | `applied=False`, note names both levels |
| `alpha is None` on every point | `applied=False` |

**Must not.** Change any point's recorded `verdict`. The correction changes what
the *table* says about significance across the field; it does not retroactively
overturn a verdict a gate recorded, and the document must be able to show both —
"NO-GO as recorded; not significant once corrected across three candidates" is
the honest cell and it is more interesting than either half.

**Failure mode when wrong.** Three candidates against one floor inflate false
positives and the report claims a regression that a corrected family does not
support — while stating that the correction was applied. Claiming a guard you did
not apply is worse than applying none.

**Test that fails first.** `test_the_correction_changes_a_candidates_
significance_when_two_more_candidates_are_added` — one candidate at p=0.03 with
alpha=0.05 is significant; add two more at p=0.04 and p=0.045 and assert the
first appears in `changed`. This is the spec's named case, verbatim.

**Reviewer.** Two specific traps. First, applying Holm across candidates *and*
across judges without saying so: each point's `p_value` was already corrected
across its own judges by `compare` (`comparison.py:852-860`), so this is a second
correction on an already-corrected number, and the note must say that or it is
misleading. Second, `applied=True` with an empty `thresholds` mapping — a report
that says "Holm-Bonferroni was applied" while showing no thresholds is precisely
the overclaim the spec exists to prevent.

---

#### C7 — the trend and the parameter strip

**Files.** `series.py`, `tests/test_series.py`.

**Contract.**

```python
def trend(
    points: Sequence[RunPoint], *, baseline_model: str, candidate_model: str
) -> tuple[RunPoint, ...]: ...

@dataclass(frozen=True)
class ParameterChange:
    name: str          # "model_id" | "n_per_item" | "items" | "judges" | "golden set" | "config"
    before: str
    after: str
    changed: bool

def parameter_strip(
    previous: RunPoint | None, current: RunPoint
) -> tuple[ParameterChange, ...]: ...
```

`trend` filters to one pair and sorts by parsed `created` ascending; points with
unparseable or empty `created` are **excluded from the return** and the caller
learns of them separately (C10/C14 renders the count).

`parameter_strip` returns **one row per tracked parameter always**, including
unchanged ones with `changed=False`. That is the spec's whole argument: "when one
row moved and everything else held, the drop is attributable rather than merely
observed." A strip that lists only what changed cannot make that claim, because
absence of a row is indistinguishable from absence of a record.

Tracked parameters and their sources:

| name | source |
|---|---|
| `model_id` | `candidate_model` |
| `n_per_item` | `n_per_item` |
| `items` | `items` |
| `judges` | `judges_hash`, displayed to 16 chars |
| `golden set` | `goldenset_hash`, 16 chars |
| `config` | `config_hash`, 16 chars |

`previous is None` (the first run in the series) yields every row with
`before=""`, `after=<value>`, `changed=False` — the first run changed nothing
because there was nothing to change from.

**Edges.**

| Input | Required |
|---|---|
| a value unrecorded on one side (`""`) | `changed=False` and the rendered value must distinguish "unrecorded" from "unchanged". A blank cell reading as "unchanged" is the failure mode; use the existing `THRESHOLD_SOURCE_UNRECORDED` idiom. |
| every parameter identical | six rows, all `changed=False` |
| unsorted input | output sorted ascending by date |
| two points at the identical `created` | stable order, input order preserved |

**Must not.** Omit unchanged rows. Compare truncated hashes — truncate for
display only, compare in full.

**Failure mode when wrong.** The strip says "judges unchanged" on a run where the
judge model changed and only the panel hash happened to collide, or — far more
likely — says nothing at all about judges and a reader infers they held.

**Test that fails first.** `test_the_parameter_strip_lists_every_tracked_
parameter_including_the_ones_that_did_not_change`.

**Reviewer.** Check that an unrecorded value cannot render as "unchanged". This
is the highest-consequence, lowest-visibility bug in the whole plan: the strip's
entire job is to license an attribution, and a blank that reads as "held" licenses
a false one.

---

### Phase 2 — dimensions

#### C8 — per-tag counts from the judged artifacts

**Files.** `series.py` (or a new `dimensions.py` if `series.py` exceeds ~600
lines — decide at C8, not now). `tests/test_series.py`.

**Contract.**

```python
def dimension_counts(
    judged: JudgedArtifact,
    tags_by_item: Mapping[str, tuple[str, ...]],
    *,
    judge: str,
) -> dict[str, tuple[int, int]]: ...
```

Returns `tag -> (passes, n)` counting **judge verdict records**, one per
completion, for the named judge. An item carrying three tags contributes to three
tags — tags are a set per item and the report already renders them that way
(`goldenset.py:60-67`). Items with no tags contribute to a reserved key
`""` which the caller renders as "untagged", never dropped.

`imputed` and `parse_failure` records: counted in `n`, counted as a pass only if
`passed` is true. Rationale, and it must be in the docstring: `comparison.py:1188`
records that an imputed score is "missing data about the model rather than
evidence against it" — but the aggregate gate already counts them this way, and a
dimension view that counted them differently from the gate above it would produce
two rates for the same completions.

**Edges.**

| Input | Required |
|---|---|
| an item in `judged` with no entry in `tags_by_item` | contributes to `""` |
| a tag present in `tags_by_item` with no judged records | key present with `(0, 0)` — a dimension that was in the set and produced nothing is a finding |
| `judge` not in the artifact | `{}` |
| an item tagged twice with the same tag | impossible — `goldenset._parse_tags` refuses duplicates at load (`goldenset.py:286`); assert the invariant rather than defending against it |

**Must not.** Open a file. Take a path. Compute a rate, an interval, or a
verdict — this chunk returns integers only.

**Failure mode when wrong.** A multi-tagged item counted once, so `#refusal`
and `#multi-value` disagree about `refuse-04` and the two columns do not sum to
anything a reader can check.

**Test that fails first.** `test_an_item_carrying_two_tags_is_counted_under_
both_of_them`. The demo's `refuse-04` is exactly this item — `["refusal",
"multi-value"]` — and it is one of the three flips, so this test is also the
demo's own case.

**Reviewer.** The double-count question cuts both ways: contributing to both tags
is correct, and it means the column totals exceed the item count. Check the
function does not "fix" that by dividing, and check the caller is told so the
document can say it.

---

#### C9 — the cell, and the refusal

**Files.** as C8. **Contract.**

```python
@dataclass(frozen=True)
class DimensionCell:
    tag: str
    passes: int
    n: int
    rate: float | None
    interval: tuple[float, float] | None
    floor: float | None
    verdict_refused: bool
    needed: int | None          # items needed for a verdict, when refused
    note: str                   # the refusal sentence, "" when not refused

MIN_N_FOR_A_VERDICT: int = 20

def dimension_cell(
    tag: str, passes: int, n: int, *, confidence: float | None, floor: float | None,
    min_n: int = MIN_N_FOR_A_VERDICT,
) -> DimensionCell: ...
```

Calls `opik_rigor.wilson_interval(passes, n, confidence)`. When `n == 0` it
returns every derived field `None` and calls nothing — `wilson_interval(0, 0)`
raises `ValueError("a rate over zero runs is not a rate")`, verified in
`tests/test_report.py:1374`, and that is a rendering state and not a computation.

`verdict_refused` is `True` when `n < min_n`, **regardless of how the interval
sits against the floor**. The note reads, in the spec's own words:
`"20 items needed for a verdict here; you have 4."`

`floor` is passed in from the run's gate; the cell never reads config.

**Edges.**

| Input | Required |
|---|---|
| `n == 0` | `rate`, `interval` `None`; `verdict_refused=True`; note says nothing was measured |
| `n == 4`, `passes == 1` | interval computed and shown, `verdict_refused=True`, `needed == 20` |
| `n == 20`, `passes == 20` | interval shown, `verdict_refused=False` |
| `confidence is None` | fall back to rigor's `DEFAULT_CONFIDENCE` and record that in `note`; never silently |
| `floor is None` | cell renders, `verdict_refused` unaffected — the sample-size refusal does not depend on the floor |
| `passes > n` | `ValueError` — a corrupt count must not render |

**Must not.** Colour, style, or otherwise imply a verdict on a refused cell.
Compare the interval to the floor when `verdict_refused`. Fall back to a
default confidence without saying so.

**Failure mode when wrong.** The spec names it: "Every dashboard in this market
would happily colour that cell red. Declining is the differentiator." A cell that
renders a verdict at n=4 is not a bug in a chart, it is the product's claim
failing.

**Test that fails first.** `test_a_dimension_with_four_items_shows_its_interval_
and_declines_the_verdict`.

**Reviewer.** Check `verdict_refused` is not short-circuited by a wide interval
that happens to clear the floor. The tempting implementation says "refuse when
the interval is too wide to decide", which is a different and worse rule: it
would answer at n=4 whenever four out of four passed.

---

#### C10 — wire the matrix into `ReportModel`, with two ways to be unavailable

**Files.** `report.py` (`ReportModel`, `from_evidence`). `tests/test_report.py`.

**Contract.**

```python
@dataclass(frozen=True)
class DimensionMatrix:
    available: bool
    reason: str                       # "" when available
    judge: str
    tags: tuple[str, ...]             # row order: golden-set tag order, untagged last
    baseline: Mapping[str, DimensionCell]
    candidates: Mapping[str, Mapping[str, DimensionCell]]   # model_id -> tag -> cell
    min_n: int
```

`ReportModel` gains `dimensions: DimensionMatrix`. Built in `from_evidence` from
the artifacts `_load_side` already returned and the tags `_load_goldenset`
already exposed (`gs_view["tags"]` and, for the per-item mapping,
`gs_view["by_id"]`).

Unavailability has exactly two causes and each gets its own sentence:

- the judged artifact for either side is `None` — `_resolve` refused it, or it
  could not be read. Reason must name which side and reuse the warning already
  in `warnings`, not invent a second wording.
- `gs_view["available"]` is `False` — no golden set, or the hash no longer
  matches. Reason must reuse `gs_view["reason"]`, which already explains that
  pairing today's file with last week's outputs would be a fabricated exhibit.

**Edges.**

| Input | Required |
|---|---|
| both artifacts present, hash matches | `available=True`, one column per side |
| candidate artifact missing | `available=False`, reason names the candidate artifact |
| golden set hash mismatch | `available=False`, reason is `gs_view["reason"]` |
| golden set present but every item untagged | `available=True`, one row, `""` → untagged. **Not** unavailable — "you tagged nothing" is a different fact from "the file is gone", and the document must say the right one. |
| a log with no series | matrix still built from the headline run |

**Must not.** Fabricate cells from `item_counts` when the artifacts are missing —
`item_counts` is aggregate, and splitting it across tags by any rule is invention.
Read a file. `from_evidence` already resolved every path it is allowed to read;
this chunk consumes what that returned.

**Failure mode when wrong.** Either a crash on the ordinary cross-machine
render — a reviewer opening a shared log with no artifact directory, which is the
designed workflow — or, worse, a matrix built from a golden set whose hash no
longer matches, which is the fabricated exhibit `_load_goldenset` exists to
prevent.

**Test that fails first.** `test_the_dimension_matrix_declines_to_render_when_
the_judged_artifacts_are_not_beside_the_log` — render a log whose artifacts have
been moved away and assert `available is False` with the artifact named.

**Done.** Full report suite plus `tests/test_stranger_path.py`, which is where
the moved-log case already lives.

**Reviewer.** The most likely subtle wrong is treating "no tags in the set" as
unavailable. Second: reason strings that duplicate rather than reuse the existing
warning wording — three copies of a disclosure are three chances for one to go
stale, which is the reasoning already written at `report.py:645-650`.

---

### Phase 3 — the counterfactual

#### C11 — the spot-check line, with its assumption stated

Read [§7.4](#74-the-counterfactual-line-is-not-a-power-calculation) before
implementing. This chunk deliberately implements something narrower than the spec
describes, and the narrowing is the point.

**Files.** `series.py`, `tests/test_series.py`.

**Contract.**

```python
@dataclass(frozen=True)
class SpotCheck:
    k: int                    # prompts a spot check tries
    items: int                # N
    failing: int              # F, items failing under the candidate
    unstable: int             # counted as passing; see below
    probability: float        # P(a k-prompt check sees no failure)
    sentence: str

def spot_check(
    items_passing: int, items_failing: int, items_unstable: int, *, k: int = 12
) -> SpotCheck | None: ...
```

`probability = comb(N - F, k) / comb(N, k)` where `N = passing + failing +
unstable` and `F = failing`. Hypergeometric: k **items** drawn without
replacement. Uses `math.comb`; no statistical primitive, no rigor import.

Unstable items are counted as **passing**, i.e. not as guaranteed failures. That
choice is not arbitrary and must be in the docstring: it makes the spot check look
*better* than it is, so the tool never inflates its own case. Stating which way
the thumb is on the scale is the whole reason this sentence survives scrutiny.

Returns `None` — no sentence at all — when any of:

- `F == 0`: nothing to miss, and "a spot check would have found nothing" is
  vacuous rather than persuasive;
- `N < k`: the check would try every item;
- `N == 0`.

`sentence` names its assumption explicitly. The wording must contain the words
*"drawn at random"* and must say **spot checks**, not **runs**:

> A 12-prompt spot check drawn at random from these 96 items would have shown no
> failures at all in 34% of cases.

**Edges.**

| Input | Required |
|---|---|
| `passing=96, failing=0, unstable=0` | `None` |
| `passing=8, failing=1, unstable=0, k=12` | `None` (N=9 < 12) |
| `passing=88, failing=8, unstable=0, k=12` | probability ≈ 0.351 |
| `passing=85, failing=8, unstable=3, k=12` | unstable counted as passing; identical to the row above at N=96, F=8 |
| `failing == N` | `probability == 0.0`, sentence still returned |
| `k == 0` | `ValueError` |

**Must not.** Use the completion-level pass rate. `p ** 12` over 60 completions
that are 12 decisions understates the spot check's blindness by roughly an order
of magnitude, and it is the exact arithmetic error a reviewer would find. Say
"in X% of runs". Import from `opik_rigor`.

**Failure mode when wrong.** The most-quoted sentence in the document is wrong,
in the direction that flatters the tool, in a report whose entire claim is that
it does not overclaim.

**Test that fails first.** `test_no_spot_check_sentence_is_offered_when_nothing_
was_failing` — the vacuous case, because it is the one an implementer optimising
for "always show the persuasive line" will get wrong.

**Second test.** `test_the_spot_check_counts_unstable_items_as_passing_so_the_
number_never_flatters_the_tool`.

**Reviewer.** Check the denominator is items and not completions. Check the
sentence does not say "runs". Check `math.comb` is not replaced by a float
product that loses precision at N≈100 — it will not here, but the reason to use
`comb` is that the exact integer arithmetic is free.

---

### Phase 4 — template and SVG

Everything here is under `assert_self_contained` (`report.py:414`) and its
`_UrlScanner`, which walks the document with the stdlib `HTMLParser` and rejects
`FORBIDDEN_TAGS = {script, link, iframe, object, embed, base}`, any fetching
attribute, `url(...)` in CSS, `@import`, and any non-`data:` scheme. That
forecloses every charting library, web font, icon set and CDN. It is also why
these chunks are testable blind: **the constraint is machine-checkable**.

#### C12 — the interval bar, as a pure function returning SVG

**Files.** `report.py` (a new render helper section). `tests/test_report.py`.

**Contract.**

```python
def interval_bar_svg(
    *, rate: float | None, interval: tuple[float, float] | None,
    floor: float | None, width: int = 480, height: int = 44,
    label: str = "",
) -> str: ...
```

Returns one `<svg>` element as a string. No `<script>`, no `<style>` with a
`url()`, no external anything. Presentation via inline `fill`/`stroke`
attributes and geometry.

Geometry contract, so a tester can assert numbers rather than appearance:

- the x-axis maps `[0.0, 1.0]` to `[PAD, width - PAD]` linearly, `PAD = 8`;
- the interval band is a `<rect>` whose `x` is the mapped `interval[0]` and whose
  `width` is the mapped span;
- the point estimate is a `<line>` or `<rect>` at the mapped `rate`;
- the floor is a `<line>` at the mapped `floor`, carrying
  `class="floor"` so it is findable;
- every element carries `data-value` with the unmapped float to 6 places, so a
  test can assert the model's number reached the drawing without re-deriving
  the projection.

Missing values, each a distinct rendering state:

| Missing | Required |
|---|---|
| `interval is None` | no band element; the point estimate still drawn |
| `rate is None` | no point element |
| `floor is None` | no floor line, **and** an `<title>` saying the floor was not recorded — an absent rule must not read as a floor of zero |
| all three `None` | a single `<text>` element reading the em dash `—`, and nothing else |

Accessibility, which is also the blind-test seam: the `<svg>` carries
`role="img"` and a `<title>` whose text states the same numbers in words. That
title is what a test asserts against when it wants to know the picture is
telling the truth.

**Must not.** Emit `<script>`, `<style>`, `xlink:href`, or any attribute in
`report.FETCHING_ATTRS`. Round the underlying value before putting it in
`data-value`. Draw a floor at 0 when `floor is None`.

**Failure mode when wrong.** Two: an external reference, caught at render time by
`assert_self_contained` and therefore loud; or a silently wrong projection, where
the band sits above the floor while the numbers beneath say it does not. The
second is the dangerous one, because the spec says the relationship between the
band and the floor **is** the verdict.

**Test that fails first.** `test_the_interval_bar_places_the_floor_line_at_the_
same_fraction_of_the_width_as_the_floor_is_of_the_range` — assert the `x` of the
floor line equals `PAD + floor * (width - 2*PAD)` to within a pixel.

**Second test.** `test_an_interval_bar_with_no_recorded_floor_says_so_rather_
than_drawing_a_line_at_zero`.

**Third test.** `test_every_interval_bar_variant_passes_assert_self_contained` —
parametrised over the missing-value table, wrapping each in a minimal document.

**Reviewer.** Check the projection handles `rate` outside `[0, 1]` (it cannot
occur, but a clamp is cheap and an SVG that draws off-canvas is invisible rather
than wrong-looking). Check the `<title>` is not the only place a number appears —
a picture whose accessible text is right and whose geometry is wrong passes a
lazy test.

---

#### C13 — the timeline, as a pure function returning SVG

**Files.** `report.py`. `tests/test_report.py`.

**Contract.**

```python
def timeline_svg(
    points: Sequence[RunPoint], *, width: int = 900, height: int = 260,
) -> str: ...
```

**The x-axis is time.** Map parsed `created` linearly from the earliest to the
latest across `points`. This is the spec's explicit requirement and the reason
must be in the docstring: under CI a three-week gap is information, and evenly
spaced dots hide it.

Per point: a marker at the mapped `(created, pass_rate)`, a vertical whisker
spanning the mapped `interval`, and a `class` naming the verdict
(`go`/`nogo`/`review`/`none`). The floor is drawn as a **step function**, not one
rule: consecutive points sharing a `floor` join into one horizontal segment, and
a change in `floor` between two points is a vertical step. A single rule across a
series whose floor moved would be the lie §1(a) is about.

Segments where `floor is None` are **not drawn**, and the count of such runs is
returned to the caller for a sentence beneath the chart. This is where the spec's
fallback survives.

Each marker carries `data-created`, `data-rate`, `data-verdict`, `data-floor`.

**Edges.**

| Input | Required |
|---|---|
| `points` empty | an `<svg>` containing a single `<text>` saying no dated runs, and nothing else. **Not** an empty string, and not a crash — the spec names this: "a single point and no candidate table, rather than an empty chart or a crash". |
| one point | one marker, drawn at the horizontal centre; no interpolation |
| every point at the identical `created` | zero time span: markers evenly spaced, and a `<title>` saying the runs share a timestamp |
| two points three weeks apart, one adjacent | horizontal spacing proportional to the gap, assertable from `data-created` and `x` |
| a point with `floor is None` between two with 0.9 | the rule breaks and resumes |
| a point with `pass_rate is None` | no marker; counted and reported |

**Must not.** Sort by index. Emit `<script>`. Interpolate a rate between runs —
draw markers and, at most, a line joining them, but never a value at a date on
which nothing ran.

**Failure mode when wrong.** Evenly spaced dots that hide a three-week CI outage,
or a floor rule drawn at today's config across fourteen historical runs.

**Test that fails first.** `test_two_runs_three_weeks_apart_are_drawn_three_
weeks_apart` — three points at day 0, day 1 and day 22; assert the x-gap between
the second and third is 21 times the gap between the first and second, within a
pixel.

**Second test.** `test_a_series_whose_floor_changed_draws_a_step_and_not_one_
rule`.

**Third test.** `test_a_run_with_no_recorded_floor_leaves_a_gap_in_the_rule_and_
is_counted`.

**Reviewer.** The zero-span case is a division by zero waiting to happen and is
reachable on any log where two comparisons landed in the same microsecond — which
is exactly what a seed generator that patches `utc_now` to a constant would
produce. Check it. Second: the step function is easy to implement as a
`<polyline>` through the floor values, which draws diagonal ramps between
different floors; a floor that ramps is a floor that never existed.

---

#### C14 — the template: zones 1, 1b, 2, and REVIEW as a shape

**Files.** `report.py` `_TEMPLATE` (`report.py:2135-2584`), `_CHANGES_MACRO`,
`render_html_string`, `_environment` (new filters). `tests/test_report.py`.

The largest chunk and the one that strains the pipeline hardest. See §6.

**Contract.** The rendered document gains, in this order, before the existing
"What was compared" section at `report.py:2361`:

| Element | `id` | Present when |
|---|---|---|
| verdict banner with inline interval bar | `verdict` | always (exists today; gains the SVG) |
| spot-check sentence | `counterfactual` | `spot_check(...)` is not `None` |
| candidate table | `candidates` | `candidate_field(...)` is not `None` |
| multiplicity note | `multiplicity` | candidate table present |
| excluded-runs list | `excluded` | any exclusion |
| dimension matrix | `dimensions` | `model.dimensions.available` |
| dimension unavailability note | `dimensions` | not available — **same id**, so a link never dangles |
| timeline | `timeline` | `len(model.series) >= 1` |
| parameter strip | `parameters` | `len(model.series) >= 2` |

Everything currently in the document keeps its `id` and its relative order.
Methodology appendix and provenance keep their position, per the spec's
"Unchanged".

**REVIEW is a shape, not a colour.** When `model.verdict == "REVIEW"`, the
banner's interval bar is rendered with the floor **inside** the band, and the
callout replaces the verdict sentence with an actionable one built from a
recorded number — see §7.5 on which number. The template must select on the
verdict word, not on the CSS class, so the shape cannot be lost by a restyle.

**Must not.** Add `<script>`, `<link>`, a web font, or any attribute in
`FETCHING_ATTRS`. Use `{{ ... | safe }}` on anything derived from model output —
the SVG helpers return trusted markup and must be marked at the single point they
are injected, and a test must assert no other `| safe` exists in the template.
Introduce a new `{{ }}` reference to a field `ReportModel` does not define:
`StrictUndefined` will raise, which is the designed behaviour and must not be
worked around.

**Failure mode when wrong.** `assert_self_contained` runs inside `render_html`
before the file is written (`report.py:2708`), so an external reference fails the
render rather than shipping. The failure that *does* ship is a `| safe` on a path
that can carry model output, which turns an escaped `<img src="https://tracker/
x.png">` in a completion into a real fetch. `tests/test_report.py:1241` already
covers the existing paths; it must be extended to the new ones.

**Test that fails first.** `test_the_document_marks_exactly_one_expression_safe_
per_hand_rolled_svg_and_no_others` — parse `_TEMPLATE + _CHANGES_MACRO` and
assert the set of `| safe` filters equals the known SVG injection points by name.

**Then.** `test_the_rendered_report_has_no_external_url` and
`test_the_rendered_report_has_zero_script_and_zero_link_elements`
(`tests/test_report.py:1218`, `:1224`) must pass unchanged against a fixture that
exercises every new section.

**Done.**
```
.venv/Scripts/python -m pytest tests/ -q          # whole suite, zero skips
.venv/Scripts/python scripts/verify_release.py    # exit 0, not 2
```

**Reviewer.** Three specifics. First, `| safe` — grep the template and account
for every one. Second, `StrictUndefined` means a typo'd field raises at render;
check no `{% if model.foo is defined %}` was added to dodge that, because the
raise is the feature. Third, every new section's empty state: the spec's named
failure is "an empty chart or a crash", and there are eight new conditional
sections here, each with an empty case.

---

### Phase 5 — the seed generator

The spec is emphatic and correct: the seed is produced by running the **real
pipeline** against deterministic fake adapters, never by hand-writing a log.
"Hand-writing an evidence log would make the showcase a mockup wearing the
renderer's clothes."

Four facts, established by reading and by running the demo, that shape how this
is possible at all:

1. **`FakeAdapter` accepts a callable** `Callable[[str], str]`
   (`opik_rigor/adapters/fake.py:47`), not only a prompt→response mapping. A
   mapping gives every draw of an item the same answer, so every item is 0/n or
   n/n and the pass rate is quantised to multiples of 1/N. A callable holding a
   private per-prompt counter gives per-draw variation, which is what any
   interval that straddles a floor requires. This is the only way to seed a
   REVIEW, and the spec does not mention it.
2. **Concurrency must be 1.** A stateful callable is order-dependent;
   `run_goldenset`'s pool is within one item's n draws (`runner.py:291-296`). The
   demo already passes `concurrency=1` and records why. The seed must too, or it
   is not deterministic.
3. **Fourteen runs cannot share a directory.** `run_goldenset` names the artifact
   `<slug>__<goldenset_hash[:16]>.jsonl` in `out_dir` and, finding it non-empty,
   **resumes** rather than re-running (`runner.py:361`), producing "completed in
   2 parts". Each night needs its own subdirectory. `_contained`
   (`report.py:1079`) permits any path under the evidence log's own directory —
   `target.startswith(base + os.sep)` — so `seed/evidence.jsonl` with
   `seed/night-01/...` resolves and is read. Verified by reading; C17's test
   proves it.
4. **`created` is patchable and `ts` is not.** §4.1. The seed patches
   `model_migration_kit.comparison.utc_now`.

#### C15 — the synthetic golden set

**Files.** New `src/model_migration_kit/data/showcase_goldenset.jsonl` (or under
`docs/` if it must not ship in the wheel — decide against `MANIFEST`/`pyproject`
data rules, and check `tests/test_release_checks.py` for what the wheel is
asserted to contain). A small generator script under `scripts/`.

**Contract.** 96 items across 6 tags, 16 items per tag, every item singly tagged
except a deliberate handful carrying two — because C8's double-count behaviour
must be exercised by the showcase, not only by a unit test. Every `id` prefixed
`synthetic-`. Every `input` unique, because
`demo._refuse_duplicate_inputs` will otherwise refuse the set and the refusal
message will be about the demo rather than about the seed.

96 and 16: the spec asks for 60–120 items and per-dimension n near 15–25. At
n_per_item=5 the per-tag completion count is 80, and the per-tag **item** count
is 16 — below `MIN_N_FOR_A_VERDICT = 20` if the cell counts items, at or above it
if the cell counts completions. **That is a decision C9 must make and C15 must
match**: I recommend the cell counts **completions**, matching every other rate
in the document, and `min_n` stays 20 completions, which at 16 items × 5 draws
is comfortably cleared. The rest of this sentence used to read "while a
4-item tag at 20 completions is not", which is arithmetically false -- 4 x 5
is exactly 20 and `20 < 20` is `False`, so that tag clears. **R9 settles it**
with a second, independent floor in items; read R9 rather than this. If that reads
wrong to the reviewer, the alternative is item-level counting with `min_n = 20`
and a 25-item-per-tag set; say which before C9 is implemented, not after.

**Test that fails first.** `test_the_showcase_golden_set_loads_and_every_tag_has_
at_least_sixteen_items`.

**Reviewer.** Check no item id or input could be mistaken for a real product
question. The set is going to be read by strangers as an example of what a golden
set looks like.

#### C16 — the narrative adapters

**Files.** New `src/model_migration_kit/showcase.py`, or `scripts/`. Not
`demo.py` — `demo.py` is shipped, tested and stable, and the showcase's narrative
requirements (three candidates, per-draw variation, a scripted collapse on a
given night) are not the demo's.

**Contract.**

```python
def showcase_adapters(
    goldenset: GoldenSet, *, night: int
) -> tuple[FakeAdapter, tuple[FakeAdapter, ...]]: ...
```

Returns the baseline and three candidates for a given night index 1..14. The
narrative, from the spec:

- nights 1–13: all three candidates green;
- night 14: candidate B's `#refusal` dimension collapses, every other parameter
  held — same `n_per_item`, same golden set, same judges, same config, so the
  parameter strip has exactly one row with `changed=True`, which is the entire
  argument the strip exists to make;
- one earlier night (say 6) puts candidate C into REVIEW — its interval straddles
  the floor. This requires per-draw variation, hence the callable form.

Each adapter's `model_id` is prefixed so `RunSummary.is_fake` fires — the prefix
check is `adapter.startswith("Fake")` on the **class name**, which `FakeAdapter`
satisfies automatically, but the model ids should also read as synthetic
(`synthetic-baseline-v1`, `synthetic-candidate-b-v2`) so a screenshot cannot be
mistaken for a real provider.

Determinism is the contract: `showcase_adapters(gs, night=6)` called twice
produces adapters that, run through `run_goldenset` at `concurrency=1`, yield
byte-identical artifacts.

**Test that fails first.** `test_two_runs_of_the_same_night_produce_identical_
artifacts`.

**Reviewer.** The per-draw counter is state, and state plus a thread pool is a
flake. Check `concurrency=1` is not merely a default the caller could change but
is asserted where it matters.

#### C17 — the driver

**Files.** as C16, plus `tests/test_showcase.py`.

**Contract.**

```python
def build_showcase(work_dir: str | Path, *, nights: int = 14) -> Path: ...
```

Returns the path to the evidence log. Layout:

```
work_dir/evidence.jsonl
work_dir/night-01/<artifacts>
...
work_dir/night-14/<artifacts>
work_dir/showcase_goldenset.jsonl
work_dir/showcase.toml
```

For each night, for the baseline and each of three candidates: `run_goldenset`
into that night's directory with `evidence=` the shared log; then
`judge_artifact`; then `compare` once per candidate against that night's
baseline. Fourteen nights × three candidates = **42 comparison records** in one
log.

Dates: patch `model_migration_kit.comparison.utc_now` to return a fixed
RFC3339 string per night, one night apart, ending at a date the document treats
as "today". The patch is the seed generator's, applied with
`unittest.mock.patch` around the `compare` call and nowhere else — the run and
judging records keep rigor's real `ts`, which is honest: those lines really were
written a moment ago, and only the comparison claims a date.

**That asymmetry must be disclosed in the showcase itself.** A log whose
`migkit.comparison.created` says 14 nights ago and whose envelope `ts` says
today is exactly the sort of thing a careful reader notices, and a document that
did not name it would have earned the suspicion. One sentence in the synthetic
band.

**Edges.**

| Input | Required |
|---|---|
| `nights=1` | one night, three comparisons, no timeline gaps |
| `work_dir` already holding a log | refuse, rather than appending to it |
| a night whose run fails | propagate; a partial showcase must not be written |

**Must not.** Write any evidence record directly. Patch anything inside
`opik_rigor`. Patch `utc_now` globally or for the duration of the run.

**Failure mode when wrong.** Two runs collide on an artifact filename, resume,
and the showcase renders "completed in 2 parts" on every night — which is not
wrong, exactly, but is a story about resumption that nobody meant to tell.

**Test that fails first.** `test_fourteen_nights_write_fourteen_dated_
comparisons_into_one_log` — assert 42 comparison records, 14 distinct `created`
dates, and no artifact with `parts > 1`.

**Second test, which is the point of the whole phase.**
`test_the_showcase_log_renders_through_the_same_from_evidence_a_user_reaches` —
`ReportModel.from_evidence(log)` and `render_html`, asserting the document is
self-contained and that `is_demo` is true. If the showcase needs any code path a
user does not reach, the claim "this is the tool's actual output" is false, and
this test is what keeps it true.

**Reviewer.** Check the `utc_now` patch is scoped and that nothing in
`opik_rigor` was touched. Check the run time: 42 comparisons over 96 items at
n=5 is 4×96×5×14 = 26,880 completions plus judging. The demo does 120 in under a
second, so this should land in the low tens of seconds, but if it does not, the
seed becomes a thing nobody regenerates and it rots.

#### C18 — the synthetic band, and its unsuppressibility

**Files.** `report.py` (the `fake` band at `report.py:2318`), `tests/test_report.py`.

**Contract.** The band's text becomes series-aware: it names how many of the
runs in the document were produced by fake adapters, and the C17 timestamp
asymmetry. It cannot be suppressed by any input — not by a title override, not by
a real headline run appended to a seeded log, not by an empty adapter string.

**Test that fails first.** `test_a_real_run_appended_to_a_seeded_log_does_not_
remove_the_synthetic_band`.

**Reviewer.** Try to suppress it. That is the review.

---

## 6. Dependency graph, and what can run in parallel

```
C1 ──► C2 ──► C3 ──┬──► C4 ──► C5 ──► C6
                   │                  │
                   │           C7 ────┤
                   │                  │
                   ├──► C8 ──► C9 ──► C10
                   │
                   └──► C11   (independent; needs only item counts)

C6, C7, C10, C11 ──► C12, C13 (parallel with each other) ──► C14

C15 ──► C16 ──► C17 ──► C18      (C15/C16 may start any time; C17 needs C3)
```

**Critical path:** C1 → C2 → C3 → C8 → C9 → C10 → C13 → C14. Eight chunks, 32
agent passes.

**Parallel from the start:** C15 (the golden set) depends on nothing and should
be dispatched first alongside C1, because it is the only chunk whose output a
human may want to review by eye and it should not be on the critical path.

**Parallel after C3:** the C4–C7 arm and the C8–C10 arm are independent; C11 is
independent of both.

**De-risking order.** C1 and C2 come first not because they are foundational in
the abstract but because they are the chunks where the two settled facts become
code. If `RunPoint.floor` is wrong, every downstream chunk renders a truthful
number against a false gate.

Per the project's practice, dispatch each chunk into its own git worktree.
Two chunks touching `report.py` simultaneously — C3 and C10, or C12 and C13 —
will conflict in `_TEMPLATE` and in the `ReportModel` field list; keep them
serialised or accept the merge.

---

## 7. Chunks that resist the four-agent pipeline

Three do, and one of them badly.

### 7.1 C14, the template. Badly.

A blind tester cannot assert "the report looks right". What they can assert,
entirely blind, is structure:

- `external_urls(html) == ()` and `assert_self_contained(html)` — machine-checked
  and already the repo's idiom;
- element counts by `id` for each of the nine new sections, on fixtures that
  exercise present and absent;
- the `| safe` inventory over the template source, parsed rather than grepped;
- `StrictUndefined` — render every fixture and assert no `UndefinedError`;
- determinism — render twice with the same `now` and assert byte equality, which
  `render_html_string`'s docstring already promises.

**The browser check the coordinator described is real and useful and must not be
a pytest test.** Loading the rendered file in headless Chrome and asserting the
**page-scoped** request list holds nothing but the document is a genuine
end-to-end proof, and the coordinator's own warning is the reason to be careful:
a browser-wide net-log returned 13 "external" URLs that were all browser
telemetry, which would have been a false positive. Page-scoped means: attach to
the specific target/page, enumerate requests initiated by that page's frame
tree, and ignore anything not attributable to it.

It cannot live in `tests/`, because Chrome is not installed everywhere and the
test would have to skip — and `scripts/verify_release.py:30` pushes the exit
code to **2** on any SKIPPED check, while `tests/test_release_checks.py:627-630`
asserts SKIPPED and PASS are deliberately different statuses. A gate that goes
amber on a developer laptop is a gate people learn to ignore.

So: put it in `scripts/check_report_offline.py`, run it in the `demo` CI job
where the environment is known, and let the in-process
`assert_self_contained` remain the thing the test suite guarantees. The browser
proves the parser is not lying; the parser is what runs everywhere.

### 7.2 C12 and C13, the SVG. Manageably.

The `data-value` / `data-created` / `data-floor` attributes in those contracts
exist **for the blind tester**. They turn "does the chart look right" into "does
the floor line's `x` equal `PAD + floor * (width - 2·PAD)`", which is an
assertion someone can write from the contract alone. That is why the geometry is
specified in the contract rather than left to the implementer: without it, the
chunk is untestable blind, and with it, it is arithmetic.

### 7.3 C16 and C17, the narrative. Awkwardly.

"Night 14 shows a refusal collapse and a REVIEW appears on night 6" is a property
of an interaction between a scripted adapter, a scripted judge, real statistics
and a threshold. The tester cannot compute the expected pass rates from the
contract without effectively reimplementing the adapter.

The resolution is to make C16's contract about **determinism and shape**, not
about specific rates: same night twice gives identical artifacts; night 14's
`#refusal` completions for candidate B are strictly fewer than night 13's; every
other parameter hash is unchanged between 13 and 14. Then C17's test asserts the
*rendered consequence* — the parameter strip on night 14 has exactly one row with
`changed=True` — which is checkable from the contract and is the property that
actually matters. Whether the resulting verdict is REVIEW or NO-GO is a fact
about the seed the implementer tunes and the reviewer confirms by eye, and it
should be stated as such rather than pretended into a blind assertion.

### 7.4 The counterfactual line is not a power calculation

The spec says: "Requires a real power calculation. Not a hand-wave."

I do not think that is right, and the disagreement is worth settling before C11
is dispatched.

A power calculation answers *"how many samples would I need to detect an effect
of size δ at power β"*. `required_sample_size` (`comparison.py:163`) already
computes exactly that, it is already recorded — the demo log carries
`power.n_required: 140` and `runs_needed: 931` — and the report already prints
it ("powered for the configured effect: no (60 observed per side, roughly 140
required)").

But the sentence the spec wants is a different quantity:

> A 12-prompt spot check would have shown no failures at all in 34% of runs.

That is *"what is the probability that a k-item sample from this set contains
none of the F failing items"*. It is hypergeometric, it is `comb(N-F, k)/comb(N,
k)`, and it needs no power theory at all. The number 140 does not appear in it and
cannot be made to.

Three further objections, in descending order of how much they matter:

1. **"in 34% of runs" is the wrong noun.** Nothing here is distributed over runs.
   It is 34% of *spot checks*. A director who reads "34% of runs" and asks what a
   run is has found a hole.
2. **The unit must be items, not completions.** Using the completion-level rate
   as `p ** 12` is the obvious implementation and it is wrong here: at
   temperature 0 — and under `FakeAdapter` with a mapping — all n draws of an
   item are identical, so 60 completions are 12 decisions. `0.75 ** 12` is 3.2%
   where the item-level answer is an order of magnitude larger. A tool arguing
   that naive methods are blind must not compute its own headline number by a
   naive method.
3. **A real spot check is not a random draw.** An engineer picks twelve prompts
   they think are representative. Nobody can model that, so the sentence must
   name the assumption it did make — "drawn at random" — and let the reader
   discount it.

**Recommendation: implement C11 as specified above (hypergeometric, item-level,
assumption named), and drop the phrase "power calculation" from the spec.**

There is also a second sentence available, which reuses the recorded number
exactly and requires no new arithmetic whatever, and I would ship both:

> This run observed 60 completions per side. Detecting a ten-point drop at 80%
> power needs roughly 140.

That one is a lookup from `power.n_required`, it is already in the document, and
it makes the same argument from the other direction.

### 7.5 The REVIEW callout must pick between two numbers that mean different things

The spec's example is *"collect 340 more completions"*. Two recorded numbers
could produce it, and they are not interchangeable:

- `runs_needed` — observed 931 on the demo's baseline gate — is completions
  needed for the **one-sided lower bound to clear the floor at the currently
  observed rate**. It answers "how much more evidence to pass".
- `power.n_required` — observed 140 — is completions needed to **detect the
  configured minimum effect at the target power**. It answers "how much evidence
  before 'no regression detected' means anything".

REVIEW is the state where the evidence is too thin to decide, so the callout
should quote **`n_required`**, minus what was observed, and say which question it
answers. Quoting `runs_needed` would be telling the reader how long to keep
sampling until they pass, which is a different and much less defensible
instruction. Getting this backwards produces a report that reads perfectly and
advises collecting evidence until the answer comes out right.

Both numbers can be `None` — `runs_needed` was `null` on the demo's candidate
gate — so the callout needs a no-number fallback: "the evidence is too thin to
decide and the record does not say how much more would suffice."

---

## 8. What the spec gets wrong

Collected, so none of it is discovered mid-chunk.

1. **`holm_bonferroni` is not opik-rigor's.** It is `comparison.py:224`, in
   migkit's own `__all__`. `opik_rigor` 0.2.0 raises `AttributeError` for the
   name. (§3)
2. **"Grouping must respect `_require_comparable`" is not implementable as
   written.** That function takes live `JudgedArtifact` objects and checks
   per-key coverage; the report has payloads and often has no artifacts. A
   narrower payload-level predicate is the plan, with a test bridging the two.
   (§4.4)
3. **"No new instrumentation, no schema change" understates the dimension
   matrix.** No schema changes, but zone 1b acquires a dependency on the judged
   artifacts and on a hash-matching golden set, and inherits two independent ways
   of being unavailable — including the ordinary cross-machine case. (§1c)
4. **The counterfactual is not a power calculation**, and its natural
   implementation (`p ** 12` on the completion rate) is wrong by roughly an order
   of magnitude for this tool's data. (§7.4)
5. **`report.py:809-811` "keeps only the last of each" needs a new home for the
   statistics.** Keeping all the records is trivial; the consequence is that
   multiplicity and per-dimension intervals need `holm_bonferroni` and
   `wilson_interval` at render time, and `tests/test_report.py:1333` fails the
   build if report.py calls either. A new module is the answer, not an amended
   invariant. (§3)

Two smaller ones:

6. The evidence envelope key is `event_type`, not `event`.
7. Zone 1b's spec says `goldenset.py` "already counts them and nothing downstream
   uses the counts". The counts *are* rendered today — the demo report shows
   `arithmetic: 4, extraction: 4, multi-value: 2, refusal: 4` in the "What was
   compared" block (`report.py:2377-2381`). What nothing uses is the **join**
   between tags and outcomes, which is the harder thing and is §1(c).

Nothing in the spec is unimplementable outright. Item 3 is the one that will cost
noticeably more than it reads.

---

## 9. Risks, ranked, with the cheap resolution for each

**1. The dimension matrix is unavailable in the demo-sharing case.**
Its inputs are two files outside the log, and `_resolve` legitimately refuses
them on any machine the log was moved to. If most readers of the showcase see
"dimensions unavailable", the spec's most actionable feature is invisible in the
place it matters most. *Resolve cheaply:* before C8, render the existing demo log
from a directory with the artifacts moved away and confirm the degradation reads
acceptably. Ten minutes. If it does not, the showcase must ship its artifacts
beside the log, which is a packaging decision better made now than at C17.

**2. The showcase's REVIEW cannot be seeded.** It needs an interval straddling
the floor, which needs per-draw variation, which needs the callable `FakeAdapter`
form and careful tuning against a real Wilson bound. *Resolve cheaply:* before
C16, spike a throwaway script — 96 items, a callable adapter with a fixed
per-item pass fraction, `assert_pass_rate` from rigor — and confirm a REVIEW is
reachable at n=5. An hour. If it is not, the spec's "at least one REVIEW earlier
in the timeline" needs `n_per_item` to vary across nights, which then changes
`n_per_item` between runs and trips the comparability guard, which is a genuine
design collision and better found in a spike than at C17.

**3. `test_the_report_module_computes_no_statistic_of_its_own` is load-bearing
and easy to route around.** An implementer under pressure will import
`wilson_interval` into `report.py` and add it to the test's allowlist. *Resolve
cheaply:* state in C9's and C6's contracts that `series.py` is where the
statistics live and that the forbidden list in `tests/test_report.py:1343-1352`
may not be edited. Make the reviewer check `git diff` for that file.

**4. `report.py` is 2,749 lines and C14 rewrites a third of the template.**
Two chunks touching `_TEMPLATE` concurrently will conflict badly. *Resolve
cheaply:* serialise C12, C13 and C14, and have C12/C13 add their functions in a
new section at the end of the module rather than near related code.

**5. The 42-comparison showcase log may be large.** rigor's `judge.verdict`
record embeds input, output and the raw reply per completion — the reason
`_stream_records` exists. 26,880 completions × 4 sides is a log in the tens of
megabytes. *Resolve cheaply:* measure at C17, and if it is unwieldy, keep the
showcase's model outputs short by construction. The golden set is ours to write.

**6. Six new conditional sections, each with an empty state.** The spec's named
failure is "an empty chart or a crash". *Resolve cheaply:* one parametrised
fixture set in C14 — empty series, one-point series, no candidates, no
dimensions, no floor — rendered through every section.

**7. `stale_after_days` and `MIN_N_FOR_A_VERDICT` are judgement calls with no
recorded provenance.** They will be quoted back as if they were derived.
*Resolve cheaply:* both get a docstring paragraph saying they are conventions,
and both appear in the methodology appendix (`methodology_sections`,
`report.py:1634`) beside the thresholds that *were* configured, clearly separated
from them.

---

## 10. What I would do first

Three things, in this order, before any chunk is dispatched:

1. **Settle the counting unit for `MIN_N_FOR_A_VERDICT`** — completions or items
   (§C15). C9 and C15 both depend on it and they are on different arms of the
   graph.
2. **Run risk 1's ten-minute check.** It can change the packaging of the
   showcase.
3. **Dispatch C1 and C15 in parallel.** C1 is the critical path's head; C15 is
   the only chunk a human will want to read by eye and should not block on
   anything.

---

## Revisions, 2026-08-21, after the pilot and the two de-risk checks

These supersede the chunk contracts above where they conflict. Each is recorded
with the evidence that produced it, because the reason is what a later reader
needs and it is the part that evaporates.

### R1 — C8 does not need the judged artifacts, and should not use them

**The contract for C8 is wrong.** §7 argued that because `judge.verdict` records
carry no `item_id`, the per-tag matrix must come from the judged artifacts, which
`_resolve` refuses on a cross-machine render. The premise is true and the
conclusion is false.

`judge.verdict.input` is the verbatim golden-set input — `judging.py:744` passes
`item_input` straight through — so a verdict joins to an item by its input text.
Verified on a real 300-record demo log: 120 of 120 verdicts joined, and the
resulting matrix was checked cell-for-cell against the judged artifacts at
**24/24 identical, zero mismatches**. The side comes from the append-only
ordering: verdicts accumulate until the next `migkit.judging_completed`, whose
`model_id` names the side, and `cli.py:521` iterates the two runs strictly
sequentially, so the ordering is structural rather than incidental.

Build the matrix from **`judge.verdict` + the golden set**. This collapses the
two independent decline reasons to one, and it makes the matrix survive the
cross-machine re-render that `report.py`'s own module docstring calls the
designed workflow.

Three guards the join needs, each detectable from the log:

- **Duplicate inputs.** `goldenset.py:113-125` enforces unique `id` but not
  unique `input`. Two items sharing an input cannot be told apart, so refuse the
  matrix loudly rather than attributing a verdict to the wrong item.
- **Imputed rows.** A failed completion never reaches `evaluate()` and writes no
  verdict. Recover it from `migkit.completion` where `ok=false`, which carries
  both `item_id` and the sampled `model_id`.
- **Resumed judging.** `judging.py:612-620` skips already-graded records.
  `migkit.judging_completed` carries `graded`/`judged`/`imputed`/`parse_failures`,
  so a shortfall is detectable; decline rather than under-count.

Parse failures correctly write no verdict and are already excluded from pass
rates, so they need no special handling.

### R2 — the showcase does not ship the judged artifacts

The question "must the showcase ship artifacts" was based on a wrong model of how
the showcase is made. `migkit-demo-report.html` is not tracked; it is rendered by
whoever runs the demo, and `cli.py:573-596` renders it *before* tearing down the
work directory, for exactly this reason. The showcase is a self-contained page
produced where every file still exists, and the stranger who reads it never
re-renders. A matrix computed at render time is simply in it.

R1 makes this moot anyway, but for the record: all four artifacts are 65,847
bytes raw, ~5.9 KB gzipped, against an evidence log of 143,558 bytes.

### R3 — a per-dimension cell counts completions, not items

C15 flagged this as a decision C9 must make and C15 must match, and left it open.
It is settled: **completions**, with `MIN_N_FOR_A_VERDICT = 20` completions.

Three-way agreement made this cheap to ratify — the plan recommended it, C15's
implementer was briefed to it, and C15's tester asserted it. It also matches every
other rate in the document. Under item-level counting the set would need 25 items
per tag rather than 16, and both of C15's headline tests would be wrong.

### R4 — the definition-of-done commands do not work from a worktree

Every chunk's **Done** block says `.venv/Scripts/python -m pytest tests/...`.
Run from a worktree that is what happens:

```
E   ModuleNotFoundError: No module named 'model_migration_kit.series'
```

The editable install resolves `model_migration_kit` to the main checkout, so a
new module in a worktree is invisible and an edited one is silently the wrong
copy. This bit the C1 merge, and it is the third distinct encounter with the same
hazard in one session — one agent reported a false green from it earlier, and
C15's tester designed around it unprompted by resolving from `__file__` rather
than `importlib.resources`.

The working form, and what every chunk's Done block should say:

```
PYTHONPATH=<worktree>\src <main-checkout>\.venv\Scripts\python.exe -m pytest <worktree>\tests\... -q
```

Verify rather than trust: print `module.__file__` and confirm the path is inside
the worktree. A green suite that imported the main checkout has tested nothing.

### R5 — two defects in shipped code, found while de-risking

Neither is part of this plan. Both are in 0.1.1 today and are recorded here so
they are not lost.

**The degraded render is wrong, not merely incomplete.** When artifacts cannot be
resolved the document still prints "5 draws per item over **0 items**",
"completions observed / expected: **baseline 0 / ?**" directly above a table
reading 55/60, and "Every one of the 4 changed item(s) carries its full outputs:
**0 characters** of quoted model text" — a completeness claim made while showing
nothing. The partial banner also names the wrong reason; the real one appears
some 200 lines further down. Missing data stated as zero is worse than missing
data stated as missing.

**`migkit demo --work-dir ./relative` renders a self-degraded report.** Paths
resolve to `demo1\demo1\...`, every artifact fails to load, and the run produces
the degraded output above from a directory where the files are sitting in plain
sight. Absolute paths work. A reader following the README with a relative path
gets a broken showcase on their first attempt.

Also worth naming: report degradation never affects the exit code, because
`cli.py:435` derives it from the verdict alone. A completely stripped render and
a complete one are indistinguishable to a pipeline. Elsewhere in this codebase a
SKIPPED release check is exit 2 precisely so a gate cannot mistake absence for
success; the report does not hold that line.

### R6 — C13's signature contradicts C13's prose; the counts come back in a tuple

The contract declares `timeline_svg(...) -> str` and then says, two paragraphs
later, that "the count of such runs is returned to the caller for a sentence
beneath the chart" — and its Edges table adds a *second* count, for points whose
`pass_rate is None`. A string cannot carry either.

Dispatched as written this guarantees a mismatch: the implementer picks one
reading, the tester picks the other, and neither is wrong. Settled before
dispatch, and both agents were briefed identically:

```python
class Timeline(NamedTuple):
    svg: str
    runs_without_floor: int
    runs_without_rate: int

def timeline_svg(
    points: Sequence[RunPoint], *, width: int = 900, height: int = 260,
) -> Timeline: ...
```

The name `timeline_svg` stays because C14 will call it by that name, and a
`NamedTuple` is still a tuple, so `svg, *_ = timeline_svg(...)` keeps working.
Both counts are counts of **points**, not of segments — a run with no floor is one
point whether or not it sits between two that share a floor.

### R7 — the render helpers' padding constants are named, and imported by their tests

C12's geometry contract writes its padding as `PAD = 8`. `report.py` is 2,700
lines and a bare `PAD` is a collision waiting for the second helper that wants
one — which is C13, in the same module, in the same week. So: **`INTERVAL_BAR_PAD`
for C12, `TIMELINE_PAD` for C13**, both module-level and importable from
`model_migration_kit.report`.

Both testers were told to **import the constant, never to hard-code its value**. A
test that spells `8` where the code spells `INTERVAL_BAR_PAD` cannot tell a
deliberately changed constant from a broken projection: it fails either way, and
the failure says nothing about which happened.

C12's `data-value` is also pinned, for the same reason the constant is: the
*unmapped* float to exactly six places. That attribute is the seam the tests use
to check the model's number reached the drawing without re-deriving the
projection, and a rounded one would make the check circular.

### R8 — OPEN: the headline verdict and `series[-1].verdict` can disagree

Not settled. Raised by C3's implementer and recorded here so the reviewer arrives
at it with the evidence rather than rediscovering it.

C3's Edges table requires that "`series[-1]` describes the same run as the
headline fields". Its "Must not" requires that no existing field change value. On
one log shape those two requirements point in opposite directions: given
`C1 C2 V1`, the headline takes the last `migkit.verdict` record unconditionally
and reports V1, while the series pairs FIFO and closes C1 with V1, leaving
`series[-1].verdict` as `None`.

The implementer chose "headline unchanged" over "the two always agree", on the
grounds that `compare` writes a comparison and its verdict adjacent
(`comparison.py:906-908`), so no log written today has this shape. That reasoning
is sound and the choice is the conservative one, but it leaves a stated edge
unmet, and the fix — if one is wanted — belongs to the headline's reduction, not
to the series, which makes it a behaviour change C3 explicitly forbids. So it is
a chunk of its own or it is a documented limitation. **Decide it at C3's review,
and if the answer is "documented limitation", the document that carries it is
`report.py`'s docstring, not this plan** — nobody debugging a disagreeing banner
will be reading the build plan.

---

#### C19 — the verdict belongs to the comparison before it

Added 2026-08-21, out of C3's review. This is a **correction to C2**, not a change
of mind: C2's contract contradicts itself, and C2's implementer picked the half
that is wrong. It also carries a defect older than either, shipped in 0.1.1.

**Files.** `series.py` (the pairing rule in `SeriesBuilder.add`, and
`read_series`' docstring). `report.py` (the reduction in `from_evidence`, and its
module docstring). `tests/test_series.py` (two tests, rewritten).
`tests/test_report.py` (one `xfail` marker removed, tests added). Plan lines
469-486, amended so prose and Edges row five agree.

**Why.** There is exactly one writer of `migkit.comparison` and `migkit.verdict`
in this repository -- `comparison.py:907-908`, two `evidence.append` calls back to
back inside one `if`. So a log holding two comparisons before either verdict
cannot be written by this pipeline, and that is the only shape first-in-first-out
pairing gets right. The shape a **crash** produces -- a comparison with no verdict
after it, then the next night appended to the same file -- is the shape FIFO gets
wrong, and it gets it wrong cumulatively:

```
log:  C(night-1)   C(night-2) V(night-2)   C(night-3) V(night-3)
      night-1  verdict=GO     reason='night 2 was fine'
      night-2  verdict=NO-GO  reason='night 3 regressed'
      night-3  verdict=None
```

One crashed night shifts every later verdict by one, permanently, in a file that
only ever grows. That is verbatim the failure `series.py`'s docstring says the
module exists to prevent, produced by the rule chosen to prevent it.

**Contract, part one -- the series.** A comparison record opens a point. **Every
verdict record updates the most recently opened point**, overwriting a value
already there. A verdict arriving before any comparison is ignored. Nothing is
sorted, nothing is de-duplicated, and points stay in the order their comparisons
were written -- all three of `read_series`' existing arguments for that survive
unchanged; only the FIFO justification goes.

**Contract, part two -- the headline.** `from_evidence` currently keeps the last
comparison and the last verdict as two **independent** last-wins variables, so a
verdict from an earlier run fills the slot of a run that never produced one. Set
`verdict_record = None` on every comparison record, so a verdict only ever
describes the comparison it followed.

This is the change C3's "Must not" forbade, which is why it is here and not there.

**The two rules are one rule.** Ship either alone and the banner and the timeline
can still disagree about which night a NO-GO belongs to. The point of the pair is
that `series[-1]` describes the headline run **by construction** rather than by
coincidence:

| log shape | headline verdict | `series[-1].verdict` | agree |
|---|---|---|---|
| `C V` | V | V | yes |
| `C1 V1 C2 V2` | V2 | V2 | yes |
| `C` (crashed) | None, with disclosure | None | yes |
| `V C` | None | None | yes |
| `C1 C2 V` | V | V, on C2 | yes |
| `C1 V1 C2` (crashed) | None, with disclosure | None | yes |
| `C1 C2 V1 V2` | V2 | V2 on C2, C1 None | yes |
| `C V1 V2` | V2 | V2 | yes |

The last row is why the rule is "updates" and not "closes the most recent *open*
point": under a close-once rule V2 is dropped and the series disagrees with a
banner that took it.

**Edges.**

| Input | Required |
|---|---|
| `C V C` | `model.verdict is None`, `exit_code == 3`, and `completeness.missing` names the absent `migkit.verdict` record |
| a crashed night mid-log | that point's verdict is `None` **and no later point's verdict moves** |
| `V` before any `C` | ignored; no point created, nothing overwritten |
| `C V1 V2` | the point carries V2 |
| every log written by `compare` today | byte-identical output to before |

**Must not.** Sort. Drop a point. Change what a *complete* log renders --
this chunk may only change what a log containing a crashed run renders.

**Remove** `tests/test_report.py`'s dead-run `xfail(strict=True)` marker, and
leave the test body untouched. Stated as its own instruction rather than as a
Must-not item, because C19's tester read the original -- "Must not ... Leave the
marker in place: when this lands it XPASSes, and a strict xfail that starts
passing fails the suite on purpose" -- as contradicting itself. The clause after
the colon is the *reason* the marker must go, not an argument for keeping it, but
a sentence that needs that explained is a sentence that should have been written
the other way round.

**Failure mode when wrong.** A crashed run rendering as a clean GO with exit 0
and `completeness.complete is True`, which is what 0.1.1 does today. `cli.py:435`
derives the exit code from the verdict alone, so a pipeline sees green for a run
that never decided anything.

**Test that fails first.** `test_a_run_that_died_before_deciding_is_not_reported_
as_last_nights_verdict` -- log `C V C`, assert the headline verdict is `None` and
the exit code is 3.

**The two tests that must change.** `test_two_comparisons_written_before_either_
verdict_pair_first_with_first` and `test_a_verdict_with_no_point_left_open_is_
ignored_and_overwrites_nothing`, both in `tests/test_series.py`. They are the only
tests in the repository that pin the old rule. Keep their docstrings' *reasoning*
about why pairing is delicate and invert what they expect. They are evidence that
C2's rule was chosen deliberately, not by accident, so deleting them loses the
record of a decision that was reconsidered.

**Reviewer.** Confirm by sweep, not by argument, that `comparison.py:907-908` is
still the only writer of either event -- the whole case rests on it. Then check the
concurrent-writer case: `opik_rigor`'s evidence log documents that concurrent
writers interleave whole records and `cli.py:87` makes one shared path the
default, so `C_A C_B V_A V_B` and `C_A C_B V_B V_A` are equally likely. Neither
rule is right there; say whether this chunk should detect it rather than guess.

---

#### C20 — the self-containment scanner judges shape where it means dereference

Added 2026-08-22, out of C12's and C13's reviews. Shared code. Three chunks are
already shaped by this defect and C14 will be the fourth.

**Files.** `report.py` (`_UrlScanner._attribute_reason`, `FETCHING_ATTRS`).
`tests/test_report_untrusted_input.py`.

**The defect.** `_attribute_reason` applies two *name-agnostic* rules -- the
protocol-relative check and `_SCHEME_RE` -- to every attribute value regardless of
the attribute's name. The property it means to enforce is "this document fetches
nothing when rendered". The property it actually tests is "no attribute value
begins with something that looks like a scheme". Those are different, and the rule
is neither sound nor complete:

| fragment | scanner | browser |
|---|---|---|
| `<svg xmlns="http://www.w3.org/2000/svg">` | flagged | never fetched |
| `<rect data-verdict="review: n was too small">` | flagged | never fetched |
| `<rect data-verdict="javascript:alert(1)">` | flagged | never fetched, no script runs |
| `<p data-note="//TODO fix this">` | flagged | never fetched |
| `<p title="see http://example.com for details">` | **not** flagged | never fetched |

The last row is the tell: `title` escapes only because `_SCHEME_RE` is anchored
`^\s*`. The rule is not defending a boundary; it fires on values that happen to
*begin* with a scheme.

**What it has already cost.** Two chunks omitted `xmlns` from their `<svg>`,
which is correct for inline SVG in HTML5 and wrong the moment anyone saves the
chart standalone. Worse, C13 found that a recorded verdict reading
`review: n was too small` matches `scheme:` -- so **an evidence log can stop the
report rendering at all**. A control whose false positives are triggerable by the
untrusted input it exists to defend against is a denial-of-render vector, which is
a larger hole than the one it closes.

**Contract.** In `_attribute_reason`, skip **only** the two name-agnostic rules
for attribute names a browser provably never dereferences: `data-*`, `aria-*`,
`xmlns`, and `xmlns:*`. Change nothing else. The event-handler rule, the
`FETCHING_ATTRS` rule and both CSS rules are already name-based and are doing all
the real work.

Additionally, add `ping`, `xlink:href` and `xml:base` to `FETCHING_ATTRS`. All
three genuinely fetch, none is in the set today, and each is currently caught
*only* by the broad rule. They are not exempted by this change, so nothing breaks
today -- but leaving three real fetching attributes resting on a rule this chunk
has just narrowed is how the next narrowing becomes a hole.

**Why an exemption rather than an allowlist.** An allowlist must enumerate every
dereferenced attribute correctly and decays as HTML grows. The exemption needs
only that three families are *never* dereferenced, which is a stable property of
the platform. It shrinks the trusted claim to something a reader can check.

**The safety argument, which must be written into the code.** This is sound only
because `<script>` and inline event handlers are separately forbidden and
asserted: nothing can read a `data-` value into a fetch if no script runs. Put
that coupling in the comment beside the exemption. **If the script ban is ever
relaxed, this exemption becomes unsafe** -- and the person relaxing it will be
reading that comment, not this plan.

**Must not.** Relax, narrow or reword the `<script>` ban or the event-handler
rule; this chunk's correctness rests on them. Exempt any attribute family beyond
the four named. Touch `_SCHEME_RE` itself -- the anchoring is odd but it is what
makes the remaining name-based checks cheap, and changing it is a separate
question.

**Test that fails first.** `test_a_data_attribute_holding_a_scheme_is_inert_
rather_than_a_violation` -- a document carrying `data-verdict="javascript:alert(1)"`
must render, must report no external URLs, and must contain no script. Note the
assertion is "the document renders and nothing fetches", **not** "the scanner does
not fire": a test that only checks the scanner stayed quiet would pass against a
scanner that had been deleted.

**Edges.**

| Input | Required |
|---|---|
| `data-*`, `aria-*`, `xmlns`, `xmlns:*` holding a scheme or `//` | not a violation |
| `href`, `src`, `ping`, `xlink:href`, `xml:base` holding a scheme | violation, as today |
| `onclick="..."` anywhere | violation, as today |
| a `<style>` carrying `url(https://…)` | violation, as today |
| an evidence log whose recorded verdict reads `review: n was too small` | the report renders |

**Failure mode when wrong.** Two, opposite. Too narrow and a real fetch ships in
a document that promises it is self-contained. Too broad and an evidence log can
refuse to render -- which is what happens today.

**Reviewer.** Mutate the exemption to cover `href` and `src` and confirm the suite
screams. Then check the claim the whole chunk rests on: that no `data-*`,
`aria-*` or `xmlns*` attribute is dereferenced by a browser in a document with no
script. Look specifically for CSS `attr()`, SVG `<use>`, and anything that can
turn an attribute value into a request without script. If you find one, this
chunk is wrong and the status quo is right.

**Afterwards, not part of this chunk.** C12 and C13 may put `xmlns` back; C13's
`data-verdict` filter becomes defence-in-depth rather than load-bearing, which
unblocks carrying the recorded verdict verbatim-escaped; C14 gets the same
freedom for its own `data-` attributes.

---

### R9 — the completions floor has an effective item floor of four, and four is the number the spec uses as its own example

`MIN_N_FOR_A_VERDICT = 20` counted in completions, ratified by R3, does not do the
job R3 believed it did. The plan's justification for it (line 1411) says 16 items
x 5 draws "is comfortably cleared while a 4-item tag at 20 completions is not."
Four items at `n_per_item=5` **is** 20 completions, and `20 < 20` is `False`, so
that tag clears. The effective floor is four items.

Four is not an arbitrary number to land on. It is the number in the spec's own
refusal sentence — *"20 items needed for a verdict here; you have 4"* — which is
the product's showpiece example of a cell that must decline. Under R3 as written,
the showpiece renders a verdict.

Two things are wrong at once and they need separate fixes:

**The unit in the refusal sentence is items, and R3 chose completions.** The spec
wrote that sentence before R3 existed. Keeping the sentence and changing the unit
under it produces a cell that says "20 items needed, you have 4" while refusing on
a completion count of 20 — a note that contradicts the number it is refusing over.

**Twenty completions from four items are not twenty observations.** They are four
questions asked five times each. The draws within an item are correlated by
construction: same prompt, same reference, same rubric clause. A dimension verdict
generalises over *questions*, so the sample size that matters for "does the
candidate hold up on refusal" is nearer four than twenty. This is the substantive
reason the completions floor is too weak, and it is why the fix is not simply a
bigger completions number — at `n_per_item=10` a 4-item tag would clear a floor of
40 just as easily.

**Ruling: two independent floors, and a cell must clear both.**

```python
MIN_N_FOR_A_VERDICT: int = 20        # completions, unchanged -- R3 is not reopened
MIN_ITEMS_FOR_A_VERDICT: int = 10    # distinct items contributing to the tag
```

Ten. **R10.6 corrects the argument that stood here**, which was circular -- it
defined the threshold by restating it. The two constraints below narrow the
number to the band 5 to 16 and no further; ten is a judgement inside that band,
expressed as a leverage tolerance: no single golden-set item should move a
published dimension claim by more than a tenth. It refuses the spec's own 4-item
example. The showcase clears it at 16 items and 80
completions, so **C15 does not change and its two headline tests stand.**

R3 is amended, not reversed: the completions floor stays at 20 completions and
keeps every argument R3 made for it. R9 adds a second floor that R3 did not
consider, because R3 was answering "items or completions?" and the answer turns
out to be "both, for different reasons."

This changes C9's signature (it must be told the item count) and C8's return (it
must produce one). Both are restated below. It does not change anything already
merged.

---

### C8 (restated under R1) — per-tag counts from the log, never from the artifacts

This replaces the C8 section at line 866 in full. That section takes a
`JudgedArtifact`; R1 established that it must not, and the evidence for that is in
R1. Read R1 and this; do not read line 866.

**Files.** A **new** `src/model_migration_kit/dimensions.py` and a new
`tests/test_dimensions.py`. Both exist on `main` already, holding a module
docstring and nothing else — that placement decision is made and is not yours to
revisit. `series.py` is 652 lines, past the ~600 the original contract set as the
trigger, and it is being edited by C19 concurrently.

**Insert your code directly after the module docstring**, above everything C9 will
add. C9 is being written in parallel against the same file and appends at EOF. Do
not reorganise, re-header, or reflow the module; the merge between you is mine and
I want it mechanical.

**Contract.**

```python
class TagCount(NamedTuple):
    passes: int
    n: int
    items: int      # distinct golden-set items that contributed, for R9's floor

@dataclass(frozen=True)
class DimensionCounts:
    available: bool
    reason: str                                       # "" when available
    by_model: Mapping[str, Mapping[str, TagCount]]    # model_id -> tag -> count

def dimension_counts(
    records: Iterable[EvidenceRecord],
    items: Mapping[str, Item],
    *,
    judge: str,
) -> DimensionCounts: ...
```

`records` is what `evidence.stream_records` yields — **stream it, do not list
it.** `evidence.py`'s docstring records the measurement: a `judge.verdict` embeds
the input, the output and the judge's raw reply for every completion, and holding
the log cost 5.0-5.8x its own bytes resident. Materialising the records here would
reintroduce exactly the amplification that module was extracted to kill. `items`
is `gs_view["by_id"]`, id -> `Item`.

**The join, and why it is by input text.** `judge.verdict` carries no `item_id`.
It carries `input`, which `judging.py:744` passes through verbatim from the golden
set, so an item is recovered by inverting `items` on `Item.input`. R1 verified
this on a real 300-record log at 120/120 joined and the resulting matrix at 24/24
cells identical to the artifact-derived one.

**The side comes from ordering, not from the record.** The `model_id` on a
`judge.verdict` is the **judge's** model, not the candidate's — read
`judge.py:318-328` before you write a line of this, because using it is the single
most likely way to get this chunk silently wrong, and every cell would still
render. The side is the `model_id` on the next `migkit.judging_completed`
(`judging.py:664`). Verdicts accumulate; that record closes the group and names
whose they were.

Failed completions do **not** need the ordering trick: `migkit.completion` carries
`model_id` on the record itself (`runner.py:465-475`). Use it.

**One forward pass. Four rules.**

| record | what it does |
|---|---|
| `judge.verdict` with `payload["judge"] == judge` | append `(input, passed)` to the open group |
| `judge.verdict` for any other judge | ignore |
| `migkit.judging_completed` | close the group, attribute it to `payload["model_id"]` |
| `migkit.completion` with `ok` false | one non-pass for `payload["item_id"]` under `payload["model_id"]` |

A failed completion never reaches `evaluate()` and writes no verdict, so without
that last rule the denominator silently loses exactly the completions the model
failed — the one bucket a pass rate must not lose.

**Parse failures need no rule, and this is the correction R1 makes to the original
contract.** Line 866 says parse failures are "counted in `n`", with the rationale
that "the aggregate gate already counts them this way." That rationale is exactly
right and the conclusion drawn from it is backwards. `comparison.py:1184-1193`
drops them from both numerator and denominator, and the module docstring at
`comparison.py:39-43` gives the reason: "the *judge* having been unintelligible
... Conflating the two would let an unreliable judge read as an unreliable model."
A dimension view that counted them would disagree with the gate above it, which is
the harm the original rationale named. In the log they are `judge.parse_failure`
records, which carry no `input` and therefore cannot join to an item at all — so
this is a consequence to state in the docstring, not a branch to write.

**Imputed records stay in, and the same source settles it.** `comparison.py:1191`:
"a model that times out has told us something." That is the failed-completion rule
above.

**Counting.** For each attributed verdict, `tags = item.tags or ("",)`; every tag
gets `n += 1` and `passes += 1` if it passed, and the item id joins that tag's
distinct-item set. An item carrying three tags contributes to three tags —
`_parse_tags` (`goldenset.py:263`) returns a duplicate-free tuple, so assert that
invariant rather than defending against it. Untagged items go to the reserved key
`""`, which the caller renders as "untagged" and never drops.

**The three guards R1 requires, each a refusal with a reason, never a silent
approximation.** `available=False` with a sentence a reader can act on:

| condition | reason must name |
|---|---|
| two items share an `input` | both item ids. `goldenset.py:113-125` enforces unique `id` and **not** unique `input`, so this is reachable; two items with one input cannot be told apart and a verdict would be attributed to the wrong one |
| a verdict's `input` is in no item | the offending input, truncated. `_load_goldenset` already refused on hash mismatch, so an unjoinable input means the log and the set disagree in a way the hash did not catch |
| `len(group) != payload["graded"].get(judge, 0)` | the judge, the expected count and the seen count. `judging.py:612-620` skips already-graded records on a resume, so a resumed pass writes fewer verdicts to this log than the artifact holds; under-counting silently is the failure mode |
| verdicts still open at end of stream | that a judging pass did not complete |
| the judge produced no verdicts anywhere | the judge name |

That last row **changes** the original contract's edge, which returned `{}`. C10
has to print a sentence saying why a matrix is missing, and `{}` is not a
sentence. Decline with a reason.

**Edges.**

| Input | Required |
|---|---|
| an item with no entry in `items` | unreachable via the hash check; still, refuse rather than guess |
| a tag in the set with no records for a model | key present, `TagCount(0, 0, 0)` — a dimension that was in the set and produced nothing is a finding, not an absence |
| every item untagged | one key, `""`. `available` stays `True` |
| a log with no `migkit.judging_completed` at all | refuse, naming the judge |

**Must not.** Open a file. Take a path. Import `report`. Touch a `JudgedArtifact`.
Compute a rate, an interval or a verdict — this chunk returns integers only. Build
a list of the records.

**Failure mode when wrong.** Two of them, and the second is worse. A multi-tagged
item counted once, so `#refusal` and `#multi-value` disagree about `refuse-04` and
the columns sum to nothing a reader can check. Or the judge's `model_id` used as
the side, which produces a full, plausible, entirely wrong matrix in which both
columns are the same numbers.

**Test that fails first.**
`test_an_item_carrying_two_tags_is_counted_under_both_of_them`. The demo's
`refuse-04` is exactly this item — `["refusal", "multi-value"]` — and it is one of
the three flips, so this test is also the demo's own case.

**Done.** `PYTHONPATH=<worktree>\src <main>\.venv\Scripts\python.exe -m pytest
<worktree>\tests\test_dimensions.py -q`, and print `dimensions.__file__` to prove
it resolved inside your worktree. See R4; a green suite that imported the main
checkout has tested nothing.

**Reviewer.** Three things, in this order. **One:** confirm the side is taken from
`migkit.judging_completed` and not from the verdict's own `model_id`; construct a
log where the two differ and prove the implementation picks the right one. **Two:**
the double-count question cuts both ways — contributing to both tags is correct,
and it means column totals exceed the item count. Check the function does not
"fix" that by dividing, and check the caller is told so the document can say it.
**Three:** mutate the resumed-judging guard off and confirm something goes red;
that guard is the one with no natural test data.

---

### C9 (amended by R9) — the cell, the refusal, and the two floors

This replaces the C9 section at line 923. The differences from it are R9's second
floor, the item count that feeds it, and the unit carried on the refusal sentence.
Everything else there still holds and the reasoning in it is still the reasoning.

**Files.** `dimensions.py`, `tests/test_dimensions.py`. **Append at EOF**, below
everything. C8 is being written in parallel against the same file and inserts at
the top. Do not reorganise or re-header the module.

**Contract.**

```python
MIN_N_FOR_A_VERDICT: int = 20        # completions
MIN_ITEMS_FOR_A_VERDICT: int = 10    # distinct items -- see R9

@dataclass(frozen=True)
class DimensionCell:
    tag: str
    passes: int
    n: int
    items: int
    rate: float | None
    interval: tuple[float, float] | None
    floor: float | None
    verdict_refused: bool
    needed: int | None          # how many more, in the unit that binds
    needed_unit: str            # "items" | "completions" | "" when not refused
    note: str                   # the refusal sentence, "" when not refused

def dimension_cell(
    tag: str,
    passes: int,
    n: int,
    items: int,
    *,
    confidence: float | None,
    floor: float | None,
    min_n: int = MIN_N_FOR_A_VERDICT,
    min_items: int = MIN_ITEMS_FOR_A_VERDICT,
) -> DimensionCell: ...
```

Takes plain integers, not C8's `TagCount`. That is deliberate: the two chunks are
being written blind against each other and neither may import the other's types.

Calls `opik_rigor.wilson_interval(passes, n, confidence)`. When `n == 0` every
derived field is `None` and it calls nothing — `wilson_interval(0, 0)` raises
`ValueError("a rate over zero runs is not a rate")`, verified at
`tests/test_report.py:1374`, and that is a rendering state rather than a
computation.

**`verdict_refused` is `True` when `n < min_n` or `items < min_items`, regardless
of how the interval sits against the floor.**

**When both floors bind, the note names items.** Not a style preference — it is
the only one the reader can act on. A note that says "you need more completions"
sends someone to raise `n_per_item`, and R9 is the proof that raising `n_per_item`
cannot fix an item shortfall: it multiplies the same four questions. Naming the
completions floor when the item floor also binds is advice that does not work.

The sentence keeps the spec's shape with the unit made honest:
`"10 items needed for a verdict here; you have 4."`

**Edges.**

| Input | Required |
|---|---|
| `n == 0`, `items == 0` | `rate`, `interval` `None`; refused; note says nothing was measured |
| `n == 20`, `items == 4` (4 items x 5 draws) | **refused**, `needed == 6`, `needed_unit == "items"`. This is R9's whole case; if this cell renders a verdict the chunk is wrong |
| `n == 12`, `items == 12` (12 items x 1 draw) | refused, `needed == 8`, `needed_unit == "completions"` |
| `n == 80`, `items == 16` | interval shown, `verdict_refused=False` — the showcase |
| `n == 4`, `passes == 1` | interval computed and shown, refused, both floors named in `needed`/`needed_unit` by the items rule above |
| `confidence is None` | fall back to rigor's `DEFAULT_CONFIDENCE` (0.95) and record that in `note`; never silently |
| `floor is None` | cell renders, `verdict_refused` unaffected — neither sample-size floor depends on the floor |
| `passes > n` | `ValueError` — a corrupt count must not render |
| `items > n` | `ValueError` — more distinct items than completions is impossible and means the caller mispaired two numbers |

**Must not.** Colour, style, or otherwise imply a verdict on a refused cell.
Compare the interval to the floor when `verdict_refused`. Fall back to a default
confidence without saying so. Collapse the two floors into one number.

**Failure mode when wrong.** The spec names it: "Every dashboard in this market
would happily colour that cell red. Declining is the differentiator." A cell that
renders a verdict at four items is not a bug in a chart, it is the product's claim
failing — and R9 exists because the pre-R9 rule rendered a verdict on precisely
the example the spec chose to illustrate declining.

**Test that fails first.**
`test_a_tag_with_four_items_and_twenty_completions_declines_the_verdict`.

**Done.** As C8's Done block. Same R4 warning, same `__file__` proof.

**Reviewer.** Check `verdict_refused` is not short-circuited by a wide interval
that happens to clear the floor — the tempting implementation says "refuse when
the interval is too wide to decide", which is a different and worse rule: it would
answer at n=4 whenever four out of four passed. Then check the two floors are
genuinely independent: mutate `min_items` to 0 and confirm a test goes red, and
mutate `min_n` to 0 and confirm a *different* test goes red. If one mutation kills
both, the floors were collapsed.

---

### C10 (amended) — the decline reasons collapse from two to one and a half

C10 at line 989 stands, with one change forced by R1 and R9.

Its "exactly two causes" of unavailability were: a judged artifact missing, and
the golden set unavailable. R1 deleted the first — there are no artifacts in this
path any more. The causes are now:

- `gs_view["available"]` is `False`. Reason reuses `gs_view["reason"]` verbatim,
  which already explains that pairing today's file with last week's outputs would
  be a fabricated exhibit. Unchanged.
- `dimension_counts` returned `available=False`. Reason is its `reason`, reused
  verbatim, not re-worded. `report.py:645-650` already reasons about why: three
  copies of a disclosure are three chances for one to go stale.

`DimensionMatrix` carries `min_n` and now also `min_items`, because a document
that refuses a cell has to be able to say what it refused against.

The edge "candidate artifact missing -> unavailable" is **deleted**. A
cross-machine render with no artifact directory now produces a full matrix, and
that is the point of R1. The C10 test named at line 1050 must be rewritten to
assert the opposite of what it currently says: a log whose artifacts have been
moved away still renders its matrix.

---

### R10 — C9's four ambiguities, ruled; and one product defect R9 created

C9's tester found four places where its contract was ambiguous enough that the
implementer and the tester could each pick a different reading and neither be
wrong, plus two objections to R9 itself. All six are accepted. The rulings went
to both C9 agents verbatim, mid-flight, in identical words — that is the only way
a blind pair does not diverge on a late correction.

**R10.1 — `note` is not only the refusal sentence.** The field comment says
`# the refusal sentence, "" when not refused`; the edge table says a defaulted
confidence must be recorded in `note`, "never silently". An unrefused cell with
`confidence=None` is both. **"Never silently" wins**: `note` is the cell's
disclosure line, and a defaulted confidence appears there whether or not the cell
is refused. The field comment is wrong.

**R10.2 — at `n == 0`, `needed is None` and `needed_unit == ""`.** "You need 6
more items" implies you have some. At zero nothing was measured and the honest
statement is different in kind, so the note says that and names neither floor as
a shortfall.

**R10.3 — the completions refusal sentence, verbatim:**
`"20 completions needed for a verdict here; you have 12."` It mirrors the items
sentence. C9 gave a verbatim for one and not the other, which is how two agents
end up pinning two different strings.

**R10.4 — `floor` is an echoed input, not a derived field.** Echo it always,
including at `n == 0`. "Every derived field is `None`" does not reach it.

**R10.5 — the n=4 edge row is a slip, and repairing it exposed a real defect.**
That row says "both floors named in `needed`/`needed_unit`", which those singular
fields cannot express. `needed`/`needed_unit` keep the **actionable** floor —
items when both bind, for the reason R9 gives.

But naming only one floor is a product defect, and C9's tester found it: a user
at n=4/items=4 who is told "10 items needed; you have 4" adds six single-draw
items, lands at items=10 and n=10, and **is refused again** on the floor nobody
mentioned — having done exactly what the note asked. For a document whose entire
differentiator is declining honestly, being refused twice for one shortfall is
the worst available second impression.

So **`note` names the other floor too when it also binds**, in two sentences:

```
"10 items needed for a verdict here; you have 4. The 20-completion floor is
also unmet: you have 4."
```

One sentence when only one floor binds.

**R10.6 — R9's justification for ten is circular, and this corrects it.**
"Below ten items a single item is worth more than a tenth of the dimension's
verdict" defines the threshold by restating it. The constraints R9 actually cites
— refuse the spec's 4-item example, clear the showcase's 16 — narrow the number
to the band **5 to 16** and no further. Ten is a **judgement inside that band**,
expressed as a leverage tolerance: no single golden-set item should move a
published dimension claim by more than a tenth.

The number does not change. What changes is that the plan no longer presents a
judgement call as a derivation, which is the kind of dressing-up that survives
into a docstring and then into an argument with someone who checked the
arithmetic.

---

### R11 — four things C8's contract got factually wrong, one process hazard, and a second correction to R9's arithmetic

C8's implementer cross-checked its work against a real 300-record `migkit demo`
log and matched the artifact-derived matrix at **8/8 cells, zero mismatches**,
over both models — the same check R1 ran at 24/24. On the way it found four
errors in the contract I wrote.

**R11.1 — the resumed-judging guard's expected count is wrong, and the literal
reading breaks every normal run.** The contract says the check is
`len(group) != payload["graded"].get(judge, 0)`. But `graded` is incremented for
*every* `JudgeRecord` written (`judging.py:653-659`), including imputed ones and
parse failures, and neither of those emits a `judge.verdict` — an imputed record
returns before `evaluate()` is called, and a parse failure writes
`judge.parse_failure` and raises. The correct expected count is:

```
verdicts in the log = graded - imputed - parse_failures
```

Comparing against raw `graded` declines the whole matrix the moment one
completion fails, which is precisely the case rule 4 exists to handle. **As
written, C8's rule 3 and rule 4 were mutually exclusive.** Use `.get(judge, 0)`
on each of the three so a synthetic log omitting the latter two degrades to the
literal reading.

**R11.2 — that guard cannot detect the resume it is named for.** On a resume
`pending` excludes already-graded records, so `graded` shrinks by exactly the
amount the verdict count shrinks. Resuming into the same log after a crash leaves
earlier partial verdicts with no intervening `judging_completed`, so they merge
into the group and the guard fires on a total that is actually correct; resuming
with a fresh log and a copied-in judged artifact makes both numbers shrink
together and the under-count passes silently. It remains a valid consistency
check on one record. It is not a resume detector, and R1 overstated it. Test it
with hand-built shortfall data and say so in the docstring.

**R11.3 — the class is `GoldenItem`, in `contracts.py:106`.** There is no `Item`.
Both C8's contract and R1 name a type that does not exist.

**R11.4 — `judge.verdict` has no usable `EVENT_*` constant.** `contracts.py`
deliberately names only the `migkit.` events, and `EVENT_JUDGE_VERDICT` lives in
`opik_rigor.evidence` and is not in `opik_rigor.__all__`. Importing it would put a
private rigor name in `COMPATIBILITY.md`. Type the literal as a private module
constant with a comment saying why. So "use the constants, do not type the
strings" holds for the two `migkit.` events and cannot hold for this one.

**R11.5 — a process hazard, and it is new.** Two chunks appending to one *new*
module can each define `__all__`, and the second binding silently wins: the first
chunk's names vanish from the export list with no error and **no git conflict**.
Giving C8 the top of the file and C9 the EOF made the merge mechanical for
everything except this. When two chunks share a new module, the orchestrator
creates `__all__` in the skeleton or checks it by hand at merge — a conflict
marker is the thing that makes a merge safe, and this collision does not produce
one.

**R11.6 — the two floors overlap far more than R9 implies.** C9's implementer did
the arithmetic R9 did not: ten items at `n_per_item >= 2` is already 20
completions, so `MIN_N_FOR_A_VERDICT` **can only bind when `n_per_item == 1`**,
or on a ragged tag whose items were not all sampled the same number of times.

Its remaining job — "one draw per item is not enough" — is real, and the floors
are still floors on different quantities, so R9's shape stands. But R9 presents
the two as doing comparable work and they do not, and the both-bind case R10.5 is
built around is reachable only at one draw per item. **This is the second time
R9's arithmetic has needed correcting**, which is worth noticing: the ruling was
made quickly, and both errors were found by agents who checked rather than
assumed.

**Deferred to C9's review, not ruled here.** C9's implementer argues that
`needed` + `needed_unit` is the wrong shape — a stringly-typed unit beside a
single number can express only one floor, so the moment both bind the second fact
has to be smuggled into prose, and R10.5 is exactly that patch. It proposes
`needed_items: int | None` and `needed_completions: int | None`, which would carry
both at the type level, delete `needed_unit`, and make the note derivable from
fields rather than being the only place a fact lives. The argument is good and I
have not taken it, because review is where renames belong and both C9 agents have
already written against the current signature. **C9's reviewer decides.**
