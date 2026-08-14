# PROGRESS

Running state of the migration-kit v0.1 build. Updated at the end of every
session. If you are picking this up cold, read [HANDOFF.md](HANDOFF.md) first —
it is written for exactly that — then [docs/build-plan.md](docs/build-plan.md),
which is the approved plan this build follows and was written before any code.

## Where the build stands

| Session | Scope | Status |
|---|---|---|
| 0 | Scaffold, license, CI, frozen contracts | **complete** — not yet committed |
| 1 | Data path, offline: `goldenset.py`, `runner.py`, resumability | **not started** |
| 2 | Judgment and verdict: `judging.py`, `comparison.py` | not started |
| 3 | Faces: `report.py`, `cli.py`, `migkit demo`, README | not started |

**Nothing has been committed yet.** `git init` has run; every file is untracked.
The first commit should be the scaffold + contracts, before any module work.

## What exists

| Path | State |
|---|---|
| `pyproject.toml` | Apache-2.0, deps `opik-rigor>=0.1.0,<0.2`, `jinja2`, `rich`; `migkit` console script pointing at `migration_kit.cli:main` (not yet written) |
| `LICENSE`, `NOTICE` | Apache-2.0 full text, copyright filled in |
| `.gitattributes` | forces LF — load-bearing, see invariants |
| `.gitignore` | ignores `*.jsonl` outputs but whitelists `goldensets/**` and test fixtures |
| `.github/workflows/ci.yml` | py3.10–3.13 × Ubuntu + Windows, ruff, pytest, **plus a `demo` job** that runs `migkit demo` under a 120s timeout and uploads the HTML |
| `src/migration_kit/errors.py` | full exception hierarchy — **frozen** |
| `src/migration_kit/contracts.py` | shared data shapes — **frozen** |
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

## Known gaps entering Session 1

- `src/migration_kit/__init__.py` does not exist yet. Write it last, as in the
  previous project, once there is a surface to re-export.
- The `migkit` console script points at `migration_kit.cli:main`, which does not
  exist — `pip install -e .` succeeds anyway, but running `migkit` will fail
  until Session 3.
- No tests exist. The acceptance contract is section 3 of the build plan; treat
  it as the checklist.
- opik-rigor's report objects are `dict[str, Any]`, so comparison code will be
  reading string keys. Known friction, already on rigor's 0.2 roadmap.
