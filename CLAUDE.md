# Working in this repository

Rules that apply to every agent here, whatever the task. They exist because
each one has already cost this project real time.

**Two machines run this project.** The pipeline machine is Windows; a second
operator works from a macOS checkout. Every fact below that differs between them
is labelled. Nothing in this file may name a path that exists on only one of them
without saying so — that mistake cost a full session on 2026-08-24, because an
agent on the wrong box cannot tell a stale instruction from a broken tree.

## The interpreter

**Resolve `$PY` once, at the top of your session, and use it for everything** —
gates, probes, pytest, one-off `python -c`. Never a bare `python`.

```bash
# Windows (the pipeline machine)
PY="C:/Users/ewehm/repos/migration-kit/.venv/Scripts/python.exe"
# macOS / Linux (a second operator's checkout, which has its own .venv)
PY="$(git rev-parse --show-toplevel)/.venv/bin/python"
```

**A bare `python` is the wrong interpreter on at least one of these machines, and
on macOS it fails in both directions.** There it is Anaconda 3.9.13 — below this
project's `requires-python = ">=3.10"`, and it cannot import the package at all:

- `python scripts/check_merge.py` reports a **false red** — 16 collection errors
  and "this merge is not green" — on a tree that is green.
- bare `pytest` reports a **false green**: it collects **247 of 2206** tests with
  16 collection errors, and a small passing run looks like a clean one.
- `ruff` is not on `PATH` at all; only `$PY -m ruff` / `.venv/bin/ruff` works.

The false green is the dangerous one. Check what you are running before you
believe a result:

```bash
$PY -c "import model_migration_kit, sys; print(model_migration_kit.__file__); print(sys.version)"
```

### The `.pth` trap

The editable install's `.pth` hardcodes one checkout's `src`, so running from a
*second* worktree silently imports the *first* one's code — no warning, no error,
wrong answers, and green tests that tested nothing. It caught the orchestrator and
six agents.

`scripts/worktree_path.py` fixes it by resolving the package from your **current
working directory's** worktree. **It is per-venv and is not installed by default.
Check before you trust it, in every venv you use:**

```bash
$PY scripts/worktree_path.py --status      # want: "hook module present: yes"
$PY scripts/worktree_path.py --uninstall   # restores the original
```

- **Windows venv: installed** (2026-08-24), verified from four worktrees at once.
- **macOS venv: NOT installed** as of 2026-08-24. Harmless while that checkout has
  a single worktree; a live hazard the moment it has two.

`pytest` is safe either way — the repo-root `conftest.py` redirects `sys.path` and
`PYTHONPATH` from `__file__`. The gap is bare `python -c`. Setting `PYTHONPATH`
explicitly still works and still wins, and printing
`model_migration_kit.__file__` is still cheap and still worth doing when a result
surprises you.

## Running the suite

`pytest-xdist` is installed. All 12 configurations tested return identical
pass/fail, and the suite is order-independent (verified across two shuffled seeds
via `scripts/audit/shuffle_order.py`; note `pytest-randomly` is **not** installed,
so `-p no:randomly` is a no-op and proves nothing).

Measured, same suite, 2206 tests:

| | Windows (16 cores) | macOS (M1 Max, 8P+2E) |
|---|---|---|
| serial | ~136 s | **19 s** |
| `-n 8` | ~97 s | **13 s** |
| `tests/test_report.py` (435 tests) | ~30 s | **5.6 s** |

**Check the load and the core count before choosing a worker count.** Wall-clock
here is dominated by *other agents*, not by the code: `tests/test_report.py`
measured 365 s with five agents running and 27 s minutes later on identical code.
Use roughly half the logical cores when the board is busy. On macOS, RAM is not
the constraint (64 GB, no swap, ~2 GB per test tree) — **cores are**: 10 logical
cores support 3-4 concurrent testing agents, and `-n 8` buys only ~6 s over serial
because ~5.6 s of it is per-worker `import scipy.stats`.

## One agent, one worktree

Never two agents in one working tree — they overwrite each other's edits and
each other's restores, and a `git add -A` from anyone can capture whatever is
live at that moment. Reviewers included. Put worktrees in a **sibling** directory
of the main checkout, never inside it:

```bash
WT="$(dirname "$(git rev-parse --show-toplevel)")"
git worktree add --detach "$WT/mk-<chunk>-review" <sha>
```

Detached, so several can sit at one commit without fighting over a branch.
**A new worktree shares its parent's venv, so re-check `worktree_path.py
--status` inside it.**

**Never work directly in the main checkout** — the tree `git rev-parse
--show-toplevel` returns when you have not created a worktree. It is usually on
some agent's branch. On Windows that is `C:\Users\ewehm\repos\migration-kit`, and
`mk-main` is the worktree for `main`.

## Your scratchpad is shared. Namespace it.

Worktrees isolate the repo. They do **not** isolate the session scratchpad —
every agent of a pair defaults to the same directory, and a common filename
(`probe.py`, `check.py`, `fixture.py`) will collide.

**Put your files in a subdirectory named for your role and chunk**, e.g.
`scratchpad/c14b-impl/`. Never write a bare filename into the scratchpad root.
When a dispatcher fans out several agents at once, give them **sibling**
directories rather than nested ones — nesting one agent's area inside another's
recreates the collision by construction.

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
$PY scripts/check_merge.py        # seven checks; the merge gate
$PY scripts/check_contract.py <plan>
$PY scripts/verify_release.py     # SKIP is exit 2, not a pass
$PY scripts/dependency_surface.py
```

`check_merge.py` may be run freely inside your own worktree. Only serialise it
when two agents share a tree, which they should not.

**What CI does and does not run.** `check_merge.py` and `check_contract.py` are in
no workflow — four of `check_merge`'s seven checks (conflict markers, every file
parses, no shadowed top-level names, `__all__` completeness) run **nowhere** in CI,
so the merge gate is honour-system. `verify_release.py` runs only in
`publish.yml`, never on a PR. Conversely CI runs two gates this file does not
name: a network-blocked `pytest -p netguard`, and a cold-venv 120-second
`migkit demo` check. CI covers ubuntu + windows on 3.10-3.13; **there is no macOS
runner**, so a green suite on the Mac is evidence CI never collects.

**Two gates cannot be fully run outside the Windows box**, and a skip there is not
a pass:

- `verify_release.py` needs `build` and `twine`; without them it exits **2** with
  13 of 15 checks skipped. The wrong interpreter makes it *quieter*, not redder —
  14 skips at the same exit code — so watch the skip count, not just `$?`.
- `check_contract.py` resolves citations against sibling *source* checkouts. Where
  `opik-rigor` is installed as a wheel rather than checked out next door, every
  citation into it reads as **wrong** rather than as **unverifiable**. It also
  accepts `\`-separated citations, which resolve on Windows and fail elsewhere.

## Known and deliberately deferred — do not "fix" these

- **`ruff format --check tests/test_report.py` fails on `main`** with ~60
  pre-existing hunks (229 lines) and never has passed. Do not report it, do not
  reformat.
- **Repo-wide format drift**: the tree is formatted at 88, `pyproject.toml` says
  100. **33 tracked Python files** (`src` 10, `tests` 18, `scripts` 5), plus 4
  Markdown files since ruff 0.16 formats fenced code blocks. `test_report.py` is
  one of the 33, not a special case. It gets its own chunk when the tree is quiet.
- `tests/test_report.py` holds **435 tests**. If you see six minutes for it, that
  is **CPU contention from other agents**, not the file. Measured 365 s once with
  five agents running and 27 s with the same code minutes later.

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

**Test the seam, not only the two sides of it.** A value can be produced
correctly, asserted correctly on the producer, and never reach the page — and a
suite that tests `model.x` and separately tests the rendered document will not
notice. Four fields sat computed and unread this way. When you add a field a
reader is meant to see, add one test that renders the document and finds it.

## If the contract is wrong

Report it and **stop** — do not pick a reading and proceed. Four agents have done
exactly that and all four were right; one refused a ruling that a second ruling
in the same brief had falsified.
