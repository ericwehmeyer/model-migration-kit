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
above it in seven places -- R8 is the eighth entry and is an open question, not a
ruling.

## Where things stand

**C1, C2 and C15 are merged to main and green.** 1228 tests, ruff clean.
`series.py` (`RunPoint`, `run_point`, `read_series`), `evidence.py`
(`stream_records`, `resolve_evidence`), the relative work-dir fix, and the
96-item synthetic showcase golden set with its generator are all in.

C15 merged after its revision pass, which is worth knowing about because the
finding generalises: five of its sixteen `refusal` items **could not be refused**.
Each referred to something the prompt never supplied — a support thread, a rubric
"you were given", tokens "shown in this conversation", "this set", a medical
reason stated elsewhere. The correct single-turn answer to all five is "you have
not given me that", which a refusal rubric scores as a *failure to refuse*. The
rule now written into the generator: the thing being asked for must be present in
the item, and the whole of the requested output must be the harm. An item that can
be half-answered grades as partial credit, and the dimension the showcase
collapses on cannot be built out of partial credit.

The set is now pinned by content hash, verified by mutation: changing `SYN-1204`
to `SYN-1205`, one character, leaves all thirteen property tests green and fails
the pin alone. **Re-pin deliberately, never to make the suite green** — after C17
an edit here is a 56-run re-seed, not a commit.

| Branch | Contains | State |
|---|---|---|
| `main` | spec, plan, R1-R8, C1, C2, C15, work-dir fix | green, pushed |
| `chunk/c3-impl` | C3 implementation (`b90db16`) | done, awaiting its tests |
| `chunk/c3-test` | C3's blind tests | in flight |
| `chunk/c12-impl`, `chunk/c12-test` | the interval bar | in flight |
| `chunk/c13-impl`, `chunk/c13-test` | the timeline | in flight |
| `chunk/c1-*`, `chunk/c2-*`, `chunk/c15-*`, `fix/relative-work-dir` | merged | can be deleted |

Worktrees are under `repos\mk-*`; `git worktree list` enumerates them. They
persist across sessions, so do not recreate them — reset an idle one to `main`
instead.

## Do these next, in this order

1. **Land C3, C12 and C13**: merge each tester's file onto its implementer's
   branch, run it yourself, then dispatch a reviewer that sees both and is asked
   for mutation testing. C12 and C13 both touch `report.py` and
   `tests/test_report.py`; they were given disjoint insertion points (C12 before
   `_TEMPLATE_NAME`, C13 at EOF) so the merge is mechanical, but both testers
   append to the same test file and that conflict is real, trivial, and yours.
2. **Settle R8 at C3's review.** The headline verdict and `series[-1].verdict`
   disagree on a `C1 C2 V1` log. It is unreachable today and the edge table asks
   for agreement anyway. Decide, and if the answer is "documented limitation", put
   it in `report.py`'s docstring where a person debugging a disagreeing banner
   will find it.
3. **Then C8-C10, then C14.** C8's contract is superseded by R1 — build the matrix
   from `judge.verdict` plus the golden set, not from the judged artifacts. C10
   wires the matrix into `ReportModel` and therefore **cannot** run beside C3; see
   below.

Off the path, pick up when convenient: C11, C16-C18.

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

### When it is safe to go wide

Width is not limited by worktrees — those already solved file collisions. It is
limited by **vocabulary**. The one time this project got burned, C1's review
demanded field renames (`completions_*` became `judged_*`) *after* C2 had started
typing the old ones. Nothing collided; the work simply had to be redone.

Three rules, in the order they bite:

1. **Every name a chunk will type must already be through review** — not merely
   merged. Merged-but-unreviewed is the trap, because review is where the renames
   come from. Three of three reviews so far demanded changes.
2. **Never run a producer beside its consumer.** C3 defines
   `ReportModel.series`; C10 wires the matrix into `ReportModel`. Sequential, no
   matter how many worktrees are free.
3. **Pure functions are the wide lane.** C12 takes floats and returns SVG; C13
   takes `Sequence[RunPoint]` and reads only fields that are reviewed, merged and
   post-rename. Verify that in `series.py` rather than trusting the plan — the
   plan predates the renames. Both went out beside C3 at zero coordination cost.

And a fourth that is about you, not the code: **the orchestrator is the cap.** You
merge each tester's file onto each implementer's branch and run it. Three chunks
in flight is about the width at which each still gets a real review, and the
review is where the value has been.

**Before dispatching a pair, read the contract for a self-contradiction and settle
it yourself.** C13's said `-> str` in the signature and "returns the count" in the
prose; dispatched as written, the implementer and the tester would each have
picked a different reading and neither would have been wrong. R6 and R7 are what
that looks like written down: decide, put it in *both* briefs verbatim, and
record it as a revision.

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
