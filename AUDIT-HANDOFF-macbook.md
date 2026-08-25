# MacBook handoff — read this first

**Written 2026-08-25, at the token limit of the session that produced most of this branch.**
Owner: the MacBook (audit side). Windows owns `AUDIT-VERDICTS.md`, `AUDIT-JOB-*.md`, `src/`,
`tests/`, `docs/superpowers/`.

---

## 1. Where things stand, in one paragraph

The MacBook has audited the **rendered document**, the **terminal renderer**, the **gates**, the
**showcase**, and the **README journey**, and has shipped the audit tooling into
`scripts/audit/`. Windows has verified essentially all of it and refuted nothing structural.
Coordination has moved from `JOBS.md` to **GitHub Issues** (see §4). Everything is on
`audit/macbook-2026-08-24`; the tree is clean and the suite is green (**2206 passed**).

## 2. The single most important thing to know

> **Every finding in JOB-3 is a check printing `[PASS]` over something it did not look at.**

That is Windows' summary and it generalises to the whole branch. The product enforces *"an
absence must not render as a measurement"* rigorously; **none of the tools that enforce it do.**
The proof is one diff, verified independently in a fresh worktree at branch head:

```
18 insertions, 67 deletions, one file
-> fourteen independent lies in the rendered document
-> check_merge.py: all seven [PASS];  2206 passed;  demo job's three assertions all pass
```

That tree's demo prints `NO-GO (exit 0)`, transposes baseline and candidate, and carries no
FAKE MODELS band in the terminal. **Five of six controls die**, so the gates are not weak in
general — the boundary is that **a value the suite reads by name is pinned; a value's label,
position, presence or completeness is not**, and nothing in 2,206 tests reads a whole rendered
surface as a whole.

## 3. Deliverables on this branch

| file | owner | what it is |
|---|---|---|
| `AUDIT-macbook.md` | Mac | 46 findings on the rendered HTML, adversarial verdicts inline |
| `AUDIT-terminal.md` | Mac | T0–T38: the terminal renderer, mutation testing, scale |
| `AUDIT-gates.md` | Mac | G1–G45: every gate, plus the composed green-but-broken tree |
| `AUDIT-showcase.md` | Mac | two independent passes over the showcase |
| `SYNC-V2.md` | Mac | the coordination change, **high priority for Windows** |
| `scripts/audit/` | Mac | 7 modules + README — the tooling, so sweeps can be rerun |
| `AUDIT-VERDICTS.md` | **Windows** | their verification of the above — read, never edit |

## 4. The protocol changed — GitHub Issues, not `JOBS.md`

Because `JOBS.md` is a shared mutable file on a branch whose rule 1 is *append-only*, and the
claim race cost five push rejections and one stale claim in a single session. **Issue assignment
is atomic and server-side, so the race is gone.**

```bash
gh issue list --label job-board                          # the board
gh issue edit <N> --add-assignee @me --add-label machine:windows   # claim
gh issue comment <N> --body "…command and output…"       # progress
gh issue close <N> --comment "done in <sha>"
```

Labels: `job-board`, `machine:mac`, `machine:windows`. **`SYNC-V2.md` asks rather than
announces** — if `gh` is unusable on Windows, say so on #4 and revert; a protocol only one
machine can follow is worse than the one we had.

**Board at handoff:** #3 JOB-4 **closed**, #5 JOB-6 **closed**, #4 JOB-5 **OPEN (for Windows)**,
#6 JOB-7 **claimed, in flight**.

## 5. Work in flight when this session ended

Roughly a dozen agents were mid-run. **Their findings are in the scratchpad, not on the
branch** — a new session should harvest them before starting anything new:

```
/private/tmp/claude-501/-Users-ericw-IdeaProjects-model-migration-kit/
    426b2f8b-7004-40a6-9cbf-05616f6b869c/scratchpad/audit/*/FINDINGS.md
```

**⚠ That path is session-scoped and may not survive.** If it is gone, the work is gone; re-run
from the issue briefs rather than trying to reconstruct it.

In flight: JOB-7 (the five decision rules; judging/runner/goldenset), JOB-8 (`series.py` as
logic), JOB-9 (determinism under varied `PYTHONHASHSEED`), JOB-11 (verdict fuzzing for
non-monotonicity), JOB-12 (`report.py`'s reconstruction path), JOB-14 (hostile-log security),
JOB-15 (the suite as a corpus), JOB-16 (**the statistics themselves** — highest value: confirming
the Mann-Whitney operands are not transposed), JOB-17 (the Windows checklist), JOB-18
(accessibility as a correctness matrix), JOB-19 (assumptions about `opik-rigor`), JOB-20
(cross-audit consistency), JOB-21 (`dimensions.py` as logic).

**Two landed but unpushed at cutoff** — write them up from the scratchpad first:

- **JOB-10, the README journey.** `pip install model-migration-kit` on this machine's default
  Python (Anaconda 3.9.13) fails with *"No matching distribution found"* — the wording pip uses
  for a package that does not exist — twelve lines after the README says *"Published on PyPI."*
  The `>=3.10` requirement is stated 29 lines **after** the command it breaks. And the real-model
  config at README:433-447 **cannot be loaded as printed** — it names `rubric.md`, which the
  README never tells you to create. I verified both.
- **JOB-13, the plan as a contract.** **No genuine unmarked contradiction** — the plan corrects
  itself in place four times of five. What it is bad at is propagating a ruling back into the
  chunk contract it overturns: seven of nine findings are a live ruling and a stale contract
  1,000–2,000 lines apart. Note the coverage caveat: this checkout has **R1–R33 only**; R34/R38/
  R39 are cited in `JOBS.md` but live on the Windows side.

## 6. Rules that bind anyone continuing this

From `CLAUDE.md`, `JOBS.md` and hard experience this session:

1. **One agent, one worktree.** `git worktree add --detach <path outside the repo> <sha>`. A dozen
   were live at once here without collision.
2. **Restore from a byte-verified backup** (`cp` + `shasum`), **never `git checkout --`.** There
   is a hook that refuses it.
3. **Namespace the scratchpad** — `scratchpad/<role>/`, never a bare filename. It has cost twice.
4. **The Mac does not change `src/` or `tests/`** on this branch. A finding is worth more than a
   patch, because a patch skips the four-role pipeline.
5. **Every finding carries its command and output.** Three rulings on this project were wrong for
   want of that.
6. **Run an adversarial pass and state verdicts INLINE**, not only in a summary table. It killed
   9 of the first audit's 41 findings and is the most valuable thing either machine produced.
7. **`.venv/bin/python` always.** Bare `python` here is Anaconda 3.9.13: `check_merge.py` gives a
   **false red**, and bare `pytest` collects **247 of 2206** and looks green.

## 7. Corrections I made against myself — keep these visible

A fair reading of this branch needs them:

- **Findings 12 and 13 were not discoveries.** R33 (`e2b0614`), landed the day before, already
  said it and counted it the same way. Demoted in place.
- **My `<title>` fix broke `<head><title>`**, and my regression check said "still found" because
  it counted presence, not occurrences (2 → 1). Scoped and re-verified **by count**.
- **Finding 10 misquoted the page** — I dropped the word *not* from *"so this judge is **not**
  powered"*.
- **Finding 11 misread the mechanism** — those flags are candidate-only, so `colspan="2"` is
  correct and the page *is* self-derivable.
- **The `--quiet` headline was false** — the HTML still bands twice; Windows measured it and
  struck the clause.
- **Tier 2 over-claims reachability.** Most of it hardens reads against a writer that does not
  exist. **Finding 35 (no schema guard on the evidence log) is the prerequisite** that would make
  the rest reachable, and should be read as Tier 1.

## 7a. Hibernation, 2026-08-25 ~03:15Z — what was lost and what survived

The session was paused at the token limit. **Eleven running agents were stopped.** Ten had not
yet written `FINDINGS.md` and **their work is gone** — do not try to reconstruct it; re-run from
the briefs below if the work is still wanted.

**Lost (re-runnable from these briefs):** JOB-11 verdict fuzzing for non-monotonicity · JOB-12
`report.py`'s reconstruction path · JOB-15 the suite as a corpus (single-assertion invariants) ·
**JOB-16 the statistics themselves** — the highest-value one, whose first check is confirming the
Mann-Whitney operands are not transposed · JOB-17 the Windows checklist · JOB-18 accessibility as
a disclosure matrix · JOB-19 assumptions about `opik-rigor` · JOB-20 cross-audit consistency ·
JOB-21 `dimensions.py` as logic.

**Survived and landed:** JOB-14, the hostile-evidence-log security review, now
`AUDIT-security.md`. Its headline is that **a hostile log turns off the golden-set identity gate
by blanking one field, and the page says nothing** — and that path confinement is *textual*, so a
symlink shipped beside a log reads a file outside the tree while the page names the symlink.
Confinement is also measured against the **process CWD** rather than the log's directory, and
*"the triggering shape is exactly what this project's Windows box writes."* Output safety beyond
the known `cli.py:437` and `verify_release.py`'s archive extraction were both **REFUTED** with
their coverage named.

**Also landed at the limit:** `AUDIT-verdict.md` (JOB-7 and JOB-8, JOB-7-upstream, JOB-9).

## 8. If you do only one thing next

**Take JOB-5 (#4)** — re-run `scripts/audit/differential_render.py` and the mutation harness
against current `main` and report **only the delta**. The tooling already caught one regression
that way. JOB-6 pinned the baseline bucket table for exactly this purpose; it is in
`AUDIT-gates.md`'s JOB-6 commit and in `scripts/audit/README.md`.
