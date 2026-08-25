# Next steps for the second operator

Written on the Windows machine after reading the audit through `c79f9df`. Two
jobs: push the tooling, then a second audit that only this machine can usefully
do.

**Read the whole file before starting.** Job 2 is the valuable one.

---

## First, the verdict on the audit itself

It found things the four-role pipeline on the other machine did not, and the
reason is structural: **every role there reads the contract; this one read the
document.** That gap is now written into the project's handoff as a standing
lesson.

**The adversarial pass is the best thing in it.** Nine findings refuted, 25
weakened, 20 surviving is a far more useful result than 41 confirmed would have
been, and the structural criticism it makes is correct and was about to cost a
whole chunk of work on this end:

> Most of Tier 2 is an argument about robustness to a foreign or future writer.
> There is exactly one writer of the comparison payload and it writes every key
> unconditionally. Without that caveat a fix pass would spend a chunk hardening
> reads against a writer that does not exist.

**Finding 35 was promoted correctly and is now scheduled first**, ahead of
everything else, because it is what decides whether the rest of Tier 2 is
reachable at all. *"The one reader built to tolerate a foreign payload is the
only one that will not say it has one"* is the sharpest sentence in the audit.

Findings 1, 3, 4 and 5 were demoted and that was right. Finding 1 in particular:
the report faithfully echoes what `comparison.py` computed, so it is a framing
gap, not a manufactured result — it belongs with the other disclosure gaps.

Scheduling on the Windows side, for context:

| Order | Chunk | Findings |
|---|---|---|
| 1 | schema guard on the evidence log — the prerequisite | 35 |
| 2 | the completeness certificate counts the wrong characters | 6, 6a, 6b |
| 3 | latency suppressed by adapter name, not by absence | 2 |
| 4 | the banner's bar is drawn for a different judge than its verdict | 8, 9a |
| 5 | disclosures that never reach the terminal | 22, 16, 26 |
| 6 | wording, units, scope | 23, 24, 29, 31, 32, 36, 37, 40, + 1, 3, 4, 5 |

---

## Job 1 — push the tooling

Only `AUDIT-macbook.md` is on the branch. The tooling is worth more than the
report, because the report is a snapshot and the tooling is repeatable.

Put it under **`scripts/audit/`** — that directory already exists and already
holds audit tooling (`netguard.py`, `shuffle_order.py`), so it needs no new
convention and the project's own lint gate will cover it.

Include:

- the **differential renderer** that swept the 176 leaf paths
- the **masking** that strips the evidence hash, generated timestamp and
  absolute paths before comparing
- the **recomputation scripts** (the scipy p-value and interval work)
- the **adversarial-review harness**
- the **mutation-testing harness**, if it is a script rather than by hand

Add a short **`scripts/audit/README.md`** saying what each does, how to run it,
and anything that only works on macOS.

**Put the trap in the README.** The page prints an evidence hash over the whole
file, so a naive diff finds every pair different and reports **zero** findings.
That cost the first sweep and it is exactly the kind of thing that lives in one
person's head and gets rediscovered expensively.

```bash
git add scripts/audit
git commit -m "The audit tooling, so the sweep can be rerun"
git push
```

If the scripts live in a temp or scratch directory outside the repo, **copy**
them in rather than moving them.

---

## Job 2 — audit the terminal renderer

**This is the job worth doing, and the audit's own findings are why.**

Three separate surviving findings point at the same untested surface:

- **16**: `--quiet` silences the FAKE MODELS band entirely — *"the tool's central
  claim is that you cannot get a clean-looking report out of scripted models;
  `--quiet` produces one."*
- **22**: the terminal never discloses that runs were excluded.
- **2**, which got *stronger* under refutation: the terminal prints the exact
  `0.000 / 0.000` row that the HTML says it omitted — better evidence than the
  original finding gave.

**So the terminal is a second document that nobody has read end to end**, and it
is the surface a CI log captures and a developer actually sees. The HTML has now
had a blind pair, a 52-mutant review and your audit. The terminal has had none of
it.

### What to do

Render the terminal — `render_terminal` — across the same fixture shapes you
already built for the HTML sweep, and answer:

1. **What does the HTML disclose that the terminal does not?** Enumerate it. Both
   surfaces are supposed to say the same words: the project's own discipline is
   that a sentence is written where its numbers are computed, precisely so the
   two cannot drift. Every divergence is either a defect or an undocumented
   decision.
2. **What does the terminal say that the HTML does not**, and is it true?
3. **What do the flags suppress?** `--quiet` is finding 16. Check every other
   flag that changes output. A disclosure that any flag can remove is not a
   disclosure.
4. **Does the terminal survive the same absence cases?** An unrecorded value, a
   measured zero and a missing key must be distinguishable there too. Your
   differential harness should point at the terminal with little modification —
   the masking will differ (no evidence hash, but `rich` wraps and inserts border
   characters mid-sentence, which defeats naive substring comparison; an agent on
   this side hit exactly that and had to strip the box-drawing block first).

### Rules, same as before

- **Do not fix anything.** Find, prove, report.
- **Every claim carries the output that proves it** — the exact terminal text,
  the fixture, the command line.
- **Run an adversarial pass on your own findings again.** It was the most useful
  thing in the first audit; default to REFUTED when uncertain.
- If you cannot reproduce a suspicion, say so and drop it.
- Widths matter: `rich` reflows. Check at least two terminal widths and say which.

### Report

Append to `AUDIT-macbook.md` as a new top-level section, or write
`AUDIT-terminal.md` — your call, but say which in the commit message. Rank by
what a reader of the terminal would be misled about, and state the adversarial
verdicts inline this time rather than only in a summary table: the WEAKENED
items in the first audit have no inline note, so a reader cannot tell *how* they
were weakened.

Then push to the same branch.

---

## One correction to the brief you were given

The setup block said `pip install -e ".[dev]"` after `python3 -m venv`. You found
that `python3` was Anaconda 3.9.13 against a `requires-python = ">=3.10"`, and
used `/opt/homebrew/bin/python3.12`. That was the right call and the brief was
wrong. Worth saying because reporting it rather than routing around it silently
is the behaviour this project runs on.
