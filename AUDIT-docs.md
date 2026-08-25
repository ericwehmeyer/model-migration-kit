# The documents around the product — `README.md` and the plan

Two audits that were **run and finished on 2026-08-24 but never landed**. They were
summarised in four sentences in `AUDIT-HANDOFF-macbook.md` §5 and nowhere else; the
full text, with every command and its output, sat only in the session scratchpad.
Recovered from that scratchpad on **2026-08-25 05:35Z** and landed **verbatim** —
not re-summarised, because the value of a finding on this project is its output, and
a paraphrase of an output is not one.

Both carry adversarial verdicts inline (**SURVIVES / WEAKENED / REFUTED**), per the
protocol.

**Read the coverage caveat on JOB-13 before quoting it, and note that it is now
measured rather than estimated.** This checkout holds **R1–R33 only**. R34, R38 and
R39 are cited in `JOBS.md` but live on the Windows side, so a finding here that a
ruling was never propagated may have been propagated in a revision this machine
cannot see. As of `origin/main` at `f887b31`:

```
$ git diff --stat HEAD origin/main -- docs/superpowers/plans/2026-08-21-migkit-report-plan.md
 .../plans/2026-08-21-migkit-report-plan.md | 984 +++++++++++++++++++++
 1 file changed, 984 insertions(+)

$ git diff --stat HEAD origin/main -- README.md
(no output — identical)
```

So **984 lines of plan were invisible to JOB-13**, and every one of them is an
addition — which is the direction that closes a stale-contract finding, not the
direction that opens one. Treat each SURVIVES below as *"survives against R1–R33"*
until someone re-runs it on the Windows side. **JOB-10 needs no such caveat:
`README.md` is byte-identical to `main`'s**, so its findings are current.

---

# JOB-10 — the new-user journey through `README.md`

**Machine:** MacBook (Darwin 25.5.0, arm64) · **Date:** 2026-08-24
**Method:** started from an empty directory and a bare interpreter; typed only what
`README.md` says to type, in the order it says it; recorded the point at which each
step broke. Four throwaway venvs, all under `$SP/job10-journey/`. Nothing in `src/`
or `tests/` was touched; nothing was installed into the project venv.

Every claim below carries the command, the verbatim output, and the README line.
Adversarial verdict is inline at each finding.

**Interpreters on this machine** (`which -a python python3`):

```
/Users/ericw/opt/anaconda3/bin/python      Python 3.9.13   <- bare `python`
/Users/ericw/opt/anaconda3/bin/python3     Python 3.9.13   <- bare `python3`
/opt/homebrew/bin/python3                  Python 3.12.5
/usr/bin/python3                           Python 3.9.6
```

Attempt 1 used bare `python` (what the README literally says). Attempts 2–4 used
`/opt/homebrew/bin/python3` (3.12.5), which is the first interpreter on this box
that satisfies `requires-python = ">=3.10"`.

I installed **from PyPI** where the README says PyPI (README:554), and from a
**local clone** and an **unpacked sdist** where the README says a checkout
(README:575) — PyPI was reachable throughout, so no substitution was needed.

---

## 1. `pip install model-migration-kit` fails on this machine's default Python, and says the package does not exist — SURVIVES

README:553-556 is the canonical from-nothing path:

```bash
python -m pip install model-migration-kit
migkit demo
```

README:574 opens the other install path with the same bare interpreter:

```bash
python -m venv .venv
```

Followed literally:

```
$ python -m venv .venv
exit: 0
$ .venv/bin/python -V
Python 3.9.13
$ .venv/bin/python -m pip install model-migration-kit
ERROR: Could not find a version that satisfies the requirement model-migration-kit (from versions: none)
ERROR: No matching distribution found for model-migration-kit
WARNING: You are using pip version 22.0.4; however, version 26.0.1 is available.
exit: 1
```

`python -m venv` succeeds silently on 3.9, so nothing between the README's first
command and this error mentions a version. The message a new user reads is
*"from versions: none"* / *"No matching distribution found"* — the wording pip uses
for a name that is not on the index. Twelve lines earlier the README says
**"Published on PyPI. Verified on 2026-08-21."** A reasonable person concludes the
README is wrong and stops.

It is not wrong. The package is there, and every artifact declares `>=3.10`:

```
$ curl -s https://pypi.org/simple/model-migration-kit/
... model_migration_kit-0.1.0-py3-none-any.whl  data-requires-python="&gt;=3.10"
... model_migration_kit-0.1.0.tar.gz            data-requires-python="&gt;=3.10"
... model_migration_kit-0.1.1-py3-none-any.whl  data-requires-python="&gt;=3.10"
... model_migration_kit-0.1.1.tar.gz            data-requires-python="&gt;=3.10"
```

The README states the requirement at **line 583** — *29 lines after* the install
command it breaks, and 536 lines after the Quickstart — and in the badge at line 4.

**What rescues it, partially:** the venv on 3.9 ships pip 22.0.4, whose diagnostic
is useless. A user who takes pip's own upgrade advice gets a good one:

```
$ .venv/bin/python -m pip install --upgrade pip     # pip 26.0.1
$ .venv/bin/python -m pip install model-migration-kit
ERROR: Ignored the following versions that require a different python version: 0.1.0 Requires-Python >=3.10; 0.1.1 Requires-Python >=3.10
ERROR: Could not find a version that satisfies the requirement model-migration-kit (from versions: none)
```

**Verdict: SURVIVES.** Would a real user hit it? Two of the three interpreters on a
Mac with Anaconda installed are 3.9 — including `/usr/bin/python3` (3.9.6), which is
not Anaconda's doing. Anaconda is the default environment for exactly the LLM-eval
audience this tool is written for. The README never says "check your version first"
and never pins an interpreter in a command. This is the single most likely place a
new user gives up, and they give up believing the wrong thing.

---

## 2. The real-model config at README:433-447 cannot be loaded as printed — SURVIVES

README:431 — *"Write a config naming the judges and the thresholds:"* — then a TOML
block whose fifth line is `rubric  = "rubric.md"`. The README never tells the reader
to create `rubric.md`, never shows its contents, and never says what a rubric is.

Typed exactly as printed, into an otherwise-empty directory, then run through the
README's own verification command (README:464):

```
$ python -c "from model_migration_kit.judging import JudgeConfig; c = JudgeConfig.load('migkit.toml'); print(c.specs[0]); print(c.thresholds)"
Traceback (most recent call last):
  ...
model_migration_kit.errors.ConfigError: migkit.toml: judge 'accuracy' has no rubric at rubric.md
exit: 1
```

README:460-467 presents that same command succeeding, so the reader has no reason to
expect a failure. The missing file is identifiable — it is `tests/fixtures/rubric.md`,
and the hash proves it:

```
$ shasum -a 256 tests/fixtures/rubric.md
cc39e4aad0ef5db821fb627bb1217bab78095543642634bc2d30581f642c6268
```

which is byte-for-byte the `rubric_hash` printed at README:465. It is also
byte-identical to `demo_rubric_path()` inside the wheel. So the config in the README
is real and the transcript under it is real; the file that joins them is absent from
the document. Supplying it makes the README's transcript reproduce exactly:

```
$ python -c "from model_migration_kit import demo_rubric_path; import shutil; shutil.copy(demo_rubric_path(),'rubric.md')"
$ python -c "from model_migration_kit.judging import JudgeConfig; c = JudgeConfig.load('migkit.toml'); print(c.specs[0]); print(c.thresholds)"
JudgeSpec(name='accuracy', model='claude-sonnet-4-5-20250929', rubric=PosixPath('rubric.md'), rubric_hash='cc39e4aad0ef5db821fb627bb1217bab78095543642634bc2d30581f642c6268', adapter='anthropic')
Thresholds(pass_rate_floor=0.9, alpha=0.05, confidence=0.95, judge_failure_tolerance=0.05, min_detectable_effect=0.1, power_target=0.8)
```

(identical to README:465-466 apart from `WindowsPath` → `PosixPath`.)

Two smaller things in the same block: the config is called `migkit.toml` at
README:229 and README:456 but `judges.toml` at README:464, and the reader is never
told which name to use; and nothing in the README says what a rubric file must
contain, so a user who invents one gets a judge grading against a rubric that shares
nothing with the tool's design intent.

**Verdict: SURVIVES.** This is the first command in the credentialed half of the
README and it cannot run as printed. A user with API keys — the paying audience —
hits it immediately.

---

## 3. The keyless path over your own golden set exists, works, and is nowhere in the README — SURVIVES

The tool has it:

```
$ migkit demo --help
usage: migkit demo [-h] [--out OUT] [--goldenset GOLDENSET] [--n N] ...
  --goldenset GOLDENSET  run over your own golden set instead of the bundled
                         one; the rubric and the thresholds stay the bundled
                         ones, because the judge is scripted
```

The tool's own error text advertises it (from `migkit compare` on the shipped
fixtures, 0.1.1):

```
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". ... The keyless path over your own items is `migkit demo --goldenset <your set> --n <draws>`, ...
```

The README does not:

```
$ grep -n -- "--goldenset" README.md
452:migkit run --goldenset goldenset.jsonl --model <baseline-id>  --adapter anthropic --n 20
453:migkit run --goldenset goldenset.jsonl --model <candidate-id> --adapter anthropic --n 20
483:$ migkit run --goldenset goldenset.jsonl --model fake-wire-v1 --adapter fake --n 2
499:$ migkit run --goldenset goldenset.jsonl --model fake-wire-v1 --adapter fake --n 2
538:$ migkit run --goldenset dupes.jsonl --model fake-wire-v1 --adapter fake --n 1
$ grep -n -i "your own golden" README.md
(no match)
```

Every one of those is `migkit run`, which README:222-224 correctly says produces no
verdict. So the README's story for a keyless reader is: the bundled twelve items get
you a verdict; your own items get you an artifact and then a wall, because
`compare` needs a credential (README:287, 341). README:260-262 reinforces it —
*"From a plain wheel install, `migkit demo` is the equivalent that runs"* — with
`migkit demo` meaning, to a README-only reader, the bundled twelve.

It works fine when you find it. Over the 6-item set I wrote from README:513-515:

```
$ migkit demo --goldenset mine.jsonl --out mine-report.html
migkit: demo: 6 items x n=5, no credentials, no network
...
│ golden-set size                    6                                          │
│ passed / observed  │ 25 / 30  │ 25 / 30  │
warning: judge 'accuracy': 30 completions per side cannot detect a 10% drop at
80% power; roughly 207 are needed.
VERDICT: NO-GO (exit 1)
exit: 1
```

**Verdict: SURVIVES.** The wrong belief is specific and consequential: *"I cannot
try this on my own data without buying API calls."* That is the belief that decides
whether someone evaluates the tool at all, and it is false.

Secondary note, **WEAKENED**: that run returns `NO-GO ... rule 2` with baseline and
candidate at *identical* pass rates (25/30 vs 25/30, 83.3% vs 83.3%) and a flip list
naming the reader's real item ids (`open-01 5/5 -> 0/5`). The red band
("FAKE MODELS - these numbers describe scripted responses, not a real provider")
and the `--help` text ("measures your set, not your models") both disclose it. I
mark this WEAKENED because the disclosure is present and correct — but the README,
which is where the framing would live, says nothing at all, so the banner is the
only thing standing between the reader and a NO-GO about their own item ids.

---

## 4. The real-model path needs an install step the README never gives — SURVIVES

From a plain `pip install model-migration-kit`, the README:452 command:

```
$ migkit run --goldenset mine.jsonl --model claude-sonnet-4-5-20250929 --adapter anthropic --n 20
migkit: ConfigError: adapter 'anthropic' needs the 'anthropic' package, which is not installed. It is an optional dependency because the keyless paths -- `migkit demo` and `--adapter fake` -- do not need it. Install it with: pip install "model-migration-kit[anthropic]"  (or: pip install anthropic)
exit: 3
```

The distribution has three extras:

```
$ python -c "from importlib.metadata import metadata; print(metadata('model-migration-kit').get_all('Provides-Extra'))"
['anthropic', 'dev', 'openai']
```

README:583-584 lists the dependencies — *"Runtime dependencies are `opik-rigor`,
`jinja2`, `rich`, and `tomli` on 3.10 only"* — and does not mention extras. The only
place the word appears in the whole file is inside a transcript at README:336, which
gives the bare form `pip install anthropic` rather than the extra.

**Verdict: SURVIVES as a missing step (category 4), not as a blocker.** The runtime
error is one of the best in the tool: it names the exact command, explains why the
dependency is optional, and offers both forms. A user recovers in one step. But they
recover from the tool, not from the README, and the README's "Real models" section
reads as though `pip install model-migration-kit` is sufficient for it.

Same section, related: `--adapter openai-compat` is a real value —

```
$ migkit run --help
  --adapter {fake,anthropic,openai-compat}
```

— and the string `openai-compat` appears **nowhere** in `README.md`. The README
discusses `OPENAI_API_KEY` (line 425) and "both provider adapters" (line 318) but
never names the flag value, so an OpenAI user cannot get from the README to a
working command.

---

## 5. README:332-338 shows a bogus API key producing a harmless local error; it actually dials out — WEAKENED

README:328-330 says, correctly: *"supplying one does not help either, because the
judge then does what a judge does — it starts spending the credential on the
fixtures"*. The transcript pasted underneath shows something else:

```
README:332-338
$ ANTHROPIC_API_KEY=not-a-real-key migkit compare --baseline tests/fixtures/go-a.jsonl \
      --candidate tests/fixtures/go-b.jsonl --judges $TMP/judges-anthropic.toml
migkit: judging with accuracy
migkit: grading fixture-go-baseline-v1
migkit: AdapterError: AnthropicAdapter needs the 'anthropic' package, which is not installed. Install it with: pip install anthropic
exit: 3
```

Reproduced with the `anthropic` extra installed, over my own artifacts and the
README's config:

```
$ ANTHROPIC_API_KEY=not-a-real-key migkit compare --baseline .migkit/my-baseline-v1__861fdaa188373726.jsonl \
      --candidate .migkit/my-candidate-v1__861fdaa188373726.jsonl --judges migkit.toml
migkit: judging with accuracy at concurrency 4
migkit: grading my-baseline-v1
migkit: AdapterError: anthropic call failed for model 'claude-sonnet-4-5-20250929': AuthenticationError: Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error', 'message': 'invalid x-api-key'}, 'request_id': 'req_011CeNjQLDAMtMDPKgUtrAEX'}
exit: 3
```

That is a live HTTPS request to the provider, at concurrency 4. The README's
transcript was captured on a machine where the `anthropic` package was absent, so it
records a dependency error and not the behaviour its own prose describes.

**Verdict: WEAKENED.** The prose immediately above the transcript states the true
behaviour, and a reader who reads the sentence is warned. The observed failure is
free (a 401, not a bill). I mark it WEAKENED rather than REFUTED because the
transcript is the part readers of this README are trained to trust — line 18 says
*"Every command and every block of output below was executed, and the output is
pasted rather than described"* — and here the pasted output contradicts the sentence
above it.

Note the paired case, which is the *good* half and reproduces exactly (see §"What
holds", item 7): with no key at all, README:321-326's `AdapterError` about
`ANTHROPIC_API_KEY` comes out character for character.

---

## 6. The Quickstart cannot be executed by the reader it is aimed at — WEAKENED

README:47-59, the first actionable section in the file:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install <checkout>
.venv\Scripts\migkit.exe demo
```

Three problems for a new reader, in order of arrival: the paths are Windows-only;
`<checkout>` is an undefined placeholder; and the README contains no `git clone`
command anywhere —

```
$ grep -n -i "clone" README.md
258:(`demo_goldenset_path()` and its two siblings) and nothing else. A clone works, and
$ grep -n "checkout" README.md
53:wheel from a checkout:
57:.venv\Scripts\python.exe -m pip install <checkout>
570:Installing from a checkout is still the path for the commands further down that
618:dependency on a sibling checkout, so "first real consumer" means something -- the
```

The working, copy-pasteable path is at README:573-577, five hundred lines below.

**Verdict: WEAKENED.** README:52-53 explicitly frames the block as a record of what
was executed — *"Executed on Windows 11, Python 3.14.4, in a fresh virtualenv,
installing the built wheel from a checkout"* — not as instructions, and the Install
section does deliver a runnable path. It is a placement problem, not a false claim.
It stays on the list because "Quickstart" is a promise about where to start, and a
user who starts there types a command with a literal `<checkout>` in it.

When the intended block *is* used, it works. From a fresh clone of `main` (`4c55f74`)
on Python 3.12.5:

```
$ /opt/homebrew/bin/python3 -m venv .venv
$ .venv/bin/python -m pip install .
Successfully installed ... model-migration-kit-0.1.1 ...
$ .venv/bin/migkit demo
... VERDICT: NO-GO (exit 1)
exit: 1   wall: 13.98 s
```

---

## 7. README:483, the "check the wiring before you spend anything" command, uses a file the reader has never been given — WEAKENED

README:476-493 is the section a cautious user runs first. Typed verbatim into a
fresh directory:

```
$ migkit run --goldenset goldenset.jsonl --model fake-wire-v1 --adapter fake --n 2
migkit: GoldenSetError: cannot read golden set goldenset.jsonl: [Errno 2] No such file or directory: 'goldenset.jsonl'
exit: 3
```

The transcript at README:484-490 lists `arith-01 … refuse-04` and the artifact
`fake-wire-v1__5fef50364057cad8.jsonl` — `5fef5036…` being the *bundled demo set's*
hash (README:531). So the author copied the demo golden set out to
`goldenset.jsonl`. The README never says to, and the only pointer to
`demo_goldenset_path()` is 46 lines further on, in a different section.

**Verdict: WEAKENED.** Exit code is correct, the error is clear, and a user who has
read to line 529 can work it out. But this is the one command in the file whose
whole purpose is "run this before you spend money", and it is unrunnable as printed.

---

## 8. Outputs that differ from what the README shows — SURVIVES (as a set)

Each was produced this session on `main` `4c55f74` / PyPI `0.1.1`.

**8a. README:301 — the fake-judge refusal text has been rewritten.** Exit codes still
match README:302-308 (3/3/3/3), but:

```
README:301
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.

actual (0.1.1)
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Judging is a credentialed verb for that reason. The keyless path over your own items is `migkit demo --goldenset <your set> --n <draws>`, which scripts both models as well as the judge and bands the report accordingly -- it tells you whether your set and your n are big enough to decide anything, not whether your candidate model is good.
```

This is the same omission as §3, from the other side: the shipped error message
documents `--goldenset` and the README's copy of that message predates it.

**8b. README:336 — different exception class and different install advice.**
Shown: `AdapterError: AnthropicAdapter needs the 'anthropic' package … pip install anthropic`.
Actual from a plain wheel install: `ConfigError: adapter 'anthropic' needs the 'anthropic' package … pip install "model-migration-kit[anthropic]"`.

**8c. README:484-489 — `migkit run` now emits a concurrency line the transcript
does not have.** Mine, on a 6-item set:

```
migkit: 6 items x n=2 (--n) against fake-wire-v1 via FakeAdapter
migkit: concurrency 4 exceeds n=2; the sampling pool spans one item's draws, so the effective width is 2
migkit: [1/6] sum-01: 2 draw(s)
```

`grep -n -i "concurren" README.md` → no match. The word appears in `migkit run --help`
(`--concurrency  threads within one item`) and in `migkit compare`'s progress output
(`judging with accuracy at concurrency 4`), but a README reader meets it first as an
unexplained warning about a flag they have never seen.

**8d. Version drift in the install transcript.** README:64 shows
`model-migration-kit-0.1.0`, `pygments-2.20.0`, `scipy-1.18.0`. Actual PyPI install
today: `model-migration-kit-0.1.1`, `pygments-2.21.0`, `scipy-1.18.1`. Cosmetic.

**8e. README:600 — `1101 passed in 38.34s`.** Actual, from the 0.1.1 sdist with
`pip install -e ".[dev]"` on 3.12.5: `1107 passed in 11.11s`. Six more tests.

**8f. Box-drawing style.** README:81-167 renders tables as `┌─┐ ├┼┤ └┘`; every
non-legacy-Windows terminal gets `╭─╮` and `┏━┓ ┡╇┩` from the same `rich-15.0.0`.
Purely visual, but it is the first thing a user compares.

**8g. README:224 — "`migkit --help` says so too, on the `run` row."** The row says
`run  sample one model over a golden set (produces no verdict; exits 0 or 3)`. The
sentence about gating on nothing is in the epilog, not the row. **WEAKENED /
pedantic** — the substance ("produces no verdict") is on the row.

**Verdict on 8 as a set: SURVIVES**, at low individual severity. 8a and 8c are the
ones a user actually meets; the rest are drift a user would shrug at. None of them
would make anyone give up.

---

## 9. Small omissions in the golden-set format — WEAKENED

README:513-514: *"`id` and `input` are required; `reference` and `tags` are
optional."* The loader also accepts `metadata`, which the README never mentions:

```
$ migkit run --goldenset t.jsonl --model fake-wire-v1 --adapter fake --n 1
migkit: GoldenSetError: t.jsonl line 1: unknown key(s) 'difficulty'. Allowed: id, input, metadata, reference, tags. Put anything else under 'metadata', where it survives without being silently ignored.
exit: 3
```

**Verdict: WEAKENED.** The error names the allowed set and tells you what to do. A
user is corrected in one run. Recorded only because the README's format section is
otherwise complete enough to write against (see below), and this is its one gap.

---

## What holds — checked, with the command

This README has had real work put into it, and most of it survives contact.

1. **`migkit demo` from a plain PyPI wheel reproduces README:81-167 exactly.**
   `pip install model-migration-kit` on Python 3.12.5, then `migkit demo`. Diffed
   after normalising box characters and wrap width: **every difference is a
   filesystem path or a line-wrap column.** Every number is identical —
   `55 / 60`, `45 / 60`, `91.7%`, `75.0%`, `[0.8193, 0.9639]`, `[0.6277, 0.8422]`,
   `0.8385`, `0.6486`, `11 / 1 / 0`, `9 / 3 / 0`, `0.007843 (0.050)`,
   `mann-whitney-u`, `yes / no / no`, the three flips
   (`extract-01`, `refuse-02`, `refuse-04`, each `5/5 -> 0/5`), the one gain
   (`extract-03  0/5 -> 5/5`), zero unstable, `5,821 characters` against
   `10,000,000`, `roughly 140 are needed`, `VERDICT: NO-GO (exit 1)` twice, and the
   report path as the last line before the verdict. Golden-set hash
   `5fef50364057cad8`, judges hash `bb624f0ed1781d85`, config hash
   `1ad89c46dcbd426d` all match.
2. **Exit 1 is the demo working (README:170-177).** Confirmed: `exit: 1` from both
   the PyPI install and the clone install.
3. **Timing (README:183-186).** Claimed 20.69 s cold / 2.11 s warm. Measured here
   **15.57 s cold / 0.93 s warm** (PyPI venv) and **13.98 s cold** (clone venv). The
   README's figures are not exceeded.
4. **The HTML report fetches nothing (README:193-201).**
   `python -c "...print(external_urls(Path('migkit-demo-report.html').read_text(encoding='utf-8')))"` → `()`, character for character.
5. **README:203 structure.** `<script>` elements: 0. `<link>` elements: 0.
   CRLF count: 0, LF count: 512 — LF-terminated as claimed. Size 26,096 bytes vs the
   README's 25,931; the delta is the embedded absolute paths, and the README states
   25,931 as a fact about that one run, not a promise.
6. **The four-verdict fixture matrix (README:267-275) reproduces character for
   character — from an unpacked sdist, exactly as README:259-260 promises.**
   ```
   $ python tests/fixtures/make_fixtures.py --check
   11 committed fixture files are byte-identical to a rebuild
   go     -> GO      exit 0  ok  (rule 5: ...)
   nogo   -> NO-GO   exit 1  ok  (rule 1: ...)
   review -> REVIEW  exit 2  ok  (rule 4: ...)
   migkit: ArtifactError: the golden set at tests/fixtures/goldenset.jsonl has changed since fixture-error-baseline-v1 was run (84d623332ed60ad5 now, b3c2853a494d3472 then). ...
   error  -> ERROR   exit 3  ok  (migkit compare refused the pair)
   ```
   And the sdist claim itself: `tar tzf model_migration_kit-0.1.1.tar.gz | grep -c "tests/fixtures/[^/]*$"` → **12**, including `make_fixtures.py` and `rubric.md`.
7. **README:321-326, the credential error, character for character:**
   ```
   $ ANTHROPIC_API_KEY= migkit compare --baseline ... --candidate ... --judges migkit.toml
   migkit: AdapterError: AnthropicAdapter needs the ANTHROPIC_API_KEY environment variable. Credentials are read from the environment only -- they are never accepted as constructor arguments -- so export ANTHROPIC_API_KEY before constructing the adapter.
   exit: 3
   ```
8. **README:347-350.** `pytest tests/test_cli.py -k "TestExitCodeContract"` →
   `14 passed, 64 deselected` (13.41 s here vs 0.84 s there; counts identical).
9. **README:355-359, exit 3 on a missing evidence log** — character for character,
   `exit: 3`.
10. **README:385-386.** `required_sample_size` → `[67, 109, 155, 229]`. Exact.
11. **README:466 thresholds** — `Thresholds(pass_rate_floor=0.9, alpha=0.05, confidence=0.95, judge_failure_tolerance=0.05, min_detectable_effect=0.1, power_target=0.8)`, exact.
12. **README:469-471, the pin rule.** `claude-3-5-sonnet-latest` is refused at config
    load with a `ConfigError` that names the alias token and lists the accepted
    designator forms. Exactly as described, and better than described.
13. **README:495-508, resume.** Running the identical `migkit run` twice re-samples
    nothing — second run prints only the summary and the same artifact path, `exit: 0`.
14. **README:529-531, golden-set stats and hash** — character for character,
    including the full 64-hex hash.
15. **README:538-542, duplicate-id validation** — character for character, `exit: 3`.
16. **README:592, `ruff check src tests`** → `All checks passed!`, exit 0.
17. **README:222-224, the `run` exception.** `migkit --help` does carry it, in the
    epilog: *"`run` produces no verdict, so its 0 means the run completed and never
    means GO -- a pipeline that gates on `run` gates on nothing."*
18. **The golden-set format description (README:512-521) is sufficient to write a
    valid golden set from scratch.** This was an open question; the answer is yes.
    I wrote a 6-item set using only lines 513-515 and the three sample lines —
    mixing items with and without `reference`, with and without `tags`, and one with
    two tags — and it loaded first time, with no revisions, through both
    `migkit run` and `migkit demo --goldenset`:
    ```
    $ migkit run --goldenset mine.jsonl --model fake-wire-v1 --adapter fake --n 2
    migkit: 6 items x n=2 (--n) against fake-wire-v1 via FakeAdapter
    migkit: [1/6] sum-01: 2 draw(s) ... migkit: [6/6] refuse-01: 2 draw(s)
    migkit: 12 completion(s), 0 failed, 1 part(s)
    exit: 0
    ```
    The four plausible mistakes I then tried on purpose — an unknown key, `tags` as a
    string, a missing `input`, a JSON array instead of one object per line — each
    produced a specific, line-numbered, actionable `GoldenSetError` and `exit: 3`.
    A trailing blank line is tolerated. A single-item set is accepted.

---

## Reproduction

```
$SP/job10-journey/
  attempt1/            venv on bare `python` (3.9.13) — the failure in §1
  attempt2/            venv on 3.12.5, `pip install model-migration-kit` from PyPI
    run1/              `migkit demo` + HTML checks
    wire/              README:483, and mine.jsonl written from README:513-515
    realpath/          README:433-447 config, rubric recovery, compare without/with a key
  sdist/               `pip download --no-binary :all:`; unpacked 0.1.1 sdist
    model_migration_kit-0.1.1/   make_fixtures.py --check, [dev] venv, pytest, ruff
  attempt4/checkout/   git clone of main (4c55f74), README:573-577 install
  gs/                  the golden-set format probes
```

Nothing outside `$SP/job10-journey/` was written to. `src/` and `tests/` were not
modified; the project venv was not touched. Nothing was fixed.


---

# JOB-13 — `2026-08-21-migkit-report-plan.md` audited as a contract

**Target:** `docs/superpowers/plans/2026-08-21-migkit-report-plan.md`, 5,869 lines / 307,344 bytes,
at `0c30686` on `review/2026-08-24`. **Read-only. Nothing changed.**

**Coverage note first.** This checkout's plan accretes **R1 … R33**, not R1…R39.
`grep -n "R3[4-9]" ` returns **zero hits**. R34.3, R38 and R39 are cited in `JOBS.md`
as existing, so they live on the Windows side and have not reached this branch.
Everything below is an audit of R1–R33 plus §§1–10 and C1–C22b. Nine findings, all
with both quotes and their line numbers, each with an inline adversarial verdict.

Line numbers marked `plan:NNNN` are the plan; `file.py:NNNN` is the tree.

---

## F1 — C16 still requires the stateful callable adapter that R13.2 removed, and still leaves its own file location open. No banner. **SURVIVES**

`plan:1578`, inside C16's Contract, unmarked:

> - one earlier night (say 6) puts candidate C into REVIEW — its interval straddles
>   the floor. **This requires per-draw variation, hence the callable form.**

`plan:1592-1594`, C16's Reviewer note, tells the reviewer to police the hazard the
sentence above creates:

> **Reviewer.** The per-draw counter is state, and state plus a thread pool is a
> flake.

And `plan:1498-1505`, Phase 5's "Four facts, established by reading and by running
the demo", states it as *fact*:

> A callable holding a private per-prompt counter gives per-draw variation, which is
> what any interval that straddles a floor requires. **This is the only way to seed a
> REVIEW**, and the spec does not mention it.

Against `plan:3118-3131`:

> **R13.2 — the callable form is not required, and the contract's reason for it is
> already disproven.** … *"A genuine REVIEW is seedable with a plain `Mapping`
> FakeAdapter — no callable, no per-draw variation."* … The contract creates the
> hazard in its Contract section and then asks the reviewer to police it — when the
> hazard is not needed at all. **Default to a plain `Mapping`. No counter, no state,
> and the flake the reviewer was told to hunt cannot exist.**

Second half of the same defect, `plan:1556`:

> **Files.** New `src/model_migration_kit/showcase.py`, **or** `scripts/`.

Against `plan:3103-3104`: **"R13.1 — the file is `scripts/showcase.py`."**

**What the code implements.** R13, not C16. `scripts/showcase.py:535-540` refuses any
other concurrency with the reason *"The scripted models are **stateless mappings**
and cannot be…"*, and `scripts/showcase.py:787-789` is `showcase_adapters(...) ->
tuple[FakeAdapter, tuple[FakeAdapter, ...]]` built from mappings. There is no
`src/model_migration_kit/showcase.py`.

**Verdict: SURVIVES.** C16 is the *only* contract in Phase 5 with no amendment
banner — C17, immediately below it at `plan:1599`, carries one. C4, C5, C6, C7, C10,
C11 and C14 all carry one. An agent handed C16 and told "read your contract" builds a
stateful adapter and puts it in `src/`, and its reviewer is told by the same contract
to hunt the flake rather than to refuse the design. The build was correct only because
the orchestrator carried R13 into the brief by hand — which is exactly the failure
mode R28.1 names ("a ruling in the plan with no brief behind it"), running in reverse.

**Rank: 1.** JOB-4 is auditing the showcase right now against this contract.

---

## F2 — C13's signature is still `-> str`; R6 replaced it with a `Timeline` NamedTuple. No banner. **SURVIVES**

`plan:1349-1352`, C13's Contract, verbatim and unmarked:

```python
def timeline_svg(
    points: Sequence[RunPoint], *, width: int = 900, height: int = 260,
) -> str: ...
```

`plan:2118-2141`, R6:

> The contract declares `timeline_svg(...) -> str` and then says, two paragraphs
> later, that "the count of such runs is returned to the caller"… **Dispatched as
> written this guarantees a mismatch: the implementer picks one reading, the tester
> picks the other, and neither is wrong.**
>
> ```python
> class Timeline(NamedTuple):
>     svg: str
>     runs_without_floor: int
>     runs_without_rate: int
> ```

**What the code implements.** R6. `report.py:4977-5003` defines `class Timeline(NamedTuple)`
with `runs_without_floor` and `runs_without_rate`; `report.py:5101` returns it.

**Verdict: SURVIVES.** C12 and C13 are the only two chunk contracts in the document
containing **zero** references to any ruling (`grep -o "R[0-9]"` over `plan:1271-1410`
returns nothing). R6's own text says dispatching C13 as written guarantees an
implementer/tester split — and C13 is still written that way.

**Rank: 2.**

---

## F3 — every one of the plan's five self-citations by line number is stale by ~89 lines, and three of them are "do not read that" instructions now pointing into a different chunk. **SURVIVES**

`scripts/check_contract.py` verifies `file.py:NN` citations only (its docstring,
`scripts/check_contract.py:1-30`: *"every `file.py:NN` in it is load bearing"*).
Nothing checks the plan's citations to its own sections. Measured:

| plan line | what it says | what is actually at that line | the real target |
|---|---|---|---|
| `plan:2473`, `plan:2475` | "This replaces the C8 section **at line 866** in full… **do not read line 866**." | `plan:866` is C7's `ParameterChange` dataclass field list | C8 starts at `plan:955` |
| `plan:2630` | "This replaces the C9 section **at line 923**." | `plan:923` is inside C7's first-run-marker amendment | C9 starts at `plan:1012` |
| `plan:2733`, `plan:2995` | "C10 **at line 989** stands" / "This replaces the C10 section **at line 989**" | `plan:989` is a row of **C8's** Edges table | C10 starts at `plan:1078` |
| `plan:2751` | "The C10 test named **at line 1050** must be rewritten to assert the opposite" | `plan:1050` is the `\| Input \| Required \|` header of **C9's** Edges table | C10's named test is at `plan:1131` |
| `plan:2419` | "The plan's justification for it (**line 1411**) says 16 items x 5 draws…" | `plan:1411` is `#### C14 — the template` | the quoted sentence is in C15, `plan:1536-1540` |

All five are off by the same ~89 lines — the amount the document grew above them.

**Verdict: SURVIVES.** An agent obeying `plan:2475` ("do not read line 866") skips
C7's dataclass and still reads the superseded C8. An agent sent to `plan:989` to
find C10's standing contract lands in C8's edge table. This is the brief's category 4
exactly, and it is unguarded: no gate in `scripts/` reads plan-internal citations.

**Rank: 3.** Cheapest to fix of anything here; convert them to anchors, not integers.

---

## F4 — C10 (restated)'s contract block ships two shapes the code does not have, one of them corrected only 2,000 lines later. **WEAKENED** (Mapping) / **SURVIVES** (tag order)

`plan:3026-3033`, C10 (restated)'s Contract:

```python
    tags: tuple[str, ...]           # golden-set tag order, UNTAGGED last
    baseline: Mapping[str, DimensionCell]
    candidates: Mapping[str, Mapping[str, DimensionCell]]  # model_id -> tag -> cell
```

**(a) `Mapping`.** Contradicted inside the same section, by its own banner at
`plan:2977-2983`:

> 2. **Do not ship `baseline` and `candidates` as `Mapping`s.** They reproduce
>    *exactly* the `column.items` hazard this contract's own Reviewer note tells the
>    reviewer to check for… Use `tuple[DimensionCell, ...]` … or a small frozen
>    `TagColumn`.

Code implements the banner: `report.py:1167` `baseline: TagColumn`, `report.py:1179`
`candidates: tuple[TagColumn, ...]`. **Verdict: WEAKENED** — the banner is 50 lines
above the block and unambiguous. Still worth striking, because the block is what a
reader copies.

**(b) `# golden-set tag order`.** Contradicted only at `plan:5044`, in R27.3:

> **The contract's phrase "golden-set tag order" becomes "alphabetical, `UNTAGGED`
> last."** No discriminating fixture is needed for file order, because a regression
> to it is unimplementable.

Nothing in C10's section says so. The stale phrase also survives at `plan:1090` (the
original C10) and `plan:3631` (R16.3). Code implements R27.3 —
`report.py:1158-1167`, whose own comment reads *"The contract said 'golden-set tag
order' and R27.3 corrected the phrase."* **Verdict: SURVIVES** — an implementer sorting
by golden-set file order writes code R31.1 later measured to be unreachable, and burns
the chunk finding that out.

**Rank: 4.**

---

## F5 — C12's four-row missing-value table is jointly unsatisfiable, and so is C13's empty case. Never adjudicated by any ruling; the implementer resolved it in a code comment. **SURVIVES**

`plan:1307-1308`, two adjacent rows of one table:

| Missing | Required |
|---|---|
| `floor is None` | no floor line, **and** an `<title>` saying the floor was not recorded — an absent rule must not read as a floor of zero |
| all three `None` | a single `<text>` element reading the em dash `—`, **and nothing else** |

The all-three-`None` case *is* a case where `floor is None`, so row 3 requires a
`<title>` that row 4 forbids. `plan:1310-1311` then requires it a third time,
unconditionally:

> Accessibility… the `<svg>` carries `role="img"` and a `<title>` whose text states
> the same numbers in words.

C13 carries the same shape at `plan:1377`: *"`points` empty | an `<svg>` containing a
single `<text>` saying no dated runs, **and nothing else**."*

**What the code implements.** Neither reading as written — the implementer adjudicated
it silently. `report.py:3719-3723`:

> ```
> # The title is unconditional -- it is the accessible name of a `role="img"`
> # element, and it is where the floor-was-never-recorded state is stated in
> # words. "Nothing else" in the all-None row of the contract is about drawn
> # elements; a document whose only picture had no accessible name would trade
> # one silent failure for another.
> ```

and `report.py:5250-5259` for C13: *"Every branch of this chart emits one, the empty
one included."*

**Verdict: SURVIVES.** This is a live blind-pair splitter of exactly the kind R6 and
R14 exist to catch, in the two chunks §7.2 calls the *most* blind-testable in the plan
(*"That is why the geometry is specified in the contract"*). It survived because both
implementers happened to read it the same way; a tester asserting `len(children) == 1`
on the all-None bar would have gone red against correct code. It is also, by
JOB-6's finding, the row that matters most: the floor's only surviving surface on some
paths *is* that `<title>`.

**Rank: 5.**

---

## F6 — R20.4's D7 is still stated as fact; R22.1 withdrew it. Only the later text is marked. **SURVIVES**

`plan:4159-4162`:

> - **D7 — `CandidateField.baseline_pass_rate` can never be `None`** from
>   `candidate_field`, since every rendered point passed `_ungraded`, which requires
>   `judged_baseline > 0`. **Say so, or C6 writes a dead branch.**

`plan:4451-4453`:

> **Ruling: D7 is withdrawn.** `baseline_pass_rate` is `None` when the baseline
> side's counts do not describe a rate. **C6 must handle `None`; the branch is not
> dead.**

**Verdict: SURVIVES, with a caveat.** This *is* the instance `CLAUDE.md` already
records ("one refused a ruling that a second ruling in the same brief had falsified"),
so the finding is not that it was missed — it is that **R20.4 carries no in-place
marker while three comparable cases do**: R23.1 has *"WRONG, superseded by R26"*
(`plan:4506`), R30.4 has *"PARTLY CORRECTED by R32.1"* (`plan:5436`), R14.2 has
*"SUPERSEDED by R18.4"* (`plan:3318`), R17.1 has *"CORRECTED — read R25"*
(`plan:3666`). An agent pointed at R20 — which R17.3 at `plan:3765-3768` explicitly
warns is *"a section an agent may be pointed at directly"* — reads D7 as live and
tells C6 to delete a reachable branch.

**Rank: 6.**

---

## F7 — C9 (restated) still carries two clauses R10 ruled wrong, and contains no reference to R10 at all. **SURVIVES**

`grep -o "R[0-9]"` over C9's restated section (`plan:2628-2730`) returns only **R4**
and **R9**. R10, R11.6 and R12 are invisible from inside it.

**(a)** `plan:2700`, C9's Edges:

> \| `n == 4`, `passes == 1` \| interval computed and shown, refused, **both floors
> named in `needed`/`needed_unit`** by the items rule above \|

`plan:2785-2788`:

> **R10.5 — the n=4 edge row is a slip.** That row says "both floors named in
> `needed`/`needed_unit`", **which those singular fields cannot express.**

**(b)** `plan:2656`, C9's `DimensionCell`:

```python
    note: str                   # the refusal sentence, "" when not refused
```

`plan:2766-2770`:

> **R10.1 — `note` is not only the refusal sentence.** The field comment says
> `# the refusal sentence, "" when not refused`; the edge table says a defaulted
> confidence must be recorded in `note`, "never silently"… **"Never silently" wins**…
> **The field comment is wrong.**

**Verdict: SURVIVES.** Both are named as defects by R10 and neither was struck from
C9. (b) is the sharper of the two: an implementer obeying the field comment leaves an
unrefused cell's defaulted-confidence disclosure unsaid, which is the "never silently"
rule this plan calls its own central one.

**Rank: 7.**

---

## F8 — C11's Contract still carries the sentence R18.1 struck. **WEAKENED**

`plan:1210-1213`:

> Unstable items are counted as **passing**… That choice is not arbitrary and must be
> in the docstring: it makes the spot check look *better* than it is, **so the tool
> never inflates its own case.**

`plan:3841`: **"Struck from C11's contract: 'so the tool never inflates its own case.'"**

**What the code implements.** R18.1/R18.2, correctly and at length —
`series.py:2429-2442` gives both halves, both probabilities, and names the 7%
direction. `grep -rn "never inflates" src/ tests/` returns nothing.

**Verdict: WEAKENED.** C11 carries a four-line banner at `plan:1166-1181` naming
R18.1 explicitly, and the shipped docstring is right. Reported only because C11's
Contract is the sentence an implementer copies into a docstring verbatim — the plan's
own instruction is *"must be in the docstring"* — and R18.1 calls this "the worst kind
of defect this project can produce." Compare C2 at `plan:475-486`, where the same
situation was fixed by **rewriting both halves** rather than by adding a banner.

**Rank: 8.**

---

## F9 — the shipped report names the same objects "comparison(s)" and "run(s)" two lines apart; R29.4 forbids the second. **SURVIVES** (a live rendering defect, not only a plan defect)

Rendered from `migkit demo` on this machine, `/tmp/j13demo/demo.txt:225-227`, verbatim
and consecutive:

```
Run history — 1 comparison(s) in this log
Candidate pass rate over 1 run(s); the horizontal axis is time.
```

Both numbers count `len(model.series)`, i.e. **points, i.e. comparisons**.
`report.py:4298` emits the first; `report.py:5266` emits the second
(`f"Candidate pass rate over {runs} run(s); …"`). Two more at `report.py:4318` and
`report.py:4322`: *"{{ n }} run(s) recorded no pass rate"*, *"{{ n }} run(s) recorded
no floor"*, both fed from `Timeline.runs_without_rate` / `runs_without_floor`
(`report.py:5048-5049`, `sum(1 for point in points …)`).

The plan says both things. `plan:2130` and `plan:2140`, R6:

> `runs_without_floor: int` … **Both counts are counts of points**, not of segments

`plan:5338`, R29.4:

> **Ruling: count comparisons, and say "comparisons".** **Never publish a run count
> this data cannot dedupe.** A precise-looking number that is wrong is worse here than
> a coarser one that is right, because the whole clause is about a disclosure a reader
> must be able to trust.

R29.4's own arithmetic, `plan:5327-5330`: in the showcase shape *"a night is 4 runs
but 3 points"* — so on the 14-night showcase the chart's accessible name will read
**"Candidate pass rate over 42 run(s)"** over **56 actual runs**, directly under a
heading that says *42 comparison(s)*.

**Verdict: SURVIVES.** R29.4's ruling text is unqualified and its stated reason
(*"`RunPoint` carries no run id or artifact path to dedupe by"*) applies verbatim to
`Timeline.runs_without_*` and to `_svg_title`. It was applied to the synthetic band
(`report.py:1006-1016` counts `scripted_comparisons` of `comparisons`, correctly) and
to the timeline **heading** — and not to the four sentences beside it. On the demo
(1 comparison, 1 run) the two numbers coincide, which is why nothing caught it.

**Rank: 9** as a plan defect, but it is the only finding here that is *already wrong
on the page*, and it will become visibly wrong the moment the showcase renders.

---

## F10 — R15.2 instructed "Amend the graph"; §6's graph was never amended, and is now nine chunks stale. **SURVIVES**

`plan:3468`: *"This makes C7 depend on C4, which §6's graph does not show. **Amend the
graph.**"*

`plan:1711-1721`, unchanged:

```
C1 ──► C2 ──► C3 ──┬──► C4 ──► C5 ──► C6
                   │                  │
                   │           C7 ────┤
```

C7 hangs off nothing. §2 at `plan:139` still says *"Six phases, **eighteen
chunks**"*; R31 at `plan:5503` says *"20 of **22** chunks"*. C19, C20, C21, C22, C22a,
C22b, C14a, C14b and C14c appear nowhere in the graph or the phase table.

**Verdict: SURVIVES**, low consequence — §6 is a scheduling aid, not a contract, and
R21.4/R23.3/R33 supply the real order. Recorded because R15.2 issued an instruction
that was never executed, which is R28.1's shape ("a ruling recorded is not a ruling
scheduled") applied to the plan's own text.

**Rank: 10.**

---

## F11 — R23.2 refused two differently-keyed exclusion lists; R30.4 and R33.2 put two on the page. **WEAKENED**

`plan:4548-4553`, R23.2:

> **That is wrong, and it is the `dimension_counts` mistake again.** … A second
> top-level partition would put the same facts on the model twice, computed by two
> calls that can drift apart… **Worse here than there, because the two partitions
> would be against possibly *different* keys, so the disagreement would be legitimate
> on both sides and impossible to adjudicate from the model.**
> **Ruling: the rendered excluded-runs list is the candidate field's own `excluded`.
> One partition, one source.**

`plan:5835`, R33.2's table for the new lineage block:

> \| `excluded` \| each `Exclusion`'s own sentence, unrewritten \| "3 runs excluded" is
> the count without the reason (R23.2's argument, one section over) \|

**Measured.** Two independent calls with different `against` keys are live:
`series.py:1280` inside `candidate_field` (`against=key`, the widest eligible group)
and `series.py:3099` inside `trend` (`against=comparability_key(anchor)`, the lineage
anchor). Both results are on `ReportModel` — `candidates.excluded` and
`trend.excluded` — and R33.2 schedules the second to render.

**Verdict: WEAKENED.** R23.2's literal ruling is not violated: `#excluded` still
draws from the candidate field. But R23.2's stated hazard is now realised by later
rulings that cite R23.2 in support, and after C14c the page will carry two exclusion
lists, from two keys, with nothing saying which key each was measured against. Worth
one sentence in C14c's brief; not worth a fix pass on its own.

**Rank: 11.**

---

## What I checked and did **not** find

Reported as a negative because a negative that names its coverage is the point.

- **Every `#### C` chunk contract read in full** (C1–C18, C19, C20, C14a, plus the
  restated C8/C9/C10 and C21/C22/C22a/C22b as described in R16/R21/R23/R30). The
  amendment-banner convention is applied to **C2, C4, C5, C6, C7, C10, C11, C14, C17**
  and is absent from **C9(restated), C12, C13, C16** — which is F1, F2, F5 and F7.
- **Every ruling R1 → R33 read in full.** The four explicit self-corrections
  (R8/C19, R14.2→R18.4, R17.1→R25, R23.1→R26, R30.4→R32.1) are each marked **in
  place** and are **not** defects. Only R20.4-D7 (F6) is unmarked.
- **R3 vs R9 vs R10 vs R11.6 on the two floors** — checked pairwise and they compose.
  R9 says explicitly *"R3 is amended, not reversed"* (`plan:2460`); R11.6's arithmetic
  (`MIN_N_FOR_A_VERDICT` can only bind at `n_per_item == 1`) is consistent with
  R10.5's both-bind example at n=4/items=4. **Not a contradiction.**
- **R21.5 vs R30.1 on the lineage.** R21.5's part 1 is conditional (*"Declared, **when
  the config declares it**"*) and R30.1 measures the condition to be never true today,
  then applies part 2. **Not a contradiction** — two compatible statements.
- **R23.2 vs R30.2 (`candidates` = `correct_field`'s field).** `correct_field` appends
  caveats and leaves `excluded` alone; one partition still feeds one list.
  **Not a contradiction.**
- **The `.py` citations.** Out of scope — `scripts/check_contract.py` covers them, and
  R11.3/R11.4/R12.1 already record the five that were wrong.
- **Terms with two meanings**, swept for *underpowered / powered / observed /
  completions / run / comparison / field / candidate / floor*:
  - **run / comparison** → F9, the one real hit.
  - **floor** carries two senses in one dataclass — `DimensionCell.floor` is a
    *pass-rate* threshold, while "the two floors" throughout R9/R10/C9 are
    *sample-size* minimums; `plan:2702` states the collision in one line (*"neither
    sample-size floor depends on the floor"*). But the plan disambiguates by adjective
    everywhere it matters and `verdict_refused` provably never reads the rate floor.
    **REFUTED as a contradiction**; recorded as a naming hazard only.
  - **observed** (10 hits) is used consistently for recorded values. **REFUTED.**
  - **powered / underpowered** appears three times, all inside §7.4/§7.5's single
    argument. **REFUTED.**
  - **field** and **candidate** are heavily overloaded (`CandidateField` vs dataclass
    field vs "the field that moved"; `Candidate` vs candidate model vs "candidate
    sources") but every instance I checked is disambiguated by its sentence.
    **REFUTED.**
- **Requirements stated twice with different numbers.** The two the plan already
  caught (R14.1's 0.351/34%/0.32877, R9's "20 items… you have 4") are corrected in
  place. I found **no third**: the showcase's 96/6/16, 42 comparisons, 56 runs, 26,880
  completions, `MIN_N=20`, `MIN_ITEMS=10`, `stale_after_days=7.0`, `k=12` are each
  stated consistently wherever they recur.

**Overall:** for a 5,869-line document accreted over ~10 sessions, the *rulings* are
in remarkably good order — the plan corrects itself in place four times out of five,
and I found **no pair of rulings that genuinely contradict each other with both
unmarked**. What it is bad at is **propagating a ruling back into the chunk contract
it overturns**: seven of the nine findings above are a live ruling and a stale contract
sitting 1,000–2,000 lines apart with nothing pointing between them. R17.3 diagnosed
this exactly (`plan:3765-3766`) — *"a correction lands in one place a reader might look and
not in another"* — and named the banner convention as the fix. The convention works;
it is simply not applied to C9, C12, C13 or C16.
