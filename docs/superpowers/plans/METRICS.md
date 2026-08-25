# What this process costs, and what it catches

A living record of the pipeline measuring itself. **Append to it; do not rewrite
it.** Every number here was measured, and where a number is an estimate it says
so. The point is to make the process improvable rather than merely repeatable.

Started 2026-08-24. All figures from that session unless dated otherwise.

---

## 1. What a role costs

From 11 completed agents, each reporting `duration_ms`, `subagent_tokens` and
tool-call count.

| Role | Wall-clock median | Range | Tokens median | Token range | n |
|---|---|---|---|---|---|
| Implementer | 14.1 min | 5.2 – 22.8 | **110k** | 105 – 114k | 4 |
| Blind tester | 22.4 min | 19.0 – 26.9 | **169k** | 147 – 188k | 3 |
| Reviewer | ~17 min† | 17.0 / 67.9 | **150k** | 144 – 157k | 2 |
| Fix pass | ~33 min† | 33.3 / 49.8 | **198k** | 191 – 205k | 2 |

† The larger of each pair includes stall-and-resume dead time; wall-clock counts
the gap. Use the smaller.

**A full chunk through all five stages costs roughly 600–650k tokens.**

### Seven more agents, and a constant nobody expected

Added 2026-08-24, second batch. Every figure is from the agent's own reported
`subagent_tokens` and `duration_ms`, not estimated.

| Agent | Role | Tokens | Tools | Wall-clock | s per 1k tokens |
|---|---|---|---|---|---|
| C7 lineage | follow-up impl | 157k | 64 | 20.3 min | 7.8 |
| C10 fix | fix pass | 275k | 188 | 44.7 min | 9.7 |
| C18 round two | implementer | 189k | 73 | 34.5 min | 11.0 |
| C22b impl | implementer | 123k | 63 | 18.1 min | 8.8 |
| C22b test | blind tester | 204k | 62 | 26.6 min | 7.8 |
| C14b impl | implementer | 153k | 79 | 26.7 min | 10.4 |
| C14b test | blind tester | 200k | 63 | 23.9 min | 7.2 |

**1.30M tokens across seven agents**, all running 2–4 at a time on 16 logical
CPUs.

**The right-hand column is the finding.** Seconds per 1,000 tokens: 7.2, 7.8,
7.8, 8.8, 9.7, 10.4, 11.0. Median **8.8**, and every one inside ±25% of it —
across four different roles, three different chunks, and a 2.2× spread in raw
token count.

So the estimate that actually works is:

> **wall-clock ≈ 9 seconds per 1,000 tokens, at 2–4 concurrent agents.**

Combined with §1's role costs, that answers "how long will this take" without
guessing: an implementer at ~110–150k tokens is **17–23 minutes**; a blind
tester at ~170–200k is **25–30**; a fix pass at ~200–275k is **30–41**.

**This does not contradict "predict with tokens, not wall-clock" — it explains
it.** Tokens are the work; the constant is the exchange rate; and the rate is
stable *only while the machine is not oversubscribed*. §1's 4.4× wall-clock
spread came from a batch that included stalls and a five-agent pile-up. Hold
concurrency at 2–4 and the rate holds. Push to six and it is the first thing to
go — which is measurable, and is the cheapest early warning that the board is
too wide.

**What it is not.** It is not a per-machine constant to be quoted elsewhere: it
is this laptop (12-core i7-1260P), this model, this shape of work. What
transfers is the *method* — divide reported tokens by reported duration, watch
the spread, and treat a widening spread as a concurrency signal rather than a
code signal.

### Tool calls do not predict anything

Worth recording as a negative result, because it is the number a dashboard would
reach for first. Tokens per tool call across the same seven: 1.5k (C10 fix, 188
calls) to 3.3k (C22b test, 62 calls) — a **2.2× spread with no pattern by role**.
C10's fix pass made three times as many calls as C22b's tester for 35% more
tokens, because mutation testing is many cheap calls and fixture design is few
expensive ones. Count tokens. Ignore call counts.

### Predict with tokens, not wall-clock

The four implementers spent 105k, 109k, 111k and 114k tokens — a **9% spread**
across four different chunks. Their wall-clock ranged 5 to 23 minutes, a
**4.4× spread**.

Tokens measure the work. Wall-clock measures the work *plus* machine load,
stalls, and how contended the CPU was that minute. When estimating, use tokens.

### The roles rank consistently

`implementer 110k < reviewer 150k < blind tester 169k < fix pass 198k`

The blind tester costs more than the implementer every time, which is worth
knowing before treating tests as the cheap half. The fix pass is the most
expensive stage in the pipeline — see §3 for why that is correct rather than
wasteful.

---

## 2. Concurrency is the wall-clock bottleneck, not the code

Measured on 16 logical CPUs:

| Condition | `tests/test_report.py` (371 tests) |
|---|---|
| ~5 agents running full suites | **365 s** |
| 24 python processes, same code, minutes later | **27 s** |

**A 13× swing with no code change.** An audit measured the same file at 31.4 s
under load and the full suite at 240–273 s serial.

This produced a real defect in the handoff: the 365 s figure was written into
`CLAUDE.md` as *"`tests/test_report.py` takes ~6 minutes; that is normal, not a
hang"*, and every agent dispatched afterwards was told to budget for a wait that
does not exist. The measurement was real; the **attribution** was invented.

> **Never record a timing without recording the load it was taken under.**

### pytest-xdist, measured

12 configurations in a throwaway venv, all returning identical pass/fail:

| Config | Wall | Config | Wall |
|---|---|---|---|
| serial | 240 s | `-n 8` | **96 s** |
| `-n 4` | 158 s | `-n 8` (repeat) | 157 s |
| `-n 6` | 128 s | `-n 8 --dist loadscope` | 125 s |
| `-n 16` | 116 s | `-n 16 --dist loadfile` | 130 s |

**1.8–2.5×, ~120 s saved per run.** Zero parallel-induced failures across all 12.
Default `--dist load` beats both grouping modes. Past ~8 workers the
session-scoped fixtures get rebuilt per worker and eat the gains.

Note `-n 8` measuring 96 s once and 157 s later **on identical config** — that
is §2's point restated: the machine is shared with other agents.

---

## 3. What the process catches

- **7 of 7 reviews found defects both other roles missed.** 3 found defects in
  already-merged code. The review is the middle of a chunk, not the end.
- **Survivor counts per review:** C5 → 15 of 39 mutants; C7 → 11 of 43, all
  confirmed non-equivalent and all surviving the *full* 1998-test suite.
- **3 consecutive blind pairs produced zero disagreement** (C7, C10, C6) — and
  this is not evidence the review can be skipped. C5's pair also agreed, and its
  269-test green suite was hiding the chunk's own named failure mode.
- **6 agents have stopped rather than guess on a bad contract, and all 6 were
  right.** Two caught factual errors in the orchestrator's own revisions. One
  refused a ruling that a second ruling in the same brief had falsified.

The fix pass being the most expensive stage is the process working: it is where
15 and 11 real defects got closed.

---

## 4. The orchestrator's error taxonomy

Four errors in one session, **all the same shape**: a conclusion drawn from a
source nobody re-checked.

| | Error | What was actually done |
|---|---|---|
| R25 | R17.1 claimed `holm_bonferroni` returns uncorrected `alpha` after a step-down stop | Read the initializer, reasoned about it, never ran it |
| R26 | R23.1 claimed `item_counts` carries `passing`/`failing`/`unstable` — in a section whose own words were *"checked rather than assumed"* | Grepped, found a **function** reading those keys, concluded the **field** had them |
| — | "`core.fsmonitor` is already true" | Ran four `git config --get` calls, saw one `true`, attributed it to the first |
| §2 | "`test_report.py` takes ~6 minutes" | Measured correctly under five concurrent agents; reported it as a property of the file |

Every one was caught by an agent, and **every time for the same reason: the
agent needed the actual value to do its job and so could not take the sentence
on trust.**

> **Two rules out of this.**
> 1. **When a claim says something was checked, it must carry the output that
>    checked it.** A sentence saying "verified" is not evidence; a pasted
>    `KeyError` or printed dict is.
> 2. **Write briefs that make an agent derive a number rather than accept one.**
>    This is the only mechanism that has actually caught these.

A related instance with a different surface: the stall watchdog fired 7 alerts
naming completed agents, and the handoff initially blamed the tool. The tool was
correct — it reads `completed_agents.txt`, which the orchestrator is supposed to
maintain and stopped maintaining. **A tool fed by a hand-maintained side channel
degrades exactly when the operator is busiest, which is when its output matters
most.**

---

## 5. Open improvements

| # | Action | Expected | Status |
|---|---|---|---|
| 1 | Throttle concurrent full-suite runs across agents | Largest single lever; explains a 60 s swing on identical config | **no approval needed** |
| 2 | `pip install pytest-xdist` + `PYTEST_ADDOPTS="-n 8"` | ~120 s per suite/gate run | needs approval (shared venv); CI needs `pytest-xdist` in the `dev` extra in the same commit |
| 3 | Fix `worktree_path.py`'s `FALLBACK`, then `--install` | Kills the `.pth` trap at source | needs approval (shared venv); `--uninstall` reverts |
| 4 | Correct `CLAUDE.md`'s test-duration claim | Stops a phantom 6-min budget | **done** |
| 5 | Defender exclusion for `repos\` | Unquantified; 144,728-file surface, 33,695 `.pyc` | needs admin + a security-posture decision |
| 6 | Remove 23 merged+clean worktrees | 151 MB of 323 MB — **clarity, not speed** | needs approval (destructive) |
| 7 | `core.fsmonitor` / `untrackedCache` | ~0 at 84 tracked files | **skip** |

### Stage labelling

Agents should be told **which stage of five** they are (implementer, blind
tester, merge, reviewer, fix pass — stage 3 is the orchestrator's) and **which
chunk of the plan**. An agent that knows a fix pass follows it can hand off
instead of over-reaching, which is precisely the boundary C22a's implementer had
to reason out unaided when it shipped half a chunk and stopped.

---

## How to add to this file

When an agent completes, its notification carries `duration_ms`,
`subagent_tokens` and tool count. Record role, chunk, and the **concurrent agent
count at the time** — without that last number the timing is not interpretable,
which §2 is the proof of.
