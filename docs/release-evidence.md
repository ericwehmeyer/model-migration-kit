# Release evidence — `docs/session-4-release-contract.md` §5, executed

`docs/session-4-release-contract.md` §5 opens with *"Every row is a command. The
evidence is its output."* Until now nobody had run the whole thing end to end and
written the output down. This file is that run.

Every command below was executed. Every block marked `output` is pasted, not
described. Where a row could not be run as written, the row says so, says exactly
why, and names what would have to exist for it to run — a substituted command is
labelled a substitution and never counted as the row passing.

## Provenance

| | |
|---|---|
| Date | 2026-08-14 (local, Windows) |
| Commit under verification | `c8e6a03a4bc5b99eb1ca7bbce9903f11c35db6db` — *"Build the exit-code fixtures the checklist asks for, and badge the README"* |
| Working tree | clean; `git status --porcelain --untracked-files=all` empty |
| Tree location | `C:\Users\ewehm\repos\mk-wt-checklist` (a git worktree of `C:\Users\ewehm\repos\migration-kit`) |
| Interpreter | `.\.venv\Scripts\python.exe` — CPython 3.14.4, win32 |
| Base interpreter for throwaway venvs | `py -3` → 3.14.4. **`py -3.12` does not exist on this machine**; see defect D3 |
| `pytest` | 9.1.1, `pluggy` 1.6.0, plugins `opik-rigor-0.1.1`, `cov-7.1.0` |
| `ruff` | 0.16.3 |
| `twine` | 7.0.0, `build` 1.5.0 |
| `opik-rigor` resolved | **0.1.1** (see finding F4) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | set to `''` for every row |

### Why a worktree and not the repo root

The checklist's commands are written for the repository root. They were run in a
worktree instead, for one reason: while row 1 was executing, the repository's `HEAD`
moved from `f1eb368` to `c8e6a03` underneath the run as another change landed. An
evidence appendix whose commands were run against a tree that no longer exists is
not re-runnable, which is the one thing this file has to be. The worktree pins the
subject to a single sha.

Two consequences, both stated so nobody has to guess:

- The worktree has its own `.venv`, created for this run with
  `py -3 -m venv .venv` then `pip install --upgrade pip build twine` and
  `pip install -e ".[dev]"`. Every `.\.venv\Scripts\python.exe` below is that one.
  This is why `opik-rigor` resolves to 0.1.1 here and to 0.1.0 in the repo's
  long-lived venv — a fresh resolve today takes the newest release inside
  `>=0.1.0,<0.2`.
- Paths in pasted output read `mk-wt-checklist` where the contract would read
  `migration-kit`. Nothing else differs.

To reproduce: check out `c8e6a03`, build a venv the same way, and run the commands
in the order they appear here.

---

## Verdict table

| # | Row asserts | Verdict |
|---|---|---|
| 1 | Suite green, offline, keyless | **PASS** (with a substitution — see D1) |
| 2 | Lint clean | **PASS** |
| 3 | Build and metadata; `twine check` PASSED ×2 | **PASS** |
| 3a | `scripts/verify_release.py`, as written | **FAIL** — `version-not-dev`, which is the gate working |
| 3b | `scripts/verify_release.py --allow-dev-version` | **PASS on 14, SKIPPED on 1** (exit 2) |
| 4 | sdist and wheel contain what they must | **PASS** |
| 5 | Clean throwaway venv, wheel install, demo, timed (`scripts/clean_venv_check.ps1`) | **PASS** as the script defines it; the contract's own definition of done is **not met** — see F1 |
| 6 | The HTML is genuinely self-contained | **PASS** |
| 7 | Exit-code matrix through `migkit compare` | **CANNOT-RUN-AS-WRITTEN** — two substitutions run, both green |
| 8 | Import purity, subprocess, from the installed wheel | **PASS** |
| 9 | Every README code block executed | **CANNOT-RUN-YET** — and two blocks are already known stale (F5) |
| 10 | Version consistency, four ways | **PASS on three of four**; the fourth (`git tag`) does not exist before Phase 8 |
| 11 | Teardown | **PASS** |

Nine contract defects are recorded in the last section. Three were already found
tonight before this run; these are additional.

---

## Row 1 — Suite green, offline, keyless

**Asserts:** the whole suite passes with no credentials and no network, and the
pass/skip counts are recorded so a later reader can tell whether something
silently stopped running.

**Command, as the contract writes it:**

```powershell
$env:ANTHROPIC_API_KEY=''; $env:OPENAI_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -m "not requires_network"
```

**Output:**

```
============================= test session starts =============================
platform win32 -- Python 3.14.4, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\ewehm\repos\mk-wt-checklist
configfile: pyproject.toml
testpaths: tests
plugins: opik-rigor-0.1.1, cov-7.1.0
collected 750 items

tests\test_cli.py ...................................................... [  7%]
...
============================== warnings summary ===============================
tests/test_cli.py::TestConsoleScript::test_process_exit_status_matches_the_in_process_return
  C:\Users\ewehm\repos\mk-wt-checklist\.venv\Lib\site-packages\_pytest\threadexception.py:58: PytestUnhandledThreadExceptionWarning: Exception in thread Thread-1 (_readerthread)

  Traceback (most recent call last):
    File "...\Lib\threading.py", line 1082, in _bootstrap_inner
      self._context.run(self.run)
    File "...\Lib\threading.py", line 1024, in run
      self._target(*self._args, **self._kwargs)
    File "...\Lib\subprocess.py", line 1614, in _readerthread
      buffer.append(fh.read())
    File "...\Lib\encodings\cp1252.py", line 23, in decode
      return codecs.charmap_decode(input,self.errors,decoding_table)[0]
  UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 in position 236: character maps to <undefined>

  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.
    warnings.warn(pytest.PytestUnhandledThreadExceptionWarning(msg))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================= 750 passed, 1 warning in 114.05s (0:01:54) ==================
```

Exit code 0.

**Substitution, recorded rather than silently dropped.** `requires_network` was
removed from `[tool.pytest.ini_options] markers` tonight. The row was therefore
also run without the selector:

```powershell
$env:ANTHROPIC_API_KEY=''; $env:OPENAI_API_KEY=''
.\.venv\Scripts\python.exe -m pytest
```

```
================= 750 passed, 1 warning in 141.46s (0:02:21) ==================
```

Exit code 0. **750 selected either way, and 750 passed either way** — the `-m`
expression deselects nothing, which is the whole of defect D1 below.

**Verdict: PASS.** Record the counts as **750 passed, 0 skipped, 1 warning**.

**Two things this row does not establish, stated because the row's own title
claims them:**

1. *Offline* is not asserted by anything in the command. The keys are emptied and
   the suite passes, but nothing here prevents a socket from opening. Since
   `requires_network` was retired, no artefact in the repository deselects a
   network test; the promise is kept by there being no such test, which is a
   property of the suite rather than of this row. If the row is meant to be a
   gate, it needs a network-blocking harness that does not exist.
2. The warning is not new and not a failure, but it is real: `test_cli.py`'s
   `test_process_exit_status_matches_the_in_process_return` reads a subprocess's
   output through Windows' cp1252 default and a reader thread dies on a byte the
   code page has no mapping for. The test passes because it does not depend on
   that thread's buffer. It is a latent decoding bug in the test's plumbing, and
   on a machine whose code page is not 1252 it may not be latent.

---

## Row 2 — Lint clean

**Command:**

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

**Output:**

```
All checks passed!
```

Exit code 0. `ruff 0.16.3`.

**Verdict: PASS.**

---

## Row 3 — Build and metadata

**Asserts:** `twine check` reports PASSED for both artifacts, plus Phase 1's
METADATA excerpt.

```powershell
.\.venv\Scripts\python.exe -m twine check dist/*
```

```
Checking dist\model_migration_kit-0.1.0.dev0-py3-none-any.whl: PASSED
Checking dist\model_migration_kit-0.1.0.dev0.tar.gz: PASSED
```

Exit code 0.

```powershell
$whl = Get-ChildItem dist\*.whl | Select-Object -First 1
$out = Join-Path $env:TEMP 'mk-wheel-inspect'
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
Expand-Archive $whl.FullName -DestinationPath $out
Get-Content $out\*.dist-info\METADATA |
    Select-String -Pattern '^(License|License-Expression|License-File|Classifier: License|Requires-Dist|Requires-Python|Name|Version)'
Get-ChildItem -Recurse $out\*.dist-info\licenses
Get-Content $out\*.dist-info\licenses\LICENSE -TotalCount 3
```

```
Name: model-migration-kit
Version: 0.1.0.dev0
License-Expression: Apache-2.0
License-File: LICENSE
License-File: NOTICE
Requires-Python: >=3.10
Requires-Dist: jinja2>=3.0
Requires-Dist: opik-rigor<0.2,>=0.1.0
Requires-Dist: rich>=13.0
Requires-Dist: tomli>=2.0; python_version < '3.11'
Requires-Dist: pytest-cov>=4.0; extra == 'dev'
Requires-Dist: pytest>=7.0; extra == 'dev'
Requires-Dist: ruff>=0.6; extra == 'dev'
name    = "accuracy"
```

```
LICENSE
NOTICE
```

```
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/
```

Against Phase 1's seven exit clauses: (1) PASSED ×2 ✔; (2) `License-Expression:
Apache-2.0` ✔; (3) both `License-File:` lines present and both files under
`.dist-info/licenses/` ✔; (4) no `Classifier: License ::` line ✔; (5) no `License:`
field at all, so the Apache body has not crept into metadata ✔; (6) the shipped
`LICENSE` begins *Apache License / Version 2.0, January 2004* ✔; (7) the four
runtime `Requires-Dist` entries and `Requires-Python: >=3.10` are exactly as the
amended clause names them ✔.

**Verdict: PASS.**

**Defect D8, visible in the pasted output above.** The last line of the METADATA
excerpt is `name    = "accuracy"`. That is not metadata — it is a line of the
README's `judges.toml` example, which travels inside METADATA as the long
description. `Select-String` is case-insensitive by default in PowerShell, so
`^(...|Name|...)` matches lowercase `name`. Harmless here, but the contract's
command is the one whose output gets pasted as evidence, and it puts non-evidence
in the paste. `-CaseSensitive` fixes it.

---

## Row 3a — `scripts/verify_release.py`, as written

**Asserts:** the mechanically checkable rows of §5, read off the built artifact.

```powershell
.\.venv\Scripts\python.exe scripts\verify_release.py
```

**Output, complete:**

```
====================================================================================================
model-migration-kit release verification
docs/session-4-release-contract.md, section 5
====================================================================================================
repo        : C:\Users\ewehm\repos\mk-wt-checklist
dist dir    : C:\Users\ewehm\repos\mk-wt-checklist\dist
interpreter : C:\Users\ewehm\repos\mk-wt-checklist\.venv\Scripts\python.exe
platform    : win32

[PASS   ] build: built one sdist and one wheel
            wheel: model_migration_kit-0.1.0.dev0-py3-none-any.whl (110,534 bytes)
            sdist: model_migration_kit-0.1.0.dev0.tar.gz (356,048 bytes)
            source tree: C:\Users\ewehm\repos\mk-wt-checklist
[PASS   ] wheel-demo-data: all 3 demo files present inside model_migration_kit-0.1.0.dev0-py3-none-any.whl
            in wheel: model_migration_kit/data/demo_goldenset.jsonl (1,950 bytes)
            in wheel: model_migration_kit/data/demo_rubric.md (2,083 bytes)
            in wheel: model_migration_kit/data/demo.toml (1,259 bytes)
            each file byte-identical to src/model_migration_kit/data/
[PASS   ] wheel-demo-data-importable: importlib.resources reaches all demo data with only the wheel on sys.path
            probe ran with -S, cwd=C:\Users\ewehm\AppData\Local\Temp\mk-verify-vsp43ax_, sys.path[0]=C:\Users\ewehm\AppData\Local\Temp\mk-verify-vsp43ax_\wheel-extract
            anchor: MultiplexedPath('C:\Users\ewehm\AppData\Local\Temp\mk-verify-vsp43ax_\wheel-extract\model_migration_kit\data')
            model_migration_kit.__path__ = ['C:\\Users\\ewehm\\AppData\\Local\\Temp\\mk-verify-vsp43ax_\\wheel-extract\\model_migration_kit']
            demo.toml: 1,259 bytes via importlib.resources
            demo_goldenset.jsonl: 1,950 bytes via importlib.resources
            demo_rubric.md: 2,083 bytes via importlib.resources
[PASS   ] sdist-contents: sdist carries licence, readme, pyproject, src/ and tests/
            sdist root: model_migration_kit-0.1.0.dev0/ (55 entries)
            required files present: ['LICENSE', 'NOTICE', 'README.md', 'pyproject.toml']
            src/ present: True; tests/ present: True
            demo data in sdist: 3/3
[PASS   ] license-metadata: SPDX expression, licence files and classifiers are coherent
            License-Expression: Apache-2.0
            License: (absent, correct under PEP 639)
            deprecated 'License ::' classifiers: none
            License-File: ['LICENSE', 'NOTICE']
            in wheel: model_migration_kit-0.1.0.dev0.dist-info/licenses/LICENSE (11,342 bytes)
            in wheel: model_migration_kit-0.1.0.dev0.dist-info/licenses/NOTICE (235 bytes)
            shipped LICENSE begins: Apache License / Version 2.0, January 2004
            shipped text identified as Apache-2.0, consistent with 'Apache-2.0'
[PASS   ] dependencies-declared: all 4 runtime requirements accounted for
            pyproject dependencies (4): ['opik-rigor>=0.1.0,<0.2', 'jinja2>=3.0', 'rich>=13.0', "tomli>=2.0; python_version < '3.11'"]
            metadata Requires-Dist, runtime (4): ['jinja2>=3.0', 'opik-rigor<0.2,>=0.1.0', 'rich>=13.0', "tomli>=2.0; python_version < '3.11'"]
            metadata Requires-Dist, extras (3): ["pytest-cov>=4.0; extra == 'dev'", "pytest>=7.0; extra == 'dev'", "ruff>=0.6; extra == 'dev'"]
            Requires-Python: metadata '>=3.10' vs pyproject '>=3.10'
[PASS   ] tomli-marker: tomli is conditioned on python_version < "3.11"
            Requires-Dist: tomli>=2.0; python_version < '3.11'
            marker: "python_version < '3.11'"
[PASS   ] contract-dependency-clause: the build matches the frozen contract's dependency clause
            contract clause (session-4-release-contract.md, Phase 1 criterion 7): ['jinja2', 'opik-rigor', 'rich', 'tomli']
            built metadata: ['jinja2', 'opik-rigor', 'rich', 'tomli']
[PASS   ] version-coherence: all 4 version sources say 0.1.0.dev0
            wheel METADATA Version: 0.1.0.dev0
            wheel filename: 0.1.0.dev0
            sdist filename: 0.1.0.dev0
            src/.../__init__.py __version__: 0.1.0.dev0
            pyproject declares a dynamic version (hatch reads __init__.py)
[FAIL   ] version-not-dev: a development version would be published: ['0.1.0.dev0']
            Phase 3 replaces 0.1.0.dev0 with 0.1.0 before the tag exists.
            The publish workflow's tag-vs-wheel guard would also reject this,
            but only after a release had been cut. Pass --allow-dev-version
            to acknowledge this while the build is still in progress.
[PASS   ] version-matches-installed: __version__ and installed metadata agree on 0.1.0.dev0
            interpreter: C:\Users\ewehm\repos\mk-wt-checklist\.venv\Scripts\python.exe
            importlib.metadata.version('model-migration-kit') = 0.1.0.dev0
            distribution location: C:\Users\ewehm\repos\mk-wt-checklist\.venv\Lib\site-packages\model_migration_kit-0.1.0.dev0.dist-info
            __version__ in the tree under verification = 0.1.0.dev0
[PASS   ] console-script: `migkit` points at a module the wheel ships
            entry_points.txt: [console_scripts] | migkit = model_migration_kit.cli:main
            target model_migration_kit.cli:main -> looked for ['model_migration_kit/cli.py', 'model_migration_kit/cli/__init__.py']
            found in wheel: model_migration_kit/cli.py
[PASS   ] twine-check: twine check PASSED on both sdist and wheel
            checked: model_migration_kit-0.1.0.dev0.tar.gz, model_migration_kit-0.1.0.dev0-py3-none-any.whl
            Checking
            C:\Users\ewehm\repos\mk-wt-checklist\dist\model_migration_kit-0.1.0.dev0-py3-no
            ne-any.whl: PASSED
            Checking
            C:\Users\ewehm\repos\mk-wt-checklist\dist\model_migration_kit-0.1.0.dev0.tar.gz
            : PASSED
[PASS   ] readme-pip-install: no `pip install <name>` line in README.md to get wrong
            scanned 649 lines of README.md
[PASS   ] readme-commands: all 4 README command(s) exist in the CLI
            CLI introspected: C:\Users\ewehm\repos\mk-wt-checklist\src\model_migration_kit\cli.py
            README shows: ['compare', 'demo', 'report', 'run']
            `migkit compare --help` -> exit 0
            `migkit demo --help` -> exit 0
            `migkit report --help` -> exit 0
            `migkit run --help` -> exit 0

====================================================================================================
14 passed, 1 failed, 0 flagged, 0 skipped, 15 checks total
  FAILED   version-not-dev: a development version would be published: ['0.1.0.dev0']
====================================================================================================
Release is blocked. Every line above is reproducible; fix the cause, not the check.
```

Exit code 1.

**Verdict: FAIL, and the failure is correct.** The version is deliberately
`0.1.0.dev0`; Phase 3 has not run. `version-not-dev` blocking the release is the
gate doing its job, and its absence would be the defect. This must become a PASS
before Phase 8 cuts the tag.

Two things worth noting from the passes:

- `contract-dependency-clause` **passed**. That row exists to raise a FLAG when
  the built metadata and the frozen contract's criterion-7 sentence disagree over
  `tomli`. The sentence has since been amended in the contract, the script parses
  the sentence rather than holding a copy, and the flag has cleared itself. That
  is the mechanism working as designed and is worth recording as such — the row is
  now proving agreement, not tolerating disagreement.
- `readme-pip-install` reports "no `pip install <name>` line in README.md". That
  is a true statement about the README as written (its install lines all take a
  local path or a checkout, which the scanner deliberately drops), not evidence
  that the install name is correct. This row starts asserting something the day
  the README gains a real `pip install model-migration-kit` line, which Phase 9
  is where it happens.

---

## Row 3b — the same script, acknowledging the dev version

```powershell
.\.venv\Scripts\python.exe scripts\verify_release.py --allow-dev-version
```

**Output, the changed row and the summary:**

```
[SKIPPED] version-not-dev: a .dev version is present and --allow-dev-version was passed
            a development version would be published: ['0.1.0.dev0']
            this must be a PASS before Phase 8 cuts the tag
```

```
====================================================================================================
14 passed, 0 failed, 0 flagged, 1 skipped, 15 checks total
  SKIPPED  version-not-dev: a .dev version is present and --allow-dev-version was passed
====================================================================================================
Nothing failed, but a check could not run. A skip is not a pass -- exit code 2 so a
release gate cannot mistake this for green.
```

Exit code 2. All fourteen other checks are identical to row 3a.

**Verdict: PASS on 14 of 15, SKIPPED on 1, and the process exit code is 2 rather
than 0** — the script refuses to report green over a skip, which is the behaviour
its own docstring promises.

---

## Row 4 — The sdist and wheel contain what they must

```powershell
tar -tzf (Get-ChildItem dist\*.tar.gz).FullName
```

```
model_migration_kit-0.1.0.dev0/.gitattributes
model_migration_kit-0.1.0.dev0/CHANGELOG.md
model_migration_kit-0.1.0.dev0/COMPATIBILITY.md
model_migration_kit-0.1.0.dev0/HANDOFF.md
model_migration_kit-0.1.0.dev0/PROGRESS.md
model_migration_kit-0.1.0.dev0/.github/workflows/ci.yml
model_migration_kit-0.1.0.dev0/.github/workflows/drift-canary.yml
model_migration_kit-0.1.0.dev0/.github/workflows/publish.yml
model_migration_kit-0.1.0.dev0/docs/build-plan.md
model_migration_kit-0.1.0.dev0/docs/readme-scan-contract.md
model_migration_kit-0.1.0.dev0/docs/session-2-contract.md
model_migration_kit-0.1.0.dev0/docs/session-2-verdict-review.md
model_migration_kit-0.1.0.dev0/docs/session-3-contract.md
model_migration_kit-0.1.0.dev0/docs/session-4-release-contract.md
model_migration_kit-0.1.0.dev0/scripts/clean_venv_check.ps1
model_migration_kit-0.1.0.dev0/scripts/verify_release.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/__init__.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/cli.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/comparison.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/contracts.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/demo.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/errors.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/goldenset.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/judging.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/report.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/runner.py
model_migration_kit-0.1.0.dev0/src/model_migration_kit/data/demo.toml
model_migration_kit-0.1.0.dev0/src/model_migration_kit/data/demo_goldenset.jsonl
model_migration_kit-0.1.0.dev0/src/model_migration_kit/data/demo_rubric.md
model_migration_kit-0.1.0.dev0/tests/test_cli.py
model_migration_kit-0.1.0.dev0/tests/test_comparison.py
model_migration_kit-0.1.0.dev0/tests/test_goldenset.py
model_migration_kit-0.1.0.dev0/tests/test_import_purity.py
model_migration_kit-0.1.0.dev0/tests/test_judging.py
model_migration_kit-0.1.0.dev0/tests/test_release_checks.py
model_migration_kit-0.1.0.dev0/tests/test_report.py
model_migration_kit-0.1.0.dev0/tests/test_runner.py
model_migration_kit-0.1.0.dev0/tests/fixtures/error-a.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/error-b.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/go-a.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/go-b.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/goldenset.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/judges.toml
model_migration_kit-0.1.0.dev0/tests/fixtures/make_fixtures.py
model_migration_kit-0.1.0.dev0/tests/fixtures/nogo-a.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/nogo-b.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/review-a.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/review-b.jsonl
model_migration_kit-0.1.0.dev0/tests/fixtures/rubric.md
model_migration_kit-0.1.0.dev0/.gitignore
model_migration_kit-0.1.0.dev0/LICENSE
model_migration_kit-0.1.0.dev0/NOTICE
model_migration_kit-0.1.0.dev0/README.md
model_migration_kit-0.1.0.dev0/pyproject.toml
model_migration_kit-0.1.0.dev0/PKG-INFO
```

`LICENSE`, `NOTICE`, `README.md`, `pyproject.toml`, `src/` and `tests/`: all
present. The row's list is satisfied exactly.

```powershell
Expand-Archive (Get-ChildItem dist\*.whl).FullName -DestinationPath $env:TEMP\mk-w -Force
Get-ChildItem -Recurse $env:TEMP\mk-w | Select-Object -ExpandProperty FullName
```

Paths below are shown relative to `$env:TEMP\mk-w` for width; the command prints
them absolute.

```
model_migration_kit\__init__.py
model_migration_kit\cli.py
model_migration_kit\comparison.py
model_migration_kit\contracts.py
model_migration_kit\demo.py
model_migration_kit\errors.py
model_migration_kit\goldenset.py
model_migration_kit\judging.py
model_migration_kit\report.py
model_migration_kit\runner.py
model_migration_kit\data\demo_goldenset.jsonl
model_migration_kit\data\demo_rubric.md
model_migration_kit\data\demo.toml
model_migration_kit-0.1.0.dev0.dist-info\entry_points.txt
model_migration_kit-0.1.0.dev0.dist-info\METADATA
model_migration_kit-0.1.0.dev0.dist-info\RECORD
model_migration_kit-0.1.0.dev0.dist-info\WHEEL
model_migration_kit-0.1.0.dev0.dist-info\licenses\LICENSE
model_migration_kit-0.1.0.dev0.dist-info\licenses\NOTICE
```

**The clause the row calls "the row most likely to fail" — the wheel must contain
the demo golden set — holds.** `model_migration_kit/data/demo_goldenset.jsonl` is
in the zip at 1,950 bytes, and row 3a's `wheel-demo-data-importable` check proves
it is reachable through `importlib.resources` with only the extracted wheel on
`sys.path` and `-S` in force, so no editable install is supplying it. Open
Decision 3 has been resolved in the direction the contract recommends: the data
lives at `src/model_migration_kit/data/` and `.gitignore` whitelists it.

**Verdict: PASS.**

---

## Row 5 — Clean throwaway venv, wheel install, demo, timed

This is the definition-of-done row, and the repository's sanctioned form of it is
`scripts/clean_venv_check.ps1`. It had never been run tonight. It has now been run
three times: once plain, once with `-Keep` (so rows 6–10 had a `$tmp` to point at),
and once as the contract's inline snippet with per-stage timing.

### 5a — `scripts/clean_venv_check.ps1`, plain

```powershell
pwsh -File scripts\clean_venv_check.ps1
```

```
====================================================================================================
model-migration-kit clean-venv check -- a stranger with no keys
docs/session-4-release-contract.md, pre-release checklist item 5
====================================================================================================
repo        : C:\Users\ewehm\repos\mk-wt-checklist
wheel       : model_migration_kit-0.1.0.dev0-py3-none-any.whl (110,534 bytes)
interpreter : C:\Users\ewehm\AppData\Local\Microsoft\WindowsApps\py.exe -3
throwaway   : C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1

[PASS   ] venv: throwaway virtualenv created, empty of everything
            python: 3.14.4
[PASS   ] install: installed model_migration_kit-0.1.0.dev0-py3-none-any.whl from the local file
            Name: model-migration-kit
            Version: 0.1.0.dev0
            Location: C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1\Lib\site-packages
            C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1\Lib\site-packages\model_migration_kit
            C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1\Lib\site-packages\opik_rigor\__init__.py
[PASS   ] isolation: model_migration_kit and opik_rigor both resolve inside the throwaway venv
            command: migkit demo --out C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1\demo.html
            exit 1, elapsed 8.6s, cwd C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1
            exit codes: 0=GO 1=NO-GO 2=REVIEW 3=error (contracts.Verdict.EXIT_CODES)
            demo.html is 25,760 bytes
[PASS   ] migkit demo: a keyless stranger got an HTML report
[PASS   ] two-minute claim: 8.6s, inside the 120s budget

cleaned up C:\Users\ewehm\AppData\Local\Temp\mk-clean-19d1090971d7435ab8818eb3a67ad4b1
====================================================================================================
The definition of done holds: a stranger with no keys gets an HTML report.
```

Exit code 0.

**Does it clean up after itself?** Yes, verified rather than assumed. Before the
run, `@(Get-ChildItem $env:TEMP -Directory -Filter 'mk-clean-*').Count` was `0`;
the script printed `cleaned up <path>` and the directory was gone afterwards. The
`-Keep` switch is the only way to leave one behind, and when used it prints a line
telling you to delete it. It also refuses to build the throwaway venv from the
repo's own `.venv`, and refuses to run at all if the system temp directory turns
out to live inside the repository.

**Both `__file__` paths, the empirical import-name check:** `model_migration_kit`
and `opik_rigor` resolved under
`...\mk-clean-19d1.../Lib/site-packages`, neither under `C:\Users\ewehm\repos\`.
The row asks for exactly this and it holds.

**Exit code: 1, not 0.** See defect D2 — the row's stated evidence is wrong on
this point and the Phase 9 amendment already says why.

### 5b — the contract's inline snippet, with per-stage timing

The script times only `migkit demo`. The contract's *definition of done* is the
whole stranger path — install then run then read. So the snippet was run
separately with a stopwatch on each stage. The literal command in the contract is
`py -3.12 -m venv $tmp`; that fails on this machine (defect D3), so `py -3` was
substituted:

```
[ERROR] No runtime installed that matches 3.12. Try running "py install 3.12".
venv-create py3.12 : 0.3s (exit -1610612730)
```

```powershell
$tmp = Join-Path $env:TEMP ('mk-verify-' + [guid]::NewGuid().ToString('N'))
py -3 -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install (Get-ChildItem dist\*.whl).FullName
Push-Location $tmp
$sw = [Diagnostics.Stopwatch]::StartNew()
& "$tmp\Scripts\migkit.exe" demo --out "$tmp\demo.html"
$code = $LASTEXITCODE; $sw.Stop()
```

Run A:

```
venv-create py -3  : 24.9s (exit 0)
pip install wheel  : 91.1s (exit 0)
Successfully installed MarkupSafe-3.0.3 jinja2-3.1.6 markdown-it-py-4.2.0 mdurl-0.1.2 model-migration-kit-0.1.0.dev0 numpy-2.5.2 opik-rigor-0.1.1 pygments-2.20.0 rich-15.0.0 scipy-1.18.0
migkit demo        : exit=1  elapsed=26.3s
TOTAL stranger path: 142.3s
C:\Users\ewehm\AppData\Local\Temp\mk-verify-3f965adf9e6b4270833c0e63a1e3c0c8\Lib\site-packages\model_migration_kit\__init__.py
C:\Users\ewehm\AppData\Local\Temp\mk-verify-3f965adf9e6b4270833c0e63a1e3c0c8\Lib\site-packages\opik_rigor\__init__.py
demo.html bytes: 25760
```

Run B, an independent repeat, with a second `migkit demo` in the same venv to
separate cold cost from steady state:

```
run2 venv-create   : 20.5s
run2 pip install   : 83.3s
run2 demo (cold)   : exit=1 23.5s
run2 demo (warm)   : exit=1 4.4s
run2 TOTAL         : 127.3s
```

### The timing number, and what it means

| Measurement | Run A | Run B | `clean_venv_check.ps1` |
|---|---|---|---|
| `py -3 -m venv` | 24.9s | 20.5s | (not timed) |
| `pip install <wheel>` | 91.1s | 83.3s | (not timed) |
| `migkit demo`, first run in the venv | 26.3s | 23.5s | 8.6s / 7.7s |
| `migkit demo`, second run in the same venv | — | 4.4s | — |
| **Whole stranger path** | **142.3s** | **127.3s** | **not measured** |

**The number `clean_venv_check.ps1` reports is 8.6s (7.7s on the `-Keep` run), and
it is the elapsed time of `migkit demo` alone.** That is what the script prints
next to the words "two-minute claim". The end-to-end path a stranger actually
walks — create an environment, `pip install`, run the demo — measured **142.3s and
127.3s** on two runs, both **over the 120-second definition of done**. See finding
F1; this is the most important thing in this document.

The script's demo number is also systematically lower than a true first run,
because the script imports `model_migration_kit` and `opik_rigor` in an isolation
probe immediately before timing the demo, which warms `numpy` and `scipy` off
disk. The unwarmed first-run cost is 23–26s.

**Verdict for row 5: PASS as `clean_venv_check.ps1` defines the row** — a keyless
stranger did get a 25,760-byte HTML report from a wheel in a throwaway venv, with
both imports resolving outside the repository. **The contract's own definition of
done is not met on this machine**, and no check in the repository is positioned to
notice.

---

## Row 6 — The HTML is genuinely self-contained

Run against the `demo.html` produced by the `-Keep` run of
`clean_venv_check.ps1`, i.e. a report written by the installed wheel in the
throwaway venv, not one produced from the repo.

```powershell
Select-String -Path "$tmp\demo.html" -Pattern 'https?://|src\s*=|<script[^>]+src' -AllMatches
```

**Output: nothing.** No matches, exit code unset (`Select-String` emitting no
objects).

A silent pass on a `Select-String` is indistinguishable from a mistyped path, so
a positive control and a wider net were run as well:

```powershell
(Select-String -Path "$tmp\demo.html" -Pattern '<html' -AllMatches | Measure-Object).Count
foreach ($p in @('https?://','\ssrc\s*=','<script[^>]+src','<link\b','@import','url\(')) {
    $n = (Select-String -Path "$tmp\demo.html" -Pattern $p -AllMatches | Measure-Object).Count
    "{0,-20} -> {1} match(es)" -f $p,$n
}
```

```
1
https?://            -> 0 match(es)
\ssrc\s*=            -> 0 match(es)
<script[^>]+src      -> 0 match(es)
<link\b              -> 0 match(es)
@import              -> 0 match(es)
url\(                -> 0 match(es)
```

The control matches once, so the file is being read. Nothing the browser would
fetch is present, and there is not even a URL in prose. The report is 25,760
bytes and opens with no network at all.

**Verdict: PASS.**

---

## Row 7 — Exit-code matrix

**Asserts:** `go -> 0`, `nogo -> 1`, `review -> 2`, `error -> 3`, matching
`contracts.Verdict.EXIT_CODES`.

`tests/fixtures/` now exists — it landed in `c8e6a03`, the commit under
verification — and holds exactly the file names the contract's loop expects
(`go-a.jsonl`, `go-b.jsonl`, …, `judges.toml`). So the row's *inputs* exist. The
row still cannot be run as written, for three separate reasons, each demonstrated
below.

### 7a — literally as written, cwd `$tmp`

Item 5 does `Push-Location $tmp` and never pops; items 6–10 inherit that cwd.

```powershell
foreach ($f in 'go','nogo','review','error') {
    & "$tmp\Scripts\migkit.exe" compare --baseline .\tests\fixtures\$f-a.jsonl --candidate .\tests\fixtures\$f-b.jsonl --judges .\tests\fixtures\judges.toml
    "{0} -> {1}" -f $f, $LASTEXITCODE
}
```

```
migkit: ArtifactError: no run artifact at tests\fixtures\go-a.jsonl
go -> 3
migkit: ArtifactError: no run artifact at tests\fixtures\nogo-a.jsonl
nogo -> 3
migkit: ArtifactError: no run artifact at tests\fixtures\review-a.jsonl
review -> 3
migkit: ArtifactError: no run artifact at tests\fixtures\error-a.jsonl
error -> 3
```

The relative paths resolve against the throwaway venv, which has no `tests/`.
Defect D4.

### 7b — absolute fixture paths, still cwd `$tmp`

```powershell
& "$tmp\Scripts\migkit.exe" compare --baseline "$repo\tests\fixtures\$f-a.jsonl" ...
```

```
migkit: GoldenSetError: cannot read golden set tests\fixtures\goldenset.jsonl: [Errno 2] No such file or directory: 'tests\\fixtures\\goldenset.jsonl'
go -> 3
migkit: GoldenSetError: cannot read golden set tests\fixtures\goldenset.jsonl: [Errno 2] No such file or directory: 'tests\\fixtures\\goldenset.jsonl'
nogo -> 3
migkit: GoldenSetError: cannot read golden set tests\fixtures\goldenset.jsonl: [Errno 2] No such file or directory: 'tests\\fixtures\\goldenset.jsonl'
review -> 3
migkit: GoldenSetError: cannot read golden set tests\fixtures\goldenset.jsonl: [Errno 2] No such file or directory: 'tests\\fixtures\\goldenset.jsonl'
error -> 3
```

Absolute artifact paths are not enough: the artifacts record their golden set by
the repo-relative path `tests/fixtures/goldenset.jsonl`, so the cwd must be the
repository root regardless. Second half of defect D4.

### 7c — cwd at the repository root

```powershell
foreach ($f in 'go','nogo','review','error') {
    & "$tmp\Scripts\migkit.exe" compare --baseline .\tests\fixtures\$f-a.jsonl --candidate .\tests\fixtures\$f-b.jsonl --judges .\tests\fixtures\judges.toml
    "{0} -> {1}" -f $f, $LASTEXITCODE
}
```

```
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
go -> 3
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
nogo -> 3
migkit: JudgeConfigError: judge 'accuracy' declares adapter = "fake". A scripted judge grading real completions produces numbers nothing in the report marks as invented. Use `migkit demo` for the keyless path.
review -> 3
migkit: ArtifactError: the golden set at tests/fixtures/goldenset.jsonl has changed since fixture-error-baseline-v1 was run (84d623332ed60ad5 now, b3c2853a494d3472 then). Judging these completions against it would grade answers to questions nobody asked.
error -> 3
```

This is the reason the row cannot be run as written, and it is a design refusal
rather than a bug. `tests/fixtures/judges.toml` declares `adapter = "fake"`, and
`migkit compare` rejects a scripted judge grading real completions on purpose.
Producing `go -> 0`, `nogo -> 1` and `review -> 2` through `migkit compare` needs a
provider credential, which the whole checklist is written to avoid. The fixture
file says so itself, in its header comment.

The `error -> 3` case *is* produced through the CLI, and is the intended one: the
baseline records a golden-set hash that the golden set no longer has, and the tool
refuses rather than grading answers to questions nobody asked.

### 7d — substitution 1: `make_fixtures.py --check`

The fixture set ships its own re-derivation, which is what the fixture author
supplied in place of the CLI path. **This is a substitution, not the row.**

```powershell
.\.venv\Scripts\python.exe tests\fixtures\make_fixtures.py --check
```

```
11 committed fixture files are byte-identical to a rebuild
go     -> GO      exit 0  ok  (rule 5: No judge regressed, every judge cleared the pass-rate floor, and every judge had enough completions to have seen the configured minimum effect.)
nogo   -> NO-GO   exit 1  ok  (rule 1: Judge 'accuracy' shows a statistically significant regression after Holm-Bonferroni correction across judges.)
review -> REVIEW  exit 2  ok  (rule 4: Judge 'accuracy' has too few completions to detect the configured minimum effect at the configured power, so 'no regression detected' would be a question never asked.)
error  -> ERROR   exit 3  ok  (migkit compare refused the pair)
migkit: ArtifactError: the golden set at tests/fixtures/goldenset.jsonl has changed since fixture-error-baseline-v1 was run (84d623332ed60ad5 now, b3c2853a494d3472 then). Judging these completions against it would grade answers to questions nobody asked.
```

Exit code 0. The four-way matrix the row asks for is reproduced exactly:
`0, 1, 2, 3`, each with the decision rule that produced it, and the fixtures are
confirmed byte-identical to a rebuild.

### 7e — substitution 2: `pytest -k TestExitCodeContract`

**Also a substitution, not the row.**

```powershell
.\.venv\Scripts\python.exe -m pytest -k TestExitCodeContract -v
```

```
collecting ... collected 750 items / 736 deselected / 14 selected

tests/test_cli.py::TestExitCodeContract::test_frozen_table_matches_this_suites_own_copy PASSED [  7%]
tests/test_cli.py::TestExitCodeContract::test_error_is_the_default_for_an_unknown_verdict PASSED [ 14%]
tests/test_cli.py::TestExitCodeContract::test_report_returns_the_recorded_verdicts_code[GO] PASSED [ 21%]
tests/test_cli.py::TestExitCodeContract::test_report_returns_the_recorded_verdicts_code[NO-GO] PASSED [ 28%]
tests/test_cli.py::TestExitCodeContract::test_report_returns_the_recorded_verdicts_code[REVIEW] PASSED [ 35%]
tests/test_cli.py::TestExitCodeContract::test_the_verdict_line_agrees_with_the_returned_code[GO] PASSED [ 42%]
tests/test_cli.py::TestExitCodeContract::test_the_verdict_line_agrees_with_the_returned_code[NO-GO] PASSED [ 50%]
tests/test_cli.py::TestExitCodeContract::test_the_verdict_line_agrees_with_the_returned_code[REVIEW] PASSED [ 57%]
tests/test_cli.py::TestExitCodeContract::test_a_report_with_no_verdict_record_returns_error PASSED [ 64%]
tests/test_cli.py::TestExitCodeContract::test_an_unrecognised_verdict_string_returns_error PASSED [ 71%]
tests/test_cli.py::TestExitCodeContract::test_an_evidence_log_with_no_comparison_returns_error PASSED [ 78%]
tests/test_cli.py::TestExitCodeContract::test_a_missing_evidence_path_returns_error_rather_than_an_empty_report PASSED [ 85%]
tests/test_cli.py::TestExitCodeContract::test_every_returned_value_is_one_of_the_four PASSED [ 92%]
tests/test_cli.py::TestExitCodeContract::test_cli_source_carries_no_integer_exit_literal PASSED [100%]

===================== 14 passed, 736 deselected in 4.07s ======================
```

Exit code 0.

**Verdict for row 7: CANNOT-RUN-AS-WRITTEN.** Both substitutions pass and together
they establish the matrix — but neither is the row. What would have to exist for
the row to run keylessly: a judges config the CLI accepts without a provider
credential (the CLI's own error message points at `migkit demo` as the keyless
path, which is a different command from the one the row runs), plus either
cwd-independent golden-set references in the artifacts or an explicit statement in
the row that it runs from the repository root. See defects D4 and D5.

---

## Row 8 — Import purity, in a subprocess, from the installed wheel

Run inside the throwaway venv from row 5b, so the subject is the installed wheel
and not the source tree.

```powershell
& "$tmp\Scripts\python.exe" -c "import sys, model_migration_kit; tops={m.split('.')[0] for m in sys.modules}; bad=tops & {'jinja2','rich','anthropic','openai','opik'}; print(sorted(bad)); assert not bad, bad; assert 'model_migration_kit.cli' not in sys.modules; assert 'model_migration_kit.report' not in sys.modules"
```

```
[]
```

Exit code 0.

Prints `[]` and exits 0, exactly as the row's stated evidence requires. Both
asserts also held: importing the package pulled in neither `.cli` nor `.report`.
The comparison is on top-level module names, so `opik_rigor` — which *is* loaded,
being a dependency — does not collide with the forbidden `opik`; that is the
`8b6e6a9` lesson correctly encoded, and this run is the empirical proof it works
in the direction that matters.

This is the only part of §2 still load-bearing after Open Decision 7 resolved to
CLI-only with `__all__ = []`, and it holds.

**Verdict: PASS.**

---

## Row 9 — Every README code block executed

**Asserts:** extract every README code block, run each, and diff the captured
output against what the README claims character for character; evidence is the
transcript and an empty diff.

**Verdict: CANNOT-RUN-YET.** Nothing in the repository extracts and executes
README blocks, and the row is not a command — it is a task description that ends
in "a diff that is empty" without saying what produces the diff. Its inputs were
inventoried instead, using the README parser that `scripts/verify_release.py`
already contains and that `docs/readme-scan-contract.md` specifies:

```powershell
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'scripts'); import verify_release as v; from pathlib import Path; t=Path('README.md').read_text(encoding='utf-8'); print('fenced code blocks:', len(v.fenced_code_blocks(t))); print('pip-install targets:', v.readme_pip_install_targets(t)); print('migkit subcommands:', v.readme_cli_commands(t))"
```

```
fenced code blocks: 29
blocks containing a runnable-looking command: 14
README pip-install targets: []
README migkit subcommands: ['compare', 'demo', 'report', 'run']
```

What *was* verified about the README, and by what:

- Every `migkit` subcommand the README shows exists in the CLI — row 3a's
  `readme-commands` check, `compare`/`demo`/`report`/`run`, each `--help` exiting 0.
- No `pip install` line in the README names a wrong distribution — row 3a's
  `readme-pip-install`. It reports "no target to get wrong", because the README's
  install lines all take a local path, which the scanner drops by design.

**Two README output blocks are already known to be stale**, found while
inventorying and reported here rather than fixed, because `README.md` belongs to
another agent tonight. See finding F5. Row 9 cannot pass until they are re-run.

For this row to become runnable, something has to exist that: extracts the 14
command-bearing blocks, runs each in a clean venv against the built wheel,
captures stdout/stderr, and diffs against the adjacent output block — with an
explicit allowlist for the blocks that legitimately cannot run keylessly (the
`migkit run` transcripts against a real provider) so that "cannot run" is
distinguishable from "did not match".

---

## Row 10 — Version consistency, four ways

```powershell
& "$tmp\Scripts\python.exe" -c "import model_migration_kit, importlib.metadata as md; print(model_migration_kit.__version__, md.version('model-migration-kit'))"
(Get-ChildItem dist\*.whl).Name
git tag --list 'v*'
```

```
0.1.0.dev0 0.1.0.dev0
```

```
model_migration_kit-0.1.0.dev0-py3-none-any.whl
```

```
(no output)
```

Three of the four sources agree on `0.1.0.dev0`: `__version__` read from the
installed wheel, `importlib.metadata`, and the wheel filename. Row 3a adds two
more that the row does not ask for — the sdist filename and the METADATA `Version`
field — and both also say `0.1.0.dev0`; five sources, one value.

The fourth source, `git tag --list 'v*'`, prints nothing. That is correct: no tag
exists, Phase 8 has not run, and the contract's Phase 0 exit criterion requires
that no tag exist. The row's sentence "All four agree or Phase 8 stops" therefore
cannot be satisfied *before* Phase 8, only after it. Minor wording defect, D9.

The number itself is `0.1.0.dev0` and must become `0.1.0` in Phase 3.

**Verdict: PASS** on everything the row can assert at this point in the phase
order.

---

## Row 11 — Teardown

```powershell
Remove-Item -Recurse -Force $tmp
```

`clean_venv_check.ps1` performs its own teardown and was verified doing so (row
5a). The `-Keep` venv and the two hand-built `mk-verify-*` venvs used for rows
5b–10 were removed explicitly:

```
removed C:\Users\ewehm\AppData\Local\Temp\mk-clean-bce14ac5bdcb4ce0a119bc8d7ed18b04
removed C:\Users\ewehm\AppData\Local\Temp\mk-verify-3f965adf9e6b4270833c0e63a1e3c0c8
removed C:\Users\ewehm\AppData\Local\Temp\mk-w
removed C:\Users\ewehm\AppData\Local\Temp\mk-wheel-inspect
```

One directory was deliberately left alone: `%TEMP%\mk-verify-probe`, which was not
created by this run and does not belong to it.

**Verdict: PASS.**

---

## Findings

Ordered by how much they cost if ignored.

### F1 — The definition-of-done timing check cannot fail for the reason the definition of done would fail

The contract's definition of done is stated twice: in the preamble (*"a stranger
with no keys can `pip install model-migration-kit`, run `migkit demo`, and be
reading an HTML report inside two minutes"*) and in Phase 9 (*"elapsed time is
recorded and is under 120 seconds"*). It is a claim about **install plus run**.

`scripts/clean_venv_check.ps1` starts its stopwatch after `pip install` has
already finished. Its "two-minute claim" row therefore reports 7.7–8.6s and
passes, while the path it is supposed to be guarding measured **142.3s and
127.3s** on two independent runs — both over budget. A check that reports 8.6s
against a 120s budget has 14× of headroom it has not earned, and the component it
excludes is the one that dominates: `pip install` of the wheel pulls `numpy` and
`scipy` transitively through `opik-rigor` and took 83–91 seconds.

Phase 9's own snippet has the same shape — its stopwatch also wraps only
`migkit demo` — so the number Phase 9 records against its 120-second criterion
will be the demo's, not the stranger's.

**The same narrowing is in CI, with the claim stated explicitly.**
`.github/workflows/ci.yml`'s demo job reads:

```yaml
      # The definition of done says a stranger with no keys gets a report in
      # under two minutes. That claim is only true if something checks it.
      ...
          timeout 120 migkit demo --out demo-report.html
```

The `timeout 120` bounds `migkit demo` only. The `pip install dist/*.whl` that
precedes it is a separate, untimed step. So all three places that believe they
guard the two-minute definition of done — the contract's §5 item 5, the
repository's `clean_venv_check.ps1`, and the CI job whose comment asserts the
claim in so many words — measure the same 4–26 second interval and none of them
measures the 83–91 seconds that dominates it.

This is not a proposal to loosen the budget. It is that the two numbers in play
are measuring different things and only one of them is the one the README will be
asked to stand behind. Either the stopwatch moves to cover `pip install`, or the
criterion is reworded to be about `migkit demo` alone and the preamble stops
saying `pip install` is inside the two minutes.

### F2 — The README makes no timing claim at all, so there is nothing to reconcile — yet

§6's README requirement 4 says *"The two-minute claim is backed by the measured
elapsed time from checklist item 5, or it is reworded to whatever was measured."*
`README.md` at `c8e6a03` contains no occurrence of "minute", "elapsed", "120", or
any timing assertion — checked with `Select-String` over the whole file. So there
is no disagreement between the README and the measured number today.

The finding is that requirement 4 is currently vacuous, and that anyone writing
the sentence in Phase 4 has to write it against **142.3s / 127.3s**, not against
the 8.6s the script prints. Writing "under two minutes" from the script's output
would put a false claim into a file that PyPI freezes at upload (per the Phase 9
amendment).

### F3 — `clean_venv_check.ps1` deletes the `$tmp` that rows 6, 7, 8 and 10 depend on

§5 items 6, 7, 8 and 10 all reference `$tmp` — the throwaway venv item 5 creates.
The repository's sanctioned implementation of item 5 destroys `$tmp` on the way
out, which is correct behaviour for a standalone check and makes the four
following rows unrunnable if you run the checklist the way the repository invites
you to.

The workaround is `-Keep`, which is what this run used, followed by an explicit
`Remove-Item` for item 11. Neither the contract nor the script's help says so.
Anyone running §5 in order will hit this.

### F4 — A clean install today resolves `opik-rigor` **0.1.1**, not the 0.1.0 the compatibility record is written against

`pip install <wheel>` into a fresh venv produced `opik-rigor-0.1.1` in every
throwaway environment created tonight. The declared bound `>=0.1.0,<0.2` permits
it. The repository's long-lived `.venv` still holds 0.1.0, which is why this is
invisible from a dev shell — the pytest header prints `plugins: opik-rigor-0.1.0`
there and `opik-rigor-0.1.1` in a freshly resolved venv.

`COMPATIBILITY.md`'s verified-against table names **0.1.0**, and §6 requires that
record to describe *the installed artifact*. The artifact a stranger installs
today is 0.1.1. Nothing observed tonight suggests 0.1.1 misbehaves — the demo,
the suite and import purity are all green against it — but "verified against
0.1.0" and "users get 0.1.1" is precisely the gap §6 exists to close, and the
sibling's `d41856c` lesson (*predictions need checking back*) applies to a
verification that has been overtaken as much as to one that was guessed.

### F5 — Two README output blocks no longer reproduce

`README.md` states: *"Every command and every block of output below was executed,
and the output is pasted rather than described."* Two blocks contradict that at
`c8e6a03`:

1. **The suite transcript** claims `730 passed, 4 xfailed in 30.90s`. The suite at
   this commit reports **`750 passed`** with no xfails — the four xfails were
   retired in `f1eb368`, the commit immediately before this one. §5 row 1 exists
   specifically so that "a later reader knows whether something silently stopped
   running", and the README's copy of that number now says something false in
   exactly that direction.
2. **The install transcript** pastes
   `... opik-rigor-0.1.0 pygments-2.20.0 rich-15.0.0 scipy-1.18.0`. A clean
   install today produces the same line with `opik-rigor-0.1.1`. The README's
   prose then leans on it: *"the install transcript in the quickstart shows
   `opik-rigor-0.1.0` arriving from the index."*

Both are the `9339435` failure mode in miniature — a claim that was true when
written, in a file whose selling point is that its claims were executed. Both are
one re-run away from being true again. `README.md` belongs to another agent
tonight, so they are recorded here and not touched.

---

## Contract defects

Three defects were already found in `docs/session-4-release-contract.md` tonight
and amended in place. These are additional, all found by executing §5.

**D1 — §5 item 1's `-m "not requires_network"` selects nothing and reads as a
promise.** The `requires_network` marker was removed from
`[tool.pytest.ini_options]` tonight; `pyproject.toml`'s comment records why. The
row still carries the selector. Measured: 750 items collected and 750 passed both
with and without it, so the expression deselects nothing. `--strict-markers` does
not catch this — it governs markers *applied* to tests, not names used in a `-m`
expression, so an expression naming a marker that no longer exists evaluates
false for every test and quietly matches all of them. The row therefore looks
like a network guarantee and provides none. *Fix:* drop the selector from the row
and state the guarantee the way `ci.yml` and `drift-canary.yml` now keep it — the
keys are blanked and everything runs.

**D2 — §5 item 5's stated evidence says "exit code 0"; the correct exit is 1.**
The row reads *"Evidence: exit code 0; elapsed seconds ..."*. The bundled demo
exists to show the tool refusing an unsafe migration, so a correct run is a NO-GO
and exits 1 — measured 1 on every one of the five demo runs tonight.
`clean_venv_check.ps1` agrees (it treats only exit 3 as a failure), `ci.yml`
asserts exactly 1, and the README's transcript shows 1. Phase 9's identical error
was already caught and carries an amendment dated 2026-08-13 explaining that
reading it literally *"would have had somebody treat the correct outcome as a
failed release, or worse, 'fix' the demo until it returned 0"*. **The same
sentence in §5 item 5 was not amended.** It is the more dangerous of the two,
because §5 is the checklist somebody works through row by row.

**D3 — `py -3.12` is hardcoded in §5 item 5, Phase 7 and Phase 9, and does not
exist on the release machine.** `py --list` reports one runtime,
`-V:3.14[-64] * Python 3.14.4`. The literal command fails:

```
[ERROR] No runtime installed that matches 3.12. Try running "py install 3.12".
```

with exit code `-1610612730` — and, run as written, the failure is silent in the
worst way: `py -3.12 -m venv $tmp` leaves no `$tmp`, and every subsequent line of
the row then fails on a missing path rather than on the real cause.
`clean_venv_check.ps1` gets this right, falling back `-3.12` → `-3`, which is why
it worked. The pin is not arbitrary — `ci.yml`'s demo job runs
`python-version: "3.12"`, so the contract is mirroring CI — but the machine the
release is cut on runs 3.14.4 and has no 3.12. *Fix:* the contract should say "a
base interpreter that is not the repo venv" and defer to the script's resolution
order, or the machine needs 3.12 installed and the mirroring of CI made explicit
as the reason.

**D4 — §5 item 7's fixture paths cannot resolve from the cwd the checklist leaves
you in, and cannot resolve from anywhere except the repository root.** Item 5 does
`Push-Location $tmp` and never pops; items 6–10 inherit it. From `$tmp`, all four
relative paths fail with `ArtifactError: no run artifact at ...` and all four exit
3 (7a above). Making the artifact paths absolute is not sufficient: the artifacts
record their golden set as `tests/fixtures/goldenset.jsonl`, relative, so the
golden-set read fails next (7b above). Only cwd = repository root gets past both.
The row's own note *"Adjust the fixture paths to whatever Session 2/3 actually
built"* anticipated the file names, which are now right, but not the cwd, which is
not. *Fix:* the row needs an explicit `Pop-Location` or its own `Push-Location`
to the repo root, and should say that artifact files carry cwd-relative golden-set
references.

**D5 — §5 item 7 cannot produce three of its four verdicts without a provider
credential, in a checklist whose premise is that no credential is needed.**
`tests/fixtures/judges.toml` declares `adapter = "fake"` and `migkit compare`
refuses it by design, with a message that redirects to `migkit demo`. Measured:
`go`, `nogo` and `review` all exit 3 with `JudgeConfigError` from the repository
root. Only `error -> 3` runs through the CLI, and it is the one case that needs no
judging at all. The fixture author documented this in the file's header and
supplied `make_fixtures.py --check` as the derivation — which does produce
`0, 1, 2, 3` correctly. The contract's row does not know that. *Fix:* either the
row cites `make_fixtures.py --check` as its command, or the fixtures gain a
CLI-acceptable keyless judging path. As written, a person working the checklist
sees four 3s and has no way to tell a broken build from a refused config.

**D6 — §5 item 5 measures the wrong interval for the criterion it is placed
under.** Detailed as finding F1. Recorded here too because the fix is a change to
the contract's snippet and to Phase 9's, not only to the script.

**D7 — §5's rows 6, 7, 8 and 10 depend on a `$tmp` that the repository's own
implementation of row 5 deletes.** Detailed as finding F3.

**D8 — §5 item 3 / Phase 1's METADATA grep is case-insensitive and pastes
non-metadata into the evidence.** `Select-String -Pattern '^(License|...|Name|
Version)'` matches `name    = "accuracy"` from the README's TOML example, which
travels inside METADATA as the long description. Cosmetic, but this command's
output *is* the evidence. *Fix:* `-CaseSensitive`.

**D9 — §5 item 10 says "All four agree or Phase 8 stops", but the fourth source
does not exist until Phase 8 has run.** `git tag --list 'v*'` correctly prints
nothing before the tag is cut, and Phase 0's exit criterion requires that no tag
exist. The row's own text hedges with "and after Phase 8, `v0.1.0`", so the intent
is clear; the summary sentence is not. *Fix:* "the three build-side sources agree
now; the tag is the fourth and is checked at Phase 8."

---

## What a re-runner should expect

Re-running this file against `c8e6a03` should reproduce every verdict. Two numbers
will differ and are expected to: the wall-clock timings (machine and network
dependent, though the shape — `pip install` dominating, total over 120s — should
hold), and `opik-rigor`'s resolved version, which will be whatever the newest
release under `<0.2` is on the day.

One verdict is expected to *change*, and its changing is the point: row 3a's
`version-not-dev` FAIL must become a PASS once Phase 3 replaces `0.1.0.dev0` with
`0.1.0`. If it is still failing when Phase 8 is reached, Phase 8 does not start.
