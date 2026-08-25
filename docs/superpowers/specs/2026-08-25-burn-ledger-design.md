# The burn ledger and the thin brain — design

**Written 2026-08-25. Status: approved, not implemented.**

A measured context-and-cost budget for this pipeline, and a small durable store
that stops every dispatched agent from rediscovering the repository from zero.
Both machines run the same logic because both pull the same repository.

---

## 1. The problem, measured

Every number in this section came out of the Claude Code session transcripts at
`~/.claude/projects/<slug>/**/*.jsonl`, which record a `usage` block on every
assistant message. Nothing here is estimated.

### 1.1 Consumption

424 transcripts, 22 active days, **5.83 billion tokens**.

```
2026-08-14   1,678,328,586   <- record
2026-08-24     970,162,095
2026-08-25     744,876,333   <- by 06:18, one session
2026-08-21     670,682,026
2026-08-22     512,164,217
2026-08-01     470,679,576
2026-08-13     248,726,218
             ...16 further days, every one under 125M
```

**Median day ~57M. The top six days are 77% of the month.** The distribution is
not noisy, it is bimodal: ordinary days, and fan-out days.

By project: `opik-rigor` 3.25B, `model-migration-kit` 1.71B, `governor` 515M.

### 1.2 Where it goes

**`model-migration-kit` only** — §1.1 is all projects. On 08-24 the two are
identical to the token, because nothing else ran that day; on 08-25 they differ
by ~2.8M, which is another project's activity.

| | 2026-08-24 | 2026-08-25 (to 06:18) |
|---|---|---|
| API calls | 6,910 | 5,333 |
| output | 4,034,858 | 2,501,266 |
| cache **write** | 23,248,272 | 14,552,948 |
| cache **read** | **942,865,149** | **724,985,381** |
| uncached input | 13,816 | 10,666 |

**Cache reads are 97.4% of all consumption.**

### 1.3 The constant

```
cache-read / calls  =  136,449 tokens per call   (08-24)
                       135,943 tokens per call   (08-25)
```

Average context is **~136K tokens and is re-read on every API call**. Stable to
0.4% across two days and two unrelated workloads. This is the single number the
whole design turns on.

### 1.4 The cost model

```
cost  ≈  tool_calls  ×  context_size
```

Validated against the measurement: 77 subagent transcripts total **1.19 billion
tokens**, median **12.4M** per agent, max **68.5M**. Agents whose reports named
66/62/89/101/73 tool calls predict 9–14M at 136K context; the measured median is
12.4M. Subagents are **70% of all consumption**.

### 1.5 The defect this design exists to correct

`docs/superpowers/plans/METRICS.md` records agent cost at **110–200K tokens**.
The measured median is **12.4M** — an **~80× undercount**.

The cause is that `subagent_tokens`, as reported by an agent, counts output and
uncached input. It does not count cache reads, which are 97.4% of the bill. The
document the pipeline calibrates against has been measuring the wrong quantity
since it was written, and it misled a live dispatch decision on 2026-08-25.

METRICS.md also records, as a finding: *"Tool calls do not predict anything…
Count tokens. Ignore call counts."* That is correct for predicting *reported*
tokens and **wrong for predicting cost**, because each tool call re-reads the
whole context. §1.4 is the correction.

**Correcting METRICS.md is the first deliverable, before anything is built on
it.** Building a calibration system beside an 80× error in the file it
calibrates against would be this project's own signature defect: a check
satisfied by something other than what it claims to check.

---

## 2. Scope

**In:** telemetry recording, a regenerated rollup, a dispatch gate, a generated
brief-pack, a dashboard, and the METRICS.md correction.

**Out:** per-outcome cost attribution (cost per merged chunk, per confirmed
finding). The ledger is designed so attribution stays possible — dispatches are
stamped with a chunk or issue id from day one — but the analysis layer is not
built now.

---

## 3. Components

Each is independently useful, independently testable, and fails safe.

### 3.1 `scripts/burn/record.py` — the recorder

**Purpose.** Capture context and cost continuously at zero token cost.

**Interface.** Runs as the Claude Code statusline command. Reads one JSON object
on stdin. Writes one line to `.claude/burn/samples-<YYYY-MM-DD>.jsonl`. Prints
the status line on stdout.

**Input fields consumed** (all through `.get` with fallbacks, none assumed
present):

```
context_window.remaining_percentage
cost.total_cost_usd
session_id
model
workspace.current_dir
```

**Sample record:**

```json
{"ts":"2026-08-25T06:18:44Z","session":"a8b1a6bb","model":"claude-opus-5",
 "ctx_remaining_pct":41.0,"cost_usd":12.47,"cwd":"C:/Users/ewehm/repos/mk-main",
 "branch":"main"}
```

**Why a statusline command.** It is a subprocess. Nothing it reads or writes
enters any context window, so the measurement is free. This is the only channel
in the harness that observes context usage without consuming context.

**Hard constraints, both learned the hard way in `scripts/statusline.py`:**

- **ASCII only on stdout**, and stdout reconfigured to UTF-8 with
  `errors="replace"`. A status line that raises `UnicodeEncodeError` under
  `cp1252` takes the whole render down.
- **It must never raise.** Every field defensive, every failure degrading to a
  shorter line. A recorder that breaks the status line will be removed within a
  day, and then there is no measurement at all.

`.claude/burn/` is **gitignored** — high volume, machine-local, and the
transcripts are the durable record regardless.

**Status line addition.** One segment: `burn 745M/750M (99%)`, computed from
today's rollup cache (§3.2), never by parsing transcripts inline — that query
takes minutes and this runs on every render.

### 3.2 `scripts/burn/rollup.py` — the truth source

**Purpose.** Turn transcripts into a committed, greppable, shared record.

**Interface.**

```
python scripts/burn/rollup.py              # regenerate BURN.md and burn.html
python scripts/burn/rollup.py --check      # exit 1 if BURN.md is stale
python scripts/burn/rollup.py --backfill   # one-time, all 424 transcripts
python scripts/burn/rollup.py --today      # fast path, writes .claude/burn/today.json
```

**Reads** `~/.claude/projects/**/*.jsonl`, summing the four usage fields
**separately**. They are priced differently and a cache read is not a cache
write; collapsing them is the conflation this project spends its nights finding.

**Writes** `docs/superpowers/plans/BURN.md` — committed, **DERIVED zone,
generated and never hand-edited**, same contract as `dependency_surface.py`.
Contents: daily totals for 30 days, per-project totals, per-agent distribution,
the §1.3 constant recomputed, and the current ceiling with days over it.

**Also writes** `.claude/burn/today.json` — `{"day":"...","total":744876333,
"ceiling":750000000,"pct":99.3,"agents":5}` — the fast path the recorder and the
gate both read. Regenerated on demand; never parsed from transcripts by a caller
that must be fast.

**Staleness.** `--check` is wired into `scripts/check_merge.py` as an eighth
check, **advisory (prints a warning, does not fail the merge)**. A generated
report going stale must not be able to red a merge that is otherwise sound; that
would be a gate blocking correct work, which this project has already ruled is
worse than the defect it prevents.

### 3.3 `scripts/burn/gate.py` — the dispatch gate

**Purpose.** Put the number in front of the decision, at the moment of the
decision. R28.1 in one sentence: a budget in a document nobody consults while
dispatching is a budget nobody executes.

**Interface.** A `PreToolUse` hook matched on the agent-dispatch tool. Reads
`.claude/burn/today.json` and `.claude/burn-budget.json`.

**Behaviour.**

| today's burn | action |
|---|---|
| < 60% of ceiling | inject one line: `burn today: 312M/750M (42%), 3 agents` |
| 60–100% | inject the same line as an explicit **warning**, naming the projected cost of this dispatch |
| ≥ 100% | **refuse**, and name the override in the refusal text |

**Projected cost of a dispatch** = `expected_tool_calls × current_context`, from
§1.4. `expected_tool_calls` comes from the brief (§3.4). Absent one, use **70**,
the median of the twelve agents whose tool-call counts are on record — METRICS.md
§2's seven (64, 188, 73, 63, 62, 79, 63) and 2026-08-25's five (66, 62, 89, 101,
73). Note the 188: the spread is 3×, so this default is a floor for a warning,
not a forecast to rely on.

**Config** — `.claude/burn-budget.json`, **committed**, which is what makes both
machines run the same policy:

```json
{"daily_ceiling": 750000000,
 "warn_fraction": 0.60,
 "session_soft_budget": 400000000,
 "default_expected_tool_calls": 90,
 "override_env": "BURN_OVERRIDE"}
```

**Why a session budget as well as a daily one.** The daily figure cannot see the
shape that produced it. At a 57M median day, a 750M ceiling fires roughly twice a
month — correctly, on the fan-out days — but a single session did 745M on
2026-08-25. The session budget is the control that can see that.

**Open risk, to be settled before anything is built on it.** It has not been
verified that a `PreToolUse` matcher fires on the subagent-dispatch tool in this
harness, nor what the tool is named for matcher purposes. **Task 1 of
implementation is to verify this empirically**, with a hook that does nothing but
log. If it does not fire, the gate degrades to the status-line number plus a
`CLAUDE.md` rule — materially weaker, and the design must be re-reviewed rather
than silently downgraded.

### 3.4 `docs/BRIEF-PACK.md` — the thin brain

**Purpose.** Stop each agent paying ~80 tool calls' worth of context to
rediscover facts the repository already knows.

**The leverage.** At 136K context and ~80 calls, 30K removed from an agent's
starting brief is **~2.4M tokens saved per agent**, not 30K. This is the
highest-return component in the design.

**DERIVED zone. Generated by `scripts/burn/briefpack.py`, never hand-edited.**
Regeneration checked by `check_merge.py` the way `dependency_surface.py` is —
and this one **does** fail the merge, because unlike a burn report a stale
brief-pack actively misinforms an agent.

**Contents, all derivable from the tree and git:**

- where things live: the gate scripts and what each gates; the plan, ledger and
  metrics documents and what each is for
- the chunk→commit map: branch name → merge commit → what it landed
- the gate inventory: every check in `check_merge.py` and `verify_release.py`,
  what it asserts, and what it is known not to assert
- conventions an agent otherwise rediscovers: the interpreter path, `PYTEST_ADDOPTS`
  hygiene, revert-proof discipline, prose commit style, "do not merge, do not push"
- the standing rules: grep merged code before writing a brief; prescribe outcomes,
  not mechanisms; verified claims carry their output

**What it must never contain:** any claim about current correctness that a merge
could invalidate. It says where to look and what the conventions are. It does not
say what is true. Judgment lives in METRICS.md and the R40 ledger, which are
append-only and dated and never claim currency.

### 3.5 The dashboard

**`burn.html`**, self-contained, regenerated by `rollup.py`, publishable as an
Artifact so it is readable from either machine and shareable.

Five views, in priority order:

1. **Daily burn vs ceiling** — 30-day bars with the 750M line. The primary view.
2. **Composition** — cache-read / output / cache-write, stacked. Shows where it
   goes; cache-read has been 97.4%.
3. **Cumulative month-to-date vs allowance** — the resource-management view:
   will we reach the 31st.
4. **Per-agent cost distribution** — the fan-out view. Median 12.4M, max 68.5M.
5. **Average context per call** — the leading indicator. Pinned at ~136K; if it
   climbs, everything climbs with it.

Backfilled once from the existing 424 transcripts, so it opens with a month of
history rather than empty.

---

## 4. Thresholds

Calibrated from §1, and **explicitly provisional** — they are to be revisited
after two weeks of recorded data.

```
context:   warn 60%    compact at next boundary 75%    compact now 85%
daily:     ceiling 750,000,000     warn from 450,000,000 (60%)
session:   soft budget 400,000,000
dispatch:  projected = expected_tool_calls x current_context
brief:     every dispatch states a tool-call budget; the agent reports against it
```

**A natural boundary** is: a merge landed, the gate green, and the branch pushed.

**Above 50% context at a natural boundary, prefer a fresh process to a compact.**
A compact costs a summarization pass and loses fidelity; a fresh process costs
nothing and loses everything not written down.

**Precondition for a fresh process: findings are written down.** This is not a
preference. The MacBook lost eleven agents at a token limit with ten of them
having written no `FINDINGS.md`, and that work is gone. Carrying context is
strictly cheaper than losing a night's findings.

---

## 5. Testing

Following `tests/test_release_checks.py` and `tests/test_contract_check.py`,
which are this repository's established homes for testing `scripts/`.

- **`record.py`** — malformed stdin, absent stdin, missing fields, a payload
  shaped differently than expected: each must still print a usable line and must
  never raise. Explicit `cp1252` encoding test, because that exact failure has
  already happened once in this repository.
- **`rollup.py`** — a constructed transcript tree in `tmp_path` with known
  totals, asserted field by field so a cache read cannot be silently summed into
  output. Must not depend on the real `~/.claude` directory.
- **`gate.py`** — the three bands (under, warn, refuse) driven end to end over a
  constructed `today.json`; the override honoured; a missing or unreadable
  `today.json` failing **open** with a visible note, never failing closed on a
  telemetry error.
- **`briefpack.py`** — regeneration is deterministic, and a hand-edit is detected
  by `--check`.

Every fix reverted individually against a byte-verified copy, restored by hash,
and confirmed red — the discipline used on G3, G18 and G25.

---

## 6. Order of work

1. **Correct METRICS.md** (§1.5). Nothing else may be built first.
2. **Verify the `PreToolUse` matcher fires** on the dispatch tool (§3.3). If it
   does not, stop and re-review this design.
3. `rollup.py` with `--backfill` — the history exists; capture it before a
   retention policy deletes it.
4. `record.py` and the status-line segment.
5. `gate.py`.
6. `briefpack.py` and `BRIEF-PACK.md`.
7. The dashboard.

Steps 3–7 are each independently shippable and independently useful.

---

## 7. What would falsify this design

Recorded here because a design with no failure condition is a hypothesis nobody
can attack.

- **If average context per call stops being stable**, §1.4's model is wrong and
  every projection built on it is wrong. The dashboard's view 5 exists to make
  that visible early.
- **If the brief-pack does not measurably reduce per-agent cost**, it is
  overhead. The test is per-agent median before and after, from the transcripts.
  It must fall from 12.4M or the component should be deleted.
- **If the gate is overridden routinely**, the ceiling is wrong, not the work.
  The override is recorded so the override *rate* is itself a measurement.
