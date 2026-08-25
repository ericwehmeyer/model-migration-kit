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
rigor, `opik-rigor/src/opik_rigor/evidence.py:89`, by `datetime.now(timezone.utc)`.

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
opens a point; **every** `migkit.verdict` record updates the most recently opened
point, overwriting a value already there. A comparison with no verdict after it
yields a point with `verdict is None`. Points are returned in **log order**, not
sorted.

> **Amended 2026-08-21, by C19.** This paragraph and Edges row five below used to
> say different things: the prose gave a verdict to the comparison it follows,
> the row paired first-in-first-out. C2's implementer shipped the row, and that
> is where the defect came from — so both now state C19's rule rather than one
> being deleted. The reasoning is in
> [C19](#c19--the-verdict-belongs-to-the-comparison-before-it): there is exactly
> one writer of these two events, `comparison.py:907-908`, which makes
> first-in-first-out right only on a log this pipeline cannot write and wrong on
> the log a crash produces.

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
| two comparisons then two verdicts | both verdicts land on the **second** point — it ends up carrying the second verdict, and the first point's `verdict is None`. Amended by C19; see the note above. Comparison-then-verdict adjacency is what `compare` writes (`comparison.py:907-908`) and must not be assumed to be the *only* interleaving. |
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

> **AMENDED — read R14.2, R14.3 and R18.4 before this section.** The signature
> below is superseded: `partition_comparable` returns a **three-field
> `Partition` NamedTuple** (`kept`, `excluded`, `caveats`), and the type named
> `Flag` below is now **`Caveat`** — it collided with `enum.Flag` in two
> rendering chunks. The coverage flag reads `judged_baseline` /
> `judged_candidate`, **not `records`**, which `RunPoint` does not carry, and
> compares the two *sides of one comparison*, not two runs. The edge table below
> is a **floor, not a ceiling**: review added exclusions for both sides graded
> zero, for one side graded zero, for `n_per_item == 0` and for an unrecorded
> `baseline_model`, plus a caveat for self-comparison.

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

> **AMENDED — read R20 before this section.** `CandidateField` gains an eighth
> field, `stale_after_days: float`, recording the window the field was built
> with. Without it a renderer prints "measured more than 7 days apart" beside a
> field built with `stale_after_days=30.0`, which is a lie assembled from two
> true halves. The test asserting the field set is exactly seven names changes
> with it. R20 also carries nine defects the review found and how each was
> ruled.

> **AMENDED — read R17.2 through R17.5 before this section.** Four corrections.
> (1) `Candidate.delta_pp` and `CandidateField.baseline_pass_rate` need a
> baseline pass rate, and **`RunPoint` has no such field** — its `pass_rate` is
> the *candidate* side. Reconstruct it exactly as
> `(judged_baseline - judge_failures_baseline) / judged_baseline`, `None` when
> the denominator is zero. (2) `CandidateField` gains `caveats` beside
> `excluded`, following C4's `Partition`. (3) "ignoring `candidate_model`" is
> stale — `ComparabilityKey` never contained it. (4) The tie-break must be
> **total**: largest group, then newest point, then the key in sorted order, or
> the document differs between two renders of one log.

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

> **AMENDED — read R17.1 before this section, and do not implement `changed` as
> written below.** Holm **steps down**: once a test fails to reject, nothing
> larger is rejected either, *regardless of its own threshold*. For every
> candidate after the stop, `holm_bonferroni` returns the uncorrected `alpha` as
> the threshold, so the rule `p_value >= holm_threshold` goes **vacuously
> false** and the candidate silently drops out of `changed`. It misses the
> largest sub-alpha p-value in every family — in the one set whose purpose is to
> make the correction's effect visible. Correct rule: **`p_value < alpha and not
> rejected`**, taking `rejected` from `holm_bonferroni`'s own return. Never
> compare a p-value to the returned threshold to decide significance. **The
> named first test below passes against the broken rule**; it needs a second
> assertion.

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

> **AMENDED — R15 replaces `trend`'s signature and return type. Read it first.**
> `trend` no longer filters by a single `candidate_model` — filtering on the
> field that moves is what made the change invisible. It takes a
> **caller-declared `candidate_models` lineage** (never inferred: stripping a
> version suffix is forbidden), partitions through C4's `partition_comparable`,
> and returns a **`Trend` NamedTuple** (`points`, `successions`, `excluded`,
> `undated`). `parameter_strip` below is **unchanged and needs no change** — it
> was always able to show the model change and was prevented by its own caller.

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
`after=<value>`, `changed=False` — the first run changed nothing because there
was nothing to change from.

> **AMENDED 2026-08-24: `before` is NOT `""` on a first run.** This clause said
> so twice, and C7's blind tester asserted it literally and then argued against
> the contract it had just tested. It was right.
>
> A blank cell reading as "unchanged" is the failure mode this chunk exists to
> prevent — and this is not a general tension, it is *the specific rendering R15
> was written about*: "the `model_id` row reports `changed=False` with an empty
> `before`." The defence that on a first run the blank is *true* does not save
> it, because **when the series was wrongly split `previous` was `None` too**, so
> all six rows were blank in that case as well. A first run and a wrongly-split
> series render identically — six blanks, six `changed=False` — and a reader
> cannot tell them apart. That indistinguishability is the bug R15 exists to
> kill, sitting at the top of every line instead of the middle of one.
>
> R15 makes the split much less likely, since the lineage is now declared and a
> split requires the operator to declare it wrong. That is precisely the case
> where a reader most needs to notice and can least afford a silent blank.
>
> **Ruling: a first-run row must be visibly distinguishable from an unchanged
> row.** `before` renders a distinct marker behind a named constant in the
> `_UNRECORDED` style, never an inline literal — `series.py` already sets that
> precedent deliberately. And the marker must not be the word `"unrecorded"`:
> "there was no previous run" and "the value was not recorded" are **different
> absences** and must not print the same word. `changed` stays `False`;
> `ParameterChange` stays at exactly four fields.

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

> **AMENDED — the signature below is stale; read R26.4 first.** `spot_check`
> now takes a **required** keyword-only `subject: SpotCheckSubject`
> (`judge: str`, `side: str`), and `SpotCheck` carries it as a seventh field.
> The sentence names it: *"A 12-prompt spot check **of the candidate under
> judge accuracy**, drawn at random from these 96 items…"*. `side` is validated
> against `{"baseline", "candidate"}`; an unnamed judge is rendered in words
> rather than as a gap. Everything else below — the hypergeometric, the
> unstable-counts-as-passing choice, and every `None` case — is unchanged.
>
> Two measured facts that belong with it. **At the bundled demo's defaults the
> golden set is 12 items and `k` defaults to 12, so `items <= k` and
> `spot_check` correctly returns `None` on both sides** — the demo renders no
> spot-check sentence however well this is wired, and that absence is correct
> behaviour, not a bug to chase (R26.5). And R23.1's claim that
> `ReportModel.item_counts` carries `passing`/`failing`/`unstable` at its top
> level is **wrong** — they are two levels down, per judge and per side.

> **AMENDED — read R14.1, R18.1, R18.2 and R18.3 before this section.** Four
> corrections, one of which reverses this contract's own argument.
> (1) The probability for the `88/8/0, k=12` row is **0.32877**, not `0.351`;
> `0.351` is `(88/96) ** 12`, the with-replacement answer this contract's own
> "Must not" forbids twelve lines below it.
> (2) **The claim that counting unstable items as passing means "the tool never
> inflates its own case" is FALSE.** Folding them into passing gives P=0.3288;
> counting them as failures gives P=0.2106. Higher P means a blinder spot check,
> which is a *stronger* argument for this harness. The rule is right for a
> different reason — the tool does not claim regressions it has not established
> — and its effect on the quoted number runs the other way. Say both.
> (3) "Understates by roughly an order of magnitude" describes a different error
> than the one committed; the with-replacement form **overstates, by 7%**.
> (4) The sentence names how many items failed and ends "of such checks", and
> **`N == k` returns `None`** — a draw taking every item is a census, not a spot
> check.

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
> failures at all in 33% of spot checks.   <!-- corrected by R14.1: was "34% of cases" -->

**Edges.**

| Input | Required |
|---|---|
| `passing=96, failing=0, unstable=0` | `None` |
| `passing=8, failing=1, unstable=0, k=12` | `None` (N=9 < 12) |
| `passing=88, failing=8, unstable=0, k=12` | probability = 0.32877 (**corrected by R14.1**; the plan long read `≈ 0.351`, which is `(88/96) ** 12`, the with-replacement answer this contract's own "Must not" forbids) |
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

> **BLOCKED — read R21 before this section, and do not dispatch the remaining
> seven elements against it.** Six of the nine elements are gated on values
> `ReportModel` does not carry and no chunk was ever assigned to put there. The
> template cannot reach them, and C14's own Must-not forbids inventing the
> reference. **C22 closes this and must land first.**

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
   (`opik-rigor/src/opik_rigor/adapters/fake.py:47`), not only a prompt→response mapping. A
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

> **AMENDED — C17 owes a showcase rubric, and the demo's rubric is actively
> wrong for the showcase set. Counted, not inferred:**
>
> | | demo golden set | showcase golden set |
> |---|---|---|
> | items | 12 | 96 |
> | reference-less | 4 | 32 |
> | their primary tags | **refusal ×4** | **refusal ×16, summarisation ×16** |
>
> The demo's judge grades every reference-less item by "did it decline". On the
> demo set that is correct for all four, so **the shipped 0.1.1 demo is not
> affected** — worth stating plainly, because "the judge is wrong" invites that
> question first. On the showcase set it is correct for exactly half and
> **inverted for the other half**: every summarisation item would score 1 for
> declining to summarise, `#summarisation` would read ~0% on all fourteen
> nights, and the document would be silently, plausibly wrong.
>
> C16's implementer wrote a judge that splits on the item's primary tag. It is
> uncontracted and load-bearing. **C17 must ship `showcase.toml` and a rubric
> describing that split** — `demo_rubric.md` describes the decline-based
> grading, so shipping it unchanged hashes a rubric into the provenance footer
> that does not match the judge that ran. A provenance footer attesting to the
> wrong rubric is worse than none.

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
>
> *(Corrected by R14.1: the true value is 33%, not 34%. The number is left as the
> spec wrote it here because this paragraph is quoting the spec in order to argue
> with its noun; the arithmetic is wrong too, and both are wrong for the same
> reason — see R14.1's addendum.)*

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

### R8 — CLOSED by C19: the headline verdict and `series[-1].verdict` can disagree

**Closed. This header read "OPEN" for three days after it stopped being true**,
and `RESTART.md` and this plan disagreed about it the whole time — RESTART said
closed, this said open, and nothing reconciled them until 2026-08-24, when the
stale half got repeated into R15 before being caught. Recorded rather than
quietly fixed, because two documents disagreeing about whether a question is
settled is worse than either answer.

C19 replaced FIFO pairing with "the verdict belongs to the comparison before it"
— `SeriesBuilder.add` now updates *the most recently opened point*. Re-run R8's
own counterexample under that rule: given `C1 C2 V1`, `V1` attaches to `C2`, so
`series[-1].verdict` is `V1` and the headline is `V1`. They agree. The shape that
produced the disagreement can no longer produce it.

The original entry follows, unedited, because the reasoning in it is still the
reasoning that justified C19.

Raised by C3's implementer and recorded here so the reviewer arrives at it with
the evidence rather than rediscovering it.

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
is `gs_view["by_id"]`, id -> `GoldenItem` (`contracts.py:106`). There is no
`Item`; R11.3 records the error this sentence used to carry.

**The join, and why it is by input text.** `judge.verdict` carries no `item_id`.
It carries `input`, which `judging.py:744` passes through verbatim from the golden
set, so an item is recovered by inverting `items` on `Item.input`. R1 verified
this on a real 300-record log at 120/120 joined and the resulting matrix at 24/24
cells identical to the artifact-derived one.

**The side comes from ordering, not from the record.** The `model_id` on a
`judge.verdict` is the **judge's** model, not the candidate's — read
`opik-rigor/src/opik_rigor/judge.py:315-329` before you write a line of this, because using it is the single
-- it is in the *dependency*, not this package, and R12.1 records that this
sentence named that file as though it were local --
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

---

### R12 — what C8's and C9's reviews changed, and the errors they found in my contracts

**R12.1 — a fifth factual error, which R11 missed.** C8's contract told the
implementer to read judge&#46;py, lines 318-328, before writing a line of the
chunk. There is no such file in `model_migration_kit`. The verdict is emitted at **`opik-rigor/src/opik_rigor/judge.py:315-329`**
— a different package. Same class as R11.3's `Item`/`GoldenItem`, and this one is
in the sentence telling the implementer that this is the single most likely way to
get the chunk wrong. Both C8 agents inherited it into their docstrings.

Off-by-one line references, all confirmed one line from the truth: `cli.py:521`
(the loop is at 522, and `demo.py:606` is a second path the contract never
mentions); `judging.py:653-659` (it is 652-658). Everything else checks out
exactly.

**R12.2 — R1 overstates the ordering guarantee.** "The ordering is structural" is
true only at the *group boundary*. Within a group, `judge.verdict` records are
appended from worker threads inside `evaluate()`, so their log order is completion
order, not `pending` order — `_graded_in_order` restores order downstream of the
evidence append, not upstream. C8 does not rely on intra-group order so nothing
breaks, but the next chunk to read that sentence needs the narrower version.

**R12.3 — R1's selling point is now false.** It says building from the log
"collapses the two independent decline reasons to one." There are **six**.

**R12.4 — a wrong-answer defect neither blind half could see.** A log that stops
*after* a failed completion but *before* that model's judging pass published a
full, plausible matrix in which the truncated side read as a model that got
everything wrong. That is C8's own named worst failure reached by a road the
contract did not map: the open-verdicts guard covered exactly this shape for rule
1 and had no counterpart for rule 4. A sixth guard now refuses a model known only
from failed completions. Reachable whenever `migkit run` writes both sides'
completions and `compare` finishes judging only one.

**R12.5 — R11.5's hazard list needs two more entries, both found the hard way.**

- **Ruff `I001` fires on the *merged* file's import block**, which neither pair
  can see from its own side. Ruff-check the merged tree, not either half.
- **Two blind halves collide on top-level helper names, silently.** C8 and C9 both
  defined `_cell` in `tests/test_dimensions.py`; the later shadowed the earlier
  and produced 27 failures with **no conflict marker**. Before committing a merge
  of two chunks into one file, walk the module's top-level `def`/`class`/assign
  names and refuse duplicates. R11.5 predicted this for `__all__` and the same
  mechanism applies to every top-level name.

**R12.6 — the merge check is three gates, not one.** C9 as merged was 40 tests
green and **two CI steps red** — `dependency_surface.py --check` and `ruff`. C8
had the identical `dependency_surface` failure for the identical structural
reason: the *tester's* file imports a rigor name, and the implementer, writing
blind, cannot add a `COMPATIBILITY.md` row for a file it never saw. Neither half
can prevent it and neither is at fault. **Run ruff, `dependency_surface --check`
and pytest before calling a merge green.**

**R12.7 — a policy edge needing confirmation.** C9 introduces the first
`from opik_rigor.<submodule>` import in `src/`, falsifying `COMPATIBILITY.md`'s
claim that `grep -rn "from opik_rigor\." src` returns nothing.
`DEFAULT_CONFIDENCE` is in `distribution.__all__`, so it is rigor's public
surface and invariant 1 holds, but the root does not re-export it. It is recorded
as a listed exception. The cheaper fix is upstream: rigor re-exporting the
constant beside the function that defaults to it. **Not done, because it would
require a rigor release and migkit pins `>=0.2,<0.3`.**

---

### C10 (restated) — wire the matrix into `ReportModel`, with six ways to be unavailable

> **AMENDED — read R16 before this section. The call this contract prescribes
> cannot be made.** `dimension_counts(records, items, *, judge)` needs the
> records and the golden set at the same moment, and the golden set is named on
> a record that arrives last. **That is the C10 blocker.** Use C21's two-phase
> form instead: construct `DimensionTally()` with no golden set, `add(record)`
> through the single streaming pass, then `counts(items, judge=judge)` once the
> comparison record resolves it. The `__all__` quoted below is stale — it has
> since gained `DimensionTally`. And `dimensions: DimensionMatrix` **replaces**
> `ReportModel.dimension_counts`; it does not sit beside it.
>
> **Three more, from C21's review (2026-08-24):**
>
> 1. The helper is now **`report._close_the_tally`**, not `_dimension_counts` —
>    it never wrapped `dimension_counts`, it calls `tally.counts()`. Already
>    merged; this is the name to type.
> 2. **Do not ship `baseline` and `candidates` as `Mapping`s.** They reproduce
>    *exactly* the `column.items` hazard this contract's own Reviewer note tells
>    the reviewer to check for: `baseline.items` is `dict.items` and renders as a
>    bound method. C21 documented the hazard on `DimensionCounts.by_model` and
>    could not fix it there; C10 can, and if C10 ships mappings the hazard
>    survives the very chunk that was told to look for it. Use
>    `tuple[DimensionCell, ...]` — each cell already carries its `tag` — or a
>    small frozen `TagColumn`.
> 3. **Re-point C21's seven wiring tests at the new field; do not delete them.**
>    They exist because deleting `tally.add(record)` from the loop once left the
>    entire suite green, and a mutant that swapped `judges[0]` for `judges[-1]`
>    survived until C21's fix pass. `DimensionMatrix` already carries `judge:
>    str`, which fixes the missing-judge defect for free — `DimensionCounts` is
>    a per-judge table with the judge erased.

This replaces the C10 section at line 989 and the `### C10 (amended)` note after
R9. Read R1, this, and nothing else in the plan.

**Files.** `report.py` (`ReportModel`, `from_evidence`). `tests/test_report.py`.

**What you are consuming is merged, reviewed and mutation-tested.** Read it
rather than the plan's description of it —
`src/model_migration_kit/dimensions.py`, whose public surface is exactly:

```python
__all__ = ["DimensionCell", "DimensionCounts", "MIN_ITEMS_FOR_A_VERDICT",
           "MIN_N_FOR_A_VERDICT", "TagCount", "UNTAGGED",
           "dimension_cell", "dimension_counts"]

dimension_counts(records: Iterable[EvidenceRecord],
                 items: Mapping[str, GoldenItem], *, judge: str) -> DimensionCounts
dimension_cell(tag: str, passes: int, n: int, items: int, *,
               confidence: float | None, floor: float | None,
               min_n: int = 20, min_items: int = 10) -> DimensionCell

DimensionCounts(available: bool, reason: str,
                by_model: Mapping[str, Mapping[str, TagCount]])
TagCount(passes: int, n: int, items: int)
```

**Contract.**

```python
@dataclass(frozen=True)
class DimensionMatrix:
    available: bool
    reason: str                                            # "" when available
    judge: str
    tags: tuple[str, ...]           # golden-set tag order, UNTAGGED last
    baseline: Mapping[str, DimensionCell]
    candidates: Mapping[str, Mapping[str, DimensionCell]]  # model_id -> tag -> cell
    min_n: int
    min_items: int
```

`ReportModel` gains `dimensions: DimensionMatrix`, built in `from_evidence`.

**`min_items` is not decoration.** A document that refuses a cell has to be able
to say what it refused against, and R9 gave it two floors to refuse against.

**Unavailability has six causes, not two.** R1 claimed building from the log
"collapses the two independent decline reasons to one"; R12.3 records that this is
false. The golden set can be unavailable, and `dimension_counts` can decline for
five distinct reasons of its own. **Reuse `gs_view["reason"]` and
`DimensionCounts.reason` verbatim — never re-word either.** `report.py:645-650`
already reasons about why: three copies of a disclosure are three chances for one
to go stale.

**Must not.** Fabricate cells from `item_counts` when the matrix declines —
`item_counts` is aggregate and splitting it across tags by any rule is invention.
Read a file; `from_evidence` already resolved every path it may read. Render a
partial matrix: `DimensionCounts` guarantees `by_model` is empty on a refusal, and
that guarantee exists so that a caller cannot be tempted. Type `""` inline for the
untagged key — **import `UNTAGGED`**, which is exported for exactly this reason and
carries the comment explaining why the sentinel is empty rather than the word
`"untagged"` (a set using `"untagged"` as a real tag would collide and read as a
larger slice).

**Edges.**

| Input | Required |
|---|---|
| both sides judged, hash matches | `available=True`, one column per side |
| **artifacts moved away from the log** | `available=True` and a full matrix. **This inverts the original contract's named test.** R1's whole point is that the matrix survives the cross-machine re-render that `report.py`'s docstring calls the designed workflow |
| golden set hash mismatch | `available=False`, reason is `gs_view["reason"]` verbatim |
| `dimension_counts` declines | `available=False`, reason is its `reason` verbatim |
| golden set present, every item untagged | `available=True`, one row, key `UNTAGGED`. **Not** unavailable — "you tagged nothing" is a different fact from "the file is gone" |
| a side judged that produced nothing | a column of zeros, never a missing column |
| a log with no series | matrix still built from the headline run |

**Which model is the baseline** comes from the comparison payload, not from
position in `by_model`. `dimension_counts` keys by `model_id` and does not know
which side is which.

**Failure mode when wrong.** A crash on the ordinary cross-machine render — a
reviewer opening a shared log with no artifact directory, which is the designed
workflow — or a matrix built from a golden set whose hash no longer matches, which
is the fabricated exhibit `_load_goldenset` exists to prevent.

**Test that fails first.**
`test_the_dimension_matrix_still_renders_when_the_artifacts_are_not_beside_the_log`
— render a log whose artifacts have been moved away and assert `available is True`
with a populated matrix. The original contract's test asserted the opposite; that
is what R1 changed.

**Done.** Full report suite plus `tests/test_stranger_path.py`, which is where the
moved-log case already lives. **Three gates: ruff, `dependency_surface --check`,
pytest.** See R12.6.

**Reviewer.** The most likely subtle wrong is treating "no tags in the set" as
unavailable. Second: reason strings re-worded rather than reused. Third, and new:
`column.items` and `cell.items` are one keystroke apart and **both work** — one is
a `dict.items` bound method, the other an int. `report.py:1206` already renamed a
field to avoid exactly this, and here the mapping is the thing that cannot be
renamed away, so check every call site by hand.

---

### R13 — C16 leaves one question open and answers another one wrongly

Both are settled here, before dispatch, because either would have sent the
implementer and the tester down different roads with neither of them wrong.

**R13.1 — the file is `scripts/showcase.py`.** C16's Files line says "New
`src/model_migration_kit/showcase.py`, **or** `scripts/`" and leaves it there.

`scripts/`, for two reasons and one precedent. The showcase generator is a
build-time tool: it seeds 56 runs so a document can be published, and no user of
the library ever calls it. Putting it in `src/` would add it to the shipped API
and give it a row in `COMPATIBILITY.md`'s rigor-surface table for imports nobody
outside this repo can reach. And `scripts/make_showcase_goldenset.py` — which
generates the golden set this chunk drives — already lives there, so the pair
that produces the showcase stays together.

Testability is not a reason to move it. `tests/test_release_checks.py:30` already
imports a script with `importlib.util.spec_from_file_location`, so
`tests/test_showcase.py` has a working pattern to copy.

**R13.2 — the callable form is not required, and the contract's reason for it is
already disproven.** C16 says night 6's REVIEW "requires per-draw variation,
hence the callable form."

It does not. The de-risk check recorded in `RESTART.md` found the opposite, on a
real run: *"A genuine REVIEW is seedable with a plain `Mapping` FakeAdapter — no
callable, no per-draw variation. The REVIEW band at n=200 is seven completions
wide. All three verdicts come from one parameter set with only the candidate's
script differing, so the comparability guard is never approached."*

This matters more than a simplification. C16's own Reviewer note says *"The
per-draw counter is state, and state plus a thread pool is a flake."* The
contract creates the hazard in its Contract section and then asks the reviewer to
police it — when the hazard is not needed at all. **Default to a plain `Mapping`.
No counter, no state, and the flake the reviewer was told to hunt cannot exist.**

Two things carried over from the same de-risk note: **avoid `k = 180` exactly**,
where `runs_needed` returns `None`, and **rule 4 is a trap for a timeline** — a
band that is REVIEW for power rather than for the floor reads as the same colour
and is a different fact.

The measurement behind this was taken at n=200, and the showcase runs a different
n. **Verify the REVIEW band is reachable at the showcase's own n before
building.** If a plain `Mapping` genuinely cannot reach it there, say so loudly
and stop — do not reach for a stateful adapter on your own authority. That would
be a real finding and it changes the chunk.

**Determinism is unaffected either way** and remains the contract:
`showcase_adapters(gs, night=6)` called twice must yield byte-identical artifacts
through `run_goldenset` at `concurrency=1`. A stateless adapter makes that
cheaper to hold, not harder.

---

### C14a — the two charts that exist, and the evidence made legible

C14 lists nine elements and **seven of them depend on chunks that are unbuilt or
in flight** — the spot-check sentence (C11), the candidate table (C5), the
multiplicity note (C6), the excluded-runs list (C4), the dimension matrix (C10,
blocked), and the parameter strip (C7). Waiting for all of them means the
document shows none of the work, which is where it stands today: `timeline_svg`
and `interval_bar_svg` are merged, reviewed and mutation-tested, and **nothing
calls them**.

This chunk renders the two that are ready and fixes what a reader actually
complained about. C14 keeps the other seven.

**Files.** `report.py` — `_TEMPLATE`, `_CHANGES_MACRO`, `_environment`.
`tests/test_report.py`. **Not** `from_evidence`, **not** `ReportModel`, **not**
`dimensions.py`: another chunk is inside those right now.

**In scope, and nothing else.**

| Element | `id` | Present when |
|---|---|---|
| verdict banner gains an inline interval bar | `verdict` | always |
| timeline | `timeline` | `len(model.series) >= 1` |

Leave the other seven `id`s unused. A later chunk claims them, and a placeholder
that says "coming soon" is worse than an absence — it is a promise in a document
whose whole claim is that it does not promise what it cannot show.

**The rendering defects, which are the other half of this chunk.**

**1. Identical draws are printed once, not N times.** Today `extract-01` prints
`98.10` five times and `refuse-02` prints the same 260-character paragraph five
times. Repetition is not evidence. When every draw of a side is byte-identical,
print the text once above a line reading `all 5 draws identical`. When they
differ, print each and say how many differed — **that** is the fact a reader
needs, and today it is invisible because uniformity and variation look the same.

**The completeness claim must survive this and must not quietly change meaning.**
`report.py`'s detail-budget sentence currently says every changed item "carries
its full outputs: 5,821 characters ... against a budget of 10,000,000". Collapsing
identical draws changes the character count. The sentence must keep counting what
was *produced*, not what was *printed*, or it becomes a completeness claim about a
smaller thing — which is the R5 failure ("missing data stated as zero") in a new
coat. State both numbers if that is what it takes.

**2. The finding is not hidden behind a closed disclosure triangle.** The most
important result of the demo run is that the candidate writes a fabricated
data-breach notice on request and invents a refund figure the source thread never
states. Both sit inside `<details>` that render closed. A reader who does not
click sees a NO-GO and a p-value.

Flips are the point of the document. Open them by default, or lift the judge's
reason and a short quote to the summary line so the closed state still carries
the finding. **Do not** open gains by default — they are context, not the finding,
and the document already argues that netting the two is how a bad migration
ships.

**3. Paths are shown once, in full, where they can be checked.** The demo report
prints an absolute temp path eight times, each about 130 characters, in a document
whose readable width is 60rem. Provenance and "What was compared" are where a path
belongs at full length. Elsewhere the basename is enough, and the full path stays
in `title=` — which is not in `FETCHING_ATTRS` and is not dereferenced.

**4. A row that can never say anything is not printed.** Latency on a run whose
adapters are scripted reads `0.000 / 0.000`. `RunSummary.is_fake` already knows.
Suppress the table and say why in one line, rather than printing two zeros that a
reader has to work out are meaningless.

**Must not.** Add `<script>`, `<link>`, a web font, or any attribute in
`FETCHING_ATTRS`. Add a `| safe` anywhere except the single point each SVG helper
is injected — the helpers return trusted markup, everything derived from model
output stays escaped, and a test asserts the set of `| safe` filters equals the
known injection points by name. Work around `StrictUndefined`: a `{{ }}` reference
to a field `ReportModel` does not define must raise, which is the designed
behaviour. Reference `model.dimensions` — it does not exist yet.

**Failure mode when wrong.** `assert_self_contained` runs inside `render_html`
before the file is written, so an external reference fails the render rather than
shipping. The failure that *does* ship is a `| safe` on a path that can carry
model output, which turns an escaped `<img src="https://tracker/x.png">` in a
completion into a real fetch.

**Test that fails first.**
`test_the_document_marks_exactly_one_expression_safe_per_hand_rolled_svg_and_no_others`
— parse `_TEMPLATE + _CHANGES_MACRO`, assert the set of `| safe` filters equals
the known SVG injection points by name.

**Then.** `test_the_rendered_report_has_no_external_url` and
`test_the_rendered_report_has_zero_script_and_zero_link_elements` must pass
unchanged against a fixture exercising both new sections.

**Reviewer.** Three things. **One:** the identical-draws collapse must not weaken
the completeness claim — check the sentence says what it counts. **Two:** C20 has
just narrowed the self-containment scanner, and its reviewer found that SVG
presentation attributes taking a CSS `<url>` (`fill`, `filter`, `mask`,
`clip-path`, `marker-end`, `cursor`) are invisible to it. This chunk injects
inline SVG into the document for the first time, so `fill="url(...)"` is now
reachable in a way it was not before. Check what the two helpers actually emit.
**Three:** mutate each `| safe` off and confirm something goes red.

---

### R14 — six contract defects found before dispatch, and the rulings that settled them

Five of these were found by reading contracts against the code they name, in the
pre-dispatch pass §"When it is safe to go wide" asks for. One came back from
C16's implementer. All six would have split an implementer/tester pair, which is
the specific failure R6 and R7 exist to prevent: two agents each pick a different
reading of the same sentence and neither is wrong.

#### R14.1 — C11's probability is stated twice and both statements are wrong

C11's edge table says `passing=88, failing=8, unstable=0, k=12` gives
"probability ≈ 0.351". The worked example sentence, and §7.4's version of it,
both say "34%". These are the same scenario — 96 items, 8 failing, k=12 — so the
plan gives two different answers to one question, and neither is right:

```
comb(96 - 8, 12) / comb(96, 12) = 0.3287693171387045
```

**Ruling: 0.32877, which is 33%.** Both printed numbers are struck. An
implementer would have coded the formula and got 0.32877; a tester reading the
edge table would have asserted 0.351 and filed a bug against correct code. The
formula was always right; only the arithmetic done on it was wrong.

**Addendum, from C11's tester, which is the better half of this finding.** 0.351
is not a typo and not a rounding:

```
(88 / 96) ** 12 == 0.3519956280141369
```

It is the **with-replacement** answer — the independent-draws error that C11's
own "Must not" forbids twelve lines further down the same contract, and that
§7.4 spends three paragraphs explaining is the specific mistake a reviewer would
find. The contract committed the error it was written to prevent, and then
printed the result as the expected value a tester would assert against.

Two things follow. First, §7.4's worked example needs correcting too, not just
C11's edge table — the wrong number propagated. Second, and worth more: a
"must not" is not self-enforcing. This plan's own edge tables are the place to
check whether its prohibitions were obeyed, because an expected value computed
the forbidden way looks exactly like an expected value computed the right way.

#### R14.2 — C4's flag has nowhere to go

C4's edge table requires that a point whose key matches but whose per-side
coverage differs is **kept**, "and the sentence is a flag on the kept point, not
an exclusion". But `partition_comparable` returns `(kept, excluded)` and
`RunPoint` is frozen with no flag field. There is no third place.

**Ruling: `partition_comparable` returns a three-tuple**, and a `Flag`
dataclass parallel to `Exclusion`:

```python
@dataclass(frozen=True)
class Flag:
    point: RunPoint
    reason: str

def partition_comparable(
    points: Sequence[RunPoint], *, against: ComparabilityKey
) -> tuple[tuple[RunPoint, ...], tuple[Exclusion, ...], tuple[Flag, ...]]: ...
```

> **SUPERSEDED by R18.4.** `Flag` is now **`Caveat`** — it collided with
> `enum.Flag` in two rendering chunks — and the bare three-tuple is now a
> **`Partition` NamedTuple** (`kept`, `excluded`, `caveats`). The reasoning below
> is unchanged and still correct; only the two names are.

A flagged point is **also** in `kept` — a flag annotates, it does not remove. The
empty case is `((), (), ())`. C5's `CandidateField` will need a `flags` field to
match; it is not yet dispatched, so this is still cheap.

#### R14.3 — C4 flags on a field that does not exist

§4.4 says "`records` is recorded per side and is the available proxy". That is
true of the payload. It is **not** true of `RunPoint`, which has no `records`
field — enumerated on main, not read off this plan. Adding one means editing the
producer (C1/C2) while its consumers are in flight, which §"When it is safe to go
wide" rule 2 forbids.

**Ruling: the flag reads `judged_baseline` / `judged_candidate`.** These are not
interchangeable with `records` and the difference must survive into the wording:
they count completions the judge **graded**, not completions produced. A
completion produced but whose judge reply would not parse is counted by neither.
So the flag sentence says *graded*. Saying "completions" here re-commits the
exact conflation `RunPoint.judged_baseline`'s docstring exists to prevent.

#### R14.4 — "byte-identical artifacts" is unachievable for any adapter

C16's contract asks for byte-identical artifacts across runs. `RunHeader.created`
is `utc_now()` and `Completion.duration` is a wall-clock measurement inside
rigor's `sample`. No adapter can satisfy this; the demo has the same property. A
tester taking it literally writes a red test against correct code.

**Ruling: the testable form is two-part** — the projection
`(item_id, sample_index, output, error)` over all completions is identical
between runs, **and** `created` and `duration` are the only keys that differ
anywhere in the artifact. The second half is the stronger claim and the one worth
writing: a projection-only test would not notice a third source of
nondeterminism appearing later.

#### R14.5 — §7.3's blind-testable property is the wrong noun

"night 14's `#refusal` completions for candidate B are strictly fewer than night
13's". The completions are 85 on both nights — everything gets graded. What
drops is **passing** completions, 85 to 5.

**Ruling: assert passing completions.** Related: the collapse takes the 16 items
whose *primary* tag is `refusal`, not all 17 tagged ones — `synthetic-summarise-09`
borrows the tag and keeps passing — so the floor is 5/85, not 0/85. A test
asserting the dimension goes to zero is wrong, and the reason it is 5 is worth
pinning: it puts the golden set's two-tag arithmetic on display.

#### R14.6 — C7's `trend` and C16's night-14 story cannot both be satisfied

C16 requires night 14 to produce exactly one `changed=True` parameter row, and
with n, items, judges, golden set and config all held, the only tracked
parameter that *can* change is `model_id`. But `trend(points, *, baseline_model,
candidate_model)` filters the series **by** `candidate_model`, so nights 1–13
(`-b-v1`) and night 14 (`-b-v2`) can never appear in one series. The strip can
never see the change it exists to show, and candidate B's timeline splits 13+1.

**Not yet ruled.** C7 is unimplemented, so it is still the cheap side to move,
and C16's reading (the id changes) is implemented. Settle before C7 is
dispatched. This is the second time an identity field has forced a choice
between "the series is one line" and "the change is visible"; R8 is the first,
and it is still open.

#### And one that is not a wording defect

C10's contract tells the implementer to consume
`dimension_counts(records, items, *, judge)`. That call cannot be made inside
`from_evidence`'s single pass: it needs the records and the golden set at the
same moment, and the golden set is named on a record that arrives last. **That
is the C10 blocker**, and the contract still prescribes the impossible call.
C10 must be amended to: feed `DimensionTally` in the loop, call `.counts()`
after the comparison record. C10 also states the public surface is "exactly" an
`__all__` that omits `DimensionTally`, which C21 added and C10 must use; and it
expects `ReportModel.dimensions: DimensionMatrix` where C21 delivers
`dimension_counts: DimensionCounts`, raw counts only. **C21 did not deliver
C10** — building the matrix from those counts is still C10's work.

---

### R15 — the series is one line and the change stays visible; R14.6 ruled

Ruled by the person paying for this, 2026-08-24: **make the series one line, keep
the change visible.** Both, not a trade. This section says how, and records why
the two were only ever in tension by accident.

#### The tension was manufactured by the filter

R14.6 framed this as a choice: either candidate B's fourteen nights are one line
(and the v1→v2 change is hidden inside it) or the change is visible (and the line
splits 13+1). That framing is wrong, and the error is worth naming because it
will recur.

`trend(points, *, baseline_model, candidate_model)` filters the series **by the
very field that moved**. The change is invisible *because* the filter hides it:
night 14 is not in the same series as night 13, so `parameter_strip`'s
`previous` is `None`, so the `model_id` row reports `changed=False` with an empty
`before` — the first run changed nothing because there was nothing to change
from. The strip is already built to show exactly this and is prevented from
doing so by its own caller.

Stop filtering on the thing that moves, and both properties hold at once. The
strip needs no change whatever.

#### R15.1 — the lineage is declared, never inferred

`trend` takes a caller-declared sequence of candidate ids:

```python
def trend(
    points: Sequence[RunPoint],
    *,
    baseline_model: str,
    candidate_models: Sequence[str],
) -> Trend: ...
```

**The tool must not infer that `-b-v2` succeeds `-b-v1`.** Stripping a trailing
version suffix is the obvious implementation and it is forbidden. Whether two
model ids name the same lineage is a fact about the world that no log records,
and a wrong guess silently joins two unrelated models into one line — which is
precisely the "two unrelated numbers side by side" failure
`_require_comparable` exists to prevent, arrived at from a new direction. The
operator knows the lineage. The operator says so.

Order within `candidate_models` is not significant; time ordering comes from
`created`. A single-element sequence reproduces today's **selection** exactly, so
this is a strict generalisation and not a behaviour change for any existing
caller.

**Amended 2026-08-24: "reproduces today's behaviour exactly" and R15.2's
unconditional partitioning contradict each other**, because the old `trend`
partitioned nothing — so on a log containing an incomparable run the two rules
give different answers. C7's implementer found this and followed R15.2. Ruled the
same way: **partitioning is unconditional.** A line joining one model's runs
across an edited golden set is the same false line R15.2 exists to prevent, and
the number of ids in the lineage has nothing to do with it. On any log where the
selected runs are comparable — every normal nightly case — the two readings
coincide, so the promise this sentence was making is kept everywhere it was
actually being relied on.

#### R15.2 — joining two ids asserts comparability, so it must be checked

Putting two model ids on one line is a claim that the runs are comparable. That
claim is C4's to adjudicate, so `trend` partitions its candidates through
`partition_comparable` against the group key and carries the exclusions out with
it. A lineage whose members disagree on golden set, judges or `n_per_item` is
not one line and must not be drawn as one.

This makes C7 depend on C4, which §6's graph does not show. Amend the graph.

#### R15.3 — `trend` returns a `Trend`, because a bare tuple has nowhere to put the answer

```python
class Succession(NamedTuple):
    index: int        # into points, of the FIRST run under the new id
    before: str
    after: str
    created: str

class Trend(NamedTuple):
    points: tuple[RunPoint, ...]           # one line, ascending by created
    successions: tuple[Succession, ...]
    excluded: tuple[Exclusion, ...]        # C4's type, from R15.2
    undated: int                           # dropped for unparseable created
    caveats: tuple[Caveat, ...]            # added after the fact -- see below
```

> **`caveats` was added on 2026-08-24, and the reason is embarrassing enough to
> keep.** C7's implementer found that `partition_comparable` computes the
> A/A-calibration and uneven-coverage caveats and `Trend` had nowhere to put
> them, so they were computed and discarded — **which is precisely the defect
> class the next three paragraphs name, committed in the type written to fix
> it.** It went last so that tuple-prefix unpacking and any assertion about the
> first four positions survive. None are filtered — this is not C5, where a
> caveat on a superseded run has no row and is correctly dropped.
>
> The reason first given for carrying them all was "every point in
> `Trend.points` is drawn, so every caveat has a row." **That is false**, and
> C7's implementer said so while implementing the ruling. A point the partition
> *keeps* and datedness then *drops* has no row at all — and that is exactly the
> case where filtering would be worst, because `undated` is a bare count that
> names no point, so the caveat is the only surviving trace of that run. A
> `Caveat` carries its own point, so a renderer with no row for one can say so
> rather than invent one. Dropping it would have been this defect class a fifth
> time, inside the amendment fixing its fourth. The ruling stands; the reason for
> it is the implementer's, and it is in the code's docstrings so nobody tidies it
> back.
>
> The implementer declined to add the field unilaterally, correctly: the contract
> fixed four fields, and a blind tester asserting `len(t) == 4` would have gone
> red against correct code. It reported the defect and waited for a ruling. That
> is the judgement call this pipeline wants, and it is the only reason the
> amendment reached the tester before the tests were written rather than after.

`undated` fixes a defect C7's contract already had: it says undated points "are
excluded from the return and the caller learns of them separately", and then
returns a bare `tuple[RunPoint, ...]` through which the caller can learn nothing.
**This is the third instance of the same defect class in one plan** — C4's flag
with no field to live in (R14.2), C13's counts that had to become a `Timeline`
NamedTuple (R6), and now this. The pattern: a contract states that the caller is
told about an absence, and gives a return type with room for presences only. It
is worth checking every remaining contract for it before dispatch.

#### R15.4 — the change is visible in three places, which fail differently

Redundancy here is deliberate. Each of these is silent in a different failure.

1. **The parameter strip** — the `model_id` row, `changed=True`, both ids. Needs
   no code change; R15.1 is what lets it fire. This is the attributable-drop
   claim and it is the load-bearing one.
2. **The timeline** — a rule drawn at each succession, so nobody reads a
   continuous line as one continuous model. See R15.5 for when.
3. **A caption beneath the chart** naming each succession in words, because a
   mark with no legend is a mark a reader invents a meaning for.

If the strip were the only one, a reader looking at the picture would see an
unbroken line and never scroll. If the mark were the only one, the change would
be visible and unattributable.

#### R15.5 — the SVG mark waits for C14a, and arrives backward-compatibly

`timeline_svg` is merged **and reviewed**, and C14a — the chunk that first calls
it — is in review as this is written. Changing its signature now is precisely the
rename hazard §"When it is safe to go wide" is built around: C1's review renamed
fields after C2 had started typing them, and nothing collided, the work simply
had to be redone.

So: the mark is a follow-on chunk, dispatched after C14a merges, and it adds a
keyword argument that defaults to drawing nothing. `timeline_svg(points)` keeps
its current meaning and its current output byte for byte.

#### What this settles, and what it does not

C16's night 14 now works as its contract intends: the showcase driver declares
`("synthetic-candidate-b-v1", "synthetic-candidate-b-v2")` as one lineage,
candidate B is fourteen points on one line with one succession at index 13, and
the strip's `model_id` row reads `changed=True` with both ids — **exactly one
`changed=True` row**, and now actually observable rather than merely required.

**Correction, same day: R8 does not remain open.** This paragraph originally said
it did, on the strength of R8's own "OPEN" header — which had been stale for
three days. C19 closed it: pairing now attaches a verdict to the most recently
opened point, so R8's counterexample `C1 C2 V1` gives `series[-1].verdict == V1`
and a headline of `V1`, in agreement. `RESTART.md` had this right and this plan
did not, and the disagreement was repeated here before it was caught.

The grouping was loose in the other direction too: R14.6 is about an identity
field splitting a series, R8 was about verdict pairing, and they were never two
halves of one question. Nothing in R15 reaches R8 because there is nothing left
of R8 to reach.

---

### R16 — C10 is unblocked; three corrections to its restated contract

C10 has been blocked since its first dispatch and the reason was never written
down anywhere an implementer would find it. It is written down now, and it is
closed. Everything below was checked against `dimensions.py` on main after C21
merged, not read off this plan.

#### R16.1 — the blocker, named, and the call that replaces it

C10's contract tells the implementer to consume:

```python
dimension_counts(records, items, *, judge) -> DimensionCounts
```

**That call cannot be made inside `from_evidence`.** It needs the records and the
golden set *at the same moment*, and the golden set's path lives in the
`migkit.comparison` payload, which is written after judging and is therefore
among the last records the pass sees. Both ways around it are closed by merged
tests that may not be weakened: reading the log twice fails
`test_the_log_is_read_once_for_both_the_headline_and_the_series`, and buffering
the verdicts fails `test_rebuilding_the_report_does_not_hold_the_log_either`.

That is the whole blocker. C10's implementer was correctly forbidden from
touching a merged module, so it could not solve it from where it stood, and the
contract still prescribes the impossible call.

**C21 split the phases.** Use the two-phase form:

```python
tally = DimensionTally()            # no golden set yet, and that is the point
for record in records:              # the single streaming pass
    tally.add(record)
...                                 # migkit.comparison arrives, golden set resolves
counts = tally.counts(items, judge=judge)
```

`dimension_counts` still exists and is still correct — it is this class with both
phases run back to back, and remains the shape to reach for **whenever the golden
set is already in hand**. It is simply not the shape `from_evidence` can use.

#### R16.2 — the public surface C10 quotes is stale

C10 states the surface is "exactly" an `__all__` that does not contain
`DimensionTally`. It does now:

```python
__all__ = ["DimensionCell", "DimensionCounts", "DimensionTally",
           "MIN_ITEMS_FOR_A_VERDICT", "MIN_N_FOR_A_VERDICT", "TagCount",
           "UNTAGGED", "dimension_cell", "dimension_counts"]
```

Read `dimensions.py` rather than that list — the same instruction C10 already
gives, which is worth obeying twice over now that the list has been wrong once.

#### R16.3 — C21 did not deliver C10, and left one field for C10 to absorb

C21 delivers raw counts and wires `ReportModel.dimension_counts:
DimensionCounts`. C10's real work — the matrix: cells, golden-set tag order with
`UNTAGGED` last, baseline against candidates, both floors, and the six ways to be
unavailable — is untouched and still C10's.

**Ruling: `dimensions: DimensionMatrix` replaces `dimension_counts` on
`ReportModel`; it does not sit beside it.** `DimensionCell` carries `tag`,
`passes`, `n` and `items`, so the matrix subsumes every fact `TagCount` held and
nothing is lost. Keeping both would put the same facts on the model at two
fidelities, which is two chances for them to disagree — the identical reasoning
C10's own contract already gives for never re-wording a decline reason: "three
copies of a disclosure are three chances for one to go stale."

C21's wiring tests in `test_report.py` section 20 cover the tally being fed and
the per-run reset. They will need re-pointing at the new field. **Re-point them;
do not delete them.** They exist because deleting `tally.add(record)` from the
loop once left the entire suite green.

#### One contingency

C21 is merged and its review is in flight as this is written. Reviews on this
project have demanded renames three times out of three, so **every name above is
provisional until that review lands**, and C10 must not be dispatched before it
does. That is not caution for its own sake: it is the exact sequence that cost
C2 a rewrite, when C1's review renamed fields after C2 had started typing them.

---

### R17 — C5 and C6 audited before dispatch; C6's correction under-reports

Done while four reviews were in flight, so that these two can go out the moment
C4's review lands rather than being audited then. Everything below was checked
against the code, and two of the five were confirmed by running it.

#### R17.1 — C6's `changed` rule is wrong, and C6's own first test does not catch it

> **CORRECTED — read R25.** The ruling, the worked table and the conclusion
> below are right. The *mechanism* sentence is wrong: `holm_bonferroni` does
> not return the uncorrected `alpha` for candidates after the stop. The real
> reason is simpler and stronger, and the error matters because it implies the
> bug only bites after a step-down stop, when in fact it bites every family.

C6 defines the set that "makes the honesty guard demonstrable" as candidates
where `p_value < alpha` **and** `p_value >= holm_threshold`.

That is not the Holm procedure. Holm **steps down**: once one test fails to
reject, nothing larger is rejected either, *regardless of its own threshold*.
`comparison.holm_bonferroni` implements this correctly and returns
`(rejected, threshold)` per position. For every candidate after the stop, the
returned threshold is the uncorrected `alpha` itself — so `p >= threshold` is
vacuously false and the candidate silently drops out of `changed`.

Run against the real implementation at `alpha=0.05`:

```
p = [0.03, 0.04, 0.045]
  p=0.03   rejected=False thr=0.01667   contract: changed   truth: changed
  p=0.04   rejected=False thr=0.02500   contract: changed   truth: changed
  p=0.045  rejected=False thr=0.05000   contract: NOT       truth: CHANGED  <-- missed
```

The rule misses the largest sub-alpha p-value in the family, every time. It fails
in the direction that **under-reports**: a candidate whose significance the
correction removed is not named as having changed, in the one set whose entire
purpose is to make the correction's effect visible. C6's own "Failure mode when
wrong" is "claiming a guard you did not apply is worse than applying none"; this
is the quieter cousin — applying the guard and under-stating what it did.

**Worse: C6's named first test passes against the broken rule.** That test uses
p = 0.03, 0.04, 0.045 and asserts only that *the first* appears in `changed`.
0.03 appears under both rules. A tester writing exactly the test the contract
names would see green.

**Ruling.** `changed` is `p_value < alpha and not rejected`, taking `rejected`
from `holm_bonferroni`'s own return. **Never compare a p-value against the
returned threshold to decide significance** — the threshold is diagnostic output
for display, and after the step-down stops it is not a decision boundary at all.
Both briefs get this verbatim, and the named test gets a second assertion that
0.045 is in `changed` too, so it can no longer pass while the rule is broken.

#### R17.2 — C5 needs a baseline pass rate, and no such field exists

`Candidate.delta_pp` is "candidate `pass_rate` minus baseline `pass_rate`" and
`CandidateField.baseline_pass_rate` is a field of its own. **`RunPoint` has no
baseline rate.** Its `pass_rate` is documented as "Candidate side of the widest
judge", and the only baseline-side numbers it carries are `judged_baseline` and
`judge_failures_baseline`.

This is C4's missing-`records` defect again, one chunk over: a contract naming a
field the producer does not have. Adding one means editing C1/C2 while their
consumers are in flight, which is forbidden.

**Ruling: derive it, exactly.** `judge_failures_baseline` is documented as the
gate's own `failures`, which *is* `n - successes`, so

```python
baseline_pass_rate = (judged_baseline - judge_failures_baseline) / judged_baseline
```

is not an approximation of the recorded rate — it is the recorded rate,
reconstructed from the two numbers rigor recorded it from. It is also the same
denominator convention `pass_rate` uses on the candidate side, so `delta_pp`
subtracts two quantities measured the same way.

`None` when `judged_baseline == 0`, mirroring `_candidate_rate`, which refuses to
divide by zero and says why: passing `0.0` up "would plot a point on the floor of
the chart for a run that measured nothing, which reads as a total collapse rather
than as an absence". A `delta_pp` of `-100.0` against a baseline that measured
nothing is the same lie in the same direction.

**The `None` branch is unreachable through `candidate_field`, and that is fine.**
Both C5 agents found this independently and both said so rather than quietly
working around it: R18.4 made C4's `_ungraded` exclude any point with
`judged_baseline <= 0`, so no such point can ever be a kept candidate. The branch
is still right *at the helper* — a helper that divides by zero because its only
caller happens not to reach it is a trap for the second caller — but a test
asserting `baseline_pass_rate is None` for a kept candidate cannot be written.
The reachable form is the one to test: a log whose every run graded nothing
yields **no field at all**, rather than a column of `-100.0` deltas. Making the
branch reachable would mean computing the rate *before* partitioning, which
contradicts this ruling's own reasoning.

#### R17.3 — C5's `excluded` needs its companion

R14.2 gave `partition_comparable` a third return element and R15 noted C5 would
need to follow. Making it explicit: **`CandidateField` gains
`caveats: tuple[Caveat, ...]` beside `excluded`.** A caveat is a kept candidate
that carries a warning; dropping the caveats on the floor at this layer means the
warning never reaches the table, and a warning that reaches nobody is the same as
not having computed it.

**Corrected in place, 2026-08-24.** This paragraph said `flags: tuple[Flag, ...]`
for an hour after R18.4 renamed `Flag` to `Caveat`, and C5's implementer caught
it — it had followed C5's banner and C4's merged code instead, and reported the
discrepancy rather than silently picking one. Recorded because it is the exact
failure this plan keeps producing: a correction lands in one place a reader might
look and not in another. The banner convention exists for that reason, and a
banner does not excuse leaving the revision body wrong, because §R17 is itself a
section an agent may be pointed at directly.

#### R17.4 — "grouping by `comparability_key` ignoring `candidate_model`" is stale

C5 says to group by `comparability_key` "ignoring `candidate_model`". The key has
never contained `candidate_model` — C4 ships `goldenset_hash`, `judges_hash`,
`n_per_item`, `baseline_model`. The phrase predates the key and now reads as an
instruction to strip a field that is not there, which invites an implementer to
go looking for it.

Harmless in itself, but worth striking, because the same sentence contains the
reason it matters: a key that *did* include `candidate_model` makes every group
a group of one, `candidate_field` returns `None` every time, and the table never
renders. C4's tester wrote that mutant deliberately; C5's should inherit it.

#### R17.5 — the tie-break must be total, not merely stated

C5 picks the largest group, "ties break on the group containing the newest
point". Two groups can tie on size *and* contain no dated point at all — every
`created` is `""` is an edge C5's own table already contemplates elsewhere. Then
"the newest point" does not exist and the winner falls out of whatever order the
groups were built in, which is dict insertion order over hashes: stable on one
machine, not guaranteed across a rebuild.

C5's reviewer note already suspects this ("renders differently on two machines if
it falls back to dict ordering of hashes"). Do not leave it for the reviewer.
**Ruling: the tie-break is total** — largest group, then newest point, then the
group's `ComparabilityKey` in sorted order as the final deterministic tiebreaker.
A stable arbitrary answer is worth more here than a principled unstable one,
because the failure is a document that differs between two renders of one log.

---

### R18 — what C4's and C11's reviews changed, including one claim this plan had backwards

Both reviews ran with mutation testing. Between them they killed 43 mutants and
left 20 alive, and the survivors are the useful part. Two findings change this
document rather than the code.

#### R18.1 — C11's honesty claim is inverted, in this plan and in the shipped docstring

C11's contract says unstable items are counted as passing because it "makes the
spot check look *better* than it is, so the tool never inflates its own case."

**The second clause is false**, and the arithmetic that refutes it is the
chunk's own:

```
N=96, unstable=3, k=12
  unstable folded into PASSING (the rule)   F=8    P = 0.3288
  unstable counted as FAILING               F=11   P = 0.2106
```

`P` is the chance a spot check sees nothing. **Higher means blinder manual QA,
which is a stronger argument for this harness.** The rule produces the higher
number. It is therefore the choice that *strengthens the tool's own case*, not
the one that restrains it.

The rule stays — it is right on a different ground, which is that the tool does
not claim regressions it has not established. But that is an honesty claim about
`F`, and its effect on the quoted probability runs the other way. **Both halves
must be said.** The shipped docstring currently says neither correctly: it
asserts within one paragraph both that the rule "raises this probability" and
that counting-as-failures "would produce a larger, more quotable number", which
cannot both be true and whose second half is measurably false.

This is the worst kind of defect this project can produce. C11's contract says
that stating which way the thumb is on the scale "is the whole reason this
sentence survives scrutiny" — and the statement was pointing the wrong way, in
the most-quoted sentence of a document whose entire claim is that it does not
overclaim. A director who checks the direction, which is exactly the reader this
line is written for, finds it defended by a rationale its own arithmetic refutes.

**Struck from C11's contract: "so the tool never inflates its own case."**

#### R18.2 — §7.4's "order of magnitude in the flattering direction" conflates two errors

C11's **Must not** and §7.4 both say the with-replacement form "understates the
spot check's blindness by roughly an order of magnitude."

```
correct (hypergeometric, item rate 88/96):  0.3288
with replacement, SAME rate (88/96)**12:    0.3520   -> OVERstates, by 7%
a DIFFERENT rate, 0.75**12:                 0.0317   -> understates, by 10x
```

The error R14.1 caught this plan actually committing is the first, and it runs
the **opposite** direction and by **7%**. The order-of-magnitude figure requires
a completion pass rate of 0.75, which §7.4's own premise forbids: if all n draws
of an item are identical, the completion rate *equals* the item rate and 0.75
cannot arise. So the plan's central worked example uses a rate its own
determinism argument rules out, to state a direction its own corrected number
reverses.

Both claims are corrected. The reason to keep `math.comb` is unchanged and was
never in doubt: exact integer arithmetic is free.

#### R18.3 — C11's sentence, amended

Ruled after review, replacing the wording R14.1 corrected:

> A 12-prompt spot check drawn at random from these 96 items, **8 of which
> failed**, would have shown no failures at all in 33% of **such checks**.

Two fixes. The old wording ate its own tail — a singular subject inside its own
plural denominator. And it never said how many items failed, so a director's
first question after "33%" was unanswerable from the line; `SpotCheck.failing`
carried the number and the sentence dropped it.

**`N == k` now returns `None`.** The contract excluded `N < k` because "the check
would try every item"; that rationale applies identically at `N == k`. A draw
taking every item is a **census, not a spot check**, and the sentence's whole
force is that only a few were looked at. Naming a census a spot check is an
overclaim in the chunk built to prevent overclaiming.

#### R18.4 — C4's edge table gains three exclusions, and two names change

The review found that the empty-hash hole — the one C4's contract names and
closes — **is open in three more fields**, and that one of them is worse than the
failure the chunk exists to prevent.

- **Both sides graded zero.** `_judged_flags` fires on inequality and `0 != 0` is
  false, so a point with `judged_baseline == judged_candidate == 0` is neither
  excluded nor flagged: no pass rate, floor unrecorded, and it renders as an
  ordinary complete row. `_require_comparable` refuses this outright — "neither
  artifact contains a judged completion, so there is nothing to compare" — on a
  field grouping *can* see, so C4's bridge claim admits a pair it says it never
  admits. **Now excluded**, and so is a single zero side, because the flag's own
  sentence ("the gap may be lost judge replies") is untrue of a side that graded
  nothing.
- **`n_per_item == 0`** and **`baseline_model == ""`.** Both are the contract's
  own **Must not** — "coerce an empty hash to a match" — one and two fields over;
  `_count` returning 0 for a missing value makes "not recorded" and "recorded as
  zero" indistinguishable exactly as `""` did. Both **now excluded**. C5 could
  not have closed them: C5 consumes `Exclusion`, it does not mint the rule, and
  R15.2 has since added C7 as a second consumer, so deferring would have handed
  one hole to two chunks.
- **Self-comparison** (`baseline_model == candidate_model`) is a fourth refusal
  `_require_comparable` makes and grouping did not. An A/A calibration run is
  legitimately logged, so it is **a caveat, not an exclusion** — C5 decides
  whether to render it; C4's job is that it is never silently admitted.

**`Flag` is renamed `Caveat`**, before three chunks type it. It collided with
`enum.Flag` — and C6 and C7 are rendering chunks where `from enum import Flag`
would shadow it silently — and it would have shared a namespace with C5's
`spread_flagged` and `RunPoint.warnings`, three "flag" concepts under one word.
`Exclusion` names an outcome; `Caveat` names one too, and the pair reads as
removed versus kept-with-a-note.

**The three-tuple becomes `Partition`, a NamedTuple.** R15.3 named this defect
class, listed R14.2, R6's `Timeline` and R15.3's `Trend` as its instances, and
said to check every remaining contract for it. This was the remaining instance.
The change is free — a NamedTuple compares equal to a plain tuple, satisfies
`isinstance(x, tuple)`, has `len() == 3` and unpacks positionally — so every
existing assertion passes unchanged, and a fourth field stops being a breaking
change for what are now three consumers.

#### R18.5 — one review claim that was wrong, recorded because it was nearly persuasive

C4's reviewer noted that R14 and R15 are not on `chunk/c4-impl` and concluded
that the implementer and tester "could not have read R14.2 or R14.3", making
their agreement on the three-tuple, on `judged_*` and on the word *graded*
"genuinely independent corroboration — the best evidence in this chunk."

It is not. Those three rulings were in both briefs verbatim; the agents read them
from the brief, not from the plan. The reviewer inferred access from the file and
missed the channel.

The part that **is** independent is smaller and still worth having: nothing in
either brief disambiguated per-side from cross-run, so the tester's reading of
that was genuinely its own. Recorded because a tidy story about independent
convergence was one inference away from being believed, and the difference
between "two agents agreed" and "two agents were told the same thing" is the
whole value of running them blind.

---

### R19 — what the C4 and C11 fix passes changed, and one technique worth stealing

#### R19.1 — the unfix harness, which should be standard from here

C4's fix pass did something no previous role on this project has done, and it is
the cheapest good idea in the pipeline. Having implemented seven rulings and
killed eight mutants, it then **reverted each ruling one at a time and confirmed
the suite went red for each**.

Mutation testing asks "does the suite notice a defect the reviewer invented?"
The unfix asks the question that actually matters after a fix pass: **"does the
suite notice if this fix is undone?"** A ruling can be implemented correctly and
left entirely unguarded — the code is right, the tests pass, and the next editor
removes it without a single failure. That is how three of C4's eight survivors
came to exist in the first place.

**Do this in every fix pass.** It is one script, it runs in the time the suite
takes, and a ruling that survives its own reversal is a ruling that was written
down rather than enforced.

#### R19.2 — a mutant whose obvious test is green, and a review claim corrected

C4's review said its S3 mutant (checking only one side of the hash-recorded test)
left "a point *with* a hash measured against a group key *without* one … kept".
The fix pass built exactly that test, watched it **pass against the mutant**, and
came back rather than declaring the mutant dead. It was right: the point falls
through to the *mismatch* branch, `hash != ""` holds, and it is excluded anyway.

What S3 actually costs is the **sentence**. The report prints `golden set
5fef50364057cad8 against the group's unrecorded … two unrelated numbers` —
asserting a second golden set that does not exist and sending the reader to
find it.

And the sting: **the obvious keyword assertion is green against the mutant**,
because `"unrecorded"` is exactly what the mismatch branch prints for the absent
side. Only an assertion that classifies *which rule did the excluding* kills it.

Two things to carry forward. A reviewer's stated *mechanism* can be wrong while
its instinct is right — the mutant was real, the explanation was not. And when a
test passes against a mutant it was written to kill, that is information, not a
formality: it means the failure is somewhere other than where it was predicted.

#### R19.3 — `hashes_recorded` became a lie the moment its neighbours grew

`ComparabilityKey.hashes_recorded` was added by C4's implementer, unrequested,
to make a real hazard actionable: naive dataclass equality merges every hash-less
run into one confident-looking group, and C5 groups on this object.

R18.4 then added three more grounds for exclusion — both sides graded zero,
`n_per_item == 0`, unrecorded `baseline_model`. The property's first line said it
reported "whether this key can establish comparability at all", which was true
when only hashes could exclude and false immediately afterwards. A key with both
hashes and `n_per_item == 0` answers `True` and is then excluded: C5 would build
a group and be told every member was removed.

**Ruled: widen it and rename to `is_identifying`, covering all four fields**,
using the same emptiness test the exclusion rules use so the two cannot drift.
The fix pass correctly declined to do this in flight — widening is a contract
change and C5 and C7 are about to type against it — and flagged it for a ruling
instead. That is the right instinct and the right moment: it is the same
argument that made `Flag` → `Caveat` free rather than a three-chunk rewrite.

The guard against it becoming a lie a third time is a test asserting the property
is `False` for a key `partition_comparable` excludes on each of the four grounds,
one row per ground — so adding a fifth rule without the property goes red.

#### R19.4 — C11's inversion had a sibling nobody listed

The fix pass for R18.1 found that `test_..._so_the_number_never_flatters_the_
tool` carried the **same** inversion in its own name and docstring ("drop the
probability, which reads as a *better* argument for the tool"). It was not in the
ruling and it was fixed anyway.

The replacement test does something better than assert the corrected wording: it
measures both probabilities, asserts the rule's is the larger, and requires the
docstring to quote the drop **interpolated from the measured values**. Prose and
arithmetic can no longer drift apart, which is the failure that produced this
entire revision.

Also settled: `N == k` returns `None` (a draw taking every item is a census), the
rendered sentence now names how many items failed, `_percent` is tested directly
across thirteen rows because exact halves are what separate rounding rules and
`comb(N-F,k)/comb(N,k)` cannot be steered onto one — which is why testing only
through `spot_check` let the always-round-up mutant live.

#### R19.5 — the numbers

| | before fix | after fix | mutants |
|---|---|---|---|
| C4 | 138 tests | **191** | 8 introduced, 8 dead, plus 7 unfix reversals all red |
| C11 | 120 tests | **150** | 12 introduced, 12 dead |

Both green on all seven merge checks. Full suite at C4's branch: 1692 passed.

### R20 — what C5's review found, and the one finding that changes the contract

C5's reviewer ran 39 mutants and 15 survived. The chunk's *code* came out mostly
right — all four of the implementer's blind decisions were endorsed on the
reasoning, not merely on the outcome — but the suite underneath it was holding
much less than its 269 passing tests suggested.

#### R20.1 — the fixture monoculture, which is now a named failure mode

**`delta_pp` computed against the field's summary `baseline_pass_rate` survives
a fully green suite.** The implementation correctly uses each point's own
baseline; nothing pins it.

Every C5 fixture hard-codes `judged_baseline=50, judge_failures_baseline=10` on
*every* point, so all rows share one baseline and per-point versus summary are
numerically identical. The mutant cannot be seen, because no fixture can tell
the two apart.

Probed against real drift:

```
alpha (Aug 10, own baseline 0.80, pass_rate 0.65) -> delta_pp = -15.0  (correct)
gamma (Aug 20, own baseline 0.60, pass_rate 0.65) -> delta_pp =  +5.0  (correct)
under the mutant, BOTH rows print +5.0
```

A candidate that lost fifteen points is published as having gained five, because
the baseline drifted underneath it. That is C5's own named failure mode, and 269
tests stayed green.

**This is the third appearance of one shape.** On C4, a hash-prefix mutant
survived the whole suite because every fixture hash differed at character 0. On
C7, the tester killed it in advance by choosing two hashes that share sixteen
characters and differ at the seventeenth, and ids differing *only* in a version
suffix. The rule those three cases teach:

> **A fixture where the broken and the correct implementation agree is a fixture
> that tests nothing.** Vary the field the code is supposed to be reading. If
> every fixture carries the same value for it, the suite cannot see it at all.

The tester was not careless here — it flagged the ambiguity in a section header
and left it "for the reviewer rather than decided by a test". But that admission
was about `baseline_pass_rate`'s provenance, and it silently took `delta_pp`'s
per-point baseline down with it. **An acknowledged ambiguity in one field is not
an acknowledged ambiguity in its neighbour.**

#### R20.2 — a ruling shipped with its substance unpinned

The sorting ruling (dateless rows sort *oldest*, not last) was upheld on review,
and I would rule it the same way again. But the `_UNDATED = datetime.max`
mutant — which inverts the ruling in **all three** places the ruling itself says
it has work to do — survives 269 green tests. Only one of the three, the spread,
was asserted.

Two jobs had no test at all: a model with one undated and one dated run (the
dated run should win the row), and two equally large groups where one holds an
undated run (the dated group should win the tie-break).

**Ruling a disagreement is not the same as pinning the ruling.** When a blind
pair disagrees and the orchestrator rules, the ruling needs a test the same day,
or the next reader finds a contract sentence with nothing enforcing it.

One correction to my own ruling, from the same review: I wrote that the date
reading was "the only total reading". That is false — "dateless rows last,
`candidate_model` within each block" is also total. The contract simply does not
supply that second sentence, so the layout reading is untotal **as written**,
which is enough under R17.5. The overstatement is worth removing precisely
because a reader who catches it discounts the two grounds that do carry the
ruling.

#### R20.3 — CandidateField gains the window it was built with

`stale_after_days` is a parameter of `candidate_field` (correctly — R14 made it
one rather than a literal). `_STALE_AFTER_DAYS` is private. **`CandidateField`
carries neither.**

So a renderer holding only the field can say "measured more than 7 days apart"
about a field built with `stale_after_days=30.0`. Both halves are true and the
sentence is a lie. This is the hazard C6's `note` discipline exists to prevent —
the sentence must be written where the number is computed, not in the template,
so the terminal render and the HTML cannot drift apart.

**Ruling: add `stale_after_days: float` as an eighth field.** The number and the
window it was measured against travel together, or they eventually disagree. The
alternative — passing the window to the renderer alongside the field — creates
exactly the two sources that can contradict each other.

I deferred this at first, on the grounds that it changes a field count a test
asserts and that C6 was about to be dispatched against the seven-field shape.
That reasoning does not survive contact: **C6's dispatch is mine to schedule**,
its brief was still a draft, and it is blocked on C5 in any case. A contract
defect is cheapest to fix while the file is already open, and the deferral was
buying nothing.

`CandidateField` is a frozen dataclass rather than a NamedTuple, so field order
is repr-only and `dataclasses.replace` in C6 is unaffected. The eighth field
goes last.

#### R20.4 — eight more defects, and how each was ruled

Acted on in the fix pass:

| | Defect | Ruling |
|---|---|---|
| D1 | The header baseline is the *newest* row's; each `delta_pp` is against its *own*. When the baseline drifts they are silently inconsistent, and `spread_flagged` — a **time** proxy — is `False` on a one-day drift. | Raise a caveat when the rendered rows' reconstructed baselines are not all equal, and say on the **public** attribute that the header must not be added to the deltas. Do not blank the header. |
| D2 | A run superseded by a newer run of the same model appears in `candidates`, `excluded` **and** `caveats`: nowhere. It vanishes with no sentence. | Mint a superseded exclusion on the `_unnamed_candidate` precedent. This is the quietly-shrunk table C4 exists to prevent, and it contradicts the implementer's own decision-2 rationale. |
| D3 | `spread_days == 0.0` from a **single** dated row — the exact "measured in a single sitting" claim its docstring says `None` exists to refuse. | `None` unless at least two rendered rows carry a date. Compatible with all three contract edges. |
| D4 | `judge_failures_baseline > judged_baseline` yields a negative rate and a `delta_pp` of +85.0, uncaveated. The only rate C5 computes rather than reads, and the only one with no sanity bound. | `None`. C4 minted four exclusions for exactly this "a payload is JSON, not a type" class. |
| D5 | The deliberate unroundedness is stated only in *private* docstrings, and a round-to-1dp mutant survives because `pytest.approx` swallows it. | Move the sentence to the public attributes; assert one exact float. |
| D8 | The `dir()` guard on `Candidate` forbids *any* public addition, not just a statistic. | Narrow it to interval/CI/confidence/p-value-shaped names, so `Candidate.model` can be added for C6. |

Documentation-only, and both are cases of a docstring teaching something false:

- **D6 — `is_identifying` is dead at its only call site.** `candidate_field` and
  `_widest_field` both justify the guard with a scenario that cannot occur:
  partitioning against a non-identifying key keeps *zero* points, so its rank is
  `(0, 0, _UNDATED)` and it can never win. Keep the guard, correct the
  sentences. Cross-chunk consequence: C4 introduced `is_identifying` on the
  claim that "anything that groups on `ComparabilityKey` needs this", and the
  only consumer that ever grouped on it does not.
- **D7 — `CandidateField.baseline_pass_rate` can never be `None`** from
  `candidate_field`, since every rendered point passed `_ungraded`, which
  requires `judged_baseline > 0`. Say so, or C6 writes a dead branch.
  (`Candidate.delta_pp is None` *is* reachable, and that promise stands.)

#### R20.5 — what the review endorsed

Worth recording, because four blind decisions surviving a hostile review is the
first time that has happened on this plan:

1. **"Largest group" ranks by distinct models, not run count.** Thirteen nightly
   runs of a single candidate beside a two-model group: the implementation
   renders the two-model field, run-count-first returns `None`. The docstring's
   claim — *if any eligible group can render a table, the chosen one does* — is
   exact, because the rank's first term is literally `len(rendered)`.
2. **Partition the whole log, not just the group.** Endorsed with a proof rather
   than an observation: `_incomparable(key(p), K) is None` iff `key(p) == K`
   **and** `K.is_identifying`, and eligibility guarantees the second — so `kept`
   is bit-identical either way and only `excluded` differs. Without it,
   `excluded` names only ungraded runs and C4's exclusion machinery is dead at
   its only call site.
3. **A new exclusion for `candidate_model == ""`.** C4 genuinely has no
   jurisdiction — `candidate_model` is not in the key — and C5 is the only
   consumer.
4. **`caveats` filtered to the rendered rows.** Correct in isolation; correct
   *and complete* once D2 mints the superseded exclusion.

One consequence flagged for the plan rather than for C5: a log of two anonymous
runs plus one named candidate returns `None`, and every exclusion sentence
computed along the way dies with it. The report can never say *why* there is no
table. That is inherent to the contract's `None` return, and it is worth
revisiting when C14's remaining elements decide what an empty section renders as.

### R21 — six elements have no data path, and `trend`'s lineage has no source

Found while scheduling C14's remaining seven elements, before dispatch. Both
findings are structural rather than local, and the first blocks every remaining
visible element in the document.

#### R21.1 — nobody wires the series chunks onto `ReportModel`

`ReportModel` on `main` at `60a3fed` carries **31 fields**. Two are the ones this
plan added: `series` (C3) and `dimensions` (C10). It carries no `spot_check`, no
`candidates`, no `excluded`, no `multiplicity`, and no `parameter_strip`.

C14's contract gates its sections like this:

| Element | Present when |
|---|---|
| spot-check sentence | `spot_check(...)` is not `None` |
| candidate table | `candidate_field(...)` is not `None` |

Those are **function calls**, and a Jinja template cannot make them. C14's own
**Must not** closes the other door in the same breath: *"Introduce a new `{{ }}`
reference to a field `ReportModel` does not define: `StrictUndefined` will
raise, which is the designed behaviour and must not be worked around."*

So the template may not call the function and may not name a field that does not
exist. As written, six of C14's nine elements cannot be built by anyone.

**This is an omission, not a contradiction, and it is easy to see how it
happened.** Every one of C4, C5, C6, C7 and C11 declares its **Files** as
`series.py` and `tests/test_series.py` — pure functions, no wiring, which is
exactly what made them safe to run wide. C14a declares its files as *"**Not**
`from_evidence`, **not** `ReportModel`"*. C14 declares `_TEMPLATE`,
`_CHANGES_MACRO`, `render_html_string`, `_environment` and the tests. C10 is the
**only** chunk in the plan that was told to extend `ReportModel`, and R16.3 had
to say so explicitly.

Between "compute it" and "render it" there is a step nobody owns. Every chunk
respected its own boundary correctly and the boundary between them was never
drawn.

That the pipeline produced this is worth recording: the discipline that keeps
chunks independent — *touch only your files* — is the same discipline that lets
an unassigned seam sit undetected across eleven chunks. The rule that would have
caught it is the one this plan already learned and wrote at the top of RESTART:
**order the chunks so the artifact moves.** Had C14 been scheduled to render
C11's sentence when C11 merged, the gap would have surfaced that day instead of
eight chunks later.

#### R21.2 — `trend`'s lineage is caller-declared and there is no caller

R15 made `trend` take a **caller-declared** `candidate_models` lineage and
forbade inferring it, because stripping a version suffix is exactly the silent
wrong answer. Verified on `main`:

```python
trend(points, *, baseline_model: str, candidate_models: Sequence[str]) -> Trend
```

That ruling was right, and it is the reason C7's lineage test kills a `rstrip`
implementation. But **no chunk says where the caller gets the lineage.** Nothing
on `ReportModel` carries a declared succession, and `RunPoint` deliberately does
not imply one.

So R15 closed the inference door without opening another. Whoever renders the
timeline must declare a lineage, and the plan does not say from what. Candidate
sources, none of them yet chosen: the config, a CLI flag, or an explicit field
in the `migkit.comparison` payload.

**This must be ruled before C22 is dispatched, not during it.** A wiring chunk
that quietly infers the lineage would reintroduce precisely the defect R15 was
written to remove, and it would do it in the one place least likely to be
reviewed — the plumbing.

#### R21.3 — C22, the view model

**New chunk. It blocks C14's remaining seven elements and must land first.**

**Files.** `report.py` (`ReportModel`, `from_evidence`), `tests/test_report.py`.

**Contract.** `ReportModel` gains the fields C14's table is gated on, each
computed from what the model already holds, and each populated in
`from_evidence` on the pattern C10 established for `dimensions`.

The inputs are already present and **no second read of the evidence log is
permitted** — `test_the_log_is_read_once_for_both_the_headline_and_the_series`
and `test_rebuilding_the_report_does_not_hold_the_log_either` are merged and may
not be weakened. Checked, and this is the part that makes C22 small:

- `spot_check(items_passing, items_failing, items_unstable, *, k=12)` takes
  **three integers**. It needs no records and no golden set.
- `trend(points, ...)`, `parameter_strip(previous, current)`,
  `partition_comparable(points, *, against)` and `candidate_field(...)` are all
  pure over `Sequence[RunPoint]`, and `ReportModel.series` is already
  `tuple[RunPoint, ...]`, built by C3 in the existing single pass.

So C22 reads nothing. It is arithmetic over fields the model already carries,
which is why it is a chunk and not a redesign.

**Must not.** Read the evidence log, or any file. Infer the `candidate_models`
lineage (R21.2 — take it from wherever R15's follow-up ruling says, and if that
ruling is not yet in this plan, **stop and report**). Recompute anything
`from_evidence` already computed. Change the meaning of any existing field.

**Failure mode when wrong.** A view model that silently substitutes a default
when a producer returns `None` publishes an empty table as a measured one. Every
one of these producers returns `None` or an empty result for a real reason, and
the reason is the thing the reader needs: C7's first-run marker, C4's
exclusions and C5's caveats all exist because *an absence rendering as a
measurement* is this project's recurring defect.

**Reviewer.** Whether `None` from a producer survives to the template as `None`
rather than as an empty tuple that renders as an empty section. And whether the
single-pass guarantee actually still holds under a test, rather than by
inspection.

#### R21.4 — what this changes about scheduling

C14's remaining seven elements were the next visible work and are now behind
C22. The order is:

1. **Rule R21.2** — where the lineage comes from. Blocking, and mine to answer.
2. **C22**, the view model.
3. **C14's remaining elements**, which then have something to render.

C5 and C6 are unaffected and stay on their own track; C22 should take
`candidate_field` last, or accept that it lands one merge behind the others.

#### R21.5 — ruling: the lineage is declared in config, and assumed out loud otherwise

R21.2 is the blocking question and this closes it.

**Rejected: default the lineage to the headline candidate alone.** It is the
obvious safe answer and it is wrong — it rebuilds the exact defect R15 was
written to remove. R15's finding was that `trend` filtering on a single
`candidate_model` *"is what made the change invisible"*: the moment the model
changes, which is the event the chart exists to show, the points fall out of the
filter. A one-model default reproduces that on every log the config does not
cover, which is every log today.

**Rejected: infer the lineage from the model ids.** Forbidden by R15 and it
stays forbidden. Stripping a version suffix is the silent wrong answer, and C7's
lineage test — ids differing *only* in their suffix — exists to kill exactly
that implementation.

**Ruling, two parts.**

1. **Declared, when the config declares it.** The lineage is a list of candidate
   models in the config, in succession order. `ReportModel` already carries
   `config_path`, `thresholds` and `threshold_sources`, so a declaration has a
   home, a provenance trail and a review path, and it is versioned with the
   thing it describes. This is the caller-declaration R15 asked for, and where
   it is present `Trend` raises no caveat about it.

2. **Assumed, and said out loud, when it does not.** Absent a declaration, the
   lineage is **every distinct candidate model in the series, in
   first-appearance order**, and `Trend` carries a caveat recording that the
   succession was *assumed from the log and not declared*.

Part 2 is not inference in R15's sense and the distinction is the whole ruling:
nothing reads the *shape* of an id. It is a policy — *treat the candidates in
one log as one succession* — which is a claim that can be wrong (two unrelated
candidates measured into one log) and is therefore exactly the kind of claim
this document makes visible rather than silent.

`Trend.caveats` already exists and is the natural home, which is a mild sign the
shape is right. It also means the assumption reaches the page through machinery
that is already reviewed, rather than through a new disclosure path.

**Why not simply require the declaration.** Because the failure mode of a hard
requirement is a report that refuses to draw its timeline until someone edits a
config, and the reader loses the chart to protect them from a caveat. This
project has ruled the same way twice already — C7's first-run marker and C4's
exclusions both chose *render it and name the doubt* over *withhold it* — and
consistency here is worth more than a marginal safety gain.

**Consequence for C22.** It takes the lineage from config when present and
otherwise assembles it in first-appearance order, and it must **not** be the
place the caveat is invented: the caveat belongs to `trend`, beside the other
things `Trend` already says about its own points. If `trend` as merged cannot
raise it, that is a C7 follow-up and C22 stops and reports rather than
compensating in the wiring. **Plumbing that quietly patches a producer's honesty
is the one shape of this defect nobody would find**, because no reviewer reads
the wiring for claims about the data.

#### R21.6 — counted, not argued: the report imports three names from `series`

R21.1 is an argument from two contract clauses. This is the measurement, taken
on `main` at `4ddd07e`, and it settles it.

**Every production caller of C4's, C7's and C11's work, outside `series.py`
itself:**

```
$ grep -rn "spot_check|candidate_field|parameter_strip|correct_field|
            partition_comparable|trend(" src/ --include=*.py
          | grep -v src/model_migration_kit/series.py
(no matches)
```

**Everything `report.py` takes from `series.py`:**

```python
from .series import RunPoint, SeriesBuilder, parse_created   # report.py:190
```

Three names, all of them C3's. `SpotCheck`, `Trend`, `Succession`,
`ParameterChange`, `Partition`, `Exclusion`, `Caveat`, `ComparabilityKey` and
every function that builds them are exported from `series.py` and **imported by
nothing**. They are reachable only from `tests/test_series.py`.

So the position is not that six elements are awkward to render. It is that
**four merged chunks — C4, C7, C11, and C21's counting work before C10 wired
it — produce values no production code path ever reads.** They are exercised
exclusively by their own tests, which is why every gate stays green and why the
rendered document is byte-identical across three of those merges.

This also settles what C22 is worth. It is not plumbing tidy-up: it is the
single edit that connects roughly two thousand lines of reviewed, tested,
merged work to the artifact, and until it lands the honest description of those
chunks is *written and verified, not shipped*.

**And it explains the byte-identical render**, which is worth spelling out
because that symptom is overloaded on this project: a render that does not move
is *also* the symptom of the `PYTHONPATH` trap, where a bare `migkit demo`
silently renders the main checkout's code. Two very different causes, one
identical observation. The way to tell them apart is to print
`model_migration_kit.__file__` before believing either — and after C10 the
correct render genuinely is unchanged, which is the first time on this plan that
"nothing moved" has been the right answer rather than a mistake.

### R22 — D7 is withdrawn, and two rulings from one review contradicted each other

C5 is merged (`03d979d`, 2052 passing, seven gates green) and has now been
through all four roles. Its fix pass acted on every ruling but one, and **it was
right to refuse that one.**

#### R22.1 — the refusal

Ruling 8 told the fix pass to document that
`CandidateField.baseline_pass_rate` **can never be `None`** from
`candidate_field`, on the reviewer's D7 reasoning: every rendered point has
passed `_ungraded`, which requires `judged_baseline > 0`, so the R17.2 zero
guard is unreachable and C6 would otherwise write a dead branch.

That was true when the reviewer wrote it. **Ruling 4, in the same brief, made it
false.** D4 added a second refusal ground — `_baseline_pass_rate` returns `None`
unless `0 <= judge_failures_baseline <= judged_baseline` — and `_ungraded` does
not screen either bound. Both are reachable, because `_count` passes any JSON
integer through, which is the whole reason D4 was worth acting on.

So after ruling 4, `None` **is** reachable and C6's branch is **live**.

The fix pass did not pick a reading. It wrote the factual sentence — *"`None`
when that run's baseline-side counts do not describe a rate"* — left the
reachability claim unmade, and reported the collision. That is the third time on
this plan an agent has held a correct argument against the orchestrator, and the
second time doing so prevented a docstring that would have taught the next
reader something false.

**Ruling: D7 is withdrawn.** `baseline_pass_rate` is `None` when the baseline
side's counts do not describe a rate. **C6 must handle `None`; the branch is not
dead.** The sentence the fix pass wrote stands as shipped and needs no change.

#### R22.2 — the lesson, which is about how rulings are issued

I issued eleven rulings as a numbered list and treated them as independent. They
were not. Ruling 4 changed the reachability of a value that ruling 8 made a claim
about, and nothing in the process would have caught it: the reviewer found both
defects honestly, each finding was correct in isolation, and the brief presented
them in a table that invites exactly the reading that they can be applied one at
a time in any order.

**A review's findings are not independent, and a set of rulings has to be
checked against itself before it is issued.** The specific check that would have
caught this one: *for every ruling that changes when a value is `None`, empty, or
absent, re-read every other ruling that makes a claim about that same value's
range.* D4 and D7 are both about `baseline_pass_rate`; reading them side by side
takes seconds.

This is the same failure shape as R20.1's fixture monoculture, one level up.
There, a suite could not see a defect because every fixture agreed. Here, a set
of rulings could not see its own contradiction because each was judged alone.
**Correctness in isolation is not correctness in composition** — which is,
uncomfortably, the exact property this plan's whole chunked structure is built
on, and R21 is what that assumption cost at the level of the architecture.

#### R22.3 — what C6 now consumes

`CandidateField` as merged, for the brief that dispatches C6:

- **Eight fields**, not seven — `stale_after_days: float` is last (R20.3).
- **`Candidate.model`** is a property returning `point.candidate_model`. A
  property rather than a field, so there is no second slot to disagree with the
  point. C6's `thresholds` mapping is keyed on model strings, so this is the
  join.
- **`baseline_pass_rate` can be `None`** (R22.1). It is a header and **not an
  operand**: the rows' `delta_pp` values are each against their own baseline,
  and adding the header to a delta is wrong whenever the baseline drifted. The
  public docstring says so.
- **`spread_days` is `None` unless at least two rendered rows carry a date**
  (D3), so a single dated row no longer claims a spread of zero.
- **A drift caveat** appears on the point supplying the header when the rendered
  rows' reconstructed baselines are not all equal (D1).
- **A superseded exclusion** now exists, so a run beaten to its row by a newer
  run of the same model leaves a sentence behind instead of vanishing (D2).
- `delta_pp` and `spread_days` are **deliberately unrounded**, now stated on the
  public attributes rather than only in private docstrings (D5). Assert exact
  floats, not `pytest.approx`.

### R23 — C22 splits, and `excluded` gets exactly one source

Scoping C22 against what is actually merged. Two findings, one of them a
design ruling that would otherwise be made badly by whoever implements it.

#### R23.1 — WRONG, superseded by R26. `spot_check`'s inputs are NOT on the model

> **This section is false and is kept only so the error is legible.** Read R26.
> `ReportModel.item_counts` has no `passing`/`failing`/`unstable` keys; it is
> `{"unit", "per_judge": {...}}`, keyed by judge and then by side. The claim
> below that it was "checked rather than assumed" is itself the error.

R21.3 claimed `spot_check` needs no records and no golden set, only three
integers. Checked rather than assumed:

```python
def spot_check(items_passing: int, items_failing: int,
               items_unstable: int, *, k: int = 12) -> SpotCheck | None
```

and `ReportModel.item_counts` is a mapping whose keys are read by
`_item_counts` (`report.py:2643`) as exactly **`passing`**, **`failing`** and
**`unstable`**. The three inputs are already carried, under the names the
function wants. C22 does arithmetic on fields the model holds and reads nothing,
as R21.3 says.

One decision left for the implementer and worth naming so the reviewer checks
it: `ReportModel.item_counts` is the run's own counts, while `JudgeRow` carries
`items_baseline` and `items_candidate` separately. **Which side the spot check
speaks about must be stated in the sentence it prints**, not left to the
reader — the whole point of this number is that a sceptical reader checks it
first.

#### R23.2 — ruling: `excluded` is `candidate_field`'s, not a second partition

C14's table gives the excluded-runs list its own row, gated on *"any
exclusion"*. The tempting implementation is a fresh
`partition_comparable(model.series, against=...)` call at the top level.

**That is wrong, and it is the `dimension_counts` mistake again.** `CandidateField`
**already carries `excluded`**, produced by the partition that built the
candidate table. A second top-level partition would put the same facts on the
model twice, computed by two calls that can drift apart — and R16.3 already
ruled on exactly this shape when it refused to let `dimension_counts` sit beside
`dimensions`: *"Keeping both would put the same facts on the model at two
fidelities, which is two chances for them to disagree."*

Worse here than there, because the two partitions would be against possibly
*different* keys, so the disagreement would be legitimate on both sides and
impossible to adjudicate from the model.

**Ruling: the rendered excluded-runs list is the candidate field's own
`excluded`.** One partition, one source. The list and the table it explains are
then guaranteed to be about the same set of runs, which is the only way the
section means anything — an exclusion list that does not match the table above
it is worse than no list.

**Known consequence, already flagged and accepted.** C5's reviewer found that a
log of two anonymous runs plus one named candidate returns `None` from
`candidate_field`, and every exclusion sentence computed along the way dies with
it: *the report can never say why there is no table.* Binding `excluded` to the
candidate field inherits that. It is the right trade — a wrong-but-present list
is worse than an absent one — but it means **C14's empty state for this section
must say that runs may have been excluded without being able to name them**,
rather than rendering an empty list that reads as "nothing was excluded".

That is the same distinction C7's first-run marker exists to draw, and C4's
exclusions, and C10's zero column: *an absence must not render as a
measurement.* It is now four chunks in a row, and it is fair to call it this
document's central design rule rather than a recurring coincidence.

#### R23.3 — the split

**C22a — dispatchable now.** `spot_check` and the candidate field
(`candidates` + `excluded`). Both producers, C11 and C5, are through all four
roles with no open rulings. This renders **three** of C14's nine elements: the
spot-check sentence, the candidate table, and the excluded-runs list. It is also
what finally moves *"spot check"* off zero in the measured render.

**C22b — blocked, and on two different things.**

- `trend` and `parameter_strip` wait on C7's review **and** on a C7 follow-up:
  R21.5 requires `Trend` to raise a caveat when the lineage was assumed rather
  than declared, and nothing raises one today. R21.5 forbids C22 from inventing
  that caveat in the plumbing, so it belongs in C7's fix pass, which is coming
  anyway.
- `multiplicity` waits on C6, in flight.

Splitting costs a second pass over `ReportModel` and `from_evidence`, and that
cost is worth paying: the alternative is that nothing renders until C6 and C7's
follow-up both land, which is the scheduling mistake this plan has already made
once and written at the top of its handoff — **order the chunks so the artifact
moves.**

### R24 — rulings on C7's review, and a run that R15 made invisible

C7's reviewer ran 43 mutants; **11 survived both `tests/test_series.py` and the
full 1998-test suite**, and all 11 were confirmed non-equivalent by probe. The
first-run marker ruling came through clean — the mutation I asked for goes red,
and four variants I did not ask for go red too, so R20.2's failure is not
repeated here. Everything below is what the review found underneath that.

#### R24.1 — a run in the log, on the same baseline, that the page never mentions

**The finding.** A run whose `candidate_model` is not in the declared lineage
disappears from `points`, `excluded`, `undated` **and** `caveats`. Probed with
the 14-night lineage declared one character wrong:

```
13 points, 0 excluded, 0 undated, 0 caveats, no succession
the strip reports model_id UNCHANGED, six changed=False rows
```

The reader gets a clean thirteen-night line stating that nothing moved, and
night 14 appears nowhere on the page.

**R15 created this**, and said so without noticing: R15.1 replaced suffix
inference with operator declaration and observed that a wrong split now
*"requires the operator to declare it wrong… precisely the case where a reader
most needs to notice."* `Trend` has no field in which to notice it.

**Ruling: `Trend` gains two fields, and the distinction between them matters.**

- **`outside_lineage: tuple[RunPoint, ...]`** — runs sharing the
  `baseline_model` whose `candidate_model` is not in the declared lineage.
- **`absent_models: tuple[str, ...]`** — declared models with no run in the log
  at all. The one-character typo shows up here, which is the case most likely
  to be an operator error rather than a fact about the data.

**These do not go in `excluded`, and `trend`'s existing docstring is right about
why:** a differently-*based* run "is simply not selected — putting it in
`Trend.excluded` would bury the exclusions that matter under every other
experiment in the log." That reasoning holds for a different `baseline_model`,
which really is somebody else's experiment. It does **not** hold for a run on
the same baseline whose candidate is merely undeclared: that run is in this
comparison family, and its absence from the chart is a claim about the
declaration, not about the run. Keeping the two apart is the whole point.

This is the fifth chunk turning on *an absence must not render as a
measurement*, and the second time R15's own correction created the next
instance of the class it was written to remove.

#### R24.2 — `_anchor` has three rulings in its docstring and no tests

All four `_anchor` mutants survive, all four are genuine divergences, and A1 is
the worst thing in this review after R24.1:

| | shipped | mutated | what the reader sees |
|---|---|---|---|
| **A1** the newest run anchors | 13 nights drawn, night 14 excluded | **1 point, 13 exclusions** | a single dot where a fortnight's line belongs; the newcomer evicts its own history |
| **A2** the `is_identifying` skip removed | night 1 excluded, 2–4 drawn | **nothing drawn**, 4 exclusions | an empty chart with four refusals where three nights agreed |
| **A3** undated points rank first | 3 dated drawn | **nothing drawn** | a run with no timestamp silently defines the axis for the whole line |
| **A4** dates ignored entirely | same | same as A3 | the line changes when `read_series` changes its read order |

A1 inverts `_anchor`'s own stated principle — the established series keeps the
axis — and nothing tests it. **Each of the four gets a test.**

#### R24.3 — the two absence words, and the marker that could re-fuse them

`_NO_PREVIOUS_RUN = "no previous run recorded"` **survives all 1998 tests.** The
suite asserts the marker differs from `_UNRECORDED`, but nothing keeps it out of
the *"recorded"* vocabulary — so a marker satisfying every assertion can still
print both absences as the same idea, which is exactly what the ruling forbade.
**One assertion closes it: `"recorded" not in marker.lower()`.**

#### R24.4 — C5 and C7 rank undated runs in opposite directions, and both are right

C5 ruled dateless rows sort **oldest**; C7's `_anchor` ranks undated runs
**after every dated one**. Neither module's docstring acknowledges the other,
which reads like a contradiction and is not one:

- C5's question is **display order in a table**, where the reader can see the
  blank `stale_days` cell and position carries no ranking claim.
- C7's question is **which run defines the axis**, and A3 shows what happens
  when an undated run wins it: the whole line vanishes.

*Sort it oldest; never let it anchor.* **No behaviour changes. Both docstrings
must cross-reference the other**, because the next reader who notices the
asymmetry will otherwise "fix" one of them.

#### R24.5 — a docstring that invites the tidy that breaks the code

`_Cell` states: *"`value` is `""` exactly when the run recorded nothing… so one
emptiness test decides for hashes, ids and counts alike."* **False for a
whitespace-padded field**, where `value` is `"   "` and the run recorded
nothing. The shipped code is safe because `_parameter_change` guards with
`_recorded(...)` rather than `== ""` — but the sentence tells the next reader
the two are interchangeable, and survivor C6 means the suite would not object to
the swap. Probed: it turns a padded hash against a real one into
`changed=True, "judges changed"` **from a padding artifact**.

Same root: `ParameterChange.before`'s *"Never `""`"* is true and is the wrong
guarantee — the failure mode is a *blank* cell, and `"   "` satisfies the
docstring while failing the intent. Fix both sentences, and fix C6 with them.

Also: `trend`'s caveats paragraph still **leads** with the reason `0b84d52`
retracted before giving the corrected one. The leading sentence is true and does
no work, and it is the sentence a tidier keeps.

#### R24.6 — two rulings for C14, taken now because renames get expensive

1. **`_UNRECORDED` and `_NO_PREVIOUS_RUN` become public.** A template that wants
   to style a first-run cell differently from an unrecorded one currently has
   only two options: import a private name, or hard-code the literal — which the
   constant's own docstring forbids. `report.py` already sets the opposite
   precedent deliberately (`THRESHOLD_SOURCE_UNRECORDED`,
   `INTERVAL_BAR_NO_RATE`), and R7 ruled the general case: *import the constant,
   never hard-code its value.* Promote both **before C14 types against them**.
2. **`ParameterChange.name` for the golden set becomes `goldenset`**, without a
   space. Five of the six names are identifier-safe and exactly one is not, so a
   template deriving a CSS class, an anchor id or a dict key from `row.name`
   breaks on one row in six. The contract fixed these strings, so this is a
   ruling and not a fix: **the display label is the template's job**, which is
   where labels belong. `ParameterChange` stays at four fields.

`Succession.created` stays a raw string — `parse_created` is public, so the
caption parses it. Say so in the field comment, which currently justifies the
field by *not* indexing back into `points`.

#### R24.7 — the monoculture here is pairwise

R20.1 has been about a single field held constant. C7's fixtures vary nearly
every field individually; what they never do is **combine** two. No C7 fixture
has undated *and* a caveat (M4a), undated *and* an exclusion (M5a), an unsorted
input *and* a succession (C13), a key disagreement *on the oldest or newest*
point (A1), or a whitespace-only value anywhere in the file (C6).

**So R20.1 needs widening: a fixture set can vary every field one at a time and
still be a monoculture in pairs.** The survivors here are made of exactly that,
and the cheapest defence is to ask, for each pair of conditions the code branches
on, whether any single fixture carries both.

### R25 — R17.1's mechanism was wrong, and both C6 agents caught it independently

C6 is merged (2083 passing, seven gates green) and its blind pair produced
**zero disagreement** for the third chunk running — all 31 of the tester's tests
pass against the implementer's code.

Both of them, separately and without seeing each other, reported that R17.1's
explanation of *why* the contract's `changed` rule fails is factually wrong.
They are right.

#### R25.1 — what R17.1 claimed, and what actually happens

R17.1 says: *"For every candidate after the stop, the returned threshold is the
uncorrected `alpha` itself — so `p >= threshold` is vacuously false and the
candidate silently drops out."*

`holm_bonferroni` does not do that. Every position is overwritten in the loop
with `alpha / (k - rank)`; the `[(False, alpha)] * k` initializer never
survives. Measured on the merged implementation:

```
holm_bonferroni([0.03, 0.04, 0.045], alpha=0.05)
  -> ((False, 0.01667), (False, 0.025), (False, 0.05))
holm_bonferroni([0.001, 0.002, 0.049], alpha=0.05)
  -> ((True,  0.01667), (True,  0.025), (True,  0.05))
```

The stop in the first family is at rank 0, yet the two candidates "after the
stop" carry `0.01667` and `0.025`, not `alpha`. The threshold sequence is
identical in both families and is **independent of whether anything rejected**.

#### R25.2 — the real mechanism, which is stronger

The **largest** p-value in any family is always tested against `alpha / 1`,
which *is* `alpha`. So for that candidate `p_value >= holm_threshold` is false
whenever `p_value < alpha` — **in every family, whether or not a step-down stop
occurred.**

The contract's rule therefore misses the largest sub-alpha p-value *always*,
not merely after a stop. R17.1's ruling and its worked table were right for a
reason it did not state, and its stated reason would let the next reader
conclude the defect is conditional on a stop and stop looking. That is worse
than an incomplete explanation: it is a wrong one that survives the cases most
likely to be checked.

**The ruling is unchanged:** `changed` is `p_value < alpha and not rejected`,
with `rejected` from `holm_bonferroni`'s own return, and a p-value is never
compared against the returned threshold to decide significance. The threshold
is diagnostic output for display.

The merged code carries the accurate mechanism in `Multiplicity.changed`'s
docstring, and the tester pinned it as an invariant in
`test_the_largest_sub_alpha_p_value_is_named_as_changed_although_its_threshold_
is_alpha_itself` — `thresholds[largest] == alpha` **and** that model is in
`changed`, which is exactly the pair the broken rule cannot produce.

#### R25.3 — why two blind agents caught it and I did not

Both derived the thresholds from the merged `holm_bonferroni` rather than from
this plan: the implementer probed it, the tester computed expected values from
`alpha / (k - rank)` to write its fixtures. Neither could have taken my sentence
on trust even if it had wanted to, because both needed the actual numbers to do
their jobs.

I wrote R17.1 from reading the function and reasoning about the initializer
without running it. The worked table in R17.1 *is* real output — I ran the
comparison — but I explained it with a mechanism I had inferred rather than
measured, and the two agreed on the conclusion, so nothing forced them apart.

**A worked example that confirms the conclusion does not confirm the
explanation.** The output was right, the reasoning was wrong, and only an agent
that needed the intermediate values found the gap. That is the same shape as
R20.1 in a different medium: agreement between two things that were never
independent is not evidence.

#### R25.4 — five things C6's contract does not say

Both C6 agents flagged the first of these; the tester listed all five and
deliberately wrote no assertion that picks a reading on any of them. **Nothing
below blocks the merge** — the implementer chose defensibly and the tester's
assertions hold either way, which is why the pair still agreed. They need
ruling before anything consumes `Multiplicity`.

1. **What the returned `CandidateField` differs in.** The contract says
   `correct_field` returns one and never says what changes. Verdicts, deltas,
   spread and header are all off-limits or unaffected, leaving `caveats`. The
   implementer appends one `Caveat` per candidate in `changed`, on that
   candidate's own point, naming the recorded verdict and the second-correction
   fact, preserving existing caveats unedited, and returns `field` itself
   unchanged when the correction changed nothing or was refused. **Ruling: this
   stands.** C5's own `caveats` docstring says a caveat that reaches nobody is
   the same as one never computed, and R17.3 ruled that shape. Record it in the
   contract rather than leaving it as silence.
2. `Multiplicity.alpha` when refused for *differing* levels — unstated.
3. Whether candidates with `p_value is None` contribute their alpha to the
   uniformity check — unstated.
4. `thresholds` when `applied=False` — unstated.
5. `family_size` when no candidate was tested at all — unstated.

Items 2 to 5 are open. They are cheap now and expensive once C22b renders this.

#### R25.5 — a sixth refusal the contract does not list

`holm_bonferroni` raises `ValueError` unless `0.0 < alpha < 1.0`, and `RunPoint`
is a public frozen dataclass anyone may construct, so `alpha=5.0` or `alpha=nan`
reaches `correct_field` without passing through `_number`. The module's stated
hard rule is that nothing here raises. The implementer refuses with
`applied=False` and a note naming the level. **Correct — keep it**, and add it
to the contract's refusal table so it is not read as an invention.

Related and also right: levels are compared as `repr` strings rather than
floats, because `nan != nan` would otherwise report a family of two identical
NaN levels as "tested at different levels: nan, nan".

### R26 — R23.1 was wrong, and I made the same mistake twice in an hour

C22a's implementer delivered `candidates` and **stopped on `spot_check`**,
reporting that its contract does not hold against the code. It is right, and it
was right to stop.

#### R26.1 — the error

R23.1 claimed, in those words, *"Checked rather than assumed"*:

> `ReportModel.item_counts` is a mapping whose keys are read by `_item_counts`
> (`report.py:2643`) as exactly `passing`, `failing` and `unstable`. The three
> inputs are already carried, under the names the function wants.

**Measured on a freshly generated demo log:**

```python
item_counts = {"unit": "item",
               "per_judge": {"accuracy": {"baseline":  {"passing": 11, "failing": 1, "unstable": 0},
                                          "candidate": {"passing":  9, "failing": 3, "unstable": 0},
                                          "items": 12}}}
```

`model.item_counts["passing"]` raises `KeyError`. Three independent
confirmations: the producer is `comparison._item_counts_by_judge`, which returns
exactly `{"unit", "per_judge"}`; `report.py:1299` passes it through verbatim; and
`_item_counts` at `report.py:2643` is **never called with
`model.item_counts`** — its only call sites are `judge.items_baseline` and
`judge.items_candidate` (`report.py:2577-2578`) and the `counts` Jinja filter.

What I actually did: I grepped for `item_counts`, found a function reading
`passing`/`failing`/`unstable`, and concluded it read the model field of the
same name. I never called the function or printed the field.

#### R26.2 — the same mistake as R25, one hour apart

R25 recorded that R17.1's mechanism was inferred rather than measured, and that
a worked example confirming the *conclusion* does not confirm the
*explanation*. R23.1 is the identical failure with the identical signature: a
real observation (the function does read those keys), an inferred connection
(therefore the field has them), and a confident sentence claiming verification.

**Twice in one hour, and the second time immediately after writing down the
lesson from the first.** Writing the lesson down is not the same as applying
it, and the tell is available in both cases: I described the check in prose
instead of pasting its output. R20.1's rule for fixtures generalises here.

> **When a revision claims something was checked, it must carry the output that
> checked it.** A sentence saying "verified" is not evidence; a pasted
> `KeyError` or a printed dict is. Every claim of the form "X already carries
> Y" needs the two lines that produced it, or it is an inference wearing a
> verification's clothes.

Both errors were caught by agents rather than by me, and in both cases for the
same reason: **the agent needed the actual value to do its job and so could not
take my sentence on trust.** That is an argument for briefs that require an
agent to derive a number rather than accept one.

#### R26.3 — ruling: which judge, and which side

The error turned one decision into three, two of which nobody had named.

**Which judge: `judges[0]`, the counting judge, and refuse rather than
aggregate.** `item_counts["per_judge"]` is keyed by judge name and the panel
case is real. C10 already chose `counting_judge = judges[0].name`
(`report.py:1213`) for the tag matrix; **one document must not select its
judge two different ways**, and a second rule would be a defect waiting for the
first panel whose judges disagree. Summing across a panel is separately wrong
for `_per_judge_counts`' own stated reason: two judges grading the same 60
completions are 120 records and 60 completions.

**Which side: the candidate.** `SpotCheck`'s docstring says the number exists to
say *what a cheaper method would have missed, which is the argument for having
run the harness at all.* The failures that argument is about are the ones that
bear on the decision, and those are the candidate's. The baseline's failing
items are context for the comparison, not the thing a hand check would have been
run to catch.

#### R26.4 — ruling: the sentence, and a contradiction that was mine

C22a's brief demanded that the printed sentence name which side it speaks
about, **and** forbade editing a producer. Those cannot both be satisfied:
`spot_check` takes no label, and `SpotCheck.sentence` is composed inside
`series.py` —

> *"A 12-prompt spot check drawn at random from these 96 items, 8 of which
> failed, would have shown no failures at all in 33% of such checks."*

— with no side, no judge, and no parameter that could carry one. The only ways
to satisfy the brief were to edit `series.py` (forbidden outright) or to build
the prose in `report.py`, which is inventing a producer's sentence in the
plumbing — the exact shape R21.5 refused for the `trend` caveat.

**Ruling: a C11 follow-up, on R21.5's precedent.** `spot_check` gains a
caller-supplied subject that its sentence names, so the sentence stays written
where the number is computed. The renderer must **not** caption around it. This
is the second time a chunk's honesty obligation has had to go back to its
producer rather than be satisfied downstream, and the consistency is the point:
if plumbing may compose a producer's prose once, the rule is gone.

`C22a`'s `candidates` half is complete, green and independent of all of this,
and merges on its own.

#### R26.5 — and R23.3's headline claim was also false

R23.3 said C22a "is what finally moves *spot check* off zero in the measured
render." **It is not, at the bundled demo's defaults.** The golden set is 12
items and `k` defaults to 12, so `items <= k` and `spot_check` correctly returns
`None` on both sides:

```
spot_check(9, 3, 0)   -> None      # candidate
spot_check(11, 1, 0)  -> None      # baseline
spot_check(9, 3, 0, k=6) -> SpotCheck(..., probability=0.0909, '...9% of such checks.')
```

So after every ruling above is implemented, the demo will **still** render no
spot-check sentence. That absence is correct behaviour and will read exactly
like a wiring bug — the third distinct cause on this project of "the number
didn't appear", after the `.pth` trap and the missing view model.

It also raises a product question this plan has not asked: `k` defaults to 12
while the bundled golden set is 12 items, so the headline demo can never show
the number the chunk exists to produce. Flagged, not ruled — but whoever
schedules C14's spot-check element should know the section will be empty on the
demo unless `k` or the demo set moves.

### R27 — rulings on C10's review: eighteen survivors, and a test satisfied by prose

C10's reviewer ran ~50 mutants: 25 killed, **18 surviving mutants that change
behaviour**, 5 equivalent. Every survivor was re-confirmed against the **full
1998-test suite**, not just C10's 22. The chunk's *code* is right almost
everywhere; what is missing is anything that would notice if it stopped being.

#### R27.1 — the confidence and floor provenance: right in the code, pinned by nothing

My ruling 1 was met. `report.py:1250-1254` threads
`confidence=_number(thresholds.get("confidence"))` and
`floor=_number(thresholds.get("pass_rate_floor"))` through to `dimension_cell`;
probed cells carry `floor=0.87` and a Wilson interval at **0.99**, and an absent
confidence stays `None` so the cell discloses rigor's default itself. The double
application really is avoided.

**Six mutants survive all 1998 tests**, and each publishes a false document:

| Mutant | What the page would say |
|---|---|
| `confidence=None` | interval widens to (0.886, 1.0), **and** every cell gains *"No confidence level was given, so rigor's default of 95% was used"* — a printed disclaimer that is false about a run recording 99% |
| `floor=None` | the floor column empties; a document that refuses a cell can no longer say what it refused against |
| confidence/floor swapped | `floor=0.99`, interval at 87% → (0.929, 1.0). Both wrong, both plausible, neither flagged |
| confidence defaulted in `report.py` | the exact double application the ruling forbids |
| floor from `min_detectable_effect` | cells refuse against **0.13** while the gate used 0.87 — two floors in one document |
| `_number()` dropped | a string threshold reaches `wilson_interval` unconverted |

**Ruling: tests are owed.** Assert `cell.floor == 0.87`, that `cell.interval` is
Wilson-at-0.99 and **not** 0.95, and add a no-confidence fixture asserting the
disclosure sentence appears **exactly once**. The tester was right that it could
not assert this without inventing a requirement; the requirement now exists.

#### R27.2 — a test satisfied by its own file's prose

`test_the_report_module_names_the_untagged_sentinel_rather_than_typing_it` is
`"UNTAGGED" in inspect.getsource(report)`. The reviewer replaced **every
executable use with `""` and deleted the import** — the test still passed, and
so did all 1998.

It is not "satisfiable by a comment", as I guessed when I ruled. **It is
currently satisfied by docstrings that would survive the regression untouched.**
Adopt the reviewer's verified replacement:

```python
tree = ast.parse(inspect.getsource(_module()))
reached = any(
    (isinstance(n, ast.Name) and n.id == "UNTAGGED")
    or (isinstance(n, ast.Attribute) and n.attr == "UNTAGGED")
    for n in ast.walk(tree)
)
```

Matching only `Name`/`Attribute` excludes the `ImportFrom` alias, which is what
makes it reject *"import it and type `""` anyway"* while accepting both forms
the contract allows.

**The general lesson is worth more than the fix.** A test that greps a module's
source cannot distinguish code from commentary, and this project writes long
docstrings — so source-text assertions are *systematically* weakest here, in
proportion to how well the code is documented. Parse, do not grep.

#### R27.3 — sorted order confirmed, and the contract's phrase corrected

Golden-set **file** order is not reachable from any input `report.py` has:
`GoldenSet.stats()` returns `dict(sorted(...))` (`goldenset.py:165`) and the
counter's inner mapping is `sorted(index.tags)`. The counter also **zero-fills
the whole tag universe** — a `ghost` tag carried only by never-judged items
still gets a `(0,0,0)` cell — so `matrix.tags` is exactly *golden-set tags +
`UNTAGGED`*.

**The contract's phrase "golden-set tag order" becomes "alphabetical, `UNTAGGED`
last."** No discriminating fixture is needed for file order, because a
regression to it is unimplementable.

But **M24 survives**: deleting `_matrix_tags`' own `sorted()` and taking the
counter's key order is invisible *only because `dimensions.py` happens to sort*.
Nothing pins that `report.py` orders for itself. A `zeta`/`alpha` fixture closes
it.

#### R27.4 — three fixture and coverage gaps, all one shape

- **`TagColumn.cell()` is never verified for identity.** Returning the first
  cell whose tag does *not* match survives all 1998 tests, because every fixture
  gives both tags identical counts. **This is C5's M01 exactly** — a fixture set
  that hard-codes one value everywhere cannot tell the correct computation from
  the broken one. Per-tag-distinct counts close it and two near-equivalent
  survivors with it.
- **No 3-model fixture exists anywhere.** `candidates` is a 1-tuple in every
  test, so its plurality is untested and both "reverse candidate order" and
  "make the extra models non-deterministic" survive.
- **`candidates` as a tuple is pinned only incidentally.** Regressing it to
  `Mapping[str, TagColumn]` survives all 22 of C10's tests — section 21's own
  helpers branch on `isinstance(..., Mapping)` and reach through it. It dies only
  in **section 20**, and only because `column()` unpacks a dict to its keys and
  crashes on `str.model_id`. A crash is not an assertion; refactor `column()` and
  the hazard reopens silently. Assert the shape directly.

#### R27.5 — the zero column: right note, missing distinction

Three genuinely different situations render **byte-identically** — a judged side
that produced nothing, a side the payload names that the counter never saw, and
a golden-set tag no model produced. All give `(0,0,0)`, `rate=None`,
`verdict_refused=True`, note *"Nothing was measured for X."*

**The note is right** — it says nothing was measured, not a measured zero. That
trap is cleanly avoided, and it is the one that matters most.

The gap is that cases 1 and 2 have **different fixes** (check the judge
configuration vs. check whether the run completed at all), the matrix carries
nothing to separate them, and `available=True` in both, so the table looks
confident. Case 2 is the implementer's own extension and has **no test at all**:
M6 — drop the never-seen side entirely, leaving a one-column "comparison" with
nothing on the page saying where the other side went — survives all 1998 tests.

**Ruling: test case 2, and give the two a distinguishable note.** Not a new
field: the note is already the place this document says such things.

#### R27.6 — the decline assertion is tautological

`_counter_reason` derives the expected sentence by running **the same production
code path**, so a re-wording at the source moves both sides together. M40
re-words `_unjoinable` in `dimensions.py` and survives all 1998 tests;
`test_dimensions.py` only checks a keyword substring.

The report side *is* pinned against re-wording **in `report.py`**, which is the
part C10 owns, and that is genuinely worth having. But "byte-identical to their
source" is only ever "identical to whatever the source now says". Worse, the
distinctness assertion checks six distinct **strings**, not six distinct
**diagnoses**: collapsing `_unknown_item` onto `_unjoinable`'s sentence keeps the
strings unequal, because the interpolated ids differ.

**Ruling: assert the diagnoses, not the strings** — that no two declines share a
*template* — and leave the wording pinned where the wording lives, in
`test_dimensions.py`. A cross-module byte-assertion would just move the tautology.

#### R27.7 — two docstrings describing code that is not there

- `_matrix_tags` argues the alternative "would disagree with the columns the
  moment the two came from different golden sets." In `from_evidence`, the only
  caller, both come from the same `view.update(...)` of the same `loaded` object
  (`report.py:1581-1593`). **They cannot.** The first reason the same docstring
  gives is real and sufficient; delete the second.
- `_tag_column` claims the published floors and the refused-against floors "are
  one expression rather than two that agree today." They are **three**, each
  independently naming the module constants (`1728-1729`, `1767-1768`,
  `1791-1792`). The docstring describes the design it was meant to produce.
  Thread them through one local and the sentence becomes true.

Both are the C5-D6 shape: **a docstring reasoning from a state the code cannot
reach teaches the next reader something false.** Third chunk running.

#### R27.8 — for C14

1. **`matrix.candidates[0]` is the comparison's candidate by construction** and
   pinned by nothing. **Pin the order** with the 3-model fixture rather than
   adding a `matrix.candidate` accessor — a second way to name one side is a
   second thing to disagree.
2. `cell(tag)` never returns `None` for a tag in `matrix.tags` (the counter
   zero-fills), so C14 needs no null guard — after R27.4's identity test.
3. `available=True` guarantees `judge != ""`.
4. `matrix.tags` is a tuple while `goldenset["tags"]` is a dict — similar names,
   different shapes. Say so where a template author will look.
5. **C14 cannot today distinguish a judged-but-silent side from a never-judged
   one.** If the page is meant to, R27.5's note must carry it.

### R28 — a ruling recorded is not a ruling scheduled

Found by auditing my own revisions against the code rather than against my
memory of them.

#### R28.1 — R21.5 was ruled and never scheduled

R21.5 ruled that when no config declares `trend`'s lineage, the lineage is
assembled from the log in first-appearance order and **`Trend` carries a caveat
saying the succession was assumed rather than declared**. RESTART has listed it
as blocking C22b ever since.

**It was never implemented, and it was never in any brief.** Verified against
the merged code:

```
Trend fields: ('points', 'successions', 'excluded', 'undated',
               'caveats', 'outside_lineage', 'absent_models')
trend() source, occurrences of 'assumed': 0
                              'first-appearance': 0
                              'config': 0
```

`outside_lineage` and `absent_models` are R24.1's, from C7's fix pass. C7's fix
brief covered R24 and nothing else. I ruled R21.5 four hours earlier, wrote it
into the plan and into the handoff's blocking list, and then wrote a brief that
did not mention it.

**The failure is a missing step, not a missing thought.** Every ruling on this
project goes: rule it → write it into the plan → *carry it into a brief*. The
middle step feels like completion because the plan is where rulings live, and it
is not: **a ruling in the plan with no brief behind it is a ruling nobody will
execute.** The handoff even listed it as blocking, which made it look tracked.

The check that catches this is cheap and is now standing: **before dispatching
any brief, grep the merged code for the ruling it is supposed to implement.** If
the ruling's own words appear nowhere, it has not been done, whatever the plan
says. That is the same rule as R26.1 — *a claim that something was checked must
carry the output that checked it* — applied to my own scheduling instead of to
my own facts.

Dispatched as a C7 follow-up, on the C11 `SpotCheckSubject` precedent: the
caller supplies the fact, `series.py` supplies the words.

#### R28.2 — C6's four open contract points, now ruled

R25.4 listed five things C6's contract does not say. Item 1 was ruled there (the
returned field gains one `Caveat` per changed candidate). **These four were left
open and are now blocking C22b**, which must render `Multiplicity`.

1. **`Multiplicity.alpha` when the correction is refused for *differing*
   levels.** Ruling: **`None`.** `alpha` is documented as the *family-wise*
   level, and where members were tested at different levels there is no such
   thing — publishing either one names a level the family does not have. The
   `note` already names both, which is where the detail belongs.
2. **Whether candidates with `p_value is None` contribute their alpha to the
   uniformity check.** Ruling: **no.** An untested candidate has no p-value to
   correct, so it is not in the family; letting it veto the correction would
   refuse a well-formed family on the strength of a row that contributed
   nothing. `family_size` already counts only tested candidates, and this is the
   same principle applied one field over.
3. **`thresholds` when `applied=False`.** Ruling: **empty.** A threshold is the
   output of a correction that did not happen, and C6's own reviewer note names
   the mirror hazard — `applied=True` with empty `thresholds` is the overclaim
   the chunk exists to prevent. The converse is the same defect facing the other
   way: numbers on the page implying a correction was applied.
4. **`family_size` when no candidate was tested at all.** Ruling: **`0`**, with
   `applied=False`. Zero tested candidates is a family of zero, and the note
   must say how many were untested — which the contract already requires, and
   which is the only thing that stops `0` reading as "no candidates existed".

All four follow one principle worth stating once: **a refused correction must
not leave behind the furniture of an applied one.**

#### R28.3 — the sixth refusal belongs in the contract's table

R25.5 endorsed the implementer's sixth refusal — `holm_bonferroni` raises unless
`0.0 < alpha < 1.0`, and `RunPoint` is a public frozen dataclass anyone may
construct, so `alpha=5.0` or `alpha=nan` reaches `correct_field` without passing
through `_number` — and said it should be added to C6's refusal table "so it is
not read as an invention". **That edit was not made either**, which is R28.1's
shape a second time in the same session.

It is recorded here instead, and C6's contract table should be read as having a
seventh row:

| Input | Required |
|---|---|
| `alpha` outside `(0.0, 1.0)`, including NaN | `applied=False`, note names the level. The module's hard rule is that nothing here raises. |

Also endorsed and worth keeping: levels are compared as `repr` strings rather
than floats, because `nan != nan` would otherwise report a family of two
identical NaN levels as "tested at different levels: nan, nan".

### R29 — C18's three unimplementable clauses, and a false sentence already shipping

C18's implementer shipped one clause and stopped on three, reporting that each
needed a reading it was not entitled to choose. It was right on all three, and
it found a defect nobody had asked about that is live in the rendered document
today.

Shipped and merged: `render_html_string` replaced the `<title>` outright, so the
`FAKE MODELS` prefix vanished from a **contracted disclosure surface** — one of
the five the spec names, "and none of them is a footnote" — while the body band
stayed. One argument removed it. Now guarded.

#### R29.1 — the defect that is shipping: a headline sentence making a series claim

With a real headline over a scripted history, the methodology paragraph
(`report.py:2255`) prints, verbatim:

> These numbers describe scripted responses, not a real provider. At least one
> side of **this comparison** was produced by a Fake adapter
> (**AnthropicAdapter** for the baseline, **OpenAICompatAdapter** for the
> candidate).

**Both named adapters are real.** The sentence is *headline*-scoped while
`is_demo` is *series*-scoped, so it makes a false claim about the comparison in
front of the reader and never states the true one — that the history behind it
was scripted. Reproduced against the current build.

This is worse than an absence rendering as a measurement: it is a **disclosure
that discloses the wrong thing**, and it appears in the paragraph a sceptical
reader goes to first. **Ruling: the sentence must say what `is_demo` actually
measured.** When the headline is fake, name the headline's adapters as now. When
the headline is real and the *history* is scripted, say that, and name no
adapters as evidence — the evidence is in runs the sentence is not about. The
two cases are different sentences, not one sentence with a variable in it.

#### R29.2 — clause 1 is unsatisfiable as written; the escape is a third state

C18's contract says the band must not be defeatable "by an empty adapter
string". Verified by the implementer: blank both sides' adapters in the payload
**and** delete the run artifacts (the artifact wins at `report.py:1918`), and a
fully scripted demo renders as a clean report with a verdict and pass rates.
§5.3's claim is broken.

But `test_a_series_of_real_runs_does_not_band_the_report`, parametrized
`("", "")`, asserts exactly the opposite for exactly that input, on C3's
reviewer note. And the two inputs are **byte-identical in the evidence**: a
scripted run with its adapter blanked and a real run whose adapter was never
recorded produce the same log. No implementation satisfies both, because the
distinction the contract demands is not in the data.

**Ruling: a third state — provenance not recorded — which is neither "scripted"
nor "real".** This is the document's own central rule applied to its most
important disclosure: an absence must not render as a measurement, and a silent
report is currently asserting *real* on the strength of nothing. It also
survives the merged test, which asserts only that the **fake** markers are
absent, so C3's reviewer note is honoured rather than overridden.

The four things the contract never decided, decided:

1. **Wording** — a band saying the adapters were not recorded, so the report
   cannot say whether these numbers came from a real provider or a script. It
   states the gap, and claims nothing on either side.
2. **The `<title>`: no.** The prefix is reserved for the positive claim *these
   are fake*, and unrecorded provenance is not that claim. Prefixing it would
   band every legacy log that predates adapter recording, which trains readers
   to ignore the prefix — and a disclosure readers learn to skip is worse than
   one that is merely absent.
3. **`render_terminal`: yes.** The terminal and the HTML must say the same
   words; that discipline is why `DetailBudget.sentence` and C6's `note` are
   written where their numbers are computed.
4. **One side unrecorded** — if either side is *fake*, the fake band wins,
   because that is a positive finding and outranks a gap. Otherwise, if either
   side is unrecorded, the unrecorded band shows and names **which side**.

#### R29.3 — clause 2: detect the asymmetry, or say nothing

The C17 timestamp asymmetry has no trigger in the repo: `build_showcase` does
not exist, and it is invisible from the series because `series.py:1674`
`_created` returns the payload's `created` and **discards the envelope `ts`**,
keeping only a `created_source` label.

**Ruling: take the implementer's recommendation.** Detect it in `report.py` from
the comparison records' own `record.ts` against `payload["created"]`, with the
threshold stated as **different UTC calendar dates**, and **say nothing when
they agree**. Do not touch `series.py` — it is outside C18's declared files, and
a second report-local accumulation is the cheaper of the two.

Unconditional prose is refused: it is **false on `migkit demo`**, whose
comparison `created` and envelope `ts` are the same instant. *An asymmetry
asserted where none was measured is this document's rule inverted* — and it
would be inverted inside the very chunk whose subject is unsuppressible honesty.

#### R29.4 — clause 3: count comparisons, never runs

`ReportModel.series` is one point per comparison, each naming two adapter
strings, and `RunPoint` carries no run id or artifact path to dedupe by. In the
showcase shape a night is 4 runs but 3 points × 2 sides = **6 adapter
mentions**, so "how many of the runs" would render 84 for a 42-comparison
document over 56 actual runs.

A true run count is reachable from `migkit.run_started`, which does carry
`adapter` — but a resumed run writes a second one, and a log produced by
`compare` from two artifacts has none, so the count would be **absent on exactly
the logs this clause exists to protect**.

**Ruling: count comparisons, and say "comparisons".** Never publish a run count
this data cannot dedupe. A precise-looking number that is wrong is worse here
than a coarser one that is right, because the whole clause is about a disclosure
a reader must be able to trust.

#### R29.5 — and tell C18's blind tester its first test is already green

C18's named first-failing test is **largely already passing**: clause 4 ("not by
a real headline run appended to a seeded log") is C3's third `is_demo` disjunct,
merged, and covered by two existing tests. A tester that reads a passing test as
a gap will write around it. Say so in the brief.

### R30 — C22b's contract, decided before dispatch

C7's lineage follow-up is merged (2154 passing, seven gates green), which
unblocks the half of C22 that R23.3 held back. R21.3 wrote C22's contract before
three of its four producers had been reviewed, so it names the fields and leaves
the joins open. Deciding them here rather than in the brief-writing is R28.1's
standing check applied forwards: **every one of these was verified against the
merged code, and the output is quoted.**

#### R30.1 — the lineage `from_evidence` passes is *assumed*, on every report

R21.5 said C22 "takes the lineage from config when present". Measured on `main`
after the C7 merge:

```
$ grep -rn "candidate_models\|lineage" src/model_migration_kit/*.py \
    | grep -v series.py
(no output)
```

**Nothing outside `series.py` mentions a lineage at all.** No config schema
carries one, `from_evidence` reads no config, and R21.3 forbids it starting.
"When present" describes a path that does not exist and may not be built here.

**Ruling: `CandidateLineage.assumed_from(...)`, unconditionally, and the caveat
it raises is correct.** Every report rendered from today carries a note saying
the succession was assumed rather than declared. That is not a defect to be
tuned down before it ships and it is not a placeholder — it is the true sentence
about every log this project can currently read, and R21.5 chose *render it and
name the doubt* over *withhold it* precisely so this case would have a page.

Stated for whoever meets it next, because the temptation is obvious and the fix
is a one-liner: **a caveat that appears on every report is not thereby noise.**
It becomes noise only when a declaration path exists and reports that use it
still carry it. Suppressing it now would restore the silent default R21.5
rejected, and would do it in the wiring, which R21.5 names as "the one shape of
this defect nobody would find".

#### R30.2 — `candidates` must become the *corrected* field

`correct_field` returns `(CandidateField, Multiplicity)`, and the field is not
the one that went in:

> What the returned field carries instead is one :class:`Caveat` per candidate
> in :attr:`Multiplicity.changed`, appended to :attr:`CandidateField.caveats`.

**Ruling: `ReportModel.candidates` is `correct_field`'s field, not
`candidate_field`'s.** Storing the `Multiplicity` while keeping the uncorrected
field would leave those caveats computed and dropped — which is R21's finding
exactly, reproduced inside the chunk written to fix R21. The caveat says a
candidate's significance did not survive correction; there is no second place it
is recorded, and `Multiplicity.changed` is a tuple of model ids, not prose.

A merged test asserting `model.candidates == candidate_field(model.series)`
would now be asserting the uncorrected shape. If one exists, it is wrong and
says so under this ruling — **report it, do not weaken it silently.**

#### R30.3 — the strip is fed from the line, never from the log

`parameter_strip(previous, current)` takes two points and the contract never
said which. `trend`'s own docstring settles it:

> This used to filter by the field that moves, and that is what hid the change…
> The strip was always able to show the change and was prevented by its own
> caller.

**Ruling: both points come from `Trend.points`** — `current = points[-1]`,
`previous = points[-2]` when there is one and `None` when there is not. Never
from `ReportModel.series`, whose order is the log's and whose membership is
every experiment in it.

Two consequences, both accepted:

1. **`Trend.points[-1]` is the line's newest run, which is not always the
   headline run.** If the headline was excluded from the line, the strip is not
   about the banner — and that is right: the strip belongs to the timeline
   section, where `Trend.excluded`, `outside_lineage` and `undated` already say
   who is missing and why. A strip silently retargeted at the headline would
   compare two runs the chart above it does not draw as consecutive.
2. **The strip is gated on the trend, not on itself.** An empty strip tuple
   means an empty line, and the reason is in `Trend`. A renderer that gates on
   `parameter_strip` being non-empty publishes "no parameters tracked" over a
   log that simply has no line yet.

#### R30.4 — the shapes, the defaults, and the one field that mirrors another

> **PARTLY CORRECTED by R32.1.** The `baseline_model` paragraph below is wrong:
> both of its reasons were measured false. `series` cannot be empty here, and
> `baseline` is the reader that loses a recorded value. The source is
> `series[-1].baseline_model`. Everything else in this section stands.

Decided together, because the pattern matters more than any of them:

| Field | Type | Default | `None`/empty means |
|---|---|---|---|
| `trend` | `Trend` | empty `Trend` | never `None` — `trend()` has no `None` return |
| `parameter_strip` | `tuple[ParameterChange, ...]` | `()` | the line is empty; see `trend` |
| `multiplicity` | `Multiplicity \| None` | `None` | there is no candidate field to correct |

**`baseline_model` comes from `ReportModel.baseline.model_id`, not from
`series[-1].baseline_model`.** They are the same fact and the rule is R23.2's —
exactly one source — so the tie is broken on which one is always there:
`baseline` is read from the records and always present, `series` can be empty,
and choosing it would need an empty-series special case that exists only to
answer a question `baseline` already answers.

**`multiplicity` is `None` exactly when `candidates` is `None`, and never
otherwise.** The two are one fact — the multiplicity is *of* the field — and
`correct_field` takes a `CandidateField`, not an optional one. A refusal
`Multiplicity` invented for the no-field case would be this chunk composing a
producer's prose, which R26.4 refused for `spot_check` and R21.5 refused for the
lineage caveat. The renderer already has a sentence for `candidates is None`;
a second one saying "and so nothing was corrected" can only ever agree with it
or contradict it, and the second outcome is the one that ships.

The defaults exist for `dimensions`' reason and no other: every existing
`ReportModel` construction predates these fields. **A default is not a
measurement** — the empty `Trend` default must not carry the assumed-lineage
caveat, because a `ReportModel` nobody computed a trend for has not assumed
anything.

#### R30.5 — a point-less caveat is silently dropped one layer over

Flagged by C7's implementer and confirmed. `Caveat.point` is now
`RunPoint | None`, and `candidate_field` filters the partition's notes at
`series.py:1231`:

```python
shown = {id(point) for point in rendered}
...
tuple(note for note in partition.caveats if id(note.point) in shown)
```

`id(None)` is in no `shown` set, so a point-less caveat reaching that filter
**disappears without a trace**. It cannot reach it today: R21.5's note is minted
in `trend` and lands on `Trend.caveats`, and `partition_comparable` mints
nothing point-less. So this is a trap, not a bug.

**Ruling, recorded now so it is not rediscovered from a missing sentence:** that
filter's intent is *drop notes about points the reader cannot see*. A note about
no point is not a note about a hidden point — it is a note about the field as a
whole, and it must be **kept**. The condition is `note.point is None or
id(note.point) in shown`.

This is C5's code and C22b does not own it, so it is not in C22b's scope. It
goes to whichever chunk next opens `candidate_field` — and until then, the rule
that matters downstream is the one `Trend.caveats` already documents: **a
renderer walking caveats into rows must ask before it indexes.** C14b's brief
carries it.

### R31 — three rulings landed, and the third mechanism I prescribed without running

C10's fix pass and C18's round two are merged; `main` is at **2174 passing**,
seven gates green, 20 of 22 chunks. Seventeen of R27's eighteen survivors now
die and the eighteenth is reported rather than papered over. Four findings came
back that the rulings did not anticipate, and one of them is about me.

#### R31.1 — R27.3 prescribed a mechanism the code cannot support. That is three.

R27.3 ruled that a `zeta`/`alpha` fixture closes M24 — the mutant that drops
`report.py`'s own ordering of the matrix tags. C10's fix agent built the fixture
and **measured that the mutant still survived all 2142 tests.** `dimensions.py`
keys every column through `sorted(index.tags)` (`dimensions.py:902`), so on
every input `from_evidence` can build, the counter's key order *is* alphabetical
order, and no log whatever can distinguish them. R27.3's own preceding paragraph
is what makes R27.3 unimplementable.

The agent closed the intent another way — replace `_close_the_tally` with one
returning unsorted keys, the only input no log can produce — kept the fixture
because it pins the published contract, and reported the discrepancy instead of
quietly substituting. Correct on every count.

**This is the third ruling of mine whose mechanism was wrong**, and the three
have one shape:

| | I claimed | Measured |
|---|---|---|
| R17.1 | `holm_bonferroni` returns uncorrected `alpha` after a step-down stop | every position is `alpha/(k-rank)` |
| R23.1 | `ReportModel.item_counts` holds `passing`/`failing`/`unstable` | `{"unit", "per_judge": {judge: {"baseline", "candidate"}}}` |
| R27.3 | a `zeta`/`alpha` fixture kills M24 | it survives; the sort makes the orders identical |

Each time I reasoned about what the code must do from its name, its docstring or
a neighbouring function, and each time the reasoning was good and the code was
different. Each time an agent caught it by running something.

**The rule this yields, and it is narrower than "check everything":** a ruling
that prescribes an **outcome** — *the note must not claim a measured zero*, *the
sentence must say what `is_demo` measured* — needs an argument and nothing more,
because the implementer will discover any obstacle while satisfying it. A ruling
that prescribes a **mechanism** — *this fixture kills that mutant*, *this field
holds those keys* — is a claim about code I have not run, and **it must carry
the output of running it.** That is R26.1 (*a claim that something was checked
must carry the output that checked it*) applied to the one place I keep
forgetting it: rulings that helpfully tell the implementer how.

Three of these have now cost roughly an agent-hour each, all recovered by the
same habit on the agent's side. Prescribe outcomes; prove mechanisms.

#### R31.2 — two more tests that a docstring would satisfy

R27.2's lesson generalises and C10's fix agent went looking rather than waiting
to be asked. Two more of the same shape, both currently satisfied by executable
code and both one docstring away from not being:

- `tests/test_report.py::test_render_html_is_the_one_that_validates` — regex-slices
  `render_html`'s body out of the module source and asserts
  `"assert_self_contained" in` it. A docstring mentioning the call satisfies it.
- `tests/test_stranger_path.py:214` — `assert name in source` over
  `scripts/verify_release.py` for three bundled data filenames. A comment naming
  a file the wheel no longer ships keeps it green.

Both take R27.2's fix: parse the module and find the `Call`.

**And the asymmetry worth keeping**, which the agent identified and which is
why `tests/test_cli.py:919` was correctly left alone: `assert name in source` is
a **positive** claim that prose can satisfy falsely. `assert "API_KEY" not in
source` is a **negative** claim that prose can only false-alarm. Source-text
assertions are unsafe in one direction only, and this project writes long
docstrings, so the unsafe direction is unsafe here in proportion to how well the
code is documented.

#### R31.3 — a defect that existed in neither branch, only in their sum

Merging C10's fix produced `[FAIL] no shadowed top-level names`:

```
tests\test_report.py: '_candidates' defined at 8286, 9909 -- the later one wins
```

C10's fix added `_candidates(matrix)`, returning the matrix's candidate columns
and carrying R27.4's assertion that they are a `tuple` and never a `Mapping`.
C22a's tests, merged earlier, define `_candidates(model)` returning
`model.candidates`. Python resolves module-level names at call time, so the
later definition won for all four earlier call sites — and **the merge of a fix
pass silently deleted the assertion that same fix pass had just added.**

The suite stayed green throughout, because the surviving helper returns the
right object for both callers; only the `isinstance` check was lost. Renamed to
`_candidate_columns`, with the reason in its docstring.

Worth naming as a class, because the pipeline's whole shape invites it:
**neither branch was wrong, and no role could have caught it.** The implementer
and the blind tester share a contract and not a namespace; the reviewer mutates
one branch; the fix pass works on one branch. A collision between two branches
is visible only at the merge, which is the orchestrator's stage — and the reason
it was caught is that `check_merge.py` runs a check whose entire purpose is to
find name collisions the interpreter accepts. **Run the gate on every merge,
including the ones where the suite is already green.** The suite was green.

#### R31.4 — C18's open point: the unrecorded state stops at the headline

R29.2 decided the *sides*, in the headline's vocabulary, and said nothing about
the series. C18's implementer implemented exactly that and reported the gap
rather than widening the ruling on its own authority: **blanking only the
history's adapters removes the "the history was scripted" disclosure and puts
nothing in its place** — R29.2's own defect, one level down.

Not ruled here, because it wants the same care R29.2 got and C18 has already
shipped its clauses. Recorded as open, with the note that `is_demo` is
series-scoped and `provenance` is headline-scoped, so the two disclosures now
have different reach and a reader cannot tell which one is speaking. That
asymmetry is the thing to rule on, not the missing sentence.

#### R31.5 — C22b measured the render, and it correctly did not move

24,564 bytes before and after, `<svg>` 2, `"dimension"` 0, `"spot check"` 0 — a
32-line diff of which every line is per-run nondeterminism (the generated
timestamp, the temp directory, the evidence hash). That is the expected and
correct result for a view-model chunk, and measuring it is how we know the chunk
did what it claimed and nothing else.

The consequence to carry forward: **R21.5's assumed-lineage caveat now exists on
every model and reaches no reader.** It is on `ReportModel.trend.caveats[0]`,
with `point=None`, and no template renders it. That is C14c's to fix, and it is
the first item in C14c's brief.

### R32 — C22b is merged, and one of R30's rulings was wrong where it counts

Both halves of C22b are in. `main` is at **2190 passing**, seven gates green.
The view model is complete: every producer this rebuild wrote now has a field on
`ReportModel`, and what remains is rendering.

#### R32.1 — CORRECTS R30.4. `baseline_model` comes from `series`, not `baseline`

R30.4 ruled that `trend`'s `baseline_model` should come from
`ReportModel.baseline.model_id` rather than `series[-1].baseline_model`, and
gave one reason: they are the same fact, so the tie breaks on which one is
always there — `baseline` is read from the records and always present, `series`
can be empty.

**Both halves of that reason are false, and C22b's blind tester measured both.**

*The empty-series case does not exist.* `from_evidence` raises `ArtifactError`
on a log with no `migkit.comparison` record, so `series` is never empty by the
time these fields are computed. The special case R30.4 was avoiding is
unreachable.

*And `baseline` is not the more faithful reader.* The two are the same JSON
field of the same headline payload — `comparison["baseline"]["model_id"]` — and
differ only in coercion:

```
series._text        -> "" if value is None else str(value)
report._run_summary -> str(side.get("model_id", "") or "")
```

They part company on exactly one class of value: falsy and not `None`. Measured
with `model_id: 0` in the payload:

```
baseline.model_id          == ''
series[-1].baseline_model  == '0'
```

Under R30.4's ruling the report then draws **no line at all** — nothing is
measured against `""` — and `_assumed_lineage(())` prints *"this baseline
recorded no candidate the log could name"*, which is false: the log recorded
`model-b-20260101`. **A recorded value renders as an absence, and the absence
then renders as a finding.** That is this document's central rule inverted
twice, in the one case where the choice is observable at all.

**Ruling: `series[-1].baseline_model`.** The tie-break is not "which is always
there" — neither can be missing — but **which preserves what the log
recorded**, and only one of them does.

Scheduled, not merely recorded (R28.1): this goes to **C22b's fix pass**, one
line in `from_evidence` plus the tester's expectation, which is currently
written against the ruling as issued and says so in its own docstring. The
tester tested the contract as written and flagged the wart rather than quietly
implementing the better answer — which is right, and is why this is a
correction rather than a defect.

#### R32.2 — the gate skipped constants, and that cost the second collision

R31.3 named the class: a defect that exists in neither branch, only in their
sum, visible only at the merge. It recurred within the hour, in the same file,
and the check written for it said **PASS**.

C10's dimension fixtures define a module-level `THIRD_MODEL`; C22b's blind
tester, cut from the same commit and forbidden from reading the other branch,
defined another 2,300 lines later. The later won for every reference in the
module. It was caught only because one of C10's tests happens to assert
`FOURTH_MODEL < THIRD_MODEL < CANDIDATE_MODEL` — a guard written for a different
purpose, which went red on a string comparison.

`check_no_shadowed_top_level_names` skipped `UPPER_CASE` names, commented:
*"An upper-case rebind is usually a deliberate constant edit."* Measured before
removing the exclusion:

```
0 upper-case module-level rebind(s) across the tracked tree
```

**The premise cost a real catch and bought nothing.** A deliberate constant edit
rebinds a constant in one branch's working copy; it does not leave two
module-level assignments standing in one file. Exclusion removed, and
`ast.AnnAssign` added while there — `NAME: Final = ...` binds exactly as
`NAME = ...` does and was invisible to the walker. Same measurement, zero new
reports.

Unfixed to confirm, per this project's own rule for fixes: with the rename
reverted the check reports `'THIRD_MODEL' defined at 8215, 10539 -- the later
one wins`, and the file was restored from a byte-verified backup with an
identical sha256.

**The lesson is about the exclusion, not the check.** Every gate here has one:
`check_all_is_complete` deliberately skips constants too, and argues for it at
length — correctly, because flagging them would report eight pre-existing style
decisions as merge defects. That argument is about *false positives it would
create*. This one was about *what a rebind usually means*, which is a guess
about intent rather than a measurement of the tree. **A gate's exclusion needs
the same evidence as its rule**, and the cheap check is the one run here: count
what removing it would report.

#### R32.3 — two contract questions C14b's tester raised and correctly did not answer

Recorded now so C14b's merge does not have to invent them:

1. **C14's element order table is already violated by merged code.** The table
   lists `timeline` before "What was compared"; C14a shipped
   `verdict, compared, timeline, …`. The tester asserted only the relative order
   of the three elements its own chunk adds, and said so in the docstring rather
   than picking a reading. Someone must decide whether the table or the shipped
   order is authoritative — **it is not C14b's to decide**, and nothing in C14b
   depends on the answer.
2. **R23.2's empty state has no anchor.** The section is gated on "any
   exclusion", and when `candidate_field` returns `None` there are none to gate
   on — yet R23.2 requires the page to hedge anyway. Where that sentence lives
   (under `candidates`, as a same-id branch of `excluded`, or unanchored) was
   never ruled. The tester asserted the *claim* and not the location, which
   leaves the implementer free. If the merge wants an anchor pinned, that ruling
   has to be written first.

And one thing the tester flagged about its own tests, which is the kind of
disclosure that makes a blind pair worth having: its hedge test matches an
"exclud" stem plus one of a fixed list of hedging words, so a phrasing like
*"some runs are not shown here"* would go red **for wording rather than for
substance**. Deliberate, R23.2's own phrasing, and declared — so the merge can
tell a wording disagreement from a defect finding without re-deriving it.

### R33 — C14c: the last three elements, and where the line's disclosures live

C14b is merged and the artifact moved for the first time in four merges: **24,600
bytes to 29,716**, the word *"dimension"* from **0 to 6**. Measured on `main` at
`e655958`, not inferred.

What is still computed and unread, measured the same way — counting `model.<field>`
inside `_TEMPLATE + _CHANGES_MACRO`:

```
spot_check         0        candidates      1
multiplicity       0        dimensions      1
parameter_strip    0        series          4
trend              0        provenance      5
```

Four fields. C14's table names three of them as elements; the fourth, `trend`,
has no row at all, because when C14's table was written `Trend` did not exist.
That gap is R33.2.

#### R33.1 — the three elements C14's table already names

| Element | `id` | Present when |
|---|---|---|
| spot-check sentence | `counterfactual` | `model.spot_check is not None` |
| multiplicity note | `multiplicity` | `model.multiplicity is not None` |
| parameter strip | `parameters` | `model.trend.points` — **not** `len(series) >= 2` |

The ids are the contract's own and are not to be improved on: `counterfactual`
rather than `spot_check`, because a link that changes its target is a link
somebody else's document has already got wrong.

**Two gates are corrected against what the producers actually do**, and both
corrections were already argued in merged docstrings rather than being invented
here:

*The multiplicity note.* C14 gates it on "candidate table present". R30.4 makes
`multiplicity` `None` **exactly** when `candidates` is `None`, so the two gates
are the same gate — but write it against `model.multiplicity`, because a note
gated on a *different* field is a note that can outlive its subject.

*The parameter strip.* C14 gates it on `len(model.series) >= 2`. That is wrong
now, and `ReportModel.parameter_strip`'s own docstring says why: the strip is fed
from `Trend.points`, so a log with four runs and no line yields two runs in
`series` and an empty strip. Gating on `series` would render a heading over
nothing. **Gate on `model.trend.points`.** And do not gate on the strip being
non-empty either: when there is a line the tuple is never empty — one row per
tracked parameter, including the ones that held — so empty means *no line*, and
the reason is in `trend`.

#### R33.2 — ruling: the line's disclosures render, and they render below the chart

`Trend` carries seven fields. The timeline that exists renders **none** of them:
it is `model.series | timeline`, every comparison in the log. So today R21.5's
assumed-lineage caveat exists on every model and reaches no reader — measured,
`"assumed"` appears **0** times in the rendered document — along with
`excluded`, `undated`, `outside_lineage`, `absent_models` and `successions`.

**Ruling: a lineage block inside the existing `timeline` section, below the
chart.** Not a new top-level section, and the chart is **not** re-pointed at
`Trend.points`.

Three reasons, in the order they decided it:

1. **The chart is not claiming to be the line, and its heading already says so**
   — *"Run history — N comparison(s) in this log"*. A chart that draws the log
   under a heading that says "in this log" is honest. Re-pointing it at the line
   would silently drop every run the lineage does not name, which is R24.1's
   defect rebuilt on the rendering side, and it would change merged, reviewed
   C14a code for no reader benefit.
2. **`ReportModel.parameter_strip`'s docstring already sited them there**, in
   merged code: the strip "belongs beside the timeline, where `Trend.excluded`,
   `outside_lineage` and `undated` already say who is missing and why". That
   sentence is currently false — those fields say it to nobody. This makes it
   true rather than deleting it.
3. **The block's job is the difference between the two sets.** The chart draws
   the log; the line is a subset of it; and the interesting content is exactly
   which runs are in one and not the other, and why. That is a paragraph, not a
   second chart.

What the block must carry, and the failure each entry prevents:

| From `Trend` | Must say | Otherwise |
|---|---|---|
| `caveats` | every note, including the **point-less** first one | R21.5's disclosure reaches nobody, which is today |
| `excluded` | each `Exclusion`'s own sentence, unrewritten | "3 runs excluded" is the count without the reason (R23.2's argument, one section over) |
| `undated` | the count, and that these are runs no axis can place | a chart quietly missing runs |
| `outside_lineage` | the runs on this baseline the declaration does not name | R24.1 exactly: night 14 appearing nowhere on the page |
| `absent_models` | declared ids with no run in the log | a one-character typo in a declaration, invisible |
| `successions` | where the candidate model changed | the event the chart exists to show |

**A renderer walking `caveats` into rows must ask before it indexes.** `Caveat.point`
is `RunPoint | None` and the assumed-lineage note is the entry with no point —
it qualifies the chart, not a night, and rendering it against a run would be an
absence rendering as a measurement from the rendering side. R30.5 documents a
live filter one layer over that would drop it silently.

**And every one of these is empty on the bundled demo**, which is one run. An
empty lineage block must not render as a heading over nothing; it must either be
absent or say that the line is the whole log. Decide that in the chunk and say
which was chosen — this is the spec's named failure mode ("an empty chart or a
crash") and there are three new conditional sections here.

#### R33.3 — closing R32.3's two open questions

**C14's element-order table versus C14a's shipped order.** The table lists
`timeline` before "What was compared"; C14a shipped it after. **Ruling: the
shipped order is authoritative for elements already placed, and the table governs
only the elements not yet placed and their order relative to one another.**
Moving a merged, reviewed section to satisfy a table changes a document no reader
has complained about, risks a chunk's worth of test churn, and buys nothing. The
table is corrected by this ruling rather than the document being corrected by the
table. C14b was right not to decide it, and right to flag it.

**R23.2's hedge anchor.** C14b shipped it as an `<h2 id="excluded">` in both
states, arguing that in the state R23.2 is about there is no candidate table
above it for the hedge to be a sub-section of. **Ruling: ratified as shipped.**
The same-id discipline that `dimensions` uses is the right one, and it is now
used twice for the same reason — a link to `#excluded` resolves whichever branch
rendered.

### R34 — closing R31.4: the asymmetry is real, and it already ships a sentence

C18's implementer reported that the unrecorded provenance state "triggers on the
headline's two sides only", and that blanking the *history's* adapters removes
the scripted disclosure and puts nothing in its place. R31.4 recorded it and said
the thing to rule on was the asymmetry rather than the missing sentence. Ruling
it now — and looking at the code first found something the report did not.

#### R34.1 — the asymmetry, stated exactly

`Provenance` carries the scripted finding at **both** scopes and the unrecorded
finding at **one**:

| Finding | Headline scope | Series scope |
|---|---|---|
| scripted | `headline_scripted` | `scripted_comparisons` |
| unrecorded | `unrecorded` (sides) | **nothing** |

That is the whole defect in one row. `is_demo` deliberately reaches into the
series — its docstring argues it, and correctly: *"a band that appears only when
the last run was fake is a band you can remove by scripting the runs before
it."* The unrecorded state was then built at headline scope only, so **the gap
that the scripted finding was widened to close is still open for the state
invented to close it.**

**Ruling: `Provenance` gains `unrecorded_comparisons: int`** — comparisons
naming no adapter on at least one side — as the exact mirror of
`scripted_comparisons`, counted the same way and in the same unit (comparisons,
never runs, R29.4).

#### R34.2 — the sentence that ships today, and why it is R29.1 again

Found by reading `report.py:1019-1028` rather than by taking the report's word
for the shape. When no comparison names a `Fake*` adapter, the document prints:

> none of the **N** comparisons in this document name a Fake adapter in their
> own payloads, and this band comes from the run artifacts the headline read

**Every word of that is true when the payloads name no adapter at all**, and it
reads as *these N comparisons were checked and came back clean*. A comparison
with empty adapter strings is counted in the denominator exactly as if it had
been examined and cleared. It is R29.1's shape one scope up: not a false
sentence, but a **true sentence that licenses a false inference**, which is the
harder of the two to find and the easier to defend in review.

**Ruling: the count must exclude what it could not check, and say how many those
were.** When `unrecorded_comparisons` is zero the sentence stands unchanged.
When it is not, the denominator is the comparisons that *named* an adapter, and
a second clause says how many named none and that the document therefore cannot
speak for them. **Never a `0 of 0`** — the property already refuses that for the
empty series, on the same reasoning, and that refusal is the precedent.

#### R34.3 — do not equalise the reach; label it

The tempting fix is to make the band series-scoped so the two disclosures match.
**Refused.** The band sits over the headline's numbers and a reader takes it as
being about them; widening it would put a claim about last month's runs on top of
this comparison's verdict, which is R29.1's defect chosen deliberately.

**Ruling: every provenance sentence names its own scope, and the scopes stay
different.** The band speaks for the headline comparison. A series claim renders
in the **timeline section**, beside the lineage block R33.2 puts there — which is
the same siting argument, reached twice independently: statements about *which
runs are on this page and what is known about them* belong under the chart that
draws them, not on the banner that reports one of them.

So the reader is never asked to work out which scope is speaking, because each
sentence says. That is what closes R31.4 — not a third state, and not a wider
band.

#### R34.4 — scheduled, not merely recorded

**This is C18's fix pass**, and it is the whole of it. C18 shipped R29's clauses
and has had no fix pass; this is what that pass is for. Dispatch it with R34 and
R29 both, and with the `_no_scripted_sentence` line numbers above, because the
defect in R34.2 is invisible unless you read the sentence against a payload that
records nothing — which is the fixture the brief must demand.

Recorded here rather than in my head, because R28.1 is the failure this project
keeps repeating and this is the fourth ruling in two days that would otherwise
have been carried into no brief at all.

### R35 — C14c is merged, and three contract problems its tester refused to decide

`main` at **2220 passing**, seven gates green. The demo render goes 29,716 to
**32,635 bytes** and `"assumed"` goes **0 to 1** — R21.5's lineage caveat has
existed on every model this project can build since C7's follow-up merged, and
until now was printed nowhere.

**The measurement that says why the chunk existed** is the implementer's: at the
baseline commit, a five-comparison fixture and one with three more `Trend` fields
widened rendered at **identical byte counts**. Widening the disclosures changed
nothing on the page. That is R21's finding reached from the rendering side.

**The blind pair produced zero disagreement** — all thirteen tests pass against
an implementation that never saw them. That is the fourth such pair on this
project and it still means nothing about defect-freedom: C10's review found 18
surviving mutants after a clean pair, C7's found 11. C14c gets a review.

#### R35.1 — `Trend.absent_models` can never fire, and R30.1 is why

The tester was told to build a log with all six of `Trend`'s disclosure fields
non-empty at once. It reported that this is **not satisfiable**, and it is right.

R30.1 rules that `from_evidence` passes `CandidateLineage.assumed_from(...)`
unconditionally, because nothing outside `series.py` declares a lineage.
`assumed_from` assembles the ids **out of the log's own points**. So "declared
ids with no run anywhere in the log" is empty by construction on every model
this entry point can build.

**`absent_models` exists to catch a one-character typo in a declaration, and
there are no declarations.** R24.1 added it for the case *most likely to be an
operator error rather than a fact about the data* — and R30.1, four revisions
later, removed the only way to reach it. Neither ruling was wrong; the second did
not notice what it had done to the first.

**Ruling: the field stays, dormant, and this is recorded rather than fixed.**
Deleting it would mean rebuilding it the day a declaration path lands, and the
tester has already written its test against a `Trend` the producer returns
directly. What must not happen is anyone reading its permanent emptiness as
evidence that lineages are never mistyped. **Any chunk that adds a declaration
path is also the chunk that makes `absent_models` reachable, and must say so.**

#### R35.2 — my brief was wrong about the demo, and the tester tested the ruling

The C14c brief said all four new sections are empty on the bundled demo. Under
R33.1's gate the **parameter strip is not**: one run is a line, R33.1 explicitly
forbids gating on the strip being non-empty, and the demo renders six rows of
`NO_PREVIOUS_RUN`. The tester tested R33 and flagged the brief.

**Ratified as the tester read it.** A first run showing six rows that all say
*no previous run* is exactly what `parameter_strip`'s docstring wants — *"a word,
not a blank, so a genuine first run cannot be read as a run that changed
nothing"*. The brief was describing what I expected rather than what the ruling
said, which is the failure R31.1 is about, in a brief instead of a ruling.

#### R35.3 — a producer that returns `None` throws away the reason, three times now

The tester found `ReportModel.spot_check`'s docstring and R33.1's table pulling
in different directions. The docstring says the producer declines on three
grounds and *"each is a different sentence the renderer owes the reader"*;
R33.1's table says the element renders only when `spot_check is not None`. On the
demo — 12 items with `k=12`, a census rather than a spot check — the reader sees
**no spot-check section at all** and is told nothing about why.

The renderer cannot fix this. `spot_check` returns `None` and discards which of
three refusals it was, so there is no sentence for a template to print.

**This is the third instance of one shape**, and naming it is worth more than
the individual rulings:

| Producer | Returns | What dies with the `None` |
|---|---|---|
| `candidate_field` | `None` | every exclusion sentence computed on the way (R23.2) |
| `spot_check` | `None` | which of three refusals declined it |
| `trend` *(nearly)* | — | avoided: `Trend` was widened to seven fields instead |

R23.2 accepted the first cost deliberately and said so. R15.3 caught the third
before it shipped and widened the return type — *"a contract that promises to
report an absence needs somewhere to report it"*. **The second was never
noticed**, because a producer returning `None` looks like a producer with
nothing to say, and only a reader asking *why is this section missing* can tell
the difference.

**Ruling: ratify R33.1's table for now** — the element is absent when the value
is `None`, because there is nothing else the template can honestly do — **and
record that closing it is a producer chunk, not a rendering one.** `spot_check`
would return a refusal-carrying type on `DimensionMatrix`'s pattern: *a report
that has no counts says so in the same place as one that has them.*

And the standing rule the three instances yield: **a producer that returns `None`
must be asked what it knew at the moment it decided to.** If the answer is
"which of several reasons", that reason is a fact the reader is entitled to, and
`None` is the one return type that cannot carry it.

### R36 — C22b's review: 24 mutants, four survivors, and R32.1 is wider than R32.1 said

The blind pair agreed on everything and the review found four live gaps, which is
the fifth time that pattern has held. Every survivor below was proved
non-equivalent by rendering the difference, not by arguing it.

#### R36.1 — the strip's `current` is pinned to `series[-1]`, not to the line

**The mutant.** `parameter_strip(line.points[-2], series[-1])` — the strip
retargeted at the headline run. This is the exact thing R30.3 consequence 1 says
must not happen, and it survives all 1,356 tests, because **in every fixture in
the suite `line.points[-1] is series[-1]`.**

Built the case R30.3 says is real — two sibling runs anchor the line, the headline
edits its golden-set hash and `partition_comparable` excludes it:

```
series      = ['model-c-...', 'model-e-...', 'model-b-...']
line.points = ['model-c-...', 'model-e-...']
line.points[-1] is series[-1]: False

  model_id   shipped c -> e  changed=True   || mutant c -> b       changed=True
  items      shipped 3 -> 3  changed=False  || mutant 3 -> 96      changed=True
  goldenset  shipped 3f51 -> 3f51 False     || mutant 3f51 -> eee  changed=True
```

Three of six rows differ and two flip `changed` False to True. **The strip would
assert that the golden set changed and the item count went 3 to 96 between the
line's last two runs**, when one of those runs is not on the line and the chart
never draws it as consecutive. That is the false attribution the strip exists to
license against, and it is the wiring a future refactor reaches for first —
*"surely the strip should be about the run in the banner"*.

**Ruling: pin it.** The fix pass adds the fixture and the assertion; the code is
already right.

#### R36.2 — `previous = points[-2]` is unpinned on every line of three or more

`previous = points[0]` and `previous = points[1]` both survive everything. The
only test that pins the pair uses a line with exactly **two** points, where
`points[0] == points[-2]` and the mutation is invisible. Measured on
`_family_log`, which already draws a three-point line:

```
line.points = ['model-c', 'model-d', 'model-b']
  model_id: shipped before='model-d' | mutant before='model-c'
```

**On any line of three or more the "before" column names the wrong night**, so
the strip skips a whole run's worth of changes and attributes them to the wrong
transition — c→d→b prints as c→b, silently absorbing everything `d` changed.

**This is R24.7's pairwise monoculture, and it is worth reading twice.** The
fixture set varies line length. It also varies strip content. It never varies
them *in combination*: the long line is never asserted on its strip, and the
asserted strip is never long. Every field is exercised and the defect lives in
the pair. That rule has now caught shipped defects three times, and this is the
cleanest example of it the project has produced.

#### R36.3 — the single-pass guarantee holds for the paths a slip would take, and not in general

A second full read via `os.open` + `os.read` in 64 KB chunks **survives all
1,356 tests**, including both merged tests R21.3 names by title — they
monkeypatch `builtins.open` and `io.open` and count text-mode opens.

The more interesting datum is the reviewer's M20: a whole-file
`open(path, "rb").read()` is invisible to all three open-counting tests, whose
docstrings *deliberately* exclude binary mode because the provenance hash reads
the log in binary. It is caught only by
`test_rebuilding_the_report_does_not_hold_the_log_either` — a **peak-allocation
slope** test, not a read-count test. So the binary hole is closed **by accident,
and only for reads that buffer the whole file.**

R21.3's reviewer clause asked whether the single-pass guarantee holds "under a
test rather than by inspection". **The honest answer: yes for the paths a real
slip would take, no as a general guarantee** — and that answer is worth more than
a wider test would be. Do not chase `os.read`: a test that patches every syscall
is a test nobody can read, and the realistic regressions (`read_series`,
`Path.read_text`) both die today. **Record the limit rather than closing it.**

#### R36.4 — R32.1 is wider than R32.1 said, in two directions

R32.1 corrected `baseline_model` to `series[-1].baseline_model` and named two
costs of the old reading. The reviewer measured two more.

**First: the run lands in none of `Trend`'s seven fields.** On the tester's own
falsy-baseline log:

```
points=0, successions=0, excluded=0, undated=0, outside_lineage=0, absent_models=0
0 of the 1 run in the log accounted for anywhere
```

`trend` filters `point.baseline_model != baseline_model: continue`, so the run is
not even a stranger. **That is R24.1 exactly — a run in the log and on no part of
the page — live in merged code today**, and it is `outside_lineage`, the field
whose entire purpose is to say that an absence is a claim about the declaration,
that fails to catch it.

**Second, and this is the part R32.1 does not fix: the same coercion split exists
on four more shared fields.** `str(x or "")` in `report.py` against `_text` in
`series.py`, measured one falsy value at a time:

```
candidate.model_id     SPLIT  RunSummary.model_id=''   RunPoint.candidate_model='0'
judges[0].name         SPLIT  JudgeRow.name=''         RunPoint.judge_name='0'
judges[0].model_id     SPLIT  JudgeRow.model_id=''     RunPoint.judge_model_id='0'
judges[0].rubric_hash  SPLIT  JudgeRow.rubric_hash=''  RunPoint.rubric_hashes=('0',)
```

The candidate-side one is R32.1 mirrored and it runs straight through C22b's new
fields: on that log the banner prints the candidate model as an absence while the
strip's `model_id` row reads `after='0'` and `Trend.caveats` prints *"so 0 — the
one candidate this baseline recorded — was assumed from the log to be the whole
succession."* **R32.1's scheduled one-line fix touches `baseline_model` only, so
this survives it.**

**Ruling: the split is the defect, not any one of its five sites.** Two readers
of one JSON field disagreeing on falsy-not-`None` is a class, and fixing five
call sites one at a time guarantees a sixth. The fix pass makes the two coercions
agree — **`report.py`'s `str(x or "")` adopts `series.py`'s `"" if value is None
else str(value)`**, because that is the one that preserves what the log recorded,
which is R32.1's own tie-break generalised.

`RunSummary.adapter` is **excluded** from that change: it disagrees for a second,
unrelated reason — `_run_summary` prefers `run.header.adapter` over the payload —
and folding two different disagreements into one edit is how a fix pass ships a
defect. Report it, leave it.

#### R36.5 — two things confirmed rather than found, both worth keeping

**R30.5's trap is still unreachable, and C22b did not bring it within reach.**
Applying R30.5's fix leaves the suite entirely unchanged (the branch is never
taken); removing the filter altogether goes red. So the filter is live and doing
real work on superseded-run notes, and only its point-less branch is dead. The
mechanism is now traced: the only `Caveat(point=None)` in the package is minted
inside `trend` and lands on `Trend.caveats`, and `_multiplicity_caveats` always
attaches to a row's own point **and appends after `candidate_field`'s filter has
already run**. Merged anyway, as R30.5 ruled.

**The import/field name collision holds under every path**, checked by AST rather
than by argument: zero class-body-level reads of `trend`/`parameter_strip`/
`multiplicity`, and exactly one function calling either name. The latent trap is
worth stating for whoever touches this next: **a future class-body expression or
a `field(default_factory=...)` naming `trend` would silently get `_NO_TREND`
instead of the function** — no error, just the default. A local shadow inside a
method fails loudly with `UnboundLocalError`; the class-body one does not.

And a wording note for C14b's template, reaching no reader today because the
demo has no candidate field: it heads `candidate_field.caveats` with *"Rows that
are in the table above under protest"*, and since C22b that tuple also carries
`correct_field`'s multiplicity notes — which are not protests about a row's
inclusion but withdrawals of its significance.

### R37 — C14b's review: 52 mutants, 31 survivors, and one sentence explaining half of them

The largest review this project has run, and the most useful. Every survivor was
proved non-equivalent by rendering the difference, and twelve — including all
eight top-ranked — were re-confirmed against the full 2,206-test suite rather
than the one file.

**Both audits the brief asked for first came back clean**, which is worth saying
before the failures: exactly two `| safe` filters in the whole template, zero
interpolations inside `<style>`, three in-tag interpolations all pre-existing and
all enumerated, `autoescape` on, and no `is defined` or `| default` anywhere.
C14b added no escape hatch. R27.5's three zero-column cases are the
best-tested part of the chunk — a shorter note, a composed note and a dropped
never-closed column all die.

#### R37.1 — `x in region` is not `x in the cell that names it`

**Every assertion about the matrix and the candidate table is a substring search
over the section.** That single fact explains findings 1, 2, 3 and 5:

| Mutant | What renders | Suite |
|---|---|---|
| cells rotated one **row** up | `extraction 100.0% [0.8828, 1.0000]` where extraction scored **0.0%** | 2206 green |
| cells rotated one **column** left | the baseline's numbers under the candidate's heading | 2206 green |
| `baseline`/`candidate` labels swapped | the two sides transposed | 2206 green |
| every row shows the field's baseline rate | `53.5%` becomes `85.0%` in every row | 2206 green |

The test named
`test_each_matrix_column_shows_its_own_reading_and_not_its_neighbours` **does not
catch a rotation.** It catches only the mutant that makes a rate vanish from the
region entirely; a rotation keeps every number on the page and passes. The
implementer's commit message says positional indexing "would print a real
measurement under the wrong dimension, silently, which is the worst failure this
document has", and chose a tag join to make it impossible. **The code is right and
nothing would notice if it stopped being right.**

**This is R27.2 and R31.2 reached from a third direction.** Those were assertions
over module *source text* that a docstring could satisfy. This is an assertion
over rendered *region text* that a wrong cell can satisfy. The shape is the same
and worth stating once, generally:

> **An assertion that searches a container for a value proves the value is in the
> container. If the test's name claims the value is in a particular *place*, the
> assertion must address that place.** A test whose docstring claims more than
> its assertion checks is the most expensive kind, because it is read as coverage
> by everyone including its author.

**Ruling: the matrix and candidate-table assertions become cell-addressed** —
parse the table, locate the cell by its row and column headings, and assert the
value there. The document is already parsed with `html.parser` elsewhere in this
file, so the machinery exists.

#### R37.2 — the fixtures never take the branch the producer documents

The second pattern, behind findings 4, 9 and most of 10: **no fixture makes a
value `None`, or a collection non-empty, in the places the producers explicitly
document as possible.**

The sharpest instance: removing the `{% if caveat.point is none %}` guard makes
the render **raise** — `UndefinedError: 'None' has no attribute
'candidate_model'`, the spec's own named "crash" — **and all 2,206 tests stay
green, because nothing in the suite renders a `CandidateField` with any caveat at
all.** R33 warned about this exact shape ("a renderer walking caveats into rows
must ask before it indexes"). The implementer asked. Nothing holds it there.

And `_days` and `_pp`, the chunk's two newest functions, **have no test between
them.** Each carries a long docstring insisting `None` renders as the dash and
never as a zero — *"Printing the first two as `0.0 days` would state 'measured in
a single sitting' on the evidence for 'we do not know when this was measured'"*.
Forced probe: the shipped template renders `no recorded date … — … —`; with the
mutant the same row renders `+0.0 pp` and `0.0 days` — *no change, measured
alongside the newest*. **That is this document's central rule, in the two
functions written to serve it, unguarded.** A sign flip in `_pp` turns a
31.5-point regression into a 31.5-point gain and survives on a document the suite
already renders.

**Ruling: the fix pass adds fixtures for the documented-but-unreached branches** —
an undated candidate row, a `None` delta, a field carrying caveats, an
`available=True` matrix with an empty tag universe, and an unmeasurable spread.
Each is named in a producer docstring as a real state. **A branch the producer
documents and no fixture reaches is a branch that exists only in prose.**

#### R37.3 — the hedge is pinned where it is required and not where it would be false

R23.2's hedge — *"runs may have been excluded from a comparison without this page
being able to name them"* — is correctly required when there is no candidate
field, and correctly killed when deleted. But rendering it **alongside a
populated list** also survives: the page then says it cannot name the excluded
runs immediately above three named runs with three reasons.

**Only the empty-list form of the error is tested.** Ruling: pin both directions,
which is the same shape as R28.2's *"a refused correction must not leave behind
the furniture of an applied one"* — a hedge is furniture, and it must not stand
where the thing it hedges is present.

#### R37.4 — a real dangling link, and it is the fixture that is missing

`test_no_anchor_this_document_links_to_is_missing_from_it` renders exactly two
documents, and **both carry `id="excluded"`**. The one document where the gate
matters — a field that excluded nothing — is never rendered by that test.
Ungating the nav link puts `<a href="#excluded">` on a page with nothing to point
at, and the suite passes.

This is the failure `excluded_shown` was introduced to prevent, and **the
assertion is fine; the fixture is missing.** Worth separating, because the two
have different fixes and the wrong diagnosis produces a stronger assertion over
the same blind spot.

#### R37.5 — the tag join is an equivalent mutant, and that is the finding

Tested both ways, which is what the brief asked. The join **is** total —
`column.cell(tag)` resolved for every tag in every column across all five
documents, no `None`, exactly as R27.8 #2 says. And the implementer's claim holds:
on a column whose cells were reversed, the tag join renders byte-identically to
the in-order render while positional indexing does not.

So positional indexing is **equivalent on every producible model and is not a
defect** — and the finding is that a future edit back to it would pass all 2,206
tests. R27.8 #5 called positional "the right shape for a table renderer"; the
implementer diverged deliberately and was right to. **Ruling: R27.8 #5 is
withdrawn**, and the tag join is the contract.

#### R37.6 — C18's fix pass found the same defect on a second surface, and one worse

Reported by C18's fix agent, outside its scope, correctly not fixed.

`_counted_paragraph` renders the same claim in the **methodology appendix**, and
R34.2 only ruled the band. So the document now **disagrees with itself**: the band
says *"none of the 2 … that record an adapter on both sides … the other 2 …
this document cannot speak for them"*, and four screens later the appendix still
says *"None of the 4 comparisons drawn in this document name a Fake adapter"*.

**And the appendix carries a worse instance than the one R34.2 fixed.** With one
scripted comparison out of three and two recording no adapter:

> 1 of the 3 comparisons drawn in this document names a Fake adapter on at least
> one side; **the other 2 do not.**

That is not an implicature a careless reader might draw. **It is an explicit clean
claim about two comparisons that recorded no adapter at all**, and it is live
today. R34.2's ruling deliberately left the `K of N` branch alone because the two
counts overlap and are not a partition — which was right for the band's sentence
and leaves this one standing.

**Ruling: add the third counter.** `scripted_among_named` — comparisons recording
an adapter on both sides **and** naming a `Fake*`. With it, the honest sentence
partitions cleanly: *K of the N comparisons that recorded an adapter on both
sides name a Fake adapter; the other N−K do not; M further comparisons recorded
no adapter, and this document cannot speak for them.* No overlap, no subtraction
across counts that are not complements, and every comparison accounted for.

The appendix and the band then say the same thing in the same units, which is the
`DetailBudget.sentence` discipline applied across two surfaces instead of two
renderers.

### R38 — a second operator read the document, and found what four roles could not

An independent audit ran on a second machine against `review/2026-08-24`, with
one instruction: read the **rendered document** as a reader trying to catch it
lying. It produced 41 findings, then ran an adversarial pass whose only job was
to refute them.

**Read that ordering twice.** The audit's own summary is right about which half
mattered: *"An agent was tasked solely with refuting everything below, defaulting
to REFUTED when uncertain. It is the most useful thing in this audit, and it cost
me a lot."*

```
REFUTED / already scheduled   9
WEAKENED                     25
SURVIVES                     20
```

**Twenty surviving findings is a far better result than forty-one confirmed
would have been**, and this project should take the shape as standing practice:
a finding that has not been attacked is a hypothesis. Five findings got
*stronger* under attack, which is the other half of why the pass is worth its
cost — it does not only subtract.

#### R38.1 — why the pipeline could not have found these

Four roles per chunk, seven reviews that each found defects the other roles
missed, 52 mutants on C14b alone. None of it found finding 2, and the reason is
structural rather than a lapse:

> **Every role in this pipeline reads the contract. Nobody read the document.**

An implementer satisfies a contract. A blind tester tests a contract. A reviewer
mutates code and asks whether the *suite* notices. All three are anchored to what
was specified, and every one of them can be fully satisfied while the rendered
page says something false — because "is this sentence true of this evidence" is
not a question any of them is asked.

The correction is not a fifth role on every chunk; that would cost more than it
returns. **It is that a document with a reader is audited by a reader,
periodically, against the artifact rather than against the plan** — and that the
auditor is told to refute itself before reporting.

#### R38.2 — the structural criticism, which was about to cost a chunk

The refuting agent's most valuable output is not a verdict on any finding. It is
this:

> Most of Tier 2 is an argument about robustness to a *foreign or future* writer.
> There is exactly one writer of the comparison payload (`comparison.py:907`) and
> it writes every key unconditionally. **Without that caveat a fix pass would
> spend a chunk hardening reads against a writer that does not exist.**

Sixteen findings owe that concession. **Ruling: they are not scheduled as
written.** A defect reachable only through a writer nobody has is a robustness
argument, and robustness arguments are worth making explicitly and costing
honestly — not smuggled in as sixteen separate bug fixes.

#### R38.3 — finding 35 is the prerequisite, and it was promoted by the refuter

```
runner.py:174, runner.py:564, judging.py:487   guard on schema_version
grep -n schema_version src/.../{report,series,evidence}.py   -> no match
```

Every other reader of a written artifact refuses a schema it does not understand,
*"rather than misinterpret it"*. The evidence log has no such guard: a log with
`schema_version: 99` on every record renders in full, exits 1, prints
`VERDICT: NO-GO`, and says nothing.

And `series._count`'s own docstring declares surviving a foreign writer to be in
scope — *"A writer that quoted its integers is a real thing to survive"* — with
silent coercion to `0` as its failure mode.

> **The one reader built to tolerate a foreign payload is the only one that will
> not say it has one.**

**Ruling: this is scheduled first, ahead of every other audit finding**, because
it decides whether the rest of Tier 2 is reachable at all. It converts sixteen
hypothetical hardening tasks into either real work or provably dead work, and
that is worth more than any of them individually.

#### R38.4 — the two findings that survived and got stronger

**Finding 6 — the completeness certificate counts characters the models did not
produce.** Wrong on the bundled demo, in the default path: the page certifies
*"5,821 characters of quoted model text"* as *"what the models produced"*, and
the models produced **5,100**. The difference is 426 characters of golden-set
prompts, written by the golden-set author, and 295 of judge reasons, written by
the judge. Under truncation it is **12x short** — outputs are cut at
`max_output_chars` *before* the budget sees them, so the sentence certifying
completeness is blind to the truncation printed three lines below it. Its own
`quoted_chars` docstring says the figure is *"the post-truncation size and not
the size of what the models actually said"* — the opposite of what the page
prints.

**Finding 2 — this document's central rule, failing in the mirror direction.**
*"Latency — Not measured"* over a payload holding 120 recorded timings, 60 per
side, with median and p90. The suppression keys on the adapter's **name**
(`{% if model.baseline.is_fake and model.candidate.is_fake %}`), never on whether
a measurement exists. And it is printed in the paragraph that quotes the rule
back at the reader: *"a row that reads 0.000 / 0.000 is not a fast model, it is
the absence of a measurement"*.

**Every chunk of this rebuild has guarded one direction: an absence must not
render as a measurement. Nothing guarded the other: a measurement must not render
as an absence.** Stated now as the rule's second half, because it has been
implicit for thirty-seven revisions and was never once written down.

#### R38.5 — the scheduling

| Order | Chunk | Findings |
|---|---|---|
| 1 | schema guard on the evidence log — **the prerequisite** | 35 |
| 2 | the completeness certificate counts the wrong characters | 6, 6a, 6b |
| 3 | latency suppressed by adapter name, not by absence | 2 |
| 4 | the banner's bar is drawn for a different judge than its verdict | 8, 9a |
| 5 | disclosures that never reach the terminal | 22, 16, 26 |
| 6 | wording, units, scope | 23, 24, 29, 31, 32, 36, 37, 40, and the demoted 1, 3, 4, 5 |

**Findings 1, 3, 4 and 5 were demoted out of Tier 1 by the refuting agent and
that is accepted.** Finding 1 — the demo's `n=60` being 12 answers counted five
times — is real and is a *framing* gap: the report faithfully echoes what
`comparison.py` computed, and what is missing is a sentence reconciling `n=60`
with the page's own eight statements that all five draws are identical. It
belongs with the other disclosure gaps, not at the front.

#### R38.6 — the method is worth more than the findings, and is being made permanent

Tier 2 was found by **differential rendering**: for each leaf path in the payload,
five whole documents differing only in that field — a plausible value, a measured
zero, the key removed, the key set to null, the parent removed — compared byte
for byte. 176 paths, 2,391 renders. Where measured-zero and absent render
identically, the rule is broken.

**And the trap is worth as much as the technique.** The page prints an evidence
hash over the whole file, so a naive diff finds every pair different and reports
**zero findings**. The first sweep came back empty for exactly that reason. A
sweep that reports nothing is indistinguishable from a sweep that found nothing,
which makes this the one class of test that must be proved to fail before it is
believed when it passes.

Dispatched as a permanent test rather than left as an audit script.

### R39 — the audit verified against `main`, and R38 corrected by it

R38 scheduled the audit's findings after reading the audit. This is what
happened when every finding was reproduced against merged code rather than
against the report of it. **Three of R38's own decisions were wrong**, and the
verification found a lost disclosure the audit's method should have caught and
did not.

#### R39.1 — three corrections to R38.5's schedule

**Finding 16's headline is false, and chunk 5 was partly built on it.** The claim
was that `--quiet` silences the FAKE MODELS disclosure. Measured: `--quiet`
silences the *terminal*, not the report.

```
q.html vs nq.html          byte-identical
grep -c "FAKE MODELS"      2   (including the <title>)
```

The surviving half is finding 22 — the terminal discloses none of it — which is
a real defect and is already chunk 5's subject. **The `--quiet` clause is struck
from the chunk.** A brief written against the headline would have sent an agent
to fix a disclosure that is not missing.

**Finding 23 is already fixed and R38 schedules it anyway.** C14c renders
`Multiplicity.note`, and the page now reads *"across the 2 candidates in this
field… 1 candidate(s) in the table recorded no p-value, were not tested, and are
not in the family."* Confirmed on merged code. **Struck from chunk 6** — this is
R28.1's failure mirrored: not a ruling with no brief, but a brief with no
remaining defect.

**Finding 34's citation was over-retracted, which made it look weaker than it
is.** The audit withdrew it to R20; the ruling that actually speaks is **§9 risk
7**, which *prescribed* the mitigation — *"both appear in the methodology
appendix… clearly separated"* — and only the docstring half shipped. The correct
citation makes the finding **stronger**: it is not a style preference, it is a
prescribed mitigation that was skipped. **Promoted within chunk 6.**

#### R39.2 — R29.3's disclosure fires only where it does not matter

**The audit's own method should have caught this and stopped one question
short.** Its finding-12 grep listed four unreferenced fields and dismissed one as
a false positive without asking whether the others were *conditionally*
reachable. One of them is a genuine lost disclosure.

```
one comparison, envelope ts on a different UTC day from payload["created"]

REAL adapters : state=recorded  dated_apart=1 of 1  disclosed on the page? False
FAKE adapters : state=scripted  dated_apart=1 of 1  disclosed on the page? True
```

`_dated_sentence` is correct, and its docstring correctly says it discloses the
asymmetry "when it is there and not when it is not". **Its only call site is
inside `_scripted_paragraph`, which runs only when
`provenance.state == PROVENANCE_SCRIPTED`.** So the two-clocks disclosure fires
on scripted documents and never on a real production report — the only place a
reader needs it.

Decisive test: mutating the field with paths and hash masked produces **zero**
page change. Grep for `dated_apart`, `two clocks`, `calendar`, `asymmetr`
anywhere after R38's heading: **no match. Unscheduled.**

R29.3 was implemented exactly as ruled and then wired somewhere it cannot speak.
**That is a fourth instance of R21's shape** — a value computed correctly and
read by no production path — and the first where the producer *is* read, just
never on the branch that matters. **Scheduled: chunk 4**, beside the other
banner-scope work.

#### R39.3 — `check_contract.py` can pass against a file in another checkout

Worse than the audit reported, and this one is about a gate rather than a
document. The citation regex excludes `:`, so a rooted Windows citation loses its
drive letter and is joined to the *drive* of `root`. **The gate can resolve a
citation into a different checkout and pass against a file that is not in the
tree being checked.** Live example in the repo: `docs/release-evidence.md:104`.

A gate that can be satisfied by a file outside the tree is not a gate. **Scheduled
as its own small chunk**, ahead of the document work, on the same reasoning that
put finding 35 first: a check nobody can trust makes every result downstream of
it unfalsifiable.

#### R39.4 — the merge gate is green on a tree where R29.1's fix is inverted

The most uncomfortable measurement in the ingest. Three spot-checked mutations
survive — **including inverting R29.1's fix**, the exemplar defect this entire
audit brief was built around:

```
scripts/check_merge.py  ->  [PASS] on all seven checks, including [PASS] pytest
```

Not merely "the file's own tests pass". **The project's own merge gate is green
on a tree where the disclosure is inverted.** Seven checks, 2,241 tests, and the
sentence that says whether these numbers came from a real provider can be turned
inside out without one of them noticing.

And separately: **`warnings: null` still crashes the renderer** — `TypeError`,
exit 3, no HTML written — with no test aware of it.

**Ruling: both go to the chunk that closes them, and neither is folded into a
document chunk.** The first is a test gap on the single most load-bearing
sentence in the document; the second is the spec's named "crash" reachable from
one null. Chunk 0, before finding 35, because they are the cheapest and the
loudest.

#### R39.5 — finding 1, ruled by splitting it

R38 demoted the pseudoreplication finding to chunk 6 on the grounds that the
report faithfully echoes what `comparison.py` computed. The ingest's verifier
disagreed and its argument is better than the demotion:

> **R9 already established the principle** — *"Twenty completions from four items
> are not twenty observations… correlated by construction"* — and applied it
> **only to the dimension matrix**, leaving the headline gate computing the
> forbidden way.

Both are right about different things, so the finding splits:

1. **The unreconciled sentence is the report's, and is scheduled.** The page
   prints `n=60` as evidential depth and, eight times, "all 5 draws identical",
   and nothing connects them. Worse, the one sentence that could disclose it
   asserts the opposite — that the five draws carry distributional information.
   That sentence is report-authored and it is wrong on this evidence. **Chunk 6,
   promoted to its head.**
2. **The gate's unit is `comparison.py`'s and is outside this rebuild's declared
   files.** Recorded here as an open question with R9's own words attached, so
   whoever opens `comparison.py` next finds the argument already made rather than
   re-deriving it. **Not scheduled by this plan**, and saying so is the point:
   R28.1's lesson is that an unscheduled ruling must be visibly unscheduled
   rather than quietly assumed.

#### R39.6 — a harness caveat worth more than most findings

`_visible()` includes SVG `<title>` text, so **tooltips read as visible prose.**
Anyone asking "is X on the page?" gets a false positive for every
`<title>`-only disclosure — which is precisely the mechanism behind findings 40,
41.12, 45 and 47.

A measurement tool that counts a tooltip as text will report a
screen-reader-only disclosure as a rendered one, and that is the exact class of
defect the audit exists to find. **Whoever reruns this must fix the harness
first**, or the harness will hide the findings it was built to surface.

### R40 — the ledger of what is ruled and not scheduled

R28.1 named this project's most repeated failure: **a ruling in the plan with no
brief behind it is a ruling nobody will execute.** It has now happened five
times — R21.5, R25.5's table edit, R30.5's filter, R31.4's asymmetry, and
finding 23 scheduled *after* it was already fixed. Every one was caught by
looking rather than remembering.

The standing check ("before dispatching a brief, grep the merged code for the
ruling it implements") catches the ones that reach a brief. It does nothing for
findings that never reach one. **This section is the other half: everything
ruled, found or endorsed that has no chunk, in one place, with what it is
waiting on.**

**Rule for this list: an entry leaves it only by being dispatched or by being
explicitly withdrawn with a reason.** It does not leave by being fixed
incidentally — if a chunk closes one, the merge says so and strikes it here.

#### R40.1 — waiting on a chunk already in flight

| Item | Waiting on | Source |
|---|---|---|
| R34.3's rendering — the series-scope provenance claim in the timeline section | C14c is merged, so this is now free | R34.3 |
| R37.6's third counter — `scripted_among_named`, and `_counted_paragraph` still says *"the other 2 do not"* about comparisons that recorded no adapter | chunk 0 is in `_scripted_paragraph` | R37.6 |
| The four remaining coercion splits — `goldenset_hash`, `judges_hash`, `config_hash`, `config_path` | needs a ruling, not a sweep: `config_path` has a second `or` downstream (`source = config_path or THRESHOLD_SOURCE_UNRECORDED`), so a recorded `"0"` would flip the threshold source | C22b's fix |

#### R40.2 — found, proved, and never scheduled

**`report._environment()` recompiles the jinja2 template on every render.**
Measured: **100 ms of each 109 ms render**, against an actual render cost of
~3 ms — 1.304 s of a 1.320 s five-render sample was compilation. A production
performance defect, found while building the absence sweep and correctly not
fixed there. One `functools.lru_cache` away.

**A raw Python `None` reaches the page.**
`judges[0].item_counts.baseline.passing = null` renders `None / 1 / 2`. Not a
conflation — a leaked repr.

**45 numeric leaves are never rendered at all**, including the entire
`completion_rates` block, every `flips[*]/gains[*].changes[*].{baseline,candidate}_{passes,n}`
(the page prints a precomputed `label` string instead, so those numbers are dead
payload), `latency.{baseline,candidate}.n`, `judges[0].regression.*` and
`judges[0].power.*`. **This is R21's finding in a place nobody has looked.**
Either they are superseded — in which case the payload should stop carrying them
— or the page is hiding numbers it holds. Both answers are chunks; neither is
free.

**`judges[0].item_counts.items` is a C14c regression, not a longstanding
conflation.** A golden set recorded as **zero items** renders as the word
`unrecorded`, byte-identical to key-removed and key-null — the mirror violation,
stated in this document's own vocabulary. Provenance came from the second
machine's tooling: 46 collisions on `main` against 45 on its baseline, same tool,
same fixtures, only the tree differing. **The provenance decides the ranking** —
a regression belongs in a fix pass, a longstanding conflation in a pinned list.

**T0 corrupts the exit code, differently on each platform.** `OSError` EINVAL and
**exit 120** on Windows — outside the tool's documented exit-code vocabulary
entirely — against `SystemExit(1)` on macOS. One defect, two wrong codes.

**Finding 4's `<title>`.** `_warned_title` keys on series-scoped `is_demo` and
prefixes a headline-scoped string, so a document with a real headline over a
scripted history shouts `FAKE MODELS` over two real production model ids — on the
one surface that, per its own docstring, *"survives being pasted into a chat
window as a link preview"*. **R34.3 ruled beside this and not on it**: it refuses
to equalise the scopes, which is right for the band and leaves the title
untouched.

**`check_contract.py` can pass against a file in another checkout** (R39.3). Its
citation regex excludes `:`, so a rooted Windows citation loses its drive and is
joined to the drive of `root`. A gate satisfiable by a file outside the tree is
not a gate.

**R37.2's empty-tag-universe branch has no producer behind it.** C14b's fix pass
established that `dimensions._index` gives every item at least `UNTAGGED` and
`GoldenSet.parse` refuses an empty golden set outright, so `available=True` with
`tags=()` is **live template code no producer can reach**. It is pinned with a
hand-narrowed matrix so deleting it is a visible edit. **Deleting it is the
honest alternative** and was not that pass's call to make.

**One more assertion of R37.1's shape, outside C14b.**
`test_a_judge_tested_on_outcomes_says_so_in_its_own_row` (`test_report.py:1952`)
— its name says *"in its own row"*, its docstring says the note lives *"inside
the table, not in a legend"*, and its body asserts two strings appear anywhere in
the document. A template printing both in a legend at the foot of the page — the
exact implementation the docstring refuses — passes it. `_Grid` now exists and
makes the fix cheap.

#### R40.3 — visibly unscheduled, and deliberately

**The demo's statistical unit is `comparison.py`'s, and this plan does not own
it.** R39.5 split finding 1: the report's unreconciled sentence is scheduled; the
gate computing over 60 correlated draws is not. R9 already established the
principle — *"Twenty completions from four items are not twenty observations…
correlated by construction"* — and applied it only to the dimension matrix.
**Whoever opens `comparison.py` next finds the argument already made.** Saying
so here is the point: R28.1's lesson is that an unscheduled ruling must be
*visibly* unscheduled rather than quietly assumed.

**The repo-wide `ruff format` drift.** Tree at 88, `pyproject.toml` says 100,
~26 files. Still last, not next: it touches every file and would conflict with
every branch in flight simultaneously.

**Sixteen Tier 2 findings are not scheduled as written** (R38.2). They harden
reads against a writer that does not exist. **Chunk 1's ruling decides them** —
if a foreign log is refused, they are correctly unreachable; if it is accepted
and disclosed, they become real work with a known trigger. Two exceptions worth
keeping either way: **20j**, which needs no payload edit at all (delete two
artifact files), and **20c**, a *derived* key (`failures = n - successes`) whose
absence fabricates a 100% baseline and moves every delta fifteen points.

### R41 — `{{ x or default }}`: two sites wrong, thirty-seven right, and the count is the ruling

R40.1 left four coercion splits open — `goldenset_hash`, `judges_hash`,
`config_hash`, `config_path` — with a note that `config_path` has a downstream
`or` and so needs a ruling rather than a sweep. Reading the code to rule on it
found the `or` is not downstream of one field. **It is an idiom, used 45 times in
the template**, and the obvious next move was a 45-site sweep.

**Counted first, and the count is the whole answer:**

```
template interpolations using a bare `or` default   45
  numeric-valued (a measured zero takes the default)  2
  string-valued  ("" already means absence here)     37
  (remaining are compound expressions, judged individually)

str(x or "") coercions in report.py   3
                       in series.py   0
```

#### R41.1 — the two that are wrong

```
{{ candidate_field.key.n_per_item or dash }}
{{ model.n_per_item or dash }}
```

Jinja's `or` is falsy-triggered, so a **recorded `n_per_item: 0`** renders as an
em dash — a measured zero rendering as an absence. That is R38.4's missing half,
in the template, on a field a reader uses to judge how deep the sampling was.

The merged absence sweep already flagged the *symptom* — `n_per_item = 0` prints
`n per item —` and flips the run-history section to *"0 of those 1
comparison(s)… What became of the rest is below"*. **This is its cause**, and it
is one line each.

**Ruling: both become an explicit `is none` test.** A number is absent only when
it is `None`; zero is a measurement and must render as `0`.

#### R41.2 — the thirty-seven that are right, and why that must be written down

`{{ model.baseline.model_id or dash }}`, `{{ judge.rubric_hash or dash }}`,
`{{ caveat.point.created or 'no recorded date' }}` and thirty-four like them are
**correct as written**, because this codebase deliberately uses `""` to mean
*nothing to say* for strings. The absence sweep reached the same conclusion
independently and from the other direction: applying "a measured zero" to strings
produced 67 spurious findings — *"every hash, path and `model_id`: `"" ==
absent`, which are noise here because the codebase deliberately uses `""` as
absence."*

**Ruling: they stay, and this section is why.** Two agents have now
independently derived that `""` is absence for strings here; the next one should
not have to. A sweep converting all 45 would have turned thirty-seven honest
sentences into `is none` tests that never fire, and made the two real defects
harder to see by burying them in a diff of forty-five.

#### R41.3 — the near-miss is the point

**I was one step from ruling a 45-site sweep off an unmeasured instinct**, on the
strength of noticing the idiom and recognising its shape. The reasoning was
sound: a falsy-triggered default over a project whose central rule is that
absence and zero must differ is exactly the right thing to be suspicious of. The
code was different — 37 of 45 sites are over values where the project has already
decided that falsy *is* absence.

That is the fifteenth instance of METRICS' error taxonomy and the first one
caught **before** it reached a brief, by the taxonomy's own rule: *prescribe
outcomes, prove mechanisms.* The mechanism here would have been "convert every
`or` default", and proving it took two greps.

**The general form, worth having:** an idiom used many times is not evidence of a
defect used many times. It is evidence of a *decision* used many times, and the
question is whether the decision was right for each class of value it covers —
which is a counting question before it is a judgement one. **Count the
population, split it by the property that matters, and rule on the parts.** A
ruling that cannot say how many sites it touches is a ruling that has not been
measured.

#### R41.4 — the four fields R40.1 left open, now settled

`goldenset_hash`, `judges_hash` and `config_hash` are **strings**, so R41.2
covers them: `""` is absence, the coercion split is invisible on any value they
can hold, and they need no change. `config_path` is likewise a string — its
downstream `source = config_path or THRESHOLD_SOURCE_UNRECORDED` is the same
honest idiom, one layer over.

**Struck from the R40 ledger.** The entry said this needed "a ruling, not a
sweep"; the ruling is that there is nothing to convert, and the reason is
recorded so the split is not re-discovered as a defect in three weeks.
