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
| `opik-rigor` | **0.2.0** (installed from PyPI — see provenance below) |
| `jinja2` | **3.1.6** |
| `rich` | **15.0.0** |
| Declared bound | `opik-rigor>=0.2,<0.3` (`pyproject.toml`) |
| Python used to verify | **3.14.4** (Windows, `.venv`) |
| Python in CI | **3.10, 3.11, 3.12, 3.13** × `ubuntu-latest`, `windows-latest` (`.github/workflows/ci.yml`) |
| rigor's `Requires-Python` | `>=3.10` |
| rigor's runtime deps | `numpy>=1.21`, `scipy>=1.10` (here 2.5.2 / 1.18.0). numpy is now declared directly rather than inherited through scipy |
| Verified on | **2026-08-14** against 0.2.0; earlier the same day against 0.1.1; first written 2026-08-13 against 0.1.0 |
| Method | Introspected the installed wheel with `inspect`, `typing.get_protocol_members`, live calls in `.venv`, and one `pytest` run for the marker path |

> ### ⚠ 0.2.0 refuses a one-sided `confidence` at or below 0.5 — the one behaviour change that can break a caller
>
> Verified against **the installed 0.2.0 wheel** by running it, not by reading
> rigor's tree. `wilson_lower_bound` and `assert_pass_rate` now raise `ValueError`
> for `confidence <= 0.5`, where 0.1.1 accepted the value and answered:
>
> ```
> wilson_lower_bound(18, 20, 0.4 ) -> ValueError: confidence must be greater than 0.5
>                                     for a one-sided bound, got 0.4. ...
> wilson_lower_bound(18, 20, 0.5 ) -> ValueError  (0.5 exactly is refused too)
> wilson_lower_bound(18, 20, 0.51) -> 0.8983057373544914
> assert_pass_rate((18, 20), 0.5, confidence=0.5) -> ValueError  (same message)
> assert_pass_rate((18, 20), 0.5, confidence=0.51) -> ok, lower_bound=0.8983057373544914
> ```
>
> `wilson_interval` is **not** affected and still takes the whole open interval —
> `wilson_interval(18, 20, 0.1)` returns `(0.8912522331050441, 0.9081166342348816)`
> — because its `z` is `ppf((1 + c) / 2)`, never negative. All three functions
> still refuse `0.0`, `1.0` and anything outside `(0, 1)`, under a *different*
> message (`confidence must be strictly between 0 and 1`) but the same
> `ValueError`, so the out-of-range refusal and the new one-sided refusal are
> distinguishable only by message text. See §4.10 for the full range, run value by
> value.
>
> **A third surface carries `confidence` and is not in `__all__`:** rigor's pytest
> marker. `@pytest.mark.rigor_repeat(n, min_rate, confidence=...)` forwards the
> value to `assert_pass_rate` **unvalidated and after the runs are spent**, so a
> suite carrying a low confidence on a marker pays for `n` executions and then
> errors. Run here against 0.2.0, `n=8`:
>
> ```
> $ .\.venv\Scripts\python.exe -m pytest -s -q --tb=line     (scratch dir, not this repo)
> [rigor_repeat] test_marker_confidence.py::test_low_confidence_marker runs=8 successes=8
>                failures=0 exceptions=0 pass_rate=1.0000 n=8 min_rate=0.5000 confidence=0.4000
> F
> RUNS SPENT: low=8 ok=8
> E   ValueError: confidence must be greater than 0.5 for a one-sided bound, got 0.4. ...
> ...\opik_rigor\distribution.py:213: ValueError
> 1 failed, 2 passed
> ```
>
> The `runs=8` line prints *before* the `ValueError`, and the test body's own
> counter confirms it: eight executions, then the refusal. model-migration-kit does
> not use the marker (§7.9), so this costs it nothing — it is recorded because the
> marker autoloads into every suite that installs rigor (§1) and the failure lands
> at the end of a run rather than at collection.
>
> **Both 0.1.1 defects are fixed in 0.2.0**, verified by running the installed
> wheel rather than by reading a changelog:
>
> ```
> is_pinned('claude-opus-5'             ) = True    (was False)
> is_pinned('claude-sonnet-5'           ) = True    (was False)
> is_pinned('claude-opus-4-8'           ) = True    (was False)
> is_pinned('claude-opus-4-6'           ) = True    (was False)
> is_pinned('claude-haiku-4-5'          ) = True    (was False)
> is_pinned('claude-haiku-4-5-20251001' ) = True    (unchanged)
> is_pinned('claude-3-7-sonnet-20250219') = True    (unchanged; retired, but pinned)
> is_pinned('gpt-4.1'                   ) = False   (was True)
>
> AnthropicAdapter(self, model_id: 'str', *, max_tokens: 'int' = 1024,
>                  temperature: 'float | None' = None, timeout: 'float' = 60.0,
>                  **forbidden: 'ForbiddenKwarg') -> 'None'
> AnthropicAdapter('some-model-v1').temperature                 -> None
> AnthropicAdapter('some-model-v1', temperature=None).temperature -> None
>
> inspect.getsource(AnthropicAdapter.complete) now contains:
>     if self._temperature is not None:
>         request["temperature"] = self._temperature
> ```
>
> So the real-model path is reachable in 0.2.0: `claude-opus-5` clears the pin
> rule, and `temperature` defaults to `None` on the Anthropic adapter instead of
> `0.0`. Note the one id that moved the *other* way — `gpt-4.1` is now refused,
> which is a tightening a user could meet as a new `ConfigError` on a judge config
> that loaded under 0.1.1.
>
> Neither fix touches the keyless path: `FakeAdapter`'s default `fake-scripted-v1`
> was pinned under both rules, and no adapter here is constructed with a
> `temperature`. So `migkit demo`, `--adapter fake` and the test suite see nothing
> of either change — which is also why neither defect was caught by this
> repository's own runs while it had them (§7.7, §7.9).

```
$ .\.venv\Scripts\python.exe -c "import sys, importlib.metadata as md; print(sys.version); [print(d, md.version(d)) for d in ('opik-rigor','jinja2','rich','pytest','scipy','numpy')]"
3.14.4 (tags/v3.14.4:23116f9, Apr  7 2026, 14:10:54) [MSC v.1944 64 bit (AMD64)]
opik-rigor 0.2.0
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
$ cat .venv/Lib/site-packages/opik_rigor-0.2.0.dist-info/INSTALLER
pip
$ ls .venv/Lib/site-packages/opik_rigor-0.2.0.dist-info/
INSTALLER  METADATA  RECORD  REQUESTED  WHEEL  entry_points.txt  licenses
$ cat .venv/Lib/site-packages/opik_rigor-0.2.0.dist-info/direct_url.json
cat: .venv/Lib/site-packages/opik_rigor-0.2.0.dist-info/direct_url.json: No such file or directory
(no direct_url.json -> installed from an index, not a path)
```

`REQUESTED` is new here and means the same thing in the other direction: pip
writes it for a distribution the user asked for by name, rather than one pulled in
as somebody else's dependency. Together with the absent `direct_url.json` that is
two independent signals that this is the published artifact, fetched from an index
by name.

**The rule this file exists to honour.** Introspection tells you what an API *is*.
It does not tell you what its documentation *says*. Those are separate claims
needing separate evidence — the sibling project learned this by asserting a fault
in someone else's docs on the strength of one HTML-to-markdown converter, and had
to retract it (see `opik-rigor/COMPATIBILITY.md`, "A correction to this file, not
to the docs"). So: every statement below is about **behaviour of the installed
0.2.0 wheel**, backed by a command. Where this file talks about rigor's *roadmap*
or its *intentions*, it cites `opik-rigor/PROGRESS.md` and says so.

**A second rule, learned on 2026-08-14:** a statement about rigor's *source tree*
is not a statement about what users get either. The morning's revision of this
file recorded a rewritten pin rule, an adapter that omits `temperature`, a
lazily-imported scipy and several tightened gates as sitting **unreleased** on
rigor's `main`, and refused to describe them as behaviour. 0.2.0 published them
that afternoon and every one of them is confirmed above and below — by running
the wheel, not by promoting the earlier note. The rule paid for itself in both
directions: nothing had to be retracted when the release landed, and nothing was
claimed in the window where the tree said one thing and the index another.

---

## 1. The API model-migration-kit actually calls

Name by name. Every `.py` file in this repository that names `opik_rigor` is on
this list; anything not on it is not relied on and a rigor release may move it
freely. The enumeration is mechanical and re-runnable — see the command below the
table — so the completeness claim is checkable rather than asserted.

| Module | Imported from `opik_rigor` |
|---|---|
| `src/model_migration_kit/cli.py` | `Adapter`, `AdapterError`, `AnthropicAdapter`, `EvidenceLog`, `FakeAdapter`, `OpenAICompatAdapter`, `RigorError`, `SampleTimeout`, `__version__` |
| `src/model_migration_kit/comparison.py` | `EvidenceLog`, `PassRateError`, `RegressionError`, `SCORE_MAX`, `SCORE_MIN`, `assert_no_regression`, `assert_pass_rate`, `wilson_interval` |
| `src/model_migration_kit/demo.py` | `AdapterError`, `EvidenceLog`, `FakeAdapter` |
| `src/model_migration_kit/judging.py` | `Adapter`, `EvidenceLog`, `JudgeOutputError`, `ModelPinError`, `PinnedJudge`, `SCORE_MIN`, `hash_rubric_file`, `require_pinned` |
| `src/model_migration_kit/report.py` | `EvidenceError`, `EvidenceRecord` -- **plus** the module itself (`import opik_rigor`), read for `__version__` |
| `src/model_migration_kit/runner.py` | `Adapter`, `EvidenceLog`, `sample` |
| `tests/fixtures/make_fixtures.py` | `EvidenceLog`, `FakeAdapter` |
| `tests/test_cli.py` | `AdapterError`, `EvidenceError`, `EvidenceLog`, `FakeAdapter`, `JudgeOutputError`, `ModelPinError`, `PassRateError`, `RegressionError`, `RigorError`, `RubricDriftError`, `SampleTimeout` |
| `tests/test_comparison.py` | `SCORE_MAX`, `SCORE_MIN` |
| `tests/test_comparison_regressions.py` | `PassRateError`, `RegressionError`, `SCORE_MAX`, `SCORE_MIN`, `assert_no_regression` |
| `tests/test_evidence_scale.py` | `EvidenceError`, `EvidenceLog`, `FakeAdapter` |
| `tests/test_judging.py` | `EvidenceLog`, `FakeAdapter`, `JudgeOutputError`, `ModelPinError`, `PinnedJudge`, `SCORE_MIN`, `hash_rubric_file`, `is_pinned` |
| `tests/test_property_based.py` | `FakeAdapter`, `SCORE_MAX`, `SCORE_MIN` |
| `tests/test_report.py` | `EvidenceLog`, `wilson_interval` -- **plus** the module itself (`import opik_rigor`), read for `__version__` |
| `tests/test_report_scale.py` | `EvidenceLog`, `FakeAdapter` |
| `tests/test_runner.py` | `EvidenceLog`, `FakeAdapter`, `PassRateError`, `assert_pass_rate`, `sample_of` |
| `tests/test_stranger_path.py` | `EvidenceLog`, `anthropic`, `openai_compat` |
| `tests/test_thresholds_confidence_contract.py` | `assert_pass_rate`, `wilson_lower_bound` |

Enumerated by parsing every `.py` file rather than by grepping line starts,
because a `grep "^from opik_rigor"` misses an import indented inside a function —
`tests/test_judging.py:429` is exactly that, and an earlier revision of this table
happened to list its name anyway, which is luck rather than method. The test files
are under active development, so re-run this rather than trusting the table after
a session boundary:

```
$ .\.venv\Scripts\python.exe -c "import ast, pathlib; [print(f'{p.as_posix()}:{n.lineno} from {n.module} import ' + ', '.join(sorted(a.name for a in n.names))) if isinstance(n, ast.ImportFrom) and (n.module or '').split('.')[0]=='opik_rigor' else [print(f'{p.as_posix()}:{n.lineno} import {a.name}') for a in getattr(n,'names',[]) if isinstance(n, ast.Import) and a.name.split('.')[0]=='opik_rigor'] for p in sorted(pathlib.Path('.').rglob('*.py')) if not {'.venv', '.claude'} & set(p.parts) for n in ast.walk(ast.parse(p.read_text(encoding='utf-8')))]"
src/model_migration_kit/cli.py:43 from opik_rigor import Adapter, AdapterError, AnthropicAdapter, EvidenceLog, FakeAdapter, OpenAICompatAdapter, RigorError, SampleTimeout
src/model_migration_kit/cli.py:53 from opik_rigor import __version__
src/model_migration_kit/comparison.py:88 from opik_rigor import EvidenceLog, PassRateError, RegressionError, SCORE_MAX, SCORE_MIN, assert_no_regression, assert_pass_rate, wilson_interval
src/model_migration_kit/demo.py:91 from opik_rigor import AdapterError, EvidenceLog, FakeAdapter
src/model_migration_kit/judging.py:54 from opik_rigor import Adapter, EvidenceLog, JudgeOutputError, ModelPinError, PinnedJudge, SCORE_MIN, hash_rubric_file, require_pinned
src/model_migration_kit/report.py:108 import opik_rigor
src/model_migration_kit/report.py:110 from opik_rigor import EvidenceError, EvidenceRecord
src/model_migration_kit/runner.py:38 from opik_rigor import Adapter, EvidenceLog, sample
tests/fixtures/make_fixtures.py:68 from opik_rigor import EvidenceLog, FakeAdapter
tests/test_cli.py:55 from opik_rigor import AdapterError, EvidenceError, EvidenceLog, FakeAdapter, JudgeOutputError, ModelPinError, PassRateError, RegressionError, RigorError, RubricDriftError, SampleTimeout
tests/test_comparison.py:52 from opik_rigor import SCORE_MAX, SCORE_MIN
tests/test_comparison_regressions.py:66 from opik_rigor import PassRateError, RegressionError, SCORE_MAX, SCORE_MIN, assert_no_regression
tests/test_evidence_scale.py:43 from opik_rigor import EvidenceError, EvidenceLog, FakeAdapter
tests/test_judging.py:46 from opik_rigor import EvidenceLog, FakeAdapter, ModelPinError, PinnedJudge, SCORE_MIN, hash_rubric_file, is_pinned
tests/test_judging.py:436 from opik_rigor import JudgeOutputError
tests/test_property_based.py:58 from opik_rigor import FakeAdapter, SCORE_MAX, SCORE_MIN
tests/test_report.py:65 from opik_rigor import EvidenceLog, wilson_interval
tests/test_report.py:2248 import opik_rigor
tests/test_report_scale.py:72 from opik_rigor import EvidenceLog, FakeAdapter
tests/test_runner.py:29 from opik_rigor import EvidenceLog, FakeAdapter, PassRateError, assert_pass_rate, sample_of
tests/test_stranger_path.py:677 from opik_rigor import EvidenceLog
tests/test_stranger_path.py:702 from opik_rigor.adapters import anthropic
tests/test_stranger_path.py:703 from opik_rigor.adapters import openai_compat
tests/test_thresholds_confidence_contract.py:56 from opik_rigor import assert_pass_rate, wilson_lower_bound
```

The `.claude` exclusion is new and was earned: agent worktrees under
`.claude/worktrees/` are checkouts of this same repository, so without it the
command prints four stale copies of every row above and the output stops being an
inventory of what ships. `scripts/dependency_surface.py` never had the problem —
it walks `src/` and `tests/` by name — which is the argument for the script being
the check and this command being the illustration.

`__version__` is load-bearing rather than decorative: `migkit --version` prints
it, so removing it from the package root would break the CLI's most trivial
invocation at import time (`cli.py:53` is a `from … import`, not a `getattr`).

```
$ .\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from model_migration_kit import cli; cli.main(['--version'])"
migkit 0.1.0.dev0 (opik-rigor 0.2.0)
```

`report.py:913` reads the same attribute off the module to stamp it into the
report, but defensively — `getattr(opik_rigor, "__version__", "unknown")` — so
that path degrades rather than raising. `tests/test_report.py:2251-2252` asserts
the stamped value equals `opik_rigor.__version__` and that it appears in the
rendered HTML, so the two are pinned together.

`require_pinned(model_id, *, context='judge')` is called by `judging.py` at config
load so an unpinned judge model is a `ConfigError` before any judge is
constructed; `context=` is passed and appears in the message
(`ModelPinError: judge 'x' in cfg.toml refuses unpinned model id 'gpt-4o'. ...`).

**What 0.1.0 → 0.1.1 changed for this consumer: nothing broke, five names
appeared.** Re-checked mechanically on 2026-08-14, comparing the installed 0.1.1
against the table below rather than re-reading it:

```
signatures checked: 7, changed: 0
attribute-level dependencies -- Run, Verdict, SampleResult, PinnedJudge,
EvidenceLog: none missing
__all__: 33 names -> 38
added: SCORE_MIN, SCORE_MAX, hash_rubric_file, hash_rubric_text, example_rubric_path
removed: none
SCORE_MIN = 1.0   SCORE_MAX = 5.0     (unchanged)
Adapter protocol members: ['complete', 'model_id']   (unchanged)
pytest11 entry point: rigor = opik_rigor.integrations.pytest_plugin  (unchanged)
```

That is the whole point of keeping this file: rigor promised 0.1.1 would be
additive, and this is the check rather than the promise. The four names this
project had been importing from `opik_rigor.judge` — a violation of its own first
invariant — are among the five added, which is what let that violation be retired.

**The bound moved to `>=0.1.1,<0.2` for that reason.** Note what the old bound did
in the meantime: `>=0.1.0,<0.2` let every *fresh* install resolve 0.1.1 while this
repository's long-lived `.venv` kept 0.1.0, so for a day this document described a
version strangers were no longer getting, and nothing detected that. A long-lived
venv stops being evidence about what users get the moment a floor is permissive.

**What 0.1.1 → 0.2.0 changed for this consumer: the name surface did not move at
all, and one accepted argument value became a `ValueError`.** Re-run the same way
on 2026-08-14 against the installed 0.2.0:

```
__all__: 38 names -> 38     added: none     removed: none
signatures checked: 14, changed: 1
  FakeAdapter  **forbidden: 'object' -> **forbidden: 'ForbiddenKwarg'   (annotation only)
outside the recorded set, three more signatures moved:
  AnthropicAdapter     temperature: 'float' = 0.0 -> 'float | None' = None
  OpenAICompatAdapter  temperature: 'float' = 0.0 -> 'float | None' = 0.0   (default held)
  hash_rubric_text     (data: 'bytes') -> (data: 'bytes | str')
attribute-level dependencies -- Run, Verdict, SampleResult, PinnedJudge,
EvidenceLog, EvidenceRecord: none missing; SampleResult gained .errored_runs
SCORE_MIN = 1.0   SCORE_MAX = 5.0     (unchanged)
Adapter protocol members: ['complete', 'model_id']   (unchanged)
pytest11 entry point: rigor = opik_rigor.integrations.pytest_plugin  (unchanged)
PassRateError.stats on the failure path: 15 keys -> 18   (added, see §4.3)
```

So the thing that breaks a caller is not on this list. `__all__` is identical name
for name across the two releases and every relied-on signature still accepts every
call this project makes — while the release refuses a `confidence` this project's
own config schema used to allow (§4.10). That is a change in the *accepted values*
behind an unchanged signature, which is the failure mode a name-level diff cannot
see, and the reason this section is not the whole check.

The signatures those names have — recorded against 0.1.0, re-verified in 0.1.1,
re-verified in 0.2.0 with the one annotation change marked inline:

```
$ .\.venv\Scripts\python.exe -c "import inspect, opik_rigor; print(opik_rigor.__all__); ..."
__all__ = ['Adapter', 'AdapterError', 'AnthropicAdapter', 'Baseline', 'BaselineError',
 'EvidenceError', 'EvidenceLog', 'EvidenceRecord', 'FakeAdapter', 'JudgeOutputError',
 'ModelPinError', 'OpenAICompatAdapter', 'PassRateError', 'PinnedJudge', 'RegressionError',
 'RigorError', 'RubricDriftError', 'Run', 'SCORE_MAX', 'SCORE_MIN', 'SampleResult',
 'SampleTimeout', 'ScoreDistributionError', 'StatisticalAssertionError', 'Verdict',
 '__version__', 'assert_no_regression', 'assert_pass_rate', 'assert_score_distribution',
 'example_rubric_path', 'hash_rubric_file', 'hash_rubric_text', 'is_pinned',
 'require_pinned', 'sample', 'sample_of', 'wilson_interval', 'wilson_lower_bound']
                                        (38 names, sorted, identical in 0.1.1 and 0.2.0)

Adapter(*args, **kwargs)
EvidenceLog(path: 'str | os.PathLike[str]') -> 'None'
FakeAdapter(*, model_id: 'str' = 'fake-scripted-v1', responses: 'ResponseSource', cycle: 'bool' = False, seed: 'int | None' = None, latency: 'float' = 0.0, fail_with: 'BaseException | type[BaseException] | None' = None, fail_after: 'int | None' = None, **forbidden: 'ForbiddenKwarg') -> 'None'
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
                          all present in 0.2.0; nothing on this list moved
```

`assert_pass_rate` and `assert_no_regression` accept more than a `SampleResult`:

```
PassData  = opik_rigor.sampling.SampleResult | tuple[int, int] | collections.abc.Sequence[bool]
ScoreData = opik_rigor.sampling.SampleResult | collections.abc.Sequence[float]
```

### Some names are imported from a submodule, not from `__all__`

`judging.py` used to do `from opik_rigor.judge import SCORE_MIN, hash_rubric_file`,
and the tests added `SCORE_MAX`. In 0.1.0 none of them was re-exported at package
level:

```
$ .\.venv\Scripts\python.exe -c "..."
  opik_rigor.judge.SCORE_MIN                  in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.SCORE_MAX                  in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.hash_rubric_file           in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.hash_rubric_text           in opik_rigor.__all__: False   top-level attr: False
  opik_rigor.judge.OUTPUT_FORMAT_INSTRUCTION  in opik_rigor.__all__: False   top-level attr: False
```

Every other name on the list above **is** in `__all__`, checked individually
against the installed 0.1.0 — `Adapter`, `AdapterError`, `AnthropicAdapter`,
`EvidenceError`, `EvidenceLog`, `FakeAdapter`, `JudgeOutputError`, `ModelPinError`,
`OpenAICompatAdapter`, `PassRateError`, `PinnedJudge`, `RegressionError`,
`RigorError`, `RubricDriftError`, `SampleTimeout`, `__version__`,
`assert_no_regression`, `assert_pass_rate`, `is_pinned`, `require_pinned`,
`sample`, `sample_of`, `wilson_interval`, and (used by attribute rather than
imported) `Verdict`, `Run`, `SampleResult`, `wilson_lower_bound` — all `True`:

```
$ .\.venv\Scripts\python.exe -c "import opik_rigor; [print(f'{n:22} in __all__: {n in opik_rigor.__all__!s:5} top-level attr: {hasattr(opik_rigor, n)}') for n in ('Adapter','AdapterError','AnthropicAdapter','EvidenceError','EvidenceLog','FakeAdapter','JudgeOutputError','ModelPinError','OpenAICompatAdapter','PassRateError','PinnedJudge','RegressionError','RigorError','RubricDriftError','SampleTimeout','__version__','assert_no_regression','assert_pass_rate','is_pinned','require_pinned','sample','sample_of','wilson_interval')]"
Adapter                in __all__: True  top-level attr: True
AdapterError           in __all__: True  top-level attr: True
AnthropicAdapter       in __all__: True  top-level attr: True
EvidenceError          in __all__: True  top-level attr: True
...                                                             (23 names, all True)
__version__            in __all__: True  top-level attr: True
                    (re-run against 0.2.0: all 23 still True, plus the four
                     attribute-only names Verdict, Run, SampleResult,
                     wilson_lower_bound and EvidenceRecord)
```

These were the thinnest part of the promise under invariant 1
("model-migration-kit imports opik-rigor's *public* API only"): not private — no
leading underscore, and `SCORE_MIN` / `SCORE_MAX` documented in
`docs/session-2-contract.md` §0 — but reachable only through `opik_rigor.judge`,
so a rigor release could have moved them without touching `__all__` and been
within its rights.

**Closed in 0.1.1 and still closed in 0.2.0**, the version this file is now
verified against:

```
$ .\.venv\Scripts\python.exe -c "import opik_rigor; [print(...) for n in (...)]"
SCORE_MIN            in __all__: True  top-level attr: True
SCORE_MAX            in __all__: True  top-level attr: True
hash_rubric_file     in __all__: True  top-level attr: True
hash_rubric_text     in __all__: True  top-level attr: True
example_rubric_path  in __all__: True  top-level attr: True
```

The `opik_rigor.judge` spellings also still resolve, and to the same objects
(`opik_rigor.judge.SCORE_MIN is opik_rigor.SCORE_MIN` and the same for the other
three) — so the re-export is an alias rather than a move, and a consumer still on
the old spelling is not broken by 0.2.0. That was rigor's stated intent when the
names landed; it is now a run result rather than an intent.

Every site in this repository now imports them from the package root, and
`grep -rn "from opik_rigor\." src` returns nothing. That is checked on every push
rather than asserted: `scripts/dependency_surface.py --check` derives the table in
§1 from the AST and fails CI when this document and the tree disagree. It was
written because this record was wrong three times in a row about how many sites
existed, always understating — the fix landed with **six**, where the last count
said three.

One recorded exception, in tests only: `tests/test_stranger_path.py` imports
`opik_rigor.adapters.{anthropic,openai_compat}` for their `PACKAGE` constants,
which are not in `__all__`. It exists to detect drift — it asserts the SDK names
this CLI tells a reader to install are the ones rigor is actually about to import
— so it is the opposite of a hidden dependency, but it is a dependency and it is
listed rather than waved through.

### rigor's pytest plugin autoloads into this suite

```
$ cat .venv/Lib/site-packages/opik_rigor-0.2.0.dist-info/entry_points.txt
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
test n times and gate the pass rate with opik_rigor.assert_pass_rate. A body that returns
is a pass, one that raises AssertionError is a failure, any other exception is an exception
(harness broke, not the system under test).
```

The marker's `confidence=` is the third surface carrying 0.2.0's new refusal, and
the only one that is not in `__all__` — see the box at the top of this file and
§4.10. It reaches this suite whether or not this suite asks for it, which is why
an autoloading plugin is worth an entry here at all.

Optional provider SDKs are absent, as invariant 3 requires, and importing
`opik_rigor` does not pull them in:

```
opik       ABSENT
anthropic  ABSENT
openai     ABSENT
scipy      installed
numpy      installed

modules in sys.modules after `import opik_rigor`, from that list: ['numpy']
```

New in 0.2.0: `scipy` is **not** imported by `import opik_rigor` — it is now
loaded on first use. It is still a declared runtime dependency and still installed
here, so nothing this project does changes; the observable difference is import
time, and the absence of scipy from `sys.modules` is what shows the lazy import is
real rather than intended.

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
   single highest-consequence drift risk in this dependency. 0.2.0 grew that dict
   from 15 keys to 18 without touching either of the two that are read, which is
   the additive case; the read is by name and ignores the rest, so the new keys
   cost nothing here.
4. **The `p_value` key** on `assert_no_regression`'s return and on
   `RegressionError.stats`; Holm-Bonferroni correction is applied to it.
5. **`Verdict.passed` / `.score` / `.reason`**, and `JudgeOutputError` being the
   exception raised for an unparseable judge response — the parse-failure
   tolerance in `judging.py` counts exactly that type.
6. **`PinnedJudge`'s constructor keywords** and the `judge.init`-record semantics
   of rubric-drift detection (§4.6).
7. **`SCORE_MIN`, `SCORE_MAX` and `hash_rubric_file` remaining importable at
   all** — relaxed from "importable from `opik_rigor.judge`" once 0.1.1 exported
   them at the package root and every site here moved to that spelling. Their
   *values* still matter: `tests/test_comparison.py` asserts against the 1.0–5.0
   range directly, so a configurable score range in rigor is a change here even
   if the constants survive. Both held at 1.0 / 5.0 in 0.2.0.
8. **`EvidenceLog.append` / `.read`** and `EvidenceRecord`'s four fields, on which
   invariant 2 (the report renders from the evidence log) depends entirely.

9. **`FakeAdapter`'s constructor keywords** — `model_id`, `responses`, `cycle`,
   `latency`, `fail_with`, `fail_after`. Not production code, but the test suite
   and `migkit demo` are built on it, and CI's `demo` job is part of the
   definition of done. Note `**forbidden: ForbiddenKwarg` in its signature
   (`object` before 0.2.0 — the annotation moved, the behaviour did not): it rejects
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

12. **`AnthropicAdapter` and `OpenAICompatAdapter` taking `model_id` positionally
    as their first argument, and remaining importable from the package root.**
    `cli.py:336` and `cli.py:338` construct them — `AnthropicAdapter(model_id)`,
    `OpenAICompatAdapter(model_id)` — as the `--adapter anthropic` and
    `--adapter openai-compat` arms of `_model_adapter` (`cli.py:333`). What is
    relied on is exactly three things and no more: the import, the one positional
    parameter, and that the result satisfies the `Adapter` protocol, because
    `_model_adapter` is annotated `-> Adapter` and everything downstream reaches
    the object only through `.complete(prompt)` and `.model_id`. No keyword is
    passed, so `max_tokens`, `temperature`, `timeout` and `base_url` may all move;
    the defaults are accepted sight unseen. **They moved in 0.2.0 and this is what
    "accepted sight unseen" buys**: `AnthropicAdapter`'s `temperature` default went
    from `0.0` to `None` and nothing here changed, because no keyword is passed.
    Verified against the installed 0.2.0:

    ```
    $ .\.venv\Scripts\python.exe -c "import inspect; from opik_rigor import AnthropicAdapter, OpenAICompatAdapter; [print(c.__name__, inspect.signature(c.__init__)) for c in (AnthropicAdapter, OpenAICompatAdapter)]"
    AnthropicAdapter (self, model_id: 'str', *, max_tokens: 'int' = 1024, temperature: 'float | None' = None, timeout: 'float' = 60.0, **forbidden: 'ForbiddenKwarg') -> 'None'
    OpenAICompatAdapter (self, model_id: 'str', *, base_url: 'str | None' = None, max_tokens: 'int' = 1024, temperature: 'float | None' = 0.0, timeout: 'float' = 60.0, **forbidden: 'ForbiddenKwarg') -> 'None'

    $ ANTHROPIC_API_KEY=x OPENAI_API_KEY=x .\.venv\Scripts\python.exe -c "from opik_rigor import AnthropicAdapter, OpenAICompatAdapter, Adapter; [print(c.__name__, 'model_id=', repr(c('some-model-v1').model_id), 'isinstance(Adapter)=', isinstance(c('some-model-v1'), Adapter)) for c in (AnthropicAdapter, OpenAICompatAdapter)]"
    AnthropicAdapter model_id= 'some-model-v1' isinstance(Adapter)= True
    OpenAICompatAdapter model_id= 'some-model-v1' isinstance(Adapter)= True
    ```

13. **Both adapters raising `AdapterError` — not `KeyError`, not `RigorError` —
    when the credential environment variable is absent.** This is the *only*
    behaviour of either adapter this project has observed, and it is on the
    common path: `migkit run --adapter anthropic` with no key set must exit with
    one stderr line rather than a traceback, which works because `AdapterError`
    is in `cli.py`'s `EXPECTED_ERRORS` tuple (`cli.py:105-112`). Credentials are
    read from the environment inside the constructor, never passed in:

    ```
    $ .\.venv\Scripts\python.exe -c "import os; os.environ.pop('ANTHROPIC_API_KEY', None); from opik_rigor import AnthropicAdapter; AnthropicAdapter('some-model-v1')"
      File ".../opik_rigor/adapters/anthropic.py", line 172, in __init__
        self._api_key = require_env_key(ENV_ANTHROPIC_API_KEY, type(self).__name__)
      File ".../opik_rigor/adapters/base.py", line 105, in require_env_key
        raise AdapterError(
    opik_rigor.adapters.base.AdapterError: AnthropicAdapter needs the ANTHROPIC_API_KEY
    environment variable. Credentials are read from the environment only -- they are
    never accepted as constructor arguments -- so export ANTHROPIC_API_KEY before
    constructing the adapter.
    ```

    Note the direction: `AdapterError` subclasses `Exception` directly, not
    `RigorError`, which is why `cli.py:105-112` names it separately from
    `RigorError` in that tuple — catching only `RigorError` would let a missing
    credential escape as an unhandled traceback:

    ```
    $ .\.venv\Scripts\python.exe -c "from opik_rigor import RigorError, AdapterError, SampleTimeout, EvidenceError, JudgeOutputError, ModelPinError, PassRateError, RegressionError, RubricDriftError; [print(f'{e.__name__:18} issubclass(RigorError)={issubclass(e, RigorError)}  mro={[c.__name__ for c in e.__mro__[:4]]}') for e in (AdapterError, SampleTimeout, EvidenceError, JudgeOutputError, ModelPinError, PassRateError, RegressionError, RubricDriftError)]"
    AdapterError       issubclass(RigorError)=False  mro=['AdapterError', 'Exception', 'BaseException', 'object']
    SampleTimeout      issubclass(RigorError)=False  mro=['SampleTimeout', 'Exception', 'BaseException', 'object']
    EvidenceError      issubclass(RigorError)=True   mro=['EvidenceError', 'RigorError', 'Exception', 'BaseException']
    JudgeOutputError   issubclass(RigorError)=True   mro=['JudgeOutputError', 'RigorError', 'Exception', 'BaseException']
    ModelPinError      issubclass(RigorError)=True   mro=['ModelPinError', 'RigorError', 'Exception', 'BaseException']
    PassRateError      issubclass(RigorError)=True   mro=['PassRateError', 'StatisticalAssertionError', 'AssertionError', 'RigorError']
    RegressionError    issubclass(RigorError)=True   mro=['RegressionError', 'StatisticalAssertionError', 'AssertionError', 'RigorError']
    RubricDriftError   issubclass(RigorError)=True   mro=['RubricDriftError', 'RigorError', 'Exception', 'BaseException']
    ```

    A rigor release that *added* `RigorError` to `AdapterError`'s or
    `SampleTimeout`'s bases would not break anything here; it would only make
    those two tuple entries redundant. Removing `RigorError` from one of the other
    six would. All eight MROs are unchanged in 0.2.0.

14. **The range of `confidence` `assert_pass_rate` accepts**, added on 2026-08-14
    because 0.2.0 changed it. `comparison.py` passes `thresholds.confidence`
    straight through, so the accepted range *is* this project's accepted range,
    and a tightening at either end turns a config that used to run into a
    `ValueError` raised from inside a statistics call — no rename, no signature
    change, nothing a name-level diff sees. Recorded value by value in §4.10.
    Distinct from item 3 in one way that matters: this one raises, where item 3
    would return a wrong verdict quietly.

A rigor release does **not** break model-migration-kit by adding names, adding optional
keywords, changing the Opik integration, or changing anything about `Baseline` or
`assert_score_distribution` — neither of which is imported here. Nor by changing
what either provider adapter does *on the wire*: `complete()` is never called on
one in this project's tests, its CI, or `migkit demo`, all of which run keyless
(§7.7). The adapters' *import, construction and no-credential failure* are relied
on; their request bodies, retries, and provider SDK usage are not.

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

The `TypeError` is rigor's own, raised by `default_outcome`. **The message grew in
0.2.0 and now names the trap this section was written to record** — re-run against
the installed wheel, in full:

```
cannot decide pass/fail from str 'Paris'; sample() records this run as errored,
which drops it from .values, .outcomes, .successes and .completed -- so a whole
sample of these reads as pass_rate=0.0 beside failures=0, which looks like an
outage rather than an unanswered question. Return a bool or an object with a
boolean .passed attribute, or pass an explicit outcome=... callable to sample().
If you have no pass/fail question yet and only want the values back, say so with
outcome=lambda value: True.
```

It also now quotes the offending value (`str 'Paris'`), which is the difference
between reading the message and having to reproduce the call. The *behaviour*
below is unchanged in 0.2.0 — the refusal still lands in `Run.error` and `.values`
is still empty — so nothing in this section is retired, only the excuse that a
reader could not have known.

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

All three blocks above are unchanged in 0.2.0 — same two protocol members, same
public attributes on all three adapters, same sweep result over a 38-name
`__all__`. This section is entirely intact and neither of §3's two behaviours has
been retired.

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
wilson_interval(0, 5)     -> (0.0, 0.43448246478317465)
wilson_lower_bound(0, 5)  -> 0.0
wilson_interval(18, 20)   -> (0.6989663547715128, 0.9721335187862319)
wilson_lower_bound(18, 20)-> 0.7383369536731332
wilson_interval(5, 3)     -> ValueError: successes (5) cannot exceed n (3)
```

**Two of those numbers moved by one unit in the last place between 0.1.1 and
0.2.0**, and they are recorded rather than quietly re-pasted:
`wilson_interval(0, 5)`'s upper edge was `0.43448246478317476` and is now
`…65`; `wilson_lower_bound(18, 20)` was `0.7383369536731331` and is now `…32`.
Every other number in this section is identical to the digit. A last-place move is
what a rearranged floating-point expression looks like from outside — the same
formula, associated differently — and it is worth knowing that these values are
not stable enough to assert with `==` in a test. Nothing here does — the one test
that pins a rigor lower bound to full precision uses
`pytest.approx(..., rel=1e-9)` (`tests/test_comparison.py:647`), and the report
renders to 4 decimal places, where both numbers are unchanged.

Consequence for `report.py` (Session 3): a judge with zero completions on one side
must be rendered as an em-dash, never as `[0.0, 1.0]`. Calling the interval to
find out whether there is data will raise.

Note also that `wilson_interval` is **two-sided** and `wilson_lower_bound` is
**one-sided**; the gate uses the one-sided bound and the report prints the
two-sided interval, so the printed lower edge is *lower* than the number the gate
tested (`0.8350` vs `0.8597` in the example below). That is correct and it will
look like a bug to a reader unless the report says which is which. That
distinction stopped being cosmetic in 0.2.0: the one-sided function now refuses a
`confidence` the two-sided one accepts (§4.10), so the two are no longer
interchangeable at the argument level either.

### 4.2 `assert_no_regression` rejects `bool` and `None` in a score array

```
bools        -> ValueError: current[0] must be a number, got bool True
None present -> ValueError: current[1] must be a number, got NoneType None
float(bool)  -> {'gate': 'no_regression', ..., 'p_value': 0.9999946491728054, ...}
                (current = ten 1.0s, baseline = ten 0.0s -- the inputs are stated
                 because the first revision of this block did not, and re-running
                 it in 0.2.0 meant searching for them)
empty current-> ValueError: current has no scores; there is nothing to compare against baseline.
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

All four lines above held in 0.2.0, `p_value` to the last digit and the key set at
the same 16 names on both paths. The only difference is a full stop: the "no
scores" message now ends in one, and when the input is a `SampleResult` rather
than an empty sequence it continues into a second sentence naming what the runs
actually contained (§5.D). An assertion matching that message with `==` would
break; nothing here matches it at all.

### 4.3 `assert_pass_rate` carries `underpowered` / `runs_needed` only when it fails

Failure path, 38/40 against a 0.90 floor:

```
PassRateError.stats = {'gate': 'pass_rate', 'label': None, 'passed': False, 'n': 40,
 'successes': 38, 'failures': 2, 'pass_rate': 0.95, 'lower_bound': 0.8596681784340272,
 'interval_lower': 0.8349612263085903, 'interval_upper': 0.9861793326138516,
 'min_rate': 0.9, 'confidence': 0.95, 'method': 'wilson-one-sided',
 'underpowered': True, 'runs_needed': 113,
 'power_at_runs_needed': 0.6638064775606558, 'target_power': 0.8,
 'runs_for_target_power': 188}

pass rate gate failed: 38/40 passed (observed 0.9500); one-sided 95% Wilson lower bound
0.8597 < min_rate 0.9000. Two-sided 95% interval [0.8350, 0.9862]. The observed rate
0.9500 clears min_rate 0.9000 but the lower bound does not: this is an underpowered
sample, not a demonstrated failure. 40 runs cannot distinguish a system at 95.0% from
one at 86.0%. Hold the rate at exactly 0.9500 and the bound clears from 113 runs on.
That is arithmetic on this one rate, not a power calculation: a fresh sample of 113
runs from a system whose true rate is 95.0% lands above or below 95.0% at random, and
clears this gate only 66% of the time. Budget 188 runs to clear it 80% of the time;
that is the number to plan against.
```

**What moved here in 0.2.0, and what did not.** `runs_needed` is still 113 and
`underpowered` is still `True` — the two keys `comparison.py` decides REVIEW vs
NO-GO on. Three keys were added (`power_at_runs_needed`, `target_power`,
`runs_for_target_power`), taking the failure dict from 15 keys to 18, and the
message gained the paragraph that explains what 113 does and does not buy. Only
`lower_bound` moved, by one unit in the last place (`…271` → `…272`; the same
kind of move as §4.1). The success dict below is unchanged in every key and every
digit, at 13 keys.

The added keys are additive and safe here, but note what they are: `runs_needed`
now sits next to a number saying that running exactly `runs_needed` more times
clears the gate only 66% of the time. Anything that quotes 113 to a user without
`runs_for_target_power` beside it is quoting the optimistic half of a pair rigor
now ships whole.

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

All six held across 0.1.1 → 0.2.0 even though the rule underneath was rewritten
(see the box at the top: five undated frontier ids flipped `False` → `True`, and
`gpt-4.1` flipped `True` → `False`). Six unchanged answers over a replaced
implementation is the case for pinning examples rather than describing rules.

`my-model-v1-stable` is the instructive one: it *does* end in a version marker,
and it is still refused, because an alias token anywhere in the string
disqualifies it.

**The refusal message is rewritten in 0.2.0** and no longer states the rule the
same way — it now names the offending suffix and explains it, where 0.1.1 recited
the grammar:

```
require_pinned('gpt-4o') -> ModelPinError: judge refuses unpinned model id 'gpt-4o'.
It ends in '4o', which names a kind of model rather than one release of it, and a kind
is what a provider re-points. A pinned id names one immutable model version: it must
not contain an alias token (latest, newest, current, stable, default), and it must end
in a release designator -- a release number, a date stamp, or an explicit version -- as
in claude-opus-4-8, claude-haiku-4-5-20251001, gpt-4o-2024-08-06, my-finetune-v1. An
alias re-points over time, which silently invalidates every score recorded against it.
```

The operative change is "a release number" as an accepted designator: that is what
lets `claude-opus-5` and `claude-haiku-4-5` through, and it is why an undated
frontier id is now nameable as a judge. `migkit` passes `context='judge'`, which
is the word at the front of the message.

And the check fires in the constructor:

```
PinnedJudge(FakeAdapter(model_id="gpt-4o", ...), rubric, evidence)
  -> ModelPinError
```

So a bad judge config fails at `JudgeConfig.build(...)`, before a single API call
is spent. `FakeAdapter`'s default `fake-scripted-v1` passes, which is what makes
the keyless demo path possible at all.

### 4.5 Rubric hashing: CRLF is normalised, a bare CR is not, and a trailing newline changes the hash

`hash_rubric_text` normalises only the two-byte sequence `\r\n`. In 0.2.0 it also
accepts `str` (§5.C); the hashing itself is unchanged:

```python
def hash_rubric_text(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, (bytes, bytearray)):
        raise TypeError(...)
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
```

Re-run against 0.2.0. **The input is `b"# Rubric\nBe helpful.\n"`, stated here
because the previous revision of this block pinned digests without saying what was
hashed, which made every number in it unreproducible — including by the next
person to verify it:**

```
hash_rubric_text(LF)          = ee07eae3a91581280602eabfc8ba2fafddd15aa13294db4b35b6193f356f9638
hash_rubric_text(CRLF)        = ee07eae3a91581280602eabfc8ba2fafddd15aa13294db4b35b6193f356f9638
LF == CRLF                    : True
hash_rubric_text(bare CR)     = 0de45a5bb3b5137963cfca8c125ffa3281089c108a050fdcd8fccd25a823d39d
LF == bare CR                 : False
no trailing newline           = 718ecc0e50283db35cc59a0061759fe57e4746f0e9156dca3483204a7ab1a198
trailing newline changes hash : True
raw sha256(LF bytes)          = ee07eae3a91581280602eabfc8ba2fafddd15aa13294db4b35b6193f356f9638
equals hash_rubric_text(LF)   : True
hash_rubric_text("# Rubric\nBe helpful.\n")  ==  hash_rubric_text(same as bytes) : True
hash_rubric_file(lf.md)   == hash_rubric_file(crlf.md) : True
hash_rubric_file(missing) -> FileNotFoundError   (not a rigor-specific exception)
```

Every *relation* in this block is what it was in 0.1.1 — CRLF folds, bare CR does
not, the trailing newline matters, and an LF file's hash is plain `sha256` of its
bytes. The digests differ from the ones previously printed here only because the
input differs; there is no way to tell that from the old block, which is the
lesson. Digests are pinned to a stated input from now on.

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

Re-run in 0.2.0 with the §4.5 rubric edited from `Be helpful.` to
`Be extremely helpful.`:

```
RubricDriftError: rubric drift for judge 'helpfulness': evidence log last recorded
ee07eae3a91581280602eabfc8ba2fafddd15aa13294db4b35b6193f356f9638, rubric file now
hashes to 4cb8e0a1eda1fd62fed0d1c1833e145402ab17fc0c40793f83b9d5833f9f96fa. Scores
before and after this change are not comparable. Pass accept_rubric_change=True to
acknowledge and record the change.

different judge name, changed rubric -> constructed fine: 4cb8e0a1eda1fd62...
```

The two sentences after the hashes are printed in full this time; the previous
revision elided the tail, so whether they are new in 0.2.0 cannot be settled from
what this file recorded. Constructing the same changed rubric under a different
`name=` still succeeds. rigor
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
| `I think it is fine, honestly.` | `JudgeOutputError: judge response is not a verdict: response contained no JSON object` |
| `{"pass": "true", "score": 3}` | `JudgeOutputError: 'pass' must be a JSON boolean, got str 'true'` |
| `{"pass": true, "score": 9}` | `JudgeOutputError: 'score' 9 is outside the rubric's range 1-5; it is not clamped because a score the rubric cannot express means the judge misread the rubric` |

Every row still classifies the same way in 0.2.0 — same verdicts, same scores,
same three refusals. Two message texts grew: the no-JSON case gained the
`judge response is not a verdict:` prefix, the out-of-range case gained the clause
explaining why it is not clamped, and **all three `JudgeOutputError` messages now
end in `(raw response recorded in evidence log)`**, trimmed from the table above
for width. `judging.py` counts the exception type and never matches on text, so
this is free here; a consumer matching messages would be broken by it.

`SCORE_MIN == 1.0`, `SCORE_MAX == 5.0`, unchanged in 0.2.0 and still not
configurable. `judging.py` imputes failed completions at `SCORE_MIN`.

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
                                          (both lines identical in 0.2.0)
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
                       'label','lower_bound','method','min_rate','n','pass_rate','passed',
                       'successes']

  on the failing path the same record carries the six extra keys from §4.3:
  assertion.evaluated ['confidence','failures','gate','interval_lower','interval_upper',
                       'label','lower_bound','method','min_rate','n','pass_rate','passed',
                       'power_at_runs_needed','runs_for_target_power','runs_needed',
                       'successes','target_power','underpowered']
```

`assertion.evaluated` carries `successes` in 0.2.0, which the 0.1.1 record of this
block did not list — one key, and no way to tell from here whether it was added by
the release or missed by the earlier transcription. Recorded as observed rather
than as a diff. The evidence payload mirrors the stats dict on both paths, so the
asymmetry in §4.3 is visible in the log as well as in the exception.

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

Re-run in 0.2.0: every line above is identical, including the MRO. This is the
section §2 item 10 depends on — the class name is written into artifacts as data —
and it did not move.

### 4.10 0.2.0 refuses a one-sided `confidence` at or below 0.5

The one behaviour change in this release that can turn working code into a
`ValueError`. Run value by value against the installed wheel; `18/20` is used
throughout so the accepted answers are comparable:

```
wilson_lower_bound(18, 20, -0.2      ) -> ValueError: confidence must be strictly between 0 and 1, got -0.2
wilson_lower_bound(18, 20,  0.0      ) -> ValueError: confidence must be strictly between 0 and 1, got 0.0
wilson_lower_bound(18, 20,  0.1      ) -> ValueError: confidence must be greater than 0.5 for a one-sided bound, got 0.1
wilson_lower_bound(18, 20,  0.5      ) -> ValueError: confidence must be greater than 0.5 for a one-sided bound, got 0.5
wilson_lower_bound(18, 20,  0.5000001) -> 0.8999999831850252
wilson_lower_bound(18, 20,  0.51     ) -> 0.8983057373544914
wilson_lower_bound(18, 20,  0.9      ) -> 0.7816040731078
wilson_lower_bound(18, 20,  0.95     ) -> 0.7383369536731332
wilson_lower_bound(18, 20,  0.999    ) -> 0.5567327780349589
wilson_lower_bound(18, 20,  1.0      ) -> ValueError: confidence must be strictly between 0 and 1, got 1.0

assert_pass_rate((18, 20), 0.5, confidence=0.5      ) -> ValueError  (same two messages,
assert_pass_rate((18, 20), 0.5, confidence=0.0      ) -> ValueError   same boundaries)
assert_pass_rate((18, 20), 0.5, confidence=0.5000001) -> ok, lower_bound=0.8999999831850252
assert_pass_rate((18, 20), 0.5, confidence=0.51     ) -> ok, lower_bound=0.8983057373544914
```

So the accepted range is the open interval `(0.5, 1.0)` on both one-sided
surfaces, and `0.5` itself is refused. The boundary is exact: `0.5000001` is
accepted and returns `0.8999999831850252` — the observed rate `0.9` to seven
places, which is what the refusal message says a bound at 0.5 degenerates to.

**`wilson_interval` is deliberately unaffected** and still takes the full open
interval, which is the part a reader is most likely to assume wrong:

```
wilson_interval(18, 20, 0.0001) -> (0.8999915921989916, 0.9000084071726899)
wilson_interval(18, 20, 0.1   ) -> (0.8912522331050441, 0.9081166342348816)
wilson_interval(18, 20, 0.5   ) -> (0.8454875495484072, 0.9367197215490332)
wilson_interval(18, 20, 0.95  ) -> (0.6989663547715128, 0.9721335187862319)
wilson_interval(18, 20, 0.0   ) -> ValueError: confidence must be strictly between 0 and 1, got 0.0
wilson_interval(18, 20, 1.0   ) -> ValueError: confidence must be strictly between 0 and 1, got 1.0
```

Its `z` is `ppf((1 + c) / 2)`, non-negative across `(0, 1)`, so it never inverts
and there is nothing to refuse. The asymmetry is intentional and it means a caller
cannot infer one function's accepted range from the other's — §4.1's two-sided /
one-sided distinction now has teeth.

Both refusals raise plain `ValueError`, not a `RigorError` subclass, so they land
in `cli.py`'s `EXPECTED_ERRORS` through the `ValueError` entry rather than through
`RigorError`, and the two cases are distinguishable only by message text.

**The third surface: the pytest marker, which is not in `__all__`.**
`@pytest.mark.rigor_repeat(n, min_rate, confidence=...)` hands its value to
`assert_pass_rate` with no validation of its own, and does it *after* running the
test body `n` times. Verified by running pytest in a scratch directory (not this
repository) with `n=8` and `confidence=0.4`:

```
[rigor_repeat] ...::test_low_confidence_marker runs=8 successes=8 failures=0
               exceptions=0 pass_rate=1.0000 n=8 min_rate=0.5000 confidence=0.4000
F
RUNS SPENT: low=8 ok=8              <- counter incremented inside the test body
E   ValueError: confidence must be greater than 0.5 for a one-sided bound, got 0.4
...\opik_rigor\distribution.py:213: ValueError
1 failed, 2 passed
```

Eight executions, then the refusal — the plugin's own line reports `runs=8` before
raising, and the body's counter agrees. For a suite whose repeats are real model
calls that is `n` calls' worth of budget spent on a value that was invalid before
the first one. The marker is reachable in every suite that installs rigor, because
the plugin autoloads (§1). model-migration-kit does not use it (§7.9); this is
recorded for the consumer who does.

**One half of this is not re-runnable from here, and is marked rather than
smuggled in.** That 0.1.1 *accepted* `confidence <= 0.5` and answered is stated
throughout this file as the "before" side of the change; it cannot be verified in
this venv, because 0.1.1 is gone — `pip` replaced it. Everything above is a
statement about the installed 0.2.0, which is the version this file is verified
against; the 0.1.1 side rests on this repository's own history — `Thresholds`
bounded `confidence` to `(0.0, 1.0)` until today's commit widened nothing and
narrowed it to `(0.5, 1.0)` — rather than on a wheel anyone can still introspect
here. A future
re-verification could settle it in a throwaway venv; this one did not, because no
`pip install` was run (§7.11).

**What it costs this project:** nothing at runtime. `Thresholds.__post_init__`
(`judging.py:133-163`) bounds `confidence` to the open interval `(0.5, 1.0)` and
raises `ConfigError` at config load — the same interval rigor now enforces, one
level up and before any completions are paid for — and `pyproject.toml`'s floor
moved to `>=0.2` in the same change (§6). Both halves are needed: the floor
without the validation gives a config that fails inside a statistics call after
the runs, and the validation without the floor gives a config this package rejects
and the installed rigor would have accepted.

---

## 5. Friction found here that rigor's roadmap did **not** already have

Recorded rather than worked around, per invariant 1. None of these blocks
Session 2; all are cheap for rigor to fix and expensive for a consumer to
discover.

**Status of A–F: all six are closed in 0.2.0, and each was re-checked by running
the installed wheel rather than by reading a changelog.** The morning's revision
of this section recorded them as fixed in rigor's tree but **unreleased**, and
refused to describe them as behaviour on that basis. 0.2.0 released them. Each
entry below now carries what the wheel does, under the description of what it
used to do — kept rather than deleted, because the entry is also the record of how
the gap was found, and a consumer still pinned to 0.1.x meets the old behaviour.

```
$ .\.venv\Scripts\python.exe -c "import opik_rigor; print(opik_rigor.__version__)"
0.2.0
```

**A. `opik-rigor` shipped no `py.typed` marker. Closed in 0.2.0.**

```
$ .\.venv\Scripts\python.exe -c "from pathlib import Path; import opik_rigor; print((Path(opik_rigor.__file__).parent/'py.typed').exists())"
True             (False in 0.1.0, and recorded here as still open at 0.1.1)
```

The package is thoroughly annotated — every signature in §1 carries types — but
PEP 561 says a type checker must not use inline annotations from an installed
package that ships no `py.typed`. On that reading a consumer got no type
information at all from a fully typed dependency. That is now fixed: the marker is
in the wheel, alongside a new `typecheck` extra in rigor's metadata
(`mypy>=1.11; extra == 'typecheck'`).

Still honestly labelled: model-migration-kit runs `ruff` but no type checker
(`dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff>=0.6"]`), so **no checker has
been run against the annotations this marker now exposes**. The claim here is the
presence of the file, which is what the command shows, plus what PEP 561
specifies. What changed is that the gap is on this side of the fence now.

**B. `SampleResult.exceptions` returns `Run` objects, not exceptions. Renamed —
not fixed — in 0.2.0, deliberately.**

```python
@property
def exceptions(self) -> tuple[Run, ...]:
    """Deprecated alias of :attr:`errored_runs`, kept working forever."""
    return self.errored_runs
```

`errored_runs` is the new name and says what it hands back; `exceptions` still
returns the same tuple of `Run` objects, still emits no `DeprecationWarning`, and
per its docstring will keep working. Re-verified by printing
`type(e).__name__` over both: `['Run', 'Run']` from each, and the two tuples
compare equal. So the trap in the *old* name is unchanged — a caller writing
`[str(e) for e in result.exceptions]` still gets run reprs — and the fix is that
there is now a name that does not invite the mistake. Nothing here uses either.

**C. `hash_rubric_text(data: bytes)` took bytes despite the name. Closed in
0.2.0.** Passing a `str` used to fail inside the function rather than at the
boundary:

```
0.1.1:  TypeError: replace() argument 1 must be str, not bytes
0.2.0:  hash_rubric_text("hello\n")  == hash_rubric_text(b"hello\n")  -> True
        hash_rubric_text(3)          -> TypeError: hash_rubric_text() wants the
        rubric's text or bytes, got int; pass a str, pass bytes, or use
        hash_rubric_file(path) if what you have is a path
```

The old message pointed at rigor's own `b"\r\n"` literal rather than at the
argument the caller got wrong, so it read as the inverse of the actual mistake.
0.2.0 takes `bytes | str` — a `str` is encoded UTF-8 and hashed identically — and
refuses anything else by name at the boundary. model-migration-kit only uses
`hash_rubric_file`, so this cost one scratch-script iteration and nothing more,
which is the whole reason it was worth writing down rather than working around.

**D. `assert_no_regression(SampleResult, SampleResult)` on text completions
reported "no scores" rather than a type error. Closed in 0.2.0** — the message now
distinguishes the two cases, which is exactly what this entry asked for:

```
0.1.1:  ValueError: current has no scores; there is nothing to compare against
        baseline
0.2.0:  ValueError: current has no scores; there is nothing to compare against
        baseline. It is a SampleResult of 3 runs, 3 of which completed, but none
        of them carried a numeric .score: run 0 returned str 'Paris'. This gate
        compares judge scores, so pass the verdicts -- or a sequence of numbers --
        rather than the raw completions.
```

`SampleResult.scores()` still harvests `getattr(run.value, "score", None)`, so a
sample of *strings* still yields an empty tuple; what changed is that the caller
is now told the shape is wrong rather than that the data is missing. It was the
same class of confusion as §3.1 — a structural problem reported as an empty
result — and both were addressed in the same release. An empty sequence still
gets the short message, unchanged (§4.2).

**E. The published wheel contained no example rubric. Closed in 0.2.0.** Verified
by walking the installed package rather than by reading rigor's repository — the
0.1.x wheel shipped no `.md` file at all, the 0.2.0 wheel ships one:

```
$ .\.venv\Scripts\python.exe -c "from pathlib import Path; import opik_rigor; r=Path(opik_rigor.__file__).parent; print(sorted(p.relative_to(r).as_posix() for p in r.rglob('*') if '__pycache__' not in p.parts)); print('markdown files:', [p.name for p in r.rglob('*.md')])"
['__init__.py', 'adapters', 'adapters/__init__.py', 'adapters/anthropic.py',
 'adapters/base.py', 'adapters/fake.py', 'adapters/openai_compat.py',
 'baseline.py', 'distribution.py', 'errors.py', 'evidence.py', 'examples',
 'examples/__init__.py', 'examples/summarise_eval.py', 'integrations',
 'integrations/__init__.py', 'integrations/opik.py',
 'integrations/pytest_plugin.py', 'judge.py', 'pinning.py', 'py.typed',
 'rubrics', 'rubrics/example-rubric.md', 'sampling.py']
markdown files: ['example-rubric.md']

$ .\.venv\Scripts\python.exe -c "import opik_rigor; p=opik_rigor.example_rubric_path(); print(p, p.exists())"
...\.venv\Lib\site-packages\opik_rigor\rubrics\example-rubric.md True
```

`pip install opik-rigor==0.1.1` gave a consumer a `PinnedJudge` and nothing to
point it at; `pip install opik-rigor` now gives them a rubric that parses,
reachable as `opik_rigor.example_rubric_path()` without knowing a path. Three
things arrived in the wheel with it and are worth naming because they are new
surface a consumer can now reach: `py.typed` (item A), a `rubrics/` directory, and
an `examples/` subpackage containing `summarise_eval.py`. None of them is imported
here.

model-migration-kit wrote its own rubric (`src/model_migration_kit/data/demo_rubric.md`)
before this landed, which is arguably correct anyway — a rubric is the one file a
consumer should own — so nothing here changes on the strength of it.

The double-instruction hazard this entry used to carry is gone as well: the
shipped example does **not** contain `OUTPUT_FORMAT_INSTRUCTION`, so a rubric
copied from it will not carry the format block twice in the rendered prompt.
Checked against the file in the wheel, not against rigor's repository:

```
$ .\.venv\Scripts\python.exe -c "import opik_rigor; from opik_rigor.judge import OUTPUT_FORMAT_INSTRUCTION as OFI; t=opik_rigor.example_rubric_path().read_text(encoding='utf-8'); print('shipped example contains OUTPUT_FORMAT_INSTRUCTION:', OFI.strip() in t)"
shipped example contains OUTPUT_FORMAT_INSTRUCTION: False
```

**F. Three names model-migration-kit needs were not in `__all__`** — `SCORE_MIN`,
`SCORE_MAX` and `hash_rubric_file`. Detailed in §1; repeated here because it is
the one item on this list that model-migration-kit was *relying* on rather than merely
tripping over. **Closed in 0.1.1, and confirmed still closed in 0.2.0:**

```
$ .\.venv\Scripts\python.exe -c "import opik_rigor; [print(f'  {n:18} top-level attr in installed 0.2.0: {hasattr(opik_rigor, n)}') for n in ('SCORE_MIN','SCORE_MAX','hash_rubric_file','hash_rubric_text')]"
  SCORE_MIN          top-level attr in installed 0.2.0: True
  SCORE_MAX          top-level attr in installed 0.2.0: True
  hash_rubric_file   top-level attr in installed 0.2.0: True
  hash_rubric_text   top-level attr in installed 0.2.0: True
```

The migration was moving three import lines, and it is done — no file in `src/`
or `tests/` imports from `opik_rigor.judge` any more (§1's enumeration). §2 item 7
has been relaxed accordingly. The old spellings still resolve, to the same objects
(§1), which is now a run result rather than rigor's stated intent.

---

## 6. Version policy

`pyproject.toml` declares:

```toml
dependencies = [
  # The whole point: every statistical primitive is imported, none reimplemented.
  #
  # Floor raised to 0.2 on 2026-08-14, and the reason is a behaviour change rather
  # than a new feature: 0.2.0 refuses a one-sided `confidence` at or below 0.5,
  # ...
  "opik-rigor>=0.2,<0.3",
  ...
]
```

- **Lower bound `0.2`**, raised from `0.1.1` on 2026-08-14, and note *why*: not
  because 0.2.0 added something this project wanted, but because it **removed an
  accepted argument value** (§4.10). `Thresholds.confidence` is handed straight to
  `assert_pass_rate`, and `judging.py` now refuses the same range at config load.
  The floor and that validation have to move together — a floor without the
  validation lets a config fail inside a statistics call after the runs are paid
  for, and validation without the floor rejects configs the installed rigor would
  have accepted. This is the first floor here raised by a *narrowing*, and it is
  the argument for pinning a floor to verified behaviour rather than to a feature
  list.
- **Upper bound `<0.3`** for the reason `<0.2` was written: the next minor is
  where rigor's own roadmap proposes to move exactly what this project reads — a
  non-raising `check_pass_rate(...) -> report` beside the asserting one (item 3)
  and frozen report dataclasses replacing `dict[str, Any]` (item 4), while
  `comparison.py` gets `underpowered` and `runs_needed` by catching
  `PassRateError` and reading `.stats`. Items 8 and 9 would change `sample`'s
  classification behaviour and the `Adapter` protocol, both of which `runner.py`
  sits on. 0.2.0 did none of those; it did the confidence narrowing instead, which
  is a reminder that the bound is protection against *a* change, not a prediction
  of which one.

  The failure mode of guessing wrong is what makes the bound tight rather than
  polite. `assert_pass_rate` returns a plain `dict`, so a renamed key is a
  `KeyError` at best and a `.get(...)` default at worst — and per build-plan §6 a
  missing `underpowered` flag silently converts REVIEW into NO-GO. A wrong verdict
  that looks like a right one is the one failure this project exists to refuse,
  so the bound stays at the minor version until the surface is re-verified.

  0.1.1 → 0.2.0 is the first upgrade this file has performed rather than
  described, and the cost of the re-verification was one afternoon against a
  release that broke one argument value and no names. The bound did its job in
  the boring direction: nothing resolved to 0.2.0 until this document said it
  could.

rigor depends on `numpy>=1.21` and `scipy>=1.10` (numpy is declared directly in
0.2.0, not merely inherited through scipy), so model-migration-kit gets both
transitively. `comparison.py` imports neither; the statistics come through rigor's
API, which is the point. New in 0.2.0: scipy is imported lazily, so it is no
longer in `sys.modules` after `import opik_rigor` (§1) — a change to import cost,
not to the dependency.

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
3. **What else `>=0.2,<0.3` could resolve to was not checked.** No network call
   was made while re-verifying this file — the 0.2.0 wheel was already installed
   when this pass began. The installed version is 0.2.0; whether PyPI now carries
   a 0.2.1 that would satisfy the bound is unknown here. This is the gap that bit
   once already: a permissive floor plus a long-lived venv meant this document
   described 0.1.0 for a day after strangers were getting 0.1.1.
4. **The `.venv` here is not clean-room.** It is the working environment, created
   with `pip install -e ".[dev]"` and since upgraded in place from 0.1.0 to 0.1.1
   to 0.2.0. The opik-rigor 0.2.0 dist-info has no `direct_url.json` and does have
   `REQUESTED`, which establishes it came from an index by name rather than from a
   path, but the venv as a whole has not been rebuilt from scratch for this file.
5. **Concurrency was not exercised.** `sample(..., concurrency=N)` for `N > 1` is
   passed through by `runner.py` and was verified only at the default `1`.
6. **`assert_score_distribution` and `Baseline` were not verified**, because
   model-migration-kit does not import them.
7. **No live provider adapter was called.** `AnthropicAdapter` and
   `OpenAICompatAdapter` were introspected and *constructed* — with and without a
   credential in the environment, which is what §2 items 12 and 13 rest on — but
   `complete()` was never invoked on either, so nothing here describes a request
   this project sends or a response it parses. There are no credentials in this
   environment and CI blanks them; the only value ever put in `ANTHROPIC_API_KEY`
   or `OPENAI_API_KEY` while writing this file was the literal `x`, to get past
   the constructor's env check.
8. **Judge behaviour was verified against `FakeAdapter`,** so §4.7 describes
   rigor's *parser*, not how any real model actually answers. The parse-failure
   tolerance in `judging.py` exists precisely because that number is unknown.
9. **rigor's pytest plugin: `rigor_repeat` was exercised in 0.2.0, the rest was
   not.** The marker was run end to end in a scratch directory to establish where
   its `confidence=` is validated (§4.10) — that much is now behaviour, not
   inference. The `rigor_evidence` / `rigor_judge` fixtures and the
   `rigor_evidence_path` ini option remain unexercised; model-migration-kit does not
   use any of them. This repository's own test suite was not run while re-verifying
   this file — other work was in flight in `tests/` — so nothing here rests on a
   green suite.
10. **No type checker was run** (see §5.A). 0.2.0 ships `py.typed`, so the
    annotations are now exposed to one; nobody has pointed one at them from this
    side, and `dev` still has no mypy or pyright.
11. **No `pip install` was performed during this pass.** The upgrade to 0.2.0 was
    already in the venv when re-verification began; every command in this file is
    read-only against it, apart from the one `pytest` run in a scratch directory
    for §4.10 and the temporary rubric files §4.5 and §4.6 hash, none of which are
    in this repository.

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
5. **Diff the accepted *values*, not only the names.** 0.1.1 → 0.2.0 moved no name
   and no signature this project relies on, and still broke a config: `confidence`
   at or below 0.5 became a `ValueError`. `__all__` and `inspect.signature` cannot
   see that. Re-run the boundary cases in §4.10, §4.1 and §4.4 — the arguments a
   caller is most likely to have set to something unusual — before concluding a
   release is additive.
6. **Pin every number to a stated input.** The digests in §4.5 could not be
   reproduced on re-verification because the text that was hashed was never
   written down, and the `p_value` in §4.2 had to be found by search. A number
   without its input is a number nobody can check, which is the one thing this
   file is for.
