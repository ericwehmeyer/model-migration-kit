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
anything else.** It is the delta and it supersedes the contracts above it in
eleven places. R8 is closed by C19. R9 through R11 are the newest and the least
settled: R9's arithmetic has already needed correcting twice, both times by an
agent that checked rather than assumed, so treat that one as suspect.

## Where things stand

**C1, C2, C3, C13 and C15 are merged to main and green.** 1274 tests, ruff clean,
`main` at `647efcf`. `series.py` (`RunPoint`, `run_point`, `read_series`,
`SeriesBuilder`, `parse_created`), `evidence.py` (`stream_records`,
`resolve_evidence`), the timeline chart, the relative work-dir fix, and the
96-item synthetic showcase golden set with its generator are all in.

**Four chunks are written, merged onto their own branches, green, and in
review.** None has landed on main yet.

| Branch | Contains | State |
|---|---|---|
| `main` | spec, plan, R1-R11, C1, C2, C3, C13, C15 | green, 1274 passed |
| `chunk/c8-impl` | per-tag counts, both halves, 41 passed | in review |
| `chunk/c9-impl` | the cell and its two floors, both halves, 40 passed | in review |
| `chunk/c19-impl` | verdict pairing, both halves, 1265 passed | in review |
| `chunk/c20-impl` | the scanner narrowing, both halves, 1315 passed | in review |
| `chunk/c12-impl` | the interval bar, 238 passed | mutation re-verification |
| `fix/isolate-release-probe` | `-E` plus a corrected docstring, and the conftest | in flight |

C15 merged after its revision pass, which is worth knowing about because the
finding generalises: five of its sixteen `refusal` items **could not be refused**.
Each referred to something the prompt never supplied. The rule now written into
the generator: the thing being asked for must be present in the item, and the
whole of the requested output must be the harm.

The set is pinned by content hash, verified by mutation. **Re-pin deliberately,
never to make the suite green** -- after C17 an edit here is a 56-run re-seed.

Worktrees are under `repos\mk-*`; `git worktree list` enumerates them. They
persist across sessions, so do not recreate them -- reset an idle one instead.

## Do these next, in this order

1. **Land the four reviews.** Each reviewer sees both halves and was asked for
   mutation testing. C8's was additionally asked to rule on **seven ambiguities**
   its tester enumerated, because three of them decide things C10 will be written
   against. C9's was asked to rule on whether `needed`/`needed_unit` should become
   `needed_items`/`needed_completions` -- a change that is cheap now and expensive
   after C10 and C14 exist.
2. **Merge C8, C9, C19, C20**, in that order. C8 and C9 share one new module,
   `dimensions.py`, split top and bottom. **Check `__all__` by hand** -- see R11.5,
   the one collision in that arrangement that produces no conflict marker.
3. **Then C10, then C14.** C10 wires the matrix into `ReportModel` and therefore
   cannot run beside anything editing `report.py`. Its contract is amended -- see
   `### C10 (amended)` -- because R1 deleted one of its two decline reasons, and
   its named test must now assert the *opposite* of what the original says.

Off the path, pick up when convenient: C4-C7, C11, C16-C18.

**Two shipped defects are recorded in R5 and still unfixed in 0.1.1**: the
degraded render that prints "0 items" beside a table reading 55/60, and report
degradation never reaching the exit code, so a stripped render and a complete one
are indistinguishable to a pipeline. Both live in `report.py`, so they wait for it
to go quiet.

**One pre-existing hole, found by both C20 agents**: `_URL_FN_RE` matches only
`url(`, so `src()` and `image-set()` fetch remotely and score zero violations, in
a `<style>` block and in a `style=` attribute alike. Verified pre-existing at
`559e521`. Worth a chunk; note that CSS is the one place C20's "no script runs"
argument does not help.

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

### Time-bounding the agents

Two agents once sat dead for 5.5 hours and were only found by going to look. Two
things fix that, and they catch different failures.

**A watchdog on the right signal.** Poll the mtime of
`~/.claude/projects/<cwd>/<session>/subagents/agent-<id>.jsonl` -- the live
transcript, which grows on every tool round. The `.meta.json` beside it carries
the agent's description, so an alert can name it rather than print a hex id. Two
tiers, 20 minutes quiet to look and 45 to stop and salvage, each firing once per
agent, plus a warm-up pass that marks everything already stale at startup in
**every** tier and announces none of it. Marking only the lower tier lets a
finished agent cross the upper line later and announce itself as newly stalled.

Do **not** watch `tasks/<id>.output`. Those are zero-length placeholders and
their mtime does not track tool activity: one agent was demonstrably working at
04:23 with an `.output` untouched since 04:02. A watchdog on that signal reports
health it cannot see, which is worse than no watchdog.

**A self-imposed bound in every brief.** "If one problem resists three genuine
attempts, commit what you have, write down the blocker, and return." The watchdog
catches agents that hang; this catches agents that grind, and grinding is the
more common and more expensive failure.

And say **commit early and commit often** in every brief. Three agents were lost
in one session with uncommitted work. An interrupted agent with commits is a
five-minute salvage; without them it is a total loss.

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
- **The release gate is not vulnerable to `PYTHONPATH`, and this was checked
  properly.** `check_demo_data_importable` runs its probe with `-S`, which does
  not ignore `PYTHONPATH`, and `run()` inherits the environment, so it looks
  exposed. It is not: the package ships an `__init__.py`, so it is a regular
  package rather than a namespace one, the first `sys.path` hit wins, and the
  probe's own `sys.path.insert(0, extract)` guarantees that hit is the wheel. A
  `__path__` assertion catches any leak independently and fails with an accurate
  message. Verified against a doctored wheel across a 2x2 matrix. **The docstring
  arguing for `-S` is stale** -- it says the package is a namespace package, which
  it no longer is -- and that stale paragraph is what made this look like a hole.
- **Thresholds and identities are recorded**, twice over, including at the point
  of measurement — the historical floor can be drawn from the gate that applied it.

## Two decisions that are settled

**A dimension cell counts completions, not items.** `min_n = 20` completions. The
unit argument is decisive: C8 returns judge verdict records, one per completion,
so a `min_n` in items would compare against a number that is not in items.

**That caveat is now settled by R9, and the answer was neither of the two options
this file used to offer.** The justification was arithmetically false: 4 x 5 is
exactly 20 and `20 < 20` is `False`, so a 4-item tag clears. Raising the
completions number does not fix it either -- at `n_per_item=10` a 4-item tag
clears a floor of 40 just as easily, because five draws of one item are five
readings of one question. R9 adds a **second, independent floor in items**
(`MIN_ITEMS_FOR_A_VERDICT = 10`) and leaves R3's completions floor intact. R11.6
then corrects R9 in turn: the completions floor can only bind at one draw per
item, so the two do not do comparable work.

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
