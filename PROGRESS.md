# PROGRESS

Running state of the migration-kit v0.1 build. Updated at the end of every
session. If you are picking this up cold, read [HANDOFF.md](HANDOFF.md) first —
it is written for exactly that — then [docs/build-plan.md](docs/build-plan.md),
which is the approved plan this build follows and was written before any code.

## Where the build stands

| Session | Scope | Status |
|---|---|---|
| 0 | Scaffold, license, CI, frozen contracts | **complete**, committed `45b6567` |
| 1 | Data path, offline: `goldenset.py`, `runner.py`, resumability | **complete** |
| 2 | Judgment and verdict: `judging.py`, `comparison.py` | contract frozen, code not started |
| 3 | Faces: `report.py`, `cli.py`, `migkit demo`, README | contract frozen, code not started |
| 4 | Release: name check, `__init__.py`, publish workflow, PyPI | contract frozen, not started |

Session 4 is not in the build plan, which defines three build sessions. It is the
release phase the plan defers *into* ("check the PyPI name in Phase 0 of
publishing, not now"), specified rather than improvised, because the sibling
project improvised it and paid for that.

## What exists

| Path | State |
|---|---|
| `pyproject.toml` | Apache-2.0, deps `opik-rigor>=0.1.0,<0.2`, `jinja2`, `rich`; `migkit` console script pointing at `migration_kit.cli:main` (not yet written) |
| `LICENSE`, `NOTICE` | Apache-2.0 full text, copyright filled in |
| `.gitattributes` | forces LF — load-bearing, see invariants |
| `.gitignore` | ignores `*.jsonl` outputs but whitelists `goldensets/**` and test fixtures |
| `.github/workflows/ci.yml` | py3.10–3.13 × Ubuntu + Windows, ruff, pytest, **plus a `demo` job** that runs `migkit demo` under a 120s timeout and uploads the HTML |
| `src/migration_kit/errors.py` | exception hierarchy — frozen, unfrozen once for `ReportError` (see decisions) |
| `src/migration_kit/contracts.py` | shared data shapes — frozen; `EVENT_COMPLETION` added after review |
| `src/migration_kit/goldenset.py` | strict JSONL loader, content + provenance hashes, `stats()` |
| `src/migration_kit/runner.py` | n draws per item via rigor's `sample`, resumable, append-only artifact |
| `tests/test_goldenset.py`, `tests/test_runner.py` | written by agents who did not write the modules |
| `docs/build-plan.md` | the approved plan, verbatim |
| `.venv` | created, `pip install -e ".[dev]"` done, imports verified |

Verified working: `opik-rigor 0.1.0` (installed **from PyPI**, not from the local
repo — migration-kit consumes the published artifact), `jinja2 3.1.6`,
`rich 15.0.0`, and `migration_kit.contracts` imports clean.

## Decisions made, and why

**`argparse`, not `click`.** The plan left this open pending review. argparse
handles subcommands adequately for three verbs and costs no dependency, and a
tool whose selling point is auditability is better with a smaller supply chain.
Revisit only if subcommand ergonomics genuinely suffer.

**`opik-rigor` comes from PyPI, pinned `>=0.1.0,<0.2`.** Not a path dependency to
the sibling repo. This makes migration-kit a real consumer of the published
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
`tomllib` with a `tomli` fallback; 3.11+ installs nothing extra.

**`errors.py` was unfrozen once, for `ReportError`.** Deliberately, and recorded
here because "frozen" means changes are decisions rather than drift. The CLI maps
exception types to exit codes, so "this evidence log contains no verdict" has to
be distinguishable from "the tool broke"; reusing the base class would have made
every unrelated failure look like a report failure at the one place it matters.

**The demo golden set lives in `src/migration_kit/data/`, not repo-root
`goldensets/`.** Two independent reviews found the same trap: the wheel packages
only `src/migration_kit`, and CI's demo job uses `pip install -e .`, which keeps
the repo root importable. A demo reading from `goldensets/` therefore passes CI
and every local test, and fails only for people who installed the wheel — who are
exactly the audience the definition of done is written about. `.gitignore` needed
a matching whitelist line, since `*.jsonl` was swallowing the file.

**v0.1 ships CLI-only, with an empty `__all__`.** The definition of done is
entirely a CLI story, and rigor's report objects are `dict[str, Any]` today with
typing scheduled for its 0.2 — anything re-exported now would be a compatibility
promise over a surface that is about to move.

**A `demo` CI job exists before the demo does.** The definition of done says a
stranger with no keys reads a report within two minutes. That is only true if
something checks it on every push, so the job is written now and will fail until
Session 3 delivers `migkit demo`.

## Invariants

1. **migration-kit imports opik-rigor's *public* API only.** No reaching into
   internals. If something needed is missing, that is a rigor roadmap item —
   record it in this file, work around it at the API surface, do not monkey-patch.
2. **The report renders from the evidence log, not from in-memory state.** A
   crashed run must still produce a partial report from disk.
3. **The suite is green with no credentials and no network.** The demo path uses
   `FakeAdapter`; anything needing a key is marked `requires_network`.
4. **Two artifacts are comparable only if their `goldenset_hash` and judge-config
   hash both match.** Comparing across either is an `ArtifactError`, not a
   warning.
5. **`REVIEW` is never silently converted to `GO`.** Underpowered means
   underpowered.
6. **A failed completion is kept, never dropped.** If model B times out on three
   items, that is part of the migration decision; discarding it quietly improves
   B's apparent quality.
7. **Exit codes 0/1/2/3 are the CI contract.** Changing them is a breaking change.

## Known gaps entering Session 2

- **A provider outage bakes into an artifact.** Every recorded draw counts against
  the resume budget, failures included, so a run that errored on all n draws
  because the provider was down looks complete: re-running samples nothing, and
  the model takes a NO-GO for its infrastructure's sake. The only v0.1 remedy is
  `fresh=True`, which discards the artifact. A `retry_failed` flag was started and
  deliberately backed out: re-drawing failures is not a small change but a
  weighting decision — retried items end up with more draws than others, and the
  per-item indices collide with the ones already recorded — and weighting belongs
  to `comparison.py`. Decide it there, with the statistics in view, rather than in
  the writer.
- **CI's `demo` job will fail the moment the demo works.** It runs
  `timeout 120 migkit demo` with no exit-code handling, and the definition of done
  says the demo shows a NO-GO verdict, which is exit 1. The job was written before
  the demo on purpose; Session 3 must amend it to expect the verdict's code, not
  to make the demo exit 0.
- **Invariant 1 is violated in one declared place, and it was self-inflicted.**
  `judging.py` imports `SCORE_MIN`, `SCORE_MAX` and `hash_rubric_file` from the
  `opik_rigor.judge` submodule. None is in rigor's `__all__`; `hash_rubric_file`
  is undocumented, and `SCORE_MIN` appears only in a CHANGELOG bullet saying the
  range will change. A mechanical audit found it — nobody reading the code did,
  including the author of the invariant. It is kept rather than worked around
  because both alternatives are worse: re-deriving the score range means a
  hard-coded `1.0` that goes silently wrong the day the scale moves, and hashing
  a rubric differently from rigor means the two disagree about whether the
  instrument changed. Recorded as rigor roadmap item 10 (commit `4bb7935`), to be
  closed by exporting all three from rigor's package root in 0.2. Until then this
  is a *declared* dependency on unpromised names, not a hidden one — and the
  reason it matters is that if rigor renames any of them, this project's pinned CI
  stays green while its users hit the break on upgrade.
- **`tokens_in`/`tokens_out` are always `None`.** rigor's `Adapter` protocol is
  `model_id` plus `complete(str) -> str` and exposes no usage data, so a cost gate
  cannot be built without reaching past the seam. Recorded as roadmap item 9 in
  opik-rigor's PROGRESS.md (commit `601b40b`), per invariant 1.
- **`artifact_stem` flattens `/`, `:` and space to `-`**, so `gpt/4o` and `gpt-4o`
  resolve to one filename. Both the resume path and `fresh=True` now check header
  identity before touching the file, so a collision is loud rather than
  destructive — but the stem still cannot distinguish them, and on Windows
  `GPT-4o` and `gpt-4o` collide as well. Including a short digest of the full model
  id in the stem would fix it and changes a frozen contract; deferred, not solved.

## Known gaps entering Session 1 (historical)

- `src/migration_kit/__init__.py` does not exist yet. Write it last, as in the
  previous project, once there is a surface to re-export.
- The `migkit` console script points at `migration_kit.cli:main`, which does not
  exist — `pip install -e .` succeeds anyway, but running `migkit` will fail
  until Session 3.
- No tests exist. The acceptance contract is section 3 of the build plan; treat
  it as the checklist.
- opik-rigor's report objects are `dict[str, Any]`, so comparison code will be
  reading string keys. Known friction, already on rigor's 0.2 roadmap.
