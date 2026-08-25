# `scripts/audit/` — tooling for auditing the rendered document

The report is a document, and this directory holds the tools for reading it the
way an adversary would. They exist because a report can be green on 2,206 tests
and still tell its reader something false: every check in the suite was written
by somebody who already knew what the page was supposed to say.

Everything here is offline, keyless and free of RNG. Nothing here modifies the
repository — the one tool that edits source files edits it in a **detached
worktree of your own** and restores from a byte-verified backup.

---

## Read this first: the masking trap

**The rendered page prints an `evidence hash`, which is a sha256 over the whole
evidence file.**

Any tool that compares two rendered reports has to strip it, and a differential
sweep — which rewrites the evidence log once per render, thousands of times —
has to strip it or the entire method silently produces nothing:

* every pair of renders differs, because the hash line differs;
* every comparison therefore says "not identical";
* the sweep, whose only signal is *byte-identity between two renders*, reports
  **zero findings**;
* and a sweep that finds nothing looks exactly like a sweep of a clean document.

**The first sweep on this project did exactly that and came back empty.** It was
not a clean report; it was an unmasked hash. This is the single most expensive
thing to rediscover in this directory, which is why it is at the top of the
README, in the module docstring of `masking.py`, and in the module docstring of
`differential_render.py`.

Two more fields have the same property for tools that compare two separate
*runs*: the `generated` timestamp, and the absolute paths in the provenance
block — on macOS a fresh `/var/folders/…/migkit-demo-XXXXXXXX` every time.

`masking.py` handles all three, and **masking is lossy in the other direction**,
so each is separately switchable:

| field | masked by default | why |
|---|---|---|
| evidence hash | yes, always | derived from the log, never rendered *from* a payload field, so masking it cannot hide a finding |
| `generated` timestamp | no | `created` on a comparison record is a timestamp; masking would blind a sweep to it |
| absolute paths | no | `artifacts` are paths; same argument |

A sweep pins `now=` and keeps one fixture root instead, so those two never vary.
A harness comparing two `migkit demo` invocations calls `mask_run_output`, which
turns all three on.

---

## Read this second: a field can reach the page without being visible

**`TRIVIAL` means "the field never reaches the page at all", and for one day it
was not true.**

On 2026-08-24 `page_text.py` correctly stopped flattening SVG `<title>`/`<desc>`
into the page text — an SVG `<title>` is a tooltip and an accessible name, not
rendered prose (see the section at the bottom of this file). Windows then verified
the sweep across **one unchanged source tree**, differing only in which revision of
`page_text.py` was loaded, and found it moved **exactly one leaf**:
`judges[0].candidate.min_rate`, the judge's floor.

That field's sentence

```
candidate accuracy: pass rate 53.5%, interval 29.3% to 74.7%, floor 87.0%
```

lives **only** inside the banner interval bar's `<svg><title>`. The same SVG has
zero `<text>` elements. So the fix was right about the flattening and left the
classifier saying the opposite of the truth: the one field whose disclosure is
entirely screen-reader-only was filed under a verdict that says the page does not
carry it. And two of this project's confirmed findings are *about* `<title>`-only
disclosures — the banner bar's "floor not recorded" and the timeline's zero-span
note. A classifier that calls those "never reaches the page" hides exactly the
class of defect these audits exist to find.

**The sweep therefore compares two channels, not one:**

| channel | function | the question it answers |
|---|---|---|
| flattened text | `page_text.html_to_text` | what does a sighted reader see? |
| accessible names | `page_text.accessible_names` | what is a screen reader told? |

`accessible_names` is a deliberate parse of the **raw HTML**: `<title>`/`<desc>`
inside an `<svg>`, plus `title=`, `alt=` and the text-bearing `aria-*` attributes
anywhere. The document's `<head><title>` is deliberately **excluded** — it is not
an accessible name, it is the browser tab and the link preview, and
`html_to_text` already returns it as the visible text it is.

### `A11Y-NAME-ONLY`

Assigned when **both** hold:

1. `A` and `B` render **byte-identical visible text** — changing the field
   changes no prose; and
2. some pair of variants that are byte-identical in the prose **differ in the
   accessible names**.

Clause 1 is what keeps the verdict honest. A value printed in a table *and*
repeated in a chart's title appears in both channels, and without clause 1 it
would be reported as screen-reader-only. **A wrong bucket is worse than a missing
one**, which is the whole reason this verdict was added rather than the counts
being left to drift.

A field in neither channel produces no splitting pair and stays `TRIVIAL`.

**It does not stop at naming the channel.** The question the sweep exists to ask
is still open for an announced field, so the COLLISION/REVERSE test is re-run
*inside* the name channel and printed alongside:

```
A11Y-NAME-ONLY judges[0].candidate.min_rate | identical prose: A==B,B==C1,B==C2 | REVERSE-IN-NAME (name A==C1,C2)
```

Read that as: the prose cannot tell the recorded floor from a measured zero at
all, and the announced text *can* — but announces the recorded value when the
floor was never recorded. Confirmed with `probe`, which is what a REVERSE always
needs:

```
$ differential_render.py probe single/comparison --grep "candidate accuracy: pass rate" \
      --del 'judges[0].candidate.min_rate' --set 'thresholds.pass_rate_floor=0.11'
(untouched)                         : []
      announced only: ['… interval 29.3% to 74.7%, floor 87.0%']
del judges[0].candidate.min_rate    : []
      announced only: ['… interval 29.3% to 74.7%, floor 87.0%']   <- floor deleted, floor announced
set thresholds.pass_rate_floor=0.11 : []
      announced only: ['… interval 29.3% to 74.7%, floor 87.0%']
all of the above                    : []
      announced only: ['… interval 29.3% to 74.7%, floor 11.0%']   <- the page follows the fallback
```

Note the empty `[]` on every line: **before this change `probe` printed those four
empty lists and nothing else**, because the sentence it was asked to grep is not
in the visible text. The tool for confirming a REVERSE was blind to the only
REVERSE that mattered here. `quote` prints the accessible names unconditionally
for the same reason.

`_gated(candidate_gate.get("min_rate"), thresholds.get("pass_rate_floor"))` in
`series.py:215` is the fallback the probe follows.

---

## The tools

### `differential_render.py` — the sweep

For every leaf path in the evidence payload, render five documents that differ in
that field and in nothing else — a recorded value (**A**), a measured zero (**B**),
the key removed (**C1**), the key set to `null` (**C2**), the parent object
removed (**C3**) — flatten each to text and compare whole pages. Byte-identity
between B and any C is the rule failing: *an absence rendering as a measurement.*

```bash
.venv/bin/python scripts/audit/differential_render.py sweep
.venv/bin/python scripts/audit/differential_render.py sweep --fixture single/comparison --json out.json
.venv/bin/python scripts/audit/differential_render.py quote single/comparison 'judges[0].candidate.min_rate'
.venv/bin/python scripts/audit/differential_render.py probe single/comparison \
    --grep "Mann-Whitney p-value (alpha" \
    --del 'judges[0].alpha' --set 'judges[0].regression.alpha=0.09'
```

Five fixtures, 513 leaf paths, **2,391 renders**; a few minutes. `--json` saves
the raw result so follow-up analysis need not re-run it — with two hash tables per
leaf now, `hashes` (the flattened text) and `name_hashes` (the accessible names).
An older `--json` reader still finds `hashes` meaning exactly what it meant before.

* `sweep` classifies every leaf as COLLISION / REVERSE / TRIVIAL /
  ZERO-AND-ABSENCE-BOTH-INVISIBLE / **A11Y-NAME-ONLY** / clean.
* `quote` prints the page region for one path, A against B, and names which
  absence variants are byte-identical to the measured zero. This is what turns a
  hash comparison into a quotable finding.
* `probe` renders hand-built mutations and greps one line out of each. It is how
  a REVERSE verdict gets confirmed — a REVERSE is always the claim that the page
  printed a number that came from somewhere else, and `probe` makes the page
  follow the suspected fallback. The example above is a real one: remove
  `judges[0].alpha` and the printed alpha silently becomes
  `judges[0].regression.alpha`.

### `masking.py` — the trap, as code

`mask_page(text, evidence_path=…)` and `mask_run_output(text, evidence_path=…)`.
Read its docstring before changing anything that compares two pages.

### `page_text.py` — HTML to readable text

Flattens a rendered report so a diff points at a *sentence* rather than at a
400-character `<p>`. Table cells are joined with ` | ` so a row stays on one line
and stays readable, which is what half the findings in this project turn on.

```bash
.venv/bin/python scripts/audit/page_text.py report.html > report.txt
```

It **drops attributes**, so the SVG numbers (`data-value`, `data-created`) are
gone. A finding about a chart has to be made against the raw HTML.

`accessible_names(source)` is the companion, and the two are separate functions on
purpose: one answers "what does the reader see?", the other "what is announced?",
and a tool that merges them cannot tell an unrendered field from a tooltip-only
one. It returns the `<svg>` `<title>`/`<desc>` text plus `title=`, `alt=` and the
text-bearing `aria-*` attributes, one per line in document order. Presence there is
not by itself a finding — it is the *difference* between the two channels that
carries one.

### `fixtures.py` — evidence logs to render against

```bash
.venv/bin/python scripts/audit/fixtures.py <output-directory>
```

* `standard_scenario(root)` — the suite's own single-run scenario.
* `candidate_field_log(root)` — ten comparisons in one log: three comparable
  candidates, three runs that **must** be excluded (foreign golden-set hash,
  foreign judges hash, empty model id), three different kinds of absence (no pass
  rate, no floor anywhere, no adapter) and a newest run that supersedes an
  earlier one. The three excluded runs carry the *best* pass rates in the log on
  purpose: a renderer that forgets to exclude them does not show an extra row, it
  shows a winner.

**This module imports `tests/test_report.py` deliberately** — `_scenario`,
`_write_evidence` and `_record`. An audit fixture written from scratch is an
audit of a fixture nobody else uses, and a disagreement with the suite's fixtures
turns every finding into an argument about the fixture. The cost, stated plainly:
this breaks if those three private helpers are renamed, and it breaks in one
place (`fixtures.test_report_module`) with a message saying so.

Inherited from the suite and load-bearing: **every statistic in these payloads is
deliberately inconsistent with what a recomputation would give.** 17/20 is exactly
0.85; the recorded `pass_rate` is 0.4242. If 0.85 appears on a rendered page,
something recomputed.

### `recompute.py` — the arithmetic, from outside

The report never recomputes a statistic; it echoes what `comparison.py` recorded.
That is the right design and it means the report cannot notice a number computed
over the wrong `n`. This re-derives the demo's statistics from the scripted
responses and the golden set:

```bash
.venv/bin/python scripts/audit/recompute.py
.venv/bin/python scripts/audit/recompute.py --rebuild /tmp/demo-work
```

It prints the per-item judge scores, the Mann-Whitney U p-value **twice** — over
the 60 completions a side the payload reports, and over the 12 independent items
the demo actually has — and the Wilson intervals at both. On the shipped demo
that is p = 0.0078 against p = 0.153, and an interval roughly half the width the
independent data supports.

Two deliberate choices:

* The Wilson arithmetic is **written out from the formula and does not call
  `opik_rigor.wilson_interval`**. A recomputation that calls the code under audit
  tests the call site, not the number. (Mechanically it also matters: `scripts/`
  is one of the three directories `scripts/dependency_surface.py` walks, so an
  `import opik_rigor` here would add a row to `COMPATIBILITY.md` and fail the
  merge gate. An audit tool should not change the surface of the thing it
  audits.)
* `--rebuild` exists because **`migkit demo` deletes its own work directory** —
  in a `finally`, *after* writing the HTML — so the page names six absolute paths
  that are already gone when anyone reads it. `rebuild_demo` runs the same demo
  into a directory you keep, producing an identical log (same golden-set, judges
  and config hashes; only timestamps and paths differ).

### `mutation_harness.py` — does the suite notice?

Change one line of shipped source so the document says something false, run the
whole suite, and see whether it still passes. 30 mutations are catalogued; the
recorded run was **17 survived, 13 killed**.

```bash
git worktree add --detach /tmp/mk-mutation HEAD
.venv/bin/python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --list
.venv/bin/python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --run M1 M4
.venv/bin/python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --all
.venv/bin/python scripts/audit/mutation_harness.py --worktree /tmp/mk-mutation --render M1
```

`--render` is not optional decoration: **a surviving mutant is only a finding if
it changes the document.** It renders the demo clean and mutated and diffs the
two, and a survivor with an empty diff is a mutation of dead code, to be
discarded rather than reported. That diff is only readable because both sides go
through `mask_run_output` — see the top of this file.

Safety, all of it enforced rather than advised:

* `prepare()` **refuses a worktree that is the repository root**, and refuses
  anything that is not a checkout of this project.
* Every target file is copied and sha1'd before anything runs; every mutation is
  undone in a `finally` by copying the backup back and re-checking the hash. Not
  `git checkout --`.
* Confirm `git status` is clean before reporting anything.

### `netguard.py` and `shuffle_order.py` — pytest plugins

Loaded with `-p`, not imported.

```bash
PYTHONPATH=scripts/audit .venv/bin/python -m pytest -q -p netguard
PYTHONPATH=scripts/audit AUDIT_SHUFFLE_SEED=1234 \
    .venv/bin/python -m pytest -q -p shuffle_order
```

`PYTHONPATH` is needed because `-p` imports by module name and this directory is
not on the path — `-p netguard` alone fails with `No module named 'netguard'`.

`netguard` turns any outbound connection or DNS resolution into a hard error, so
"green with no credentials and no network" is proved at the syscall seam rather
than by grepping for `requests`. Loopback is left alone. `shuffle_order` shuffles
the whole collected list with an explicit seed — neither `pytest-randomly` nor
`pytest-random-order` is installed and the audit brief forbids installing into
the shared venv.

---

## Platform notes

**The interpreter.** `CLAUDE.md` names the Windows interpreter
(`C:\Users\ewehm\repos\migration-kit\.venv\Scripts\python.exe`). These tools were
written and run on the second-operator **macOS** machine, where it is
`.venv/bin/python` from the repository root. Nothing here hardcodes either: every
path is derived from `__file__`.

**Worktrees on macOS: `PYTHONPATH` is required.** `CLAUDE.md` documents a `.pth`
import hook that resolves the package from the current working directory's
worktree. It did **not** take effect for a worktree outside the repository root
on this machine:

```
$ cd <worktree> && .venv/bin/python -c "import model_migration_kit as m; print(m.__file__)"
/Users/…/model-migration-kit/src/model_migration_kit/__init__.py     # WRONG TREE
```

Every mutant would have "survived", against unmutated code, silently.
`mutation_harness.py` therefore sets `PYTHONPATH=<worktree>/src` on every
subprocess **and asserts that the import actually resolves inside the worktree
before applying a single mutation** — it exits rather than produce a page of
meaningless survivors.

**`masking.py`'s path mask is POSIX-only.** `_ABS_PATH` matches `/a/b/c`, not
`C:\Users\…`. On Windows a run-to-run comparison will still show the temporary
directory as a difference; add a pattern before trusting an empty diff there.

**The demo's work directory** is `/var/folders/…/migkit-demo-XXXXXXXX` on macOS
and `%TEMP%` elsewhere; only `masking.py` and `recompute.py --rebuild` care.

**Load.** `mutation_harness.py` defaults to `pytest -n 4`, per `CLAUDE.md`: wall
clock here is dominated by other agents, not by the code. Use `--workers 8` only
when the board is quiet.

### `page_text.py` dropped SVG `<title>` on 2026-08-24 — and why that is not cosmetic

`html_to_text` used to strip tags and keep their character data, so the text of an SVG
`<title>` came back as prose. An SVG `<title>` is a **tooltip and an accessible name**, not
something a sighted reader sees. The consequence was a false positive in the one question this
whole toolkit exists to answer:

```
$ migkit demo --out demo.html
$ page_text.py demo.html | grep -c "candidate accuracy: pass rate"
1          # before the fix -- the tool says a screen-reader-only string is rendered text
0          # after
```

That string lives only in `<svg><title>`; the same SVG contains **zero `<text>` elements**.

**Two of this project's audit findings are about `<title>`-only disclosures** — the banner
bar's "floor not recorded", and the timeline's zero-span note — so with the old behaviour the
sweep that found them would have reported them as present. A measurement tool that counts a
tooltip as text reports a screen-reader-only disclosure as a rendered one, which is exactly the
class of defect these audits exist to find.

`<title>` and `<desc>` are now dropped alongside `<script>` and `<style>`, **inside an `<svg>`
only** — the first version of the fix dropped `<head><title>` too, which is not a tooltip at
all, and that cost a round trip. Verified afterwards that real prose is untouched
(`FAKE MODELS`, `Methodology appendix`, `Flips`, `Provenance`, `roughly 140` all still found).

**If you want the accessible names, parse the raw HTML for them deliberately** — do not get them
by accident from a function whose contract is "what the reader sees". `accessible_names()` is
that deliberate parse, and it is the other half of this fix: dropping the tooltips from
`html_to_text` was necessary and, on its own, told the sweep that a tooltip-only disclosure was
no disclosure. See *"a field can reach the page without being visible"* near the top of this
file.
