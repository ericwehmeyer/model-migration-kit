# migration-kit — Session 4 contract: release and publishing

Frozen. Derived 2026-08-13 from `docs/build-plan.md`, `HANDOFF.md`, `PROGRESS.md`,
and — mostly — from the sibling project `opik-rigor`, which went through this exact
process today and left its mistakes in the commit log rather than tidying them away.

## What this document is, and what authorises it

`docs/build-plan.md` defines three build sessions and stops. It does not define a
Session 4, and this contract does not invent one: it specifies the phase that the
plan and `HANDOFF.md` already defer *into*. The plan says the package name is
checked "in Phase 0 of publishing, not now" (§ preamble). `HANDOFF.md`'s "Not yet
done, deliberately" defers three things and no more — no GitHub remote, the PyPI
name unchecked, `src/migration_kit/__init__.py` unwritten. Session 4 is the
discharge of exactly those three deferrals and nothing else.

**Scope is closed.** No product features. No change to v0.1 scope. Nothing is
promoted out of the plan's §5 roadmap paragraph: trend history, Opik experiment
logging, cost-per-verdict, and multi-judge weighting stay out, and stay out of the
README too, because a README that describes them is a feature commitment wearing
documentation's clothes.

Session 4 ends when a stranger with no keys can `pip install migration-kit`, run
`migkit demo`, and be reading an HTML report inside two minutes — the plan's §5
definition of done, restated as an acceptance test rather than an aspiration.

---

## The four landmines, verified against the sibling

Each of these cost the sibling real time today. Each is prevented by a specific
phase below rather than left to be rediscovered.

### 1. The import name was taken, and the check happened after the tag

opik-rigor tagged v0.1.0, *then* ran the name check, and found `rigor` already
taken on PyPI by an unrelated HTTP-API-testing DSL whose wheel installs a
top-level `rigor/`. Any environment holding both would have had one silently
shadowing the other depending on `sys.path` order. The rename (`8b6e6a9`,
"Rename the import package to opik_rigor") touched **34 files, 272 insertions,
200 deletions**, and the rename *itself* introduced two further bugs, both caught
by the suite rather than by reading:

- the replacement's lookbehind excluded word characters and dots but not hyphens,
  so the *distribution* name `opik-rigor` became `opik-opik_rigor` in four places
  — including the `pip install` hint shown to users;
- a test detected a leaked Opik import with `m.startswith('opik')`, and
  `opik_rigor` starts with `opik`, so the package reported itself as a leak.

`PROGRESS.md` draws the right lesson and it is the one that governs Phase 0:
*"the check was cheap, it was written down as a known gap rather than acted on,
and it sat unactioned right through the tag. A gap you have recorded is not a gap
you have closed."*

**Prevented by:** Phase 0, which runs before every other phase and re-runs
immediately before Phase 8. No tag exists until Phase 0's evidence is in
`PROGRESS.md`.

### 2. License mismatch between the declared identifier and the shipped text

`387b741` ("Release prep") found `license = { file = "LICENSE" }` embedding the
**entire MIT text** into the metadata's `License` field, and a deprecated
`License :: OSI Approved :: MIT License` classifier alongside it. PEP 639
deprecates the classifier in favour of the SPDX expression, and **PyPI rejects
uploads that set both**.

migration-kit's `pyproject.toml` is already in the corrected shape —
`license = "Apache-2.0"`, `license-files = ["LICENSE", "NOTICE"]`, and no
`License ::` classifier. That is the *declaration*. Nobody has yet checked that
the declaration matches the shipped bytes, or that both files land in the wheel.
Apache-2.0 §4(d) makes the NOTICE file load-bearing in a way MIT never was.

**Prevented by:** Phase 1, which reads the built wheel's `METADATA` and
`.dist-info/licenses/` rather than re-reading `pyproject.toml`.

### 3. Trusted publisher registered on TestPyPI but not PyPI

From `2c7cd46` ("v0.1.0 is on PyPI"): *"Trusted publishing needed three attempts,
all for the same reason and none of them in this repo: PyPI and TestPyPI are
separate sites needing separate publisher registrations, with a different
environment name each (pypi vs testpypi). The claims the workflow sent were
correct every time."*

The failure mode is nasty because the workflow is correct, the OIDC claim is
correct, and the error is on the far side of an HTTP boundary you cannot test
locally. Three attempts against an *irreversible* endpoint. The same commit
records what saved it: *"The TestPyPI dry run earned its keep here — it rehearsed
the exact failure before the irreversible run, and when the real upload did fail,
0.1.0 was still unclaimed."*

**Prevented by:** Phase 6, which registers both pending publishers before any
dispatch and treats "both listed, environment names verbatim" as the exit
criterion.

### 4. Documentation claims not backed by executed output

Two separate incidents, and the second is the sharper one.

`d41856c` found `COMPATIBILITY.md` documenting the pytest marker as
`@rigor.repeat(...)` when the shipped marker is `@pytest.mark.rigor_repeat`. The
wrong spelling came from the build plan, was written into the compatibility
document *before the plugin existed*, and was never reconciled against
`MARKER_NAME` once it did. The lesson, in the file itself: *"the parts describing
your own unwritten code are predictions, and predictions need checking back."*

`9339435` is a public retraction. `COMPATIBILITY.md` had asserted that Opik's
published SDK reference renders every parameter with a leading underscore and
would silently produce unnamed traces. It was wrong: the page had been read
through an HTML-to-markdown converter, and the reference italicises parameters, so
`_like this_` gave every name one extra underscore. The tell was in the same
output all along — the two genuinely private parameters came back with *two*
underscores. The generalisation, which is this contract's rule for §6:
**introspection establishes what an API is; it never establishes what its
documentation says, and the second claim needs evidence of its own.**

A third instance, `51c8e64`, is the same error pointed at a venue rather than a
document: an announcement was drafted for a Discussions forum nobody had checked
existed. It didn't.

**Prevented by:** Phase 4 and the §5 checklist, where every README claim is
traceable to a command in the evidence log, and by §6's rule that a statement
about opik-rigor's *documentation* is a separate claim from a statement about
opik-rigor's *behaviour*.

---

## 1. The ordered phase list

Phases are strictly ordered. A phase may not start until the previous phase's exit
criterion has been met and its evidence pasted into `PROGRESS.md`. The repo venv is
`.\.venv\Scripts\python.exe` throughout; commands are PowerShell 7.

---

### Phase 0 — Name availability, before anything else

**Precondition:** Session 3 complete; suite green; nothing tagged, nothing pushed.

There are three distinct names to check and they fail in three different ways.
The *distribution* name is what `pip install` takes and is first-come-first-served.
The *import* name is what collides silently in `site-packages` — the sibling's
actual failure. The *console script* name is what collides on `PATH`, which is
worse than either because it is invisible until two tools are installed together.

migration-kit gets one piece of luck the sibling did not have, and it is worth
stating because it changes how much work this phase is. PEP 503 normalises a
project name with `re.sub(r"[-_.]+", "-", name).lower()`, so `migration-kit`,
`migration_kit`, and `Migration.Kit` are **the same project on PyPI**. The
distribution name and the import name therefore resolve to one URL, and one check
covers both. opik-rigor had two genuinely different names (`opik-rigor` and
`rigor`) needing two checks, and only the second one collided. Do not let the
convenience turn into an assumption: `migkit` is a third, unrelated name.

```powershell
foreach ($u in @(
    'https://pypi.org/simple/migration-kit/',
    'https://test.pypi.org/simple/migration-kit/',
    'https://pypi.org/simple/migkit/',
    'https://test.pypi.org/simple/migkit/'
)) {
    $r = Invoke-WebRequest -Uri $u -Method Head -SkipHttpErrorCheck
    '{0}  {1}' -f $r.StatusCode, $u
}

# Second, independent check through pip's own resolver, not through a URL.
.\.venv\Scripts\python.exe -m pip index versions migration-kit
.\.venv\Scripts\python.exe -m pip index versions migkit
```

Then, by hand and recorded with the date, a search for a distribution under some
*other* name that ships a top-level `migration_kit/` — the shape of the sibling's
collision. `https://pypi.org/search/?q=%22migration_kit%22` is JavaScript-rendered
and will not answer from a script; open it in a browser and paste the result count
into `PROGRESS.md`. This check is inherently weaker than the others, which is why
Phase 7 and Phase 9 both re-verify it empirically by asserting where
`migration_kit.__file__` actually resolves in a clean install.

**Evidence already collected, 2026-08-13:** all four `Invoke-WebRequest` URLs
above returned **404**. `migration-kit` and `migkit` are unclaimed on both PyPI and
TestPyPI as of that date. This is a snapshot, not a reservation — PyPI names are
claimed by strangers at any moment, and the whole point of the sibling's failure is
that a stale check is no check. **Re-run this entire phase immediately before
Phase 8** and record the second date.

**Exit criterion:** four status codes, two `pip index versions` outputs, and one
dated search result pasted into `PROGRESS.md` under a "Name checks" heading. No git
tag exists. If any check comes back claimed, stop and take Open Decision 1 to the
lead — do not improvise a name.

---

### Phase 1 — Licensing and package metadata, read off the built artifact

**Precondition:** Phase 0 clear.

`pyproject.toml` declares Apache-2.0. Phase 1 does not re-read that declaration; it
reads what the build actually produced, because the sibling's bug was invisible in
the source and obvious in the metadata.

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade build twine
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m twine check dist/*

$whl = Get-ChildItem dist\*.whl | Select-Object -First 1
$out = Join-Path $env:TEMP 'mk-wheel-inspect'
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
Expand-Archive $whl.FullName -DestinationPath $out
Get-Content $out\*.dist-info\METADATA |
    Select-String -Pattern '^(License|License-Expression|License-File|Classifier: License|Requires-Dist|Requires-Python|Name|Version)'
Get-ChildItem -Recurse $out\*.dist-info\licenses
Get-Content $out\*.dist-info\licenses\LICENSE -TotalCount 3
```

**Exit criterion**, every clause checkable in that output:

1. `twine check` reports `PASSED` for **both** the sdist and the wheel.
2. `METADATA` contains `License-Expression: Apache-2.0`.
3. `METADATA` contains `License-File: LICENSE` **and** `License-File: NOTICE`, and
   both files are present under `.dist-info/licenses/`. Apache-2.0 §4(d) requires
   the NOTICE to travel with the work; a wheel that drops it ships a licence
   migration-kit is not complying with.
4. There is **no** `Classifier: License ::` line. PyPI rejects an upload carrying
   both that and an SPDX expression (`387b741`).
5. The `License:` field does not contain the Apache text. If the full licence body
   appears in metadata, the legacy `license = { file = ... }` form has crept back.
6. `licenses/LICENSE` begins `Apache License / Version 2.0, January 2004`, matching
   the SPDX identifier. A declared identifier that disagrees with the shipped text
   is the mismatch this phase exists to catch, and no tool checks it for you.
7. `Requires-Dist` lists `opik-rigor>=0.1.0,<0.2`, `jinja2>=3.0`, `rich>=13.0`,
   `tomli>=2.0; python_version < '3.11'` and nothing else; `Requires-Python: >=3.10`.

   *Amended 2026-08-13.* The clause originally named three dependencies. `tomli`
   was added afterwards and deliberately: `tomllib` arrived in the standard
   library in 3.11, this project's floor is 3.10, and CI actually runs 3.10 — so
   on the lowest supported Python the config loader has nothing to parse TOML
   with. Deleting the dependency to satisfy the old sentence would break that
   loader for every 3.10 user, which is why the release script raises this as a
   flag rather than letting either side win silently. Note the script reads this
   sentence out of this file rather than holding its own copy of the list, so
   editing the contract is the only way to clear the flag, and removing the
   dependency makes the check fail rather than pass.

One licence question the tooling will not raise: opik-rigor is MIT and
migration-kit is Apache-2.0. MIT is inbound-compatible with Apache-2.0 and no
relicensing problem exists, but `NOTICE` must not be read as claiming authorship of
opik-rigor. Confirm the current NOTICE text — it claims only migration-kit — is
still accurate at release.

---

### Phase 2 — `src/migration_kit/__init__.py`

**Precondition:** Phase 1 clear; Sessions 1–3 complete, so there is a surface to
re-export. Written last, deliberately, exactly as `PROGRESS.md`'s "Known gaps"
requires. Full specification in §2 below.

**Exit criterion:** `.\.venv\Scripts\python.exe -m pytest` green, including the
four new package-level tests §2 mandates (frozen `__all__`, every name importable,
no duplicates, import-purity subprocess check). Plus:

```powershell
.\.venv\Scripts\python.exe -c "import migration_kit as m; print(m.__version__); print(len(m.__all__))"
```

---

### Phase 3 — Version and changelog

**Precondition:** Phase 2 clear. Policy in §3 below.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -c "import migration_kit, importlib.metadata as md; v=migration_kit.__version__; d=md.version('migration-kit'); print(v, d); assert v==d, (v,d); assert 'dev' not in v"
```

**Exit criterion:** that command prints `0.1.0 0.1.0` and exits 0; `CHANGELOG.md`
exists with a `## [0.1.0] - <date>` section in the sibling's format; the
`0.1.0.dev0` currently in `pyproject.toml` is gone from every file.

---

### Phase 4 — Documentation written from executed output

**Precondition:** Phase 3 clear. Content requirements in §6 below.

This phase produces `README.md` (replacing the placeholder, whose own banner says
it is one) and `COMPATIBILITY.md` (which does not yet exist). Nothing may be
written here that was not first run. The order is: run the commands, capture the
output, paste it, then write prose around it — not the reverse, because prose
written first recruits evidence to fit, and that is how `@rigor.repeat(...)`
survived into a published file.

**Exit criterion:** the §5 checklist item "every README code block executed" is
satisfied, with a transcript in the scratch directory. The PyPI badge in
`README.md` remains **commented out** — the sibling kept it commented until the
package existed and uncommented it in `2c7cd46`; a badge pointing at a 404 is a
claim that the package is published.

---

### Phase 5 — GitHub remote, workflows, CI green

**Precondition:** Phase 4 clear; working tree committed.

The remote is a Phase 5 concern rather than a Phase 0 one because a trusted
publisher claim binds `owner/repo/workflow/environment`, and creating the repo
under the wrong account means re-registering everything. `pyproject.toml` already
declares `https://github.com/ericwehmeyer/migration-kit`; confirm that is right
before this phase, not after (Open Decision 5).

```powershell
gh repo create ericwehmeyer/migration-kit --public --source . --remote origin --push
gh workflow list
gh run watch
```

`.github/workflows/publish.yml` is added in this phase, by reference to the
sibling's working file at `C:\Users\ewehm\repos\opik-rigor\.github\workflows\publish.yml`.
Copy it and change only what must change; every design decision in it was paid for
today. Specified in §4 below. It publishes nothing until a release is published.

**Exit criterion:** the CI matrix is green — py3.10–3.13 × Ubuntu + Windows — and
so are the `demo` and `build` jobs. Note that migration-kit's `demo` job was
written before the demo existed (a `PROGRESS.md` decision: *"A `demo` CI job exists
before the demo does"*), so this is the first push on which it can pass. If it is
red, Session 3 is not actually complete and Session 4 stops here.

See §5 item 5 for a defect this phase will *not* catch: the `demo` job runs
`pip install -e .`, and an editable install keeps the repo root importable, so a
demo golden set living outside the package directory works in CI and fails for
every real user.

---

### Phase 6 — Trusted publisher registration, on both indexes

**Precondition:** Phase 5 green. **This is the phase that cost the sibling three
attempts against an irreversible endpoint.**

PyPI and TestPyPI are separate sites with separate account systems and separate
publisher registries. Registering on one tells the other nothing. Because neither
project exists yet on either index, both registrations are **pending publishers**,
created from the account page rather than from a project page.

On https://pypi.org/manage/account/publishing/ :

| Field | Value |
|---|---|
| PyPI Project Name | `migration-kit` |
| Owner | `ericwehmeyer` |
| Repository name | `migration-kit` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

On https://test.pypi.org/manage/account/publishing/ : identical, except
**Environment name `testpypi`**. That single differing field is the whole of the
sibling's failure. The workflow's two jobs declare `environment: testpypi` and
`environment: pypi` respectively, and the OIDC claim carries the environment name,
so a publisher registered with the wrong one rejects a perfectly correct claim.

Create the two GitHub environments explicitly rather than letting a workflow run
conjure them:

```powershell
gh api -X PUT repos/ericwehmeyer/migration-kit/environments/testpypi
gh api -X PUT repos/ericwehmeyer/migration-kit/environments/pypi
gh api repos/ericwehmeyer/migration-kit/environments --jq '.environments[].name'
```

**Exit criterion:** both pending publishers are visible in their respective
"Pending publishers" lists, with all five fields transcribed into `PROGRESS.md`
verbatim from the forms — not from this document, so a typo in this document
cannot propagate. Both GitHub environments listed by the last command. No workflow
has been dispatched.

---

### Phase 7 — TestPyPI dry run, and a clean install from it

**Precondition:** Phase 6 exit criterion met on **both** sites.

```powershell
gh workflow run publish.yml --ref main
gh run watch
```

The workflow's `testpypi` job is gated `if: github.event_name == 'workflow_dispatch'`
and the `pypi` job on `github.event_name == 'release'`, so a dispatch structurally
cannot reach the real index. That split is why the dry run is safe to repeat.

Then install what was actually uploaded, into a throwaway venv, from TestPyPI:

```powershell
$tmp = Join-Path $env:TEMP ('mk-test-' + [guid]::NewGuid().ToString('N'))
py -3.12 -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install `
    --index-url https://test.pypi.org/simple/ `
    --extra-index-url https://pypi.org/simple/ `
    "migration-kit==0.1.0"
& "$tmp\Scripts\python.exe" -c "import migration_kit; print(migration_kit.__file__, migration_kit.__version__)"
& "$tmp\Scripts\migkit.exe" demo --out $tmp\demo.html
```

`--extra-index-url` is required and is not optional hygiene: TestPyPI is not a
mirror of PyPI, so `opik-rigor`, `jinja2`, and `rich` do not resolve there. The
usual objection to spanning two indexes — that pip may silently prefer a same-named
package from the wrong one — does not apply here, because Phase 0 established that
`migration-kit` exists on neither index before this upload, so there is exactly one
candidate. Record that reasoning; it stops being true for 0.2.

**Exit criterion:** the run is green; `migration_kit.__file__` resolves under
`$tmp\Lib\site-packages\` and **not** under `C:\Users\ewehm\repos\`; `migkit demo`
exits 0 and writes the HTML. Then delete `$tmp`.

---

### Phase 8 — Tag, release, publish

**Precondition:** Phases 0–7 all clear, **Phase 0 re-run today**, §5 checklist
complete with every command's output recorded.

The tag comes here, ninth of ten phases, and this ordering is the single most
important thing in this document. opik-rigor's order was tag → check name →
rename 34 files → retag. Nothing about that was necessary; the check is two HTTP
requests.

```powershell
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --title "v0.1.0" --notes-file .\dist\release-notes.md
```

A tag alone does nothing — the workflow triggers on `release: published`, not on a
tag push, as its header comment states. `gh release create` publishes immediately;
use `--draft` and a later `gh release edit v0.1.0 --draft=false` if a human should
read the notes first. The `pypi` job then refuses to upload unless the release tag
equals `v` + the version parsed out of the wheel filename — which is also what
would catch a leftover `0.1.0.dev0`, since that wheel's version field is
`0.1.0.dev0` and would not match `v0.1.0`.

**Exit criterion:** the `pypi` job is green and `https://pypi.org/project/migration-kit/0.1.0/`
resolves. If the job fails on OIDC, do not retry blindly — go back to Phase 6 and
diff the five registered fields against the workflow, because that is what the
failure means and 0.1.0 is still unclaimed while it does.

---

### Phase 9 — Post-publish verification, and making the docs match reality

**Precondition:** Phase 8 green.

```powershell
$tmp = Join-Path $env:TEMP ('mk-real-' + [guid]::NewGuid().ToString('N'))
py -3.12 -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install migration-kit
$sw = [Diagnostics.Stopwatch]::StartNew()
& "$tmp\Scripts\migkit.exe" demo --out $tmp\demo.html
$code = $LASTEXITCODE; $sw.Stop()
"exit={0}  elapsed={1:n1}s" -f $code, $sw.Elapsed.TotalSeconds
& "$tmp\Scripts\python.exe" -m pip show migration-kit
```

No `--index-url`, no `--pre`, no flags — the sibling verified exactly this
(`2c7cd46`: *"pip install opik-rigor now works from the real index with no flags"*).

Then one commit that updates documentation from intent to fact, mirroring
`2c7cd46`: uncomment the PyPI badge, change any "not yet published" wording,
change the `CHANGELOG` "Known limitations" line that says the package is
unpublished, and record the publication date in `PROGRESS.md`.

**Exit criterion:** clean-venv install from the real index succeeds; `migkit demo`
exits 0; **elapsed time is recorded and is under 120 seconds**, because that number
is the plan's §5 definition of done and the README will be asserting it; `pip show`
reports `0.1.0` and `Apache-2.0`; the docs-match-reality commit is pushed and CI is
green on it.

---

## 2. `src/migration_kit/__init__.py` — the public surface

### The rule

**A name is re-exported at the top level if a caller holding only the published
wheel needs it to (a) construct an input to the tool, (b) read an artifact the tool
wrote, or (c) catch an error the tool raises. Everything else stays behind its
module.**

The rule is restrictive on purpose. Every name in `__all__` is a compatibility
promise, and this package is `Development Status :: 3 - Alpha` sitting on a
dependency whose own report objects are `dict[str, Any]` today and scheduled to
change in 0.2 (`PROGRESS.md`, "Known gaps"). A surface you did not need to promise
is a 0.2 migration you did not need to write.

### The surface

Session 3 leaves eight modules: `contracts`, `errors`, `goldenset`, `runner`,
`judging`, `comparison`, `report`, `cli`. The re-exports come from the first six.

**From `errors` — all six, unconditionally.** `MigrationKitError`,
`GoldenSetError`, `ArtifactError`, `JudgeConfigError`, `JudgeReliabilityError`,
`ConfigError`. Criterion (c) has no exceptions: you cannot write `except` against a
name you cannot import, and forcing `from migration_kit.errors import ...` on a
caller is friction with no benefit. This mirrors `opik_rigor/__init__.py`, which
re-exports its whole hierarchy.

**From `contracts` — the shapes, not the plumbing.** `GoldenItem`, `Completion`,
`RunHeader`, `Verdict`, `ARTIFACT_SCHEMA_VERSION`, `hash_file`. These are what a
caller reading a run artifact off disk needs, and `hash_file` is what lets someone
verify a golden-set hash independently — which is the auditability story, so it
belongs on the surface rather than one module down.

Staying private: `canonical_json`, `artifact_stem`, `utc_now`, `as_sequence`, and
the seven `EVENT_*` constants. The event names are a real consumer need for anyone
parsing the evidence log, but seven flattened constants would dominate the
namespace; they are reachable as `migration_kit.contracts.EVENT_VERDICT`, which
reads better anyway.

**From `goldenset`:** `GoldenSet`, `content_hash`. (Confirmed present.)

**From `runner`:** `RunArtifact`, `run_goldenset`, `DEFAULT_N`. (Confirmed
present.) `artifact_path_for` stays private — it is a naming convention, not a
contract, and `artifact_stem` beneath it already is.

**From `judging` and `comparison`:** the judge-config loader, the function that
applies judges to an artifact, the comparison entry point, and the comparison
result type. Names are not fixed here because those modules did not exist when this
contract was frozen; Phase 2 resolves them against the code as built, applying the
rule above, and **must not** invent aliases to match a name guessed here. Guessing
a name for unwritten code and never checking back is precisely what put
`@rigor.repeat(...)` into a published file (`d41856c`).

**Not re-exported at all: `report` and `cli`.** They are reached as
`migration_kit.report` / `migration_kit.cli`. `cli` is the console-script entry
point and importing it at package-import time would run argparse construction for
every consumer; `report` pulls in jinja2 and rich, which a caller embedding the
verdict logic in their own harness should not pay for. This is a weaker argument
than opik-rigor's "core never imports integrations" invariant — jinja2 and rich are
hard dependencies here, not optional extras, so nothing *breaks* if they load. It
is kept and pinned by a test anyway, because the cheap version of this rule stops
being cheap the moment someone adds a module-scope side effect to `cli.py`.

Also exported: `__version__` (see §3), listed in `__all__` as the sibling does.

### The mechanical rules

1. `__all__` is sorted and contains no duplicates.
2. A test freezes the **exact set**: `assert set(migration_kit.__all__) == {...}`
   with the set written out literally in the test. Adding a public name then
   requires two deliberate edits, which is the point.
3. A test asserts every name in `__all__` resolves via `getattr` — an entry that
   was renamed in its module and not here is otherwise invisible until a user hits
   it.
4. A **subprocess** test asserts that importing `migration_kit` loads neither
   `migration_kit.cli` nor `migration_kit.report`, and neither `jinja2` nor `rich`.
   In-process it would pass or fail depending on what the rest of the suite
   imported first; opik-rigor learned this the expensive way when three tests
   asserted `find_spec("openai") is None` and were testing the environment rather
   than the library (`CHANGELOG.md`, "A fourth defect was **not** caught by
   authorship separation"). The check compares **top-level module names**, not
   `str.startswith` — `opik_rigor`.startswith(`opik`) made the sibling's package
   report itself as a leak (`8b6e6a9`).
5. The module docstring states the invariant, so the next reader learns it from the
   file rather than from this contract.

---

## 3. Version and changelog policy

### Where the version lives

**One place: `__version__` in `src/migration_kit/__init__.py`**, with
`pyproject.toml` reading it:

```toml
[project]
dynamic = ["version"]

[tool.hatch.version]
path = "src/migration_kit/__init__.py"
```

opik-rigor carries the number twice — `version = "0.1.0"` in `pyproject.toml` and
`__version__ = "0.1.0"` in `__init__.py` — and they happen to agree. Nothing
enforces it. The publish workflow's tag-vs-wheel check catches a tag that
disagrees with the build, but nothing catches `__version__` disagreeing with the
metadata, and that is the copy a user prints when filing a bug. Single-sourcing
removes the failure mode instead of testing for it.

Belt and braces, since the cost is one line: the Phase 3 test asserts
`importlib.metadata.version("migration-kit") == migration_kit.__version__`. That
also catches the subtler case where a stale wheel is installed over an editable
checkout.

### How it is bumped

Semantic versioning, as the sibling's `CHANGELOG.md` declares. The bump is a single
edit to `__version__`, in its own commit, with the `CHANGELOG` section for that
version in the same commit — never in the release commit, never after the tag.
`0.1.0.dev0` is the current value and becomes `0.1.0` in Phase 3. After release,
the first commit on `main` sets `0.2.0.dev0`, so that anything built from `main` is
self-evidently not the release.

The exit codes are part of the compatibility surface: `PROGRESS.md` invariant 7
states *"Exit codes 0/1/2/3 are the CI contract. Changing them is a breaking
change."* Under SemVer that means a major bump, and the README table and
`contracts.Verdict.EXIT_CODES` must be pinned to each other by a test.

### CHANGELOG format

`CHANGELOG.md`, following the sibling exactly: Keep a Changelog 1.1.0 + SemVer
declared in the header, `## [0.1.0] - YYYY-MM-DD`, and a link reference at the
bottom to the GitHub release tag.

Sections for 0.1.0, in this order:

- **Added** — one bullet per user-visible capability, written as what the tool now
  does, not as what was implemented. The sibling's bullets state the *reasoning*
  inline (*"gates on the one-sided Wilson lower confidence bound, never on the
  observed rate"*), which is why its changelog is readable a year later.
- **Fixed** — for a first release this covers defects found *during* the build, and
  the sibling's version records **the mechanism that caught each one**, which is
  the part worth keeping: three from authorship separation, one from an environment
  change that falsified a test's hidden assumption. migration-kit runs the same
  method (`HANDOFF.md` §"The working method"), so it will have the same material.
- **Known limitations** — mandatory, and the section that does the most work. It is
  where the caller friction goes, the `dict[str, Any]` reports inherited from
  opik-rigor, the fact that `Completion.tokens_in`/`tokens_out` are `None` for
  every adapter opik-rigor ships (rigor roadmap item 9, recorded in `601b40b`), and
  anything a user would otherwise discover by being surprised. A limitation you
  wrote down is a design decision; the same limitation discovered by a user is a
  bug report.

No "Unreleased" section at release time — it is added back with the `0.2.0.dev0`
bump.

---

## 4. The release workflow

Copy `C:\Users\ewehm\repos\opik-rigor\.github\workflows\publish.yml`. It works, it
is the third iteration of something that failed twice, and every comment in it is
load-bearing. Change the repository-specific parts only.

What must be preserved verbatim, and why each clause exists:

**Trigger is `release: published`, plus `workflow_dispatch`.** A tag push does
nothing. This is stated in the file's header comment and is worth keeping there,
because "I pushed the tag, why is nothing happening" is the first thing that
happens otherwise.

**`concurrency: cancel-in-progress: false`.** A half-finished upload is not
something to race a second one against.

**Top-level `permissions: contents: read`; `id-token: write` granted only on the
two publish jobs.** OIDC is the entire authentication mechanism — there is no token
and no secret anywhere in the file.

**The two publish jobs are split by trigger event, not by discipline.** `testpypi`
is `if: github.event_name == 'workflow_dispatch'`; `pypi` is
`if: github.event_name == 'release'`. Exactly one is ever eligible per run, so a
manual dispatch **structurally cannot** reach the real index. This is what makes
the dry run repeatable without fear.

**Environments `testpypi` and `pypi`, spelled exactly as registered in Phase 6.**
The OIDC claim carries the environment name. This is landmine 3: correct workflow,
correct claim, wrong registration, three failed attempts (`2c7cd46`).

**`pypa/gh-action-pypi-publish` pinned to commit
`dc37677b2e1c63e2034f94d8a5b11f265b73ba33` (v1.14.2), not to the tag.** From
`c4485f6`: a git tag is mutable and can be repointed by whoever controls the
repository, and this job holds `id-token: write`, so whatever runs in it can mint a
token that publishes under your name. One detail that will bite anyone re-pinning:
v1.14.2 is an **annotated** tag, so the pin is the *dereferenced commit*, not the
tag object — pinning the tag object does not resolve. Keep the version in a
trailing comment and update both together. Check for a newer release before
copying; if you take one, dereference it yourself rather than trusting this SHA.

**The tag-vs-version guard on the `pypi` job.** The version comes from the built
wheel, not from the tag, so a release tagged `v0.2.0` would cheerfully upload
`0.1.0` under that name — and a PyPI version number can be yanked but **never
reused**. Cheap check, irreversible mistake.

**Build once, publish the same artifact.** The `build` job produces the sdist and
wheel and uploads them; both publish jobs download that artifact rather than
rebuilding. What is tested on TestPyPI is then byte-identical to what reaches PyPI.

Ordering constraint, restated as the rule: **both pending publishers exist before
any dispatch; the dispatch happens before the release; the release happens after
the tag; the tag happens after the name check.** The sibling violated the last of
those and paid 34 files for it; it violated the first and paid three attempts.

`ci.yml` needs no rework — migration-kit's already matches the sibling's shape and
adds the `demo` job. One change belongs here (Open Decision 4): have the `demo` job
install the built **wheel** rather than `pip install -e .`, so that CI exercises
what a user gets.

---

## 5. Pre-release verification checklist

Every row is a command. The evidence is its output, pasted into `PROGRESS.md` or a
release transcript. A row without output is not done — `HANDOFF.md` §5: *"A claim
is backed by command output or it is not a claim."*

**1. Suite green, offline, keyless.**
```powershell
$env:ANTHROPIC_API_KEY=''; $env:OPENAI_API_KEY=''
.\.venv\Scripts\python.exe -m pytest -m "not requires_network"
```
Evidence: the pass/skip counts. Record them; the sibling records `514 passed / 11
skipped` in three separate places and that is how a later reader knows whether
something silently stopped running.

**2. Lint clean.**
```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

**3. Build and metadata.** Phase 1's commands. Evidence: `twine check` PASSED ×2,
plus the METADATA excerpt.

**4. The sdist and wheel contain what they must.**
```powershell
tar -tzf (Get-ChildItem dist\*.tar.gz).FullName
Expand-Archive (Get-ChildItem dist\*.whl).FullName -DestinationPath $env:TEMP\mk-w -Force
Get-ChildItem -Recurse $env:TEMP\mk-w | Select-Object -ExpandProperty FullName
```
Evidence: the listings. The sdist must contain `LICENSE`, `NOTICE`, `README.md`,
`pyproject.toml`, `src/`, and `tests/`. The **wheel must contain the demo golden
set**, and this is the row most likely to fail.

`[tool.hatch.build.targets.wheel] packages = ["src/migration_kit"]` puts only that
directory in the wheel. `goldensets/` at the repo root is not in it. `migkit demo`
needs a bundled toy golden set, and if that set lives at the repo root the demo
works in the dev venv, works in CI's `demo` job (an editable install keeps the repo
root importable), and **fails for every user who installs the wheel** — the exact
population the plan's §5 definition of done is about. Package data belongs at
`src/migration_kit/data/` and is loaded via `importlib.resources`, never via
`Path(__file__).parent.parent.parent`. See Open Decision 3.

**5. Clean throwaway venv, wheel install, demo, timed.** The definition-of-done
test.
```powershell
$tmp = Join-Path $env:TEMP ('mk-verify-' + [guid]::NewGuid().ToString('N'))
py -3.12 -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install (Get-ChildItem dist\*.whl).FullName
Push-Location $tmp   # NOT the repo: a repo-root cwd can mask a missing package resource
$sw = [Diagnostics.Stopwatch]::StartNew()
& "$tmp\Scripts\migkit.exe" demo --out "$tmp\demo.html"
$code = $LASTEXITCODE; $sw.Stop()
"exit={0} elapsed={1:n1}s" -f $code, $sw.Elapsed.TotalSeconds
& "$tmp\Scripts\python.exe" -c "import migration_kit,opik_rigor; print(migration_kit.__file__); print(opik_rigor.__file__)"
Pop-Location
```
Evidence: exit code 0; elapsed seconds (the number the README's two-minute claim
rests on); `demo.html` exists and is non-trivial in size; **both** `__file__` paths
under `$tmp\Lib\site-packages` and neither under `C:\Users\ewehm\repos\` — the
latter is the empirical version of Phase 0's import-name check, and also proves
migration-kit is consuming the published opik-rigor rather than the sibling working
copy, which is the entire basis of its "first real consumer" claim.

Running from `$tmp` rather than the repo matters. A demo that reads
`.\goldensets\demo.jsonl` relative to the cwd passes from the repo root and fails
from anywhere else, which is every user.

**6. The HTML is genuinely self-contained.** Plan §1: it must open in an airgapped
compliance review.
```powershell
Select-String -Path "$tmp\demo.html" -Pattern 'https?://|src\s*=|<script[^>]+src' -AllMatches
```
Evidence: no external fetch. Links in prose are fine; anything the browser would
*load* is not.

**7. Exit-code matrix.** Plan §3, and `PROGRESS.md` invariant 7.
```powershell
foreach ($f in 'go','nogo','review','error') {
    & "$tmp\Scripts\migkit.exe" compare --baseline .\tests\fixtures\$f-a.jsonl --candidate .\tests\fixtures\$f-b.jsonl --judges .\tests\fixtures\judges.toml
    "{0} -> {1}" -f $f, $LASTEXITCODE
}
```
Evidence: `go -> 0`, `nogo -> 1`, `review -> 2`, `error -> 3`, matching
`contracts.Verdict.EXIT_CODES`. Adjust the fixture paths to whatever Session 2/3
actually built.

**8. Import purity, in a subprocess, from the installed wheel.**
```powershell
& "$tmp\Scripts\python.exe" -c "import sys, migration_kit; tops={m.split('.')[0] for m in sys.modules}; bad=tops & {'jinja2','rich','anthropic','openai','opik'}; print(sorted(bad)); assert not bad, bad; assert 'migration_kit.cli' not in sys.modules; assert 'migration_kit.report' not in sys.modules"
```
Evidence: prints `[]`, exits 0. Top-level names, not prefixes (`8b6e6a9`).

**9. Every README code block executed.** Extract them, run each, diff the captured
output against what the README claims character for character. opik-rigor's
`PROGRESS.md`: *"Session 4 README quickstart was executed, not written. Every code
block was run in a clean virtualenv against the built wheel, and the output pasted
verbatim."* Evidence: the transcript, and a diff that is empty.

**10. Version consistency, four ways.**
```powershell
& "$tmp\Scripts\python.exe" -c "import migration_kit, importlib.metadata as md; print(migration_kit.__version__, md.version('migration-kit'))"
(Get-ChildItem dist\*.whl).Name
git tag --list 'v*'
```
Evidence: `0.1.0`, `0.1.0`, `migration_kit-0.1.0-py3-none-any.whl`, and after
Phase 8, `v0.1.0`. All four agree or Phase 8 stops.

**11. Teardown.** `Remove-Item -Recurse -Force $tmp`. A throwaway venv that
survives becomes the environment the next check accidentally trusts.

---

## 6. What must be true in COMPATIBILITY.md and README.md

migration-kit's vendor dependency is **opik-rigor itself** — a package written by
the same author, in a sibling directory on the same disk, published to PyPI today.
That proximity is the hazard. A vendor-API record is worth something only if it
records what the *installed artifact* does; the temptation to answer a question by
reading `C:\Users\ewehm\repos\opik-rigor\src\` instead is constant and would make
the whole document circular.

### COMPATIBILITY.md — does not yet exist; Phase 4 creates it

Model it on `C:\Users\ewehm\repos\opik-rigor\COMPATIBILITY.md`. Required content:

**A verified-against table**, same shape as the sibling's: `opik-rigor` **0.1.0**;
verification date; the Python used (3.14.4 on Windows locally); opik-rigor's own
`Requires-Python` (`>=3.10`); and the method — *installed from PyPI into a clean
venv and introspected the live objects with `inspect.signature`*. Not "read the
sibling repo". `PROGRESS.md` already records that the venv holds opik-rigor 0.1.0
**from PyPI, not a path dependency**, deliberately; the compatibility record must
be produced the same way, and must assert `opik_rigor.__file__` is under
`site-packages`.

**The exact surface migration-kit calls**, read off the installed package. On
current evidence that is: `sample`, `SampleResult`, `Run`, `SampleTimeout`;
`Adapter`, `FakeAdapter`, `AdapterError`; `PinnedJudge`, `Verdict`;
`EvidenceLog`, `EvidenceRecord`; `assert_pass_rate`, `assert_no_regression`,
`wilson_lower_bound`, `wilson_interval` and their error types; `Baseline` if used.
Keep it small and say why — the sibling's framing, *"two functions' worth of
surface, so that a release which moves something costs an afternoon rather than a
rewrite"*, applies verbatim here.

**The version policy, with its reasoning.** `opik-rigor>=0.1.0,<0.2`. The lower
bound because that is the only version verified. The upper bound because rigor's
report objects are `dict[str, Any]` today and its 0.2 roadmap changes exactly that
surface (already reasoned in `PROGRESS.md`). This is the same argument shape as the
sibling's `opik>=2.0,<3`, and the same discipline: the bound is justified by a
named failure mode, not by habit.

**The two known frictions, because they are vendor behaviour migration-kit depends
on working around.** Both were found by migration-kit itself and recorded upstream
in `601b40b`:

1. `sample` records a classifier failure in the same field as a call failure, and
   `default_outcome` raises `TypeError` on a plain `str`. migration-kit's first
   end-to-end run reported *6 completions and 6 provider failures against a
   `FakeAdapter` that answered every prompt correctly*. The workaround is one
   explicit `outcome=` at every `sample` call site. Document it: a future reader
   removing that argument would reintroduce a failure that is silent in the
   direction that matters, making a model look like it answered nothing.
2. `Adapter.complete(prompt) -> str` exposes no usage data, which is why
   `Completion.tokens_in` and `tokens_out` are `None` for every adapter rigor
   ships. **Therefore the README makes no claim about tokens, cost, or
   cost-per-verdict** — which is also where the plan's §5 roadmap fence lands.

**A "what to do when this drifts" section**, as the sibling has: which files change
(the runner's `sample` calls, the judging module's `PinnedJudge` construction), and
the rule that the version bound and this document are updated **in the same commit
as the code fix**, so the record of what was verified never lags the code.

**Predictions need checking back.** COMPATIBILITY.md will describe migration-kit's
own names alongside rigor's, and Session 4 is writing it after those names exist —
so every such statement is re-checked against the shipped code, not against the
build plan. `@rigor.repeat(...)` got into a published file precisely because a name
was guessed in a plan, written into a compatibility document before the code
existed, and never reconciled (`d41856c`).

**Do not assert anything about opik-rigor's documentation** unless you have opened
its README and quoted it. Statements about behaviour come from introspection;
statements about documentation need their own evidence; the two are different
claims and conflating them produced the retraction in `9339435`. Being the author
of both projects makes this *easier* to get wrong, not harder — memory feels like
evidence.

### README.md — currently a self-declared placeholder

1. **Delete the status banner.** It says *"This README is a placeholder written in
   Session 1 so the package builds. The real one — with an executed quickstart —
   comes in Session 3."* Shipping that to PyPI publishes an admission that the
   documentation is not real.
2. **Every code block executed against the built wheel in a clean venv, output
   pasted verbatim**, with a sentence saying so — the sibling's *"Everything below
   was executed in a clean virtualenv against the built wheel, and the output is
   pasted verbatim. Nothing here is illustrative."* That sentence is a commitment;
   only write it if checklist item 9 passed.
3. **The headline is `migkit demo`**, because the definition of done is a stranger
   with no keys. Show the real verdict banner and the real exit code from the
   Phase 9 run. If the demo produces `NO-GO` — as plan §5 anticipates — that is the
   screen to lead with, on the sibling's reasoning that a quickstart showing only
   success is selling the wrong thing.
4. **The two-minute claim is backed by the measured elapsed time** from checklist
   item 5, or it is reworded to whatever was measured.
5. **The demo says loudly that it uses `FakeAdapter`s** (plan §4, demo
   credibility), with the real-adapter path documented separately for readers with
   keys.
6. **The exit-code table matches `contracts.Verdict.EXIT_CODES`** and is pinned by
   a test, because it is the CI contract and a README that disagrees with it is a
   broken pipeline someone else has to debug.
7. **Badges:** CI, Python 3.10+, License Apache-2.0 live; **PyPI badge commented
   out until Phase 9**.
8. **The opik-rigor section says what is imported and links COMPATIBILITY.md.**
   The current text — *"Every statistical primitive is imported from opik-rigor,
   none reimplemented"* — is the right claim and is checkable: no Wilson interval,
   no Mann-Whitney, no bootstrap anywhere in `src/migration_kit/`. Check it with
   `Select-String` before shipping it, because it is the project's central claim
   and the one a reviewer will test first.
9. **No roadmap item is described as if it exists.** Trend history, Opik experiment
   logging, cost-per-verdict, multi-judge weighting: a roadmap section may name
   them as not-in-v0.1, in the sibling's style, and nothing else.

---

## 7. Open decisions for the lead

**1. Fallback distribution name if `migration-kit` is claimed before Phase 8.**
Unclaimed on PyPI and TestPyPI as of 2026-08-13 (four HEAD requests, all 404), and
`migkit` is unclaimed too. Names get taken; the re-run in Phase 8 may come back
different. The fork: `migkit` as the distribution name — shorter, and it matches
the command users actually type, collapsing three names into two — versus a
qualifier like `llm-migration-kit`, which keeps the descriptive name at the cost of
a `pip install` that does not match the command. Deciding now costs nothing;
deciding under a claimed name at tag time is how the sibling ended up renaming 34
files.

**2. Import name: `migration_kit` or `migkit`.** They are currently different from
the console-script name (`migkit`) and identical to the distribution name modulo
PEP 503 normalisation. Keeping `migration_kit` is unambiguous and already written
through the codebase and its tests. Moving to `migkit` makes the import, the
command, and the distribution one word. **Phase 2 is the last cheap moment** —
after `__init__.py` and after publication the cost is the sibling's rename plus a
PyPI redirect that does not exist. Recommendation is to keep `migration_kit`, but
it is a one-way door and belongs to the lead.

**3. Where the demo golden set lives.** `src/migration_kit/data/demo.jsonl` loaded
through `importlib.resources` (ships in the wheel; the demo works for a stranger)
versus `goldensets/` at the repo root (visible, editable, browsable on GitHub — and
absent from the wheel). This is not a preference: the current
`packages = ["src/migration_kit"]` wheel config means option two produces a demo
that passes CI and fails for every user, because CI installs editable. If the lead
wants the golden set browsable at the repo root, the wheel config has to grow a
`force-include` and Phase 1's listing check has to prove it landed.

**4. Does CI's `demo` job install the wheel or `-e .`?** Today it is `-e .`, which
cannot detect decision 3 going wrong. Installing the wheel makes CI test what users
get; it also means the `demo` job depends on `build`, lengthening the critical path
on every push. Real trade, lead's call.

**5. Repository account and visibility, before Phase 6.** `pyproject.toml` assumes
`github.com/ericwehmeyer/migration-kit` and public. A trusted publisher claim binds
`owner/repo/workflow/environment`; changing the owner later means re-registering on
both indexes. Confirm before Phase 5, not after.

**6. A required reviewer on the `pypi` GitHub environment.** On a one-person repo a
required reviewer means approving your own deploy, which is theatre — except that
it inserts a deliberate pause in front of the one step in this project that cannot
be undone, and the sibling's own note is that *"a version can be yanked, but the
number can never be reused."* Cheap either way; worth choosing on purpose rather
than by default.

**7. Is there a public Python API at all in v0.1?** §2 specifies a small re-export
surface. The alternative is to ship `migration_kit` as CLI-only, with an explicitly
private Python API and an empty `__all__` — no compatibility promises, total
freedom to reshape `judging` and `comparison` in 0.2 when opik-rigor's typed report
objects land underneath them. The cost is that anyone embedding the verdict logic
in their own harness is doing something unsupported. Given that the plan's
definition of done is entirely about the CLI, this is a genuine fork and not an
obvious one.
