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


---

# `scripts/dependency_surface.py`

**The claim.** The gate's docstring (`:22-24`): *"Deliberately AST-based rather than a grep:
`from opik_rigor.judge import X` and `from opik_rigor import X` are the same dependency for this
purpose and must land in one row."* `COMPATIBILITY.md:167` publishes the result as: *"Every
`.py` file under `src/`, `tests/` and `scripts/` that names `opik_rigor` is on this list;
**anything not on it is not relied on and a rigor release may move it freely.**"* CI's step is
named *"Dependency surface matches the tree"*.

## G11. `import opik_rigor.judge` is invisible — the one spelling the gate was built to fold

`dependency_surface.py:60`:

```python
imports_module |= any(alias.name == "opik_rigor" for alias in node.names)
```

For `import opik_rigor.judge`, `alias.name` is `"opik_rigor.judge"`. The folding the docstring
promises is implemented for `ast.ImportFrom` only; the plain-`import` submodule form falls
through both branches. Verified by calling the gate's own `rigor_imports()`:

```
SEEN       import opik_rigor
INVISIBLE  import opik_rigor.judge
INVISIBLE  import opik_rigor.judge as J
SEEN       from opik_rigor import X
SEEN       from opik_rigor.judge import X
INVISIBLE  import scipy.stats
```

With those two lines added to shipped `src/model_migration_kit/report.py` in a worktree:

```
$ .venv/bin/python scripts/dependency_surface.py --check
dependency-surface table agrees with the tree (25 modules)
EXIT=0
$ .venv/bin/ruff check src tests
All checks passed!
$ .venv/bin/python -m pytest tests/test_import_purity.py -q
13 passed
```

**The defect the docstring cites as the reason the gate exists** — *"`comparison.py` had been
reaching into `opik_rigor.judge` in shipped code the whole time and appeared on no list"* — is
reproducible today, in the spelling a developer uses when they want the module object.

> **SURVIVES.** `grep -rEn "^\s*import opik_rigor\." src tests scripts` → **0**, so this is a
> live hole rather than a live bug, and I say so. Nothing else covers it: ruff is green, and
> `test_import_purity.py` checks a **fixed forbidden set** (`jinja2, rich, anthropic, openai,
> opik`) — it answers *"did these five leak?"*, not *"what did we take on?"*.

## G12. The gate named "dependency surface" sees no dependency except `opik_rigor`

`import scipy.stats` in shipped `report.py` — a third-party package **not declared in
`pyproject.toml`** — passes `--check` at exit 0 and passes `test_import_purity.py`.

> **SURVIVES, with its partial cover named.** The docstring scopes itself to rigor honestly in
> the body; the **name, the output string and the CI step name** all read wider, and this job
> ranks by what a maintainer would wrongly believe. `ci.yml`'s `demo` job is a real partial
> backstop — it installs the wheel into a clean venv and runs `migkit demo`, which imports
> `report.py`, so an *undeclared and uninstalled* package would fail there. It does not cover a
> dependency that is present **transitively** (which `scipy` is, via rigor), nor a dev-only one,
> nor anything in `tests/` or `scripts/`.

## G13. The gate audits the checkout the *script* lives in, not the one you are in

`REPO = Path(__file__).resolve().parent.parent`. The cwd is never consulted. Same cwd, two
answers, with the violating file present only in the worktree:

```
$ .venv/bin/python scripts/dependency_surface.py --check                       # relative
COMPATIBILITY.md's dependency-surface table disagrees with the tree.  EXIT=1
$ .venv/bin/python /Users/.../model-migration-kit/scripts/dependency_surface.py --check
dependency-surface table agrees with the tree (25 modules)            EXIT=0
```

Neither line names which `COMPATIBILITY.md` or which `src/` was read.

> **SURVIVES, scoped.** Weaker than G1–G4: it needs the operator to type the other checkout's
> path, whereas `check_contract.py` escapes on data inside the repo. But this project runs *one
> agent, one worktree* with up to ten live at once, and CLAUDE.md teaches agents to invoke tools
> by absolute path — for the interpreter. CI does not hit it.

## G14. An unreadable `scripts/*.py` is invisible to both CI steps

`except (SyntaxError, UnicodeDecodeError): return [], False`. The load-bearing detail is an
**asymmetry between two spellings of the same lint**: `ci.yml:41` runs `ruff check src tests`,
while `check_merge.py` runs `ruff check .`.

```
src/...   unparseable, imports rigor  -> ruff check src tests: Found 2 errors   (CI RED)
scripts/  unparseable, imports rigor  -> ruff check src tests: All checks passed  (CI GREEN)
                                      -> dependency_surface --check: agrees (25)  EXIT=0
```

`scripts/` is inside the gate's search path and outside CI's lint, and `check_merge.py` — the
only thing that would catch it — **is run by no workflow**.

> **SURVIVES for `scripts/`; WEAKENED for `src/` and `tests/`,** where ruff in CI covers it.

## G15. `worktree_path.py --status` prints a counterfactual in the shape of a measurement

Not a gate — reported because it is the diagnostic every agent brief points at, and because the
failure is this project's own named defect class.

```
$ .venv/bin/python scripts/worktree_path.py --status
original saved: no
hook module present: no
this cwd would resolve to: .../gate-dep/wt/src

$ .venv/bin/python -c "import model_migration_kit as m; print(m.__file__)"
/Users/ericw/IdeaProjects/model-migration-kit/src/model_migration_kit/__init__.py
```

The last status line is what the hook **would** choose if installed, printed identically whether
it is active or absent — beside three lines that are live facts. A reader running `--status` to
answer *"am I importing the right tree?"* — the only reason to run it — reads that line as the
answer and gets the opposite of the truth.

> **SURVIVES as a diagnostic defect, not a gate bypass** (it exits 0 either way). CLAUDE.md
> states as fact that *"the `.pth` trap is fixed; you no longer need `PYTHONPATH`"* — true of
> the Windows venv it describes, **false on this machine**. Two agents in this audit hit it and
> had to pin `PYTHONPATH` explicitly.

## G16. Smaller, confirmed

- **The gate does not verify which table it read.** The first line matching `TABLE_HEADER`
  anywhere in a 1,644-line document wins — an HTML comment included. As an *attack* this is
  contrived and **WEAKENED**; as an **anchoring defect it SURVIVES**, because §1 already carries
  prose about the generator directly above the table, and the day any worked example of the
  output format appears earlier, the gate silently starts checking that one with the same
  `[PASS]` either way.
- **`from .opik_rigor import X` — a *relative* import — is recorded as a rigor dependency**
  (`node.level` is never read), failing loudly and demanding a row for a module with no rigor
  dependency. Consequence is inverted from everything else here: it pushes the table toward
  **overstatement**, which nobody reading for the known failure mode will notice.
- **A bogus search path reports success.** With `SEARCHED` pointing at a directory that does not
  exist, `rglob` returns `[]`, `table_rows()` returns 0 rows, and `--check` prints
  `agrees with the tree (0 modules)`. **The gate cannot distinguish "the doc is complete" from
  "the gate found nothing."**
- **`make_showcase_goldenset.py --check` is a gate nobody invokes** — not CI, not
  `check_merge.py`, not the suite; the only references are a comment and an assertion that the
  *file exists*. Editing the data is caught by a pinned content hash; editing the **generator**
  without regenerating is caught by nothing. Same shape: `clean_venv_check.ps1` is referenced
  only by a CI comment.

## G17. Exclusions, measured — and this one is evidenced

| exclusion | measured cost today |
|---|---|
| `SEARCHED = ("src","tests","scripts")`, repo root excluded | **1** tracked `.py` outside them (`conftest.py`), naming `opik_rigor` **0** times |
| `.pyi` stubs | **0** in the tree (confirmed a `.pyi` with a rigor import leaves `--check` green) |
| `except (SyntaxError, UnicodeDecodeError)` | **0** tracked `.py` currently fail `ast.parse` |
| `rglob` sweeps untracked files (inverse of `check_merge.py`'s `git ls-files`) | **0** untracked `.py` in those dirs — but the two gates disagree about what "the tree" is |

> **Credit where it is due, and it is the counter-example to the brief's `UPPER_CASE`
> precedent.** The `SEARCHED` docstring records the *exact past cost* of its own omission —
> `scripts/showcase.py` invisible, the doc incomplete by one row while `--check` passed — and
> states its premise. That is a **reason, not a guess**, which is what the brief asks an
> exclusion to carry. It measures zero today only because the one excluded file happens not to
> import rigor; `conftest.py` already manipulates `sys.path` for the package, so that is
> contingent rather than structural.

**Exit codes sound.** Mismatch → 1. Missing `COMPATIBILITY.md` → uncaught `FileNotFoundError`,
traceback, exit 1. No failure path exits 0.

---

# `contracts.hash_bytes` / `hash_file` — SOUND

**REFUTED as a finding, and this is the negative result this job most needed**, because every
provenance claim in every report rests on it.

```
20,000 random byte strings over CR/LF/CRLF/ascii/utf8/NUL vs an independent
stdlib oracle .......................................... 0 mismatches
16,000 (payload, chunk-size) pairs, every chunk size 1..40 ... 0 mismatches
```

The chunk sweep is the part that matters: `hash_file` holds back a trailing `\r` and prepends it
to the next chunk, so CRLF pairs straddling a read boundary are the hazard. Zero disagreements,
including a file whose final byte is a lone `\r`.

**Every hashing site in `src/` and `scripts/` was enumerated and each uses the convention.**
`goldenset.parse` hashes `raw` *before* `.decode("utf-8-sig")`, so a BOM is part of `file_hash`
and not part of `hash` — the one place a bytes/decode confusion could have hidden, and it is
correct and documented. `dimensions._digest` uses blake2b on decoded text, but it is a tally key
that is never rendered or compared to a file digest. **Nothing hashes a path.**

**The strongest single piece of evidence is cross-platform and did not need constructing:**
hashes pasted into `README.md` on the **Windows** machine reproduce exactly here.

```
README.md:531  5fef50364057cad869f16698df32d927b650778c34382f6f68d9fd53ba4e9a04
measured here  5fef50364057cad869f16698df32d927b650778c34382f6f68d9fd53ba4e9a04
```

> **One suspicion chased and dropped.** The report prints `evidence hash` with no statement of
> the convention, so a reviewer running `sha256sum` on a CRLF file would get a different number
> and could not tell line endings from tampering. **Dropped twice over:** rigor writes the
> evidence log with `os.write` of `(line + "\n").encode()` — raw bytes, LF on every platform, so
> plain `sha256sum` agrees; and `.gitattributes` sets `* text=auto eol=lf` with a comment naming
> this exact hazard.
>
> **Not covered:** symlinked paths into `hash_file`, TOCTOU between chunked reads, and any
> Windows-only filesystem behaviour this machine cannot exercise.
