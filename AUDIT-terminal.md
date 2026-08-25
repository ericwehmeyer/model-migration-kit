# Second audit: the terminal renderer

Commissioned by `AUDIT-NEXT-STEPS.md` (Job 2), written from the macOS second-operator
machine against `audit/macbook-2026-08-24`.

**Why this document exists**, in the Windows operator's words: *"the terminal is a second
document that nobody has read end to end, and it is the surface a CI log captures and a
developer actually sees. The HTML has now had a blind pair, a 52-mutant review and your
audit. The terminal has had none of it."*

Same rules as the first audit: find, prove, report — **nothing was fixed**. Every claim
carries the terminal text, the fixture and the command line. Adversarial verdicts are stated
**inline**, at the finding, as the brief asked.

**Normalisation.** `rich` wraps text and inserts box-drawing characters mid-sentence, which
defeats naive substring comparison. Output was captured to files (so rich sees a pipe, width
80 unless `COLUMNS` says otherwise) and every command was re-run on a real pty to confirm the
text is identical. Widths tested: 20, 40, 80, 120, 200, 400.

---

## T0. A **GO** piped through `head` exits **1**, prints no verdict, and says nothing on stderr

**The sharpest finding on this surface, and the only one that corrupts the channel CI actually
gates on.** A passing migration is reported as a failing one, silently.

```
$ migkit report <go-log>                                  # unpiped
exit=0                                                    # GO

$ migkit report <go-log> 2>err.txt | head -40 > out.txt
exit=1        VERDICT lines in output=0        stderr bytes=0

$ migkit report <go-log> 2>/dev/null | cat > /dev/null     # control: cat drains
exit=0
```

Forty lines of report, **zero `VERDICT:` lines**, **zero bytes on stderr**, exit **1**.
It is the broken pipe, not the verdict, that sets the code — a REVIEW exits 1 through the same
pipe.

**Cause.** `rich/console.py:2030-2042`:

```python
def on_broken_pipe(self) -> None:
    self.quiet = True
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    raise SystemExit(1)
```

Both of migkit's guards miss, because `SystemExit` is a `BaseException` and not an `Exception`:
`cli.py:421-424`'s `contextlib.suppress(BrokenPipeError)`, and `main`'s `except Exception`. And
`on_broken_pipe` has already `dup2`'d `/dev/null` over fd 1, so the verdict line that "still
tries to write" writes to nowhere.

**The promise it falsifies is written down, in `cli._write`'s own docstring:**

> *"`migkit report | head` closes the pipe mid-document. The reader chose to stop, so the write
> is dropped and **the exit code is left alone** — turning that into an error would make a
> perfectly good report look like a failed one."*

That is precisely what happens. `grep -rn 'BrokenPipe' tests/*.py` returns nothing.

> **Adversarial verdict: SURVIVES, with one sub-claim I could not reproduce.** The agent that
> found this reported `grep -m1 -F VERDICT` — the idiomatic way to lift a verdict out of a log —
> also exiting 1. **On my fixture it exited 0**, because the document was short enough that grep
> drained it before closing. So the trigger is "the reader stops before the writer finishes",
> which depends on document length and reader buffering; `head -40` reproduces reliably, `grep
> -m1` does not. I am reporting the reproducible form only.
>
> Two further honest qualifications. **No CI workflow in this repo pipes migkit's output** (see
> T19), so this is not live in *this* project's CI today. And the proximate cause is rich's, not
> migkit's — but the defect is that migkit wrote a guard for exactly this case and caught the
> wrong exception type. `pyproject.toml` pins `rich>=13.0` with **no ceiling**, so a pipeline's
> exit code depends on transitive resolution.

## T1. A non-UTF-8 console turns NO-GO (exit 1) into "could not produce a verdict" (exit 3)

**The exit code a CI system gates on depends on the terminal width and the console encoding.**

```
$ PYTHONIOENCODING=ascii COLUMNS=80 migkit report <log> --html out.html
migkit: UnicodeEncodeError: 'ascii' codec can't encode character '…' in position 78
*** You may need to add PYTHONIOENCODING=utf-8 to your environment ***
exit=3          <-- the verdict is NO-GO, which is exit 1

$ PYTHONIOENCODING=ascii COLUMNS=400 migkit report <log> --html out.html
exit=1          <-- same log, same verdict, nothing ellipsised
```

The verdict is **never printed**. `--traceback` pins it to `report.py:3390 out.print(facts)`.

**The character that breaks it is `…` — the ellipsis rich inserts when it truncates.**
So the tool's own truncation marker is what crashes it, which is why the failure is
width-dependent: wide enough that nothing is truncated, and it succeeds.

The docstring claims exactly the immunity it lacks — *"rich degrades to ASCII box characters
on its own … where `print()` raises `UnicodeEncodeError` on a legacy Windows console."* The
box characters do degrade; the ellipsis does not. `UnicodeEncodeError` is a subclass of
`ValueError`, so `EXPECTED_ERRORS` swallows it as a *user* error and returns 3.

**And `--quiet` is the workaround** — silencing the disclosures makes it work
(`--quiet` -> 1, `--no-terminal` -> 1).

> **Adversarial verdict: SURVIVES, with its reachability stated.** Plain `LC_ALL=C` does
> **not** reproduce this on POSIX (PEP 538 coercion); it needs `PYTHONUTF8=0` or an explicit
> `PYTHONIOENCODING`. On a Windows console at `chcp 437`/`850` it is the default — **and that
> is the machine this project's pipeline runs on.** I could not test Windows from here; one
> command confirms or kills it there. `cp1252` and `cp932` both contain U+2026 and exit 1.

**What a reader wrongly concludes:** a pipeline distinguishing NO-GO (1) from
tool-failure (3) gets the wrong answer, and the operator never sees the verdict at all.

## T2. `--quiet` on `run` hides a 62%-failure run behind exit 0

```
$ migkit run --goldenset ... --adapter fake --n 2 --out d/
migkit: 24 completion(s), 0 failed, 1 part(s)          <-- the failure count lives here
exit=0

$ migkit --quiet run --goldenset ... --adapter fake --n 2 --out d/
exit=0   stdout bytes=173   stderr bytes=0             <-- the line is gone
```

With 15 of 24 draws timing out, the un-quiet form reports
`24 completion(s), 15 failed, 1 part(s)`; `--quiet` emits **zero bytes of stderr**.

`--help` promises: *"silence progress and the terminal tables; the verdict line and errors
still print."* A completion-failure count is **not** progress, **not** a table, **not** the
verdict line and **not** an error — and `run` has no tables at all. `--quiet` also removes
`concurrency 8 exceeds n=2 … the effective width is 2`, which `cli.py:466-474` says exists
*"because the operator reading a CI log is the one who set it."*

Compounding: `run` produces no verdict, so its exit 0 *never* means GO — the CLI's own help
says a pipeline gating on `run` "gates on nothing". `--quiet` removes the only signal that
would have told the operator the run was mostly failures.

> **Adversarial verdict: SURVIVES.** Errors genuinely do survive `--quiet` (verified); this
> is a *warning-class* disclosure, which the help text does not cover.

## T3. `--quiet` and `--no-terminal` are all-or-nothing, on every verb

`report` goes 86 stdout lines -> 1; `compare` 98 -> 1. Both leave **24 bytes**. The two flags
are behaviourally identical on `report`.

Sharpest case: a report whose artifacts **could not be read at all** — `completions 0 / 60`,
a Completeness strip and **six warnings**, 126 lines / 10,814 bytes — collapses under
`--quiet` to `VERDICT: NO-GO (exit 1)`, 24 bytes.

> **Adversarial verdict: SURVIVES, and it corrects finding 16 of the first audit.** Finding 16
> framed this as a `demo` bug and claimed *"`--quiet` produces a clean-looking report"* — which
> is **false**, because the HTML still bands twice. The real finding is this one: the terminal
> render is all-or-nothing on every verb, and what it drops includes warnings.

## T4. `--goldenset` override is invisible on the terminal, and the warning points at a block that does not exist

`diff` of `report` with and without `--goldenset <identical copy elsewhere>` at width 80:
**no output — byte-identical.** The path is ellipsised exactly where it diverges. Forced
narrow, the row reads `golden set  ./elsewhere/demo_goldenset.jsonl` beside the **recorded**
hash, unmarked — a false path/hash pairing.

`migkit report --help` promises: *"override the recorded golden-set path (**the override is
printed in the report**)"*. The HTML does emit `<dt>golden-set override</dt>` naming both
paths. The terminal emits **zero words**.

| | HTML | terminal |
|---|---|---|
| `--goldenset` | 1 provenance row | **0** |
| `--artifact-dir` | 2 list items + 1 row | 2 warnings |

And the terminal's own `--artifact-dir` warning says *"the override is printed in the
provenance block"* — **the terminal has no provenance block.**

> **Adversarial verdict: SURVIVES, weakened.** A golden set whose *content* differs fails the
> hash check and the terminal does warn, so no wrong *numbers* reach the reader. What is lost
> is the audit trail and one false path/hash pairing.

## T5. Refuted, and worth recording

The most likely-sounding suspicion in this area is **false**, and it took real work to kill:

- **"A verdict carried only by colour."** **REFUTED.** `NO_COLOR=1` gives text
  **byte-identical** to the coloured pty render (156 -> 112 escapes, all bold/dim);
  `TERM=dumb` gives **0 escapes** and identical text. `GO`/`NO-GO`/`REVIEW`, `yes`/`no`,
  `not available` and `-` are all **words**, not colours. This is the project's central rule
  honoured on a surface where it would have been easy to break.
- **Piping changes a disclosure.** REFUTED — pty text == pipe text; `CI=true`, unset `TERM`
  and `TTY_COMPATIBLE=0/1` are all identical.
- **The golden-set hash is ellipsised away.** REFUTED — it wraps and survives at every width
  from 20 to 400. Only the *path* is cut.
- **Content dropped rather than wrapped at narrow widths.** Not reproduced: line counts grow
  monotonically (200 -> 72, 80 -> 86, 40 -> 115, 20 -> 165) and no row, table, warning or
  strip disappears.
- **`--traceback` changes normal output.** REFUTED — 86 lines byte-identical.
- **`--no-terminal` changes the HTML.** REFUTED — 30,151 bytes either way, differing in
  exactly two lines, both the `generated` timestamp.
- **`| head` loses the exit code.** REFUTED (`pipestatus = 1 0`).
- **`--quiet` suppresses errors.** REFUTED — errors do print.

## T6. Smaller, confirmed

- **At width 80 the two pinned model ids are unreadable**: `claude-sonnet-4-5-20250929` and
  `…-20251115` both render as `claude-sonnet-4-5-2025…`. **COLUMNS >= 88 is required** to tell
  a migration's two sides apart, and there is no width flag. *Weakened: the `…` marks it, so
  this fails legibility rather than disclosure.*
- **Panel subtitles truncate with no marker at all.** `decided by no recorded rule` becomes
  `decided by ` at width 16 and `decided by no r` at width 20; complete only at width >= 34.
  Table cells get `…`; Panel titles and subtitles get nothing. *Latent: reported for the
  mechanism, not the width.*
- **`migkit report` silently reads `./migkit.toml`.** There is no `--config` on `report`, yet
  `[report] max_report_chars = 500` in the cwd changes the document. `migkit.toml` appears
  **0 times** in the terminal and **0 times** in the HTML; the `config` row names the *judges*
  TOML instead. *Weakened: affects both surfaces equally and never changes a number.*
- **Global flags must precede the subcommand** — `migkit demo --quiet` is a usage error
  (exit 3).

---

## T7. A log with no judge rows renders a confident NO-GO and says nothing about it

**The worst finding on this surface.** The HTML says it in three places; the terminal in none —
and it does not fall silent, it prints a verdict.

Fixture: `payload["judges"] == []`. Everything else is a normal run — 60 records a side, a
`migkit.verdict` of `NO-GO`, a reason naming a judge.

**Terminal**, the whole transition from thresholds to flips:

```
threshold power_target 0.77 (…/migkit.toml)
Flips (passing -> failing): 2
item      margin        judges
item-03   5/5 -> 0/5    accuracy
```

**HTML**, same model, three separate disclosures:

```
No judge rows are recorded in this evidence log, so nothing here measures quality.
candidate: pass rate not recorded, interval not recorded, floor 87.0%
1 run(s) recorded no pass rate, so they carry no marker
```

The template has an explicit arm — verified:

```
$ grep 'not model.judges' <template>
{% if not model.judges %}<p class="note">No judge rows are recorded in this
evidence log, so nothing here measures quality.</p>{% endif %}
```

`render_terminal` has **no counterpart**: `for one in model.judges` (report.py:3092) simply
iterates nothing and emits nothing.

**What a reader wrongly concludes:** a CI log shows `VERDICT: NO-GO (exit 1)`, a reason naming
a judge, `completions 60 / 60`, six thresholds and a flip list — with no signal that **no judge
measured anything**. This is the project's central rule in its silent form: on a surface where
nothing reads as "nothing to report", the absence renders as *nothing at all*.

## T8. The p-value column is labelled `(alpha)` and prints alpha — which is not the number the gate used

With more than one judge the gate compares each p-value to a **Holm threshold**, not to alpha.

```
report.py:3425   "p-value (alpha)",                                    <- terminal
report.py:4365   Mann-Whitney p-value (alpha {{ judge.alpha }}          <- HTML
                                       Holm threshold {{ ... }})
```

Fixture: two judges, alpha 0.030, Holm threshold 0.0150, `safety` recorded p = 0.0290 and
`regressed = False`.

**Terminal:**
```
│ p-value (alpha)                          │ 0.029000 (0.030) │
│ regressed / floor cleared / underpowered │ no / no / no     │
```
**HTML, same row:** `Mann-Whitney p-value (alpha 0.030, Holm threshold 0.0150) | 0.029000`

A reader does the comparison the row invites — 0.029 < 0.030 — and gets "significant". The next
row says `regressed: no`. **The terminal presents a self-contradiction and withholds the one
number that resolves it**, while the banner one screen up asserts the correction happened
("after Holm-Bonferroni correction across judges").

## T9. With no `--html`, the terminal directs the reader to a report that was never written

```
$ migkit report <log>                       # no --html
Full outputs, the flip list and the methodology appendix are in the HTML report;
a terminal is not where anyone reads 5 pairs of model outputs.
VERDICT: NO-GO (exit 1)
VERDICT: NO-GO (exit 1)
```

`--html` is optional on `report` and on `compare`; the sentence is unconditional. The reader is
sent to the only place the outputs, the exclusions and the methodology live, and that place does
not exist.

## T10. "a terminal is not where anyone reads 5 pairs of model outputs" — the 5 is draws per item

The value is `model.n_per_item`. It does not vary with the number of changed items, which is
what the sentence appears to be about. Proven three ways:

**Vary n, hold items fixed** — same 4 changed items each time:
```
demo --n 3  ->  "… a terminal is not where anyone reads 3 pairs of model outputs."
demo --n 9  ->  "… a terminal is not where anyone reads 9 pairs of model outputs."
```
**Vary items, hold n fixed** — a 24-item golden set giving 7 changed items:
```
Every one of the 7 changed item(s) carries its full outputs: 1,816 characters …
… a terminal is not where anyone reads 5 pairs of model outputs.
```
**Two consecutive lines reading "7 changed item(s)" and "5 pairs".** The HTML holds 7
item-pairs, i.e. 35 draw-pairs. Neither is 5.

**The code's own prose disagrees with the rendered string.** `_print_changes`, twelve lines
below, docstrings itself: *"A terminal is not where anyone reads twenty pairs of model
outputs"* — twenty being 4 items x 5 draws for the bundled demo. The author's own count for the
same document is 20; the sentence says 5.

And on a zero-change log it still prints `5 pairs` where the true quantity is **zero** — a
number printed where none was measured, in the closing line of the document.

> **Adversarial verdict: SURVIVES, weakened.** A charitable reading is "n_per_item paired draws
> *per item*", which may be what the author meant. Two things keep it alive: the sentence
> carries no per-item qualifier and follows a sentence whose subject is *items*; and the
> zero-change case prints an unconditional quantity where the truth is zero.

## T11. The closing sentence names the flip list as HTML-only while the terminal is printing one

The same sentence is the terminal's **only** statement about its own incompleteness, so what it
names is what a reader believes was left out. It names three things. One of them is on screen:

```
Flips (passing -> failing): 2          <- nine lines above the sentence
item      margin        judges
item-03   5/5 -> 0/5    accuracy
```

`_print_changes`'s docstring is explicit that this is deliberate — *"Ids and margins only. The
full text is the HTML's job."* The true statement is "the outputs *behind* the flip list".

What the sentence does **not** name, measured on the demo run — all absent from the terminal:
the whole dimension matrix, the exclusions, the run history and its gap counts, the latency
"Not measured" disclosure, `imputed`, `judge parse failures`, `powered for the configured
effect`, the Holm threshold, the golden-set composition and tag distribution, the rubric hash,
"Exit code a CI system would have received", the banner headline row — and **the entire
provenance block: evidence hash, tool version, `generated` timestamp, run-artifact paths.**

Two of those matter beyond enumeration. **A CI log captured from the terminal cannot be tied to
the evidence file it came from, or to the build that produced it** — no evidence hash, no
version, no timestamp. And with no rubric hash, **judge drift is undetectable on this surface.**

## T12. At the CLI's own default width, the model ids ellipsise and the deciding rule truncates with no marker

`render_terminal` does `out = console or Console()`. Redirected with `COLUMNS` unset — *which is
a CI log* — that is width 80.

```
╰─ decided by rule 2 (one-sided Wilson lower bound below the configured pass-r─╯
│ model  │ claude-sonnet-4-5-20250… │ claude-sonnet-4-5-2026… │
```

Two distinct defects:

1. **`decided by` is a rich `Panel` subtitle, and rich *crops* a subtitle with no ellipsis.**
   `…pass-r─╯` is indistinguishable from a rule name that genuinely ends there. Every other
   truncation in the document marks itself with `…`; this one does not.
2. **The model ids appear exactly once in the whole terminal render.** At 80 both are cut, at
   *different* lengths because the columns size independently — so two dated snapshots of one
   model family become `claude-sonnet-4-5-20250…` and `claude-sonnet-4-5-2026…`. That is
   precisely the migration this tool exists to adjudicate.

## T13. `--goldenset` pairs the override path with the recorded hash of a different file

```
 golden set        /…/gs24.jsonl
                   (5fef50364057cad8)
 golden-set size   not available
…
warning: the golden set at /…/gs24.jsonl no longer matches the one that was run
(e3cc8937ed9b33fe now, 5fef50364057cad8 then), so the inputs are not shown.
```

`gs24.jsonl` hashes to `e3cc8937…`. The provenance row pairs that filename with `5fef5036…`,
the hash of a **different file** — and the terminal's own warning, 50 lines later, says so. A
reviewer copying path + hash out of a CI log verifies a false pairing. **The terminal never
states that an override happened at all**; the recorded path appears nowhere on the surface.

The HTML carries `golden-set override — read from <X> rather than the recorded <Y>`.

*Credit where due:* `golden-set size  not available` is an absence rendered as an absence, not
as `0`. That half is right.

## T14. Comparison-level facts are printed in the "baseline" column

```
│ p-value (alpha)                          │ 0.007843 (0.050) │                  │
│ test that ran                            │ mann-whitney-u   │                  │
│ regressed / floor cleared / underpowered │ yes / no / no    │                  │
```

The HTML renders all three with `colspan="2"` (report.py:4368, 4370, 4372) because they belong
to neither side. `rich` has no colspan, so the terminal files them under **baseline**.

`regressed: yes` therefore reads as *the baseline regressed*. It is the candidate. And the empty
candidate cell is **not** the terminal's absence marker — four rows up, an unrecorded latency
renders `-`, so a blank cell here is indistinguishable from "the candidate's p-value was not
recorded". The degradation is forced by the widget; that it is silent is not.

## T15. Shared sentences, written for a document the terminal is not

This is the finding to carry back, because it explains most of the others. The project's
discipline — *write the sentence where its numbers are computed, so the two surfaces cannot
drift* — is exactly what puts these strings on the terminal verbatim. They were written for a
document with a provenance block and embedded outputs:

- **The capped-budget sentence:** *"…their model text is not embedded — it is in the run
  artifacts named in the provenance block."* `grep -ci "run artifacts"` on the terminal: **0**.
  It also claims the other rows are *"listed in full with their ids, tags, judges and
  margins"* — the terminal's change tables have **no tags column at all**.
- **The uncapped sentence:** *"Every one of the N changed item(s) carries its full outputs:
  5,821 characters of quoted model text."* Zero of those characters are on the terminal
  (`grep -c "98.10\|11 March"` -> 0 terminal, 6 HTML). **The document making the completeness
  claim is the document that contains nothing it certifies.**
- **The artifact-override warning:** *"the override is printed in the provenance block."*

**Shared wording stopped the two surfaces drifting, and made one of them say something untrue
instead.**

## T16. Smaller, confirmed

- **`underpowered: no` in the table; "cannot detect a 10% drop at 80% power" in a warning 26
  lines later.** The HTML prints both notions adjacently so the reader can see they are
  different fields; the terminal prints the first and drops `powered for the configured effect`
  entirely.
- **No per-dimension table and no sentence saying why.** `DimensionMatrix`'s own comment says
  the refusal sentence exists because *"`{}` is not a sentence anyone can print"* — and it is
  printed on one surface only. On the demo the terminal shows one aggregate 75.0%; the HTML
  shows the regression is entirely in `refusal` and `multi-value`, and that **no cell has enough
  items for a verdict**.
- **Golden-set composition is absent**: no "8 with a reference, 0 untagged", no tag
  distribution.
- **"This report is partial." never reaches the terminal.** The strip is titled the neutral noun
  `Completeness`, which unheaded reads as reassurance — directly beneath *"Every one of the 3
  changed item(s) carries its full outputs"*.
- **The terminal never says how many comparisons the log holds.** On a 10-comparison log,
  `grep -c "comparison(s)"` -> 0. The render is byte-comparable to a one-comparison log.
- **Row labels drop qualifiers the HTML kept:** `Wilson interval (two-sided)` loses *(for
  printing)*, whose matched pair *(the number the gate used)* is what tells a reader this
  interval is **not** the gate.
- **The duplicate `VERDICT:` line is deliberate and documented** (report.py:3477, cli.py:430 —
  the last line of the *document* and the last line of the *process*). Not a defect, but the two
  are byte-identical and, with no `--html`, adjacent: a CI assertion grepping `^VERDICT:` and
  expecting one match fails.

## T17. Where the terminal is *better* than the HTML

Recorded because it is the only such divergence found. **The terminal de-duplicates the capped
detail sentence and the HTML does not** — `grep -c "The budget for quoted model text"` gives
**2** on the HTML and **1** on the terminal. The dedupe is deliberate and commented: *"a
terminal that says the same sentence twice teaches the reader to skim the second one."*

## T18. Arithmetic and absence handling that check out

Coverage on the record. Recomputed from scratch with scipy against ground truth derived from
`demo.py`'s scripted responses, never from either rendering:

```
55/60: two-sided [0.8193, 0.9639]  one-sided lower 0.8385  rate 91.7%
45/60: two-sided [0.6277, 0.8422]  one-sided lower 0.6486  rate 75.0%
mannwhitneyu(candidate, baseline, alternative='less').pvalue = 0.007843147236661034
```

All six printed values exact; the terminal prints `0.007843`. Counts agree with what is listed
(`Flips: 3` -> 3 rows, `Gains: 1` -> 1, `4 changed item(s)` = 3+1+0); item counts `11/1/0` and
`9/3/0` each sum to the golden set's 12; `55/60` and `45/60` are 11x5 and 9x5.

**Absence handling passes on the terminal** where it is applied: latency missing key -> `- / -`,
`null` -> `- / -`, `0.0` -> `0.000 / 0.000`, half-recorded -> `0.500 / -`; golden-set size under
a hash mismatch -> `not available`, never `0`; a no-verdict log -> `NO VERDICT (exit 3)` with
*"the run ended between the comparison and the verdict, so this report is evidence and not a
decision"*.

**No silent loss at any width.** Rendered at 200/120/100/80/60/50/40: every truncation carries a
visible `…` (the sole exception is T12's Panel subtitle), every table keeps all three columns,
and no number wraps in a way that changes its meaning. Line counts grow monotonically
(200 -> 72, 80 -> 86, 40 -> 115, 20 -> 165) — rich wraps rather than drops.

**The terminal never recomputes.** On the deliberately-inconsistent test fixture it echoes
`17 / 20` beside a recorded `42.4%` faithfully — which is the correct behaviour and is evidence
that nothing is derived on this surface.

## T19. The brief's premise, revised: no workflow reads a character of the terminal output

Reported because it changes who this document's audience is, and because a negative that
revises the commissioning brief is worth more than one that confirms it.

`ci.yml`'s `demo` job — **the only job that runs the tool** — asserts exactly three things: the
**exit code**, `test -f demo-report.html`, and a `grep` over **the HTML**. The terminal render
is never redirected, captured or grepped. `drift-canary.yml` is the same shape and its own
comment says *"Every value interpolated here is an exit code this job captured, not text from an
index."* `publish.yml` never invokes migkit.

So the terminal renderer's audience is **a human scrolling a failed log**, not a machine. Two
consequences, and they pull in opposite directions:

- It makes **T0 the highest-severity finding here by a wide margin**, because the exit code is
  the one thing anything gates on.
- It *lowers* the cost of every reflow, truncation and width finding below — nothing parses
  those bytes.

## T20. Stream routing, and what each stream alone would show

| command | stdout alone | stderr alone | bytes out/err |
|---|---|---|---|
| `demo` | band, banner, tables, warnings, `VERDICT:` ×2, html path | 6 progress lines — **no verdict, no band, no warning** | 8248 / 239 |
| `report` (success) | whole document + `VERDICT:` ×2 | **empty (0 bytes)** | 7268 / **0** |
| `report` (error or crash) | **empty (0 bytes)** | one `migkit:` line, or a traceback | **0** / 336–964 |
| `run` (success) | **one line: the artifact path** — no verdict, no adapter, no failure count | header, per-item lines, `N completion(s), M failed, P part(s)` | 184 / 655 |
| `run --quiet` | **one line: the artifact path** | *empty* | 172 / 0 |

Two rules fall out:

1. **The verdict never reaches stderr, on any path, under any flag.** A stderr-only capture of a
   successful `migkit report` is *literally zero bytes*.
2. **An error never reaches stdout, on any path.** Every exit 3 writes **zero bytes** to stdout,
   so a stdout-only capture cannot distinguish "crashed" from "never ran".

Both `VERDICT:` lines are on stdout, so the deliberate duplication (T16) buys a stderr-only
capture nothing.

> **A clean negative worth recording:** the sharpest question here was *is the verdict on one
> stream and the warning that qualifies it on the other?* The answer is **no** — warnings are on
> stdout with the verdict, matching contract §3.4. But `--quiet` removes the qualifying warning
> from **both** streams while still printing `VERDICT: GO (exit 0)`, so there is no stream on
> which that disclosure survives.

## T21. Exit codes: thirteen of fifteen scenarios correct

Verified by real invocation with `echo $?`, one payload field mutated per fixture.

Correct: GO→0, NO-GO→1, REVIEW→2, verdict record absent→3, unrecognised verdict word→3,
comparison record absent→3, **the `warnings: null` crash→3 (not 0)**, `run` completes→0,
`run` cannot read the golden set→3, `compare` misconfigured→3, usage error→3 (not argparse's 2),
`--help`/`--version`→0. The two failures are T0.

**And an invariant I tried to break and could not:** editing the payload's own `exit_code` cannot
make the printed `(exit N)` disagree with `$?`, because `report.py:1923` derives it from the
verdict word rather than reading the field. That is the mechanism behind finding 5 of the first
audit — and here it is a *guarantee*, not a defect.

## T22. Not a tty: no content change at all

Ran `report` on a real pty of controlled size (`pty.fork` + `TIOCSWINSZ`, 80×40) and diffed
against the same command redirected at the same width, after stripping ANSI and `\r`:
**identical content, not one character differs.** The pty capture carries 136 escape sequences
and 78 `\r`; **every redirected capture in this audit carries zero of either**, and zero other
control bytes. The `\r` is the tty driver's `ONLCR`, not the tool's.

`TERM=dumb`, `NO_COLOR=1`, `CI=true`, `GITHUB_ACTIONS=true` and `TERM=xterm-256color` all give
byte-identical output. Only `FORCE_COLOR=1` changes anything, and with the SGR sequences
stripped it is byte-identical to plain — correct, opt-in behaviour.

**No progress bar, no cursor movement, no line rewriting, nothing hostile to a log viewer.**

## T23. Width with no tty is 80, and truncation is always marked

`Console()` with no tty and no `COLUMNS` reports `is_terminal False, width 80, color_system
None`. rich honours `COLUMNS` even without a tty; GitHub Actions sets none, so **80 is the CI
width** — which is what makes T12 a default-path finding rather than an edge case.

> **Silent truncation: REFUTED.** A 73-character model id and a 38-character adapter name both
> render with a visible `…` at width 80. Every shortened *cell* I could produce carries one. The
> single unmarked artefact is in *prose*: rich hard-wraps a long path mid-token, so an artifact
> path is three tokens at 80 and one at 200 — grepping a CI log for a path fails at the default
> width, though no characters are lost.

---

# Untrusted input: the terminal's own attack surface

The HTML's escaping was audited and found solid. The terminal has no equivalent apparatus, and
it has a surface HTML does not: **control sequences that rewrite what is already on screen.**

`_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")` (report.py:3260) is the whole of the defence.
Swept across 25 payload fields, **ESC, CR and BEL never survive on any field reaching
`render_terminal`** — `_cell` closes that completely. The holes are the CLI printing *around*
the renderer, and the class being too narrow.

## T24. The one line the tool promises always prints is the only unsanitised one

```python
verdict = model.verdict or NO_VERDICT
code = Verdict.exit_code(model.verdict or Verdict.ERROR)
_out(f"VERDICT: {verdict} (exit {code})")        # cli.py:437 — raw f-string, no _CONTROL_RE
```

The comment directly above it: *"Always the last line of stdout, including under `--quiet`: a CI
log that scrolls past 200 lines of table still ends with the finding."*

With the payload's `verdict` set to `"NO-GO\n\x1b[3A\x1b[2KVERDICT: GO (exit 0)\x1b[3B"`, the
**entire stdout under `--quiet`** is:

```
00000000: 5645 5244 4943 543a 204e 4f2d 474f 0a1b  VERDICT: NO-GO..
00000010: 5b33 411b 5b32 4b56 4552 4449 4354 3a20  [3A.[2KVERDICT:
00000020: 474f 2028 6578 6974 2030 291b 5b33 4220  GO (exit 0).[3B
00000030: 2865 7869 7420 3329 0a                    (exit 3).
```

`\x1b[3A` moves up three lines, `\x1b[2K` erases, `VERDICT: GO (exit 0)` is written over it.
Other payloads reach stdout the same way: `\r` overwrite, `\x1b[2J\x1b[1;1H` (clears the screen
and homes the cursor — the whole report is gone), `\x1b[32m` (recolours), and OSC 8 / OSC 52
(a live hyperlink to an attacker URL, and a clipboard write on any emulator honouring OSC 52).

`render_terminal`'s own copy of the same line is safe — `_CONTROL_RE` renders the escapes as
inert spaces. Only the CLI's is not.

> **Adversarial verdict: CONFIRMED, with a real weakening.** `Verdict.exit_code` is an exact
> dict lookup on the *same* string, so tampering also forces exit 3. **A CI gate reading the
> exit code is not fooled.** Fooled are: a human reading the terminal, and a gate grepping the
> log text — that is a byte-level fact, the stream literally contains `VERDICT: GO (exit 0)`, so
> `grep -q 'VERDICT: GO'` matches. And **erasure needs no forged exit code at all**: the
> screen-clear payload destroys the document while still exiting 3.

## T25. `migkit run` writes raw ESC and CR from golden-set ids — and exits 0

`cli.py:482` prints progress through `say()` → `sys.stderr.write`, with no `_CONTROL_RE`, and
`item.id` comes straight out of the golden-set JSONL. With two crafted ids:

```
000000b0: 2f32 5d20 6974 656d 2d30 310d 1b5b 324b  /2] item-01..[2K
000000c0: 6d69 676b 6974 3a20 5b31 322f 3132 5d20  migkit: [12/12]
000000d0: 6974 656d 2d39 393a 2035 2064 7261 7728  item-99: 5 draw(
```

CR returns to column 0 and `migkit: [12/12] item-99: 5 draw(s)` overwrites `migkit: [1/2]
item-01`, with `\x1b[2K` erasing the tail so nothing gives it away. **A run over 2 items reports
itself as a run over 12.** A second id emits a bare `\x1b[31m` with no reset, so every
subsequent line of the CI log is red. GitHub Actions and GitLab both implement `\r` as
line-overwrite in their log viewers, so this reproduces in the UI as well as in a terminal.

**Exit code: 0.** This is the stronger of the two: the log is forged *and* the gate is green.

## T26. `_CONTROL_RE` stops at `\x1f`, so C1 controls pass

U+009B is 8-bit CSI. Under a single-byte stdout encoding it leaves the process as a **raw
`0x9b`** inside a table cell, from a recorded `model_id`:

```
b'... | model-a-20260101 | cand\x9b2J\x9b1;1H          |\n'
```

— `CSI 2 J` (erase display) and `CSI 1;1 H` (cursor home).

> **Adversarial verdict: CONFIRMED but NARROWED, and the narrowing matters.** Under default
> UTF-8 it is emitted as `c2 9b` and is inert on every mainstream emulator. And
> `PYTHONIOENCODING=cp1252` — **the Windows console default, on this project's primary
> platform** — does *not* produce the raw byte: it raises `UnicodeEncodeError` and exits 3, a
> denial rather than an injection. Only latin-1-class encodings emit it. **Do not rank this
> above T24/T25.**

## T27. Zero-width and bidi characters survive and render at zero width

Neither U+200B nor U+202A–U+202E is in `[\x00-\x1f\x7f]`, and `rich.cells.cell_len` gives them
width 0 — so they pass the filter, occupy no columns, and disturb no table geometry.

- **Two different model ids render identically.** `model-a-2026<ZWSP>0101` displays as
  `model-a-20260101` in both columns. A reader checking *which two models were compared* reads
  the same string twice.
- **A ZWSP in the verdict word changes the exit code and the banner colour without changing the
  visible word.** `"NO-GO​"` → `Verdict.exit_code` misses → exit **3** instead of 1, and
  `_VERDICT_STYLE.get` misses → the panel border is **white instead of red**. The word on screen
  still reads `NO-GO`.

> **U+202E: the inverse of the HTML finding, and I am reporting what I measured rather than what
> I expected.** In the HTML it flips a regression into a gain. On the terminal rich assigns it
> zero width and mainstream emulators do not reorder already-laid-out cells, so it is **simply
> invisible**. The suspicion that bidi breaks rich's geometry is **REFUTED** — the panel line is
> 101 characters and 100 columns, padded correctly. The harm is that two ids a reader compares
> by eye become indistinguishable, and that it survives a copy-paste into a browser or ticket
> where bidi *is* implemented.

## T28. What held, verified rather than assumed

- **rich console markup is inert on every terminal field** — verified on a *real tty* through
  the default `Console()`, including the bare `[/]` that a docstring says once raised
  `MarkupError`. Six hostile markup payloads, all literal, no exception.
- **No field can inject a newline, CR or ESC into `render_terminal`'s output** — 25/25 fields
  swept.
- **The terminal's untrusted surface is far smaller than the HTML's.** Model outputs, judge
  per-item reasons, golden-set input text and item tags **never reach the terminal at all**.
- **rich is not a second line of defence**, so `_CONTROL_RE` is the whole of it: measured
  directly, rich strips `\r` but passes ESC through both a `Text` and a table cell unchanged.
  Any field added to `render_terminal` without `_cell` is exposed immediately.

---

# The absence sweep, pointed at the terminal

The HTML sweep re-run against `render_terminal`: 176 leaf paths x 5 fixtures x 3 widths,
~13,000 renders. The harness **imports** the HTML sweep's leaf enumeration and variant builders
rather than re-implementing them, so the two join path-by-path.

*Normalisation was proved, not asserted:* at widths 80/100/200 the number of verdicts that
differ between raw-byte and normalised comparison is **0**, so the box-drawing trap cannot have
produced a false negative.

**Coverage, per comparison fixture (161 paths): 43 collisions, 63 never printed, 33
field-invisible-but-parent-visible, and only 21 (13%) that genuinely distinguish all three
states.** 96 of 161 leaves (60%) never reach the terminal at all — finding T11 quantified.

**And the terminal reads only the newest comparison record**: the single-run, multi-run and
newest-mutated fixtures give *identical* verdicts on all 161 paths.

## T29. `passed / observed: 0 / 0` for a judge that never took a sample — and the flag that says so is read by nobody

Four states — a measured zero, the key removed, the key null, and the whole gate object gone —
produce one **byte-identical** document.

The usual reachability objection does not apply here, because **the writer deliberately flags
this exact case**: `comparison.py:1211-1237` emits `"n": 0, "successes": 0, "failures": 0, …,
"no_data": True` when a judge has zero records. And:

```
$ grep -rn "no_data" src/ tests/ scripts/
src/model_migration_kit/comparison.py:1234:   "no_data": True,
```

**One line in the entire repository: the write.** No reader anywhere. So the one field that
distinguishes "no data" from "measured zero" is written and discarded, and the terminal renders
`│ passed / observed │ 17 / 20 │ 0 / 0 │` with `-` in the four rows beneath it — which makes the
`0 / 0` read *more* like data, not less.

## T30. `failed completions: 0` and `parts: 1` for a run artifact that does not exist

`report.py:2537-2541`, the no-artifact branch, hardcodes `failures = 0` and `parts = … or 1`:

```
│ model              │          │           │   <- no marker at all
│ adapter            │ -        │ -         │
│ completions        │ 0 / ?    │ 0 / ?     │   <- correct
│ failed completions │ 0        │ 0         │   <- a value
│ parts              │ 1        │ 1         │   <- a value
```

Six rows, five different disciplines. *"Zero completions failed"* is the cleanest possible bill
of health for a run that does not exist — and it is reachable from any log whose artifacts have
been rotated away. `completions` gets it right one row above, using `?`.

## T31. `items passing / failing / unstable` invents a `0`, and leaks a literal `None`

`_item_counts` (report.py:3487-3493) guards the container but defaults each key to `0`. A
measured zero and a removed key both render `0 / 1 / 2`; a null key renders `None / 1 / 2`; only
the whole object going missing renders `-`. **A reader concludes no item passed on the
baseline** — the most alarming thing a cell in this document can say.

## T32. The terminal's absence vocabulary: eleven markers, and four of them are values

One maximal-absence render prints nine rows about facts that were never recorded, and **six of
the nine print a number or a blank**. Good markers that do exist: `?`, `not available`,
`unknown`, `not-run`, `no recorded rule`, `NO VERDICT (exit 3)`, `source not recorded in the
evidence`. The `-` (ASCII hyphen rather than the HTML's em dash) is deliberate and documented —
rich substitutes box characters on a legacy Windows console — but it is also the glyph inside
`->` in every margin `5/5 -> 0/5`.

> **The cross-renderer question, answered — and the answer refutes what I expected.** I went
> looking for drift between the two renderers because it would have been the most valuable
> output. **It is not there.** Every divergence points the same way: **the terminal prints
> less.** Not one field in 176 is collapsed by the HTML and distinguished by the terminal, and
> where both print a field they render its absence identically, including every shared defect
> above. The divergence is one-directional coverage, not disagreement.
>
> On the brief's own validation case: `judges[0].baseline.failures` is **not** a collision in
> the terminal — it is *invisible*, because the terminal has no candidate table. The harness was
> not blind: it caught the `underpowered` and `warnings: null` cases without being told to.

---

# Mutation testing: is `render_terminal` tested at all?

The brief's premise — *"the HTML has had a blind pair, a 52-mutant review and your audit; the
terminal has had none of it"* — is **true**, established with numbers and then proved by
mutation in a detached worktree with byte-verified restores.

## The census

| | test fns | reach `render_terminal` | reach `render_html*` |
|---|---:|---:|---:|
| `test_report.py` | 271 | **3** | 89 |
| `test_report_scale.py` | 23 | 1 | 19 |
| `test_report_untrusted_input.py` | 36 | 5 | 4 |
| the other 16 files | 1074 | **0** | 0 |
| **total** | **1404** | **9** | **112** |

One of the nine never renders (it only inspects a signature). **So 8 test functions in the
entire repository call `render_terminal`, against 112 that render HTML — 1 : 14.** In
`test_report.py`, the file the blind pair and the 52-mutant review ran against, it is
**3 in 271**.

**Three positive content assertions exist in the whole suite**: the verdict line, the substring
`FAKE`, and one sentence. Nothing asserts a number, rate, interval, flag, count, hash,
threshold, warning or disclosure, and there is no golden file for the terminal anywhere. Of the
thirteen terminal-side helpers, **twelve have no test of their own** — the `_cell` and
`_item_counts` hits in `tests/` are different local helpers of the same name.

## 22 of 30 mutants survive a green 2206-test suite

### The one that matters most: the FAKE MODELS band can be deleted and its own test still passes

```python
-    if provenance.banded:
+    if False and provenance.banded:
M2-drop-fake-band: SURVIVED   (2206 passed)
```

The test asserts:

```python
assert "FAKE" in buffer.getvalue().upper()
```

and the render — **with the band deleted** — still contains:

```
│ adapter    │ FakeAdapter      │ FakeAdapter      │
          judge: accuracy (fake-judge-v1)
```

`FakeAdapter` and `fake-judge-v1` both uppercase to contain `FAKE`. **The assertion has never
tested the band.**

The project owns exactly the right constant —
`FAKE_BAND_MARKERS = ("FAKE MODELS", "scripted responses")`, whose comment reads *"Both halves
are asserted so a band that dropped the explanation still fails"* — and I checked all four of
its uses: **every one is `_parse(_html(...))`, HTML-side. The terminal was never wired to it.**

This is the mechanism behind T3: you do not need `--quiet` to get a clean-looking report out of
scripted models. A one-line regression does it, and CI stays green.

### The central-rule mutants, all surviving

| mutation | result | what the terminal then prints |
|---|---|---|
| `_cell` renders the absence marker as `0` | **SURVIVED** | `adapter │ 0 │ 0` where no adapter was recorded |
| `_num` / `_pct` / `_interval`, terminal branch only | **SURVIVED** ×3 | `pass rate │ 0.0%`, `lower bound │ 0`, `interval │ [0.0000, 0.0000]` for a judge that measured nothing |
| `_latency_cell` → `0.000` | **SURVIVED** | the exact row the HTML says it omits — and nothing can tell it from a real one |
| `_flag`: "not recorded" → `no` | **SURVIVED** | `- / - / no` becomes `no / no / no` |
| `_item_counts` invents `0 / 0 / 0`; swaps passing↔failing | **SURVIVED** ×2 | `0 / 0 / 0`, and `1 / 9 / 2` for `9 / 1 / 2` |
| verdict **panel** always says `exit 0` | **SURVIVED** | `NO-GO  (exit 0)` at the top of the scrollback |
| baseline/candidate columns swapped | **SURVIVED** | the one row saying which model is which, transposed |
| verdict reason deleted; warnings loop skipped; Completeness strip deleted; facts table deleted; closing pointer deleted | **SURVIVED** ×5 | every attestation the terminal carries |
| `_print_changes` prints only the first row | **SURVIVED** | `Flips: 2` above one row |

### The borrowed-coverage experiment

The unguarded shared-formatter mutants *were* killed — each by exactly one test,
`test_zero_observed_completions_render_as_an_em_dash`, which does
`_parse(_html(...))` and counts `—` (`EM_DASH`). The terminal uses `TERMINAL_DASH = "-"` and is
never rendered by that test. **The kill is entirely HTML-side.** The control is the row above:
the same three defects confined to the terminal branch all **survived**.

> **So the terminal's coverage of this project's central design rule is borrowed in full.**
> Delete the HTML renderer from the suite and the absence rule is untested for the terminal in
> all six of its formatters.

**Killed: 8 of 30** — and of those, six were killed by tests whose subject is something else
(five control-character security tests that happen to need the table present, and three an
HTML em-dash count). **Killed by an assertion whose subject is the terminal's content: two.**

> **One mutant reported at reduced strength, deliberately.** `M23-failures-always-zero` survived,
> but `RunSummary.failures` derives from the run artifact rather than the payload keys that were
> patched, so no fixture demonstrated a changed row. **No claim is made that a reader would be
> misled by it.**
