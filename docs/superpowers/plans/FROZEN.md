# Resumed 2026-08-25 01:20 EDT — the freeze is over, this file is the landing point

The 23:28 freeze was worked off in full. Everything it listed is merged, and the
one ambiguous item on the board resolved in favour of keeping the work.

## State

**`main` is at `1bb9f8e`, clean, seven gates green, pushed to origin.**
**2351 tests ran** — a number the gate now actually holds, which it did not at
the freeze.

| Was frozen as | Now |
|---|---|
| `chunk/completeness-certificate` `c5bae5a` | merged `68de3af` |
| `chunk/provenance-timeline` `44eb692` | merged `cc84092` |
| `chunk/latency-absence` `630912d` + 2 dirty files | **committed `a895004`, still unmerged** |

## The one ambiguous item: `mk-latency` — committed, not discarded

The two dirty files were complete work, not a half-finished pass: 591 lines,
every helper resolving, **2258 passed / 1 xfailed** in that worktree. Committed
as `a895004`.

It is **still unmerged**, deliberately: it was cut from `630912d`, which is
behind `main`, and it wants a rebase and a fresh gate before it lands. It is
also entangled with the Mac's **U4** (`--timeout` makes latency the strictest
gate in the tool) — the two touch the same section and want sequencing.

What it found is larger than its brief: the latency section gated on the
*adapter's class name*, so the shipped demo printed **"Not measured" over 120
recorded timings**. Three further forms of the same rule came with it — `.3f`
renders every one of the demo's real numbers as `0.000`; `n` was in the payload
from the first version and reached neither surface, so a median over 60 draws
and a median over 1 were byte-identical documents; and `measured` is `or`, not
`and`.

## Issue #9 — closed. All three gate defects fixed and merged

| | commit |
|---|---|
| **G18** merge gate — env sanitized, count held against a floor | `a3f5a47` |
| **G25** console script — entry point resolved, not filename-matched | `6e29539` |
| **G3** contract citations — containment on the resolved path | `d7de884` |

Each was verified on this box before merging rather than taken on its agent's
report. The G18 proof is the one to remember: with `PYTEST_ADDOPTS="--co -q"`
set **and** an `assert 1 == 2` planted in `tests/`, the gate now prints
`1 check(s) failed. This merge is not green.`

**G3 was four routes, not one.** `REPO.parent` holds 95 directories, 81 of them
checkouts of this project, with `report.py` in 37 distinct lengths. An escaped
citation is answered by 37 versions of the right file.

**The count is a floor, not an expectation.** Red below `MINIMUM_TESTS = 2000`,
or on any failure/error, or on an unreadable report. Not red because the count
moved. Do not "fix" it into a hardcoded number.

## The Mac, and what changed about it

`origin/audit/macbook-2026-08-24` at `d35b79c`. **V1, V2 and V3 are now
reproduced on this box** — `AUDIT-V1-V3-windows.md` and
`scripts/audit/v1_v3_repro.py`, both merged, both re-runnable.

The reproduction earned its keep:

* **V1 confirmed** digit for digit, extreme included. One item asked 60 times is
  a GO.
* **V2 is PARTIAL** — the 99-null p is `0.161087`, not the reported 1.0. The 1.0
  belongs to the **100**-null case, which the Mac did not report and which is
  *worse*: no imputation warning, zero numeric disclosure.
* **V3 confirmed and understated 33×.** **Three** silent nulls out of 100 flip a
  GO into a NO-GO — not 99 — and the warning threshold sits at **5**, so the two
  counts that first flip the verdict are exactly the two that say nothing.

**U1, U2, U4 and `missing_scores` remain unreproduced here.** And the ledger
undercounts its source: `AUDIT-verdict.md` carries **V1–V8, L1–L3, U1–U10, D1**
— roughly 25 findings, not the seven in R40.4. **Ingesting that document in full
is the next audit job.**

## Resume, in this order

1. **Ingest `AUDIT-verdict.md` in full** into R40.4. It is currently a sample
   labelled as one, and ~18 findings are not in the ledger at all.
2. **Rebase and merge `chunk/latency-absence`** (`a895004`), sequenced with U4.
3. **Reproduce U1, U2, U4** the way V1–V3 were reproduced. U1 is the strongest:
   a judge writing `0` on a 1–5 rubric buys a GO through the parse-failure door.
4. **R37.6's third counter** (`scripted_among_named`) — the next R40.1 brief.
   Verified absent from `src/`, so nothing closed it incidentally.
5. **R40.5** — the five remaining constant-vs-artifact checks in
   `verify_release.py`. Rank the distribution-name one first.
6. **JOB-16, the statistics audit**, restarted from the Mac's brief. Its opening
   check is whether the Mann-Whitney operands are transposed, which if true sits
   underneath V2 and V3 both.

## Standing rules that earned their place tonight

* **Grep the merged code before writing a brief.** R34.3 was struck from R40 by
  looking, not by trusting a merge done twenty minutes earlier.
* **Verify the agent's claim yourself before merging.** Three of four reports
  were confirmed by an independent probe on this box; the fourth — the V1–V3
  reproduction — corrected the source it was checking.
* **Timings taken under different load are evidence of nothing but their
  spread.** G18's three full-suite numbers (269.20s, 254.57s, 211.54s) put the
  run carrying the new mechanism *fastest*. The real cost came from an
  alternated back-to-back measurement: ~5s over 536 tests.
