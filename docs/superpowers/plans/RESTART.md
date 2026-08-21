# Restarting the report rebuild

Written 2026-08-21. Everything here is recoverable from the repo; this file
exists so the next session does not have to re-derive it.

## The one-line restart

Open a session in `migration-kit` and say:

> Read `docs/superpowers/plans/RESTART.md`, then continue the chunk pipeline.

That is the whole prompt. Do not paste history, do not re-explain the design —
it is all written down, and re-explaining it is the single biggest waste
available.

## What the work is

Rebuilding `report.py`'s verdict document so it renders a *series* rather than a
single comparison. The design is `docs/superpowers/specs/2026-08-21-migkit-report-design.md`.
The build is `docs/superpowers/plans/2026-08-21-migkit-report-plan.md` — 18
chunks, each with a contract precise enough that a tester can write tests from
it without reading the implementation.

**Read only the chunk you are on.** The plan is 87KB. Reading it whole costs more
than the chunk is worth. `grep -n '^#### C' <plan>` gives the line numbers.

**Read the `Revisions, 2026-08-21` section at the end of the plan before
anything else.** It is the delta, it is short, and it supersedes the contracts
above it in five places.

## Where things stand

| Branch | Contains | State |
|---|---|---|
| `main` | spec, plan, revisions, both `PROGRESS.md` records | clean, pushed |
| `chunk/c1-impl` | `series.py` — `RunPoint`, `run_point` | reviewed; fix pass applied |
| `chunk/c1-test` | 48 blind tests | merged into c1-impl |
| `chunk/c2-impl` | `read_series` | see the session's last report |
| `chunk/c2-test` | C2's blind tests | as above |
| `chunk/c15-impl` | 96-item showcase golden set + generator | reviewed; **blocked, see below** |
| `chunk/c15-test` | 12 blind tests | merged into c15-impl |
| `chunk/c12-impl`, `chunk/c12-test` | empty | agents stopped 1 min in; re-dispatch from scratch |
| `fix/relative-work-dir` | the `.migkit\.migkit` fix, 3 tests, 1110 passing | ready to merge |

Worktrees live at `C:\Users\ewehm\repos\mk-*`. `git worktree list` from the main
checkout enumerates them. They persist across sessions; do not recreate them.

## Do these first, in this order

1. **Merge `fix/relative-work-dir`.** Independent, tested, and it fixes a defect
   reachable with no flags at all: `DEFAULT_DIR` is relative, so a plain
   `migkit run` followed by `migkit report .migkit` looked in `.migkit\.migkit`.
2. **Finish C1 and C2** — verify the fix pass, rebase C2 onto it, run both
   suites, merge to main.
3. **C15's revision pass — before C16 or C17 run.** The reviewer found five of
   sixteen `refusal` items are not refusable (`refuse-04`, `-06`, `-07`, `-14`,
   `-15`), in the dimension the entire showcase narrative collapses. Also: pin
   the content hash (one line, converts ten silent mutations to loud), assert
   the six tag names, and correct `__init__.py`'s claim that the package ships
   three data files — it now ships four. **The sequencing is the point:** today
   an edit is a commit; after C17 it is a full 56-run re-seed.
4. **Then the critical path**: C3, then C8-C10, then C13-C14.

Off the path, pick up when convenient: C11, C12, C16-C18.

## The dispatch pattern

Four agents per chunk, none sharing context:

1. **Implementer** — writes code, no tests. Worktree branched from the chunk's base.
2. **Tester** — writes tests, from the contract only, in a worktree branched from
   *before* the implementation, so the code is physically absent. This is
   structural, not an instruction an agent can ignore.
3. *(merge the tests onto the implementation and run them yourself)*
4. **Reviewer** — sees both. Ask it for mutation testing.
5. **Fix pass** — acts on the review; may edit both.

Implementer and tester run **in parallel**. The reviewer is where most of the
value has come from — three of three reviews found defects both other roles
missed, and mutation testing found holes a green suite hid.

## The preamble every agent brief needs

These traps have each bitten more than once. Paste them in rather than letting
an agent rediscover them.

**The editable install shadows every worktree.** `model_migration_kit` resolves
to the main checkout, so a new module in a worktree is invisible and an edited
one is silently the wrong copy. Every command must be:

```
PYTHONPATH=<worktree>\src C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe -m pytest <worktree>\tests\... -q
```

and every agent must print `module.__file__` and confirm the path is inside its
worktree before believing a result. One agent reported a false green from this;
two more designed around it unprompted. **The plan's own Done blocks are wrong
about this** — R4 records the correction, the individual chunk contracts do not.

**A skip is exit 2.** `verify_release.py` scores a SKIPPED check as failure so a
gate cannot mistake absence for success. A check that silently passes when its
tool is missing is worth less than no check.

**Ask the tester to check its suite against an inert stub.** Every tester that
did found tests of its own that passed against a do-nothing implementation — one
found 24. This is the highest-value instruction in the whole pattern.

**Ask the reviewer to mutate a copy and report survivors.** 15 of 44 survived on
C1, 13 of 20 on C15, including the contract's own named subtlety.

**`migkit demo --work-dir` needs an absolute path** until `fix/relative-work-dir`
merges.

## What has already been proven, so nobody re-investigates

- **The dimension matrix does not need the judged artifacts.** `judge.verdict.input`
  is the verbatim golden-set input; the join is exact, 120/120 verdicts, 24/24
  cells matching. R1 has the detail. C8's original contract is wrong.
- **A genuine REVIEW is seedable with a plain `Mapping` FakeAdapter** — no
  callable, no per-draw variation. The REVIEW band at n=200 is seven completions
  wide. All three verdicts come from one parameter set with only the candidate's
  script differing, so the comparability guard is never approached. Avoid k=180
  exactly (`runs_needed` returns `None` there). Rule 4 is a trap for a timeline.
- **Thresholds and identities are recorded**, twice over, including at the point
  of measurement — the historical floor can be drawn from the gate that applied it.

## Two decisions that are settled

**A dimension cell counts completions, not items.** `min_n = 20` completions. The
unit argument is decisive: C8 returns judge verdict records, one per completion,
so a `min_n` in items would compare against a number that is not in items.

**Caveat found late and not yet fixed:** the plan's justification for that rule is
arithmetically false. It says "a 4-item tag at 20 completions is not cleared" —
4 x 5 is exactly 20, and `20 < 20` is `False`, so it clears. The effective floor
is four items. Either raise `MIN_N_FOR_A_VERDICT` or fix the sentence; do not
ship the justification as written.

**`RunPoint` field names** were changed after review: `completions_*` became
`judged_*`, `failures_*` became `judge_failures_*`, and `floor_source` was added.
Gate `failures` means the judge failed the completion; the report's identically
named row means the adapter errored — 15 versus 0 on the demo run.

## What wastes tokens

- Reading the whole plan instead of one chunk.
- Re-deriving state that is in this file or in `PROGRESS.md`.
- Dispatching off-critical-path work while a contract upstream is unsettled. It
  does not shorten the project; it multiplies what must be re-coordinated when a
  review changes a field name. That happened here: C1's review demanded renames
  after C2 had already started on the old ones.
- Letting an agent rediscover a trap from the preamble above.
- Running the full 1110-test suite when one file's tests answer the question.
