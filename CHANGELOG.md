# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-14

First release. `migkit` takes a golden set, runs two models against it *n* times
per item, grades every completion with the same pinned judges, and answers
whether the migration is safe with a verdict, a self-contained HTML report, and
an exit code. There is no earlier version.

The thing it is built to refuse is the usual method — a dozen prompts through the
new model, read the answers, ship — which cannot separate a real quality drop
from sampling noise, cannot separate a model that answers badly from one that
times out, and cannot separate "B is worse" from "we did not collect enough
evidence to know". It concludes something anyway, and what it concludes tends to
be whatever the reader already expected.

Every statistical primitive is imported from
[opik-rigor](https://pypi.org/project/opik-rigor/) and none is reimplemented —
Wilson intervals and the pass-rate gate, Mann-Whitney U for regressions, pinned
judges with hashed rubrics, and the append-only evidence log. What is verified
about that dependency, and how, is in [COMPATIBILITY.md](COMPATIBILITY.md).

### Added

- **`migkit demo`** runs the entire flow against a 12-item golden set bundled
  inside the wheel, using scripted `FakeAdapter`s. No API key, no network, no
  configuration, about two seconds. It exists so that the first thing a stranger
  sees is the tool working rather than a page of setup, and the report it writes
  carries a red band saying the models were fake — a demo that could be mistaken
  for a real result is worse than no demo.
- **`migkit run --goldenset ... --model ...`** samples one model over a golden
  set and writes an append-only artifact it can resume from. It produces no
  verdict, and its `--help` says so, because a `0` from `run` means "the run
  completed" and never means GO; a pipeline gating on `migkit run` is gating on
  nothing.
- **`migkit compare --baseline ... --candidate ... --judges ...`** grades both
  artifacts with one panel of judges built once, compares the distributions, and
  returns the verdict. There is no code path that constructs a second panel,
  which is what turns "the same instruments measured both models" from a claim
  in a README into a property of the program.
- **`migkit report <evidence>`** re-renders a report from an evidence log alone.
  `from_evidence` is the only constructor in the report module and it takes
  paths — no function there accepts a live comparison object. That is what makes
  "a crashed run still produces a partial report" a property rather than a
  promise: the partial-render path is exercised on every green run instead of
  only after a crash.
- **Exit codes `0`/`1`/`2`/`3` are the CI contract** — GO, NO-GO, REVIEW, error.
  They route only through `Verdict.exit_code`; there is no integer exit literal
  anywhere in `cli.py`, and a test reads the AST to keep it that way, because a
  second copy of the CI contract is a second thing to forget to update. Changing
  these is a breaking change under SemVer and the project treats it as one.
- **REVIEW is a real verdict, not a hedge.** A tool that must answer GO or NO-GO
  has to guess whenever the sample is too small to settle the question, and it
  will guess in whichever direction its author found comfortable. REVIEW is the
  refusal: this run could not have detected the regression you asked about, so
  "no regression detected" would be a question never asked, reported as
  answered. It is never silently converted to GO. NO-GO outranks it, because a
  regression that reached significance was, for that question, powered enough.
- **A failed completion is graded at the judge's minimum score and marked
  imputed, never skipped.** Skipping looks obviously correct — there is no output
  to grade — and simulation showed where it leads: a candidate that times out on
  two items and one that answers those two items badly post identical pass
  counts, and if the timeouts simply vanish from the score arrays the crasher
  wins the regression test outright. A tool that prefers a model which crashes to
  one which answers poorly is worse than no tool.
- **Regression tests across multiple judges are Holm-Bonferroni corrected.**
  Uncorrected, two *identical* models produce a false NO-GO about one run in
  eleven at four judges, which would have made the tool's alarms worth ignoring
  within a month of anyone using it.
- **An item flips only when it crosses a margin** — passing at ≥80% of its draws,
  failing at ≤20% — and items in between are named as **unstable** rather than
  counted as flips. Majority-vote flipping manufactures flips out of coin tosses,
  roughly five per run on the simulated case. The report prints three item counts
  and the completion rate, and deliberately no item-level rate: three states do
  not reduce to one fraction without smuggling the ambiguous items into a bucket,
  and whichever bucket you pick the number lies in that direction.
- **Judges are pinned or refused, at config load.** An unpinned model id is
  rejected while the operator is still looking at the config they just edited,
  before any credential is spent, rather than later at judge construction. Judge
  names must be unique for the same reason they matter: the judges hash, the
  resume key, and rigor's own rubric-drift lookup all key on the name, so two
  judges sharing one make all three wrong at once.
- **A judge that cannot parse its own model's answer is counted separately from a
  model that failed.** Those records leave the pass rate entirely and count
  towards a reliability tolerance instead, because an unreliable judge does not
  produce a cautious verdict, it produces a meaningless one.
- **The golden set is validated strictly and carries two hashes.** Duplicate ids,
  empty inputs, unknown keys and duplicate tags are errors naming the offending
  line, because each otherwise surfaces as a slightly wrong number three stages
  later where nobody will trace it back. The *content* hash — over canonical JSON
  of the parsed items — decides comparability; the raw-bytes hash is kept for
  provenance. Two artifacts are comparable only if their golden-set hash and
  judge-config hash both match; comparing across either is an error, not a
  warning.
- **The HTML report is self-contained, and that is enforced at render time**
  rather than only in tests, because it has to open in an airgapped compliance
  review. A render that would emit a fetchable URL raises and leaves no file
  behind — a half-written report that silently fetches nothing is worse than no
  report. Hostile content in golden-set inputs and model completions renders as
  escaped literal text.
- **The report recomputes nothing.** It imports no statistical function at all;
  every number is passed through from the evidence payload, so the report cannot
  disagree with the gate that decided the verdict. `n == 0` renders as an em dash
  rather than a number, which is why `wilson_interval` — whose `(0, 0)` case
  raises — is never called.
- **The report is the change-control evidence**: it names the golden set by hash,
  the judges by pinned model id and rubric hash, every threshold beside the file
  it came from, every adapter that contributed, and every item whose verdict
  flipped.
- **Latency is reported and is never a gate**, and the table says so, which is
  what stops it becoming one by habit.
- Configuration is TOML: `[[judge]]`, `[thresholds]`, `[run]`, `[report]`, with
  `./migkit.toml` picked up when present and every flag overriding it.
- `scripts/verify_release.py` executes the release checklist as fifteen checks
  that each print the evidence they acted on, and reads the built wheel rather
  than the source tree. A check that cannot run reports SKIPPED and the script
  exits 2, never 0: a verification script that quietly skips manufactures
  confidence, which is worse than having none.
- Python 3.10 through 3.13, on Linux and Windows. `opik-rigor`, `jinja2` and
  `rich` are the only runtime dependencies, plus `tomli` on 3.10 alone.

### Fixed

For a first release this is the record of defects found *during* the build. The
mechanism that caught each one is the part worth keeping, because it is why they
were found at all — and because four of the five mechanisms found things no
amount of careful reading did. The fifth is the only one nobody here ran: a
dependency narrowed its own contract, and that is recorded last.

**Simulation of the plan, before any code was written against it.** Ten defects
came back confirmed by computation rather than by argument, and several would
have shipped a tool that gets migration decisions backwards:

- Failed completions carried no judge score and so vanished from the
  Mann-Whitney arrays. A candidate that timed out on two items scored GO where
  one that answered those two items badly scored NO-GO — identical pass counts,
  opposite verdicts, in favour of the model that crashes. Both now come out
  identical to five significant figures, differing only in the imputed records
  the crasher carries.
- The power rule measured the wrong test entirely, certifying a run as powered at
  n=25 where simulated power against a ten-point drop is **33.9%**. The tool
  would have printed "no regression detected" and returned GO having never been
  able to ask.
- The floor rule reimplemented, worse, a primitive rigor already exposes, and
  reached the opposite conclusion on the same input.
- No multiplicity correction across judges: false NO-GO on identical models rises
  to 9.07% at four judges, against a plan requirement of no false alarms.
- The comparability check passed a truncated artifact — which matters precisely
  because the report's crash-tolerance guarantee means truncated artifacts exist.

**Independent conformance review of code that had already been written and
smoke-tested.** Writing the modules and smoke-testing them found two defects; the
review found ten more, two of which would have produced wrong verdicts rather
than visible errors:

- A truncated artifact was indistinguishable from a complete one. That flatters
  the candidate, because a run dies on the slow items.
- A provider outage baked into an artifact so that a healthy model takes a NO-GO
  for its infrastructure's sake. Partly fixed and partly recorded — see Known
  limitations.
- The golden set used raw bytes for both identities, so letting an editor add the
  trailing newline it always adds produced a different identity for a
  semantically identical set, and the operator would have had to re-run a
  baseline that cost real money. It also contradicted `contracts.py`'s own
  docstring, which already said the hash was of content rather than of a
  formatting decision.

**Test authors who never saw the code run**, writing from a frozen contract with
hand-derived expected values, found a third class the other two missed: a
validation branch that could never execute, and an over-broad adapter guard added
*while fixing* a review finding — caught because their counting proxy was a
legitimate use the guard refused. Later suites in the same mode found an unstable
list that silently dropped the items it exists to name (an item at 3/5 under both
models appeared in no list at all, and it is the most interesting row in the
report: a coin toss on both sides of the migration), and a contradiction in the
plan's own amendment that three careful passes over the same paragraph had not.

**Packaging checks run from outside the source tree.** One bug appeared in three
separate forms, all of which pass every local test: `.gitignore`'s `*.jsonl` rule
swallowing the bundled demo data; a CI job using `pip install -e .`, which leaves
the repo root importable so it could never notice; and — subtlest —
`importlib.resources` *multiplexing* a namespace package, so the developer's own
`src/` silently supplied whatever the wheel had omitted. The demo-data check now
runs in a bare subprocess with `-S` and only the extracted wheel on `sys.path`,
the demo CI job installs the built wheel, and `src/model_migration_kit/__init__.py`
removes the namespace-package mechanism entirely. **The wheel is not your source
tree**, and no packaging claim here was verified from an environment with the
source on its path.

**A dependency narrowing its own contract.** `opik-rigor` 0.2.0 made a one-sided
`confidence` at or below 0.5 a `ValueError` out of `wilson_lower_bound` and
`assert_pass_rate`, where 0.1.1 accepted such a value and answered. **Nothing
here broke: the 929 tests that existed before this change all pass against 0.2.0
with the source untouched.** What the narrowing surfaced is a latent mismatch in
*this* package's own validation. `Thresholds` validated `confidence` on the open
interval `(0, 1)` and `comparison.py` hands the value straight to
`assert_pass_rate`, so this package accepted a configuration it could not
honour: `Thresholds(confidence=0.3)` returned an object, and
`assert_pass_rate((18, 20), 0.8, confidence=0.3)` raises. No user had hit it —
this is the first release — and nothing in this repository sets a confidence
below the 0.95 default, which is exactly why 929 tests could not see it. The
defect was real all the same, and reachable by the first person to write a
config file.

`Thresholds.confidence` is now validated on `(0.5, 1)` and raises `ConfigError`
outside it, quoting the value and saying what it would have bought rather than
only what the legal range is. The dependency floor moved to
`opik-rigor>=0.2,<0.3` in the same change and deliberately not in a separate
one: any other pairing leaves a window in which a configuration passes
validation here and raises inside rigor at verdict time, after the completions
have been paid for. Refusing it at config load is the argument this project
already makes about unpinned judge model ids — the operator is still looking at
the file they just edited.

**0.5 is the intersection of two consumers, not rigor's number copied across.**
`thresholds.confidence` reaches `assert_pass_rate` at `comparison.py:1243`,
which is one-sided and now requires more than 0.5, and `wilson_interval` at
`comparison.py:1273`, which is two-sided, takes `z = ppf((1 + c) / 2)`, is
untouched by rigor's change and still accepts the whole open unit interval. The
legal range is the range both accept, and the refusal message says so rather
than leaving the number looking arbitrary. 172 tests pin it, written from the
contract by an author who did not write the validator and who derived no
expected value by running this package; 25 of them are red against the old
range. The two that matter name no boundary at all — they bisect each side for
the least confidence it accepts and compare the results — so the pair goes red
if either project moves its floor alone, including if rigor ever loosens this
again.

Individually, and worth naming because each is a trap rather than a slip:

- `argparse` exits 2 on a usage error, and 2 means REVIEW in this tool's CI
  contract. A mistyped flag must not be reportable as "collect more data", so
  argparse's own exit is intercepted and re-mapped.
- `AdapterError` and `SampleTimeout` do not inherit rigor's `RigorError`. Both
  are handled by name after checking their MROs, because a handler that assumed
  otherwise would let a missing API key escape as a traceback.
- Two release checks failed on prose rather than on a defect: they split the
  README on whitespace and read a sentence as twelve package names, and read
  `echo "migkit failed"` inside a fenced block as a `failed` subcommand. The scan
  now reads fenced code blocks and checks command position, under a contract that
  was frozen and independently tested before it was implemented.
- The rename to `model-migration-kit` rewrote the *expected* values in a PEP 503
  normalisation test while leaving its deliberately odd input spellings alone, so
  the test asserted that `Migration.Kit` normalises to `model-migration-kit`. The
  suite caught it. Test data that merely looks like the thing being renamed is
  exactly what a mechanical replacement gets wrong, and a sweep run without a
  suite behind it would have shipped it.
- One README figure was cut rather than shipped: the report size had been
  measured from a different run than the sentence described. Nobody would have
  caught it, and it would have been a fabricated number inside a document whose
  whole argument is that its numbers are not fabricated.

### Known limitations

A limitation you wrote down is a design decision; the same limitation discovered
by a user is a bug report.

- **This is the first release, so nothing here is a change *from* anything.**
  Every entry above describes how the code got to 0.1.0, not how it differs from a
  version somebody is running. The one exception is the dependency floor, which
  moved during the release itself and is recorded under **Fixed** with the reason.
- **There is no public Python API.** `__all__` is empty by decision, and it is a
  decision rather than an omission: v0.1's definition of done is entirely a CLI
  story, and the objects a library API would expose are built on opik-rigor's
  report dictionaries, which are `dict[str, Any]` today and on rigor's roadmap to
  become typed objects. That was expected in its 0.2; 0.2.0 shipped without it,
  so the surface is still untyped and the reason for not promising an API here
  still holds. Promising that surface now would mean the first thing the promise
  did was prevent taking the improvement. The modules stay importable at your own
  risk; only the CLI and its exit codes are a compatibility promise.
- ~~`judging.py` reaches into `opik_rigor.judge` for names outside rigor's public
  surface.~~ **Resolved before release.** rigor 0.1.1 exports `SCORE_MIN`,
  `SCORE_MAX`, `hash_rubric_file` and `hash_rubric_text` from its package root;
  the dependency floor here moved to `>=0.1.1,<0.2` at that point — it has since
  moved again, see below — and every site imports from the root. This was a
  *declared* dependency on unpromised names rather than a hidden one, and the
  reason it mattered is narrow and real: if rigor had renamed any of them, this
  project's pinned CI would have stayed green while its users hit the break on
  upgrade.
- **`Completion.tokens_in` and `tokens_out` are `None` for every adapter.**
  rigor's `Adapter` protocol is a model id plus `complete(str) -> str` and
  exposes no usage data, so a cost gate cannot be built without reaching past the
  seam. Consequently the tool makes **no claim about tokens, cost, or
  cost-per-verdict** anywhere, and the report cannot tell you what a verdict cost.
- **A provider outage can still bake into an artifact.** Every recorded draw
  counts against the resume budget, failures included, so a run that errored on
  all *n* draws because the provider was down looks complete: re-running samples
  nothing, and the model takes a NO-GO for its infrastructure's sake. The only
  v0.1 remedy is `--fresh`, which discards the artifact. Re-drawing failures on
  resume was started and deliberately backed out — it would let repeated re-runs
  launder a model that times out into one that does not, and doing it honestly is
  a sample-weighting decision that belongs in the comparison layer, with the
  statistics in view, rather than in the writer.
- **Underpowered is the normal case for a small golden set, and the tool will say
  so.** `required_sample_size` reports 67, 109, 155 and 229 completions **per
  side** at baseline pass rates of 99%, 95%, 90% and 80%. A ten-item golden set
  at n=5 gives you fifty — below every one of them, so it will usually return
  REVIEW rather than GO. The bundled demo is in the same position at 60 per side
  and prints its own warning; it escapes REVIEW only because its regression is
  large enough for NO-GO to outrank it. If that is irritating, the fix is more
  draws or more items, not a looser gate.
- **Artifact filenames flatten `/`, `:` and space to `-`**, so `gpt/4o` and
  `gpt-4o` resolve to one path, and on Windows `GPT-4o` and `gpt-4o` collide as
  well. Both the resume path and `--fresh` check header identity before touching
  a file, so a collision is loud rather than destructive — but the name still
  cannot distinguish them. Including a digest of the full model id would fix it
  and changes a frozen contract; deferred, not solved.
- **Threshold provenance is per-run, not per-threshold.** The evidence payload
  records the config path but not which of default, file or flag set each
  individual threshold, so the report's appendix prints the recorded path or says
  the source is not in the record. Deriving it in the report would be the
  recomputation the report is built not to do; supplying it properly means the
  comparison layer emitting the mapping.
- **Exit `0` and exit `2` have never been produced by a live end-to-end run.**
  A GO or a REVIEW from real models needs a real judge and there are no
  credentials in the environment this was built in. Both are covered through the
  same `cli.main` entry point by the test suite, and the README says which is
  which rather than presenting all four as executed.
- **Verified against `opik-rigor` 0.2.0 only.** The declared bound is
  `>=0.2,<0.3` (`pyproject.toml`), and the floor moved for a behaviour change
  rather than for a feature — the confidence entry under **Fixed** above. It
  moved in the same change as the validation it matches, because at any other
  pairing the two projects disagree about which configurations are legal. The
  upper bound is `<0.3` because rigor reserves each minor for changes to what a
  recorded sample means. The reasoning, and what would have to change here when
  it moves, is in [COMPATIBILITY.md](COMPATIBILITY.md).
- **One comparison, one report.** No trend history, no Opik experiment logging,
  no cost model, no multi-judge weighting, no dashboard, and no claim about any
  item outside your golden set.

[0.1.0]: https://github.com/ericwehmeyer/model-migration-kit/releases/tag/v0.1.0
