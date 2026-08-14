# HANDOFF — read this first

You are picking up **model-migration-kit** cold, with no conversation history. This file
is written so that is fine. Nothing important lives only in a transcript.

## In one paragraph

model-migration-kit is a CLI that answers *"is it safe to move from model A to model
B?"* with a statistically defensible go/no-go verdict instead of a vibe check. A
golden set goes in; two models are run against it and graded by identical pinned
judges; a distribution-diff report comes out; the exit code is a CI gate. It is
the first real consumer of **opik-rigor** — every statistical primitive is
imported from it, none reimplemented. Apache-2.0, personal repo.

## Read these, in this order

1. **[docs/build-plan.md](docs/build-plan.md)** — the approved plan, written and
   agreed before any code. It contains the module contracts, the three session
   boundaries, the test inventory used as the acceptance contract, and
   pre-decided answers to the known risks. It is committed verbatim including
   anything it gets wrong. **Do not re-plan.** If the plan must change, edit the
   plan first, deliberately.
2. **[PROGRESS.md](PROGRESS.md)** — what exists, what is decided and why, the
   invariants, and the known gaps.
3. **`src/model_migration_kit/contracts.py`** and **`errors.py`** — the frozen seams.
   Read them before writing anything that touches a golden set or an artifact.

## Where the build stands (2026-08-14, ~03:40)

**Sessions 0 through 3 are complete, committed and pushed at `19c7722`. 929 tests
pass, 0 xfailed, ruff clean.** Session 4 (release) is partly done and is the
remaining work.

**Check CI before you trust that.** The run on `19c7722` was still in flight when
this was written, and it is the *first* run of a rewritten `demo` job — see "CI
job that may be red" below. Everything else was green on `c762777`.

The 4 xfails this file used to describe are **gone** — retired, not deleted, with
their fenced replacements in place. Do not go looking for them.

### The definition of done: now measured, and the measurement disagrees with itself

The `demo` job used to run `timeout 120 migkit demo` and call that the definition
of done. It was measuring the wrong interval. The claim is *"a stranger with no
keys gets a report in under two minutes"*, and a stranger's two minutes start at
`python -m venv` — not after pip has already dragged in numpy and scipy. The old
job bounded the last ~16 s of a path that measures far longer, and would have
stayed green with pip taking four minutes.

The job now times `venv` + `pip install` + `migkit demo` together and fails over
120 s. **It passes at 12 s** on `19c7722`. That is a real result and it is also
not the whole story:

| where | cold start, end to end |
|---|---|
| GitHub Ubuntu runner (the CI job) | **12 s** |
| This Windows machine, measured twice | **127 s and 142 s** — of which 83–91 s is `pip install` |

Same interval, an order of magnitude apart. Ubuntu runners get manylinux wheels
for numpy and scipy and a fast warm cache; a Windows stranger gets neither. So CI
now proves the claim *for a Linux CI machine* and says nothing about the Windows
desktop where it was falsified. **Do not read the green tick as the claim being
established.** Either extend the job to a Windows runner — which is the honest fix
and costs one matrix entry — or narrow the published claim to name the
environment it holds in.

Whatever you do, do not go back to timing a smaller interval. The job says so in
a comment, and so does this file, because that is exactly the kind of thing that
gets quietly reverted at 3am.

Everything builds and runs. The headline check, which you should run first
because it proves the whole pipeline in two seconds:

```
cd C:\Users\ewehm\repos\migration-kit
.\.venv\Scripts\python.exe -m model_migration_kit.cli demo --out demo.html
```

Expect **exit 1** (NO-GO — that is the demo working; it exists to show the tool
refusing an unsafe migration), about 2.1 seconds measured over three runs, and a
self-contained HTML report carrying a red FAKE MODELS band. Exit 0 or 2 there
means something regressed.

**The suite is now proved offline rather than described as offline.** CI runs it
under `scripts/audit/netguard.py`, which makes every outbound connect and every
DNS resolution raise. The release contract's old `-m "not requires_network"`
deselected zero tests and guaranteed nothing. `scripts/audit/shuffle_order.py`
is beside it for order-independence audits — not wired into CI; run it
deliberately with `AUDIT_SHUFFLE_SEED` set.

| Module | State |
|---|---|
| `goldenset.py`, `runner.py` | Session 1, complete |
| `judging.py`, `comparison.py` | Session 2, complete |
| `report.py`, `cli.py`, `demo.py` | Session 3, complete |
| `__init__.py` | **`__all__` now names three accessors** — see below |
| `scripts/verify_release.py` | 15 release checks; 14 pass, 1 skips on the dev version |
| `scripts/dependency_surface.py` | derives COMPATIBILITY.md's table from the AST; `--check` gates CI |
| `scripts/audit/{netguard,shuffle_order}.py` | netguard runs in CI; shuffle is run by hand |
| `.github/workflows/{ci,drift-canary}.yml` | CI plus a weekly drift canary |
| `.github/workflows/publish.yml` | copied verbatim from the sibling; see below |
| `CHANGELOG.md` | written, `## [0.1.0]`, dated at release |

### What changed on 2026-08-14 that this file did not say before

- **`__all__` is no longer empty.** It names three accessors for the data files
  the wheel ships (`demo_goldenset.jsonl`, `demo_rubric.md`, `demo.toml`). A
  reader who installed the wheel previously had no way to reach any of them, and
  the README told them to open `src/model_migration_kit/data/…`, which is a path
  inside a source checkout. The sibling solved this with `example_rubric_path()`;
  this is the same call. The rest of the no-public-API decision stands.
- **`migkit demo --goldenset <file> --n <draws>` exists.** Before it, `migkit
  compare` refused a fake judge and told you to "use `migkit demo` for the keyless
  path" — and `demo` had no way to take your golden set, so the remedy the error
  named did not exist. That was the point a cold-start reviewer said they would
  have given up. There is now a keyless end-to-end path for your own data;
  verified producing a REVIEW verdict on a hand-written 3-item set.
- **`anthropic` and `openai` are declared as extras**, and the SDK check moved to
  adapter construction. Following the Install section used to leave a reader one
  undeclared package short of the only documented real-model path, and they found
  out at *grading* time, after both runs had already been sampled.
- **The HTML report is bounded, judging is parallel, the evidence log is
  streamed.** A scale audit found 200 items × n=20 — inside the README's own
  recommended range — produced a 32 MB report, and 1000 × 20 produced 161.8 MB
  with 41,000 `<pre>` blocks, while every guard the project owns passed on it.
  `judge_artifact` now takes `concurrency` and the artifact is byte-identical
  whatever it is set to.
- **The report reader no longer trusts the evidence log it is handed.** Sharing a
  log across machines is the designed workflow, so its recorded paths are
  attacker-influenced input on a reviewer's machine — and they went straight to
  `open()`. A recorded `\\192.0.2.111\share\x.jsonl` blocked 21 s attempting SMB,
  which on Windows is how a hash gets collected. Recorded paths are now confined
  to the log's own directory; `--artifact-dir`/`--goldenset` remain the way to say
  where files moved to.
- **Terminal rendering can no longer be driven by the evidence.** A `model_id` of
  `fake-cand-v1[/]` crashed the run through rich's markup parser; `[bold
  red]FAKE CLEARED[/bold red]` rendered as styled text. In a tool whose claim is
  that you cannot get a clean-looking report out of scripted models, that is a
  forgery vector.

**The package was renamed** to `model-migration-kit` / `model_migration_kit`
(console script still `migkit`). The GitHub repo is renamed too and is **private**
at <https://github.com/ericwehmeyer/model-migration-kit>. The local checkout
directory is still called `migration-kit` and that is deliberate — renaming it
would invalidate every path in these docs for no packaging benefit.

## What is left, in order

1. ~~**Get CI green.**~~ **Done, and watched.** CI had never once been green on
   this repository: all four Windows cells failed on a test that looked for the
   console script beside `sys.executable` — true in a venv and on Ubuntu, false
   on GitHub's Windows toolcache, where the interpreter is in `x64\` and scripts
   land in `x64\Scripts\`. Because `demo` and `build` declare `needs: test`,
   neither had ever executed. The test now resolves the directory through
   `sysconfig.get_path("scripts", …)`. **8/8 green plus `demo` and `build`,
   confirmed on the real matrix and green on every push since.**
2. **Version bump** `0.1.0.dev0` → `0.1.0`. This is now **one** edit, in
   `src/model_migration_kit/__init__.py`: the version is single-sourced through
   `dynamic = ["version"]` and `[tool.hatch.version]`. `verify_release.py` blocks
   on it deliberately, and it is a release act — do it when actually releasing.
   Date the `## [0.1.0]` heading in `CHANGELOG.md` in the same commit.
3. **Session 4 phases 0 and 5 onward** in `docs/session-4-release-contract.md`.
   The contract's stale pre-rename repo name has been fixed — it previously told
   you to register a trusted publisher against `ericwehmeyer/migration-kit`,
   which does not exist, and that is precisely the failure the sibling lost three
   attempts to. Re-check the name on the PEP 503 `/simple/` index, not the JSON
   endpoint: JSON 404s both for an unregistered name and for one registered and
   never uploaded to, and only `/simple/` tells those apart. All four probes were
   404 on 2026-08-13 and **all eight were 404 again on 2026-08-14** — four
   spellings (`model-migration-kit`, `model_migration_kit`, `migkit`,
   `modelmigrationkit`) × both indexes, so the name is still free on PyPI *and*
   TestPyPI. Re-run on the day you tag anyway; it costs seconds and the failure it
   prevents is unrecoverable. Then make the repo public,
   register pending publishers on **both** TestPyPI and PyPI (separate sites,
   separate logins, different environment names), TestPyPI dry run, tag, release.
   Note the dry run is one-shot per version: TestPyPI burns a filename exactly
   as PyPI does, so do it only once the tree is what you intend to publish.
4. ~~**Retire the last invariant-1 violation.**~~ **Done.** rigor 0.1.1 exports
   `SCORE_MIN`, `SCORE_MAX`, `hash_rubric_file` and `hash_rubric_text` from its
   package root — confirmed by reading `__init__.py` out of the *published wheel*,
   not by trusting the branch that prepared it. The floor is `>=0.1.1,<0.2` and
   `src/` imports only from the root.

   **This file said three sites. There were six.** `comparison.py` had been
   reaching into `opik_rigor.judge` in shipped code the whole time and appeared on
   no list, and two more arrived the same night in new test files. That was the
   third time the count had been recorded too low, always understated — because
   the pattern a new file copies is the pattern the tree already contains. So
   `COMPATIBILITY.md`'s dependency-surface table is now **derived from the AST** by
   `scripts/dependency_surface.py`, and `--check` fails CI when the document and
   the tree disagree. It found eleven disagreements on its first run against the
   hand-written table, and has since caught two more changes it was not written
   for. If you ever find yourself editing that table by hand, stop and regenerate.

   One recorded exception: `tests/test_stranger_path.py` imports
   `opik_rigor.adapters.{anthropic,openai_compat}` for their `PACKAGE` constants,
   which are not in rigor's `__all__`. Test-only, and its purpose is drift
   detection — it asserts the SDK names this CLI tells you to install are the ones
   rigor is about to import. Argued in PROGRESS.md rather than hidden.

5. ~~**Known scale limits.**~~ **Fixed, and re-measured 2026-08-14** at the audit's
   worst in-range case — 200 items × n=20, 4 KB outputs, every item changed:

   | | before | after |
   |---|---|---|
   | HTML report | 32.4 MB | **10.14 MB** |
   | `<pre>` blocks | ~8,000 | 2,542 |
   | report model + render | — | 0.5 s |
   | judging, concurrency 1 → 8 | no pool at all | **1.46×** |

   The cap is `DEFAULT_MAX_REPORT_CHARS = 10,000,000` and **no row is dropped**:
   rows past the budget still render, without their input, draws or judge reasons,
   and say so where the outputs would have been. That was the right call — dropping
   rows to fit a byte budget would silently remove the most interesting evidence.

   **Read the 1.46× correctly.** It was measured with a `FakeAdapter` at zero
   latency, where the audit showed the pool can do almost nothing because the
   per-record `fsync` is serial and outside it. Against a real provider at ~1 s per
   call the gain is the one that matters and is much larger — and it is also the
   one this project cannot measure without spending money. Do not quote 1.46× as
   the benefit of judging concurrency; quote it as the floor.

6. ~~**`COMPATIBILITY.md` records verification against rigor 0.1.0.**~~ **Done
   2026-08-14.** Re-verified against the installed 0.1.1, and the good news is the
   strong kind: seven signatures re-checked mechanically, **zero changed**; every
   attribute-level dependency present; `SCORE_MIN`/`SCORE_MAX`, the `Adapter`
   protocol members and the `pytest11` entry point unchanged; `__all__` 33 → 38
   with nothing removed. rigor promised 0.1.1 was additive and that is now checked
   rather than trusted.

   The two defects in 0.1.1 that make the documented real-model path unusable now
   lead that file, with the workaround that does exist recorded beside them: a
   *dated* model id such as `claude-haiku-4-5-20251001` is both accepted by
   0.1.1's pin rule and current. There is no route to `claude-opus-5`, which
   carries no date. Both defects are fixed on rigor's `main` and unreleased.
## The sibling has moved a long way, and none of it is released

`opik-rigor`'s `main` is far ahead of the published **0.1.1** that this project
depends on. Do not read rigor's source tree to learn what your users get.
Unreleased there, all verified in this session:

- **`is_pinned` was rewritten.** 0.1.1 refuses `claude-opus-5`, `claude-sonnet-5`
  and `claude-opus-4-8`, accepts `claude-3-7-sonnet-20250219` (retired in
  February), and wrongly accepts `gpt-4.1` (an alias that re-points). All fixed on
  `main`.
- **`AnthropicAdapter` sent `temperature` unconditionally**, which every current
  Anthropic model rejects with a 400. Fixed on `main` by omitting the key
  entirely; an explicit value against such a model is now refused at construction.
  These two were sequential blockers — the pin rule was the front door locked,
  this was there being no room behind it — and **both are needed before rigor can
  judge with a current model**.
- `import opik_rigor` went from ~1019 ms to ~303 ms (scipy is now lazy), a
  score-distribution gate that passed on infinite input was fixed, `py.typed` was
  made true (an `api_key=` misuse type-checked clean and raised at runtime), and
  the worked example now ships *inside* the wheel.
- **One change there is not additive:** `confidence <= 0.5` is now refused by
  `wilson_lower_bound` and `assert_pass_rate`. It used to be accepted, and
  `assert_pass_rate((20, 20), 1.0, confidence=0.5)` used to *pass* — the exact
  claim that module exists to refuse. That needs a version decision on rigor's
  side before this project raises its floor again.

## Things that will bite you, all learned the hard way tonight

- **The wheel is not your source tree.** Three separate variants of one bug
  appeared: `.gitignore` swallowing the demo data, a CI job using `pip install -e .`
  so it could never notice, and — subtlest — `importlib.resources` *multiplexing*
  a namespace package so the developer's own `src/` filled in what the wheel had
  omitted. `scripts/verify_release.py` now probes a bare subprocess with `-S`.
  Never verify packaging from an environment that has the source on its path.
- **Agent worktrees are cut from the session's working directory**, which was
  `opik-rigor`. Agents told to work on this project were handed worktrees of the
  wrong repo. Tell them the repo path explicitly and to make their own worktree.
- **The venv's editable install points at the main tree**, so a worktree agent's
  tests import the main tree's package, not their own copy. Do not rename the
  package directory while an agent is running tests. This is worse than it
  sounds: an agent reported "730 passed" as a green run that had **never loaded
  its own code**. Every worktree brief must carry `PYTHONPATH=<worktree>/src` and
  a `print(module.__file__)` confirmation. The silver lining is that running a
  new test file *without* PYTHONPATH gives a free red baseline against unfixed
  `main`, which is how two agents produced red/green proofs with no stashing.
- **A test can encode the platform of whoever wrote it, and the matrix is the
  only thing that finds out.** This bit both projects on the same night in
  opposite directions: here, a Windows-only CI failure; in the sibling, four
  Ubuntu cells failing on a hardcoded `C:\...` path that `pathlib` reads as a
  single relative filename on POSIX. Both passed everywhere they were written.
- **A check that parses another tool's output is parsing a human-facing string
  nobody promised to keep stable.** The sibling's release gate counted lines
  ending in `PASSED` from `twine check`; under GitHub Actions twine colourises,
  so the line ends in an ANSI reset and the count came out zero on a build that
  was fine. It had passed on every developer machine.
- **A mechanical rename sweep will rewrite test data that merely looks like the
  thing being renamed.** It changed the expected values in a PEP 503 test whose
  inputs are deliberately odd spellings. The suite caught it; a sweep run without
  the suite behind it would have shipped it.

## Environment

```
cd C:\Users\ewehm\repos\migration-kit
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

Already created and verified: `opik-rigor` **from PyPI** (not a path dependency —
that is deliberate), `jinja2 3.1.6`, `rich 15.0.0`. Local Python is 3.14; CI
covers 3.10–3.13 on Ubuntu and Windows.

The long-lived `.venv` now holds `opik-rigor` **0.1.1**, upgraded 2026-08-14. It
had been on 0.1.0 for a while, silently, because the bound `>=0.1.0,<0.2` let
every *fresh* install resolve 0.1.1 while this venv kept what it already had — so
the venv had stopped being evidence about what users get, and nobody noticed.
Re-check it whenever the floor moves. Anything you conclude about rigor's
behaviour from a long-lived venv is a claim about a version strangers may not
get — re-check in a clean environment before writing it down.

To run the suite the way CI does, with the network physically blocked:

```
set PYTHONPATH=scripts\audit
.\.venv\Scripts\python.exe -m pytest -p netguard
```

## The working method — this is the part that matters

This project follows the method that produced opik-rigor. It is not ceremony; it
found four real defects there, every one of them in code the lead had written or
specified.

1. **Freeze shared contracts by hand before any parallel work.** Agents working
   in parallel fail at seams, not at logic. `contracts.py` exists for this reason
   and is already frozen.
2. **The author of a module never writes its tests.** Dispatch them as separate
   agents with disjoint file ownership. A model that just spent an hour building
   something is the worst available reviewer of it: it will encode what it
   *believes* the code does, and if the belief is wrong the test is wrong in the
   same direction.
3. **Supply expected values from outside the implementation.** Never let a test
   author generate an expectation by running the code under test. Derive it, hand
   it over, and ship the independent oracle with the tests.
4. **Give every agent brief something checkable** — a contract, a list of
   expected values, an acceptance checklist. A brief without a verifiable output
   contract produces fluent prose, not findings. This was learned the hard way:
   an agent asked an open diagnostic question returned a confident, partly
   fabricated report.
5. **Evidence, not confidence.** A claim is backed by command output or it is not
   a claim. Run the quickstart in a clean environment and paste the real output.
   Verify a vendor API by installing and introspecting it.
6. **Seed every RNG explicitly.** A flaky test in a project about statistical
   gates is self-refuting.
7. **End each session green, committed, with this file and PROGRESS.md updated,
   then clear the context.**
8. **Assume an agent can die mid-task, and make its work salvageable anyway.**
   Four agents were lost in one window on 2026-08-14 — two stalls, two API
   failures — after doing substantial correct work. Three had committed nothing.
   Their output was recovered by reading the uncommitted diffs out of their
   worktrees, testing it, fixing the lint they had not reached, and committing it
   as the integrator. That worked, but only because each was in its own worktree.
   Two things follow: **tell agents to commit early and often rather than once at
   the end**, and when one dies, *look in its worktree before redoing the task* —
   `git -C <worktree> status --short` is the whole check, and it took under a
   minute to recover three tasks' worth of work.

   The corollary is that a dead agent's work is unreviewed by its author. It
   arrives without the report that would normally explain it, so verify the claims
   yourself: one of the three had a passing suite and eight lint errors, and
   another changed the rigor dependency surface without updating the record — both
   caught by running the checks, not by reading the diff.

One caveat learned late on the previous project, worth carrying: introspecting a
package tells you what its API *is*. It does not tell you what its documentation
*says*. Those are separate claims needing separate evidence.

Session 1 tested the method itself, and the numbers are worth recording. Writing
the modules and smoke-testing them found 2 defects. An independent reviewer, given
the same modules and a checkable brief, found 10 more — including two that would
have produced wrong verdicts rather than visible errors, and one where the code
contradicted a docstring in the frozen contracts. The test authors, who never saw
a passing run of the code, found a third class: a validation branch that could
never execute, and an over-broad guard I had added *while fixing* a review finding
— caught because their counting proxy was a legitimate use the guard refused. None
of the three roles would have found the others' defects. That is the argument for
the method in one paragraph, and it is why the cost of running it is worth paying
again in Session 2.

## Sibling project

**opik-rigor** lives at `C:\Users\ewehm\repos\opik-rigor`, is published at
<https://pypi.org/project/opik-rigor/>, and is public at
<https://github.com/ericwehmeyer/opik-rigor>. Its own `PROGRESS.md` records the
decision log and a roadmap built from real caller friction. If model-migration-kit needs
something rigor does not expose, that is a rigor roadmap item — record it, work
around it at the public API surface, and do not reach into internals.

Its `COMPATIBILITY.md` is also worth reading for the shape of a good vendor-API
record, including a retraction of a claim that project got wrong.

## Not yet done, deliberately

- **Nothing is published.** No PyPI release, no TestPyPI upload, no public repo,
  no announcement anywhere. The GitHub repo exists and is private.
- The version is still `0.1.0.dev0`, on purpose. It moves at release time.
- No public Python API. `__all__` is empty and the reasoning is in `__init__.py`.

## The evidence that this method works, since you will be asked to pay for it

Session 1 measured all three roles on the same code. Writing the modules and
smoke-testing them found 2 defects. An independent conformance reviewer found 10
more, two of which would have produced *wrong verdicts* rather than visible
errors. The test authors, who never saw the code run, found a third class: an
unreachable validation branch, and an over-broad guard added while fixing a
review finding.

Session 2 went further and stress-tested the *plan* by simulation before any code
existed. It found that the draft verdict logic would have given **GO to a model
that crashes and NO-GO to one that merely answered badly** — identical pass
counts, opposite verdicts, favouring the model that failed — and that the power
rule certified a run as adequate at n=25 where real power against a ten-point
drop is 33.9%. Both were corrected in `docs/build-plan.md` §6 before a line was
written against them. Nothing found them by reading.
