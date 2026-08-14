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

## Where the build stands (2026-08-13, ~23:00)

**Sessions 0 through 3 are complete, committed and pushed. 675 tests green, ruff
clean.** Session 4 (release) is partly done and is the remaining work.

Everything builds and runs. The headline check, which you should run first
because it proves the whole pipeline in two seconds:

```
cd C:\Users\ewehm\repos\migration-kit
.\.venv\Scripts\python.exe -m model_migration_kit.cli demo --out demo.html
```

Expect **exit 1** (NO-GO — that is the demo working; it exists to show the tool
refusing an unsafe migration), about 2 seconds, and a self-contained HTML report
carrying a red FAKE MODELS band. Exit 0 or 2 there means something regressed.

| Module | State |
|---|---|
| `goldenset.py`, `runner.py` | Session 1, complete |
| `judging.py`, `comparison.py` | Session 2, complete |
| `report.py`, `cli.py`, `demo.py` | Session 3, complete |
| `__init__.py` | written, `__all__` empty by decision |
| `scripts/verify_release.py` | 15 release checks, executable |
| `.github/workflows/{ci,drift-canary}.yml` | CI plus a weekly drift canary |

**The package was renamed** to `model-migration-kit` / `model_migration_kit`
(console script still `migkit`). The GitHub repo is renamed too and is **private**
at <https://github.com/ericwehmeyer/model-migration-kit>. The local checkout
directory is still called `migration-kit` and that is deliberate — renaming it
would invalidate every path in these docs for no packaging benefit.

## What is left, in order

1. **`readme-pip-install` and `readme-commands` in `scripts/verify_release.py`
   fail on prose, not on a defect.** They split the README on whitespace and read
   `install from a checkout: # Windows: pip install` as package names. The README
   is correct. Fix the parser to read fenced code blocks. This is the only known
   *wrong* thing in the tree.
2. **Version bump** `0.1.0.dev0` → `0.1.0` in `pyproject.toml` and
   `__init__.py`. `verify_release.py` blocks on this deliberately, and it is a
   release act — do it when actually releasing, not before.
3. **Session 4 phases 0 and 5 onward** in `docs/session-4-release-contract.md`:
   re-check the name on PyPI (it was free on 2026-08-13, but check again — the
   sibling checked after tagging and ate a 34-file rename), make the repo public,
   register trusted publishers on **both** TestPyPI and PyPI (separate sites,
   separate registrations, different environment names — the sibling lost three
   attempts to this), TestPyPI dry run, then tag and release.
4. **Retire the last invariant-1 violation.** `judging.py` imports `SCORE_MIN`,
   `SCORE_MAX` and `hash_rubric_file` from `opik_rigor.judge` because they were
   not public. **rigor has since exported them** (merged, unreleased). When rigor
   cuts that release and this project's bound moves, change the imports to the
   package root and delete the note in PROGRESS.md's known gaps.
5. **Roadmap Phase 5** — a PR-proposing test agent — has a full build plan at
   `C:\Users\ewehm\repos\campaign\plans\phase-5-pr-agent-build-plan.md`. Not
   started, and estimated at six weeks of nights rather than the roadmap's three.

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
  package directory while an agent is running tests.
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

Already created and verified: `opik-rigor 0.1.0` **from PyPI** (not a path
dependency — that is deliberate), `jinja2 3.1.6`, `rich 15.0.0`. Local Python is
3.14; CI covers 3.10–3.13 on Ubuntu and Windows.

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
