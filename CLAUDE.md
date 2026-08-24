# Working in this repository

Rules that apply to every agent here, whatever the task. They exist because
each one has already cost this project real time.

## The interpreter

**There is no venv inside the worktrees.** The only interpreter is:

```
C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe
```

**The `.pth` trap is fixed (2026-08-24); you no longer need `PYTHONPATH`.**
`scripts/worktree_path.py` is installed into that venv as an import hook. It
resolves the package from your **current working directory's** worktree, so a
bare `python -c` from any checkout imports that checkout's code — verified from
four worktrees at once. Outside any checkout it falls back to the main checkout,
exactly as before.

Before the fix, the editable install's `.pth` hardcoded one checkout's `src`, so
running from a worktree silently imported *someone else's code* — no warning, no
error, wrong answers. It caught the orchestrator and six agents.

Setting `PYTHONPATH` explicitly still works and still wins. Printing
`model_migration_kit.__file__` is still cheap and still worth doing when a result
surprises you.

```
python scripts/worktree_path.py --status      # what is active
python scripts/worktree_path.py --uninstall   # restores the original
```

## Running the suite

`pytest-xdist` is installed. `-n 8` takes the suite from ~136 s to ~97 s on a
quiet machine, and all 12 configurations tested return identical pass/fail.

**Check the load before choosing a worker count.** Wall-clock here is dominated
by *other agents*, not by the code: `tests/test_report.py` measured 365 s with
five agents running and 27 s minutes later on identical code. If several agents
each run `-n 8` on 16 cores, everyone gets slower. Use `-n 4` when the board is
busy and `-n 8` only when it is quiet.

## One agent, one worktree

Never two agents in one working tree — they overwrite each other's edits and
each other's restores, and a `git add -A` from anyone can capture whatever is
live at that moment. Reviewers included:

```bash
git worktree add --detach /c/Users/ewehm/repos/mk-<chunk>-review <sha>
```

Detached, so several can sit at one commit without fighting over a branch.

Never touch `C:\Users\ewehm\repos\migration-kit` (the main checkout) — it is
usually on some agent's branch. `mk-main` is the worktree for `main`.

## Your scratchpad is shared. Namespace it.

Worktrees isolate the repo. They do **not** isolate the session scratchpad —
every agent of a pair defaults to the same directory, and a common filename
(`probe.py`, `check.py`, `fixture.py`) will collide.

**Put your files in a subdirectory named for your role and chunk**, e.g.
`scratchpad/c14b-impl/`. Never write a bare filename into the scratchpad root.

This has now cost twice. On C4 a tester wrote its test file over the
implementer's staging file and the harness put ~140 lines of the tester's work
into the implementer's context unrequested. On C14b the same thing happened
again, in the other direction.

**And the rule that matters more than the isolation:** if you find yourself
looking at something you were not supposed to see — your blind partner's
fixtures, its assertions, its reading of the contract — **stop and report it in
your final message.** Do not quietly route around it.

The reason is not tidiness. A blind pair's entire product is two independent
readings of an ambiguous contract, surfaced as a conflict for the orchestrator
to rule on. On C4 the leak did not merely contaminate the implementer: it
*destroyed the signal*, by letting a real contract disagreement resolve itself
silently inside one agent instead of reaching anyone who could rule on it. An
agent that hides a leak costs a contract defect. An agent that reports one hands
it over for free — which is exactly what C14b's implementer did, and it was
right to.

## Mutating code you are reviewing

Restore from a **byte-verified backup** (copy the file, compare hashes),
**never** `git checkout --`. Verify `git status` is clean before reporting.

## Gates

```bash
python scripts/check_merge.py        # seven checks; the merge gate
python scripts/check_contract.py <plan>
python scripts/verify_release.py     # SKIP is exit 2, not a pass
python scripts/dependency_surface.py
```

`check_merge.py` may be run freely inside your own worktree. Only serialise it
when two agents share a tree, which they should not.

## Known and deliberately deferred — do not "fix" these

- **`ruff format --check tests/test_report.py` fails on `main`** with ~50
  pre-existing hunks and never has passed. Do not report it, do not reformat.
- **Repo-wide format drift**: the tree is formatted at 88, `pyproject.toml`
  says 100, ~26 files. It gets its own chunk when the tree is quiet.
- `tests/test_report.py` takes **~30 seconds** (371 tests); the full suite is
  **~4 minutes** serial. If you see six minutes for `test_report.py`, that is
  **CPU contention from other agents**, not the file. Measured 365s once with
  five agents running and 27s with the same code minutes later.

## How work is done here

Four roles per chunk: **implementer** (code only) and **blind tester** (tests
only, from a branch cut before the implementation, never reading each other),
then **merge**, then a **reviewer** who mutation-tests, then a **fix pass**.

Seven of seven reviews have found defects both other roles missed; three found
defects in already-merged code. Budget for the fix pass — the review is the
middle of a chunk, not the end.

**The fix pass must unfix itself:** revert each ruling one at a time and confirm
the suite goes red. A fix whose test does not fail without the fix is not a fix.
If a revert stays green, report that plainly rather than adding an assertion to
paper over it.

## Two standing rules that catch real defects

**Fixture monoculture.** A fixture where the broken and the correct
implementation agree is a fixture that tests nothing. Vary the field the code
is supposed to be reading — and vary it *in pairs*: a fixture set can vary every
field individually and still be a monoculture in combinations. Both halves have
caught shipped defects here.

**An absence must not render as a measurement.** A value that was never recorded,
a comparison that could not be made, and a measured zero must be distinguishable
on the page. Five chunks in a row have turned on this; it is the central design
rule of this document, not a recurring coincidence.

## If the contract is wrong

Report it and **stop** — do not pick a reading and proceed. Four agents have done
exactly that and all four were right; one refused a ruling that a second ruling
in the same brief had falsified.
