# Job 3 — audit the gates

The terminal audit landed and the tooling is in. This is the next job, and it
follows directly from something the Windows-side verification found while you
were working.

## Why this job

**A gate was found that can pass against a file that is not in the tree it is
checking.** `check_contract.py`'s citation regex excludes `:`, so a rooted
Windows citation loses its drive letter and is joined to the *drive* of `root` —
and the check resolves into a **different checkout** and passes. Live example in
the repo: `docs/release-evidence.md:104`.

**And the merge gate was found green on a tree where the document's loudest
sentence is inverted.** Inverting R29.1's fix — the exemplar defect your first
audit brief was built around — leaves `scripts/check_merge.py` reporting `[PASS]`
on all seven checks, pytest included, across 2,241 tests.

Two independent gates, two ways of being satisfied by something other than what
they claim to check. **That is a class, and nobody has looked at it
systematically.**

Your first audit asked *does this document lie.* This one asks:

> **Can each of these checks be satisfied by something other than what it claims
> to check?**

## Scope

Everything in `scripts/` that gates anything, plus the CI workflows that run
them:

- `scripts/check_merge.py` — seven checks, the merge gate
- `scripts/check_contract.py` — plan-to-code citations
- `scripts/verify_release.py` — the release gate (**note**: PASS is exit 0, SKIP
  is exit 2, and the script says so — the earlier suspicion about this does not
  reproduce, so start from the assumption it is right and try to break it
  anyway)
- `scripts/dependency_surface.py`
- `scripts/audit/netguard.py`
- `scripts/audit/shuffle_order.py`
- `.github/workflows/*.yml` — including whether a gate that fails locally can
  pass in CI, or the reverse

`conftest.py` too, if it changes what the suite proves.

## The questions

For each gate, in this order:

1. **What does it claim?** Take the claim from its own docstring and its output
   strings, not from the name. A gate that prints `[PASS] no shadowed top-level
   names` is claiming something specific; write down what.
2. **What does it actually check?** Read the implementation.
3. **Construct the gap.** Build a tree that the gate passes and that violates the
   claim. That is the finding, and it needs to be a real tree, not an argument.
4. **What are its exclusions, and are they evidenced?** This matters as much as
   the rule. A real example from this project: `check_merge.py`'s shadowed-name
   check skipped `UPPER_CASE` names on the premise that "an upper-case rebind is
   usually a deliberate constant edit". That premise was a guess. When it was
   finally measured — **zero** upper-case module-level rebinds in the whole tree
   — the exclusion had cost a real catch and bought nothing. **A gate's exclusion
   needs the same evidence as its rule.** Check every one of them the same way.
5. **Can it be satisfied from outside the tree?** `check_contract.py` can. Paths,
   `PYTHONPATH`, environment, installed packages, absolute vs relative — anywhere
   a gate reads something it did not verify is inside the checkout.
6. **Does it fail loudly or silently?** A check that raises and is caught, or
   that skips a file it cannot parse, may be reporting `[PASS]` over an unread
   file. `check_merge.py` has `except (SyntaxError, OSError): continue` in at
   least two places.
7. **Exit codes.** Does every failure path actually produce a non-zero exit? A
   gate that prints a failure and exits 0 is worse than no gate.

## Rules — same as your first two audits, and they worked

- **Do not fix anything.** Find, prove, report.
- **Every claim carries the output that proves it** — the constructed tree, the
  command, the exact output.
- **Run the adversarial pass again.** It was the most useful thing in both
  audits; default to REFUTED when uncertain. And **state the verdicts inline**
  this time from the start — the first audit's WEAKENED items had no inline note,
  and you fixed that in `f5dbb0e` after being asked. Do it inline from the
  beginning.
- If you cannot reproduce a suspicion, say so and drop it.
- Rank by **what a maintainer would wrongly believe is guaranteed.** A gate's
  whole value is that people stop checking the thing themselves.

## A caveat about your own harness

Carried from the Windows-side verification of your first audit, because it will
bite here too:

> `_visible()` includes SVG `<title>` text, so tooltips read as visible prose.
> Anyone asking "is X on the page?" gets a false positive for every
> `<title>`-only disclosure.

**A measurement tool that counts a tooltip as text will report a
screen-reader-only disclosure as a rendered one** — which is the exact class of
defect the audit exists to find. Fix that in `scripts/audit/page_text.py` before
reusing it, and say in the README that you did.

## Report

`AUDIT-gates.md` at the repo root, pushed to the same branch. Rank by what a
maintainer would wrongly believe is guaranteed, with the constructed tree and the
output for each.

**If a gate is sound, say so and say what you tried.** A negative result that
names its coverage is worth having; one that does not is worth nothing. Your
first audit's negative result held up under re-verification on this side, which
is why it was worth reading.

## And a note back to you

The terminal audit, the tooling, the adversarial passes and the inline verdicts
all landed. Three things from your first audit are now scheduled work here, and
one — the `--quiet` headline — was measured false on this side and struck, which
is exactly what an adversarial pass is for and no criticism of running it.

The single most useful thing either machine has produced is your refuting agent's
observation that most of Tier 2 hardens reads against a writer that does not
exist. It saved a chunk of misdirected work, and it is now a ruling in the plan.
