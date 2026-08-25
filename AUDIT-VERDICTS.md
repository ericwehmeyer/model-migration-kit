# AUDIT-VERDICTS.md

Verdicts from the watching machine on commits pushed to
`audit/macbook-2026-08-24` after `8b8b9d6`. Every claim below carries the output
that proves it. Nothing in the project was changed to produce these.

---

## Cycle 1 — 2026-08-24, on `f5dbb0e`

**What landed.** One commit, `f5dbb0e` *"State the adversarial verdicts inline, as
the next-steps brief asked"* — `AUDIT-macbook.md`, +34 lines, seventeen inline
**WEAKENED** notes on findings 1, 3, 4, 5, 7, 14, 16, 17, 19, 21, 25, 27, 28, 30,
34, 38, 39.

**What did not land.** Neither of the two jobs the second machine was asked for:

- **Job 1, the audit tooling.** `scripts/audit/` on the branch holds `netguard.py`
  and `shuffle_order.py` and nothing else — and both of those are already on
  `main`, not new:

  ```
  $ git ls-tree -r --name-only origin/audit/macbook-2026-08-24 -- scripts/audit
  scripts/audit/netguard.py
  scripts/audit/shuffle_order.py
  $ git ls-tree -r --name-only HEAD -- scripts/audit          # main
  scripts/audit/netguard.py
  scripts/audit/shuffle_order.py
  ```

  No differential renderer, no masking helpers, no recomputation scripts, no
  adversarial-review harness, no mutation harness, no README. **Job 1 is not
  started.**

- **Job 2, the `render_terminal` audit.** No `AUDIT-terminal.md`, and
  `AUDIT-macbook.md`'s only change is the seventeen notes. **Job 2 is not
  started.**

**Checked against `main` at `25bc7ea`.** The audit was taken at a commit whose
merge-base with this branch is `e2b0614` (*R33*); `main` is **17 commits** past
that, including `C14c` merged, `C18`'s fix (`f65342a`), `C22b`'s fix (`25bc7ea`),
and revisions R33–R38.

All reproductions below ran the editable install, which resolves to `main`'s
working tree:

```
$ .venv/Scripts/python.exe -c "import model_migration_kit as m; print(m.__file__)"
C:\Users\ewehm\repos\mk-main\src\model_migration_kit\__init__.py
```

---

### The headline result: finding 1's three p-values are exactly right

This is the note that demotes the only Tier-1 finding, so it got the most work. It
claims *"unpaired MWU 0.1525, Wilcoxon signed-rank 0.1875, McNemar exact
0.3125"*.

**My first recomputation contradicted it, and my first recomputation was wrong.**
Computing the per-item unit from **pass/fail booleans** I got:

```
unpaired MWU over 12 item rates      p = 0.3041
Wilcoxon signed-rank (paired)        p = 0.6250
McNemar exact (majority per item)    p = 0.6250
```

None of the three matched. The reason is that the tool's own statistic is not
pass/fail and is not two-sided. `comparison.py:1402-1407`:

> rigor's one-sided Mann-Whitney, in ``(current, baseline)`` order. The order is
> the whole meaning of the test: ``alternative="less"`` asks whether the
> *candidate* is stochastically smaller than the baseline…

and it runs on the judge **scores**, not the booleans. Proof — the number the
document prints reproduces from the scores and not from the booleans:

```
$ grep -o 'Mann-Whitney p-value[^0]*0\.007843' keep.html
Mann-Whitney p-value (alpha 0.050, Holm threshold 0.0500) 0.007843

passed 0/1   n=60v60  MWU one-sided 'less'  p = 0.007442      <- not it
score        n=60v60  MWU one-sided 'less'  p = 0.007843      <- exact
```

Redoing the per-item unit on the same statistic the tool uses — mean judge score
per item, one-sided:

```
baseline mean score /item : [5.0, 5.0, 4.0, 5.0, 5.0, 5.0, 2.0, 5.0, 5.0, 5.0, 5.0, 5.0]
candidate mean score/item : [5.0, 5.0, 4.0, 5.0, 2.0, 5.0, 5.0, 5.0, 5.0, 1.0, 5.0, 1.0]

MWU  unpaired, 12 item mean scores  [less]      p = 0.152507
Wilcoxon paired, 12 item mean scores [less]     p = 0.187500
McNemar exact, majority per item     [greater]  p = 0.312500
```

**VERDICT: the note is correct on all three figures, to four decimals, against
`main` at `25bc7ea`.** `0.1525`, `0.1875`, `0.3125`. Its conclusion — *rule 1
fires under none of the three* — also holds: every one exceeds `alpha = 0.05`,
while the shipped unit gives `0.007843`. The demotion of finding 1 to Tier 4 is
**sound**, and the note's self-criticism (*"a code finding in a document finding's
clothes"*) is accurate: the unit is `comparison.py`'s choice.

The determinism the note leans on is real and quoted correctly —
`demo.py:14-21`:

> **Nothing is random.** … a demo that returned REVIEW on one machine and NO-GO on
> another would destroy the only claim it makes

and it is visible in the data: every per-item mean is a whole number, i.e. all
five draws within an item agree, so the ×5 inflation is exactly ×5.

*One nit:* the note prints `0.1525`; the value is `0.152507`, which rounds to
`0.1525` at four places — fine — but it is worth saying the figures are
**one-sided**, because a reader who reaches for `scipy` two-sided gets `0.3050`,
`0.3750`, `0.6250` and will think the note is wrong, as I did for twenty minutes.

---

### Finding 16: the note's counter-claim reproduces exactly

The note says finding 16's headline sentence is false because the HTML still bands
twice under `--quiet`, *"`grep -c "FAKE MODELS" q2.html` -> **2**, including the
`<title>`"*. Reproduced on `main` at `25bc7ea`:

```
$ .venv/Scripts/python.exe -m model_migration_kit.cli --quiet demo --out q2.html
C:\...\q2.html
VERDICT: NO-GO (exit 1)

$ grep -c 'FAKE MODELS' q2.html
2
$ grep -n 'FAKE MODELS' q2.html
7:<title>FAKE MODELS — NO-GO — fake-baseline-v1 to fake-candidate-v1 — model-migration-kit</title>
221:FAKE MODELS — these numbers describe scripted responses, not a real provider; …
```

**VERDICT: STILL LIVE as to the terminal, and the note's correction is right.**
Two bands, one of them the `<title>`, exactly as claimed. *"`--quiet` produces a
clean-looking report"* is false of the HTML. Merging into finding 22 is correct.

*Additional, and the note does not say it:* `--quiet` on current `main` is **not**
silent — it prints the path **and** `VERDICT: NO-GO (exit 1)`. The original
finding quoted a terminal showing only `q2.html`. So the terminal half has moved
too, and any restatement of 22 must be re-measured rather than inherited.

---

### Findings whose citations I verified verbatim against `main`

| # | The note's claim | Verified on `main` at `25bc7ea` |
|---|---|---|
| 3 | the missing disclosure is one clause earlier | `report.py:3096-3098` builds exactly *"{n} of the {N} comparisons drawn in this document names a Fake adapter on at least one side; the other one does not."* **Exact.** |
| 7 | *five* unique paths, not six; all dead | See below — **five, all `exists=False`.** |
| 17 | the plan *permits* the fallback | plan `~:427-432`: *"the fallback is permitted, but the next chunk needs to know it happened, so record nothing rather than record a guess"*. **Exact.** |
| 17 | `comparison.py:1229` hardcodes it at `n == 0` | `comparison.py:1229: "min_rate": thresholds.pass_rate_floor,`. **Exact line number, still.** |
| 19 | all three keys always written | `comparison.py:647-663` writes `imputed`, `parse_failures`, `item_counts` unconditionally. **Substance exact, 1-line offset.** |
| 21 | `runner.py:327-328` raises on `n < 1` | `if not isinstance(n, int) … or n < 1: raise ValueError(...)`. **Exact line numbers.** |
| 21 | plan `~:3898` calls this *"the contract's own **Must not**"* | plan line 3898: *"Both are the contract's own **Must not**"*. **Exact.** |
| 25 | the truncated prefixes visibly differ | `series.py:702-724` — `_incomparable` compares full hashes, and the message prints **both** prefixes via `_hash()` (16 chars, `series.py:935-937`). The note's narrowing is right. |
| 27 | R23.2 and R33 ratified the anchor | plan `4534` (R23.2 heading), `5864-5865` (*"R23.2's hedge anchor. C14b shipped it as an `<h2 id="excluded">` in both states…"*). **Cited range correct.** |
| 28 | the caption already refuses to be a trend | `report.py:4532-4534`, **rendered template text**, not a docstring: *"Nothing is interpolated: no line joins the markers, because a line between two runs would assert a pass rate on the dates in between…"*. **Exact.** |
| 30 | the caption discloses in both directions | `report.py:4536-4537`, rendered: *"…whenever the clock agrees with the file, **and is not when it does not**…"*. **Exact, and the note is right that the first audit quoted only the trailing half.** |
| 34 | plan R20 created `stale_after_days` for this | plan `669-674`: *"`CandidateField` gains an eighth field, `stale_after_days: float`…"*; plan `751-753`: *"check the default is defensible and, more importantly, **that it is a parameter rather than a literal**"*. **Both exact.** |
| 38 | `Thresholds.to_dict()` emits exactly six keys | `judging.py:163-171` — six keys, and `__post_init__` validates exactly six. So the `warp_factor` row **is** unproducible. **Confirmed.** |
| 38 | the copy is *"a second time, deliberately"* | `comparison.py:740-742` docstring, verbatim. **Exact.** |
| 39 | the plan citation is `:2101-2102`, not `:2095` | plan `2101-2102`: *"Missing data stated as zero is worse than missing / data stated as missing."* Line 2095 is a different sentence. **The self-correction is right.** |
| 39 | R5 says *"Neither is part of this plan"* | plan `2092`. **Exact.** |

Finding 7's path count, reproduced on `main`:

```
unique path-like strings: 5
  exists= False  C:\...\Temp\migkit-demo-va94zou1\demo.toml
  exists= False  C:\...\Temp\migkit-demo-va94zou1\demo_goldenset.jsonl
  exists= False  C:\...\Temp\migkit-demo-va94zou1\evidence.jsonl
  exists= False  C:\...\Temp\migkit-demo-va94zou1\fake-baseline-v1__5fef50364057cad8.jsonl
  exists= False  C:\...\Temp\migkit-demo-va94zou1\fake-candidate-v1__5fef50364057cad8.jsonl
```

**Five, and every one dead.** The note's arithmetic correction is right and the
underlying defect **STILL LIVE**.

---

### Where the notes are now stale, because `main` moved

These are the corrections the second machine could not have made, and they are the
reason this cycle was worth running.

#### Finding 4 — "the plan already records it open" is **no longer true**

The note's quote of R31.4 is verbatim (plan `5607-5611`). But R31.4 **was closed**
while this audit was being written. Plan `5871`:

> ### R34 — closing R31.4: the asymmetry is real, and it already ships a sentence

and C18's fix pass merged at `f65342a` *"Merge C18's fix: the count excludes what
it could not check, and says how many"*. It is in the code:

```
$ git grep -n "unrecorded_comparisons" HEAD -- src/ | head -3
src/model_migration_kit/report.py:983:    unrecorded_comparisons: int
src/model_migration_kit/report.py:1106:        unnamed = self.unrecorded_comparisons
src/model_migration_kit/report.py:2061:            unrecorded_comparisons=sum(
```

**But R34 does not fix the `<title>`, and says so deliberately.** R34.3:

> The tempting fix is to make the band series-scoped so the two disclosures match.
> **Refused.** … **Ruling: every provenance sentence names its own scope, and the
> scopes stay different.**

And `_warned_title` on `main` still keys on the series-scoped flag —
`report.py:5315`:

```python
    if not model.is_demo or head.upper().startswith(_FAKE_TITLE_PREFIX):
        return head
    return f"{_FAKE_TITLE_PREFIX} {EM_DASH} {head}"
```

with `is_demo` documented at `report.py:1986-1997` as *"True when a `Fake*` adapter
produced **any run this document shows**"*, and `_default_title` taking
`model.baseline.model_id` / `model.candidate.model_id` — headline-scoped.

**VERDICT on finding 4: STILL LIVE, and the weakening is now the wrong weakening.**
It is not *"recorded open"*; it is **already ruled, by a ruling that deliberately
leaves this state reachable**. The `<title>` is a *fifth* disclosure site that
R34.3's "every sentence names its own scope" rule was never applied to — and
`report.py:3018-3030` shows the project already treats exactly this shape as a
real defect at a different site:

> The old paragraph was headline-scoped while `is_demo` is series-scoped, so a
> real headline over a scripted history printed, verbatim: *"At least one side of
> this comparison was produced by a Fake adapter (AnthropicAdapter for the
> baseline, OpenAICompatAdapter for the candidate)."* **Both named adapters are
> real.**

R29.1 fixed the paragraph. The `<title>` was not in scope. **This is a genuine
second site and should be scheduled**, by the same argument the note itself uses
to keep finding 21.

#### Finding 21 — right for the right reason, wrong line number, and `main` confirms it

The note keeps 21 as *"a genuine second site"* at `report.py:2519`. That line
number is **stale** — `report.py:2519` on `main` is inside `dimension_cell`'s loop.
The live sites are:

```
report.py:1966:  n_per_item=int(payload.get("n_per_item", 0) or 0),
report.py:2667:  n_per_item = int(side.get("n_per_item", 0) or 0)
```

`or 0` makes a recorded `0` and a missing key indistinguishable, feeding
`report.py:1776`'s *"{label} run does not record how many completions were
expected"*.

**And C22b's fix pass, merged at `25bc7ea`, says in its own commit message that it
did not close this family:**

> Reported and deliberately not changed: four more shared fields —
> `goldenset_hash`, `judges_hash`, `config_hash`, `config_path` — still split on a
> falsy recorded value. **R36.4 named five sites and there are nine.**

C22b fixed the **string** coercion (`str(x or "")` vs `_text`). `n_per_item` is an
**int** coercion and was never in scope. **VERDICT: STILL LIVE, confirmed by the
fixing commit's own admission.**

#### Finding 17 — the rigor citation is half wrong, the conclusion is right

The note says *"rigor writes `"min_rate": threshold` on **both** branches
(distribution.py:794, 817)"*. `distribution.py` is not in this repo — it is
`opik_rigor` 0.2.0 in the venv. There, `794` is not a branch and not a write:

```
794:    threshold = _validate_unit(min_rate, "min_rate", exclusive=False)
817:        "min_rate": threshold,
```

`817` is the **only** occurrence, and it is in the report dict built at `806`
**before** the pass/fail split at `805`, with `_record` called on both paths. So
the substance — *`min_rate` is always present, so the trigger is unproducible* —
is **right**, but "both branches at 794 and 817" is a mis-description of a single
unconditional write. **VERDICT: conclusion correct, citation sloppy.**

---

## Ranking, by how badly a reader would be misled

**Real defects, still live on `main` at `25bc7ea`:**

1. **Finding 4 (`<title>` shouts FAKE MODELS over real model ids).** Highest harm:
   the `<title>` is what a link preview and a screen reader announce — the code's
   own comment at `report.py:5290-5294` says so — and it is the one surface that
   survives being pasted into a deck. Its weakening is now stale and its ruling
   (R34.3) deliberately did not reach it. **Schedule.**
2. **Finding 7 (five dead paths in the provenance block).** Reproduces on `main`;
   a reader told *"shown once, whole, and where it can be checked"* cannot check
   any of the five.
3. **Finding 21 (`n_per_item: 0` renders as "not recorded").** The fixing commit
   for the adjacent family states it left this one open.
4. **Finding 16/22 (terminal disclosure is all-or-nothing).** Real, but re-measure
   before restating: `--quiet` now also prints the VERDICT line.

**Correctly weakened — the notes are sound:** 1 (demote to Tier 4), 3, 5, 14, 19,
25, 27, 28, 30, 34, 38, 39. Findings 5, 14, 19 and 38 are all the same shape —
states the tool's own writer cannot produce — and the notes concede this plainly,
which is the right call.

**Style preference rather than defect:** finding 28's tension between *"no
longitudinal trend … once"* and the run-history section. Both strings render; the
caption already disclaims interpolation. Nothing false reaches the reader.

**Wrong as written:** finding 17's rigor line citation (above). Finding 21's
`report.py:2519`. Finding 4's *"records it open"*. None of the three changes the
verdict it supports.

---

## What should be scheduled

1. **The two jobs that were asked for and did not arrive** — `scripts/audit/`
   tooling with a README, and the `render_terminal` audit. Neither is started.
2. **Finding 4 as a real chunk**, under R34.3's own rule: every provenance
   sentence names its own scope. The `<title>` does not.
3. **The int-coercion family** (`n_per_item`, and the four string fields C22b
   named), as the ruling C22b's message asks for rather than a sweep.
4. **A recomputation script in `scripts/audit/`** that emits the p-value at all
   three units and **labels sidedness**, because the absence of that label cost
   this cycle twenty minutes and would cost a reader the whole finding.

---

## Method note

Read-only against `mk-main` throughout (`git show` / `git log` / `git grep`); no
tracked file touched; every reproduction wrote into a scratchpad, and this file
was written from a detached worktree at `mk-watch`. No fix was made to the
project.
