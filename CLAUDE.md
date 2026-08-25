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
python scripts/check_merge.py -n 8   # the same, under xdist
python scripts/check_contract.py <plan>
python scripts/verify_release.py     # SKIP is exit 2, not a pass
python scripts/dependency_surface.py
```

`check_merge.py` may be run freely inside your own worktree. Only serialise it
when two agents share a tree, which they should not.

**The gate does not inherit `PYTEST_ADDOPTS`, and a worker count goes on its own
command line** (`-n 8`, above). It used to inherit it, and a `--co` left in a
shell made it print `[PASS] pytest` over a committed `assert 1 == 2` — seven of
seven green, exit 0, in 16.4 seconds. It now reads pytest's JUnit report and
prints how many tests ran; a run that cannot say is a failure. `PYTHONPATH` is
still inherited, deliberately: it is what points a child at *this* checkout.

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

## The status line

`scripts/statusline.py`. Wire it up in your own untracked `.claude/settings.json`:

```json
{"statusLine": {"type": "command", "command": "python scripts/statusline.py"}}
```

`python3` on macOS if `python` is not on PATH. The path is relative to the repo
root, so it resolves in every worktree without editing.

It answers the two questions that have actually cost time here: **which of the
seventy-odd worktrees am I in**, and **how far behind `main` is this branch**.
Sample, from a reviewer sitting detached:

```
mk-c14b-review | on detached@e2b0614 | clean | 24 behind main
```

Behind-count is measured against the **local** `main`, not a remote: `main` lives
in a sibling worktree and moves several times an hour while the remote is pushed
rarely, so the local number is the one that predicts a painful merge. It is
printed only when non-zero — a field that says `0 behind` every render is a field
the eye learns to skip.

**ASCII only, and stdout forced to UTF-8.** The first version used a branch glyph
and died with `UnicodeEncodeError` on a Windows console under `cp1252` — the same
class of defect a terminal audit had just found in this project's own renderer. A
status line that raises on one machine's code page takes the whole render down
with it.

There is an optional `.claude/last-gate.json` field (`{"ok": true, "tests": N,
"at": "<sha>"}`). Nothing writes it today and the line simply omits it — an
absence must not render as a measurement, including in our own tooling. When the
recorded sha does not match `HEAD` it renders as `(stale)` rather than as
current, because a cached result with no commit attached cannot be told from a
fresh one.
