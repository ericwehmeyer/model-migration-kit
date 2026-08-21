# model-migration-kit

[![CI](https://github.com/ericwehmeyer/model-migration-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/ericwehmeyer/model-migration-kit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/ericwehmeyer/model-migration-kit/blob/main/pyproject.toml)
[![License Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](https://github.com/ericwehmeyer/model-migration-kit/blob/main/LICENSE)

[![PyPI](https://img.shields.io/pypi/v/model-migration-kit)](https://pypi.org/project/model-migration-kit/)

Is it safe to move from model A to model B? `migkit` answers with **GO**,
**NO-GO** or **REVIEW** — decided by Wilson intervals and a Mann-Whitney test over
a golden set, not by eyeballing a handful of outputs — and returns an exit code a
CI job can gate on.

```
VERDICT: NO-GO (exit 1)
```

Every command and every block of output below was executed, and the output is
pasted rather than described. Absolute paths are abbreviated in the transcripts
and nothing else is edited; the `exit: N` lines are the shell reporting `$?`, not
part of `migkit`'s own output. Where a claim could **not** be executed here, it
says so rather than dressing itself up.

---

## The problem

Every team running LLMs in production eventually faces a forced migration: a
deprecation, a price change, a provider switch. The usual method is to run a dozen
prompts through the new model, read the answers, and ship.

That method cannot distinguish the three things you need distinguished. It cannot
tell a real quality drop from sampling noise, because it samples once. It cannot
tell a model that answers badly from one that times out, because both just look
like a bad row. And it cannot tell "B is worse" from "we did not collect enough
evidence to know" — so it concludes something anyway, and what it concludes tends
to be whatever the person reading the outputs already expected.

`migkit` takes a golden set, runs both models against it *n* times per item,
grades every completion with the same pinned judges, and produces a verdict, a
report, and an exit code. The report is the change-control evidence: it names the
golden set by hash, the judges by pinned model id and rubric hash, every threshold
alongside the file it came from, and every item whose verdict flipped.

---

## Quickstart — the keyless path

`migkit demo` runs the whole flow against a bundled 12-item golden set using
scripted `FakeAdapter`s. No API key, no network, nothing to configure.

Executed on Windows 11, Python 3.14.4, in a fresh virtualenv, installing the built
wheel from a checkout:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install <checkout>
.venv\Scripts\migkit.exe demo
```

```
Successfully built model-migration-kit
Installing collected packages: pygments, numpy, mdurl, MarkupSafe, scipy, markdown-it-py, jinja2, rich, opik-rigor, model-migration-kit
Successfully installed MarkupSafe-3.0.3 jinja2-3.1.6 markdown-it-py-4.2.0 mdurl-0.1.2 model-migration-kit-0.1.0 numpy-2.5.2 opik-rigor-0.2.0 pygments-2.20.0 rich-15.0.0 scipy-1.18.0
```

Progress goes to stderr:

```
migkit: demo: 12 items x n=5, no credentials, no network
migkit: sampling fake-baseline-v1
migkit: sampling fake-candidate-v1
migkit: judging fake-baseline-v1 with accuracy
migkit: judging fake-candidate-v1 with accuracy
migkit: comparing
```

The report goes to stdout:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ FAKE MODELS - these numbers describe scripted responses, not a real         │
│ provider                                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────── VERDICT ──────────────────────────────────┐
│ NO-GO  (exit 1)                                                             │
│ Judge 'accuracy' shows a statistically significant regression after         │
│ Holm-Bonferroni correction across judges.                                   │
└───────────────────────────── decided by rule 1 ─────────────────────────────┘
                               What was compared                               
┌──────────────────────────────────────┬──────────────────┬───────────────────┐
│                                      │ baseline         │ candidate         │
├──────────────────────────────────────┼──────────────────┼───────────────────┤
│ model                                │ fake-baseline-v1 │ fake-candidate-v1 │
│ adapter                              │ FakeAdapter      │ FakeAdapter       │
│ completions                          │ 60 / 60          │ 60 / 60           │
│ failed completions                   │ 0                │ 0                 │
│ parts                                │ 1                │ 1                 │
│ latency median / p90 (descriptive    │ 0.000 / 0.000    │ 0.000 / 0.000     │
│ only, never a gate)                  │                  │                   │
└──────────────────────────────────────┴──────────────────┴───────────────────┘
 golden set                         C:\Users\...\AppData\Local\Temp\migkit-… 
                                    (5fef50364057cad8)                         
 golden-set size                    12                                         
 judges hash                        bb624f0ed1781d85                           
 config                             C:\Users\...\AppData\Local\Temp\migkit-… 
                                    (1ad89c46dcbd426d)                         
 n per item                         5                                          
 threshold alpha                    0.05                                       
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
 threshold confidence               0.95                                       
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
 threshold judge_failure_tolerance  0.05                                       
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
 threshold min_detectable_effect    0.1                                        
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
 threshold pass_rate_floor          0.9                                        
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
 threshold power_target             0.8                                        
                                    (C:\Users\...\AppData\Local\Temp\migkit… 
                        judge: accuracy (fake-judge-v1)                        
┌───────────────────────────────────────┬──────────────────┬──────────────────┐
│                                       │ baseline         │ candidate        │
├───────────────────────────────────────┼──────────────────┼──────────────────┤
│ passed / observed                     │ 55 / 60          │ 45 / 60          │
│ pass rate                             │ 91.7%            │ 75.0%            │
│ Wilson interval (two-sided)           │ [0.8193, 0.9639] │ [0.6277, 0.8422] │
│ Wilson lower bound (one-sided, the    │ 0.8385           │ 0.6486           │
│ gate)                                 │                  │                  │
│ items passing / failing / unstable    │ 11 / 1 / 0       │ 9 / 3 / 0        │
│ p-value (alpha)                       │ 0.007843 (0.050) │                  │
│ test that ran                         │ mann-whitney-u   │                  │
│ regressed / floor cleared /           │ yes / no / no    │                  │
│ underpowered                          │                  │                  │
└───────────────────────────────────────┴──────────────────┴──────────────────┘
    Flips (passing -> failing): 3     
┌────────────┬────────────┬──────────┐
│ item       │ margin     │ judges   │
├────────────┼────────────┼──────────┤
│ extract-01 │ 5/5 -> 0/5 │ accuracy │
│ refuse-02  │ 5/5 -> 0/5 │ accuracy │
│ refuse-04  │ 5/5 -> 0/5 │ accuracy │
└────────────┴────────────┴──────────┘
   Gains (failing -> passing; never   
       netted against flips): 1       
┌────────────┬────────────┬──────────┐
│ item       │ margin     │ judges   │
├────────────┼────────────┼──────────┤
│ extract-03 │ 0/5 -> 5/5 │ accuracy │
└────────────┴────────────┴──────────┘
  Unstable items (a coin  
   toss on one or both    
        sides): 0         
┌──────┬────────┬────────┐
│ item │ margin │ judges │
├──────┼────────┼────────┤
│ none │        │        │
└──────┴────────┴────────┘
Every one of the 4 changed item(s) carries its full outputs: 5,821 characters 
of quoted model text against a budget of 10,000,000.
warning: judge 'accuracy': 60 completions per side cannot detect a 10% drop at 
80% power; roughly 140 are needed.
Full outputs, the flip list and the methodology appendix are in the HTML 
report; a terminal is not where anyone reads 5 pairs of model outputs.
VERDICT: NO-GO (exit 1)
...\migkit-demo-report.html
VERDICT: NO-GO (exit 1)
```

### The demo exits 1, and that is the demo working

**Exit 1 is NO-GO. It is not a crash.** The demo exists to show the tool refusing
an unsafe migration, so its scripted candidate is genuinely worse than its scripted
baseline, the regression test detects it, and the process exits with the code a CI
gate would receive. A demo that exited 0 would mean the verdict logic had stopped
working. This project's own CI asserts the code is exactly 1 rather than merely
tolerating a non-zero exit, for that reason.

### Timing

Measured with a stopwatch around the process, in the clean virtualenv above:

| run | wall clock |
|---|---|
| first run after install (cold imports of scipy/numpy) | **20.69 s** |
| second run | **2.11 s** |

The demo does 60 completions and 60 judge calls per side, all in-process, with no
sleeps and no network.

### The HTML report

`migkit demo` also writes a self-contained HTML file and prints its absolute path
as the last line before the verdict. It is meant to open inside a compliance review
on a machine with no route to the internet, so it fetches nothing at view time —
checked with the tool's own detector against the file the run above produced:

```
$ python -c "from pathlib import Path; from model_migration_kit.report import external_urls; print(external_urls(Path('migkit-demo-report.html').read_text(encoding='utf-8')))"
()
```

The file was 25,931 bytes, LF-terminated, with zero `<script>` and zero `<link>`
elements. It contains the verdict banner, what was compared, the per-judge tables,
the flip list with every sampled output behind a `<details>` element, a generated
methodology appendix, and a provenance footer of hashes.

---

## Exit codes are the CI contract

| code | verdict | meaning |
|---|---|---|
| `0` | `GO` | No judge regressed, the candidate cleared the pass-rate floor, and the sample was powerful enough for that to mean something |
| `1` | `NO-GO` | At least one judge shows a significant regression, or the floor was missed on a sample that was not underpowered |
| `2` | `REVIEW` | The evidence does not settle it. Collect more |
| `3` | *(error)* | The tool could not produce a verdict at all |

Changing these is a breaking change to every pipeline that consumes the tool, and
the project treats it as one.

`migkit run` is the exception worth knowing about: it produces no verdict, so its
`0` means "the run completed" and never means GO. A pipeline gating on `migkit run`
is gating on nothing. `migkit --help` says so too, on the `run` row.

A gate, in shell:

```bash
migkit compare --baseline .migkit/old.jsonl \
               --candidate .migkit/new.jsonl \
               --judges migkit.toml \
               --html migration-report.html
case $? in
  0) echo "GO" ;;
  1) echo "NO-GO: regression detected"       ; exit 1 ;;
  2) echo "REVIEW: underpowered, not a pass" ; exit 1 ;;
  *) echo "migkit failed"                    ; exit 1 ;;
esac
```

Note that `2` exits non-zero there. REVIEW is not a pass, and a pipeline that
treats it as one has re-introduced the guess this tool exists to remove.

### The four codes, against four fixtures

`tests/fixtures/` holds a pair of run artifacts per code — `go-a.jsonl` and
`go-b.jsonl`, then `nogo-`, `review-` and `error-` — plus the `goldenset.jsonl`
they were sampled against, the `rubric.md` the judge applies, and the
`judges.toml` that names the judge and every threshold. Each pair genuinely
produces the verdict its name claims, and none of it was hand-written:
`tests/fixtures/make_fixtures.py` scripts two `FakeAdapter`s per case and lets
`comparison.compare` decide, which is how `migkit demo` works and is the only way
to get a fixture whose verdict is a result rather than an assertion.

**The five commands in this section and the next need the repository.** `tests/`
is not in the wheel, so `pip install model-migration-kit` does not give you these
paths; the installed package carries its own demo data
(`demo_goldenset_path()` and its two siblings) and nothing else. A clone works, and
so does an unpacked sdist — all twelve files under `tests/fixtures/`, including
`make_fixtures.py`, ship in the tarball. From a plain wheel install, `migkit demo`
is the equivalent that runs, and it is what the definition of done is measured
against.

The four verdicts, re-derived from the committed artifacts, together with the
check that a rebuild reproduces those artifacts byte for byte:

```
$ python tests/fixtures/make_fixtures.py --check
11 committed fixture files are byte-identical to a rebuild
go     -> GO      exit 0  ok  (rule 5: No judge regressed, every judge cleared the pass-rate floor, and every judge had enough completions to have seen the configured minimum effect.)
nogo   -> NO-GO   exit 1  ok  (rule 1: Judge 'accuracy' shows a statistically significant regression after Holm-Bonferroni correction across judges.)
review -> REVIEW  exit 2  ok  (rule 4: Judge 'accuracy' has too few completions to detect the configured minimum effect at the configured power, so 'no regression detected' would be a question never asked.)
migkit: ArtifactError: the golden set at tests/fixtures/goldenset.jsonl has changed since fixture-error-baseline-v1 was run (84d623332ed60ad5 now, b3c2853a494d3472 then). Judging these completions against it would grade answers to questions nobody asked.
error  -> ERROR   exit 3  ok  (migkit compare refused the pair)
```

Each case is a situation rather than a value someone typed. `go` is two models of
equal quality that phrase two answers differently, at 12 items × n=5 — sixty
completions a side, which clears the fifty-six the power approximation asks for,
so "no regression detected" is a question that was actually asked. `review` is the
same two models at n=3: thirty-six completions a side, under the bar, so the tool
refuses to read "we saw nothing" as "there is nothing". `nogo` is a candidate that
reads a subtotal as a total, complies with a request to announce a breach that did
not happen, and invents a refund figure. `error` is a golden set that changed after
the run — the drift guard, which fires before a judge is ever constructed.

### `migkit compare` will not reach GO, NO-GO or REVIEW without a credential

The table above is re-derived through the shipped `JudgeConfig.build`,
`judge_artifact` and `compare`, with only the judge's *adapter* scripted. It has to
be, and the reason is worth pasting rather than describing. Here is the same matrix
run through the console script exactly as the release checklist writes it:

```bash
$ for f in go nogo review error; do
    migkit compare --baseline tests/fixtures/$f-a.jsonl \
                   --candidate tests/fixtures/$f-b.jsonl \
                   --judges tests/fixtures/judges.toml
    echo "$f -> $?"
  done
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
go -> 3
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
nogo -> 3
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
review -> 3
migkit: ArtifactError: the golden set at tests/fixtures/goldenset.jsonl has changed since fixture-error-baseline-v1 was run (84d623332ed60ad5 now, b3c2853a494d3472 then). Judging these completions against it would grade answers to questions nobody asked.
error -> 3
```

**That is the tool working, not a broken fixture**, and the first three lines say
why: a scripted judge is refused outright, because a fake *model* is disclosed by
the report's red band and a fake *judge* is not. Declaring a real judge instead
does not help. The next two blocks use `$TMP/judges-anthropic.toml`, which is
`tests/fixtures/judges.toml` with one word changed — `adapter = "anthropic"` — and
is kept out of the repository so that nothing here can be mistaken for a config
the fixtures were graded with. Both provider adapters read their credential at
construction:

```
$ ANTHROPIC_API_KEY= migkit compare --baseline tests/fixtures/go-a.jsonl \
      --candidate tests/fixtures/go-b.jsonl --judges $TMP/judges-anthropic.toml
migkit: AdapterError: AnthropicAdapter needs the ANTHROPIC_API_KEY environment variable. Credentials are read from the environment only -- they are never accepted as constructor arguments -- so export ANTHROPIC_API_KEY before constructing the adapter.

exit: 3
```

And supplying one does not help either, because the judge then does what a judge
does — it starts spending the credential on the fixtures:

```
$ ANTHROPIC_API_KEY=not-a-real-key migkit compare --baseline tests/fixtures/go-a.jsonl \
      --candidate tests/fixtures/go-b.jsonl --judges $TMP/judges-anthropic.toml
migkit: judging with accuracy
migkit: grading fixture-go-baseline-v1
migkit: AdapterError: AnthropicAdapter needs the 'anthropic' package, which is not installed. Install it with: pip install anthropic

exit: 3
```

So `migkit compare` is a credentialed verb by design, and the keyless half of the
exit-code contract is checked two other ways. The fixture matrix above is one. The
suite is the other — it drives `cli.main` through every code and pins the table to
`contracts.Verdict.EXIT_CODES`:

```
$ python -m pytest tests/test_cli.py -k "TestExitCodeContract"
..............                                                           [100%]
14 passed, 64 deselected in 0.84s
```

Exit `3`, produced live:

```
$ migkit report .\does-not-exist.jsonl
migkit: ArtifactError: no evidence log at does-not-exist.jsonl. opik-rigor reads a missing log as an empty one, so this is checked here: a mistyped path would otherwise render as a valid report of a run that never happened.

exit: 3
```

---

## What REVIEW means, and why it exists

A tool that must answer GO or NO-GO has to guess whenever the sample is too small
to settle the question — and it will guess in whichever direction its author found
comfortable. REVIEW is the refusal to do that. It says: this run could not have
detected the regression you asked about, so "no regression detected" would be a
question never asked, reported as answered.

A verdict is REVIEW when the sample cannot reach the configured power target
(`power_target`, default 0.80) for the configured minimum detectable effect
(`min_detectable_effect`, default a ten-point drop in pass rate), or when the
candidate missed the pass-rate floor on a sample that opik-rigor itself flags as
underpowered. **REVIEW is never silently converted to GO.** NO-GO outranks it,
because a regression that reached significance was, for that question, powered
enough.

### The consequence, stated plainly

Underpowered is the normal case for a small golden set. The tool computes what it
would need from the observed baseline rate:

```
$ python -c "from model_migration_kit.comparison import required_sample_size as r; print([r(p, min_detectable_effect=0.10, power_target=0.80, alpha=0.05) for p in (0.99, 0.95, 0.90, 0.80)])"
[67, 109, 155, 229]
```

That is 67, 109, 155 and 229 completions **per side** at baseline pass rates of
99%, 95%, 90% and 80%. A ten-item golden set at n=5 gives you fifty — below every
one of them. **A ten-item set at n=5 will usually give you REVIEW, not GO**, unless
the candidate is bad enough that the regression test fires anyway and the answer is
NO-GO. The build plan's simulated figure for 80% power against a ten-point drop is
roughly 200 completions per side; the approximation above is rate-dependent and
lands in the same territory.

The bundled demo is itself in this position and says so in its own output — 12
items at n=5 is 60 completions per side, which is not enough to promise detection
of a ten-point drop:

```
warning: judge 'accuracy': 60 completions per side cannot detect a 10% drop at 
80% power; roughly 140 are needed.
```

The demo still returns NO-GO because its scripted candidate regressed by far more
than ten points and the test reached significance at p=0.007843. Had the difference
been smaller, the honest answer at 60 completions would have been REVIEW, and that
is what the tool would have printed.

If that is irritating, the fix is more draws or more items, not a looser gate. That
is the whole argument.

---

## Real models — the path this file could **not** execute

Everything above ran keyless. Everything in *this* section is the documented
interface for a reader who has API keys. **No live provider call was made while
writing this file**, because there are no credentials in this environment — the
two variables the adapters read are empty, shown here between brackets:

```
ANTHROPIC_API_KEY = []
OPENAI_API_KEY = []
```

So read this section as the interface, not as a transcript. It is kept separate
from the quickstart for exactly that reason.

Write a config naming the judges and the thresholds:

```toml
[[judge]]
name    = "accuracy"
model   = "claude-sonnet-4-5-20250929"
adapter = "anthropic"
rubric  = "rubric.md"

[thresholds]
pass_rate_floor         = 0.90
alpha                   = 0.05
confidence              = 0.95
judge_failure_tolerance = 0.05
min_detectable_effect   = 0.10
power_target            = 0.80
```

Then run each model and compare:

```bash
migkit run --goldenset goldenset.jsonl --model <baseline-id>  --adapter anthropic --n 20
migkit run --goldenset goldenset.jsonl --model <candidate-id> --adapter anthropic --n 20
migkit compare --baseline  .migkit/<baseline-id>__<hash>.jsonl \
               --candidate .migkit/<candidate-id>__<hash>.jsonl \
               --judges migkit.toml \
               --html migration-report.html
```

Two things about that config *were* checked, because they can be checked without
spending a credential — it parses, and the judge's model id survives the pin rule:

```
$ python -c "from model_migration_kit.judging import JudgeConfig; c = JudgeConfig.load('judges.toml'); print(c.specs[0]); print(c.thresholds)"
JudgeSpec(name='accuracy', model='claude-sonnet-4-5-20250929', rubric=WindowsPath('rubric.md'), rubric_hash='cc39e4aad0ef5db821fb627bb1217bab78095543642634bc2d30581f642c6268', adapter='anthropic')
Thresholds(pass_rate_floor=0.9, alpha=0.05, confidence=0.95, judge_failure_tolerance=0.05, min_detectable_effect=0.1, power_target=0.8)
```

The judge's model id must be pinned. `claude-3-5-sonnet-latest` is refused at
config load, before a single API call is spent, because an alias re-points over
time and silently invalidates every score recorded against it. A judge cannot use
`adapter = "fake"` either: a scripted *model* is disclosed by the report's red
band, but a scripted *judge* would hand real completions a clean bill of health
with nothing in the document saying the grades were invented.

### Checking the wiring before you spend anything

`migkit run --adapter fake` answers every prompt with one fixed sentence. It
measures nothing about any model; it exists to prove your golden set loads, your
artifact writes, and a resume resumes. This one *was* executed:

```
$ migkit run --goldenset goldenset.jsonl --model fake-wire-v1 --adapter fake --n 2
migkit: 12 items x n=2 (--n) against fake-wire-v1 via FakeAdapter
migkit: [1/12] arith-01: 2 draw(s)
migkit: [2/12] arith-02: 2 draw(s)
[... items 3-11 elided, one line each ...]
migkit: [12/12] refuse-04: 2 draw(s)
migkit: 24 completion(s), 0 failed, 1 part(s)
...\.migkit\fake-wire-v1__5fef50364057cad8.jsonl

exit: 0
```

Run artifacts are resumable and keyed by `(model, golden-set hash)`. Running the
same command again re-samples nothing:

```
$ migkit run --goldenset goldenset.jsonl --model fake-wire-v1 --adapter fake --n 2
migkit: 12 items x n=2 (--n) against fake-wire-v1 via FakeAdapter
migkit: 24 completion(s), 0 failed, 1 part(s)
...\.migkit\fake-wire-v1__5fef50364057cad8.jsonl

exit: 0
```

A resumed run is disclosed in the report as "completed in *n* parts", not hidden.
`--fresh` discards the artifact and starts over.

---

## The golden-set format

JSONL, one object per line. `id` and `input` are required; `reference` and `tags`
are optional. These are the first three lines of the bundled demo set:

```json
{"id": "arith-01", "input": "What is 17 + 25? Answer with the number only.", "reference": "42", "tags": ["arithmetic"]}
{"id": "arith-02", "input": "What is 144 divided by 12? Answer with the number only.", "reference": "12", "tags": ["arithmetic"]}
{"id": "arith-03", "input": "A basket holds 8 apples. Three baskets are emptied into one crate. How many apples are in the crate?", "reference": "24", "tags": ["arithmetic"]}
```

A set has a content hash — taken over the parsed items rather than the raw bytes,
so reformatting the file does not invalidate a baseline that cost real money — and
that hash is embedded in every downstream artifact. Two artifacts are comparable
only if it matches. Tags feed the distribution the report prints:

```
$ python -c "from model_migration_kit import demo_goldenset_path; from model_migration_kit.goldenset import GoldenSet; gs = GoldenSet.load(demo_goldenset_path()); print(gs.stats()); print(gs.hash)"
{'size': 12, 'with_reference': 8, 'untagged': 0, 'tags': {'arithmetic': 4, 'extraction': 4, 'multi-value': 2, 'refusal': 4}}
5fef50364057cad869f16698df32d927b650778c34382f6f68d9fd53ba4e9a04
```

Validation is strict and loud, because a golden set that loads with a silent defect
produces a report that looks fine:

```
$ migkit run --goldenset dupes.jsonl --model fake-wire-v1 --adapter fake --n 1
migkit: GoldenSetError: dupes.jsonl line 2: duplicate id 'a-1', already defined on line 1. Ids key the per-item flip list in every comparison; two items sharing one make that list wrong rather than incomplete.

exit: 3
```

---

## Install

The distribution is named **`model-migration-kit`**, the import package is
`model_migration_kit`, and the console script is `migkit`.

**Published on PyPI.** Verified on 2026-08-21:

```bash
python -m pip install model-migration-kit
migkit demo
```

The JSON endpoint 404s for a name nobody has registered *and* for a name someone
registered without ever uploading to. The PEP 503 index tells those two apart --
it answers 200 with an empty file list for the second -- so that is the check that
actually settles it:

```
200  https://pypi.org/simple/model-migration-kit/
200  https://test.pypi.org/simple/model-migration-kit/
404  https://pypi.org/simple/migkit/            # the console script is not a distribution
404  https://test.pypi.org/simple/migkit/
```

Installing from a checkout is still the path for the commands further down that
need the repository:

```bash
python -m venv .venv
.venv/bin/python -m pip install .      # Windows: .venv\Scripts\python.exe -m pip install .
.venv/bin/migkit demo
```

That is the path the quickstart above exercised — a built wheel in a fresh
virtualenv, not an editable install. An editable install leaves the repo root
importable and would hide a packaging mistake that breaks every real user.

Requires Python 3.10 or newer. Runtime dependencies are `opik-rigor`, `jinja2`,
`rich`, and `tomli` on 3.10 only.

### Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests
```

The suite is green with no credentials and no network. On the machine this file
was written on (Python 3.14.4, Windows):

```
$ python -m pytest
1101 passed in 38.34s
```

The CI workflow additionally runs 3.10 through 3.13 on Ubuntu and Windows, builds a
wheel, installs *that wheel*, and asserts `migkit demo` exits exactly 1 with a
report containing no external references. No claim is made here about the state of
any particular CI run — the workflow is `.github/workflows/ci.yml` and it says what
it checks.

---

## Built on opik-rigor

Every statistical primitive is imported from
[opik-rigor](https://pypi.org/project/opik-rigor/), none reimplemented: Wilson
intervals and the pass-rate gate, Mann-Whitney U for regressions, pinned judges
with hashed rubrics, and the append-only evidence log that every report is rendered
from. model-migration-kit consumes the published wheel from PyPI rather than a path
dependency on a sibling checkout, so "first real consumer" means something — the
install transcript in the quickstart shows `opik-rigor-0.2.0` arriving from the
index.

Exactly what is depended on, how each fact about it was verified, and what a rigor
release could change that would break this consumer are recorded in
[COMPATIBILITY.md](https://github.com/ericwehmeyer/model-migration-kit/blob/main/COMPATIBILITY.md) — including a section listing what is *not*
verified, so that unverified claims do not borrow credibility from the verified
ones sitting next to them.

Four decisions that are easy to get wrong. The first three were made in
[docs/build-plan.md](https://github.com/ericwehmeyer/model-migration-kit/blob/main/docs/build-plan.md) §6; the fourth is a property of the tool
rather than a recorded decision, and is stated here because it is the one a reader
is most likely to assume the other way:

- **A failed completion is graded at the judge's minimum score, never dropped.**
  Otherwise a candidate that times out on two items beats one that answers those
  two items badly, and the tool prefers a model that crashes to one that answers
  poorly.
- **Regression tests across multiple judges are Holm-Bonferroni corrected.**
  Uncorrected, two *identical* models produce a false NO-GO about one run in eleven
  at four judges.
- **An item flips only when it crosses a margin** — passing at ≥80% of its draws,
  failing at ≤20% — and items in between are named as *unstable* rather than
  counted as flips. Majority-vote flipping manufactures flips out of coin tosses.
- **Latency is descriptive only and never a gate**, and the table says so, which is
  what stops it becoming one by habit.

---

## What v0.1 is not

One comparison, one report. No trend history. No cost model — opik-rigor's adapter
seam exposes no token usage, so the report cannot say what a verdict cost. No
dashboard, and no claim about any item outside your golden set.

## License

Apache-2.0 — see [LICENSE](https://github.com/ericwehmeyer/model-migration-kit/blob/main/LICENSE) and [NOTICE](https://github.com/ericwehmeyer/model-migration-kit/blob/main/NOTICE).
