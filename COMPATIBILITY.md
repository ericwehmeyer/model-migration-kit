# opik-rigor compatibility

What model-migration-kit depends on in `opik-rigor`, how each claim here was verified,
and what a rigor release could change that would break this consumer.

The dependency was written by the same author. That makes this record more useful,
not less: it is the only place where the *published wheel* is described from the
outside, by the code that has to live with it. Convenience is not evidence, and
"I know what it does" is not a verified claim. Everything below was run against
the installed package and the output is pasted, not summarised.

**Verified against:**

| | |
|---|---|
| `opik-rigor` | **0.1.0** (installed from PyPI — see provenance below) |
| `jinja2` | **3.1.6** |
| `rich` | **15.0.0** |
| Declared bound | `opik-rigor>=0.1.0,<0.2` (`pyproject.toml`) |
| Python used to verify | **3.14.4** (Windows, `.venv`) |
| Python in CI | **3.10, 3.11, 3.12, 3.13** × `ubuntu-latest`, `windows-latest` (`.github/workflows/ci.yml`) |
| rigor's `Requires-Python` | `>=3.10` |
| rigor's runtime deps | `scipy>=1.10` (pulls `numpy`; here 1.18.0 / 2.5.2) |
| Verified on | 2026-08-13 |
| Method | Introspected the installed wheel with `inspect`, `typing.get_protocol_members`, and live calls in `.venv` |

```
$ .\.venv\Scripts\python.exe -c "import sys, importlib.metadata as md; print(sys.version); [print(d, md.version(d)) for d in ('opik-rigor','jinja2','rich','pytest','scipy','numpy')]"
3.14.4 (tags/v3.14.4:23116f9, Apr  7 2026, 14:10:54) [MSC v.1944 64 bit (AMD64)]
opik-rigor 0.1.0
jinja2 3.1.6
rich 15.0.0
pytest 9.1.1
scipy 1.18.0
numpy 2.5.2
```

**Provenance — it really is the published artifact.** PROGRESS.md claims rigor is
consumed from PyPI rather than as a path dependency to the sibling repo, and that
claim is checkable: pip writes `direct_url.json` into the `.dist-info` for anything
installed from a local path, a VCS URL, or a direct file, and omits it for an
index install.

```
$ cat .venv/Lib/site-packages/opik_rigor-0.1.0.dist-info/INSTALLER
pip
$ ls .venv/Lib/site-packages/opik_rigor-0.1.0.dist-info/
INSTALLER  METADATA  RECORD  WHEEL  entry_points.txt  licenses
$ cat .venv/Lib/site-packages/opik_rigor-0.1.0.dist-info/direct_url.json
cat: ...: No such file or directory
(no direct_url.json -> installed from an index, not a path)
```

**The rule this file exists to honour.** Introspection tells you what an API *is*.
It does not tell you what its documentation *says*. Those are separate claims
needing separate evidence — the sibling project learned this by asserting a fault
in someone else's docs on the strength of one HTML-to-markdown converter, and had
to retract it (see `opik-rigor/COMPATIBILITY.md`, "A correction to this file, not
to the docs"). So: every statement below is about **behaviour of the installed
0.1.0 wheel**, backed by a command. Where this file talks about rigor's *roadmap*
or its *intentions*, it cites `opik-rigor/PROGRESS.md` and says so.

---

## 1. The API model-migration-kit actually calls

Name by name. This is the whole dependency surface; anything not on this list is
not relied on and a rigor release may move it freely.

| Module | Imported from `opik_rigor` |
|---|---|
| `src/model_migration_kit/runner.py` | `Adapter`, `EvidenceLog`, `sample` |
| `src/model_migration_kit/judging.py` | `Adapter`, `EvidenceLog`, `JudgeOutputError`, `ModelPinError`, `PinnedJudge`, `require_pinned` — **plus** `SCORE_MIN` and `hash_rubric_file` from `opik_rigor.judge` |
| `src/model_migration_kit/comparison.py` | `EvidenceLog`, `PassRateError`, `RegressionError`, `assert_no_regression`, `assert_pass_rate`, `wilson_interval` |
| `tests/test_runner.py` | `EvidenceLog`, `FakeAdapter`, `PassRateError`, `assert_pass_rate`, `sample_of` |
| `tests/test_judging.py` | `EvidenceLog`, `FakeAdapter`, `JudgeOutputError`, `ModelPinError`, `PinnedJudge`, `is_pinned` — plus `SCORE_MIN`, `hash_rubric_file` from `opik_rigor.judge` |
| `tests/test_comparison.py` | `SCORE_MAX`, `SCORE_MIN` from `opik_rigor.judge` |

(Enumerated with `grep -rn "^from opik_rigor\|^import opik_rigor" --include=*.py .`
at the time of writing; the test files are under active development, so re-run it
rather than trusting this table after a session boundary.)

`require_pinned(model_id, *, context='judge')` is called by `judging.py` at config
load so an unpinned judge model is a `ConfigError` before any judge is
constructed; `context=` is passed and appears in the message
(`ModelPinError: judge 'x' in cfg.toml refuses unpinned model id 'gpt-4o'. ...`).

The signatures those names have in 0.1.0:

```
$ .\.venv\Scripts\python.exe -c "import inspect, opik_rigor; print(opik_rigor.__all__); ..."
__all__ = ['Adapter', 'AdapterError', 'AnthropicAdapter', 'Baseline', 'BaselineError',
 'EvidenceError', 'EvidenceLog', 'EvidenceRecord', 'FakeAdapter', 'JudgeOutputError',
 'ModelPinError', 'OpenAICompatAdapter', 'PassRateError', 'PinnedJudge', 'RegressionError',
 'RigorError', 'RubricDriftError', 'Run', 'SampleResult', 'SampleTimeout',
 'ScoreDistributionError', 'StatisticalAssertionError', 'Verdict', '__version__',
 'assert_no_regression', 'assert_pass_rate', 'assert_score_distribution', 'is_pinned',
 'require_pinned', 'sample', 'sample_of', 'wilson_interval', 'wilson_lower_bound']

Adapter(*args, **kwargs)
EvidenceLog(path: 'str | os.PathLike[str]') -> 'None'
FakeAdapter(*, model_id: 'str' = 'fake-scripted-v1', responses: 'ResponseSource', cycle: 'bool' = False, seed: 'int | None' = None, latency: 'float' = 0.0, fail_with: 'BaseException | type[BaseException] | None' = None, fail_after: 'int | None' = None, **forbidden: 'object') -> 'None'
PinnedJudge(adapter: 'Adapter', rubric_path: 'str | os.PathLike[str]', evidence: 'EvidenceLog', *, name: 'str' = 'default', accept_rubric_change: 'bool' = False) -> 'None'
Verdict(passed: 'bool', score: 'float | None', raw: 'str', model_id: 'str' = '', rubric_hash: 'str' = '', reason: 'str | None' = None) -> None
sample(fn: 'Callable[[], Any]', n: 'int', *, concurrency: 'int' = 1, timeout: 'float | None' = None, errors_as_failures: 'bool' = True, outcome: 'Callable[[Any], bool] | None' = None, evidence: 'EvidenceLog | None' = None, label: 'str | None' = None) -> 'SampleResult'
sample_of(values: 'Sequence[Any]', **kwargs: 'Any') -> 'SampleResult'
assert_pass_rate(result: 'PassData', min_rate: 'float', *, confidence: 'float' = 0.95, evidence: 'EvidenceLog | None' = None, label: 'str | None' = None) -> 'dict[str, Any]'
assert_no_regression(current: 'ScoreData', baseline: 'ScoreData', *, alpha: 'float' = 0.05, evidence: 'EvidenceLog | None' = None, label: 'str | None' = None) -> 'dict[str, Any]'
wilson_interval(successes: 'int', n: 'int', confidence: 'float' = 0.95) -> 'tuple[float, float]'
wilson_lower_bound(successes: 'int', n: 'int', confidence: 'float' = 0.95) -> 'float'
JudgeOutputError(message: 'str', raw: 'str') -> 'None'
PassRateError(message: 'str', **stats: 'object') -> 'None'
RegressionError(message: 'str', **stats: 'object') -> 'None'
```

Attribute-level dependencies, which a signature does not show:

```
Run           fields: index, value, outcome, error, duration
SampleResult  used:   .runs (runner reads every run, including the raised ones)
Verdict       used:   .passed, .score, .reason
PinnedJudge   used:   .name, .model_id, .rubric_hash, .evaluate(input, output)
EvidenceLog   used:   .append(event_type, payload) -> EvidenceRecord, .read() -> list
EvidenceRecord fields: ts, event_type, payload, schema_version
PassRateError / RegressionError: .stats  (dict)
```

`assert_pass_rate` and `assert_no_regression` accept more than a `SampleResult`:

```
PassData  = opik_rigor.sampling.SampleResult | tuple[int, int] | collections.abc.Sequence[bool]
ScoreData = opik_rigor.sampling.SampleResult | collections.abc.Sequence[float]
```

### Some names are imported from a submodule, not from `__all__`

`judging.py` does `from opik_rigor.judge import SCORE_MIN, hash_rubric_file`, and
the tests add `SCORE_MAX`. None of them is re-exported at package level:

```
$ .\.venv\Scripts\python.exe -c "..."
  opik_rigor.judge.SCORE_MIN                  in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.SCORE_MAX                  in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.hash_rubric_file           in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.hash_rubric_text           in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.OUTPUT_FORMAT_INSTRUCTION  in opik_rigor.__all__: False   top-level attr: False
```

Every other name on the list above **is** in `__all__` (checked individually;
`Adapter`, `EvidenceLog`, `FakeAdapter`, `PinnedJudge`, `Verdict`, `Run`,
`SampleResult`, `sample`, `sample_of`, `assert_pass_rate`,
`assert_no_regression`, `wilson_interval`, `wilson_lower_bound`,
`JudgeOutputError`, `PassRateError`, `RegressionError`, `RubricDriftError`,
`ModelPinError`, `SampleTimeout`, `is_pinned`, `require_pinned` — all `True`).

These are not private — no leading underscore, and `SCORE_MIN` / `SCORE_MAX` are
documented in `docs/session-2-contract.md` §0 — but they are the thinnest part of
the promise under invariant 1 ("model-migration-kit imports opik-rigor's *public* API
only"). A rigor release could move them without touching `__all__` and be within
its rights. The exposure is small and bounded: two floats used for imputation and
range checks, and one sha256 wrapper; any of them could be inlined in an
afternoon. The honest thing to do is ask rigor to re-export them; the honest thing
to record is that it has not.

### rigor's pytest plugin autoloads into this suite

```
$ cat .venv/Lib/site-packages/opik_rigor-0.1.0.dist-info/entry_points.txt
[pytest11]
rigor = opik_rigor.integrations.pytest_plugin
```

So the plugin is active in every `pytest` run here, without model-migration-kit asking.
`pyproject.toml` sets `--strict-markers`, which would fail on an unregistered
marker; the plugin registers correctly, so this is currently benign:

```
$ .\.venv\Scripts\python.exe -m pytest --markers
...
@pytest.mark.rigor_repeat(n, min_rate, confidence=0.95, errors_as_failures=True): run this
test n times and gate the pass rate with opik_rigor.assert_pass_rate. ...
```

Optional provider SDKs are absent, as invariant 3 requires, and importing
`opik_rigor` does not pull them in:

```
opik       ABSENT
anthropic  ABSENT
openai     ABSENT
scipy      installed
numpy      installed
```

---

## 2. The compatibility promise this implies

A rigor release breaks model-migration-kit if it changes any of:

1. **`Run`'s five fields** (`index`, `value`, `outcome`, `error`, `duration`) or
   `SampleResult.runs`. `runner.py` builds every `Completion` from a `Run`, so a
   renamed field is a hard failure at the data path.
2. **`sample`'s keyword names** `concurrency`, `timeout`, `outcome`, `evidence`,
   `label` — all five are passed by keyword.
3. **The `.stats` dict keys on `PassRateError`.** `comparison.py` reads
   `underpowered` and `runs_needed` off the raised exception and lets them decide
   REVIEW vs NO-GO (build-plan §6). These keys exist **only on the failure path**
   — see §4.3. A rename is a silent verdict change, not an exception, and is the
   single highest-consequence drift risk in this dependency.
4. **The `p_value` key** on `assert_no_regression`'s return and on
   `RegressionError.stats`; Holm-Bonferroni correction is applied to it.
5. **`Verdict.passed` / `.score` / `.reason`**, and `JudgeOutputError` being the
   exception raised for an unparseable judge response — the parse-failure
   tolerance in `judging.py` counts exactly that type.
6. **`PinnedJudge`'s constructor keywords** and the `judge.init`-record semantics
   of rubric-drift detection (§4.6).
7. **`SCORE_MIN`, `SCORE_MAX` and `hash_rubric_file` remaining importable from
   `opik_rigor.judge`** — and their *values*: `tests/test_comparison.py` asserts
   against the 1.0–5.0 range directly, so a configurable score range in rigor is
   a change here even if the constants survive.
8. **`EvidenceLog.append` / `.read`** and `EvidenceRecord`'s four fields, on which
   invariant 2 (the report renders from the evidence log) depends entirely.

9. **`FakeAdapter`'s constructor keywords** — `model_id`, `responses`, `cycle`,
   `latency`, `fail_with`, `fail_after`. Not production code, but the test suite
   and `migkit demo` are built on it, and CI's `demo` job is part of the
   definition of done. Note `**forbidden: object` in its signature: it rejects
   unknown keywords rather than ignoring them, so drift here fails loudly —
   `FakeAdapter(responses=["x"], nonsense=1)` gives
   `TypeError: FakeAdapter got unexpected keyword argument(s): nonsense`. Related
   and already on rigor's roadmap as item 1: `FakeAdapter(responses=<callable>,
   seed=...)` is refused outright —
   `ValueError: seed is only meaningful when responses is a sequence` — so the
   only fake that can react to its input is the one that cannot take a seed.

10. **The *spelling* of `SampleTimeout`.** `runner.py` records
    `type(run.error).__name__` into `Completion.error_type`, and both
    `tests/test_runner.py` and `tests/test_judging.py` assert the literal string
    `"SampleTimeout"`. Renaming the class — without changing any signature —
    would break those assertions and silently change the contents of every
    artifact already on disk. This is the only place model-migration-kit depends on a
    rigor identifier as *data* rather than as an import.
11. **What `require_pinned` accepts.** Tightening the pin rule turns a working
    judge config into a `ConfigError`; loosening it lets an alias through. The
    rule is stated in §4.4 so a change is visible as a diff against it.

A rigor release does **not** break model-migration-kit by adding names, adding optional
keywords, changing the Opik integration, or changing anything about `Baseline`,
`assert_score_distribution`, or the `anthropic` and `openai` adapters — none of
which is imported here.

---

## 3. The two behaviours that surprised this consumer

Both are already on rigor's roadmap as items 8 and 9 in `opik-rigor/PROGRESS.md`
(commit `601b40b`, "Record two rigor roadmap items found by its first external
consumer" — verified with `git show --stat 601b40b`). They are re-verified here
rather than restated, because a roadmap entry is a note about intent and this file
is a record of behaviour.

### 3.1 `sample()` files a classification error where a call failure goes — and the default classifier fails on `str`

Reproduction, run in this venv:

```python
from opik_rigor import FakeAdapter, sample

a = FakeAdapter(responses=["Paris"], cycle=True, model_id="fake-scripted-v1")
r = sample(lambda: a.complete("Capital of France?"), 3)     # no outcome=
```

```
n            = 3
successes    = 0
failures     = 0
pass_rate    = 0.0
values       = ()
outcomes     = ()

  Run(index=0, value='Paris', outcome=None, error=TypeError)
  Run(index=1, value='Paris', outcome=None, error=TypeError)
  Run(index=2, value='Paris', outcome=None, error=TypeError)

summary(): {'n': 3, 'runs': 3, 'successes': 0, 'failures': 0, 'exceptions': 3,
            'pass_rate': 0.0, 'errors_as_failures': True, 'concurrency': 1, ...}
```

The `TypeError` is rigor's own, raised by `default_outcome`:

```
cannot decide pass/fail from str; return a bool or an object with a boolean
.passed attribute, or pass an explicit outcome=... callable to sample()
```

The refusal is right — rigor cannot know whether `"Paris"` is a pass. What is
awkward is *where the refusal lands*. `Run.error` is the same field an exception
from `fn` lands in, so an adapter that answered every prompt correctly is
indistinguishable from a provider that was down. That is how it was found: per
`opik-rigor/PROGRESS.md` item 8, model-migration-kit's first end-to-end run reported six
completions *and* six provider failures for the same six draws. That run predates
this file; what is reproduced above is the same behaviour at n=3, run today.

**Sharper than the roadmap entry records.** Item 8 says the runs "each carry
`value="Paris"` *and* an error", which is true of the `Run` objects — but the
convenience accessors drop them entirely, because `.completed` filters on
`run.raised`:

```python
@property
def completed(self) -> tuple[Run, ...]:
    return tuple(run for run in self.runs if not run.raised)

@property
def values(self) -> tuple[Any, ...]:
    return tuple(run.value for run in self.completed)
```

So `r.values == ()` above: a caller who reads `.values` — the obvious accessor
for "give me the text back" — sees *nothing at all*, not text-with-an-error. And
`pass_rate` is `0.0` with `failures == 0`, a combination that reads as "the
system never responded".

**What model-migration-kit does about it.** One explicit `outcome=`, in
`runner.py:_answered`, with the reasoning in the docstring so the next person
does not delete it as noise:

```python
result = sample(
    lambda prompt=prompt: adapter.complete(prompt),
    remaining,
    concurrency=concurrency,
    timeout=timeout,
    outcome=_answered,      # <- load-bearing; see the docstring
    evidence=evidence,
    label=f"{model_id}:{item.id}",
)
```

With it, the same three calls:

```
successes = 3  failures = 0  exceptions = ()
```

At this layer `_answered` means only "the provider returned a string". Whether the
answer is any good is the judge's question, two stages later.

**`outcome=None` does not mean "do not classify"** — it is the default, and
selects `default_outcome`. Verified from the signature:
`outcome: 'Callable[[Any], bool] | None' = None`. That is one of the two fixes
rigor's roadmap proposes; today it is not available.

### 3.2 The `Adapter` seam exposes no token usage, so `tokens_in`/`tokens_out` are always `None`

The protocol is two members, and neither is usage:

```
$ .\.venv\Scripts\python.exe -c "import typing; from opik_rigor import Adapter; print(...)"
=== Adapter protocol ===
is Protocol: True
runtime_checkable: True
members: ['complete', 'model_id']
  complete(self, prompt: 'str') -> 'str'
  model_id: <property object at 0x...>
```

Nor does any concrete adapter carry it. `max_tokens` is a *request* cap, not a
report:

```
FakeAdapter:         ['call_count', 'complete', 'model_id']
   complete(self, prompt: 'str') -> 'str'
AnthropicAdapter:    ['complete', 'max_tokens', 'model_id', 'temperature', 'timeout']
   complete(self, prompt: 'str') -> 'str'
OpenAICompatAdapter: ['base_url', 'complete', 'max_tokens', 'model_id', 'temperature', 'timeout']
   complete(self, prompt: 'str') -> 'str'
```

A sweep of every public name for anything usage-shaped finds only those two caps:

```
=== usage/token-ish names anywhere on the public surface ===
['AnthropicAdapter.max_tokens', 'OpenAICompatAdapter.max_tokens']
```

`complete(prompt) -> str` is the entire contract, and a `str` cannot carry a token
count. `contracts.Completion` therefore declares `tokens_in: int | None` and
`tokens_out: int | None` and leaves both `None` for every adapter rigor ships;
`runner.py` writes them into the evidence line as `None`.

The fields are kept rather than deleted because the contract is frozen and because
the shape is right — the day rigor grows a `complete_with_usage` or a `last_usage`
property, or a consumer supplies its own adapter that has one, the columns are
already there. Getting them filled *today* would mean reaching past the seam into
a provider SDK, which invariant 1 forbids. So there is no cost gate in v0.1, and
the report cannot say what a verdict cost.

---

## 4. Other verified facts a consumer needs

### 4.1 `wilson_interval(0, 0)` raises — "no data" is a rendering state, not a number

```
wilson_interval(0, 0)     -> ValueError: n must be >= 1, got 0; a rate over zero runs is not a rate
wilson_lower_bound(0, 0)  -> ValueError: n must be >= 1, got 0; a rate over zero runs is not a rate
wilson_interval(0, 5)     -> (0.0, 0.43448246478317476)
wilson_lower_bound(0, 5)  -> 0.0
wilson_interval(18, 20)   -> (0.6989663547715128, 0.9721335187862319)
wilson_lower_bound(18, 20)-> 0.7383369536731331
wilson_interval(5, 3)     -> ValueError: successes (5) cannot exceed n (3)
```

Consequence for `report.py` (Session 3): a judge with zero completions on one side
must be rendered as an em-dash, never as `[0.0, 1.0]`. Calling the interval to
find out whether there is data will raise.

Note also that `wilson_interval` is **two-sided** and `wilson_lower_bound` is
**one-sided**; the gate uses the one-sided bound and the report prints the
two-sided interval, so the printed lower edge is *lower* than the number the gate
tested (`0.8350` vs `0.8597` in the example below). That is correct and it will
look like a bug to a reader unless the report says which is which.

### 4.2 `assert_no_regression` rejects `bool` and `None` in a score array

```
bools        -> ValueError: current[0] must be a number, got bool True
None present -> ValueError: current[1] must be a number, got NoneType None
float(bool)  -> {'gate': 'no_regression', ..., 'p_value': 0.9999946491728054, ...}
empty current-> ValueError: current has no scores; there is nothing to compare against baseline
```

Both refusals are load-bearing for this project:

- **`bool` rejected** means pass/fail outcomes must be passed as `float(v.passed)`.
  `bool` is a subclass of `int`, so a permissive implementation would have
  accepted them silently and the caller would never learn which quantity was
  tested.
- **`None` rejected** is why build-plan §6 imputes a failed completion at
  `SCORE_MIN` rather than passing `None` through. One failed completion anywhere
  would otherwise abort an entire comparison with a `ValueError` from inside a
  statistics call.

The key set is identical on the success dict and on `RegressionError.stats`
(16 keys, including `p_value`), so `comparison.py` can read `p_value` off either
path without branching. `assert_pass_rate` is **not** like this — see next.

### 4.3 `assert_pass_rate` carries `underpowered` / `runs_needed` only when it fails

Failure path, 38/40 against a 0.90 floor:

```
PassRateError.stats = {'gate': 'pass_rate', 'label': None, 'passed': False, 'n': 40,
 'successes': 38, 'failures': 2, 'pass_rate': 0.95, 'lower_bound': 0.8596681784340271,
 'interval_lower': 0.8349612263085903, 'interval_upper': 0.9861793326138516,
 'min_rate': 0.9, 'confidence': 0.95, 'method': 'wilson-one-sided',
 'underpowered': True, 'runs_needed': 113}

pass rate gate failed: 38/40 passed (observed 0.9500); one-sided 95% Wilson lower bound
0.8597 < min_rate 0.9000. Two-sided 95% interval [0.8350, 0.9862]. The observed rate
0.9500 clears min_rate 0.9000 but the lower bound does not: this is an underpowered
sample, not a demonstrated failure. 40 runs cannot distinguish a system at 95.0% from
one at 86.0%. At this observed rate roughly 113 runs would clear the bar.
```

Success path, 200/200:

```
{'gate': 'pass_rate', 'label': None, 'passed': True, 'n': 200, 'successes': 200,
 'failures': 0, 'pass_rate': 1.0, 'lower_bound': 0.9866528393452243,
 'interval_lower': 0.9811546736227335, 'interval_upper': 1.0, 'min_rate': 0.9,
 'confidence': 0.95, 'method': 'wilson-one-sided'}
```

`underpowered` and `runs_needed` are absent from the success dict. This is rigor's
own roadmap item 3 ("the report/exception split forces two code paths"), and
`comparison.py` pays it: the floor result is obtained through `try/except
PassRateError`, and any code reading `stats["underpowered"]` must use `.get`.
Build-plan §6 makes this asymmetry structural — missing the floor is NO-GO only
when rigor did **not** call the sample underpowered — so the two paths are not
interchangeable and cannot be collapsed.

The failure message is also the best prose in this dependency, and the report
should quote it rather than paraphrase it.

### 4.4 What `PinnedJudge` requires of a model id — refused at construction, not at analysis time

```
is_pinned('claude-sonnet-4-5-20250929') -> True
is_pinned('gpt-4o-2024-08-06')          -> True
is_pinned('fake-scripted-v1')           -> True
is_pinned('claude-3-5-sonnet-latest')   -> False
is_pinned('gpt-4o')                     -> False
is_pinned('my-model-v1-stable')         -> False
```

`my-model-v1-stable` is the instructive one: it *does* end in a version marker,
and it is still refused, because an alias token anywhere in the string
disqualifies it.

```
require_pinned('gpt-4o') -> ModelPinError: judge refuses unpinned model id 'gpt-4o'.
It must end in a concrete version marker (a date such as '-20250514' or '-2024-08-06',
or an explicit version such as '-v1' or '-2.1.0') and must not contain an alias token
(latest, newest, current, stable, default). An alias re-points over time, which
silently invalidates every score recorded against it.
```

And the check fires in the constructor:

```
PinnedJudge(FakeAdapter(model_id="gpt-4o", ...), rubric, evidence)
  -> ModelPinError
```

So a bad judge config fails at `JudgeConfig.build(...)`, before a single API call
is spent. `FakeAdapter`'s default `fake-scripted-v1` passes, which is what makes
the keyless demo path possible at all.

### 4.5 Rubric hashing: CRLF is normalised, a bare CR is not, and a trailing newline changes the hash

`hash_rubric_text` normalises only the two-byte sequence `\r\n`:

```python
def hash_rubric_text(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
```

```
hash_rubric_text(LF)          = ef35f7b567d955394f93b99c31112e66a6712ece22d1afaafb87e302892cf609
hash_rubric_text(CRLF)        = ef35f7b567d955394f93b99c31112e66a6712ece22d1afaafb87e302892cf609
LF == CRLF                    : True
hash_rubric_text(bare CR)     = b7c7197a17aa74b73d29d7064f95be08296b1376795b3435de894024e9b20007
LF == bare CR                 : False
no trailing newline           = d5727533b46f6e5714575b0b5d9e35e7b58f3a74b38c938ac631c5a6a9ffbddd
trailing newline changes hash : True
raw sha256(LF bytes)          = ef35f7b567d955394f93b99c31112e66a6712ece22d1afaafb87e302892cf609
equals hash_rubric_text(LF)   : True
hash_rubric_file(lf.md)   == hash_rubric_file(crlf.md) : True
hash_rubric_file(missing) -> FileNotFoundError   (not a rigor-specific exception)
```

Three things follow:

- **A Windows checkout and a Linux CI runner agree** on a rubric's identity, which
  is why `.gitattributes` forcing LF is belt-and-braces rather than the mechanism.
  Both projects hash the same way; model-migration-kit's `contracts.py` documents the
  same convention for golden sets, so the two hashes are computed compatibly.
- **A bare-CR file (classic Mac line endings) does not normalise.** Vanishingly
  rare, but it is a silent `RubricDriftError` if it ever happens.
- **Adding or removing the file's final newline changes the rubric hash**, and
  therefore trips drift detection and changes `judges_hash`. Editors that
  "helpfully" add one on save will invalidate a baseline. This is content-hashing
  working as designed — it is still the most likely accidental cause of a drift
  error on a rubric nobody meaningfully edited.

For an LF file, `hash_rubric_text` is exactly `sha256` of the bytes, so a rubric
hash can be reproduced from the command line with `sha256sum` on a normalised
checkout.

### 4.6 Rubric drift is scoped to `(judge name, evidence log)`

```
RubricDriftError: rubric drift for judge 'helpfulness': evidence log last recorded
ba90f6aae204cdf1e218502b243901d45cddb749a796729be5c587d5be8b8105, rubric file now
hashes to 129990b35a16bcae...

different judge name, changed rubric -> constructed fine: 129990b35a16bcae...
```

Constructing the same changed rubric under a different `name=` succeeds. rigor
filters `judge.init` records by name alone, which is exactly why
`docs/session-2-contract.md` §1 rejects two judges sharing a name as a
`ConfigError`: duplicates would make the drift lookup, the judges hash, and the
resume key all wrong at once.

### 4.7 The judge's expected response format, and what counts as unparseable

rigor appends `OUTPUT_FORMAT_INSTRUCTION` to the prompt itself
(`PROMPT_TEMPLATE` ends in `{output_format}`), so a rubric does **not** need to
carry it — model-migration-kit's `data/demo_rubric.md` does not, verified:

```
ends with instruction: False
contains instruction : False
```

The instruction the judge model receives:

```
Answer with a single JSON object and nothing else, in exactly this form:

{"pass": true, "score": 4, "reason": "one sentence naming the deciding criterion"}

- "pass" is required and must be the JSON boolean true or false, never a string.
- "score" must be a number from 1 to 5, or null if the
  rubric gives you no basis to score. Do not invent a number and do not answer
  outside that range.
- "reason" is one sentence.
Do not wrap the object in commentary. If you are unsure, say so in the reason
rather than answering in prose -- an unparseable answer is discarded, which is
safer than a guess.
```

Parser behaviour, one scripted response per line, verified end to end against a
real `PinnedJudge`:

| response | result |
|---|---|
| `{"pass": true, "score": 4, "reason": "ok"}` | `passed=True score=4.0 reason='ok'` |
| `{"pass": false, "score": null, "reason": "no basis"}` | `passed=False score=None reason='no basis'` |
| the same object wrapped in a `json` markdown fence | `passed=True score=5.0` — **fenced JSON is accepted** |
| `{"passed": true, "score": 3}` | `passed=True score=3.0` — `PASS_KEYS = ('pass', 'passed')` |
| `I think it is fine, honestly.` | `JudgeOutputError: response contained no JSON object` |
| `{"pass": "true", "score": 3}` | `JudgeOutputError: 'pass' must be a JSON boolean, got str 'true'` |
| `{"pass": true, "score": 9}` | `JudgeOutputError: 'score' 9 is outside the rubric's range 1-5; it is not clamped` |

`SCORE_MIN == 1.0`, `SCORE_MAX == 5.0`, and both are hardcoded in 0.1.0 — a
configurable range is on rigor's roadmap, not in this release. `judging.py`
imputes failed completions at `SCORE_MIN`.

Two consequences for `judging.py`'s tolerance rule:

- **`score: null` is normal output, not a parse failure.** It produces a valid
  `Verdict` with `score=None` and must not count toward the parse-failure budget;
  the regression test then runs on `float(v.passed)` for that judge.
- **An out-of-range score is a parse failure and is not clamped**, deliberately:
  a score the rubric cannot express means the judge misread the rubric.

Evidence written by the judge, over 7 evaluate calls:

```
Counter({'judge.verdict': 4, 'judge.parse_failure': 3, 'judge.init': 1})
first record payload keys: ['judge', 'model_id', 'rubric_hash', 'rubric_path']
```

One `judge.init` per constructed judge, one record per call on either path. The
log has **no dedupe**, so anything reading verdicts back must dedupe on
`(judge, item_id, sample_index)` — which is why that triple is `judging.py`'s
resume key.

### 4.8 What `sample()` writes to the evidence log

One record per `sample()` call, not one per run:

```
event types from sample + assert_pass_rate: Counter({'sample.completed': 1, 'assertion.evaluated': 1})
  sample.completed    ['concurrency','errors_as_failures','exceptions','failures','label',
                       'n','pass_rate','runs','successes','wall_clock']
  assertion.evaluated ['confidence','failures','gate','interval_lower','interval_upper',
                       'label','lower_bound','method','min_rate','n','pass_rate','passed']
```

`sample.completed` carries no per-item output. That is why `runner.py` writes its
own `migkit.completion` evidence line per completion — the acceptance contract
requires one per completion with the model string present, and rigor's record
does not provide it. Not a defect: rigor's record is a summary by design.

### 4.9 Timeouts surface as `SampleTimeout`, which is **not** a `TimeoutError`

```
timeout run summary: {'n': 2, 'runs': 2, 'successes': 0, 'failures': 0, 'exceptions': 2,
                      'pass_rate': 0.0, 'errors_as_failures': True, ...}
exception types: ['SampleTimeout', 'SampleTimeout']
SampleTimeout is builtin TimeoutError? False
SampleTimeout.__mro__: (SampleTimeout, Exception, BaseException, object)
```

`SampleTimeout` inherits from `Exception` directly. A caller writing
`except TimeoutError` will not catch it. This is deliberate on rigor's side —
`opik-rigor/PROGRESS.md` records a bug where catching
`concurrent.futures.TimeoutError` (which *is* `builtins.TimeoutError` since 3.11)
rewrote a provider's socket timeout as rigor's budget expiring. The two really are
facts about different systems. It is still a trap for a consumer who assumes the
builtin hierarchy, and `runner.py` correctly does not assume it: it records
`type(run.error).__name__` rather than branching on the class.

Note also that with the default `errors_as_failures=True`, timeouts stay in the
denominator (`n == runs == 2`) but are counted as `exceptions`, not `failures`:
`failures` means "produced an output that did not pass", and that meaning does not
change with the flag.

---

## 5. Friction found here that is **not** on rigor's roadmap

Recorded rather than worked around, per invariant 1. None of these blocks
Session 2; all are cheap for rigor to fix and expensive for a consumer to
discover.

**A. `opik-rigor` ships no `py.typed` marker.**

```
$ .\.venv\Scripts\python.exe -c "from pathlib import Path; import opik_rigor; print((Path(opik_rigor.__file__).parent/'py.typed').exists())"
False
```

The package is thoroughly annotated — every signature in §1 carries types — but
PEP 561 says a type checker must not use inline annotations from an installed
package that ships no `py.typed`. On that reading a consumer gets no type
information at all from a fully typed dependency. This is a different and
arguably larger gap than rigor's own roadmap item 4 (untyped report dicts): item
4 is about `dict[str, Any]` *return values*, whereas this suppresses the
annotations that already exist. An empty `py.typed` inside `src/opik_rigor/`
fixes it.

Latent rather than biting, and honestly labelled as such: model-migration-kit runs
`ruff` but no type checker (`dev = ["pytest", "pytest-cov", "ruff"]`), so **no
checker was run to observe this** — the claim above is the absence of the marker
file, which is what the command shows, plus what PEP 561 specifies. The day
either project adds mypy or pyright, it becomes real.

**B. `SampleResult.exceptions` returns `Run` objects, not exceptions.**

```python
@property
def exceptions(self) -> tuple[Run, ...]:
    return tuple(run for run in self.runs if run.raised)
```

Verified by printing `type(e).__name__` over the tuple, which gave `Run` for every
element. The name reads as "the exceptions that were raised", and the annotation
is honest, but a caller writing
`[str(e) for e in result.exceptions]` gets run reprs rather than error messages
and will not notice until the log is read. `errored_runs` would say what it is;
alternatively an `exception` alias returning the actual `BaseException` objects.

**C. `hash_rubric_text(data: bytes)` takes bytes despite the name.**
Passing a `str` fails inside the function rather than at the boundary:

```
TypeError: replace() argument 1 must be str, not bytes
```

— which points at rigor's own `b"\r\n"` literal rather than at the argument the
caller got wrong, so the message reads as the inverse of the actual mistake. The
docstring does explain the design ("normalising the *bytes we hash* — not the
file"), and the choice is right; the name is what misleads. `hash_rubric_bytes`,
or a guard raising `TypeError("expected bytes, got str; encode() it first")`,
would cost nothing. model-migration-kit only uses `hash_rubric_file`, so this cost one
scratch-script iteration and nothing more.

**D. `assert_no_regression(SampleResult, SampleResult)` on text completions
reports "no scores" rather than a type error.**

```
assert_no_regression(r, r) -> ValueError: current has no scores; there is nothing
                              to compare against baseline
```

`SampleResult.scores()` harvests `getattr(run.value, "score", None)`, so a sample
of *strings* yields an empty tuple and the caller is told they have no data when
what they actually have is data of the wrong shape. It is the same class of
confusion as §3.1 — a structural problem reported as an empty result. Distinguishing
"n runs, none of which carried a score" from "no runs" in the message would name
the actual mistake.

**E. The published wheel contains no example rubric.**

```
files shipped in opik_rigor package:
   __init__.py  adapters/…  baseline.py  distribution.py  errors.py  evidence.py
   integrations/…  judge.py  pinning.py  sampling.py
```

`rubrics/example-rubric.md` exists in rigor's repository and its README points at
it (`README.md:117`, "Save a rubric as `rubric.md` (the one in
`rubrics/example-rubric.md` ...")  — but it is not packaged, so
`pip install opik-rigor` gives a consumer a
`PinnedJudge` and nothing to point it at. model-migration-kit wrote its own
(`src/model_migration_kit/data/demo_rubric.md`), which is arguably correct anyway — but
the first thing a new user of the judge needs is a rubric that parses, and the
install does not include one. Related and worth stating because it is *not* a
problem: because `PROMPT_TEMPLATE` already appends `OUTPUT_FORMAT_INSTRUCTION`, a
rubric copied from rigor's repository example would carry the format block twice
in the rendered prompt — that example ends with the instruction, verified against
the file in the sibling checkout:

```
example rubric ends with OUTPUT_FORMAT_INSTRUCTION: True
```

**F. Three names model-migration-kit needs are not in `__all__`** — `SCORE_MIN`,
`SCORE_MAX` and `hash_rubric_file`. Detailed in §1; repeated here because it is
the one item on this list that model-migration-kit is *relying* on rather than merely
tripping over.

---

## 6. Version policy

`pyproject.toml` declares:

```toml
dependencies = [
  # The whole point: every statistical primitive is imported, none reimplemented.
  "opik-rigor>=0.1.0,<0.2",
  ...
]
```

- **Lower bound `0.1.0`** because 0.1.0 is what the surface in §1 was verified
  against. `opik-rigor/PROGRESS.md` records it as the first release
  ("v0.1.0 published to PyPI 2026-08-13"); that is cited, not independently
  checked — see §7.3.
- **Upper bound `<0.2`** because 0.2 is scheduled to change exactly what
  model-migration-kit reads. rigor's own roadmap items are a non-raising
  `check_pass_rate(...) -> report` beside the asserting one (item 3) and frozen
  report dataclasses replacing `dict[str, Any]` (item 4) — and `comparison.py`
  gets `underpowered` and `runs_needed` by catching `PassRateError` and reading
  `.stats`, which is precisely the shape those items propose to move. Items 8 and
  9 above would change `sample`'s classification behaviour and the `Adapter`
  protocol, both of which `runner.py` sits on.

  The failure mode of guessing wrong is what makes the bound tight rather than
  polite. `assert_pass_rate` returns a plain `dict`, so a renamed key is a
  `KeyError` at best and a `.get(...)` default at worst — and per build-plan §6 a
  missing `underpowered` flag silently converts REVIEW into NO-GO. A wrong verdict
  that looks like a right one is the one failure this project exists to refuse,
  so the bound stays at the minor version until the surface is re-verified.

Because rigor is `>=3.10` and depends on `scipy>=1.10`, model-migration-kit inherits
scipy and numpy transitively. `comparison.py` does not import either; the
statistics come through rigor's API, which is the point.

---

## 7. What is **not** verified

Stated plainly, because an unverified claim sitting next to verified ones borrows
their credibility.

1. **Nothing here was checked on Python 3.10–3.13.** Every command above ran on
   3.14.4 on Windows. rigor's metadata says `Requires-Python: >=3.10` and CI runs
   the matrix, but the *behaviours* in §3 and §4 were observed on one interpreter.
   The sibling project has a live example of why this matters:
   `opik-rigor/COMPATIBILITY.md` §3 records `inspect.signature` failing on an Opik
   object under PEP 649 lazy annotation evaluation (Python 3.13+, default in
   3.14) where it would not on an earlier interpreter. Anything in this file that
   depends on annotation evaluation could in principle differ across the matrix.
2. **No claim is made about opik-rigor's rendered documentation.** This file
   describes the installed package only. Where it quotes docstrings, they are
   docstrings read out of the installed module, not a website.
3. **What else `>=0.1.0,<0.2` could resolve to was not checked.** No network call
   was made while writing this file. The installed version is 0.1.0; whether PyPI
   now carries a 0.1.1 that would satisfy the bound is unknown here.
4. **The `.venv` here is not clean-room.** It is the working environment, created
   with `pip install -e ".[dev]"`. The opik-rigor wheel inside it has no
   `direct_url.json`, which establishes it came from an index rather than a path,
   but the venv as a whole has not been rebuilt from scratch for this file.
5. **Concurrency was not exercised.** `sample(..., concurrency=N)` for `N > 1` is
   passed through by `runner.py` and was verified only at the default `1`.
6. **`assert_score_distribution` and `Baseline` were not verified**, because
   model-migration-kit does not import them.
7. **No live provider adapter was called.** `AnthropicAdapter` and
   `OpenAICompatAdapter` were introspected, never invoked; there are no
   credentials in this environment and CI blanks them.
8. **Judge behaviour was verified against `FakeAdapter`,** so §4.7 describes
   rigor's *parser*, not how any real model actually answers. The parse-failure
   tolerance in `judging.py` exists precisely because that number is unknown.
9. **rigor's pytest plugin was confirmed to load and register its marker, and no
   more.** `rigor_repeat`, the `rigor_evidence` / `rigor_judge` fixtures and the
   `rigor_evidence_path` ini option were not exercised; model-migration-kit does not
   use them. The test suite itself was not run while writing this file — other
   work was in flight in `tests/` — so nothing here rests on a green suite.
10. **No type checker was run** (see §5.A), and no `pip install` was performed:
    every command above was read-only against the existing `.venv`.

---

## 8. What to do when this drifts

1. Re-run the introspection against the new version and update the table at the
   top and §1 in the same commit as the code fix, so the record never lags the
   code.
2. Check §2 first: the `PassRateError.stats` keys are the highest-consequence
   item, because drift there changes a verdict without raising.
3. If rigor gains what §3 asks for — `outcome=None` meaning "do not classify", or
   usage on the adapter seam — delete the workaround *and* the paragraph that
   explains it, rather than leaving prose describing a problem that no longer
   exists.
4. New friction goes in this file **and** in `opik-rigor/PROGRESS.md`. The
   dependency direction stays clean: record it, work around it at the public API
   surface, do not monkey-patch and do not reach into internals.
