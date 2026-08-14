# migration-kit Session 3 — module contract (DRAFT, pending review)

Derived from `docs/build-plan.md` §1 (the `report.py`, `cli.py`, and Config
paragraphs), §2 (Session 3 exit criteria), §3 (Report and CLI rows), and §5 (the
definition of done). **This does not change the plan's scope.** It fixes the
details the plan left open, before code is written against them — the same
discipline that produced `contracts.py` before Session 1 and the Session 2
contract before judging.

Session 3 is the first session whose output a stranger sees. Everything below is
written to protect two claims that the definition of done makes in public: that a
keyless stranger reads a real report in under two minutes, and that the report is
change-control evidence a compliance reviewer can open on a machine with no
network. Both claims are broken by defaults, not by bugs — an unescaped template,
a webfont, a demo that quietly says GO — so most of the frozen rules here are
about defaults.

Two dependencies are declared up front. Session 3 depends on Session 2's
`JudgedArtifact`, `Thresholds`, and the payloads of `EVENT_COMPARISON` /
`EVENT_VERDICT`; the Session 2 contract is still a draft, so §1.2 below states
exactly what `report.py` requires from those payloads and treats everything else
about them as provisional. If Session 2 lands different key names, §1.2 is the
only part of this document that changes.

---

## 0. Verified facts about `opik-rigor 0.1.0`

Introspected from the installed package with
`C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe`, read-only, not
assumed. Each of these changes a decision below.

- `EvidenceLog(path)` exposes exactly `.append(event_type, payload) ->
  EvidenceRecord`, `.read() -> list[EvidenceRecord]`, `.last(event_type,
  **payload_match) -> EvidenceRecord | None`, and `.path`. `EvidenceRecord` is a
  dataclass of `(ts, event_type, payload, schema_version)`. That is the whole
  reading surface the report gets; there is no query, no index, no filter. The
  report therefore does one `.read()` and folds the records itself.
- **`.read()` tolerates a torn final line and drops it** (verified: a log with one
  good record plus a half-written second line returns one record), and **raises
  `EvidenceError` on a malformed line anywhere earlier** (verified: `malformed
  evidence at line 2 of …`). This is the same contract `RunArtifact.load` already
  implements, so the partial-render path in §2.6 has one rule for both files.
- **A missing evidence log reads as `[]`, not an error** (verified). rigor will
  not tell `migkit report` that the path was wrong, so `report.py` must check for
  the file itself, or a typo'd path renders as a blank "nothing happened" report.
- **`wilson_interval(0, 0)` and `wilson_lower_bound(0, 0)` raise `ValueError`**
  (`n must be >= 1, got 0; a rate over zero runs is not a rate`). A judge with zero
  observed completions — routine in a truncated run — crashes the renderer if it
  is handed to rigor. §2.6 makes `n == 0` a rendering state, never a computation.
- `assert_pass_rate` returns a dict already carrying `pass_rate`, `lower_bound`,
  `interval_lower`, `interval_upper`, `min_rate`, `confidence`, `method`;
  `assert_no_regression` returns `p_value`, `u_statistic`, `alpha`,
  `median_current`, `median_baseline`, `test`, `alternative`, `degenerate`. These
  are exactly the numbers the per-judge table and the methodology appendix print,
  so the comparison layer should pass those dicts through into the evidence
  payload verbatim rather than re-deriving them (§1.2).
- **`AdapterError` and `SampleTimeout` do *not* inherit from `RigorError`** —
  verified from their MROs; both subclass `Exception` directly. A CLI that catches
  `RigorError` to map errors onto exit 3 lets a provider failure escape as an
  unhandled traceback. §3.3 names them individually.
- `StatisticalAssertionError` (hence `RegressionError`, `PassRateError`) subclasses
  `AssertionError`. A blanket `except AssertionError` in the CLI would swallow
  genuine `assert` bugs; §3.3 does not use one.
- `is_pinned("fake-baseline-v1")` is `True` (as are `fake-judge-v1`,
  `fake-candidate-v1`). `PinnedJudge` enforces `require_pinned(adapter.model_id)`
  at construction, so **the demo can build a real `PinnedJudge` over a
  `FakeAdapter`** without a special case. The demo exercises the production judging
  path, not a bypass of it.
- The judge response format rigor asks for and parses is
  `{"pass": <bool>, "score": <1–5 or null>, "reason": "<sentence>"}` — verified by
  printing `PinnedJudge.build_prompt(...)` and by round-tripping responses through
  `evaluate`. `"passed"` is accepted as an alias, a JSON object embedded in
  surrounding prose is extracted, an out-of-range score raises `JudgeOutputError`
  rather than being clamped. Two consequences: the demo's fake judge script must
  emit that exact shape, and **the appendix's "why nonparametric" paragraph can
  state as fact that judge scores are a 1–5 ordinal scale** — which is the actual
  argument, not a generic one.
- `rich 15.0.0` degrades to ASCII box glyphs on its own when the target file's
  encoding cannot represent them (verified by rendering a `Table` into a
  `cp1252`-encoded stream: output came back as `+---+`, no exception). Terminal
  rendering therefore goes through a `rich.Console` bound to an explicit file and
  never through a bare `print()` of glyphs — `print()` is the thing that raises
  `UnicodeEncodeError` on a legacy Windows console, and it raises it *after* the
  work is done.
- `jinja2 3.1.6`'s `Environment` default is **`autoescape=False`** (verified). See
  §2.4: this single default is what stands between a model output containing
  `<img src="https://…">` and a report that fetches from the network.
- `opik_rigor.__version__` is a plain string `"0.1.0"`, printable into the
  provenance block.

---

## 1. What Session 3 reads, and from where

### 1.1 Invariant 2, made concrete

> The report renders from the evidence log, not from in-memory state, so a crashed
> run still renders a partial report.

The operative consequence is a signature rule, and it is the one rule in this
document most likely to be violated by convenience:

**No function in `report.py` accepts a `ComparisonReport`, a `JudgedArtifact`, or
any other live object produced earlier in the same process. Its inputs are paths.**

`migkit compare` therefore does not hand its in-memory result to the renderer. It
writes `EVENT_COMPARISON` and `EVENT_VERDICT` to the evidence log, and then calls
the renderer with the log's path, exactly as `migkit report` would tomorrow on a
different machine. This is not purity for its own sake: a partial-render path that
only runs after a crash is a path that has never run when you need it. Routing the
happy path through the same reader means every green test run exercises the
reconstruction, and the crashed-run case differs only in how many records it
finds.

The reconstruction reads, in order:

1. the evidence log (`EvidenceLog(path).read()`), for the comparison and verdict
   payloads, the thresholds, and the run/judging chronology;
2. the two `RunArtifact`s, by the paths recorded in the comparison payload, for
   completion text, durations, failure counts, `adapter`, and `parts`;
3. the two `JudgedArtifact`s likewise, for per-judge verdicts and reasons;
4. the golden set, by the path recorded in the payload, for item inputs and tags —
   **and only if its re-computed hash still equals the recorded
   `goldenset_hash`** (§2.5).

Anything a step cannot supply degrades that section of the report and is named in
the completeness strip. Nothing is imputed.

### 1.2 Required evidence payloads (dependency on Session 2 — provisional)

`report.py` requires these keys and nothing more. Session 2 may add keys freely;
removing or renaming one breaks the renderer, so this is the seam to agree before
either session writes code.

`migkit.comparison` payload:

| key | type | why the report needs it |
|---|---|---|
| `goldenset_hash`, `goldenset_path` | str | provenance block; gate for showing inputs |
| `judges_hash`, `config_hash`, `config_path` | str | provenance block; threshold echo |
| `baseline`, `candidate` | object: `{model_id, adapter, artifact, judged_artifact, n_per_item, parts}` | loads step 2/3 above; `adapter` drives the fake-model banner |
| `thresholds` | object | echoed verbatim into the report and the appendix |
| `judges` | array of `{name, model_id, rubric_hash, baseline: <assert_pass_rate dict>, candidate: <assert_pass_rate dict>, regression: <assert_no_regression dict or null>, test_ran: str, regressed: bool, floor_cleared: bool, underpowered: bool}` | the per-judge table, whole |
| `flips`, `gains` | array of `{item_id, judges: [name, …]}` | the flip list; outputs are read from the artifacts, not carried here |
| `latency` | object: `{baseline: {median, p90}, candidate: {median, p90}}` | secondary table |

`migkit.verdict` payload: `{verdict, exit_code, reason, decided_by}` where
`verdict ∈ {GO, NO-GO, REVIEW}`, `exit_code` is `Verdict.exit_code(verdict)`
recorded for audit, `reason` is one sentence naming the deciding judge and rule,
`decided_by` is the precedence-table rule number that fired (Session 2 draft §2,
rules 1–4). The report prints `reason` under the banner; a banner with no stated
reason is a colour, not a finding.

**Rule: the report never recomputes a statistic.** If a number is not in the
payload, the report shows it as unavailable. A renderer that re-derives a p-value
from the artifacts can disagree with the verdict that was recorded, and the one
thing a change-control document may never do is contradict itself.

The one exception is the flip *rendering*: flips are identified by Session 2 and
looked up here for their text.

### 1.3 Where the outputs live

`--evidence` defaults to `./.migkit/evidence.jsonl` (already git-ignored), run
artifacts to `./.migkit/`, the HTML to the path given by `--out`/`--html` with no
default write — a tool that silently drops files in the working directory is a
tool people run once. `migkit demo` is the exception and defaults to
`./migkit-demo-report.html`, because the demo's whole job is to leave something to
open, and the CI job already names that path.

---

## 2. `migration_kit.report`

### 2.1 Public API

```python
@dataclass(frozen=True)
class RateStat:
    """A pass rate with its intervals, or the absence of one.

    ``n == 0`` is a real state in a truncated run, and rigor raises ValueError
    rather than returning a rate over zero runs. So every optional field here is
    None exactly when there was nothing to measure, and the renderer prints an
    em-dash instead of a number it would have had to invent.
    """
    passes: int
    n: int
    rate: float | None
    interval: tuple[float, float] | None   # two-sided, for printing
    lower_bound: float | None              # one-sided, the number the gate used

@dataclass(frozen=True)
class JudgeRow:
    name: str
    model_id: str
    rubric_hash: str
    baseline: RateStat
    candidate: RateStat
    p_value: float | None
    test_ran: str            # "mann-whitney-u" | "mann-whitney-u-on-outcomes" | "not-run"
    regressed: bool | None
    floor_cleared: bool | None
    underpowered: bool
    note: str                # e.g. "scores absent; tested on pass/fail outcomes"

@dataclass(frozen=True)
class FlipRow:
    item_id: str
    tags: tuple[str, ...]
    input: str | None                    # None when the golden set is unavailable/changed
    baseline_outputs: tuple[str, ...]
    candidate_outputs: tuple[str, ...]
    judges: tuple[str, ...]              # judges under which it flipped
    reasons: Mapping[str, str]           # judge name -> the candidate-side reason
    truncated: bool

@dataclass(frozen=True)
class RunSummary:
    model_id: str
    adapter: str
    n_per_item: int
    items: int
    completions: int
    expected: int
    failures: int
    parts: int
    artifact_path: str
    latency_median: float | None
    latency_p90: float | None

@dataclass(frozen=True)
class Completeness:
    """Why this report may be short, in the report itself rather than a footnote."""
    complete: bool
    observed_completions: int
    expected_completions: int
    missing: tuple[str, ...]        # human sentences: "candidate run has 47 of 60 completions"
    last_event: str | None
    last_ts: str | None

@dataclass(frozen=True)
class MethodologySection:
    heading: str
    body: tuple[str, ...]           # paragraphs, already substituted with real numbers

@dataclass(frozen=True)
class ReportModel:
    verdict: str | None             # None when the run never reached a verdict
    reason: str | None
    decided_by: str | None
    generated: str                  # RFC3339, from contracts.utc_now() unless injected
    evidence_path: str
    evidence_hash: str
    tool_version: str
    rigor_version: str
    goldenset: Mapping[str, Any]    # hash, path, size, tag distribution, available: bool
    baseline: RunSummary
    candidate: RunSummary
    judges: tuple[JudgeRow, ...]
    flips: tuple[FlipRow, ...]
    gains: tuple[FlipRow, ...]
    thresholds: Mapping[str, Any]
    threshold_sources: Mapping[str, str]   # "pass_rate_floor" -> "./migkit.toml"
    hashes: Mapping[str, str]              # goldenset, judges, config, evidence
    completeness: Completeness
    warnings: tuple[str, ...]

    @classmethod
    def from_evidence(
        cls,
        evidence: str | Path,
        *,
        goldenset: str | Path | None = None,
        artifact_dir: str | Path | None = None,
        max_output_chars: int = 4000,
        now: str | None = None,
    ) -> ReportModel: ...

    @property
    def is_demo(self) -> bool: ...     # either side's adapter is a Fake*
    @property
    def exit_code(self) -> int: ...    # Verdict.exit_code(self.verdict or Verdict.ERROR)


def render_terminal(model: ReportModel, *, console: Console | None = None) -> None: ...
def render_html(model: ReportModel, out: str | Path, *, now: str | None = None,
                title: str | None = None) -> Path: ...
def render_html_string(model: ReportModel, *, now: str | None = None,
                       title: str | None = None) -> str: ...
def methodology_sections(model: ReportModel) -> tuple[MethodologySection, ...]: ...
def external_urls(html: str) -> tuple[UrlViolation, ...]: ...
def assert_self_contained(html: str, *, source: str = "<rendered>") -> None: ...
```

`from_evidence` raises `ArtifactError` for the two cases that are not partial data
but wrong data: the evidence file does not exist (rigor returns `[]`, §0), and the
log contains no `migkit.comparison` record at all so there is nothing to report
*on*. Everything else degrades. `goldenset=` and `artifact_dir=` exist for the one
real case where paths recorded on machine A do not resolve on machine B; when they
are given they override the recorded paths and the override is printed in the
provenance block, because a report that quietly read a different file than the one
recorded is worse than one that failed.

`render_html` writes with `encoding="utf-8", newline="\n"` explicitly. On Windows
`Path.write_text` defaults to the ANSI code page, which mangles or refuses
non-ASCII model output, and CRLF would make the file's hash differ per platform —
the same reason `.gitattributes` forces LF and the hashing convention normalises
it.

### 2.2 Structure of the HTML, in the plan's order

The plan fixes the order; this fixes what is in each part. Nothing may be inserted
above the banner except the demo warning, because the first screenful is the only
part some readers see.

0. **Fake-model warning**, present iff `model.is_demo` — a full-width red band
   reading `FAKE MODELS — these numbers describe scripted responses, not a real
   provider`, repeated in the `<title>`. §5.3 explains why it derives from the
   artifact and not from a CLI flag.
1. **Verdict banner** — the word `GO` / `NO-GO` / `REVIEW` / `NO VERDICT`, the
   one-sentence `reason`, the exit code that a CI system would have received, and
   the generation timestamp. Colour is redundant with the word, never the only
   carrier: this document gets printed and photocopied.
2. **What was compared** — a definition list, not prose: baseline and candidate
   `model_id` + adapter, golden-set path with `hash` and size and tag
   distribution, `judges_hash`, config path + `config_hash`, n per item, parts per
   run ("candidate completed in 2 parts" — the plan says a resumed run is noted,
   not hidden), completions observed/expected, failed completions per side, and
   the threshold echo with the source of each threshold (§4).
3. **Per-judge tables** — one table per judge: pass rate for A and B with the
   two-sided Wilson interval printed and the one-sided lower bound printed
   *separately and labelled*, since rigor's own docstring warns they are not
   interchangeable and a reader who conflates them will think the gate is looser
   than it is; the Mann-Whitney p-value with alpha; which test actually ran; and
   the three booleans (`regressed`, `floor_cleared`, `underpowered`) that fed the
   decision table. A judge whose scores were absent carries its `note` inside the
   table, not in a legend.
4. **Latency**, secondary and explicitly labelled descriptive-only — median and
   p90 per side. It is never a gate, and saying so in the table stops it becoming
   one by habit.
5. **Flip list** — one `<details>` per flipped item, summary line
   `item-id · tags · judges that flipped`, body containing the input, all n
   baseline outputs, all n candidate outputs, and the candidate-side judge reason.
   Flips are ordered by golden-set order, which is stable across runs; ordering by
   "severity" would require a magnitude the comparison does not produce. `gains`
   follow in a second, collapsed section under a heading that says an improvement
   elsewhere does not offset a regression here — the number is shown because its
   absence would make the report an argument rather than a measurement, and the
   sentence is there because someone will otherwise net them.
6. **Methodology appendix** — §2.3.
7. **Provenance footer** — tool version, `opik_rigor` version, evidence log path
   and hash, all four content hashes, and the exact command line that produced the
   run when it was recorded.

`<details>`/`<summary>` is the whole expansion mechanism. **The page contains zero
`<script>` elements** — that is a testable assertion (§6) and it is what makes
"self-contained" cheap to keep rather than a thing that erodes one convenience at
a time.

### 2.3 The methodology appendix is generated, not pasted

Sections, each built from `model` so it cannot go stale:

- *What was tested* — n per item, item count, total draws per side, judge count,
  each judge's rubric hash.
- *Why these tests* — Wilson for the interval and the gate, naming the two-sided /
  one-sided split and the actual confidence used; Mann-Whitney U, one-sided,
  `alternative="less"`, with the alpha actually used, and the statement that an
  improvement is not a regression so the test is deliberately one-tailed.
- *Why nonparametric* — judge scores are a bounded 1–5 ordinal scale (verified in
  §0 as rigor's own rubric contract). The distance between 3 and 4 is not the
  distance between 4 and 5, so a t-test's interval-scale assumption is not merely
  unmet, it is unmeetable; ranks are the only thing the data supports.
- *What REVIEW means* — the underpowered definition actually used, with this run's
  numbers substituted, and the sentence that REVIEW is never silently converted to
  GO (invariant 5).
- *The decision table* — the four precedence rules with this run's thresholds
  substituted and the rule that fired marked.
- *What this report is not* — no cost model, no longitudinal trend, no claim about
  items outside the golden set. The plan's scope fence, stated to the reader so
  the document cannot be over-read.

Because every section is substituted from the model, a test can change a threshold
and assert the appendix text changed (§6). A hardcoded appendix passes a "contains
the word Wilson" test forever, including after the confidence is changed to 0.80.

### 2.4 Self-containment: no external URL, and how a test proves it

The rule: **no URL of any scheme other than `data:` may appear in any position of
the rendered document that a browser would fetch.** No CDN script, no
`<link rel=stylesheet>`, no webfont, no remote image, no tracking pixel, no
`@import`, no protocol-relative `//host/…`, no `<iframe>`, `<object>`, `<embed>`,
`<base>`. CSS is one inline `<style>`; typography is generic families only
(`system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`). The reason is stated
in the plan and is not aesthetic: this file is opened inside a compliance review
on a machine with no route to the internet, where a `<link>` to a stylesheet
renders the document as unstyled text and a missing webfont silently changes what
"the report looked like" — and where an outbound request from a document
containing model outputs is itself the finding.

Two mechanisms, and both are needed:

**Escaping.** The Jinja environment is
`Environment(loader=PackageLoader("migration_kit", "templates"),
autoescape=select_autoescape(default_for_string=True, default=True),
undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)`. jinja2's
default is `autoescape=False` (verified, §0), and model outputs are arbitrary
attacker-influenced text: an output containing `<img src="https://tracker/x.png">`
becomes a real network fetch in an unescaped template. `StrictUndefined` is there
for a different failure — a renamed model field would otherwise render an empty
verdict banner rather than raising, and an empty banner is a document that says
nothing while looking complete.

**Detection at render time, not only in tests.** `render_html` calls
`assert_self_contained` on its own output *before writing the file*. A template
edit that adds a font link fails the render rather than shipping a file that only
CI notices. The cost is one parse per report.

`external_urls(html)` is the detector, built on stdlib `html.parser.HTMLParser` —
not a regex over the raw text, because a regex cannot tell a URL that appears as
escaped text (harmless: no fetch) from one that appears as an attribute value
(a fetch). It returns `UrlViolation(line, column, tag, attribute, value, reason)`
for:

- any attribute value matching `^\s*(?!data:)[a-zA-Z][a-zA-Z0-9+.-]*:` — i.e. any
  scheme other than `data:`;
- any attribute value starting `//`;
- any fetching attribute (`src`, `href`, `srcset`, `poster`, `data`, `action`,
  `formaction`, `background`, `cite`, `longdesc`, `manifest`, `usemap`) whose value
  is neither a `#`-fragment nor a `data:` URI;
- any occurrence of `url(` not followed by `data:`, or of `@import`, inside a
  `<style>` element or a `style=` attribute;
- the presence of `<script>`, `<link>`, `<iframe>`, `<object>`, `<embed>`, or
  `<base>` at all.

`assert_self_contained` raises with every violation listed, each naming line, tag
and attribute, so a template author sees the offending line rather than "the
report is not self-contained".

Three tests, and the middle one is the one that matters:

1. the rendered demo report yields `external_urls(...) == ()`;
2. **the detector is not vacuous**: a fixture string containing
   `<link rel="stylesheet" href="https://cdn.example/x.css">` and
   `<img src="//cdn.example/logo.png">` and `<style>@import url(https://f/f.css)</style>`
   yields exactly three violations at the expected tags. Without this, a detector
   that always returns `()` passes test 1 forever;
3. **escaping is load-bearing**: a golden set whose input and a completion whose
   output each contain `<img src="https://evil.example/pixel.png">` and
   `<script>fetch('https://x')</script>` renders to a document that still yields
   `external_urls(...) == ()`, and the literal text is visible in the page. This
   asserts that hostile model output is neutralised rather than that we were lucky
   with our own template.

### 2.5 Showing inputs only when they are the inputs that were tested

The golden set is loaded for the flip list. Before its text is used, its hash is
recomputed and compared to the `goldenset_hash` recorded in the evidence. On
mismatch — or if the file is gone — the flip list still renders, with item ids,
tags and both models' outputs, and `input` is `None` with a visible band saying
the golden set at that path no longer matches the one that was run and the inputs
are therefore not shown. Pairing today's file with last week's outputs would be a
fabricated exhibit, and it would be indistinguishable from a real one.

### 2.6 The partial-render path

A truncated evidence log has four distinguishable shapes, and the report handles
each without an exception:

| what survived | what the report shows |
|---|---|
| log ends with a torn line | rigor drops it (verified §0); no visible effect beyond the missing record |
| `migkit.run_started` but no `migkit.comparison` | `ArtifactError` — there is no comparison to report on. This is the one refusal. |
| comparison present, verdict record missing (killed between the two) | banner reads **NO VERDICT — the run ended before a verdict was recorded**; all tables render; CLI exits 3 |
| comparison and verdict present, but an artifact on disk is short | full report, with the completeness strip and per-judge shortfalls |

Rules that hold in all of them:

- **Counts are observed over expected, everywhere.** Every judge table and both run
  summaries print `47 / 60 completions`. A rate over the items that finished is
  biased whenever the run died on a slow or hard item — the exact circumstance
  that kills runs — so the shortfall travels next to the number rather than in a
  footnote.
- **Nothing is imputed, pro-rated or extrapolated.** Missing samples are missing.
- **`n == 0` is a state, not a computation.** `RateStat` carries `rate=None,
  interval=None, lower_bound=None`, the cell prints `—`, and rigor's
  `wilson_interval(0, 0)` ValueError (verified §0) is never reached.
- The completeness strip names the last event type and its timestamp, so the reader
  knows where the run stopped, and prints the command that resumes it.
- A partial report is evidence, never a decision: with no verdict record the CLI
  exits 3, matching the README's "the tool could not produce a verdict".

### 2.7 Terminal rendering

`render_terminal` writes through one `rich.Console` and nothing else. Verdict as a
`Panel` (green/red/yellow), "what was compared" as a two-column table, one table
per judge, the flip list as ids with a per-item one-line summary and a pointer to
the HTML for the full text — a terminal is not where anyone reads twenty pairs of
model outputs. Last line of stdout is always `VERDICT: <X> (exit <n>)`, which is
also the last line under `--quiet`; a CI log that scrolls past 200 lines of table
still ends with the finding.

Determinism for tests: `render_terminal(model, console=Console(file=buf,
width=100, no_color=True, force_terminal=False))`. Colour must never be the only
carrier of a fact, so a `no_color` render still contains the verdict word, and
that is asserted.

---

## 3. `migration_kit.cli`

`argparse`, per the decision already recorded in PROGRESS.md.

### 3.1 Surface

```
migkit [--version] [--quiet] [--traceback] <command> …

migkit run      --goldenset PATH --model ID [--adapter {fake,anthropic,openai-compat}]
                [--n INT] [--concurrency INT] [--timeout SEC] [--artifact PATH]
                [--out-dir DIR] [--fresh] [--evidence PATH] [--config PATH]
migkit compare  --baseline ARTIFACT --candidate ARTIFACT --judges CONFIG
                [--config PATH] [--evidence PATH] [--html PATH] [--no-terminal]
migkit report   EVIDENCE [--html PATH] [--goldenset PATH] [--artifact-dir DIR]
                [--no-terminal]
migkit demo     [--out PATH] [--work-dir DIR] [--keep]
```

```python
def build_parser() -> argparse.ArgumentParser: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

`main` returns the exit code rather than calling `sys.exit`, so unit tests assert
`main([...]) == 1` in-process; the console-script entry in `pyproject.toml`
(`migkit = "migration_kit.cli:main"`) already wraps it in `sys.exit`. A returned
int and a process exit status are two different claims, so §6 asserts both.

Two readings of the plan are resolved here and should be confirmed (§7):

- **`migkit report <comparison>` takes the evidence log path.** Under invariant 2
  the comparison *is* the evidence log plus the artifacts it names; there is no
  separate comparison file, and inventing one would be a second source of truth. A
  directory argument is accepted and resolved to `<dir>/evidence.jsonl`.
- **`migkit compare` judges, then compares.** The plan gives `compare` the two run
  artifacts and the judge config and gives no `migkit judge` verb, so `compare`
  runs `judge_artifact` on both sides (resumable, writing `JudgedArtifact`s beside
  the run artifacts) before comparing. Re-running `compare` after a fixed
  threshold therefore costs no judge calls.

### 3.2 Exit codes

Codes come from `contracts.Verdict.EXIT_CODES` through `Verdict.exit_code(...)`.
No integer literal `0`/`1`/`2`/`3` appears in `cli.py` as an exit value — a second
copy of the CI contract is a second thing to forget to update.

| code | produced by |
|---|---|
| 0 | `compare`/`report`/`demo` whose recorded verdict is `GO`; **and** `run`, which completes without a verdict |
| 1 | recorded verdict `NO-GO` |
| 2 | recorded verdict `REVIEW` |
| 3 | every error (§3.3); a report with no verdict record; a verdict string the tool does not recognise |

`migkit run` never returns 1 or 2 — it produces no verdict, so its 0 means "the run
completed", not "GO". This is stated in `--help` and in the README table, because a
pipeline that gates on `migkit run` would otherwise be gating on nothing.

An unrecognised verdict string maps to 3 via `Verdict.exit_code`'s existing
default. That is the right default: a verdict the tool cannot interpret is a tool
error, and mapping it to GO would be the one failure mode that ships a bad model.

### 3.3 Which exceptions map to 3

Caught at the `main` boundary, printed to **stderr** as `migkit: <type>: <message>`
with no traceback, returning 3:

- every `migration_kit.errors.MigrationKitError` — `GoldenSetError`,
  `ArtifactError`, `JudgeConfigError`, `JudgeReliabilityError`, `ConfigError`.
  These already carry the explanatory messages; the CLI adds nothing to them.
- every `opik_rigor.RigorError` — `EvidenceError`, `ModelPinError`,
  `JudgeOutputError`, `RubricDriftError`, and the `StatisticalAssertionError`
  family.
- `opik_rigor.AdapterError` and `opik_rigor.SampleTimeout`, **named separately
  because they do not inherit `RigorError`** (verified, §0). Catching only
  `RigorError` here is the mistake this line exists to prevent.
- `OSError` (unreadable path, full disk) and `ValueError` raised by argument
  validation at the boundary.

A `RegressionError` or `PassRateError` reaching `cli.py` is a bug in
`comparison.py`, not a NO-GO: the verdict is read from the evidence record and is
never inferred from an exception type. It maps to 3 like any other error, and the
message says so.

Anything else — a `KeyError`, an `AttributeError` — is an unclassified bug in the
tool. It also returns 3, but its traceback is **always** printed regardless of
`--traceback`, because for an unanticipated failure the traceback is the only
diagnostic and suppressing it costs the bug report.

`KeyboardInterrupt` returns 3 with `interrupted; the artifact is valid and can be
resumed with: <command>` on stderr. This is a deliberate departure from the shell's
128+SIGINT convention: the plan documents four exit codes as *the* CI contract, and
a fifth value appearing only on Ctrl-C would break the promise that the code is
always one of four. `BrokenPipeError` from the terminal writer (`migkit report |
head`) is swallowed and does not change the code — the reader chose to stop.

### 3.4 Streams

The report goes to **stdout**; progress, warnings and errors to **stderr**. So
`migkit report x.jsonl > report.txt` captures the document and still shows progress
in the terminal, and a CI log that interleaves them stays readable. `--quiet`
silences progress but never the verdict line or errors.

---

## 4. The TOML config surface

One file, `migkit.toml`, holding the judge definitions (Session 2's `[[judge]]`)
and the thresholds. The plan asks for one config file, and splitting judges from
thresholds would produce two hashes to reconcile and one more way for a comparison
to be run under something other than what the report says.

```toml
[thresholds]
pass_rate_floor         = 0.90   # one-sided Wilson lower bound on B must clear this
alpha                   = 0.05
confidence              = 0.95
judge_failure_tolerance = 0.05
min_completions         = 20

[run]
n            = 5
concurrency  = 4
timeout      = 60.0

[report]
max_output_chars = 4000   # per output block in the HTML; truncation is always visible
```

Frozen rules:

- **Precedence is CLI flag > config file > built-in default**, and the report echoes
  each threshold *with its source* — `pass-rate floor 0.90 (./migkit.toml)` versus
  `(default)` versus `(--floor)`. The plan's requirement is that nobody can quietly
  loosen a gate without it showing in the evidence; an echoed number without its
  provenance does not achieve that, because the reader cannot tell a deliberate
  project policy from a flag someone added to make the build go green.
- **Discovery is `--config`, else `./migkit.toml`, else built-in defaults.** No
  walking up parent directories, no `~/.migkit.toml`. A threshold inherited from a
  home directory makes the same command produce different verdicts on two machines,
  and the only trace would be the echo nobody compares.
- **Unknown keys are a `ConfigError`**, naming the key and the allowed set — the
  same argument `goldenset.py` already makes for its `ALLOWED_KEYS`: a mistyped
  `pass_rate_flor` leaves the gate at 0.90 while its author believes it is at 0.80,
  and the failure surfaces as a verdict nobody can explain.
- **Ranges are validated at load**: `0 < alpha < 1`, `0 < confidence < 1`,
  `0 ≤ pass_rate_floor ≤ 1`, `0 ≤ judge_failure_tolerance ≤ 1`, `min_completions ≥
  1`, `n ≥ 1`, `concurrency ≥ 1`, `timeout > 0`. Out of range is `ConfigError`, not
  a clamp; a clamped threshold is a silently different gate.
- The config file's own content hash goes in the report's provenance block, so the
  echoed thresholds can be tied back to a file in version control.
- `[report].max_output_chars` truncates long outputs in the HTML and **always
  renders a visible `… truncated at N characters` marker**. Invisible truncation in
  an exhibit is a misquotation.

`Thresholds` itself is owned by Session 2 (`judging.py`, per its draft §1);
Session 3 consumes it and must not define a second copy. `[run]` and `[report]`
sections are Session 3's, and `ConfigError` for them is raised from the same
loader.

**Python 3.10 has no `tomllib`** (stdlib from 3.11), and `requires-python` is
`>=3.10` with CI running 3.10. This is an open decision (§7 D1) and it affects
Session 2 as much as Session 3.

---

## 5. `migkit demo`

### 5.1 What is bundled

Package data under `src/migration_kit/data/`, loaded with
`importlib.resources.files("migration_kit.data")` so it works identically from a
source checkout, an editable install and a wheel:

- `demo_goldenset.jsonl` — 12 items, tagged across three slices (`arithmetic`,
  `extraction`, `refusal`), a few carrying `reference`. Twelve because the report
  must show a tag distribution and a flip list that is more than one row, and
  because 12 × 5 × 2 = 120 completions keeps the whole demo far inside two minutes.
- `demo_rubric.md` — one rubric, scored on rigor's 1–5 scale (verified §0).
- `demo.toml` — the thresholds the demo runs under, so the demo also demonstrates
  the threshold echo.

**Packaging trap, and it is already armed.** `.gitignore` line 18 ignores `*.jsonl`
and whitelists only `goldensets/**` and `tests/**/fixtures/*.jsonl`. A file at
`src/migration_kit/data/demo_goldenset.jsonl` would be untracked by git and, since
hatchling honours VCS ignore files when collecting build targets, absent from the
wheel — while `pip install -e .` and every local test still pass, because the file
is on disk. The demo would then fail only for the stranger in the definition of
done. Fix: add `!src/migration_kit/data/*.jsonl` to `.gitignore` (Session 3 edits
one line), and assert reachability through `importlib.resources` in the suite (§6).
Note also that the CI `demo` job installs editable and so cannot catch a wheel
omission; the `build` job should list the wheel's contents and assert the demo set
is inside it.

### 5.2 Keyless, under two minutes

Two `FakeAdapter`s with pinned-looking ids `fake-baseline-v1` and
`fake-candidate-v1` (both satisfy `is_pinned`, verified §0), scripted with a
**mapping or callable, not a sequence and not a seed** — the demo must be
bit-for-bit deterministic, because a demo that occasionally returns GO would
destroy the only claim the pitch makes. The candidate's script is degraded on a
constructed subset of items so the flip list is non-empty and the verdict is
`NO-GO` at the bundled n, matching the definition of done ("an HTML report that
shows a NO-GO verdict with confidence intervals and a flip list").

The judge is a real `PinnedJudge` over a third `FakeAdapter`
(`fake-judge-v1`) whose callable parses the prompt's `=== MODEL OUTPUT UNDER
EVALUATION ===` block and emits `{"pass": …, "score": …, "reason": …}` in rigor's
exact format (verified §0). The demo therefore runs the production judging path
rather than a bypass, which is the difference between demonstrating the tool and
demonstrating a mock of it.

`latency=0.0`, `concurrency` irrelevant, no sleeps, no network, no keys read from
the environment. Budget: the demo does 120 completions and 120 judge calls, all
in-process; the CI job's `timeout 120` is the outer bound and the suite asserts a
much tighter one (§6). Artifacts go to `--work-dir`, defaulting to a temporary
directory removed at exit unless `--keep`; the HTML goes to `--out` and its
absolute path is printed as the last line before the verdict line, because the
next thing the reader must do is open it.

### 5.3 Saying loudly that the models are fake

**Demo-ness is derived from the run artifacts, not from a flag.** `RunHeader.adapter`
already records `type(adapter).__name__` (Session 1, verified in `runner.py`), so
`ReportModel.is_demo` is true whenever either side's adapter name starts with
`Fake`. The consequence is the point: you cannot obtain a clean-looking report from
fake models by avoiding `migkit demo` — anyone wiring a `FakeAdapter` by hand gets
the same red band. A flag-driven banner would be exactly the banner that goes
missing in the screenshot someone pastes into a deck.

Five places say it, and none of them is a footnote: the `<title>`; the red band
above the verdict banner; the model ids themselves, which contain the word `fake`
and appear in every table; the `adapter: FakeAdapter` row in "what was compared";
and the appendix's opening paragraph, which states that the numbers describe
scripted responses and that the only real thing in the document is the machinery.
The terminal rendering carries the same band above its verdict panel.

---

## 6. Session 3 test inventory

Expanding plan §3's Report and CLI rows. Each is phrased as the assertion to write.
Per the working method, none of these expectations may be produced by running the
code under test: the HTML fixtures, the violation counts, and the exit codes are
all stated here so the test author has them from outside.

**Report — self-containment**

1. `external_urls()` over a fixture containing `<link href="https://cdn…">`,
   `<img src="//cdn…">` and `<style>@import url(https://…)</style>` returns exactly
   3 violations, with tags `link`, `img`, `style` — the detector is not vacuous.
2. `external_urls()` over the rendered demo report returns `()`.
3. `render_html_string()` of a model whose golden-set input and whose completions
   contain `<img src="https://evil.example/p.png">` and
   `<script>fetch("https://x")</script>` returns a document with `external_urls() ==
   ()`, and the escaped literal text appears in the page.
4. The rendered document contains zero `<script>` and zero `<link>` elements,
   counted by parser rather than by substring.
5. `render_html()` raises rather than writing when handed a template that violates
   the rule, and the destination file does not exist afterwards.
6. Rendering the same `ReportModel` twice with the same injected `now` produces
   byte-identical files; with a different `now`, exactly the timestamp differs.
7. The written file is UTF-8 with LF line endings and contains
   `<meta charset="utf-8">`, asserted on the bytes, on Windows as well as Linux.

**Report — content and thresholds**

8. Every threshold in the config appears in the HTML with its value and its source
   label; changing `pass_rate_floor` in the config changes both the echo and the
   appendix's decision-table text.
9. The appendix names the tests that actually ran, and a judge whose scores were
   absent yields the `mann-whitney-u-on-outcomes` wording in that judge's row.
10. Section order in the rendered document is banner → what was compared → judge
    tables → latency → flips → gains → appendix → provenance, asserted by comparing
    the order of their anchor ids as parsed.
11. Every hash printed in the provenance block equals the one recorded in the
    evidence payload, character for character (no truncation in the machine-readable
    positions; truncation is allowed only in visible labels).
12. A `RunHeader.parts == 2` artifact renders "completed in 2 parts"; the resumed
    run is disclosed, not hidden.
13. `is_demo` is true iff an adapter name starts with `Fake`; the fake band is
    present in that case and absent for an `AnthropicAdapter` header, with no CLI
    flag involved in either.
14. Flip rows carry the constructed flips' ids in golden-set order, with n baseline
    and n candidate outputs each, and the candidate-side judge reason.
15. When the golden-set file's current hash differs from the recorded one, no
    `input` text is rendered and the mismatch band is present; ids and outputs still
    render.
16. An output longer than `max_output_chars` renders truncated *and* carries the
    visible truncation marker.

**Report — partial render**

17. An evidence log truncated mid-line renders without raising, and the record count
    used equals `len(EvidenceLog.read())`.
18. An evidence log with a `migkit.comparison` but no `migkit.verdict` renders every
    table, shows the `NO VERDICT` banner, and yields `exit_code == 3`.
19. An evidence log with no `migkit.comparison` raises `ArtifactError`; a path that
    does not exist raises `ArtifactError` rather than rendering an empty report
    (rigor returns `[]` for a missing file — §0).
20. A judged artifact truncated to 47 of 60 completions renders `47 / 60` in both
    the run summary and the affected judge table, and no rate is scaled up to 60.
21. A judge with zero observed completions renders `—` for rate, interval and lower
    bound, and `wilson_interval` is never called with `n == 0`.
22. The completeness strip names the last event type and timestamp found in the log.

**CLI**

23. `main(["compare", …])` returns 0 / 1 / 2 for evidence recording GO / NO-GO /
    REVIEW respectively, table-driven over the three.
24. Every value returned by `main` is a value of `Verdict.EXIT_CODES`; asserted by
    inspecting the source for integer exit literals as well as by behaviour.
25. Each of `GoldenSetError`, `ArtifactError`, `JudgeConfigError`,
    `JudgeReliabilityError`, `ConfigError`, `EvidenceError`, `ModelPinError`,
    `RubricDriftError`, `AdapterError`, `SampleTimeout`, `RegressionError`, `OSError`
    raised from a stubbed command body returns 3 with a one-line stderr message and
    no traceback.
26. An unexpected `KeyError` returns 3 **with** a traceback on stderr.
27. `KeyboardInterrupt` returns 3 and the stderr message contains a resume command.
28. `main(["run", …])` returns 0 on a successful run and never 1 or 2.
29. A subprocess invocation of the installed `migkit` console script exits with the
    same status the in-process call returned, for at least one non-zero case —
    marked `slow`; a function returning 3 and a process exiting 3 are different
    claims.
30. `--quiet` still emits the final `VERDICT: …` line to stdout; errors still reach
    stderr.
31. The report goes to stdout and progress to stderr, asserted by capturing them
    separately.

**Demo**

32. `main(["demo", "--out", tmp/"r.html"])` runs with `ANTHROPIC_API_KEY` and
    `OPENAI_API_KEY` unset and no network, writes the file, and returns 1 (NO-GO).
33. Two demo runs produce identical verdicts and identical flip id lists — the demo
    is deterministic; no RNG, no seed, no wall-clock dependence.
34. The demo's report passes `assert_self_contained` and carries the fake-model band.
35. `importlib.resources.files("migration_kit.data")` resolves
    `demo_goldenset.jsonl` and `demo_rubric.md`, and the golden set loads through
    `GoldenSet.load` with 12 items and the expected tag distribution.
36. The demo completes within a wall-clock budget asserted in the suite (`slow`
    marker; the CI job's `timeout 120` is the outer bound, and a test that only
    trusted CI would notice a regression a week late).

**Dogfooding (plan §3's non-negotiable clause, Session 3's share)**

37. The demo's fake judge is scripted to disagree with itself on a known fraction of
    items; `assert_pass_rate` from rigor gates the demo's own judge-agreement rate,
    so the suite's stochastic component is itself under a rigor assertion, seeded and
    deterministic.

---

## 7. Open decisions for the lead

- **D1 — `tomllib` and Python 3.10.** `requires-python = ">=3.10"` and CI runs 3.10,
  but `tomllib` is 3.11+. Either add `tomli>=2.0; python_version < "3.11"` (a
  dependency the plan's "nothing else" did not anticipate, though it is the stdlib
  backport) or raise the floor to 3.11 and drop 3.10 from the matrix. This binds
  Session 2 first, since the judge config is TOML.
- **D2 — `errors.py` is frozen, and Session 3 needs one more failure kind.** A
  self-containment violation and "this evidence log cannot be rendered" are neither
  golden-set nor artifact errors. Default taken here: raise `MigrationKitError`
  (base) with a specific message, so nothing frozen is touched. The alternative is
  to unfreeze `errors.py` once to add `ReportError` — a lead call, not mine.
- **D3 — the CI `demo` job will fail as written.** `migkit demo` is frozen here to
  exit with its verdict code, which is 1 (NO-GO) by design, so
  `timeout 120 migkit demo --out demo-report.html` fails the step. Session 3 must
  amend that step to assert exit 1 explicitly. Confirm that rather than the
  alternative of making `demo` always exit 0 — a demo whose exit code disagrees with
  its own banner teaches the wrong contract and would hide the day the scripted
  quality difference stopped being detected.
- **D4 — `demo.py` as a module.** The plan says "six modules and a CLI, one page
  each". The demo's wiring is ~60 lines and putting it in `cli.py` makes that file
  two pages. Frozen default: a small `demo.py`. Veto if the module count is meant
  literally.
- **D5 — `migkit report <comparison>` means the evidence log path.** Confirm; the
  alternative reading is a separate serialised comparison file, which would be a
  second source of truth alongside the evidence log and would put the report back on
  in-memory state (invariant 2).
- **D6 — flip-list volume.** Frozen default: every sample of both models for each
  flipped item, behind `<details>`. At n=5 that is ten output blocks per flip. If a
  real 200-item set with 40 flips produces an unwieldy file, the alternative is the
  modal output per side with the rest behind a second expander — but that requires
  defining "modal", which the comparison layer does not currently produce.
