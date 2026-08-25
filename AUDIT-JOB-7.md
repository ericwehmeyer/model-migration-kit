# JOB-7 and JOB-8 — go wide

You are the faster machine and you have been running one agent at a time. **These
two jobs are built to fan out.** Both are embarrassingly parallel, both are
mechanical once the harness is right, and both are the kind of work this box
cannot do cheaply because it is running seven chunk agents already.

**Spawn many agents. Partition the work and run them concurrently.** The only
serial parts are building the harness and merging the results.

---

## Why now: the suite has large blind regions and we have just measured one

A chunk on this side reproduced a single defect and found it was a **class**. Six
different inversions of one paragraph — swap two openings, negate the condition,
delete either opening, swap two adapter names, invert a claim word for word — all
survived the full suite. **2,241 tests, seven green gates, every time.** `grep`
for every one of those sentences returns zero hits in `tests/`.

Then, sweeping nulls across 203 addressable payload paths, it found **eight
crash paths**, not the one it was sent to fix:

```
warnings: null      TypeError, exit 3, no HTML written   (fixed)
judges: null        TypeError, identical shape           (open)
judges[0]: null     TypeError from dict(None)            (open)
flips[0]: null      flips[1]: null      gains[0]: null   (open)
flips[0].changes[0]: null  + two siblings  AttributeError (open)
```

And the top-level list keys are **split**: `flips` and `gains` use
`payload.get(name, ()) or ()` and survive; `judges` and `warnings` did not.

**One sweep, one value class, eight findings.** That is the shape of what is
below.

---

## JOB-7 — the hostile-value sweep

**Status:** OPEN. Take it now. **Report to `AUDIT-hostile.md`.**

### What to build

Extend your `differential_render.py` machinery. For **every addressable path** in
the comparison payload — and the envelope, and the run artifacts — substitute
each of a set of hostile values, render **both surfaces**, and record what
happens.

Value classes, at minimum. Add your own and say what you added:

| Class | Example | What it is probing |
|---|---|---|
| null | `None` | the eight above; how many more |
| wrong type | `"12"` for an int, `12` for a string, `[]` for a mapping | silent coercion |
| null element | `[None]`, `[{...}, None]` | the seven found above are all this shape |
| negative | `-1`, `-0.5` | counts and rates that cannot be negative |
| NaN / inf | `float("nan")`, `float("inf")` | comparisons that silently become False |
| boundary | `0`, `0.0`, `""` | measured zero vs absence — you know this one |
| huge | `10**18`, `"x" * 10**6` | formatting, truncation, layout |
| unicode / control | RTL marks, `\x00`, `\r\n\x07`, combining chars | escaping, and the terminal |
| deeply nested | 200-deep list | recursion limits |

### The three outcomes to separate, and they are not equally interesting

1. **Crash** — an exception, a non-zero exit with no document. Loud. Easy to
   find, easy to fix, and the least dangerous.
2. **Silent misrender** — a document is produced and says something false. **This
   is the one worth the whole job.** A crash tells the operator something is
   wrong; a silent misrender does not.
3. **Correctly refused** — the tool declines and says why. Record these too:
   they are the population that proves the sweep would have noticed.

**Rank by 2, then 1, then 3.** A ranked list dominated by crashes means the sweep
is finding the easy half.

### How to go wide

Partition by **path prefix** — one agent per top-level key (`judges`, `flips`,
`gains`, `latency`, `item_counts`, `thresholds`, the envelope, the artifacts) —
and give each the same harness and value table. They do not need to talk to each
other. Merge the results at the end and de-duplicate by (path, class, symptom).

Two things that will make it fast:

- **Memoise the jinja2 environment for the duration of a sweep.** An agent on
  this side measured `report._environment()` rebuilding a fresh `Environment`
  per call, so every render recompiles the template: **100 ms of each 109 ms
  render, against a ~3 ms actual render. Memoising is 25x.** A fix is in flight
  here; do not wait for it, do it in your harness.
- **Deduplicate identical payloads before rendering.** Sibling paths often
  produce the same document under "parent removed".

### Rules

- **Do not fix anything.** Every finding goes through this box's chunk pipeline.
- **Every finding carries the path, the value, the command, and the output.**
- **Run your adversarial pass**, verdicts inline. Default to REFUTED when
  uncertain. It has been the most valuable thing you produce.
- A crash that requires a payload no writer can emit is still worth recording,
  **but say so** — this project has already nearly spent a chunk hardening reads
  against a writer that does not exist, and your own refuting agent is what
  caught it.

---

## JOB-8 — mutation-sweep the whole of `src/`

**Status:** OPEN. Take it after JOB-7's harness exists, or in parallel if you
have the capacity. **Report to `AUDIT-mutants.md`.**

Your `mutation_harness.py` already exists. Point it at every module in
`src/model_migration_kit/`, not just the one under discussion, and answer one
question:

> **Which mutations survive the full suite?**

The six-inversion result above says the answer is "more than anyone expects", and
it was found by hand on one paragraph. Do it mechanically on everything.

**Partition by module** — one agent each for `report.py`, `series.py`,
`comparison.py`, `dimensions.py`, `runner.py`, `judging.py`, `evidence.py`,
`cli.py`. `report.py` is large enough to split again by section.

**Mutation classes worth prioritising**, from what has actually survived here:

- **swap two branches of a conditional** — six survivors on one paragraph
- **invert a boolean** used only in prose
- **swap two adjacent fields of the same type** — `min_n` for `min_items`,
  baseline for candidate
- **rotate a sequence by one** — this survived 2,206 tests in a table renderer
- **replace a computed sentence with a constant one**
- **drop a clause from a disclosure sentence**

**For every survivor, prove it is non-equivalent** by rendering the difference.
An argument is not a finding, and an equivalent mutant reported as a survivor
costs the reader more than it is worth. One reviewer here proved a positional-vs-
tag-join divergence was **unreachable from any producer**, which turned a finding
into a note — that is the standard.

**Rank by what a reader of the document would be misled about.** A survivor in
code no document path reaches is a note; a survivor in a disclosure sentence is
the finding.

---

## What this box is doing meanwhile

Seven chunks in flight: the schema guard (which decides whether sixteen of your
Tier 2 findings are reachable at all), the completeness certificate, the latency
suppression, R34.3's rendering, the render-cost fix, and the two gate blindspots
— that last one is where the six inversions and eight nulls came from.

`main` is at **2252 passing, seven gates green**, R1–R41. Your first audit
produced R38 and R39; your tooling's provenance decided a ranking; and your
refuting agent's Tier 2 observation is now R38.2.

Your JOB-3 gates audit is landing and being verified here as it arrives.
