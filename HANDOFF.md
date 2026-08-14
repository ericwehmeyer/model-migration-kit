# HANDOFF — read this first

You are picking up **migration-kit** cold, with no conversation history. This file
is written so that is fine. Nothing important lives only in a transcript.

## In one paragraph

migration-kit is a CLI that answers *"is it safe to move from model A to model
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
3. **`src/migration_kit/contracts.py`** and **`errors.py`** — the frozen seams.
   Read them before writing anything that touches a golden set or an artifact.

## Where the build stands

Sessions 0 and 1 are done and committed. Session 0 is the scaffold, Apache-2.0
license, CI, and frozen contracts (`45b6567`). Session 1 is the offline data
path: `goldenset.py` and `runner.py`, with tests written by agents who did not
write the modules.

**Next up is Session 2: judgment and verdict** — `judging.py` and
`comparison.py`. Its exit criteria are in the build plan, section 2, and its
module contract was derived and adversarially stress-tested before any code; see
PROGRESS.md for what was frozen and why.

Sessions 3 and 4 also have frozen contracts now. Session 4 is not in the build
plan — it is the release phase the plan defers into, specified because the
sibling project improvised it and paid for that. Two things it established that
are worth knowing early: `migration-kit`, `migration_kit` and `migkit` were all
unclaimed on PyPI and TestPyPI when checked on 2026-08-13 (**re-check before
tagging** — the sibling checked after tagging and ate a 34-file rename), and the
demo's golden set must live inside the package rather than at the repo root, or
it ships in nobody's wheel while every test still passes.

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
decision log and a roadmap built from real caller friction. If migration-kit needs
something rigor does not expose, that is a rigor roadmap item — record it, work
around it at the public API surface, and do not reach into internals.

Its `COMPATIBILITY.md` is also worth reading for the shape of a good vendor-API
record, including a retraction of a claim that project got wrong.

## Not yet done, deliberately

- No GitHub remote. The repo is local only; nothing is pushed.
- The package name has not been checked on PyPI. Per the plan, that happens in
  Phase 0 of publishing, not now. **Check it before the first release** — the
  previous project discovered its intended import name was already taken, after
  tagging.
- `src/migration_kit/__init__.py` does not exist. Write it last.
