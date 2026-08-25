# PROGRESS

Running state of the model-migration-kit v0.1 build. Updated at the end of every
session. If you are picking this up cold, read [HANDOFF.md](HANDOFF.md) first —
it is written for exactly that — then [docs/build-plan.md](docs/build-plan.md),
which is the approved plan this build follows and was written before any code.

## Where the build stands

| Session | Scope | Status |
|---|---|---|
| 0 | Scaffold, license, CI, frozen contracts | **complete**, committed `45b6567` |
| 1 | Data path, offline: `goldenset.py`, `runner.py`, resumability | **complete** |
| 2 | Judgment and verdict: `judging.py`, `comparison.py` | **complete** |
| 3 | Faces: `report.py`, `cli.py`, `migkit demo`, README | **complete** |
| 4 | Release: name check, `__init__.py`, publish workflow, PyPI | in progress — name checked and free, `__init__.py` written, version single-sourced, `CHANGELOG.md` and `publish.yml` in the tree, **nothing published** |

**929 passed, 0 xfailed**, ruff clean over `src`, `tests` and `scripts`, measured
2026-08-14 at `19c7722`. The count moved seven times in one session, so **re-run
it rather than quoting this line** — that instruction has now been vindicated
repeatedly, and every pasted transcript in the README that quoted an older number
became wrong the same night.

The 4 xfails this section used to describe are retired, with their fenced
replacements in place.

Two things the suite is now *proved* to be rather than described as:

- **Offline.** CI runs it under `scripts/audit/netguard.py`, which makes every
  outbound connect, `connect_ex`, `create_connection` and `getaddrinfo` raise,
  loopback excepted. The old `-m "not requires_network"` deselected **zero** tests
  — `--strict-markers` validates markers on tests, not a name in a `-m`
  expression — so the guarantee rested entirely on nobody having written a test
  that dialled out. A positive control was run first, because a guard that never
  fires would pass this suite silently.
- **Order-independent.** `scripts/audit/shuffle_order.py` shuffles the whole
  collected list, tearing tests out of their file and class groupings; 841 passed
  at seeds 11 and 99 when it was run. Not wired into CI — that is an audit worth
  running deliberately, not a per-push cost.

`migkit demo` runs keyless in **a couple of seconds** and returns NO-GO at exit 1,
with a self-contained 25,760-byte report carrying the red FAKE MODELS band — the
definition of done's stranger-with-no-keys path, executed rather than imagined.
This document used to say "under two seconds" and that was the one phrasing a
normal run could falsify: six consecutive runs of the installed console script on
this machine took 2.16, 2.85, 2.74, 2.64, 2.62 and 2.53 s wall, and five runs
through `python -m model_migration_kit.cli` took 2.61, 3.37, 2.21, 2.04 and 2.40 s.
Every one of the eleven was over two seconds. The README's "2.11 s" is a pasted
transcript of one run and stays true as such; a *rate* claim has to survive the
slow end of the distribution, and "under two seconds" did not. Exit 1 and the
absence of any `src="http`, `href="http` or `@import` in the report were checked
on the same runs.

The package was renamed to **model-migration-kit** (import `model_migration_kit`,
console script still `migkit`) before publication. Research killed the premise
that prompted it — Python's database-migration tools never use `-kit`, and "LLM
migration" is the term of art in this niche — but found a better reason: the old
name gave the verb and omitted the object, colliding with 198 GitHub repositories,
a taken npm name, and a trademarked commercial product on the exact phrase.

**CI is green** — the thing this section used to say had never happened. All four
Windows cells had been failing on a test that looked for the console script beside
`sys.executable`, which holds in a venv and on Ubuntu but not on GitHub's Windows
toolcache; because `demo` and `build` declare `needs: test`, neither had ever
executed. Resolved through `sysconfig.get_path("scripts", …)` and watched green on
the real matrix.

Two CI jobs were added or rewritten on 2026-08-14 and one of them may legitimately
fail:

- `dependency_surface.py --check` fails the build when `COMPATIBILITY.md`'s table
  disagrees with the AST. It has already caught two changes it was not written
  for.
- The `demo` job now times **`venv` + `pip install` + `migkit demo`** against the
  120-second definition of done. It used to time only the last step, which
  measured ~8.6 s of the whole path.

  It **passes at 12 s** on the Ubuntu runner. It measured **127 s and 142 s** on
  this Windows machine, 83–91 s of that being pip dragging in numpy and scipy.
  Both numbers are real and they are for the same interval; Ubuntu runners get
  manylinux wheels and a warm cache, a Windows stranger gets neither. So the green
  tick establishes the claim for a Linux CI machine and says nothing about the
  environment where it was falsified. Extending the job to a Windows runner is one
  matrix entry and is the honest fix; narrowing the published claim to name its
  environment is the other. **Do not go back to timing a smaller interval.**

Session 4 still to do: the version bump off `0.1.0.dev0`, making the repo public,
trusted-publisher registration on both indexes, the TestPyPI dry run, and the
release itself. `scripts/verify_release.py` reports the remaining blockers and
refuses to pass while they stand: **14 passed, 1 skipped of 15 checks** on
`--allow-dev-version`, exiting 2 because a skip is not a pass; without that flag
the `version-not-dev` check fails outright, which is the bump and is meant to
block until the release act happens.

Session 4 is not in the build plan, which defines three build sessions. It is the
release phase the plan defers *into* ("check the PyPI name in Phase 0 of
publishing, not now"), specified rather than improvised, because the sibling
project improvised it and paid for that.

## What exists

Rebuilt from `git ls-files` on 2026-08-13, not from memory. Test counts are from
`pytest --collect-only -q`; line counts from `wc -l`.

| Path | State |
|---|---|
| `pyproject.toml` | Apache-2.0, deps `opik-rigor>=0.2,<0.3` (floor moved 2026-08-14, in the same change as the `confidence` validation it matches), `jinja2>=3.0`, `rich>=13.0`, `tomli` on 3.10 only; `migkit` console script pointing at `model_migration_kit.cli:main`, **which exists and works**; version is `dynamic` and single-sourced from `__init__.py` |
| `LICENSE`, `NOTICE` | Apache-2.0 full text (202 lines) and an 8-line NOTICE. Both are in `license-files`, so both ship inside the wheel |
| `CHANGELOG.md` | `## [0.1.0] - 2026-08-14`, derived from the commit log and this file rather than from memory. Carries the rigor-0.2.0 contract entry as a fifth mechanism under **Fixed** |
| `README.md` | 551 lines, every command in it executed before it was pasted |
| `HANDOFF.md` | 220 lines, the cold-start document; read it before this one |
| `COMPATIBILITY.md` | 1,096 lines. What this project uses from rigor, verified against the *installed 0.1.0 wheel*. The dependency table is 11 module rows, enumerated by AST walk |
| `.gitattributes` | forces LF — load-bearing, see invariants |
| `.gitignore` | ignores `*.jsonl` outputs and `dist/`, but whitelists `goldensets/**` and test fixtures |
| `.github/workflows/ci.yml` | 116 lines. py3.10–3.13 × Ubuntu + Windows with ruff and pytest; a `demo` job that installs the **wheel** and asserts `migkit demo` exits exactly 1; a `build` job running `python -m build` and `twine check`. `demo` and `build` both declare `needs: test` |
| `.github/workflows/drift-canary.yml` | 299 lines. Weekly; installs the *latest* opik-rigor ignoring this project's own upper bound, so a breaking rigor release is found here on a Monday rather than by a user on a Friday |
| `.github/workflows/publish.yml` | 110 lines, byte-identical to the sibling's. Trusted publishing (OIDC) only — no token, no secret. TestPyPI gated on `workflow_dispatch`, PyPI on `release`, so a push cannot reach either index |
| `src/model_migration_kit/__init__.py` | 47 lines, written last by design. `__all__` is empty; holds `__version__ = "0.1.0.dev0"`, which is the only place the number exists |
| `src/model_migration_kit/errors.py` | 112 lines — exception hierarchy, frozen, unfrozen once for `ReportError` (see decisions) |
| `src/model_migration_kit/contracts.py` | 239 lines — shared data shapes, frozen; `EVENT_COMPLETION` added after review |
| `src/model_migration_kit/goldenset.py` | 276 lines — strict JSONL loader, content + provenance hashes, `stats()` |
| `src/model_migration_kit/runner.py` | 638 lines — n draws per item via rigor's `sample`, resumable, append-only artifact |
| `src/model_migration_kit/judging.py` | 685 lines — every completion graded by a pinned judge, failures included |
| `src/model_migration_kit/comparison.py` | 1,498 lines — the verdict logic, GO / NO-GO / REVIEW with power in view |
| `src/model_migration_kit/report.py` | 2,122 lines — self-contained HTML, rendered from the evidence log only |
| `src/model_migration_kit/cli.py` | 741 lines — `run`, `judge`-through-`compare`, `report`, `demo`; the exit-code contract lives here |
| `src/model_migration_kit/demo.py` | 388 lines — the keyless NO-GO, earned from `FakeAdapter`s rather than arranged |
| `src/model_migration_kit/data/` | `demo.toml` (32), `demo_goldenset.jsonl` (12 items), `demo_rubric.md` (44) — bundled *inside the package*, see decisions |
| `tests/test_goldenset.py` | 97 cases |
| `tests/test_runner.py` | 64 cases |
| `tests/test_judging.py` | 196 cases |
| `tests/test_comparison.py` | 42 cases |
| `tests/test_cli.py` | 78 cases |
| `tests/test_report.py` | 129 cases |
| `tests/test_release_checks.py` | 128 cases, 4 of them xfail by decision |
| `scripts/verify_release.py` | 1,383 lines — the pre-release checklist as a command that reports what it checked and why |
| `scripts/clean_venv_check.ps1` | 390 lines — the definition of done as a command: throwaway venv, built wheel, no keys |
| `docs/build-plan.md` | 76 lines, the approved plan, verbatim |
| `docs/session-2-contract.md`, `docs/session-2-verdict-review.md` | 168 and 357 lines — the judgment seams, and the review that corrected the verdict logic before it was built |
| `docs/session-3-contract.md` | 909 lines |
| `docs/session-4-release-contract.md` | 978 lines — the release phase, specified rather than improvised |
| `docs/readme-scan-contract.md` | 204 lines, frozen 2026-08-13 — how `verify_release.py` is allowed to read the README |
| `.venv` | created, `pip install -e ".[dev]"` done, imports verified |

Every module in `src/` is written and every one has a test file written by an
agent that did not write the module. All test authorship is one-directional: no
module author wrote a test for their own module.

Verified working: `opik-rigor 0.1.0` (installed **from PyPI**, not from the local
repo — model-migration-kit consumes the published artifact, and `COMPATIBILITY.md`
proves it by the absence of `direct_url.json` in the `.dist-info`), `jinja2 3.1.6`,
`rich 15.0.0`.

## Name checks

Both candidate names are free on both indexes.

| Probed 2026-08-13 | PyPI | TestPyPI |
|---|---|---|
| `model-migration-kit` | `https://pypi.org/simple/model-migration-kit/` → **404** | `https://test.pypi.org/simple/model-migration-kit/` → **404** |
| `migkit` | `https://pypi.org/simple/migkit/` → **404** | `https://test.pypi.org/simple/migkit/` → **404** |

`migkit` is the console script, not a distribution this project uploads, but it is
probed because a distribution of that name could later collide in a user's
`PATH`, and finding that out before the README hardens is free.

**Why `/simple/` and not the JSON endpoint.** The JSON API returns 404 in two
different situations: for a name nobody has ever registered, and for a name
someone registered and never uploaded a file to. Those have opposite consequences
— the first is available, the second is taken — and a JSON 404 cannot tell them
apart. PEP 503's `/simple/` index distinguishes them: an unregistered name 404s,
while a registered-but-empty project answers 200 with an empty file list. So
`/simple/` is the probe of record here. (For contrast, both JSON probes also
returned 404 today; that is consistent with the `/simple/` result and adds
nothing to it.)

This check has been got wrong once already in this repository, in the direction
that matters: the README proved "not published yet" by 404ing the **old** name,
which is evidence about a different package than the claim it supports. Fixed in
`60ca118`. Re-run all four probes on the day you tag — availability is a fact
with a timestamp, and the gap between this line and the release is where somebody
else registers the name.

## Decisions made, and why

**`argparse`, not `click`.** The plan left this open pending review. argparse
handles subcommands adequately for three verbs and costs no dependency, and a
tool whose selling point is auditability is better with a smaller supply chain.
Revisit only if subcommand ergonomics genuinely suffer.

**`opik-rigor` comes from PyPI, pinned `>=0.2,<0.3`.** Not a path dependency to
the sibling repo. This makes model-migration-kit a real consumer of the published
package rather than of a working copy, which is the only way the "first real
consumer" claim means anything. Upper bound because rigor's report objects are
untyped dicts today and its 0.2 roadmap changes that surface.

**Contracts frozen before any module is written.** `contracts.py` holds
`GoldenItem`, `Completion`, `RunHeader`, `Verdict`, the hashing convention, and
the evidence event names. It imports nothing from the rest of the package. This
is the same discipline that kept parallel agents from disagreeing on seams in
opik-rigor, and it is why it exists before Session 1 rather than during it.

**Hashing is sha256 with CRLF normalised to LF**, identical to rigor's rubric
hashing. Golden-set hashes are embedded in every downstream artifact, so without
this a Windows checkout and a Linux CI runner would disagree about whether the
golden set had changed.

**A golden set has two hashes, and only one of them gates anything.**
`GoldenSet.hash` is the *content* identity — sha256 over the canonical JSON of the
parsed items, sorted by id — and it decides comparability. `file_hash` is the raw
bytes, kept for provenance. The first implementation used the bytes for both, and
a conformance review showed what that costs: letting an editor add the trailing
newline it always adds, or writing an item's keys in a different order, produced a
different identity for a semantically identical set, and the operator would have
had to re-run a baseline that cost real money. It also contradicted the convention
`contracts.py` states in its own docstring — that the hash is of content, not of a
formatting decision.

**A failed draw counts against the resume budget, and the cost of that is
recorded, not fixed.** See known gaps: the honest default is that a draw is a
draw, and the alternative — re-drawing failures on resume — would let repeated
re-runs launder a model that times out into one that does not.

**Adapter identity is disclosed rather than enforced.** `RunArtifact.adapters`
lists every adapter that contributed to an artifact, and the report prints it.
Only one case is refused outright: resuming a real-provider run with rigor's
`FakeAdapter`, which would put fabricated completions and real ones under one
model string. Anything stricter would fire on legitimate wrapping — a retry proxy,
an instrumentation shim, the counting proxy the test suite uses to prove a resume
did not re-sample — while still missing a `FakeAdapter` hidden behind a wrapper,
since the recorded value is a class name. A check with false positives on honest
use and false negatives on determined misuse is worse than disclosure.

**`tomli` on 3.10.** `tomllib` is 3.11+, `requires-python` is `>=3.10`, and CI
runs 3.10, so the gap is real rather than theoretical. The config loader imports
`tomllib` with a `tomli` fallback; 3.11+ installs nothing extra. This clause also
had to be added to the frozen Session 4 criterion, which named three dependencies
"and nothing else" — `verify_release.py` parses that clause out of the contract
rather than holding its own copy, so the disagreement could not be silenced by
editing the script, and deleting the dependency to satisfy the sentence would
have broken the loader for every 3.10 user. Amending the contract was the only
move that cleared it (`f343293`), which is the property a checklist should have.

**`errors.py` was unfrozen once, for `ReportError`.** Deliberately, and recorded
here because "frozen" means changes are decisions rather than drift. The CLI maps
exception types to exit codes, so "this evidence log contains no verdict" has to
be distinguishable from "the tool broke"; reusing the base class would have made
every unrelated failure look like a report failure at the one place it matters.

**The demo golden set lives in `src/model_migration_kit/data/`, not repo-root
`goldensets/`.** Two independent reviews found the same trap: the wheel packages
only `src/model_migration_kit`, and CI's demo job used `pip install -e .`, which keeps
the repo root importable. A demo reading from `goldensets/` therefore passes CI
and every local test, and fails only for people who installed the wheel — who are
exactly the audience the definition of done is written about. `.gitignore` needed
a matching whitelist line, since `*.jsonl` was swallowing the file. The CI job now
installs the built wheel for the same reason (`fe4b5a9`).

**v0.1 ships CLI-only, with an empty `__all__`.** The definition of done is
entirely a CLI story, and rigor's report objects are `dict[str, Any]` today with
typing scheduled for its 0.2 — anything re-exported now would be a compatibility
promise over a surface that is about to move. `__init__.py` was written last, in
`04f4bd4`, once there was a surface to decline to export.

**A `demo` CI job exists before the demo does.** The definition of done says a
stranger with no keys reads a report within two minutes. That is only true if
something checks it on every push, so the job was written before Session 3
delivered `migkit demo`. It has since been amended to assert the verdict's exit
code — see closed gaps.

**The version is single-sourced, and the failure mode removed rather than
tested for** (`3be5101`). `pyproject.toml` declared `version = "0.1.0.dev0"` and
`__init__.py` declared `__version__` with the same string, with nothing enforcing
the agreement. `publish.yml`'s tag-vs-wheel guard catches a *tag* disagreeing with
the build, but nothing caught `__version__` disagreeing with the metadata — and
`__version__` is the copy a user quotes when filing a bug, so a disagreement is
invisible to everyone who could fix it. `dynamic = ["version"]` plus
`[tool.hatch.version]` reading `src/model_migration_kit/__init__.py` makes the
release bump one edit to one line. hatchling parses the assignment as text and
does not import the package, so nothing at build time depends on the runtime
dependencies being installed.

**How `verify_release.py` may read the README was frozen before it was
rewritten** (`aba3f67`, contract in `docs/readme-scan-contract.md`). Two release
checks were failing on prose rather than on a defect — a sentence read as twelve
package names, and a phantom `failed` subcommand that came from `echo "migkit
failed"` *inside* a fenced block, so restricting the scan to code fences was
necessary and not sufficient; command position had to be checked too. The
contract was written by hand, with the expected values derived by hand, because
the implementation (`932ec6a`) and its tests (`f2d9325`) were written in parallel
by agents that could not see each other and would otherwise have disagreed at
exactly that seam. Both of them independently found the same hole in the frozen
contract, which is the outcome that makes the method worth the overhead: a hole
found twice by parties who cannot collude is a hole in the specification, not a
disagreement between two implementations. Release checks went from **12 passed /
3 failed** at `7f5fcbe` to **14 passed / 1 failed** now, and the one remaining
failure is `version-not-dev`, which is the release act itself.

**`publish.yml` is the sibling's file, carried byte-identical** (`3be5101`).
Every line was checked for an owner, repo or project string and there is none:
the tag-vs-wheel guard parses the version positionally out of the wheel filename,
the artifact is named `dist`, and no job names a distribution. The pin
`pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33` was
re-verified — v1.14.2 is still the latest release, the tag object is annotated,
and that SHA is its dereferenced commit. Adding the file publishes nothing.

**`COMPATIBILITY.md`'s dependency surface is enumerated by AST, not by grep**
(`2875b35`). The table claimed to be "the whole dependency surface" while omitting
five files, and the old method — grepping line starts — structurally could not see
`tests/test_judging.py:429`, an import indented inside a test. Walking every `.py`
file's AST took the table from **6 module rows to 11** and made the completeness
claim checkable rather than asserted; the command is pasted in the file so it can
be re-run after a session boundary. The same pass repaired two other claims a
rigor release could have acted on: that the anthropic and openai adapters were
"not imported here" (they are imported and constructed — `cli.py:42-51` for the
import, and `cli.py:280` and `cli.py:282` inside `_model_adapter`, which
`COMPATIBILITY.md` still cites one line high), and two citations into rigor files
that had moved.

**`NOTICE` said `migration-kit`** (`60ca118`). Apache-2.0 §4(d) makes NOTICE
load-bearing and `pyproject.toml` ships it inside the wheel, so the old name would
have been frozen into the published artifact, where it cannot be edited without
burning a version number. Recorded because it is the cheapest possible bug to fix
before publication and an unfixable one after.

**The suite's determinism was audited rather than assumed** (`1808334`). A flaky
test in a tool about statistical gates is self-refuting, so: 20 full runs, 5
explicit shuffle seeds tearing tests out of file and class grouping, 4
`PYTHONHASHSEED` values, 3 timezones, and a plugin turning any non-loopback socket
call into a hard error — with fake credentials planted in the environment, to
prove the network path is not merely untaken but unreachable. Identical counts
every time. The mechanism holds it up rather than luck: there is no RNG anywhere
in `src`, `tests`, or the dependency's statistics; the one real-clock read is
timezone-aware UTC and injectable; and concurrent sampling sorts by index before
returning, so thread completion order is discarded.

**The history was rewritten before the repo goes public** (`60ca118`, replacing
`942fc81`). A pre-public read-through found a pointer into the private planning
repo in `HANDOFF.md`, naming an unannounced roadmap item, its build plan's path,
and a schedule estimate. Amending the file would have left the sentence in the
history of a repository about to be made public, so the commit was rewritten with
`filter-branch` and force-pushed; `backup/pre-history-rewrite` holds the original
locally, and `main` and `origin/main` agree at `2b7108e`. The repo was still
private throughout, so nothing leaked — the rewrite buys the ability to flip it
public without a second thought.

## Invariants

1. **model-migration-kit imports opik-rigor's *public* API only.** No reaching into
   internals. If something needed is missing, that is a rigor roadmap item —
   record it in this file, work around it at the API surface, do not monkey-patch.
2. **The report renders from the evidence log, not from in-memory state.** A
   crashed run must still produce a partial report from disk.
3. **The suite is green with no credentials and no network — enforced, not
   assumed.** The demo path uses `FakeAdapter`. No test carries the
   `requires_network` marker, so `-m "not requires_network"` deselects nothing and
   never did; the invariant now rests on `scripts/audit/netguard.py`, which CI
   runs and which makes every outbound connection and DNS resolution raise. A
   marker expression is a filter, not a guarantee — that distinction cost this
   project a false claim in a release contract.
4. **Two artifacts are comparable only if their `goldenset_hash` and judge-config
   hash both match.** Comparing across either is an `ArtifactError`, not a
   warning.
5. **`REVIEW` is never silently converted to `GO`.** Underpowered means
   underpowered.
6. **A failed completion is kept, never dropped.** If model B times out on three
   items, that is part of the migration decision; discarding it quietly improves
   B's apparent quality.
7. **Exit codes 0/1/2/3 are the CI contract.** Changing them is a breaking change.

## Known gaps, still open

Recorded entering Session 2 unless noted, and re-checked against the tree on
2026-08-13.

### Added 2026-08-14, and the two that block a release

- ~~`COMPATIBILITY.md` records verification against rigor 0.1.0.~~ **Re-verified
  against 0.1.1 on 2026-08-14.** Zero signature drift, nothing removed from
  `__all__`, five names added — the ones that let invariant 1 be retired. The two
  0.1.1 defects that make the documented real-model path unusable now lead that
  file. **They remain a release consideration even though this item is closed:**
  on the version users install, `require_pinned` refuses every current frontier
  Anthropic model id, and `AnthropicAdapter` 400s on `temperature` with no
  caller-side escape. Both fixed on rigor's `main`, both unreleased. A user's
  workaround today is a dated model id (`claude-haiku-4-5-20251001`).

  The way this went stale is the reusable part: the bound `>=0.1.0,<0.2` let every
  *fresh* install resolve 0.1.1 while this repo's long-lived `.venv` kept 0.1.0,
  so the document and the venv agreed with each other and both disagreed with
  reality. **A long-lived venv stops being evidence about what users get the
  moment a floor is permissive.**

- ~~The scale fixes were not independently re-measured.~~ **Measured 2026-08-14**
  at the audit's worst in-range case (200 items × n=20, 4 KB outputs, every item
  changed): HTML **32.4 MB → 10.14 MB**, 2,542 `<pre>` blocks, report model plus
  render 0.5 s, judging 1.46× from concurrency 1 → 8.

  Two honest qualifications. The 1.46× was measured against a `FakeAdapter` at
  zero latency, where the audit already showed the pool can do little because the
  per-record `fsync` is serial and outside it — the gain that matters is against a
  real provider at ~1 s per call, and this project cannot measure that without
  spending money, so **1.46× is the floor and not the claim**. And no row is
  dropped by the cap: rows past the budget render without input, draws or judge
  reasons and say so in place of the outputs, because dropping rows to fit a byte
  budget would silently remove the most interesting evidence.

- **`judge_artifact`'s `concurrency` is new and its determinism guarantee is the
  load-bearing part.** The artifact must be byte-identical at any setting;
  `_graded_in_order` is what buys that. If you touch judging, re-prove it with a
  hash over the records rather than assuming it.

- **The demo report's own filename was tracked in git and shipped in the sdist**
  for two commits, because `.gitignore` had `*.report.html` and the demo writes
  `migkit-demo-report.html` — a hyphen apart. Both spellings are ignored now. The
  general lesson is the reason it is recorded: `git status` was clean throughout,
  and no test would ever have caught it. **Build the sdist and read it** before a
  release; that check also caught the sibling shipping its agent worktrees and
  session-memory directory.

- **CI has never been green on this repository, and the fix is unwatched.** Added
  2026-08-13, and it should have been recorded the day CI was written. All four
  Windows cells failed a single test that looked for the `migkit` console script
  beside `sys.executable` — true in a venv and on Ubuntu, false on GitHub's
  Windows toolcache, where the interpreter is `...\Python\3.12.10\x64\python.exe`
  and pip puts console scripts one level down in `...\x64\Scripts\`. Nothing was
  wrong with the product. The expensive part is second-order: `demo` and `build`
  declare `needs: test`, so the two jobs that check the definition of done had
  never executed at all, on any push. The test now resolves the directory through
  `sysconfig.get_path("scripts", …)` for both the prefix and the user scheme,
  which is where the installation *declares* scripts go rather than where one
  layout happens to put them, and a second test reconstructs the hosted-toolcache
  layout in a temp directory on any host and asserts both halves — the old
  sibling lookup finds nothing there, the scheme lookup finds the script
  (`9f8f64b`). **The fix has not been seen on the real matrix.** The release
  contract's Phase 5 stops the release on a red matrix, so by its own rule this
  release was already halted and no document said so. A fix that has not been
  watched is a hypothesis; go and watch a run.
- **A provider outage bakes into an artifact.** Every recorded draw counts against
  the resume budget, failures included, so a run that errored on all n draws
  because the provider was down looks complete: re-running samples nothing, and
  the model takes a NO-GO for its infrastructure's sake. The only v0.1 remedy is
  `fresh=True`, which discards the artifact. A `retry_failed` flag was started and
  deliberately backed out — the name appears nowhere in `src/` or `tests/` today —
  because re-drawing failures is not a small change but a weighting decision:
  retried items end up with more draws than others, and the per-item indices
  collide with the ones already recorded, and weighting belongs to
  `comparison.py`. Decide it there, with the statistics in view, rather than in
  the writer.
- ~~**Invariant 1 is violated at declared sites.**~~ **Closed 2026-08-14.** rigor
  0.1.1 shipped on 2026-08-13 with `SCORE_MIN`, `SCORE_MAX`, `hash_rubric_file`
  and `hash_rubric_text` re-exported from `opik_rigor/__init__.py` and present in
  `__all__` — verified by reading `__init__.py` out of the published 0.1.1 wheel,
  not by trusting the branch. The floor in `pyproject.toml` was moved to
  `>=0.1.1,<0.2` at that point, every site imports from the package root, and
  `grep -rn "from opik_rigor\." src tests` returns nothing. *(The floor moved
  again on 2026-08-14, to `>=0.2,<0.3`, for rigor's confidence narrowing — the
  import-site finding below is unaffected.)*

  **The site count was wrong twice, in the same direction, and that is the part
  worth keeping.** This record originally said three names at one site; a
  mechanical audit corrected it to two names across three sites and said so
  loudly, because "a gap recorded inaccurately is worse than one not recorded at
  all — it reads as handled". When the fix was finally applied there were **six**
  sites: `comparison.py:96` had been reaching into `opik_rigor.judge` in *shipped
  code* the whole time and was never listed, and two more arrived the same night
  in new test files, because the pattern the tree already contained is the pattern
  a new file copies. A hand-maintained list of violation sites decays toward
  understatement every time somebody writes code. The grep is the record; the
  table was the mistake.

  The dependency was kept rather than worked around because both alternatives were
  worse: re-deriving the score range means a hard-coded `1.0` that goes silently
  wrong the day the scale moves, and hashing a rubric differently from rigor means
  the two disagree about whether the instrument changed. Recorded as rigor roadmap
  item 10 (commit `4bb7935`) and closed there.

  **One deliberate exception, added the same day and worth naming rather than
  hiding:** `tests/test_stranger_path.py:702-703` imports
  `opik_rigor.adapters.anthropic` and `.openai_compat` to read their `PACKAGE`
  constants, which are not in rigor's `__all__`. It is test-only, it touches no
  shipped code, and its whole purpose is drift detection — it asserts that the SDK
  names this CLI tells a user to `pip install` are the ones rigor is actually about
  to import, so an upstream rename fails a test here instead of sending a reader to
  install the wrong package. That is the opposite of a hidden dependency, but it
  does mean `grep -rn "from opik_rigor\." src tests` is no longer empty, and a
  claim that it is would be wrong. `src/` is still clean. If rigor ever exports
  `PACKAGE`, fold this in; until then it is a recorded, argued exception rather
  than an unnoticed one — which is the whole distinction this section exists to
  draw.
- **`tokens_in`/`tokens_out` are always `None`.** rigor's `Adapter` protocol is
  `model_id` plus `complete(str) -> str` and exposes no usage data, so a cost gate
  cannot be built without reaching past the seam. Recorded as roadmap item 9 in
  opik-rigor's PROGRESS.md (commit `601b40b`), per invariant 1. Still open there:
  rigor closed items 10–15 in its Phase 3 and item 9 is not among them, because an
  optional `complete_with_usage` changes the protocol rather than adding to it.
- **`artifact_stem` flattens `/`, `:` and space to `-`**, so `gpt/4o` and `gpt-4o`
  resolve to one filename (`contracts.py:219`). Both the resume path and
  `fresh=True` now check header identity before touching the file, so a collision
  is loud rather than destructive — but the stem still cannot distinguish them, and
  on Windows `GPT-4o` and `gpt-4o` collide as well. Including a short digest of the
  full model id in the stem would fix it and changes a frozen contract; deferred,
  not solved.

## Known gaps, closed

Kept rather than deleted. A gap you have recorded is not a gap you have closed,
and the inverse matters too: a closed gap still in the open list makes the whole
list untrustworthy, because a reader who checks one bullet and finds it already
fixed stops checking the others.

- **CI's `demo` job will fail the moment the demo works.** Recorded entering
  Session 2, when the job ran `timeout 120 migkit demo` with no exit-code
  handling, and the definition of done says the demo shows a NO-GO verdict, which
  is exit 1. The job was written before the demo on purpose. **Closed 2026-08-13
  in `fe4b5a9`**, and closed the way the bullet demanded — by teaching the job the
  verdict's code, not by making the demo exit 0. `ci.yml:77-95` captures the exit
  status and fails unless it is exactly 1, with an error message naming what each
  other code would mean; `|| true` was rejected explicitly because it would hide
  both a crash (3) and an accidental GO (0). The same commit switched the job from
  `pip install -e .` to installing the built wheel, so it also became the check
  that catches packaging omissions. This bullet stayed in the open list for the
  rest of the build and was still there on 2026-08-13, which is the reason this
  section now exists.

## Known gaps entering Session 1 (historical)

All four are closed. Left in place with their closures, for the same reason as
the section above.

- `src/model_migration_kit/__init__.py` does not exist yet. Write it last, as in the
  previous project, once there is a surface to re-export.
  **Closed 2026-08-13 in `04f4bd4`** — 47 lines, `__all__` empty by decision, and
  since `3be5101` it is also the single source of `__version__`.
- The `migkit` console script points at `model_migration_kit.cli:main`, which does not
  exist — `pip install -e .` succeeds anyway, but running `migkit` will fail
  until Session 3.
  **Closed 2026-08-13 in `79d3147`.** `cli.py` is 741 lines and `migkit demo`
  produces a report; `verify_release.py`'s `console-script` check confirms the
  entry point names a module the wheel actually ships.
- No tests exist. The acceptance contract is section 3 of the build plan; treat
  it as the checklist.
  **Closed across Sessions 1–4** — seven test files, 734 cases, each written by an
  agent that did not write the module under test.
- opik-rigor's report objects are `dict[str, Any]`, so comparison code will be
  reading string keys. Known friction, already on rigor's 0.2 roadmap.
  **Still true and still deferred**, which is why `pyproject.toml` keeps the
  `<0.2` upper bound: rigor's 0.2 is where that surface moves.

## Found on 2026-08-21, recorded rather than fixed

0.1.0 and 0.1.1 both shipped today. Everything below was found in the hours after
the first upload, by eight agents and a second session, and then deliberately
left alone: the work had become an audit of the release gate on a package with no
users, and the governing plan wanted the next hour spent elsewhere. Recorded here
because the reasoning is the perishable part — the branches keep the code.

### The gate's twine row can block a release it should pass

`check_twine` counts lines ending in `PASSED`. twine 7.0.0 has three verdict
spellings, at `twine/commands/check.py:146-159`: `PASSED`, `PASSED with warnings`
and `FAILED due to warnings`. Only the first ends in the word, so a long
description that renders with a docutils warning — exactly what editing the
README invites — counts zero passes and blocks the release with
`expected PASSED twice, saw it 0 time(s)`, a diagnosis naming neither the
artifact nor the warning. Latent today: 0.1.1's README renders clean.

The shape of the fix is to read the verdict token after the final `": "` and
treat `PASSED`/`PASSED with warnings` as a pass and `FAILED*` as a fail, rather
than testing the end of the line.

### …and its three tests cannot catch that, which is why it survived

`test_a_colourised_twine_pass_is_still_counted` and its two neighbours call
`vr.plain_lines(...)` and then **re-implement** the assertion
(`sum(1 for line in lines if line.strip().endswith("PASSED"))`). None of them
calls `check_twine`. A morning was spent inside that function fixing the ANSI
half of the same defect and the wording half went unseen, because the tests
covering it were testing a helper. A single test that calls `check_twine` with a
stubbed subprocess would be worth more than all three.

The general lesson, which applies past this row: a test that reimplements the
logic it is checking passes whenever the two implementations agree, including
when both are wrong.

### `check_console_script` resolves without comparing

It reads `entry_points.txt` out of the wheel and proves the target module ships,
which is real. It never compares that target against the one `pyproject.toml`
declares, so a wheel whose entry point has drifted from the source of truth is
invisible to it. opik-rigor's `check_entry_points` is the same family and worse —
it renders the mismatch into its evidence line and then passes.

### Four ported checks, written and shelved

Each is a branch off `a282c7f` with its own worktree; none is merged, and all
four need a rebase onto `c46dfca`. Each found something while being written,
which is the part worth keeping:

- `port/readme-paths` (`bef478b`, `mk-wt-readme-paths`) — found five relative
  links that 404 from the project page, two of them the badges on lines 4-5.
  Fixed independently in `b82317b` and shipped in 0.1.1. Its author declined to
  port rigor's third clause (every path-shaped argument must be in the wheel):
  it fires on 14 of migkit's paths and ~13 are false positives — output files
  the tool writes, a deliberate `./does-not-exist.jsonl` error demo, `$VAR`
  paths, and fixtures the README already says in bold need the repository.
- `port/readme-symbols` (`a56b849`, `mk-wt-readme-symbols`) — rigor anchors
  `from X import Y` to the start of a line; migkit's README writes every example
  as a `python -c` one-liner, so a faithful transplant finds zero symbols and
  reports PASS. A check that cannot fail, occupying the slot where a real one
  goes.
- `port/exports-importable` (`26793fc`, `mk-wt-exports`) — rigor's
  `^__all__\s*=\s*\[` does not match this package's annotated
  `__all__: list[str] = [`. Ported verbatim it returns `None`, skips its own
  staleness comparison, and reports a stale wheel as healthy.
- `port/wheel-annotations` (`350d71e`, `mk-wt-annotations`) — deferred for a
  reason worth remembering: it returns SKIPPED wherever mypy is absent, mypy is
  in neither `.venv` nor any workflow, and this gate scores a skip as exit 2.
  Merging it would block every release until mypy is wired into `publish.yml`.

Two of the four are the same defect in different clothes: a check ported across
sibling repos can be *syntactically* fine and *semantically* dead, and it reports
PASS while dead.

### The six mypy errors are all benign

`mypy --strict` against the built wheel reports six, and none is a bug. The three
`Argument 1 to "float" has incompatible type "float | None"` at
`comparison.py:1313,1314,1377` all sit behind `_is_number` (`comparison.py:1388`),
which is stricter than the annotation — it also rejects `bool`, NaN and
infinities. `None` is unreachable at runtime, verified by calling the wheel's own
functions, not by reading them. The other three are a lambda with an early-binding
default that mypy cannot match to a zero-arity signature, and `list` invariance
over an `Any`. Annotating `_is_number` as `TypeGuard[float]` retires three of the
six in one line and documents an invariant a future editor of the verdict path
could otherwise break silently.

### Stale prose inside the gate

`check_demo_data_importable`'s docstring says `model_migration_kit` "has no
`__init__.py` yet, so it is a namespace package". The file exists now, and its own
docstring records that it was added to stop the package being a namespace package
after a near-miss. The isolation the check performs is still right; its stated
reason is not.

### Two README defects that 0.1.1 did not fix

- The Quickstart at line 62 reads `.venv\Scripts\python.exe -m pip install <checkout>`.
  The package is on PyPI and the Quickstart still never names it.
- The transcripts at lines 323 and 336 quote `AdapterError`. That class is not in
  `model_migration_kit.errors` (which has `ArtifactError`, `ConfigError`,
  `DependencyContractError`, `GoldenSetError`, `HeaderlessArtifactError`,
  `JudgeConfigError`, `JudgeReliabilityError`, `MigrationKitError`, `ReportError`).
  It may be opik-rigor's, since `AnthropicAdapter` lives there — establishing
  which needs a real reproduction, and guessing would make a README whose opening
  promise is that every block was executed and pasted less true, not more.

### What the release gate could not see, and a human could

0.1.0's long description told every visitor `**It is not published yet.**` and
`So pip install model-migration-kit does not work today`, with pasted 404s as
evidence. Every word was true when written and all of it became false at the
moment the upload carrying it finished. Sixteen checks passed on that artifact.
The gate reads the README for commands that exist and paths that resolve; nothing
reads it for claims that publishing itself falsifies. 0.1.1 exists only to replace
that page, because a long description is frozen at upload.

## R34.3's rendering — the series-scope provenance claim, on both surfaces (2026-08-24)

Branch `chunk/provenance-timeline`, cut at `981514e`. Closes the first row of
R40.1: *"R34.3's rendering — the series-scope provenance claim in the timeline
section"*, which C18's fix pass computed and deliberately did not render because
C14c was editing the template at the time.

**What shipped.** `Provenance.timeline_sentence`, one property, printed by both
renderers: the HTML puts it under the run-history chart between the chart's prose
and R33.2's lineage block, and `render_terminal` prints it as its own line before
the closing paragraph. Two files touched: `src/model_migration_kit/report.py` and
`tests/test_report.py` (13 new tests, section 41). **2265 passed, 1 xfailed**;
`scripts/check_merge.py` green on all seven.

**The counts, and the trap.** `scripted_comparisons` and
`unrecorded_comparisons` **overlap and are not a partition** — a `Fake*` baseline
beside a candidate that recorded nothing is counted in both — so the sound
denominator for a claim about what was checked is
`comparisons - unrecorded_comparisons`, never `comparisons - scripted_comparisons`.
The cleanliness clause ("and none of them names a Fake adapter") is additionally
guarded on `scripted_comparisons == 0`, because otherwise the `Fake*` may be
sitting on one of the comparisons that *did* record both sides and this class
carries no scripted-among-named count to say so. That counter is R37.6's, still
open in R40.1, and this chunk does not build it.

**Why it is worth a page at all.** The document that needed it is the one with
**no band**: a real headline over a history whose payloads record no adapter
leaves `state` at `PROVENANCE_RECORDED` (R34.3 keeps it there), so
`Provenance._counted` never runs and `unrecorded_comparisons` was computed and
shown to nobody. Rendered, that document now says, identically in HTML and in the
terminal:

> 2 of the 4 comparisons in this run history record an adapter on both sides, and
> none of them names a Fake adapter; the other 2 record no adapter on at least
> one side, and this run history cannot say whether they were scripted.

**Two things the next agent should know.**

1. **`render_terminal` has no timeline section**, and did not grow one. C14c's
   lineage block (R33.2) is HTML-only, and nothing in the plan rules on that gap.
   This sentence is printed on the terminal as a bare line because R29.2 item 3
   requires both surfaces to say the same words; if a terminal run-history section
   is ever built, the sentence should move into it rather than be duplicated.
2. **The scope word is "in this run history", not "drawn on this chart".** The
   band says "in this document" and R34.3 requires every provenance sentence to
   name its own scope, but "this chart" is false on the surface that draws no
   chart — a disclosure pointing at a picture that is not there is R29.1's shape.
   The whole sentence therefore names the run history and nothing else; the
   speaker of the disowning clause is "this run history cannot say", not "this
   document", so the two sentences never borrow each other's scope word.

**Still open, and untouched here:** R37.6's `scripted_among_named` and the
`_counted_paragraph` sentence in the methodology appendix that still says *"the
other 2 do not"* about comparisons that recorded no adapter. The appendix and this
sentence now disagree in reach on the same document; closing that is R37.6's
chunk, not this one.
