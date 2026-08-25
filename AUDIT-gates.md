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

---

# `scripts/check_merge.py` — the merge gate

**The claim.** *"Refuse a merge that looks green and is not"*; *"Exit 0 only if every check
passed"*; and, in its own words, *"a gate that mistakes absence for success is worth less than
no gate."*

**The summary, before the detail.** The four static checks are honest about *what they find* and
silent about *what they did not look at*. Nothing below needs the gate to be wrong about a file
it read — **every finding is `[PASS]` printed over a file, a name, or a test it never looked
at**, with no line of output distinguishing that from a measurement. This repository's own
standing rule, applied to the tool that enforces it.

## G18. `[PASS] pytest` is not a claim that the test suite ran

`run()` builds `[sys.executable, "-m", "pytest", "tests", "-q"]` with **no `env=`**, and success
is `returncode == 0` and nothing else. The gate never prints, parses or compares a test count.

**With an environment variable this project's own `pyproject.toml` recommends setting:**

```
$ PYTEST_ADDOPTS="--co -q" python scripts/check_merge.py     # tree contains assert 1 == 2
... all seven [PASS] ...
Merge is green on all seven checks.      EXIT=0

$ python -m pytest tests -q | tail -2                        # what it would have found
FAILED tests/test_x2.py::test_x2 - assert 1 == 2
1 failed, 2206 passed
```

**Zero tests executed. Exit 0. Green over `assert 1 == 2`.**

**And no environment variable is required — the tree can do it.** One line after the imports of
`tests/test_report.py`:

```python
pytestmark = pytest.mark.skip(reason="chunk C99b left this in")
```
```
1771 passed, 435 skipped     # this tree, gate green
2208 passed                  # untouched tree, zero skips
```

**435 tests stopped running and the gate said green.** The clean tree has *no* skips at all, so
that is a number the gate could trivially have refused — it never looks.

> **SURVIVES.** Not every filtering form gets through, and I say which: `-k nosuchtest`
> deselects everything and pytest exits **5**, which the gate correctly reports as a failure.
> The two that do get through are shown above, and the `pytestmark` form needs no environment.

## G19. `[PASS] __all__ lists every public name` is false on `main`, and gets *quieter* as the defect worsens

**The printed claim is already false on the tree the gate passes.** Measured across all 49
tracked files: **17 public module-level assignments absent from `__all__`, across 2 modules**
(`comparison.py` 8, `report.py` 9). The docstring justifies the exclusion with a number —
*"Flagging those would report **eight** pre-existing style decisions as merge defects"* — and
the real figure is 17. The exclusion may still be right; **its stated evidence is wrong by more
than 2×.**

**Worse, the check is monotonically more permissive the worse the merge was:**

```
one public function missing from a present __all__  ->  [FAIL] ... EXIT=1
the entire nine-name __all__ block deleted          ->  all seven [PASS], EXIT=0
```

`if declared is None: continue` — a module with **no `__all__` at all** is skipped. That is the
code saying "could not run" while `main()` prints PASS over it, contradicting the docstring's
own rule that *"a check that could not run is a failure, not a pass."*

> **SURVIVES.** `tests/test_stranger_path.py` pins `model_migration_kit.__all__`, so doing this
> to `__init__.py` goes red — but nothing pins `dimensions.__all__`, and ruff's `F822` covers
> only the opposite direction (an entry naming nothing). Nothing backstops an absent one.

## G20. `[PASS] no shadowed top-level names` sees four binding forms; the tree has at least eight

`_top_level_names()` records `FunctionDef`, `ClassDef`, `Assign` with a `Name` target, and
`AnnAssign`. Invisible: tuple/list unpacking, `for` targets, `with…as`, `except…as`, `import`,
augmented assignment, walrus, and anything nested under `if`/`try`.

**The construction is this project's own C22b defect, written as a tuple assignment** — two
blind halves of a chunk appended into one file:

```python
THIRD_MODEL, FOURTH_MODEL = "claude-3-opus", "claude-3-haiku"   # C99a
...
THIRD_MODEL, FOURTH_MODEL = "zeta-9", "alpha-1"                 # C99b
```
```
all seven [PASS], EXIT=0        # and THIRD_MODEL is "zeta-9"
```

Control — the identical collision with plain `Name` targets — **fails**. *Same defect, same
names: `THIRD_MODEL = …` twice fails the gate; `THIRD_MODEL, FOURTH_MODEL = …` twice passes it.*
The source comment says C22b *"was caught only because one of C10's tests happened to assert an
ordering over the constant it had lost"*; this tree removes the luck.

> **SURVIVES, with coverage named honestly.** I probed five rebind forms against **both** check 3
> and ruff, because ruff is check 5 of the same gate. Ruff's `F811` catches the import/def
> collision and all three decorated-def forms. **Tuple-unpack rebinding and a module-level `for`
> target are missed by both.** The gate as a whole is stronger than check 3 alone, and I say so.

## G21. `[PASS] no conflict markers` prints PASS over files it could not read

The docstring: *"An unresolved marker **anywhere** is the cheapest failure to catch."* The read
is wrapped in `except (OSError, UnicodeDecodeError): continue`.

A tracked `docs/merge-note.md` with a real marker pair **and one `0xff` byte**, plus five marker
lines appended to the tracked `.gitattributes`:

```
all seven [PASS], EXIT=0

docs/merge-note.md in the scanned set: True      # scanned, then swallowed by the except
.gitattributes    in the scanned set: False      # suffix not in the keep set
markers reported: []
```

The same six lines in a clean-UTF-8 file are reported correctly. `chmod 000` triggers the same
`continue`. **Two files were not read, `[PASS]` was printed, and nothing said so** — the
docstring's own rule, broken by its own code.

The suffix whitelist, measured: **6 of 100 tracked files excluded** — `.gitattributes`,
`.gitignore`, `LICENSE`, `NOTICE`, `clean_venv_check.ps1`, `py.typed`. Not zero; two are files a
merge genuinely conflicts in and one is executable code. The exclusion carries no comment.

**And a comment describes a rule the code does not contain:** *"`=======` alone is a legal
markdown rule, so it only counts as a marker when a real one is present in the same file."*
There is no such conditional — `=======` is never counted under any condition. Low blast radius,
reported because a maintainer reading it will believe a guard exists.

## G22. `_REBIND_IS_INTENTIONAL` excludes zero things — and is a live bypass of check 4

`frozenset({"overload","register","setter","getter","deleter"})`, matched against the **bare last
component** of any decorator, so `@anything.register` qualifies.

```
top-level defs in the tree carrying ANY of them: 0
names it actually suppresses from the report:    0
```

**Zero across all 49 tracked files** — the same shape as the `UPPER_CASE` premise the brief
cites, removed three lines above it in the same function. But it is not merely dead:

```python
@_plug.register            # a class whose method is literally named "register"
def dimension_summary(cells: list) -> int: ...     # public, not in __all__
```
```
all seven [PASS], EXIT=0
```

The identical undecorated function **fails** check 4.

> **SURVIVES for check 4; REFUTED for check 3** — every decorated collision I built was caught
> by ruff `F811`, so the exclusion costs check 3 nothing *today, because another check happens
> to cover it*. Ruff has no `__all__`-completeness rule, so nothing backstops check 4.

## G23. Smaller, confirmed

- **`check_all_is_complete` skips everything outside `src/`** (undocumented). Measured: **0**
  modules with `__all__` outside `src/` today — free to remove, never justified, and real the
  moment anyone writes one. A constructed `scripts/merge_helper.py` with an incomplete `__all__`
  passes all seven.
- **Checks 1–4 read `git ls-files` (the index); check 5 runs `ruff check .` (the filesystem,
  honouring `.gitignore`).** They disagree about what "the tree" is, in both directions. Ranked
  low because `git merge` stages its own output — it matters for the hand-resolution the
  docstring is aimed at.
- **A tracked `.py` that is not valid UTF-8 kills the gate before it prints anything.**
  `read_text` raises `UnicodeDecodeError`, a `ValueError`, which neither `except` catches.
  **WEAKENED deliberately — this is not a false green**; the traceback propagates and the exit is
  non-zero. What is wrong is the reporting: a raw traceback with **no `[FAIL]` line and no check
  results**, while the same byte in a `.md` produces a silent `[PASS]`. Two checks twenty lines
  apart, opposite failure discipline.

## G24. Negative results — sound, with coverage named

**Exit codes: sound.** All seven checks made to fail **independently**, each a real gate run,
each `EXIT=1`, including `run()`'s `except OSError` for an unspawnable command. **No failure path
exits 0.** Every finding above is a check *reporting* success, never a reported failure exiting 0.

**The `.pth` cross-checkout suspicion on check 7: REFUTED, and it was my strongest prior.**
CLAUDE.md's own story says the editable `.pth` names the main checkout, so a naive worktree run
should test somebody else's code. Broken constant in a worktree's own `src`, gate run there with
`env -u PYTHONPATH`:

```
[FAIL] pytest
    FAILED tests/test_dimensions.py::test_the_completions_floor_is_still_twenty_...
    11 failed, 2195 passed
```

**The worktree's own `src` was tested.** `conftest.py` earns its docstring — it prepends `_SRC`
to `sys.path` *and* to `PYTHONPATH` for children, derived from `__file__` rather than the cwd.
Check 7 cannot be satisfied by another checkout's source.

**Ruff's incremental cache: dropped.** I suspected `ruff check .` could return a stale `[PASS]`
from `.ruff_cache`, which lives outside the tracked tree. Warmed the cache, rewrote a source file
with a **byte-identical-length** real violation and restored the mtime with `os.utime` (same
size, same mtime confirmed). Ruff reported the error both with and without the cache. **Does not
reproduce.**

**Stale `__all__` entries: covered.** Invisible to check 4 but caught by ruff `F822`; measured 0
in the real tree.


---

# `scripts/verify_release.py` — the release gate

The brief said to **start from the assumption it is right**, because my earlier suspicion about
its exit codes did not reproduce on Windows. That was the correct instruction: **the exit-code
contract is exactly as documented**, verified by construction for all four states, and the two
hardest checks resisted deliberate attack.

**Its failures are all one shape: a check that could not do its job says so in the evidence and
`PASS` in the verdict.**

**A count correction first.** The brief and this machine both say 15 checks. **The count is not
fixed** — healthy is 16, cascade is 15. `version-matches-installed` is emitted only from inside
`check_version_coherence`, so when the build is skipped it is not FAILED, not SKIPPED, and **not
in the total**. A maintainer reading `14 skipped, 15 checks total` has no way to know a
sixteenth check exists.

## G25. `console-script` passes on a wheel whose `migkit` command does not exist

Its docstring says that without this check *"installing this wheel yields a `migkit` command
that fails with ModuleNotFoundError on first use"*. It parses the entry point into `module` and
`func` — and **`func` is captured at `:1150` and used only in an evidence string at `:1153`.**
The check looks only for the *module file* in the namelist.

With `migkit = "model_migration_kit.cli:no_such_entrypoint"` and a **fresh build**:

```
[PASS   ] console-script: `migkit` points at a module the wheel ships
            target model_migration_kit.cli:no_such_entrypoint -> looked for
                   ['model_migration_kit/cli.py', ...]
            found in wheel: model_migration_kit/cli.py
16 passed, 0 failed, 0 flagged, 0 skipped, 16 checks total
Every check ran and passed.      EXIT=0
```

That wheel, installed as a user gets it:

```
$ migkit demo
ImportError: cannot import name 'no_such_entrypoint' from 'model_migration_kit.cli'
```

> **CONFIRMED, and ranked first because it survives a fresh build.** Every other wheel finding
> can be argued away with *"CI builds the wheel in the same job"*. This one cannot — CI builds
> exactly this wheel and the gate exits 0. `tests/test_release_checks.py:520` asserts only the
> PASS case against a fixture whose entry-point string is hard-coded correct at `:284`. The
> wrong-function case is not in the fixture set — **fixture monoculture in the sense CLAUDE.md
> names.**

## G26. Both README checks report **PASS** when they verified nothing

This is the direct answer to the brief's sharpest question — *is there any path where a check
skips and the summary still reports exit 0?* **Yes, and the check does not even skip. It
passes.** `check_readme_pip_install` returns `ok(...)` when `targets == []` (`:1265`);
`check_readme_commands` returns `ok(...)` when `commands == []` (`:1298`). Both join the passed
count and neither reaches `if skips: return 2`.

Combined with the frozen contract's rule 1 (four-space indented blocks are not recognised), a
README wrong in *both* the ways these checks exist to catch goes green:

```
[PASS   ] readme-pip-install: no `pip install <name>` line in README.md to get wrong
[PASS   ] readme-commands: README.md shows no `migkit <subcommand>` invocation
            nothing to verify; when Phase 4 writes the real README this check
            starts asserting every command it shows
16 passed, 0 failed, 0 flagged, 0 skipped, 16 checks total
Every check ran and passed.
```

**The evidence says "nothing to verify" and the summary says "Every check ran and passed" — in
the same run**, over a README telling the reader to install a distribution that is not this
project and to type two subcommands that do not exist.

Two further stings: that PASS text asserts a project state (*"when Phase 4 writes the real
README"*) that stopped being true several phases ago; and the frozen contract records that on
2026-08-13 this check returned `[]` against the real README — **it passed by vacuity for the
whole of its early life**, with nothing distinguishing that from a verified pass.

> **CONFIRMED.** This inverts the script's own design rule 1: an unperformable check counted as
> a performed one.

## G27. A typo in `[project.optional-dependencies]` licenses the same typo in the README

`readme_pip_install_targets`' docstring names the exact defect it exists to stop — *"the exact
thing the sibling got wrong when a rename turned `opik-rigor` into `opik-opik_rigor` in the
published install hint"*. But `allowed` is built from the dist name **plus every extras name**,
while `split_requires_dist` removes every `extra == '…'` requirement before the dependency
checks see anything. **An extras entry is checked by nothing and simultaneously widens the
README allowlist.**

```
[PASS   ] readme-pip-install: all 2 pip-install target(s) name a real distribution
            pip install opik-rigorr  ->  normalises to opik-rigorr  ->  ok
16 passed ... EXIT=0
```

`opik-rigorr` exists on no index; `pip install model-migration-kit[anthropic]` fails for every
user. **6 of the 10 requirements this project declares are extras** — all outside every
dependency assertion, all inside the README allowlist.

## G28. `--no-build` adopts any `dist/` without checking its provenance

Design rule 3 says *"The wheel is the subject."* Under `--no-build`, `check_build` globs `dist/`
and returns `ok(...)` with evidence `source tree: <repo>` — **an assertion of provenance that is
never verified.** The gate's own instance of this project's standing rule.

A wheel built from a poisoned `cli.py`, with the tree then restored and `git status` clean:

```
all 16 [PASS], EXIT=0
[PASS   ] readme-commands: all 4 README command(s) exist in the CLI
```

That wheel raises `ImportError: POISON…` on import. Note the `readme-commands` line: it
introspected the **tree**, while the wheel it is gating on cannot be imported at all. **The only
wheel code the gate ever executes is `__init__.py`**, via the resource probe. Artifacts copied
entirely outside the repo pass the same way.

> **CONFIRMED; severity WEAKENED in the current CI topology** — `publish.yml` builds seconds
> earlier from the same checkout. Real exposure: the documented local invocation, the
> `workflow_dispatch` → TestPyPI path, and any future split of build and verify, which the
> `upload-artifact`/`download-artifact` structure already does downstream.

## G29. `version-coherence` claims exhaustiveness it does not have

Docstring: *"**Every place the version is written** agrees."* `grep -n -i "changelog\|git tag"`
over the script → **no matches.** With the CHANGELOG's top entry edited to `0.2.0`:

```
[PASS   ] version-coherence: all 4 version sources say 0.1.1
16 passed ... EXIT=0
```

The published 0.1.1 sdist would ship a CHANGELOG announcing 0.2.0. And the tag guard lives in
`publish.yml`'s `pypi` job under `if: github.event_name == 'release'` — **on a manual dispatch,
nothing anywhere compares the version to a tag.**

> **CONFIRMED as a false claim of exhaustiveness.** The sound counterpart: a genuinely stale
> wheel *is* caught, `FAILED version-coherence: ['0.1.1', '0.1.2']`, exit 1.

## G30. `version-matches-installed`'s FAIL branch is unreachable in CI

`elsewhere` is computed from `Distribution._path`, which is **always the `.dist-info` directory
in site-packages** — never the source tree, editable or not. So it tests "is site-packages
inside the repo", not "is this a different checkout". In `publish.yml` site-packages is never
inside the repo, so `elsewhere` is permanently true and `bad(...)` is unreachable. Observed with
a real mismatch on a same-tree editable install:

```
[SKIPPED] version-matches-installed: the installed distribution is a different tree,
          so a mismatch here proves nothing
```

The installed distribution **is** this tree. The stated reason is false.

> **CONFIRMED as degraded, REFUTED as a false-green** — it reduces to PASS or SKIP, and SKIP
> exits 2. What a maintainer wrongly believes is that this row would report a mismatch as a
> failure; in CI it reports an unrelated excuse.

## G31. Smaller, confirmed

- **The skip cascade is legible in the rows and flat in the summary.** 14 skips, **2 root
  causes**, interleaved with no grouping. Worse: `twine-check` skips with *"no wheel was
  produced"*, so `check_twine`'s own message naming the missing tool **never prints** — the
  operator installs `build`, re-runs, and only then discovers `twine` is also absent.
- **`license-metadata`: an absent tree licence file silently drops the wheel↔tree comparison
  inside a PASS.** `if on_disk.is_file() and …` — a *changed* licence is caught, an *absent* one
  is not compared, and nothing says the comparison could not be made.
- **`command_segments` does not implement the frozen contract it is the implementation of.**
  Contract rule 2 step 3 mandates discarding segments beginning inside a quoted string, with the
  worked example `echo "a && migkit demo"`. There is no quote tracking, and the docstring
  asserts the contrary — *"a false split can only lose a match, never invent one"* — which the
  contract's own counterexample falsifies. **REFUTED as a false-green** (it makes the gate
  *stricter*, so the failure mode is a spurious FAIL); reported because the frozen contract is
  supposed to be the specification in force and is not, and **both halves of the blind pair
  agreed with each other and against the document.**
- **`_module_available` resolves from the cwd.** Any directory named `build/` — the commonest
  build-artifact name in Python — is importable as a namespace package, turning an honest
  `SKIPPED … pip install build twine` into a confusing `FAIL`, **after deleting the two real
  artifacts in `dist/`**. Loud, so ranked low; included as a clean instance of *"can it be
  satisfied from outside the tree?"* — yes, from the cwd, which the gate never inspected.

## G32. Sound, and what was tried

**The exit-code contract: verified by construction for all four states.** PASS→0, FAIL→1,
FLAG→1, SKIP→2. `--allow-dev-version` downgrades FAIL to **SKIP**, not PASS, and cannot produce
green. **No argument combination turned FAIL or FLAG into exit 0.** My earlier suspicion is
dropped a second time.

**`wheel-demo-data` and `wheel-demo-data-importable` are the two best checks in the file.** I ran
the attack `-S -E` exists to stop — stripped the demo files out of the wheel, then ran the gate
with `PYTHONPATH` pointing at a complete source tree that has them. Both failed loudly, the
`__path__` assertion reported exactly one entry, and the `-1`-vs-size encoding keeps
*unreachable*, *empty* and *measured* distinguishable. **I could not get past either.**

**`readme-commands`' wrong-tree guard is sound** — run from a worktree while the editable install
points elsewhere, it *refuses to answer*: *"verifying against the wrong tree would be a claim
about someone else's code."* That is exactly the `check_contract.py` defect class, and this
check is explicitly immune. Contrast G30, where the same idea implemented against `_path` fails.

**`contract-dependency-clause` parses rather than copies** — editing the criterion-7 sentence
flags immediately, exit 1, with text naming the amendment that would clear it.

**Exclusions, measured.** Rule 1's premise (*"this project's README does not use indented
blocks"*) is **true today — 0 of 78 relevant lines** — but it is a premise about the README, not
about markdown. `DEMO_DATA` covers 3 of 6 data files, and the uncovered 86% by size is showcase
data with **0 user-facing accessors**, verified rather than trusted. The 30-char comment-label
cap costs **0** today (the one label is 8 chars) but the 31st character *silently deletes* a
command from the scan. The extras exclusion is right for the runtime claim and **unjustified for
the README allowlist** (G27).

## G33. Two disclosures, per CLAUDE.md

**The environment changed mid-audit.** `build` appeared in the project venv at 22:25 while
everything else in that `site-packages` is timestamped 20:28. Another agent on this shared
machine ran `pip install build`. The brief's premise "13 of 15 skip" is no longer reproducible
against `.venv`; the cascade was reproduced deterministically in a throwaway venv instead, and
all cascade output above comes from there.

**One rule violation of my own.** Restoring `LICENSE`/`NOTICE` after the licence construction,
the agent used `git checkout --` rather than a byte-verified backup — those two were the only
files not backed up in advance. It verified the restore against the untouched main checkout by
SHA-256 instead (both match). Reported rather than routed around, which is what CLAUDE.md asks
for.
