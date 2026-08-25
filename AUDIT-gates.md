# JOB-3 — audit the gates

Second operator (MacBook), against `AUDIT-JOB-3.md`. The question this job asks is not
*does the document lie* but:

> **Can each of these checks be satisfied by something other than what it claims to check?**

Adversarial verdicts are stated **inline at each finding**, from the start, as the brief and
`JOBS.md` rule 5 require. Ranked by **what a maintainer would wrongly believe is guaranteed** —
a gate's whole value is that people stop checking the thing themselves.

**Nothing was fixed.** Every construction was built in a throwaway git worktree under the
scratchpad; the main checkout was read-only throughout and `src/`/`tests/` were never touched,
per `JOBS.md`.

**One prerequisite the brief required first.** My own harness was reading SVG `<title>` as
visible prose, so "is this sentence on the page?" returned true for a string no sighted reader
can see — the exact class of defect these audits exist to find, and two of my own findings are
*about* `<title>`-only disclosures. Fixed in `48d4c36`, verified against regression, and
recorded in `scripts/audit/README.md`. The gate agents branched from `bce49c9`, the commit
before that fix; it touches nothing they audited.

---

# `scripts/check_contract.py`

**The claim.** `RESTART.md:674-677`: *"catches the mechanical half before dispatch — every
`file.py:NN` **resolved against both trees**, every line number range-checked."* `CLAUDE.md:97`
lists it under **Gates**. Its own docstring: *"Symbols are advisory and file citations are not.
A missing file or an out-of-range line is a **fact**."*

**In one sentence:** it is a file-existence-and-EOF checker over three unbounded filesystem
roots, whose only confinement test is disabled by the presence of a slash.

## G1. One directory component turns the out-of-tree guard off — and the gate's own remediation tells you to add one

`check_contract.py:154`:

```python
if Path(name).parent in (Path("."), Path("")) and REPO not in target.parents:
```

`REPO not in target.parents` — **the entire out-of-tree defence** — is `and`-ed behind *"the
citation had no directory component."*

With a sibling `opik-rigor` checkout present and no `opik_rigor` in the worktree's own `src/`:

```
`judge.py:315`                     -> [FAIL] resolves only in .../opik-rigor/... ; write the
                                      path out so it is not read as this package
`src/opik_rigor/judge.py:315`      -> [PASS] [PASS]  exit 0     (no diagnostic at all)
```

`src/opik_rigor/judge.py` reads as *this package's own `src/`*, resolves into a different
distribution, and passes green. **The gate's remediation string instructs you to do the thing
that disables the check.**

> **SURVIVES.** This is the docstring's own headline defect — the `judge.py` case it was
> written for — in the form the gate does not catch.

## G2. `resolve()` returns paths the gate's own `_is_a_copy()` declares copies

`_is_a_copy()` is applied to the `rglob` branch and **never to `direct`** (`:88-90`), which
returns immediately. Reproduced directly:

```
$ cat vendored.md
The verdict is emitted at `.venv/lib/python3.12/site-packages/opik_rigor/judge.py:315`.

$ .venv/bin/python scripts/check_contract.py vendored.md
[PASS] every cited file exists
[PASS] every cited line is in range
Contract citations check out.
exit=0

  resolve() returned : .../.venv/lib/python3.12/site-packages/opik_rigor/judge.py
  _is_a_copy(that)   : True
```

**A contract can pin line numbers inside a vendored wheel — numbers that drift on every
`pip install -U` — and the gate certifies them.** The filter is one `if` away from the value it
exists to reject.

> **SURVIVES.** Verified by the orchestrator independently, not taken from the agent.

## G3. A citation can be satisfied by a file in no checkout at all

`SIBLINGS = (REPO, REPO.parent / "opik-rigor", REPO.parent)` — the third root is the
*containing directory*. On the real, unmodified checkout:

```
tutorials/netflix-modules/genie/src/main/resources/.../run_spark_submit_job.py:40-60
   -> [PASS] [PASS]  exit 0
```

**A Java tutorials repo satisfies a Python contract citation.**

> **SURVIVES, with its blast radius stated honestly.** On *this* machine only 5 non-copy `.py`
> files sit outside the repo, so the practical reach is small. **On the Windows box it is
> large by this project's own convention:** `CLAUDE.md:80-88` mandates
> `git worktree add --detach /c/Users/ewehm/repos/mk-<chunk>-review`, so `REPO.parent` there is
> the directory *every other agent's worktree lives in* — a citation resolved there is resolved
> against another agent's tree at another commit. The docstring documents `REPO.parent` so
> `opik-rigor/...` can be written naturally; what is undocumented is that it admits **all**
> siblings, with no allowlist and no check that the resolved file is under any checkout.

## G4. `..`, an absolute path, and a committable symlink each leave the tree

Each run in isolation:

| citation | result |
|---|---|
| `../outside-everything/escaped.py:3` | PASS / PASS, exit 0 |
| `/private/tmp/.../escaped.py:4` | PASS / PASS, exit 0 |
| `src/leakdir/escaped.py:5` (symlink out of the tree) | PASS / PASS, exit 0 |

The absolute-path case is **the POSIX-native form of the headline Windows defect, reproducing
here with no Windows involved** — `Path(root) / Path("/abs")` discards `root`. The symlink case
is worse: the citation text is indistinguishable from an in-tree path and it is **committable**
(`git add -n src/leakdir` succeeds; not gitignored). One committed symlink and every citation
through it leaves the tree on every machine, permanently.

> **SURVIVES.** The obvious refutation — *"nobody pastes an absolute path into a contract"* —
> is answered by the repo itself: the live example the brief cites,
> `docs/release-evidence.md:104`, **is an absolute path pasted out of a pytest warning.**
> Pasted output is the intake path.

## G5. "no such file in **either tree**" is printed when only one tree exists

`resolve()` silently `continue`s past a root that does not exist; the message is unconditional.

```
BEFORE (no sibling checkout — this machine's normal state)
  opik-rigor/src/opik_rigor/judge.py:315    -- no such file in either tree   <- REAL FILE
  opik-rigor/src/opik_rigor/nonesuch.py:315 -- no such file in either tree   <- FICTION
```

**Byte-identical sentence, two different facts** — this project's own standing rule, *an
absence must not render as a measurement*, violated inside a gate.

Quantified: the main plan has **102 `.py` citations; 98 resolve, 4 do not, and all 4 are into
the absent sibling.** Checked against the `opik_rigor` actually installed here, all four exist
and are in range, and the plan's `judge.py:315-329` spans the `Verdict(...)` construction it
claims to. **4 unverifiable, 0 wrong** — reported as exit 1 with no `[SKIP]`, no root count and
no mention that a root was absent.

> **SURVIVES.** An operator following `RESTART.md:590` gets a red gate on a clean contract and
> learns to ignore it.

## G6. The documented invocation form can check zero lines and report `[PASS]`

`RESTART.md:590` tells every agent to run `check_contract.py <plan> --from N --to M`. There is
no validation:

```
--from 99999          -> Checked lines 99999-5869 ... [PASS] [PASS]  exit 0
--from 5000 --to 4000 -> Checked lines 5000-4000 ... [PASS] [PASS]  exit 0
--from 0 --to 3       -> Checked lines 0-3       ... [PASS] [PASS]  exit 0
```

`--from 0` is the worst because it looks like an ordinary off-by-one: it becomes the slice
`lines[-1:3]`, which inspects **zero lines** — measured — while the header prints the
*requested* range. **Zero citations checked renders identically to zero citations wrong.**

> **SURVIVES.** Verified by the orchestrator independently.

## G7. The gate checks existence and range, never citation

A contract in which every citation is false — one pointing at a module docstring, one at a
**blank line**, one at another blank line — passes both checks at exit 0. Degenerate numbers
pass too: `report.py:0` (no such line) and the reversed range `report.py:900-12`; only `first`
and `last` are compared to the file length, with no ordering check and no lower bound.

Note the inversion: the gate flags the *symbol* as unverified while certifying the sentence
that places it on a blank line.

> **WEAKENED as "the gate lies" — its output strings are honest** (*"every cited line is in
> **range**"*), and so is `RESTART.md:674`. **SURVIVES on this job's ranking axis:** four of
> the docstring's five exemplar defects are off-by-a-few-lines or wrong-package, and this gate
> catches none of that class. Every `[PASS]` read as "citations verified" means "the integers
> were smaller than a line count" — which is why G1–G4 matter: the one thing it does check, it
> can be made to check in the wrong tree.

## G8. Nothing runs it, and it cannot find the documents it should run on

No CI workflow, no pre-commit config (the file does not exist), no git hook, and **zero test
coverage** — `verify_release.py` has `tests/test_release_checks.py`; this has nothing. Its only
invocation is a human typing it, against one document named on the command line.

**160 of the repo's 262 `.py:NN` citations live in documents the documented `<plan>` workflow
never reaches**, including `COMPATIBILITY.md` — a user-facing document with 42.

## G9. Exclusions, each measured against the real tree

The brief asks for this specifically, and cites the `UPPER_CASE` precedent: an exclusion
premised on a guess that measured **zero** and cost a real catch.

| exclusion | what it actually excludes here | verdict |
|---|---|---|
| `.py`-only regex | 22 `.md:NN` and 1 `.yml:NN` never checked — **including `AUDIT-JOB-3.md`'s own headline citation** | **SURVIVES** |
| `__pycache__` | **0, and structurally dead** — the filter runs only on `rglob("*.py")` and `__pycache__` holds `.pyc`; it can never match | **SURVIVES** |
| `site-packages` | **0 marginal files** — its stated justification is already covered by `.startswith(".venv")` | **SURVIVES** |
| `.git` | **0** | **SURVIVES** |
| `.venv*`, `node_modules` | 2559 and 118 — earning their place | sound |
| ambiguity guard (`len(matches)==1`) | fires **0** times; 13 bare-filename citations, none ambiguous | **WEAKENED** — no cost demonstrated, unlike the `UPPER_CASE` precedent |
| `.claude` | 0 here, but the premise is Windows-only | **WEAKENED**, unmeasurable from this machine |
| prose rule `head.islower() and "_" not in head` | drops **96 occurrences / 45 distinct** names that had *already failed to resolve*, unexamined | **SURVIVES** |
| `index_symbols` scans only `src/`+`tests/` | `scripts/` defines 145 top-level names, indexed **0**; **5 of 65 "resolves nowhere" symbols do resolve** — a note ~8% false is a note readers skip | **SURVIVES** |
| `NOT_OURS` | 31 of 48 heads never appear in the three plans | **WEAKENED** — 25 of 48 do work somewhere, and it is advisory-only |
| `except (SyntaxError, OSError): continue` | 0 unparseable files; can only affect the advisory list, never the exit code | **REFUTED, dropped** |

## G10. Exit codes — sound, and I tried to break them

**REFUTED as a finding.** Eight failure paths constructed, **all non-zero**: bad file citation,
out-of-range line, bare name resolving only in the sibling, plan missing, plan is a directory,
cited target is a directory named `ghost.py`, non-UTF-8 cited file, and the advisory-symbols
case (exit 0, documented at `:25-29` as a design decision). **There is no "prints a failure and
exits 0."** The gap here is a false-negative gap, not an exit-code gap.

