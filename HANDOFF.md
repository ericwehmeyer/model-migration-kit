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

Session 0 is done: scaffold, Apache-2.0 license, CI, and the frozen contracts.
**Nothing is committed yet** — `git init` has run and every file is untracked.
The first commit should be exactly this scaffold, before module work starts.

**Next up is Session 1: the offline data path.** Build `goldenset.py` (strict
loader/validator, JSONL, hash embedded downstream) and `runner.py` (executes a
golden set against one model via a rigor adapter, n samples per item, resumable).
Session 1's exit criteria are in the build plan, section 2.

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
