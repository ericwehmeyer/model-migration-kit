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

---

## Cycle 2 — 2026-08-24, on `a4b3c7f` … `6484193`

**What landed.** Both remaining jobs, in five commits.

| commit | what |
|---|---|
| `a4b3c7f` | **Job 1** — `scripts/audit/`: six modules + a 250-line README, 2,013 lines |
| `fac53f6` | **Job 2** — `AUDIT-terminal.md`, findings T0–T23 |
| `b3e4f5c` | untrusted input (T24–T28) and the absence sweep pointed at the terminal (T29–T32) |
| `550087d` | terminal mutation testing; **and the three stale notes from Cycle 1 corrected** |
| `6484193` | the terminal at scale |

`550087d` folded Cycle 1's three corrections back into `AUDIT-macbook.md`
accurately, including the one that makes finding 4 **stronger** rather than
weaker. The loop closed. **Both assigned jobs are complete.**

Everything below ran on Windows 11, `python 3.14.4`, against `main` at `25bc7ea`.

---

### Job 1 — the tooling, run on Windows

Every tool was executed. **All six run on Windows unmodified**, exit 0.

| tool | Windows | evidence |
|---|---|---|
| `recompute.py` | **works** | reproduces the shipped numbers exactly, below |
| `differential_render.py` | **works** | `sweep` 161 paths in **1m23s**; `quote` works |
| `page_text.py` | **works, one caveat** | needs `PYTHONIOENCODING=utf-8` when redirected |
| `fixtures.py` | **works** | wrote both scenarios; imports `tests/test_report.py` fine |
| `masking.py` | **works, POSIX-only path mask** | as its own README already declares |
| `mutation_harness.py` | `--list` **works** | 30 mutations; a full run needs a worktree I was told not to create |
| `netguard` / `shuffle_order` | **work** | as pytest plugins with `PYTHONPATH` |

**`recompute.py` is the strongest of the six.** It reproduces, on Windows,
against current `main`, every number Cycle 1 derived independently with `scipy`:

```
Mann-Whitney U, candidate < baseline
  each item x5 (what the payload records)      : U=1450.0  p=0.007843147236661033
  12 independent items                         : U=  58.0  p=0.15250667081848462

Wilson at confidence 0.95
  candidate, 5x       45/60  [0.6277, 0.8422]  width 0.2146  one-sided lower 0.6486
  baseline,  5x       55/60  [0.8193, 0.9639]  width 0.1446  one-sided lower 0.8385
```

and the page prints `[0.6277, 0.8422]`, `0.6486`, `0.8385`, `0.007843` — **all
four match**, from Wilson arithmetic written out from the formula rather than
calling `opik_rigor`. Two independent implementations, one of them not the code
under audit, agree to ten significant figures. This is the tool that should
become a permanent test.

**Windows caveats, neither of them blocking:**

* `page_text.py report.html > report.txt` mangles every em dash to `?` under the
  default `cp1252` stdout. With `PYTHONIOENCODING=utf-8` it is clean:

  ```
  default : FAKE MODELS ? NO-GO ? fake-baseline-v1 to ...
  utf-8   : FAKE MODELS — NO-GO — fake-baseline-v1 to ...
  ```

  Since half this project's findings turn on em dashes, this would silently
  corrupt a Windows sweep. **One line to fix** (`reconfigure(encoding="utf-8")`
  on stdout); the README should say so as it says the masking one.

* `masking.py`'s `_ABS_PATH = re.compile(r"/(?:[\w.@+-]+/)+[\w.@+-]*")` does not
  match `C:\Users\…`. **The README already declares this**, accurately.

**What it would take to make these permanent tests.** `recompute.py` is closest:
it is deterministic, offline, ~1 s, and asserts numbers the report prints — it
could be a test today. `differential_render.py sweep` is 1m23s for **one** of
five fixtures; the full sweep is minutes, so it belongs in a nightly or a
`-m slow` marker, not the merge gate — but a **regression form** of it is cheap
and would have caught what is below: pin the per-fixture counts (collisions,
reverse, invisible) and fail when one moves. `mutation_harness.py` cannot be a
test at all — it edits source in a worktree — but its catalogue is a checklist.

---

### The tooling found a live regression on `main` in its first run

**This is the most important result in this cycle, and the second machine has not
seen it** — it swept against the audit baseline, and `main` is 17 commits past
that.

Same tool, same fixtures, only the source tree differs:

```
audit baseline (mk-watch, ~e2b0614):
  single/comparison: 161 paths | collisions 45 | reverse 2 | trivial 47 | zero-and-absence-both-invisible 38 | clean 29

current main (mk-main, 25bc7ea):
  single/comparison: 161 paths | collisions 46 | reverse 2 | trivial 47 | zero-and-absence-both-invisible 37 | clean 29
```

Exactly one leaf changed class, and comparing the two `--json` dumps names it:

```
paths with different equality pattern: 1
  judges[0].item_counts.items
     main  : (('A',), ('B','C1','C2'), ('C3',))
     watch : (('A','B','C1','C2'), ('C3',))
```

On the baseline the field rendered nothing — A, B and both absences were
identical. On `main` the recorded value now renders, **and a recorded zero is
byte-identical to the key being removed and to the key being null.** `quote`
gives the page region:

```
# single/comparison  judges[0].item_counts.items   base=12 A=12 B=0
# absence variants byte-identical to the measured zero: ['C1', 'C2']
--- variant A ---                    --- variant B ---
      golden-set items                     golden-set items
          items |                              items |
      no previous run |                    no previous run |
      12 |                                 unrecorded |
--- variant C1: BYTE-IDENTICAL to variant B ---
--- variant C2: BYTE-IDENTICAL to variant B ---
```

**A golden set recorded as holding zero items renders as the word
`unrecorded`.** That is this project's central rule — *an absence must not render
as a measurement* — in its mirror form, and it is **newly reachable**.

Introduced by **C14c**, `bfd06fb` *"C14c: the last four unread fields"*:

```
$ git log --oneline -S"'items': 'golden-set items'" e2b0614..HEAD -- src/model_migration_kit/report.py
bfd06fb C14c: the last four unread fields, and the line's disclosures below the chart
```

`report.py:4686` adds `'items': 'golden-set items'` to the parameter-strip label
map, so the field reaches the page for the first time. It reaches it through
`series.py:231` → `_count`, whose own docstring states the collapse:

> Anything genuinely uninterpretable becomes `0`, the same value an absent key
> gives, **because both are the same statement.**

For a *count* of golden-set items those are **not** the same statement, and
`_count_cell` then prints the word `unrecorded` for the recorded zero.

**VERDICT: a live regression on `main`, introduced by a chunk whose whole purpose
was to make unread fields reach the reader, in the family C22b's fix pass says it
left open. Found by the second machine's tool on its first Windows run. Schedule
it.**

---

### Job 2 — the terminal audit

Spot-verified on the findings with the highest harm and the most checkable
claims. **Everything I checked held.**

#### T1 — the audit asked Windows to confirm or kill this. **Confirmed.**

The finding says a non-UTF-8 console turns NO-GO (exit 1) into exit 3, that the
trigger is the `…` rich inserts when truncating, and therefore that it is
**width-dependent**; and it says *"I could not test Windows from here; one command
confirms or kills it there."* Run there:

```
enc=utf-8   COLUMNS=80  exit=1 VERDICTlines=2
enc=utf-8   COLUMNS=400 exit=1 VERDICTlines=2
enc=ascii   COLUMNS=80  exit=3 VERDICTlines=0  UnicodeEncodeError: 'ascii'
enc=ascii   COLUMNS=400 exit=1 VERDICTlines=2
enc=cp437   COLUMNS=80  exit=3 VERDICTlines=0  UnicodeEncodeError: 'charmap'
enc=cp437   COLUMNS=400 exit=1 VERDICTlines=2
enc=cp850   COLUMNS=80  exit=3 VERDICTlines=0  UnicodeEncodeError: 'charmap'
enc=cp850   COLUMNS=400 exit=1 VERDICTlines=2
enc=cp1252  COLUMNS=80  exit=1 VERDICTlines=2
enc=cp1252  COLUMNS=400 exit=1 VERDICTlines=2
```

**Every prediction is right, including both qualifications.** `cp437` and
`cp850` — the legacy console codepages — give exit **3** with the verdict never
printed, and only at the narrow width. `cp1252` exits 1 at both widths, exactly
as the note said it would.

**One correction to its reachability claim**, which is the point of asking a
Windows machine. The audit says *"On a Windows console at `chcp 437`/`850` it is
the default — and that is the machine this project's pipeline runs on."* On this
machine it is **not** the default:

```
stdout.encoding      = cp1252
getpreferredencoding = cp1252
utf8_mode            = 0
version              = 3.14.4
```

**VERDICT: mechanism STILL LIVE and fully confirmed; reachability narrower than
stated.** It needs a console at `chcp 437`/`850` or an explicit
`PYTHONIOENCODING`, not the default here. Real, and one notch below the tier the
audit puts it in.

#### T0 — the broken pipe. **Different failure on Windows, same class, worse code.**

```
unpiped              : exit=1   VERDICTlines=2  stderrbytes=0
| head -40           : exit=120 VERDICTlines=0  stderrbytes=130
| cat (drains)       : exit=1   VERDICTlines=2
```

and the 130 bytes are:

```
migkit: OSError: [Errno 22] Invalid argument
Exception ignored while flushing sys.stdout:
OSError: [Errno 22] Invalid argument
```

So on Windows the process does **not** take rich's `on_broken_pipe` →
`SystemExit(1)` path the audit diagnoses. It raises `OSError(EINVAL)`, migkit's
`except Exception` **does** catch it — `contextlib.suppress(BrokenPipeError)` at
`cli.py:424` does not match `EINVAL` — and CPython then fails to flush stdout at
shutdown and forces **exit 120**.

**VERDICT: STILL LIVE, and the Windows form is worse than the macOS one.** The
symptom the audit cares about is identical — the verdict line never prints, and
the reader's decision to stop sets the exit code. But `120` is not `0`, not `1`,
not `2`, not `3`: it is **outside the tool's documented exit-code vocabulary
entirely**, so a pipeline that distinguishes NO-GO from tool-failure gets neither.
The audit's cause analysis is macOS-specific and should say so; the finding
itself is platform-independent and stronger for it.

The promise it falsifies is verbatim on `main`, `cli.py:251`:

> the write is dropped and **the exit code is left alone** — turning that into an
> error would make a perfectly good report look like a failed one

#### The mutation-testing headline. **Confirmed by construction, without mutating anything.**

The claim: the FAKE MODELS band can be deleted and its own test still passes,
because the terminal assertion is a bare case-insensitive substring for `FAKE`
while the render also contains `FakeAdapter` and `fake-judge-v1`.

`tests/test_report.py:2440-2444`, on current `main`:

```python
    _get(_module(), "render_terminal")(
        _from_evidence(scenario),
        console=Console(file=buffer, width=100, no_color=True, force_terminal=False),
    )
    assert "FAKE" in buffer.getvalue().upper()
```

And the project owns the right constant, used at four sites — all of which take a
parsed `document`, i.e. all HTML-side:

```
tests/test_report.py:155:  FAKE_BAND_MARKERS = ("FAKE MODELS", "scripted responses")
tests/test_report.py:1689  1692: document.title / document.text
tests/test_report.py:3071  3072: document.text
tests/test_report.py:3117
tests/test_report.py:5128
```

Proof the substring survives, from a real terminal render with the two band lines
removed — no source mutation required:

```
$ grep -i -v "FAKE MODELS|scripted responses" loud.txt | grep -c -i fake
8
```

Eight surviving lines: `fake-baseline-v1`, `fake-candidate-v1`, `FakeAdapter`
(×2), `fake-judge-v1`. **VERDICT: exactly right. A one-line regression removes
the scripted-models disclosure from the terminal and CI stays green.** This is
the highest-value finding in the terminal audit, above T0 and T1, because it is
the one that makes every *other* terminal disclosure untrustworthy.

#### The scale claim: **"no row cap anywhere in the terminal path"** — confirmed

```
$ git show HEAD:src/model_migration_kit/report.py | sed -n '/^def render_terminal/,/^def render_html/p' | grep -n '\[:'
82:  _cell(f"{gs['path'] or TERMINAL_DASH} ({gs['hash'][:16] or TERMINAL_DASH})"),
85:  facts.add_row("judges hash", _cell(model.hashes.get("judges", "")[:16] or ...))
90:  f"({model.hashes.get('config', '')[:16] or TERMINAL_DASH})"
```

**Three `[:16]` hash truncations and nothing else**, precisely as stated — which
also confirms the sub-finding that the terminal prints 16 of 64 hash characters
under the HTML's label for all 64.

#### T24 — the unsanitised line. Site confirmed; injection not independently reproduced.

`cli.py:437` on `main` is a raw f-string, under a comment promising it always
prints:

```python
    verdict = model.verdict or NO_VERDICT
    code = Verdict.exit_code(model.verdict or Verdict.ERROR)
    _out(f"VERDICT: {verdict} (exit {code})")
```

**VERDICT: the site is real and unsanitised.** I did not build the crafted
verdict word, so I am not claiming the screen-clear payload — only that the line
the tool promises always prints is the one line that does not go through
`_CONTROL_RE`. That matches what the audit says about it.

#### One count I could not match

The census says *"8 test functions in the whole repository call `render_terminal`
against 112 that render HTML"*. I count **5 direct call sites**:

```
tests/test_report.py:2420, 2440, 13978
tests/test_report_scale.py:633
tests/test_report_untrusted_input.py:276
```

Parametrisation plausibly takes 5 call sites to 8 *functions*, so this is likely
a difference in what is being counted rather than an error — but the number
should say which. **The substance is not in doubt** either way.

---

## Ranking after two cycles

**Schedule first:**

1. **`judges[0].item_counts.items` — the C14c regression.** New on `main`, in the
   project's central rule, found by the second machine's own tool. Nobody has
   seen it but this file.
2. **The terminal's `assert "FAKE" in ….upper()`.** One line to fix — wire the
   terminal test to `FAKE_BAND_MARKERS` — and it is the assertion holding up
   every scripted-models claim on that surface.
3. **T0, the broken pipe.** Now known to corrupt the exit code on *both*
   platforms, by two different mechanisms, into two different wrong codes
   (`1` on macOS, `120` on Windows). `cli.py:251` promises the opposite.
4. **Finding 4, the `<title>`** — carried from Cycle 1; R34.3 ruled beside it and
   not on it.
5. **T1** — real, confirmed on Windows, but gated behind a non-default codepage.

**Tooling to promote:** `recompute.py` as a test today; a pinned-counts
regression form of `differential_render.py sweep` as a nightly — it would have
caught item 1 the day C14c merged.

**Tooling to fix:** one line of encoding in `page_text.py`.

---

## Method note, cycle 2

Read-only against `mk-main` throughout. The tooling was run from `mk-main` as CWD
so `scripts/worktree_path.py` resolved `main`'s source, and from `mk-watch` for
the controlled baseline comparison — verified both ways before trusting either:

```
from mk-main : C:\Users\ewehm\repos\mk-main\src\model_migration_kit\__init__.py
from mk-watch: C:\Users\ewehm\repos\mk-watch\src\model_migration_kit\__init__.py
```

No source file was mutated; the FAKE-band result was obtained by construction
instead. `git status` clean in both worktrees. No fix was made to the project.

---

## Addendum — correcting my own claim, `main` at `59c5e9b`

`main` moved two commits while Cycle 2 was being written, and one of them
falsifies a sentence I wrote. Correcting it here rather than editing it away.

**I wrote:** *"the second machine has not seen it … nobody has seen it but this
file."*

**That was true at `25bc7ea` and is false at `59c5e9b`.** `0d5df07` *"Merge the
absence sweep: the audit's technique as a permanent test"* landed a parallel
agent's implementation of the same technique, and `tests/test_absence_sweep.py`
records the identical leaf:

```
$ git show HEAD:tests/test_absence_sweep.py | grep -n "item_counts.items"
128:    "judges[0].item_counts.items": ("key-removed", "key-null"),
```

with exactly the two absence variants my `quote` run named — `key-removed` and
`key-null` — in a table whose own docstring describes it as conflations left
*"for somebody else to rule on"*.

**Three things follow, and two of them strengthen the finding:**

1. **It is confirmed independently.** Two implementations of the differential
   technique, written on different machines without contact, converged on the
   same leaf and the same two variants. That is as good as this kind of evidence
   gets.
2. **It is recorded but not fixed.** `SWEEP_RECORDED_CONFLATIONS` pins the
   current set so it cannot grow silently. That is a real improvement and it is
   the right shape — but a pinned defect is still a defect, and a reader of the
   page still sees a golden set of zero items described as `unrecorded`.
3. **The part that is still only here is the provenance.** The sweep table
   records the conflation as a flat fact with no history. My controlled
   comparison — same tool, same fixtures, `mk-watch` against `mk-main` — shows
   this leaf was **not** conflated at the audit baseline and became conflated
   through C14c (`bfd06fb`). It is a **regression**, not a longstanding
   conflation, and it entered on the commit whose stated purpose was to make
   unread fields reach the reader.

That distinction is what decides whether it belongs in a pinned list or in a fix
pass. **It should be re-ranked as a regression and scheduled**, and the recorded
entry should carry the commit that introduced it.

**Also superseded, and worth saying plainly:** my Cycle 2 recommendation to
"promote a pinned-counts regression form of `differential_render.py sweep` to a
nightly" was already done, better, by `a9b73a9` — which memoised the jinja2
environment for a 25x speedup rather than trading away coverage, and swept every
numeric leaf. The recommendation stands as made and was overtaken while it was
being written; the credit is not mine.

Final state checked: `main` at `59c5e9b`, `mk-main` working tree clean, no
tracked file in it modified at any point.

---

## Cycle 3 — 2026-08-24, on `48d4c36`, verified against `main` at `b2c0005`

One commit landed since `a842065`:

```
48d4c36 page_text.py: drop SVG <title>, which the audit harness was reading as prose
```

It is the JOB-3 prerequisite, not a JOB-3 finding. `AUDIT-gates.md` has not
arrived. Everything below is about that commit, plus one open question it let me
close.

**`main` moved twice while this was being written** — `630912d` → `d504b78`
(R40) → `b2c0005` (METRICS). Neither touched the code under test, so every
measurement here holds at `b2c0005`, and I re-ran the two decisive ones there
rather than asserting it:

```
$ git diff --stat 630912d b2c0005 -- src tests
                      (no output: no file under src/ or tests/ changed)
```

---

### V1 — the `<title>` fix does exactly what it claims. **CONFIRMED.**

Rendered a fresh demo from `main` and ran both revisions of the module against
the same file:

```
$ python -m model_migration_kit.cli demo --out demo_main.html
$ python page_text_OLD.py demo_main.html | grep -c "candidate accuracy: pass rate"
1
$ python page_text_NEW.py demo_main.html | grep -c "candidate accuracy: pass rate"
0
```

And the premise holds — the string really is screen-reader-only:

```
occurrences of needle in raw HTML: 1
  enclosing tag start: <title>candidate accuracy: pass rate 75.
--- svg block len 663
  <text> elements in that svg: 0
```

The reasoning in the commit message is right and the change is worth having.
The rest of this section is about what came with it.

---

### V2 — it also drops the document `<head><title>`, which this project's own contract calls the one thing a screenshot cannot crop. **REAL DEFECT, introduced by `48d4c36`, not present before it.**

The whole text diff between the two revisions on the demo page is three lines,
and only two of them are SVG:

```
$ diff text_old.txt text_new.txt
3,4d2
< FAKE MODELS — NO-GO — fake-baseline-v1 to fake-candidate-v1 — model-migration-kit
11,12d8
<   candidate accuracy: pass rate 75.0%, interval 62.8% to 84.2%, floor 90.0%
228,229d223
< Candidate pass rate over 1 run(s); the horizontal axis is time.
```

The first is `<head><title>`. It is **not** a tooltip; it is the browser tab, the
bookmark and the window title, and this project treats it as load-bearing in two
separate contract tests:

```
tests/test_cli.py:1002
    Five places say it and none is a footnote. Two are asserted here because
    they are the two a screenshot cannot crop away: the ``<title>`` and the
    band above the verdict banner.

tests/test_report.py:1749
    assert ("FAKE" in document.title.upper()) is is_demo, (
        f"§2.2 item 0 repeats the warning in the <title>; got {document.title!r}"
    )
```

`test_report.py` reads `document.title` **separately from** `document.text` — the
project already distinguishes the two, which is the argument *for* the change and
also the reason it should not have been made by the same regex.

The measurable cost:

```
$ for s in "FAKE MODELS" "Methodology appendix" "Flips" "Provenance" "roughly 140"
FAKE MODELS              old=2 new=1
Methodology appendix     old=2 new=2
Flips                    old=2 new=2
Provenance               old=2 new=2
roughly 140              old=2 new=2
```

The FAKE-models warning is now visible to the harness in one of the two places
the contract names, and the one it lost is the uncroppable one.

**This is not an argument for reverting.** It is an argument for scoping the drop
to `<title>`/`<desc>` *inside `<svg>`*, or for exposing the document title as a
separate return value. Neither the module docstring nor the README says the head
title is gone; both talk only about SVG tooltips.

---

### V3 — the commit's own verification is the weak form of the check that would have caught V2. **METHOD NOTE.**

> *"Verified afterwards that real prose is untouched (`FAKE MODELS`, `Methodology
> appendix`, `Flips`, `Provenance`, `roughly 140` all still found)."*

`still found` is a boolean. `FAKE MODELS` is still found and half of it is gone.
Rule 4 on this branch is *every claim carries its output*; the output here was a
`grep`, and `grep -c` instead of `grep -q` was the whole difference. Worth
carrying into JOB-3, where the same shape of claim ("the gate still passes") will
be made about trees that changed underneath it.

---

### V4 — the harness's documented CLI invocation dies with **zero bytes and exit 1** on any report carrying a character outside the Windows ANSI code page. **STILL LIVE, and worse than this file recorded it.**

Cycle 2 of this file recorded it as a caveat:

```
AUDIT-VERDICTS.md:374  | `page_text.py` | works, one caveat | needs PYTHONIOENCODING=utf-8 when redirected |
AUDIT-VERDICTS.md:401  * `page_text.py report.html > report.txt` mangles every em dash to `?` under the …
```

`48d4c36` did not touch it. It is not mangling, and the severity is a category
higher. `main`'s own contract test requires the renderer to emit exactly the text
that kills it:

```
tests/test_report.py:2430
    accented = "café naïve — 你好"
    …
    assert accented in text
```

So I built that page with the real renderer — not a hand-written fixture — and
ran the shipped harness on it:

```
$ python -c "… tr._scenario(…, candidate_output='café naïve — 你好'); render_html(…)"
wrote: …\cjkreal\real.html 26886 chars
contains accented: True

$ python scripts/audit/page_text.py cjkreal/real.html > out.txt
exit=1
  File "…\encodings\cp1252.py", line 19, in encode
UnicodeEncodeError: 'charmap' codec can't encode characters in position 9179-9180
--- stdout size: 0 bytes ---
```

Under a cp437 console — a Windows default — **the plain bundled demo is enough**,
no exotic input required, because the report is full of em dashes:

```
$ PYTHONIOENCODING=cp437 python page_text.py demo_main.html > cp437.txt
exit=1   bytes=0
UnicodeEncodeError: 'charmap' codec can't encode character '\u2014' in position 16
```

**Why this is worse than "mangles".** The failure mode of a zero-byte stdout in
this toolkit is *every sentence reports as absent*. A caller that does
`page_text.py r.html | grep -c "…"` gets `0` and a clean-looking pipeline; the
traceback is on stderr, which a sweep script discards. For a harness whose entire
job is finding disclosures that are missing, the failure direction is the worst
available one.

**Scope, measured, not assumed.** Only the CLI path is affected. The two
in-process callers import the function and never touch stdout:

```
scripts/audit/differential_render.py:106  from page_text import html_to_text
scripts/audit/mutation_harness.py:79      from page_text import html_to_text
```

**And the recorded mechanism does not reproduce here. NOT REPRODUCIBLE as
described.** No em dash became `?` in any run. This shell is `chcp 65001` with
`[Console]::OutputEncoding = utf-8`; redirected, `sys.stdout.encoding` is
`cp1252`, in which an em dash is byte `0x97` — mojibake to a UTF-8 reader, not a
question mark. The earlier `?` was probably a different console code page. The
*existence* of the defect is confirmed; its *mechanism* as recorded is wrong, and
the recorded remedy does work:

```
$ PYTHONIOENCODING=utf-8 python page_text.py cjkreal/real.html > out.txt
exit=0   bytes=16347   "你好" occurrences: 3
```

macOS cannot see any of this. That is why it is here and not there.

---

### V5 — the headline result: **the `<title>` fix silently erased a REVERSE finding, and the sweep's counts are not comparable across it.** **REAL, and it lands on JOB-5.**

This matters because JOB-5's whole contract is *report only the delta*. Ran the
`single/comparison` sweep twice against **the same source tree**, changing only
which revision of `page_text.py` was in `sys.modules`:

```
sweep_newpt.json  single/comparison: 161 paths | collisions 46 | reverse 1 | trivial 47 | zero-and-absence-both-invisible 38 | clean 29 | errors 1
sweep_oldpt.json  single/comparison: 161 paths | collisions 46 | reverse 2 | trivial 47 | zero-and-absence-both-invisible 37 | clean 29 | errors 1
```

Exactly one path moves, and the source tree is byte-identical between the runs:

```
paths with different equality pattern: 1
  judges[0].candidate.min_rate
     new page_text: (('A', 'B', 'C1', 'C2'), ('C3',))      <- TRIVIAL
     old page_text: (('A', 'C1', 'C2'), ('B',), ('C3',))   <- REVERSE
```

The region the new tool no longer compares, from the old run's own `--json`:

```
# judges[0].candidate.min_rate   base=0.87 A=0.87 B=0.0
--- A
+++ B
-  candidate accuracy: pass rate 53.5%, interval 29.3% to 74.7%, floor 87.0%
+  candidate accuracy: pass rate 53.5%, interval 29.3% to 74.7%, floor 0.0%
```

That sentence lives only in an SVG `<title>`. Three consequences, and the middle
one is the finding:

1. **The delta is contaminated.** A JOB-5 run today against a pre-`48d4c36`
   baseline shows `reverse 2 → 1, invisible 37 → 38` and nothing in `main`
   explains it, because nothing in `main` caused it. `git diff --stat 630912d
   b2c0005 -- src tests` is empty. **Re-baseline the sweep on `48d4c36` before
   reporting any delta**, or the first delta of the next cycle is a ghost.

2. **The finding it erased got *stronger*, and the tool now files it under a
   bucket that says the opposite.** `judges[0].candidate.min_rate` is the
   judge's floor. After the fix, changing it from `0.87` to `0.0` changes **not
   one byte** of the page's visible text on this fixture — its only appearance is
   an accessible name — so it lands in `TRIVIAL`, whose documented meaning is *"the
   field never reaches the page at all"*. It reaches the page. It reaches it in
   the one form a sighted reader cannot see. The sweep has no verdict for that,
   and it is precisely the class the commit message says it cares about — the
   banner bar's "floor not recorded" is the same shape.

3. The `errors 1` in both runs predates all of this and is unchanged; noting it
   so the next reader does not chase it.

**The sweep needs a fifth verdict — `ACCESSIBLE-NAME-ONLY`** — or the fix has
traded a false positive for a false negative in the direction that hides
findings. That is JOB-6.

---

### V6 — `<desc>` is untested and the alternation has no word boundary. **NIT. Real, small, cheap.**

```
head <title> count: 3   desc: 0
```

There is not one `<desc>` in the rendered report, so that half of the change is
unexercised by the verification the commit ran. And:

```python
re.sub(r"(?s)<(script|style|title|desc).*?</\1>", "", source)
```

has no `\b`, so a future `<description>` element would be swallowed whole
(`<desc` matches, `</desc` matches inside `</description>`). Nothing emits one
today. One character fixes it.

---

### V7 — the docstring and the README describe the old behaviour in opposite words. **STYLE, not a defect.**

```
README.md      "…the sweep that found them would have reported them as present."
page_text.py   "…with the old behaviour both would have been invisible to the
                sweep that found them."
```

Both are defensible — the *disclosure* read as present, so the *finding* was
invisible — and "both" has two possible antecedents. In a module whose entire
argument is precision about what counts as visible, one clarifying word is worth
spending. **Not a defect; I am not asking for a commit on its own.**

---

## Open question 1 — **ANSWERED.** The C14c provenance is confirmed at the commit boundary, and the leaf is still live.

`JOBS.md` records this as *"being re-ranked as a C14c regression rather than a
longstanding conflation"*, on evidence from an approximate baseline (`~e2b0614`,
17 commits back). I pinned it to the commit itself. Same tool, same fixture, only
the source tree differs — resolved through `worktree_path.py`, verified before
trusting either:

```
##### cwd=…\old_c14c_pre        (bfd06fb^ = 8779207, the commit before C14c)
source tree: …\old_c14c_pre\src\model_migration_kit\__init__.py
single/comparison  judges[0].item_counts.items  base=12 A=12 B=0
equality pattern: (('A', 'B', 'C1', 'C2'), ('C3',))
A == B ? True

##### cwd=…\at_c14c             (bfd06fb, C14c itself)
equality pattern: (('A',), ('B', 'C1', 'C2'), ('C3',))
A == B ? False

##### cwd=…\mk-main             (b2c0005, current main)
equality pattern: (('A',), ('B', 'C1', 'C2'), ('C3',))
A == B ? False
```

**Before C14c the field did not reach the page at all. C14c made it reach the
page, and a measured zero renders byte-identically to key-removed and to
key-null.** Not "roughly C14c" — `bfd06fb^` clean, `bfd06fb` conflated, one
commit apart.

The page region, at current `main`:

```
--- variant A ---            --- variant B ---
    golden-set items             golden-set items
        items |                      items |
    no previous run |            no previous run |
    12 |                         unrecorded |
--- variant C1: BYTE-IDENTICAL to variant B ---
--- variant C2: BYTE-IDENTICAL to variant B ---
```

Still pinned rather than fixed at `b2c0005`:

```
tests/test_absence_sweep.py:128
    "judges[0].item_counts.items": ("key-removed", "key-null"),
```

**Ruling: the re-ranking is correct. It is a regression, it is one commit old at
the point it entered, and it belongs in a fix pass rather than a pinned list.**
The Mac's provenance contribution stands, and it is now nailed to the boundary
rather than to a 17-commit window. Question 1 is closed.

Questions 2 and 3 are unchanged: both scheduled, neither newly evidenced this
cycle.

---

## Ranking after three cycles

By what a maintainer would wrongly believe is guaranteed:

1. **V5 — the sweep hides `<title>`-only disclosures in a bucket labelled "never
   reaches the page".** A maintainer reading `TRIVIAL` concludes the field is
   unrendered and stops. It is rendered, to screen readers only. Queued as
   JOB-6.
2. **`judges[0].item_counts.items`** — unchanged in rank, now with exact
   provenance and confirmed live at `b2c0005`. Fix pass, not pinned list.
3. **V4 — the harness's CLI produces zero bytes on legal report content.** Every
   sentence reads as absent, silently, in the tool the audits are measured with.
   Windows-only, so only this side can see it.
4. **V2 — the harness lost the uncroppable FAKE warning.** Small blast radius
   today; it is the one place the contract says cannot be cropped.
5. Previous cycles' items 2–5 (terminal `FAKE` assertion, T0, Finding 4, T1)
   unchanged; nothing this cycle bears on them.

**Not defects:** V7. **Nit:** V6.

---

## Method note, cycle 3

Read-only against `mk-main` throughout; `git status` clean there at start and
end, no tracked file modified. Own writes confined to a detached worktree at
`mk-watch2` and to this session's scratchpad. The two historical trees were
`git archive`d into the scratchpad rather than checked out, so no worktree was
created in, or borrowed from, any other agent's directory.

Source resolution was verified before every comparison rather than assumed:

```
cwd=mk-main            -> C:\Users\ewehm\repos\mk-main\src\model_migration_kit\__init__.py
cwd=old_c14c_pre       -> …\scratchpad\watch2\old_c14c_pre\src\model_migration_kit\__init__.py
cwd=at_c14c            -> …\scratchpad\watch2\at_c14c\src\model_migration_kit\__init__.py
```

The two sweeps in V5 differ **only** in which `page_text` module was injected
into `sys.modules` before `differential_render` imported it; the source tree,
fixtures and pinned `now` were identical. That is what makes the one-path delta
attributable to the tool.

All harness invocations ran under `PYTHONIOENCODING=utf-8`, for the reason in V4.
No fix was made to the project.

---

## Cycle 4 — 2026-08-24, on `71ae353` (`AUDIT-gates.md`, `check_contract.py`), verified against `main` at `b2c0005`

The first of seven gate agents landed. **Every finding below was re-run here as a
constructed tree, not read** — the brief for this job says an argument is not a
finding, and that cuts both ways. Contracts were written into the scratchpad and
the gate was run from `mk-watch2`, so `REPO = C:\Users\ewehm\repos\mk-watch2` and
`REPO.parent = C:\Users\ewehm\repos` are the real thing. No tracked file was
touched.

**Headline: seven of eight findings reproduce on Windows. Three are worse here
than the audit could see from macOS, and one has its mechanism stated too
broadly.** Nothing is refuted.

---

### G1 — the out-of-tree guard is disabled by a slash. **CONFIRMED on Windows, both directions.**

```
$ check_contract.py g1_bare.md         # `judge.py:315`
[FAIL] 1 citation(s) name a file that does not exist
  line 3: judge.py:315 -- resolves only in opik-rigor\src\opik_rigor\judge.py;
          write the path out so it is not read as this package
exit=1

$ check_contract.py g1_dir.md          # `src/opik_rigor/judge.py:315`
[PASS] every cited file exists
[PASS] every cited line is in range
Contract citations check out.
exit=0
```

Following the gate's own remediation string turns the gate off. `src/opik_rigor/`
reads as this package's `src/`, resolves into a different distribution, exit 0,
no diagnostic. **SURVIVES, unchanged.**

---

### G2 — `resolve()` returns wheels that `_is_a_copy()` calls copies. **CONFIRMED, and worse on Windows.**

```
$ check_contract.py g2_wheel.md
  # `migration-kit/.venv/Lib/site-packages/opik_rigor/judge.py:315`
[PASS] every cited file exists
[PASS] every cited line is in range
exit=0

resolve() returned : C:\Users\ewehm\repos\migration-kit\.venv\Lib\site-packages\opik_rigor\judge.py
_is_a_copy(that)   : True
```

**Worse here for a reason the Mac cannot see.** On macOS the wheel is inside the
checkout's own `.venv`. On Windows `CLAUDE.md` opens with *"There is no venv
inside the worktrees. The only interpreter is
`C:\Users\ewehm\repos\migration-kit\.venv`"* — so the vendored wheel sits under
`REPO.parent`, one relative path away from every worktree on the box, and it is
the interpreter **every agent on this project runs**. `pip install -U` moves
those line numbers and the gate keeps certifying them. **SURVIVES.**

---

### G3 — the third root is the containing directory. **CONFIRMED, and the Windows blast radius the audit could only predict is now measured.**

The audit stated this honestly as *"small blast radius here; large on Windows,
by this project's own convention"* and could not measure it. Here is the
measurement — one constructed pair, same module, same line, one prefix:

```
$ check_contract.py control.md
  # `src/model_migration_kit/report.py:5700`
[FAIL] 2 citation(s) point past the end of the file
  line 3: src/model_migration_kit/report.py:5700 -- file has 5328 lines
exit=1

$ check_contract.py sibling_worktree.md
  # `mk-main/src/model_migration_kit/report.py:5700`
[PASS] every cited file exists
[PASS] every cited line is in range
exit=0
```

`mk-watch2/src/.../report.py` is 5,328 lines; `mk-main`'s is 5,724. **The gate
range-checked line 5700 against another agent's worktree at another commit and
called the contract clean.** `REPO.parent` here holds **70+ checkouts of this
same repository** at 70+ different commits — `git worktree list` names them, and
`CLAUDE.md:80-88` mandates that shape.

**Adversarial note, against myself: I looked for a live instance and found
none.** No tracked `.md` in the repo carries a `mk-*/…py:NN` citation today. The
mechanism is real and measured; the exposure is latent. That distinction belongs
in the ranking and I am making it rather than letting the constructed tree imply
more than it shows. **SURVIVES, exposure latent.** See G4 for the form that is
*not* latent.

---

### G4 — the pasted absolute path. **CONFIRMED, and upgraded: the repo's live example fails today only because that worktree was deleted.**

`docs/release-evidence.md:104` is the live paste the brief names. Its current
behaviour:

```
$ check_contract.py docs/release-evidence.md
[FAIL] 1 citation(s) name a file that does not exist
  line 104: \Users\ewehm\repos\mk-wt-checklist\.venv\Lib\site-packages\_pytest\threadexception.py:58
            -- no such file in either tree
exit=1
```

That is a red gate, not a green one — because `mk-wt-checklist` no longer exists.
The mechanism is intact and visible in what the regex captured:

```
captured citation : '\\Users\\ewehm\\repos\\mk-wt-checklist\\.venv\\...\\threadexception.py'
REPO / captured   :  C:\Users\ewehm\repos\mk-wt-checklist\.venv\...\threadexception.py
```

**The drive letter is supplied silently by `REPO`'s drive, and the checkout name
in the path is whatever was pasted.** So I rebuilt the identical form with a
target that is still alive:

```
    C:\Users\ewehm\repos\mk-main\src\model_migration_kit\report.py:5700

$ check_contract.py g4_pasted_abs.md
[PASS] every cited file exists
[PASS] every cited line is in range
Contract citations check out.
exit=0

resolve() returned: C:\Users\ewehm\repos\mk-main\src\model_migration_kit\report.py
REPO in parents   : False
```

`REPO in target.parents` is **False** — the out-of-tree defence would have caught
this — and it never runs, because the citation has a directory component. G1, G3
and G4 are one defect wearing three shapes, and this is the shape that is already
in the repo. **SURVIVES; this is the one to schedule.**

---

### G5 — "no such file in **either tree**". **CONFIRMED from the opposite side, and stronger than the audit stated it.**

The audit reported the plan as **exit 1, 4 unverifiable citations**, on a machine
with no `opik-rigor` sibling. This machine has one. Same commit, same file
(`git diff --stat bce49c9 HEAD -- <plan>` is empty), same gate:

```
$ check_contract.py docs/superpowers/plans/2026-08-21-migkit-report-plan.md
[PASS] every cited file exists
[PASS] every cited line is in range
[note] 58 symbol(s) resolve nowhere in src/ or tests/.
exit=0
```

against the audit's exit 1 and 65 symbols. Citation counts agree exactly — **102
occurrences, 19 distinct** — and the probe names where they land:

```
distinct .py citations: 19
  this repo: 16
  opik-rigor sibling: 3
     opik-rigor/src/opik_rigor/evidence.py
     opik-rigor/src/opik_rigor/adapters/fake.py
     opik-rigor/src/opik_rigor/judge.py
```

**The gate's exit code and its advisory note are both functions of what happens
to be sitting next to the checkout.** Not of the contract, not of the commit. An
operator following `RESTART.md:590` gets a red gate on one machine and a green
gate on the other for the same dispatch, and neither output says a root was
absent or which roots were consulted. **SURVIVES, and I would rank it above where
the audit put it** — it is the only finding here that changes the gate's *verdict*
across machines rather than its coverage.

---

### G6 — `--from 0` checks zero lines and prints `[PASS]`. **SURVIVES, mechanism stated too broadly. Corrected.**

The finding is real:

```
$ check_contract.py g6_long.md --from 1 --to 3
Checked lines 1-3 of g6_long.md
[FAIL] 2 citation(s) point past the end of the file
exit=1

$ check_contract.py g6_long.md --from 0 --to 3
Checked lines 0-3 of g6_long.md
[PASS] every cited file exists
[PASS] every cited line is in range
exit=0
```

Same file, same citation, one flag, and the header claims a range it did not
inspect. `--from 5000 --to 4000` and `--from 99999` reproduce too.

**But `lines[-1:3]` does not always inspect zero lines**, and the audit states it
as though it does. It is `lines[N-1:end]` for a file of `N` lines: **empty when
`N > end`, and the file's *last* line when `N <= end`.** On my first attempt the
contract was 3 lines with `--to 3`, so it inspected line 3 — and found the
citation:

```
$ check_contract.py g6.md --from 0 --to 3        # a 3-line contract
[FAIL] 2 citation(s) point past the end of the file
  line 0: src/model_migration_kit/report.py:99999 -- file has 5328 lines
exit=1
```

Note `line 0` in that diagnostic, which is its own small tell. Real plans are
thousands of lines, so the zero-inspection case is the one that will occur — the
finding stands and the ranking does not move. **The sentence should say "inspects
zero lines whenever the document is longer than `--to`", not "inspects zero
lines".** A reader who tests it on a short file will conclude the finding is
wrong.

---

### G8 — nothing runs it, nothing tests it. **CONFIRMED on Windows.**

```
$ grep -rn "check_contract" .github/          -> no output
$ ls .pre-commit-config.yaml                  -> no .pre-commit-config.yaml
$ ls tests/ | grep -i contract                -> test_thresholds_confidence_contract.py   (unrelated)
$ ls tests/test_release_checks.py             -> tests/test_release_checks.py             (exists, for the other gate)
```

**SURVIVES.**

---

### W1 — new, Windows-only: the gate takes **7.4 seconds** for one unresolvable citation, against a docstring that promises a second.

`resolve()` falls through to `rglob` over all three roots, and the third is
`C:\Users\ewehm\repos` — 70+ checkouts of this repository plus the shared venv.

```
$ time check_contract.py bare_miss.md     # one citation: `zzz_nonexistent_module.py:12`
[FAIL] 1 citation(s) name a file that does not exist
real    0m7.432s

$ time check_contract.py docs/superpowers/plans/2026-08-21-migkit-report-plan.md
real    0m5.239s
```

The docstring's closing claim is *"None needed judgement to catch. **This finds
them in a second.**"* That is a claim about the gate's fitness for the workflow
it sits in — `RESTART.md:590` puts it in front of every dispatch — and on the
machine that runs the pipeline it is off by 5-7x, scaling with how many worktrees
happen to exist. It is not a correctness defect and I am not ranking it above
any of G1-G8; it belongs in the file because it is invisible from macOS and it
gets worse every time an agent adds a worktree.

---

### Ranking of the gate findings, from this side

`check_contract.py` is a gate whose verdict depends on the contents of the
directory above the checkout. Ranked by what a maintainer would wrongly believe:

1. **G4** — a pasted traceback resolves into another checkout and is certified.
   The intake path is real, the form is already in the repo, and only the
   deletion of one worktree is keeping it red today.
2. **G5** — the same contract at the same commit is exit 1 on one machine and
   exit 0 on the other. A gate that disagrees with itself across machines is
   worse than a gate that is uniformly wrong, because the disagreement teaches
   operators to ignore it.
3. **G1** — the remediation string instructs you to disable the check.
4. **G2** — pinned line numbers inside the wheel that every agent's interpreter
   loads.
5. **G6** — real, one flag wide, mechanism needs the correction above.
6. **G3** — mechanism measured, exposure latent; it is G4's engine.
7. **G8**, **W1** — coverage and fitness, not correctness.

**Agreed and not re-verified:** G7 (the gate checks range, not citation) and G9's
exclusion table — the audit measured those against the real tree exactly as the
brief asked, including reporting one exclusion as **REFUTED and dropped**, which
is the pass doing its job. **G10 (exit codes REFUTED)** I did not re-run; eight
constructed failure paths all non-zero is a negative result that names its
coverage, which is what the brief asked for.

**Nothing in this file is refuted. One sentence needs correcting (G6) and three
findings are worse on this machine than the audit could state (G2, G3→G4, G5).**

---

### Method note, cycle 4

Every contract was written into the session scratchpad and passed to the gate as
an argument; the gate itself was run from `mk-watch2` so that `REPO` and
`REPO.parent` were the real Windows layout rather than a synthetic one. Nothing
was created inside `C:\Users\ewehm\repos` — in particular I did **not** recreate
`mk-wt-checklist` to make `docs/release-evidence.md:104` resolve, because that
directory is in the namespace other agents' worktrees live in. The equivalent
construction with `mk-main` as the target proves the same mechanism without
writing there.

`git status` clean in `mk-main` and in `mk-watch2` apart from this file.

---

## Cycle 5 — 2026-08-24, on `adc3ac1`…`579a92d`, verified against `main` at `981514e`

Five commits landed: three more gate agents (`dependency_surface.py`,
`check_merge.py`, `verify_release.py`), the status line, and `096f5f2` —
the Mac's fix for cycle 3's V2, which arrived before I had finished writing
cycle 4. `main` moved to `981514e`; `git diff --stat b2c0005 981514e -- src
tests` is empty, so cycle 3's numbers still stand.

**Everything below was reconstructed here.** Two throwaway `git clone --local`
checkouts under the session scratchpad — `mergetree` and `statictree` — so the
constructions could be committed and `git ls-files` would see them. Nothing was
created inside `C:\Users\ewehm\repos`; no worktree was added; `mk-main` clean
throughout.

---

### V8 — `096f5f2` fixes V2 cleanly. **CONFIRMED, and it closes V6 as well.**

The whole delta from `a842065` to `096f5f2`, on the same demo page, is now the
two SVG `<title>` lines and nothing else:

```
$ diff text_old.txt text_fixed.txt
11,12d10
<   candidate accuracy: pass rate 75.0%, interval 62.8% to 84.2%, floor 90.0%
228,229d225
< Candidate pass rate over 1 run(s); the horizontal axis is time.
```

Counted rather than tested for presence, which was the point:

```
FAKE MODELS                        a842065=2  broken=1  fixed=2
candidate accuracy: pass rate      a842065=1  broken=0  fixed=0
Candidate pass rate over 1 run     a842065=1  broken=0  fixed=0
Methodology appendix               a842065=2  broken=2  fixed=2
Provenance                         a842065=2  broken=2  fixed=2
```

`_SVG_LABEL` uses `<(title|desc)\b`, so **V6's missing word boundary is fixed
too** — a `<description>` element can no longer be swallowed. The commit message
names the methodology error itself without being asked, which is the right
disposal of V3.

**V4 is untouched and still live.** The fixed module, same construction as cycle
3:

```
$ python page_text_FIXED.py cjkreal/real.html > /dev/null
exit=1
UnicodeEncodeError: 'charmap' codec can't encode characters in position 9249-9250
```

That is JOB-6 item 5 and it stays open.

---

## `check_merge.py` — four findings reconstructed, four confirmed

### V9 — G18: the merge gate is green over `assert 1 == 2`, with zero tests executed. **CONFIRMED. This is the most serious thing on the branch.**

A clone of `main` with one committed failing test:

```
$ git log --oneline -1
9dbbdab constructed: a failing test

$ PYTEST_ADDOPTS="--co -q" python scripts/check_merge.py
[PASS] no conflict markers
[PASS] every python file parses
[PASS] no shadowed top-level names
[PASS] __all__ lists every public name
[PASS] ruff
[PASS] dependency surface
[PASS] pytest

Merge is green on all seven checks.
EXIT=0                                            real 0m13.8s
```

The control, same tree, same commit, run honestly:

```
$ python -m pytest tests -q -n 4
FAILED tests/test_x2.py::test_x2 - assert 1 == 2
1 failed, 2252 passed, 1 xfailed in 222.22s
```

**13.8 seconds versus 3m42s is itself the tell** — the gate's own wall clock says
it did not run a suite, and nothing in its output does. `run()` checks
`returncode == 0` and never prints, parses or compares a count. `PYTEST_ADDOPTS`
is not exotic: it is the standard pytest environment variable, and this project
sets pytest options in `pyproject.toml`.

The audit's second form (`pytestmark = pytest.mark.skip` in `tests/test_report.py`,
no environment variable needed) I did **not** re-run — one full honest suite run
was the affordable control and I spent it on the stronger claim. The mechanism is
the same one and is visible in `run()`; I am recording that I took it on the
audit's evidence rather than mine.

### V10 — G20: the project's own C22b defect passes when written as tuple unpacking. **CONFIRMED, control and all.**

One tracked module, two spellings of one collision, identical runtime outcome:

```
##### tuple_rebind:  THIRD_MODEL, FOURTH_MODEL = "zeta-9", "alpha-1"
check 3 (shadowed names): PASS
   THIRD_MODEL at runtime: zeta-9          <- C99a's value is gone

##### name_rebind:   THIRD_MODEL = "zeta-9"
check 3 (shadowed names): ["src\model_migration_kit\c99.py: 'THIRD_MODEL' defined at 8, 12 -- the later one wins",
                           "src\model_migration_kit\c99.py: 'FOURTH_MODEL' defined at 9, 13 -- the later one wins"]
   THIRD_MODEL at runtime: zeta-9
```

**Same defect, same names, same lost value: one spelling fails the gate and the
other passes it.** And ruff is not a backstop for either — `ruff check` reports
`All checks passed!` on **both** variants, so for a constant rebind check 3 is
the only thing looking, and it looks at `ast.Assign` with a `Name` target only.

### V11 — G19: deleting a module's entire `__all__` is a pass; the docstring's evidence is wrong, and I can say how. **CONFIRMED, with a sharper diagnosis.**

```
dimensions.py has __all__ : True   (9 entries)
check 4 with __all__ deleted : PASS
check 4 restored             : PASS
```

`if declared is None: continue`. The worse the merge damage, the quieter the
check.

On the count, measured independently across every tracked `src/` module:

```
comparison.py: 8  ['FAIL_MARGIN', 'PASS_MARGIN', 'STATE_FAIL', 'STATE_PASS',
                   'STATE_UNSTABLE', 'TEST_NOT_RUN', 'TEST_OUTCOMES', 'TEST_SCORES']
report.py:     9  ['EM_DASH', 'FETCHING_ATTRS', 'FORBIDDEN_TAGS', 'INTERVAL_BAR_MIN_SPAN',
                   'INTERVAL_BAR_NO_FLOOR', 'INTERVAL_BAR_NO_INTERVAL', 'INTERVAL_BAR_NO_RATE',
                   'TERMINAL_DASH', 'THRESHOLD_SOURCE_UNRECORDED']
TOTAL: 17   modules affected: 2
```

**17, exactly as the audit reported.** And the diagnosis is sharper than "wrong
by 2x": **eight is exactly `comparison.py`'s count.** The docstring names three
examples — `FETCHING_ATTRS`, `FORBIDDEN_TAGS`, `TEST_OUTCOMES` — and the first
two are in `report.py`, the third in `comparison.py`, so the sentence is drawing
its examples from both modules while its number counts one. That reads as a
figure measured on one file and then generalised, which is worth more to whoever
fixes it than "the number is wrong".

### V12 — G21: `[PASS] no conflict markers` over two files it never read. **CONFIRMED.**

A tracked `docs/merge-note.md` carrying a real marker pair and one `0xff` byte,
plus five marker lines appended to the tracked `.gitattributes`:

```
docs/merge-note.md in scanned set : True     <- scanned, then swallowed by the except
.gitattributes    in scanned set : False     <- suffix not in the keep set
markers reported                 : PASS
```

The identical markers in a clean-UTF-8 file are caught:

```
markers reported : ['docs\merge-note.md:3: <<<<<<< HEAD', 'docs\merge-note.md:7: >>>>>>> feature']
```

**And the audit's smallest point is the one I would most want fixed**, because it
is a comment that describes a guard that does not exist:

```python
# ``=======`` alone is a legal markdown rule, so it only counts as a
# marker when a real one is present in the same file.
if line.startswith(("<<<<<<< ", ">>>>>>> ", "||||||| ")):
```

There is no such conditional and `=======` is never counted under any condition.
A maintainer reading that comment believes a rule is in force that was never
written.

### V13 — G22: `@_plug.register` keeps a public function out of check 4. **CONFIRMED.**

```
check 4 (decorated)  : PASS
check 4 (undecorated): ['src\model_migration_kit\c99.py: defined but not in __all__: dimension_summary']
```

`_REBIND_IS_INTENTIONAL` is matched against the **bare last component** of any
decorator, so any object with a method called `register`, `setter`, `getter`,
`deleter` or `overload` qualifies. The audit's own verdict — **SURVIVES for check
4, REFUTED for check 3** — is the right split and I did not disturb it.

---

## `verify_release.py` — the headline reconstructed end to end

### V14 — G25: 16 checks pass, exit 0, on a fresh build of a wheel whose `migkit` command does not exist. **CONFIRMED, including the install.**

`pyproject.toml`, one line changed, then a full clean build through the gate:

```
migkit = "model_migration_kit.cli:no_such_entrypoint"

[PASS   ] console-script: `migkit` points at a module the wheel ships
            entry_points.txt: [console_scripts] | migkit = model_migration_kit.cli:no_such_entrypoint
            target model_migration_kit.cli:no_such_entrypoint -> looked for
                   ['model_migration_kit/cli.py', 'model_migration_kit/cli/__init__.py']
            found in wheel: model_migration_kit/cli.py
...
16 passed, 0 failed, 0 flagged, 0 skipped, 16 checks total
Every check ran and passed.
EXIT=0
```

**The gate prints the broken target and passes on it in the same row.** The
wheel's own metadata:

```
[console_scripts]
migkit = model_migration_kit.cli:no_such_entrypoint
```

And the wheel installed into a throwaway venv, run the way a user runs it:

```
$ migkit demo --out x.html
ImportError: cannot import name 'no_such_entrypoint' from 'model_migration_kit.cli'
migkit EXIT=1
```

That is precisely the outcome `check_console_script`'s docstring says it exists
to prevent. The audit's ranking of this first is right and I would not move it:
it is the only wheel finding that survives *"CI builds the wheel in the same
job"*, because CI builds exactly this wheel.

I did **not** re-run G26–G32. They are constructions of the same shape by an
agent that got G25 right and reported its own rule violation in G33 unprompted;
re-running them is throughput, not verification, and my one contribution to that
section is below.

---

## `dependency_surface.py`

### V15 — G11: `import opik_rigor.judge` is invisible to the gate built to fold exactly that. **CONFIRMED by calling the gate's own function.**

```
SEEN       import opik_rigor                -> names=[] imports_module=True
INVISIBLE  import opik_rigor.judge          -> names=[] imports_module=False
INVISIBLE  import opik_rigor.judge as J     -> names=[] imports_module=False
SEEN       from opik_rigor import X         -> names=['X'] imports_module=False
SEEN       from opik_rigor.judge import X   -> names=['X'] imports_module=False
INVISIBLE  import scipy.stats               -> names=[] imports_module=False
```

Byte-for-byte the audit's table. The asymmetry is visible in six lines of the
gate:

```python
if isinstance(node, ast.ImportFrom):
    if node.module and node.module.split(".")[0] == "opik_rigor":   # folds
elif isinstance(node, ast.Import):
    imports_module |= any(alias.name == "opik_rigor" for alias in node.names)   # exact match
```

`.split(".")[0]` on one branch, `==` on the other. **The audit's own honesty
holds here too** — `grep -rEn "^\s*import opik_rigor\." src tests scripts`
returns **0** on this machine as well, so it is a live hole and not a live bug,
and the audit said so before I checked.

---

## Ranking after five cycles

Merged with the earlier list, by what a maintainer would wrongly believe is
guaranteed:

1. **V9 / G18 — the merge gate reports `[PASS] pytest` without running a test.**
   `CLAUDE.md` puts `check_merge.py` at the head of its Gates section, seven
   agents run it per chunk, and the whole review discipline sits on top of it. An
   ordinary environment variable makes it green over `assert 1 == 2` in 13.8
   seconds.
2. **V14 / G25 — the release gate ships a wheel whose command cannot start.**
   Survives a fresh build; the failure reaches a user on first use.
3. **G4 (cycle 4) — a pasted traceback resolves into another checkout and is
   certified.**
4. **G5 (cycle 4) — the same contract is exit 1 on one machine and exit 0 on the
   other.**
5. **V10 / G20 — the tuple-unpack rebind.** This project's own C22b defect,
   through the gate written after it, with ruff not covering it either.
6. **V5 (cycle 3) — the sweep files `<title>`-only disclosures under "never
   reaches the page".** Queued as JOB-6.
7. **`judges[0].item_counts.items`** — provenance closed, still live at
   `981514e`, still pinned rather than fixed.
8. **V12 / G21, V11 / G19, V13 / G22, G1, G2** — real, smaller blast radius.
9. **V4 — the harness CLI's zero-byte failure.** Tooling, Windows-only.

**A pattern across all four gates, worth stating once:** every finding in this
job is a check printing `[PASS]` over something it did not look at — a test that
did not run, a file it could not read, a name it cannot see, an entry point it
did not resolve. That is this repository's own standing rule — *an absence must
not render as a measurement* — and all four gates break it in the same direction.
The rule is enforced in the product and nowhere in the tools that enforce it.

---

### Method note, cycle 5

Two `git clone --local --no-hardlinks` checkouts of `mk-main` under the session
scratchpad, so constructions could be committed and `git ls-files` would see
them; a third throwaway venv for the wheel install. `git status` clean in
`mk-main` and in `mk-watch2` apart from this file, and nothing was written into
`C:\Users\ewehm\repos` at any point.

Where I took a finding on the audit's evidence rather than my own I have said so
inline — G18's `pytestmark` form, and G26–G32. A verification that does not name
its own coverage is worth what an audit that does not name its own is worth.

---

## Cycle 6 — 2026-08-24, on `10e5d8b`…`8f39878` (JOB-3 complete), verified against `main` at `f50680f`

The workflows/`conftest.py` section and the composed finding landed, and JOB-3
closed. Three verdicts, one of which is about **this machine's own `CLAUDE.md`**
and is worse than the audit could see.

---

### V16 — G34/G35: netguard's four routes out reproduce on Windows, and the "armed" line prints while they are open. **CONFIRMED.**

`netguard.pytest_configure` called exactly as pytest calls it, then each route
tried against real hosts:

```
[audit] network guard armed (loopback still permitted)
guard armed via netguard.pytest_configure(); routes:
  BLOCKED   socket.create_connection           outbound connection attempted to ('1.1.1.1', 443)
  BLOCKED   socket.socket().connect            outbound connection attempted to ('1.1.1.1', 443)
  THROUGH   _socket.socket().connect           connected
  THROUGH   socket.gethostbyname               resolved -> 151.101.128.223
  THROUGH   UDP sendto 8.8.8.8:53              DNS reply, 90 bytes
  THROUGH   subprocess child                   child stdout: 'CHILD CONNECTED'
```

Four of six through, including a real A record for `pypi.org` and a full DNS
reply over UDP. **G35 is the first line of that output**: the guard announces
itself as armed in the same run in which four routes are open, because the
`print` is unconditional and the guard fires zero times in a healthy run. There
is no positive control, so *working* and *disarmed* produce identical logs.

The subprocess route is the one that matters here, and its exposure is confirmed
on this tree:

```
$ grep -rl "subprocess" tests/*.py
tests/test_cli.py  tests/test_import_purity.py  tests/test_release_checks.py
tests/test_series.py  tests/test_stranger_path.py          (5 modules)
```

**A child process inherits none of the monkeypatches.** `README.md:595` and
`PROGRESS.md:27` publish "Offline" as proved; `ci.yml:67` says "network
physically blocked". It is neither physical nor a syscall seam — it is four
attribute rebinds in one module's namespace, in one process.

---

### V17 — G38 is real and understated: **`CLAUDE.md`'s escape hatch does not work on this machine, for `pytest` *or* for bare `python`.** New, Windows-only, and it explains the Mac/Windows divergence.

`CLAUDE.md`, the file every agent on this box reads first:

> *"Setting `PYTHONPATH` explicitly still works and still wins."*

The audit reported that as **true for bare `python` and false only under
`pytest`**. On this machine it is false in both cases. From a worktree, with
`PYTHONPATH` pointing at a decoy checkout carrying a marker:

```
$ cd C:\Users\ewehm\repos\mk-watch2
$ PYTHONPATH=<decoy>/src python -c "…"
PYTHONPATH env  : …/statictree/src
sys.path[0:4]   :
    (empty)
    C:\Users\ewehm\repos\mk-watch2\src        <- inserted by the hook
    …/statictree/src                          <- PYTHONPATH, one place too late
    …python314.zip
imported        : C:\Users\ewehm\repos\mk-watch2\src\model_migration_kit\__init__.py
DECOY_MARKER    : <absent>
```

And from a directory that is **not** a checkout, where `CLAUDE.md` promises
"exactly the old behaviour":

```
imported        : C:\Users\ewehm\repos\migration-kit\src\model_migration_kit\__init__.py
DECOY_MARKER    : <absent>
```

**PYTHONPATH loses to the hook inside a checkout and loses to the main-checkout
fallback outside one. There is no cwd from which it wins.**

**Why the two machines disagree, and it is not a contradiction.** The audit's own
G15 prints, from the MacBook:

```
original saved: no
hook module present: no
```

and the same command here prints:

```
site-packages: C:\Users\ewehm\repos\migration-kit\.venv\Lib\site-packages
_editable_impl_model_migration_kit.pth: 'import worktree_path'
original saved: yes
hook module present: yes
this cwd would resolve to: C:\Users\ewehm\repos\mk-watch2\src
```

**The hook is installed on the machine `CLAUDE.md` is written for and not
installed on the machine that tested the sentence.** The audit measured the
uninstalled behaviour honestly and generalised it one step too far; the
installed behaviour is the one every agent here actually gets.

**Why this ranks high.** `CLAUDE.md` frames `PYTHONPATH` as the manual override
for exactly the failure it says has been the most expensive recurring hazard on
this project — *"a green test run may have tested nothing… It caught the
orchestrator and six agents."* The documented way to force the answer when a
result surprises you **does not force it**, and it fails silently, in the same
direction as the original defect. An agent that sets `PYTHONPATH`, sees a
plausible result, and moves on has been told by the project's own onboarding
document that it verified something it did not.

For `pytest` specifically the mechanism is one line of `conftest.py`:

```
83:    if sys.path[:1] != [_src]:
84:        sys.path.insert(0, _src)
```

`insert(0, …)` is ahead of every `PYTHONPATH` entry by construction. I confirmed
the bare-`python` half by measurement above and am taking the `pytest` half from
that line plus the audit's construction rather than re-running it — my own
`pytest` probe errored during collection for an unrelated reason (a test file
outside the rootdir) and I did not spend another suite run on it. **Stated so the
coverage is visible.**

---

### V18 — G41's headline is **not reproducible from the branch as pushed.** No verdict on its content, and that is the point.

The composed finding — *"One diff. One file. 18 insertions, 67 deletions.
Fourteen independent lies in the rendered document. Every gate green."* — is the
most valuable thing in JOB-3 and the hardest to check. Its evidence block opens:

```
$ git apply --stat COMPOSED.diff
```

`COMPOSED.diff` is not on the branch:

```
$ git ls-tree -r --name-only HEAD | grep -iE "composed|\.diff|\.patch"
                (no output)
```

**So the one artifact that would let anyone else reproduce fourteen simultaneous
defects is the one artifact that did not get committed.** Rule 4 on this branch
is *every claim carries the command and the output that produced it*; this claim
carries a command that names a file only one machine has.

**This is not a doubt about the content.** The section is the most carefully
adversarial writing on the branch — it reports a control that came back green as
a finding rather than promoting it, it refutes two of its own priors by name, and
it scopes its CI reproduction honestly. G42's control table, G44's leverage
arithmetic and G45's fifteen-singly-then-composed runs all read as measured. I am
recording that **I could not verify any of it**, which is a different sentence
from "I verified it".

**What would close this, and it is one command:** `git add COMPOSED.diff` and the
fifteen single mutations alongside it. On this machine the composed tree could
then be rebuilt in a throwaway clone in about four minutes, which is what cycle 5
did for `check_merge.py` and `verify_release.py`. Until then G41–G45 are a report
from one machine rather than a finding two machines hold.

---

## Standing summary — what Windows has verified, and what it has not

**Verified here, with constructed trees and quoted output:** the `<title>` fix
and its regression and its repair (V1, V2, V8); the harness's zero-byte encoding
failure (V4); the sweep contamination and the missing `ACCESSIBLE-NAME-ONLY`
verdict (V5); `check_contract.py` G1–G6 and G8, with G3's Windows blast radius
measured and G4 rebuilt against a live target; `check_merge.py` G18–G22;
`verify_release.py` G25 end to end including the install; `dependency_surface.py`
G11; `netguard`'s four open routes (G34/G35); and `judges[0].item_counts.items`
pinned to its introducing commit.

**Taken on the audit's evidence, said so inline:** G7, G9, G10, G12–G17,
G23–G24, G26–G33, G36, G37, G39, G40.

**Could not verify:** G41–G45, for want of `COMPOSED.diff`.

**Refuted:** nothing. **Corrected:** two — G6's mechanism is narrower than
stated, and G38's is wider.

---

### Method note, cycle 6

The netguard probe made real outbound connections, deliberately, because a guard
can only be tested against the thing it guards; it resolved one hostname, opened
one TCP connection to `1.1.1.1:443`, sent one DNS query, and spawned one child
that did the same. Nothing was fetched and nothing was written. The decoy
checkout for V17 was a `git clone --local` under the session scratchpad and was
left with its marker in place for re-checking; `mk-main` and `mk-watch2` are
clean apart from this file.

---

### Addendum — `main` moved again while this was being written, and it lands on G41

`main` is now `e50a842` *"Merge chunk 0: the gate stops being green on an
inverted disclosure"* (`386f3a4` + merge), the first `src/`+`tests/` change since
`630912d`:

```
$ git diff --stat f50680f e50a842
 src/model_migration_kit/report.py |  47 ++++-
 tests/test_report.py              | 371 +++++++++++++++++++++++++++++++++++++
```

It is Windows' own answer to R39.4 and it overlaps G41 directly. By its own
measurement it reproduced **six** mutations of the disclosure paragraph that the
full suite passed — *"the sentence that says whether these numbers came from a
real provider could be turned inside out without one of 2,241 tests noticing"* —
and the three tests it adds assert **pairing rather than membership**, so all six
now fail.

**What that means for JOB-3, stated carefully because I cannot test it:** part of
G41's class is now covered on `main` and part is not. Chunk 0 pins *which
sentence a document gets given what its evidence says*; it says nothing about row
order, section presence, list truncation or the terminal band — which is where
G41's other defects live, including the row-swap control that came back green.

**Two consequences.** First, this is a second independent confirmation of the
class, arrived at from the pipeline side without seeing `AUDIT-gates.md` — which
is the same shape as the `item_counts.items` convergence and is worth as much.
Second, **whoever re-runs the composed diff must re-run it against `e50a842` or
later**, because some of its fourteen may now be red and the headline count would
be wrong. That is another reason `COMPOSED.diff` needs to be on the branch: the
number in it has a shelf life measured in hours, and only the machine holding the
file can say what it is now.
