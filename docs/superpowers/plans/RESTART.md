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

**The ordering mistake is fixed and the document renders.** That was this page's
headline problem for three sessions: C14, the template that renders everything,
was scheduled after all nine of its inputs, so ten chunks landed out of sight and
`migkit demo` produced the same document it produced before any of the work
started. C14a is merged. The two SVG helpers draw, and every chunk from here
lands visibly instead of accumulating where nobody can steer on it.

**What has NOT changed is the reason that mistake mattered**, so keep the rule
that came out of it: **order the chunks so the artifact moves.** A chunk whose
output nobody can see is a chunk nobody can correct, and this project spent ten
of them finding that out.

Merged, each through all four roles -- blind pair, mutation-testing review, fix
pass: **C1, C2, C3, C4, C8, C9, C11, C12, C13, C14a, C15, C19, C20, C21**.

**Seven of seven reviews found defects both other roles missed**, and three found
defects in code that had already been merged. Budget for a fix pass on every
chunk; the review is not the end of the chunk, it is the middle.

The measured before-and-after, and what still does not render, are two sections
down. Read those numbers rather than this prose if the two ever disagree -- and
if they do, that is a defect in this file and worth fixing on sight. This
paragraph replaced a preamble that had gone stale enough to contradict a table
forty lines below it.

| Branch | Contains | State |
|---|---|---|
| `main` | C1-C17, C19-C21, C22a part one, R1-R27 | all seven gates green, **2123 passed** |
| `chunk/c10-fix` | R27's eighteen survivors | in flight (stage 5/5) |
| `chunk/c18-impl` | the synthetic band | in flight (stage 1/5) |
| `chunk/c22a-impl` | `spot_check` on the model | in flight -- **resumed**, not re-dispatched |
| `chunk/c22a-test` | 15 blind tests for C22a | **waiting to merge** with part two |

Updated 2026-08-24, main at `347f4e6`. **17 of 22 chunks merged.** Remaining:
C10's fix, C14's remaining elements, C18, C22a part two, C22b.
`chunk/c10-impl` and `chunk/c10-test` (no `2`) are **dead**; ignore them.

**One process debt, taken deliberately.** C22a part one was merged **without its
blind tests**, because several target `spot_check`, which does not exist yet.
`chunk/c22a-test` holds all 15 -- merge them the moment part two lands. Until
then the `candidates` wiring is covered only by the existing suite. I verified
the two riskiest claims by hand (`None` passes through uncoerced, no `excluded`
field exists), which is not the same as having the blind pair.

**Three consecutive blind pairs produced zero disagreement** -- C7 (33 tests),
C10 (22), C6 (31) -- and it still meant nothing about defect-freedom: C10's
review then found **18** surviving mutants and C7's found **11**. Agreement
between two roles given one contract is not independent evidence.

### The watchdog was right and I was wrong -- a correction

An earlier version of this section said the stall monitor "raises false alarms"
and told the next session to distrust it. **That was wrong, and it is worth
leaving the correction visible.**

The monitor fired seven times naming agents that had already completed. Every
one of those alerts traced to **my own bookkeeping**, not to the tool. The
watchdog is `scratchpad/agent_watchdog.sh`; it reads liveness from transcript
mtime and completion from `scratchpad/completed_agents.txt`, **a file the
orchestrator writes**. The rule was written down here in as many words -- *one
line per completion, including after a resume and for dead agents* -- and then:

- four agents (C6's pair, C22a's pair) were **never recorded at all**;
- two more were recorded as `DIED`, then **resumed and completed
  successfully**, and never re-recorded.

So the file said those agents were dead or unfinished. The monitor read it and
reported exactly that. It was correct on every alert.

**The lesson is not about watchdogs.** A tool fed by a side-channel the operator
must maintain by hand will silently degrade the moment the operator gets busy --
and the busiest moments are precisely when its output matters. It then produces
confident wrong signals, one of which here escalated to *"stop it and salvage
its commits"* against work that had been merged for ninety minutes. Acting on
that would have been destructive on the tool's advice and my data.

It is also the third instance this session of one shape: **a conclusion drawn
from a source nobody re-checked.** R25 (a mechanism inferred, not run), R26 (a
field's keys assumed from a function's keys), and this. In all three the output
looked authoritative and the input was never verified.

The monitor is currently **stopped**, for a reason unrelated to the above: the
three genuine agent deaths this session all arrived as ordinary `failed`
task-notifications from the harness, with the stall reason attached. The
watchdog's unique value -- catching an agent that hangs without notifying -- was
never actually demonstrated. If you restart it, **either maintain
`completed_agents.txt` on every single completion or derive completion from
something the orchestrator cannot forget.** A half-maintained signal is worse
than none.

## Do these next, in this order

1. **Land the five in flight** -- C22a's tester, C7's fix, C17, C11's subject
   follow-up, C10's reviewer.
2. **Merge `chunk/c22a-impl`'s `candidates` half.** It is complete and green on
   its own. Its `spot_check` half is blocked on `chunk/c11-subject` (R26.4).
3. **C22a part two** -- `spot_check` on the model, once C11's subject lands.
   Rulings are made: `judges[0]` and refuse rather than aggregate (R26.3), the
   **candidate** side, and the subject named in the producer's own sentence.
4. **C14's remaining elements** for whatever is then on the model.
   **Two things to know before scheduling the spot-check element:** its section
   will be **empty on the bundled demo** whatever we do -- 12 items and `k=12`
   means `spot_check` correctly returns `None` -- and per R23.2 a report with no
   candidate table cannot say *why*, so the excluded list's empty state must say
   runs may have been excluded without naming them.
5. **C22b** -- `trend` and `parameter_strip` (after C7's fix lands R21.5's
   lineage caveat) and `multiplicity` (C6 is merged; R25.4 lists four contract
   points still unruled, cheap now and expensive once rendered).
6. **C18**, and the deferred repo-wide `ruff format` drift (tree at 88,
   `pyproject.toml` says 100, ~26 files). Its own chunk, when the tree is quiet.

### Two errors of mine, both caught by agents, both the same shape

Recorded because the pattern is the useful part, not the individual mistakes.

**R17.1** explained a real worked table with a mechanism I had inferred from
reading code rather than running it. **R23.1** claimed `ReportModel.item_counts`
carried three keys it does not carry, in a section whose own words were "checked
rather than assumed" -- I had grepped, found a *function* reading those key
names, and concluded the *field* had them. One hour apart, the second
immediately after writing down the lesson from the first.

> **When a revision claims something was checked, it must carry the output that
> checked it.** A sentence saying "verified" is not evidence; a pasted
> `KeyError` or printed dict is.

Both were caught by agents, both because the agent **needed the actual value to
do its job** and so could not take the sentence on trust. That is an argument
for briefs that make an agent derive a number rather than accept one.

### The environment is fixed at the source -- follow `CLAUDE.md`, not old habits

Two changes landed 2026-08-24 and they make this file's older advice obsolete.

**The `.pth` trap is gone.** `scripts/worktree_path.py` is installed into the
shared venv as an import hook, so a bare `python -c` from **any** worktree
imports that worktree's code with **no `PYTHONPATH`**. Verified from four
worktrees simultaneously; outside any checkout it still falls back to the main
checkout, so nothing that worked before stopped working. `--uninstall` reverts.

This closed the failure that caught the orchestrator and six agents, whose
symptom was a result identical to the before-state -- indistinguishable from
"the merge did nothing".

> One bug was fixed before installing: `FALLBACK` was derived from `__file__`,
> which is right in `scripts/` and resolves to a nonexistent `<venv>/Lib/src`
> once the module lives in `site-packages`. It now bakes the path from the
> `.pth` it replaces, so the fallback cannot depend on which worktree happened
> to run `--install`.

**`pytest-xdist` is installed** and is in the `dev` extra so CI can honour a
`-n` without failing on an unknown flag. `-n 8` takes the suite from ~136 s to
~97 s. **Pick the worker count from the load**: wall-clock here is dominated by
other agents, not by the code, and four agents each running `-n 8` on 16 cores
make each other slower. `-n 4` when the board is busy.

The interpreter is still only in the main checkout:
`C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe`. A bare
`python` on PATH is a bare 3.14 with no pytest, which fails loudly and is
therefore harmless.

**`docs/superpowers/plans/METRICS.md` is where process measurements now go.** It
carries what each role costs (implementer ~110k tokens, reviewer ~150k, blind
tester ~169k, fix pass ~198k; a full chunk 600-650k), the evidence that **tokens
predict work and wall-clock does not**, and the orchestrator's error taxonomy.
Append to it; do not rewrite it.

### The merge map, measured rather than guessed

Run with `git merge-tree --write-tree --name-only <a> <b>` on 2026-08-24, so this
is what git actually says and not what the branches look like they should do:

| merge | result |
|---|---|
| `main` <- `chunk/c16-impl` | **clean** |
| `main` <- `chunk/c4-impl` | **clean** |
| `main` <- `chunk/c14a-impl` | `report.py` auto-merges; **`tests/test_report.py` conflicts** |
| `chunk/c4-impl` + `chunk/c11-impl` | **conflicts in `series.py` and `tests/test_series.py`** |

Both conflicts are two branches appending sections to one file, which is
mechanical. Two things make them cheaper:

- **C14a's is C21's fault, not C14a's.** C21 added section 20 to
  `tests/test_report.py` after C14a branched. Merge C14a's side first and
  re-append C21's section, rather than the reverse — C14a's tests are threaded
  through the file and C21's are one contiguous block.
- **C4 and C11 both add to `series.py`.** Merge **C4 first** (it is clean against
  `main`), then C11. C11's implementer already exploded `__all__` to one name per
  line with a magic trailing comma specifically so this merge would be
  mechanical; do not undo that.

**Deferred deliberately:** the repo-wide `ruff format` drift. Three separate
agents reported it independently — the tree is formatted at 88 while
`pyproject.toml` sets `line-length = 100`, and roughly 26 files are affected.
It looks like the ideal safe filler task and it is the opposite: a repo-wide
reformat with several chunks in flight conflicts with every one of them. Do it
when the tree is quiet, in its own chunk, touching nothing else.

## The C10 blocker, in full -- RESOLVED by C21, 2026-08-24

Kept because the shape of the problem is the useful part, and because it took
three sessions to state it clearly enough to solve.

`from_evidence` must build a per-tag matrix. The matrix joins a `judge.verdict`
to a golden-set item **by input text** (a verdict carries no `item_id`), so it
needs the golden set. The golden set's path lives in the `migkit.comparison`
payload, written *after* judging, so it is only in hand once the single
streaming pass has finished. Both ways out are closed by merged tests, and
neither may be weakened:

- **read the log twice** -> `tests/test_report.py::test_the_log_is_read_once_for_both_the_headline_and_the_series` counts opens and asserts exactly 1.
- **buffer the verdicts** -> `tests/test_evidence_scale.py::test_rebuilding_the_report_does_not_hold_the_log_either` asserts peak allocation stays flat in log size. A `judge.verdict` embeds the input; the fixture's inputs are unique 4 KB strings. `evidence.py` records the measurement behind it: an 86 MB log cost 502 MB extra resident.

**The digest was the answer, and it was measured before being accepted.** Both
guard tests are byte-identical to their pre-C21 versions and
`test_evidence_scale.py` is untouched. blake2b at **16** bytes, not the ~32 this
section guessed, and the cost is **per distinct input, not per verdict**: 317
bytes per entry, so 1000 items costs ~311 KiB at 1 draw and ~311 KiB at 50.
Flat in draws is the property; the 817x saving at 50 draws is a consequence of
it. Peak allocation reads 2.48 MB at 8 MB of log and 2.68 MB at 24 MB, against a
3.72 MB limit and an 8 MB ceiling -- two thirds of the ceiling unused.

**One caveat that was not in the original framing.** The bound holds for inputs
that *join*. The deferred phase cannot recognise an unjoinable input -- that is
what deferred means -- so a log of non-joining inputs grows linearly at 317
B/entry with no bound but the log, reaching the ceiling at roughly 18,000 such
verdicts. The docstring claiming "bounded by the golden set, not by the log" was
false and is corrected in the code.

**"Also unsettled: per-run or cumulative" is settled: per-run.** Proven over a
two-run log at both levels; the cumulative mutant fails with exactly the
irreconcilable pair, `(30, 60, 6)` vs `(30, 30, 6)`. The reasoning lives in
`DimensionTally.add`, in `ReportModel.dimension_counts`, and in the report-level
test docstrings -- in the code, as this section asked.

**What C21 did NOT do is deliver C10.** It delivers raw counts. The matrix --
cells, tag order, baseline against candidates, both floors, six ways to be
unavailable -- is still C10's work, and C10's contract had a second defect that
would have re-blocked it: it prescribes `dimension_counts(records, items, *,
judge)`, which cannot be called inside a single pass. **See R16 in the plan**
for the two-phase form that replaces it, and for two further corrections to
C10's contract. Do not dispatch C10 until C21's review lands.

## Four defects a reader found in the rendered report

Worth knowing because they are not in any chunk contract and they are what a
person actually complained about on first sight:

- **`extract-01` prints `98.10` five times**; `refuse-02` prints the same
  260-character paragraph five times. Repetition presented as evidence, and it
  hides the fact that matters — whether the draws agreed at all.
- **The finding is behind a closed disclosure triangle.** The run's most important
  result is that the candidate writes a fabricated data-breach notice on request
  and invents a refund figure no thread states. Both sit inside `<details>`.
- An absolute temp path is printed **eight times**, ~130 characters each.
- **Latency prints `0.000 / 0.000`** on scripted adapters — a row that can never
  say anything. `RunSummary.is_fake` already knows.

All four are in C14a's contract.

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
value has come from — **six of six** reviews have now found defects both other
roles missed, and mutation testing found holes a green suite hid.

### The fix pass must unfix itself

Added 2026-08-24, from C4's fix pass, which did this unprompted and should not
have had to. After implementing its rulings and killing the reviewer's mutants,
it **reverted each ruling one at a time and confirmed the suite went red for
each**.

Mutation testing asks *"does the suite notice a defect the reviewer invented?"*
The unfix asks the question that actually matters once a fix has landed: **"does
the suite notice if this fix is undone?"** A ruling can be implemented perfectly
and left completely unguarded — the code is right, every test passes, and the
next editor deletes it without one failure. That is how three of C4's eight
surviving mutants came to exist in the first place.

It costs one script and one suite run. **A ruling that survives its own reversal
was written down, not enforced.**

### Every worktree carries a stale copy of the plan, and agents read theirs

Found independently by C4's reviewer and C11's reviewer, 2026-08-24. A chunk
worktree is branched from `main` at dispatch time, so it holds the plan **as of
that moment**. Revisions written afterwards land on `main` and never reach it.

The concrete case: `chunk/c11-impl`'s plan ends at R13. Its C11 edge table still
reads `probability ≈ 0.351` and its worked sentence still reads `"34% of cases"`
— the two numbers R14.1 exists to strike. Both agents on that chunk got it right
anyway, but only because the corrected value was in their briefs. **That was a
courier working, not the document working**, and the next agent to open the
contract from inside a worktree gets the struck numbers with nothing to warn it.

Two things follow, and the second is the one that generalises:

1. **Put the plan path in every brief, pointing at the main checkout**, not at
   the worktree's copy. One line.
2. **A revision is not landed when it is committed to `main`.** It is landed when
   every reader of the thing it revises can see it. On this project the readers
   are agents in worktrees that will never pull, so the brief is the only channel
   that actually reaches them. Writing the revision and dispatching against the
   stale copy is the same failure as not writing it.

**The standing fix, applied 2026-08-24: banner the contract, not just the
revision.** A revision section at the end of an 87KB file does not reach a reader
who opens their own contract and stops — and "read only the chunk you are on",
four lines up this page, is what *tells* them to stop. So every amended contract
now carries a blockquote **immediately under its heading**, above its own text,
saying what was superseded and pointing at the revision. Six are in place: C4,
C5, C6, C7, C10, C11.

Keep doing this. When a revision changes a contract, the revision records *why*
and the banner makes sure the next reader sees *that*. A correction nobody reads
is indistinguishable from one nobody wrote — and this project has now produced
both a stale "OPEN" header that survived three days and a struck number that two
agents read and only dodged because a brief couriered the fix.

### The blindness leaks through the scratchpad, and what that cost

Found on C4, 2026-08-24. Worktrees isolate the repo. They do **not** isolate the
session scratchpad, and both agents of a pair default to the same directory. The
tester wrote its test file over the implementer's staging file, and the harness's
change notification put ~140 lines of the tester's file into the implementer's
context unrequested.

**Give every agent of a pair its own scratchpad subdirectory in its brief.** That
is the fix and it is one line per brief.

But the interesting part is what leaked, not that something did. The two agents
had **independently reached opposite readings** of the same paragraph — §4.4's
coverage flag, which the implementer read as across two runs and the tester read
as across the two sides of one comparison. Both readings are defensible; the
contract does not say; and my pre-dispatch ruling did not disambiguate it either,
which is my defect and the root cause.

That disagreement was not a problem. **It was the entire product of running a
blind pair** — two independent readings of an ambiguous contract, surfaced as a
conflict for the orchestrator to rule on. The leak did not merely contaminate the
implementer. It *destroyed the signal*, by letting the disagreement resolve itself
silently inside one agent instead of reaching me. The implementer capitulated to a
comment it should never have seen, changed a correct implementation to the other
reading, and the conflict very nearly disappeared into a commit message.

It survived only because the implementer volunteered the leak in its report. Two
lessons, and the second is the one worth keeping:

1. Isolate the scratchpad.
2. **Say in every brief: if you find yourself agreeing with something you were not
   supposed to see, stop and report it.** An agent that hides a leak costs a
   contract defect. An agent that reports one hands you the defect for free.

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

**Liveness and completion are two different questions and need two different
signals.** Built 2026-08-24 with only the first, and it false-alarmed on the
first agent to finish: a completed agent's transcript stops growing, so it
crosses the 20-minute line looking exactly like a hung one. Left alone that is
one false alarm per completed agent, which is a watchdog nobody reads by the
third chunk — the same failure as watching `.output`, arrived at from the other
side.

Two completion signals were tried and **both failed**; do not retry them:

- `tasks/<id>.output` being non-empty — still 0 bytes for agents that had
  completed and reported minutes earlier. RESTART's warning about `.output`
  covers completion as well as liveness.
- the last transcript line being an assistant message with no `tool_use` —
  identical for agents that had finished and agents still working.

What works is the one thing that is actually reliable: **the orchestrator is told
when an agent completes, so the orchestrator writes it down.** One line of
`<agent-id> <epoch>` appended to a file the watchdog reads. Store the timestamp,
not just the id, so an agent that is later resumed — its transcript moving again
*after* that time — is watched normally instead of being suppressed forever. That
case is real: C4's implementer was resumed to act on a ruling.

**Write the line every time, including after a resume.** The design above is
correct and it still produced a false alarm, because the completion line was
written *before* a resume and never again after it — so the second completion
looked like a revival that then went quiet. The watchdog was right; the
bookkeeping was late. Append the line in the same breath as reading the
completion, and append one **per completion**, not one per agent.

**A dead agent needs a line too.** Four agents were lost to an infrastructure
stall in one minute; without a line each, all four would have crossed the
45-minute mark and announced themselves as newly stalled, long after there was
anything left to salvage.

### Dispatch two at a time, and the second data point for it

2026-08-24: four agents went out in two back-to-back calls — the C5 pair and the
C7 pair. **All four died within about a minute of starting**, three with the
harness's own "no progress for 600s, stream watchdog did not recover" and one
with an API error. None had done any work: every worktree clean, no commits, no
uncommitted files. The two agents that had been running for half an hour were
untouched.

So the failure hits agents *at start-up*, and starting four at once is what met
it. This page already said "re-dispatch two at a time, not four" from an earlier
incident; that now has a second and sharper data point, and a better reason. It
is not about the orchestrator's attention — it is about the blast radius of one
bad minute.

**Re-dispatching cost nothing**, which is the useful half: with no commits and no
partial state there was nothing to reconcile and no way to be wrong about what
had been done. That is what "commit early and commit often" buys, arrived at
from the other end.

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

**The editable install used to shadow every worktree, and a `conftest.py` now
fixes it — for `pytest` only.** A bare `pytest` from a worktree tests that
worktree; no `PYTHONPATH` prefix, and the ceremony every brief used to carry is
obsolete. But the conftest runs under pytest and nowhere else:

```
bare python -c from a worktree:  ...\migration-kit\src\...\report.py   <- the MAIN checkout
the same import under pytest:    ...\mk-c14a-impl\src\...\report.py    <- the worktree
```

So anything that reads a signature, prints a docstring or probes behaviour with
bare `python` from a worktree is reading the **wrong tree**, and it looks right
because the two are usually identical. The orchestrator hit this while checking
two function signatures, an hour after merging the conftest that was supposed to
have retired the hazard.

Every agent should still print `module.__file__` once and confirm it. One agent
reported a false green from the original form of this trap; two more designed
around it unprompted. For a one-off probe outside pytest, set `PYTHONPATH`
explicitly:

```
PYTHONPATH='<worktree>\src' <main>\.venv\Scripts\python.exe -c "..."
```

Note the **single quotes** in a bash tool — without them the backslashes are
eaten and the import silently resolves to the main checkout, which is the same
false green wearing a different hat.

**Run `scripts/check_merge.py`, not `pytest`, before calling a merge green.** Two
chunks reached the orchestrator test-green and CI-red in one session, both for a
structural reason neither blind half could have prevented.

**Run `scripts/check_contract.py <plan> --from N --to M` before dispatching
against a contract.** Six contracts were wrong in one session; the mechanical half
of that is free to catch.

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

## What this session changed about how the work is done

**Order the chunks so the artifact moves.** Ten chunks merged and the document
never changed once, because the template that renders everything was scheduled
after all nine of its inputs. Bottom-up sequencing means the person paying for the
work steers on summaries instead of output, and it hides exactly the defects a
reader finds in a second — repeated draws, buried evidence, a latency row of
zeros. **Render something end to end early, then deepen it.**

**Blind-test the invisible; eyeball the visible.** The four-role pipeline earned
its cost on statistics, the join and the security scanner — places where a wrong
answer looks exactly like a right one. It is overkill for "does the chart
render", which a human verifies by opening the page. Keep the blind pair where a
defect is undetectable by looking.

**Contracts written as prose are a defect source.** Six were wrong in one
session: a type that does not exist (`Item` for `GoldenItem`), a file cited in the
wrong package, two arithmetic errors, a guard rule that contradicted another rule
in the same contract, and a mutation instruction that was unsatisfiable by
construction. Each cost an agent real time. Now that the modules exist and are
reviewed, **point the agent at the code and let the module be the spec.**

**`scripts/check_contract.py`** catches the mechanical half before dispatch —
every `file.py:NN` resolved against both trees, every line number range-checked,
unknown symbols flagged as advisory. It found four more across the plan on its
first full run.

**`scripts/check_merge.py`** refuses a merge that looks green and is not. Seven
checks, built from five real failures: a conflict region that ends mid-statement
(both sides sharing the closer git puts *after* the marker); two blind halves
defining the same top-level name with no conflict marker; `__all__` collisions;
`COMPATIBILITY.md` rows for imports only the tester's file makes; ruff on the
merged import block neither pair can see. **Run it, not pytest, before calling a
merge green** — two chunks reached the orchestrator test-green and CI-red.

## Two hazards this session found the hard way

**The `conftest.py` retires R4 for `pytest` only.** A bare `python -c` from a
worktree still imports the **main checkout**:

```
bare python:   C:\Users\ewehm\repos\migration-kit\src\...\report.py
under pytest:  C:\Users\ewehm\repos\mk-c14a-impl\src\...\report.py
```

Anything that reads a signature, prints a docstring or probes behaviour with bare
`python` from a worktree is reading the wrong tree, and it looks right because the
two are usually identical. Use `pytest`, or set `PYTHONPATH` explicitly for
one-off probes.

**Concurrent mutation arenas clobber each other.** Three agents had arenas
silently replaced with pre-change source, and every time the symptom was
fabricated `SURVIVED` results — the failure mode that quietly reports a chunk as
unpinned. The session scratchpad is shared between all agents. **Put an arena
outside both checkouts and outside the scratchpad**, and prove each mutant is on
disk before pytest runs: `__file__`, a hash of the file taken *inside the
importing interpreter*, and a check that the mutated text is present.

## Agent dispatch can fail in bursts

Six agents died at startup inside half an hour — "the response stopped arriving",
"no progress for 600s" — each after a single message, while an agent dispatched
earlier kept running to completion. Spawning was broken, not running.

Nothing was lost, because they died before doing work. But the lesson for a wide
dispatch is: **re-dispatch two at a time, not four**, and check that the first
pair is actually making progress before sending more. And when it happens, tell
the surviving long-running agent to commit immediately — it is the one with
something to lose.

## What wastes tokens

- Reading the whole plan instead of one chunk.
- Re-deriving state that is in this file or in `PROGRESS.md`.
- Dispatching off-critical-path work while a contract upstream is unsettled. It
  does not shorten the project; it multiplies what must be re-coordinated when a
  review changes a field name. That happened here: C1's review demanded renames
  after C2 had already started on the old ones.
- Letting an agent rediscover a trap from the preamble above.
- Running the full 1110-test suite when one file's tests answer the question.
