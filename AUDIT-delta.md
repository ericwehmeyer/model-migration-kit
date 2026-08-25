# JOB-5 — the delta against current `main`

**Issue [#4](../../issues/4). Claimed MacBook 2026-08-25 05:30Z.**

The job as written: *"re-run the sweeps against current `main`, report **only the
delta** — what changed, and whether each change is a fix or a regression. A delta
is worth reading; a re-listing is not."*

| | |
|---|---|
| baseline | `main` at **`e50a842`** — where cycle 7 (V19) pinned the bucket table |
| now | `main` at **`f887b31`** — 5 commits, **1,089 insertions**, 6 files |
| what landed | R38.3's evidence-schema guard (`evidence.py` +193, `report.py` +311, `test_report.py` +494) and the jinja-environment fix |
| tools | this branch's `scripts/audit/`, **one revision, both trees** — so a difference is attributable to `main`, not to the tool |

---

## The one-line answer

**Nothing the sweep measures moved, and that is not the same as nothing changed.**
`main` grew a disclosure feature that the sweep is structurally blind to, because
the sweep enumerates *payload* leaves and the new feature is keyed on an
*envelope* field. Pointing the sweep's own five-variant method at that field by
hand found one breach of the central rule, in the new code — **D1**, below.

Two further results are about the *tooling* rather than the product, and both
would mislead the next person to run this job: **D2**, the sweep's `--json` is not
comparable across runs; **D3**, the mutation catalogue.

---

## D0 — the control, which passed

Before any delta is worth reading, the tool has to reproduce the pinned number.
`AUDIT-VERDICTS.md:2021` recorded, at `e50a842`:

```
single/comparison: 161 paths | collisions 46 | reverse 1 | trivial-unrendered 47
                 | zero-and-absence-both-invisible 37 | a11y-name-only 1
                 | render-errors 0 | clean 29
```

Re-run just now, `e50a842`, same tool:

```
$ cd /Users/ericw/mk-sweep-old && PYTHONPATH=$PWD/src \
    .venv/bin/python scripts/audit/differential_render.py sweep --json sweep_e50a842.json
single/comparison: 161 paths | collisions 46 | reverse 1 | trivial-unrendered 47 | zero-and-absence-both-invisible 37 | a11y-name-only 1 | render-errors 0 | clean 29
```

Identical. The baseline is real and the tool is the same tool.

---

## D0b — the sweep delta itself: zero, in every bucket, on all five fixtures

```
$ diff <(grep -v 'fixtures under' sweep_e50a842.txt) <(grep -v 'fixtures under' sweep_f887b31.txt)
170c170
< wrote …/sweep_e50a842.json
---
> wrote …/sweep_f887b31.json
```

**The only line that differs is the name of the output file.** Every bucket count,
every `COLLISION` line, the one `REVERSE`, the one `A11Y-NAME-ONLY` — byte for
byte the same across 513 leaves and 2,391 renders:

| fixture | paths | coll | rev | trivial | zero-and-absence | a11y | err | clean |
|---|---|---|---|---|---|---|---|---|
| `single/comparison` | 161 | 46 | 1 | 47 | 37 | 1 | 0 | 29 |
| `single/verdict` | 15 | 3 | 0 | 12 | 0 | 0 | 0 | 0 |
| `field/comparison` | 161 | 48 | 1 | 46 | 36 | 1 | 0 | 29 |
| `field/verdict` | 15 | 3 | 0 | 12 | 0 | 0 | 0 | 0 |
| `field-newest/comparison` | 161 | 48 | 1 | 46 | 36 | 1 | 0 | 29 |

And per-leaf, comparing the **equality patterns** — which is what a verdict is
actually made of — rather than the bucket totals:

```
leaves total                       : 513
leaves whose page hashes CHANGED   : 513
leaves whose name hashes CHANGED   : 0
leaves whose EQUALITY PATTERN moved: 0   <- this is what a verdict is made of
leaves whose NAME pattern moved    : 0
```

That `513` in the second row is **not** a finding about `main`. See **D2**.

Controlling for it — same fixture root, rendered from each tree in turn, so the
source tree is the only variable:

```
$ diff SAME_old.txt SAME_new.txt              # visible text
IDENTICAL
$ diff SAME_old.names.txt SAME_new.names.txt  # accessible names
IDENTICAL
```

**Both channels byte-identical.** On the swept fixtures, 1,089 new lines of `main`
changed the rendered document not at all.

---

## D1 — a log that declares no schema renders exactly as a log that declares schema 1. **SURVIVES (as a rule breach), with the mitigation stated**

### Why the sweep did not find this

```
$ grep -c 'schema_version' sweep_f887b31.txt
0
$ grep -o '"path": "[^"]*schema[^"]*"' sweep_f887b31.json | sort -u
(no output)
```

`schema_version` is a sibling of `event_type`, `ts` and `payload` on the record
envelope:

```
record keys: ['event_type', 'payload', 'schema_version', 'ts']
```

`differential_render.py` walks `payload`. **The entire new feature is outside the
swept region** — so "the sweep found nothing new" means *unswept*, not *clean*,
and reporting the first as the second is the exact error this whole audit exists
to catch. Anyone quoting D0b without D1 would be doing to the sweep what the
sweep exists to catch the report doing.

### The five-variant method, applied to that field by hand

```
A  declares 1 (this build's ceiling)   hash 335416b37dd03502
    | (page says nothing about schema)
B  declares 0                          hash 335416b37dd03502
    | (page says nothing about schema)
C1 key removed                         hash 335416b37dd03502
    | (page says nothing about schema)
C2 declares null                       hash 6209e7eaaf3a7623
    | EVIDENCE SCHEMA NOT UNDERSTOOD — all 4 records in this evidence log declare envelope schema null, and this build reads up to 1. …
F  declares 99                         hash 5191d9542ce0a907
    | EVIDENCE SCHEMA NOT UNDERSTOOD — all 4 records in this evidence log declare envelope schema 99, and this build reads up to 1. …
F2 declares 2                          hash e65a5583c0890fa2
    | EVIDENCE SCHEMA NOT UNDERSTOOD — … envelope schema 2 …
T  declares True                       hash 16a80c7527071f40
    | EVIDENCE SCHEMA NOT UNDERSTOOD — … envelope schema True …

=== byte-identical page groups ===
  335416b37dd03502: ["A declares 1", 'B declares 0', 'C1 key removed']
```

**The guard is good.** `99`, `2`, `null`, `True` each produce a distinct banded
page naming the declared value. That is four states kept apart, and `True`
being kept apart from `1` is a deliberate catch (`True <= 1` is true in Python)
with its own fixture. This finding is not that the guard is weak.

**The finding is the collision inside the silent group.**
*A log that declared schema 1* and *a log that declared nothing at all* produce
**byte-identical pages**. An absence renders exactly as a measurement, which is
this project's central rule, stated in `CLAUDE.md`, and it is now in the newest
code on `main`.

`B declares 0` is in that group too and is **not** part of the finding: `(0, None)`
and `(-3, None)` are explicit rows in the coercion table with a stated
one-directional rationale — this build reads *up to* 1. Deliberate, tested, ruled.
**Not a defect.**

### The model does not distinguish them either

```
declares 1   foreign=False banded=False versions=() sentence=''
absent       foreign=False banded=False versions=() sentence=''
declares 0   foreign=False banded=False versions=() sentence=''
```

So this is not a renderer dropping a distinction the model held. **The
distinction is never computed.**

### The test that is named for this, and the direction it does not test

```python
def test_a_log_that_declares_no_schema_at_all_is_not_a_log_that_declares_an_unknown_one(
```

Its docstring: *"The rule this package turns on, pointed at its own input. A record
with no `schema_version` and a record with `schema_version: 99` are different facts
and must not converge."* And then, plainly: *"the absent case renders exactly as a
log that declared `1` does — and, crucially, **no version is printed for it**."*

Its assertions:

```python
assert _get(_schema_reading(silent), "foreign") is False
assert _get(_schema_reading(silent), "versions") == ()
assert _schema_words(silent) == ""
assert _get(_schema_reading(stranger), "foreign") is True
assert "99" in _schema_words(stranger)
```

**Every assertion about `silent` passes identically for a log that declares 1** —
the table above shows `versions=()` and `sentence=''` for both. The test proves
`absent ≠ 99`. It cannot see `absent == 1`, which is where the convergence is.

By this project's own standing rule — *"a fixture where the broken and the correct
implementation agree is a fixture that tests nothing"* — the `silent` half of that
fixture pair tests nothing. It varies one pair and the collision is in the other.

### The mitigation, which is real

The docstring's reason for not printing `1` is **correct and should not be
reversed**: *"Inventing `1` in a sentence about a writer that named nothing would
be this module writing the log's evidence for it."* Exactly right. The remedy is
not to print `1` for an absence — it is to give the absence **its own words**, the
way every other absence on this page already gets them. The page has a vocabulary
for this and does not use it here.

### Reachability — stated honestly, because the last audit over-claimed it

**Not reachable from the shipped writer.** Rigor stamps `schema_version` on every
line it appends, so a log this pipeline produced always declares one.

**Reachable from any log this pipeline did not write** — which is precisely the
population the guard was built for. A hand-edited log, a log from a foreign
writer, a log rebuilt by a script that dropped the envelope: each is read with the
most trusting possible assumption, *this build's own ceiling*, and the page says
nothing. `AUDIT-security.md` (JOB-14) already established the hostile evidence log
as in scope, and the suite ships `_schema_log(..., drop=True)` because the project
itself treats absence as a state that occurs.

### Adversarial pass

| attempt to refute | verdict |
|---|---|
| *"Absence can't happen — rigor always stamps it."* | **fails.** The suite's own `drop=True` fixture exists; JOB-14 put hostile logs in scope; the guard's entire purpose is foreign logs. |
| *"Nothing is being misrepresented — in both cases there is nothing to disclose."* | **fails.** The rule is not *don't misstate the value*, it is *the states must be distinguishable*. And an unstamped record from an unknown writer being silently read as this build's own schema is a substantive assumption, not a cosmetic silence. |
| *"`0` collides too, so the whole silent group is one finding."* | **succeeds, and narrows this finding.** `0` and `-3` are ruled rows in the coercion table. Struck from the finding. |
| *"The renderer is fine; the model dropped it."* | **fails.** `versions=()` for both — never computed. |

**Fix, or regression?** Neither: **new surface, new finding.** It did not exist at
`e50a842` because the feature did not.

---

## D2 — the sweep's `--json` cannot be diffed across two runs. All 513 leaves report "changed", and all 513 are noise. **SURVIVES**

This is a finding about the audit tooling, and JOB-5 is the job that hits it,
because JOB-5 is the only job that compares two sweeps.

```
leaves whose page hashes CHANGED   : 513   (of 513)
leaves whose EQUALITY PATTERN moved: 0
```

Cause — the fixture root is in the rendered provenance block, and each sweep
invocation makes its own:

```
$ diff A_old.txt A_new.txt
63c63
< …/job5-delta/fx_old/single_comparison/goldenset.jsonl
---
> …/job5-delta/fx_new/single_comparison/goldenset.jsonl
78c78
< …/fx_old/single_comparison/migkit.toml
---
> …/fx_new/single_comparison/migkit.toml
… 5 hunks, every one of them a path, no other difference
```

`scripts/audit/README.md` documents the mechanism honestly — absolute paths are
**deliberately unmasked**, because `artifacts` are paths and masking them would
blind the sweep to them. Its mitigation, *"a sweep pins `now=` and keeps one
fixture root instead"*, is true **within** one invocation, which is all the sweep
itself needs. Across two invocations it does not hold, and JOB-5's whole method is
across two invocations.

**Why it matters more than it looks.** This is the masking trap in its second
form, and it fails in the *opposite* direction to the first: the original trap
made a sweep report **zero findings** on a document full of them. This one makes a
delta report **every leaf changed** on a document that did not move. A reviewer
who ran two sweeps, diffed the JSON, and reported "513 of 513 leaves changed after
the schema-guard merge" would have written a completely false regression report
with a genuine command and a genuine output under it.

**Compare the classification output, not the JSON** — or pass an explicit shared
fixture root. The `diff` at D0b is the correct instrument and it is one line long.

*Adversarial:* attempted refutation — *"the README already covers this."* **Fails.**
It documents the masking policy and the within-run mitigation; it does not say the
`--json` is incomparable across runs, and the one job whose method requires that
comparison is `#4`. Not a contradiction of the README, an unstated consequence.

---
