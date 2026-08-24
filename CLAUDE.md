# Working in this repository

Rules that apply to every agent here, whatever the task. They exist because
each one has already cost this project real time.

## The interpreter, and the `.pth` trap

**There is no venv inside the worktrees.** The only interpreter is:

```
C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe
```

A bare `python` on PATH is a bare 3.14 with no pytest — that fails loudly and is
harmless. **The dangerous half is silent:** the editable install's `.pth` file
hardcodes the *main checkout's* `src`, so running that venv's python from any
worktree imports **the main checkout's code**, not yours. No warning, no error,
wrong answers.

`conftest.py` corrects this for pytest only. For anything ad-hoc, both halves
are required:

```bash
PYTHONPATH="C:/Users/ewehm/repos/<your-worktree>/src" \
  /c/Users/ewehm/repos/migration-kit/.venv/Scripts/python.exe -c '...'
```

**Print `model_migration_kit.__file__` before trusting any ad-hoc run.** If it
does not name your worktree, everything you just measured is about someone
else's code. The symptom is a result identical to the before-state, which reads
exactly like "my change did nothing".

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
- `tests/test_report.py` takes **~6 minutes**. That is normal, not a hang.

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
