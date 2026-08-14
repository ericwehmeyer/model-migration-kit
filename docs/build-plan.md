# migration-kit — v0.1 Build Plan
## Model-migration regression testing built on opik-rigor

Working name: **migration-kit** (same rule as before: package name is one constant; check PyPI in Phase 0 of publishing, not now). One sentence: a CLI that answers "is it safe to move from model A to model B?" with a statistically defensible go/no-go verdict instead of vibes. It is opik-rigor's first real consumer — every statistical primitive is imported, none reimplemented. License Apache-2.0, personal account, personal repo.

The pitch that governs scope: every team running LLMs in production faces forced migrations (deprecations, price changes, provider switches). Today they eyeball a few outputs and ship. This tool makes the decision auditable: golden set in, two models compared under identical pinned judges, distribution-diff report out, exit code a CI gate can consume. SR 11-7 framing: model change management is model risk management — the report *is* the change-control evidence.

## 1. Architecture

Six modules and a CLI, one page each. Hard rules: migration-kit imports opik-rigor's public API only (no reaching into internals — if something's missing, that's a rigor roadmap item, recorded, not monkey-patched); the report is generated from the evidence log, not from in-memory state, so a crashed run can still render a partial report from disk.

**goldenset.py** — loader/validator for the golden set format: JSONL, each line {id, input, reference (optional), tags (optional)}. Versioned the rigor way: file hash embedded in every downstream artifact. Validation is strict and loud: duplicate ids, empty inputs, and malformed lines are errors, not warnings. A `stats()` method reports set size and tag distribution so the report can say what was actually tested.

**runner.py** — executes the golden set against one model via a rigor adapter: sample() per item (n configurable, default 5 — migration decisions need distributions per item, not single shots), concurrency and timeout passed through, every completion logged to the evidence log with model string, item id, latency, and (where the adapter exposes it) token counts. Output: a RunArtifact — JSONL on disk keyed by (model_id, goldenset_hash), resumable: re-running skips completed items unless --fresh. Resumability is a feature the report notes ("run completed in 2 parts") not hides.

**judging.py** — applies one or more PinnedJudges to every completion in a RunArtifact. Same judge instances across both models — enforced structurally: judges are constructed once from a config file (model string + rubric path each) and the config's hash lands in the report. A judge that fails to parse on >X% of items (default 5%) aborts the comparison with a clear error: an unreliable judge invalidates the whole exercise, and saying so is the product.

**comparison.py** — the analytical core, and deliberately thin because rigor does the math: per-judge pass-rate for A and B with Wilson intervals; assert_no_regression (Mann-Whitney) on score distributions; per-item flip analysis (items that pass on A and fail on B, listed by id — these are the reviewable artifacts a human actually reads); latency distribution comparison as a secondary table. Verdict logic, pre-decided: GO if no judge shows a significant regression and pass-rate lower bound on B clears the configured floor; NO-GO if any judge regresses significantly; REVIEW if underpowered (n too small for the configured confidence — the tool must say "collect more data" rather than pretend). REVIEW existing as a first-class verdict is the intellectual honesty feature; document it prominently.

**report.py** — renders from the evidence log to two formats: terminal (rich tables, verdict up top) and a single self-contained HTML file (no JS dependencies fetched at view time — it must open in an airgapped compliance review). Contents in order: verdict banner; what was compared (models, golden set hash + size, judge config hash, n per item, date); per-judge comparison tables with intervals; the flip list with expandable input/output pairs; methodology appendix auto-generated (which tests, why nonparametric, what REVIEW means) — the appendix is the SR 11-7 artifact.

**cli.py** — `migkit run --model <id> --goldenset <path>`, `migkit compare --baseline <artifact> --candidate <artifact> --judges <config>`, `migkit report <comparison>`. Exit codes: 0 GO, 1 NO-GO, 2 REVIEW, 3 error — documented as the CI contract. Also `migkit demo` which runs the whole flow on a bundled toy golden set with FakeAdapters — the ninety-second sizzle path, zero keys required.

**Config:** one TOML file for thresholds (pass-rate floor, alpha, n, judge-failure tolerance) with defaults that are defensible, not permissive. Every threshold echoes into the report so nobody can quietly loosen a gate without it showing in the evidence.

Dependencies: opik-rigor, click or argparse (decide in plan review — lean argparse unless subcommand ergonomics suffer), jinja2 for HTML, rich for terminal. Nothing else.

## 2. Build phases — three sessions, handoff between each

**Session 1 — Data path, offline.** goldenset.py, runner.py against FakeAdapter, resumability, evidence logging. Exit: a golden set runs end-to-end into a RunArtifact offline; malformed-set rejection tests pass; interrupted-run-resume test passes (kill mid-run, resume, verify no duplicate ids in artifact).

**Session 2 — Judgment and verdict.** judging.py, comparison.py, the verdict logic, and the calibration tests (below). Exit: two scripted FakeAdapter "models" with a known quality difference produce the correct verdict at adequate n and REVIEW at inadequate n; identical models produce GO with no false regression across seeded runs.

**Session 3 — Faces.** report.py both formats, cli.py, `migkit demo`, README with executed quickstart, the methodology appendix content. Exit: `migkit demo` runs in a clean venv in under two minutes and opens a readable HTML report; exit codes verified by a shell test; README quickstart executed not imagined.

Same ritual as before: /handoff → cold-start check → /clear per session. If Session 2's verdict logic balloons, split at the judging/comparison boundary.

## 3. Test inventory (acceptance contract)

Golden set: duplicate id rejection; empty input rejection; hash stability across load; tag stats correct.
Runner: n samples per item; resume skips completed, --fresh doesn't; timeout counts as item failure; evidence line per completion with model string present; artifact keyed correctly by (model, set-hash) so mixed artifacts can't be compared silently.
Judging: same-judge-instance enforcement (comparing artifacts judged under different judge-config hashes is an error); parse-failure tolerance aborts at threshold with the count in the message.
Comparison: known-different scripted models → NO-GO detected at n=20-per-item scale; identical seeded models → GO, no false alarm over repeated seeds; underpowered case → REVIEW, never GO; flip list exactly matches constructed flips; verdict logic table-tested against a matrix of (regression?, floor cleared?, powered?) combinations.
Report: HTML renders self-contained (no network fetch — test by parsing for external URLs); thresholds echoed match config; partial-artifact rendering works from a truncated evidence log.
CLI: exit codes 0/1/2/3 verified; demo path runs keyless.
Dogfooding clause (non-negotiable, learned last time): the suite gates one of its own stochastic components with rigor assertions.

## 4. Risks, pre-decided

opik-rigor gaps discovered mid-build → recorded as rigor roadmap items and worked around at the API surface; if truly blocking, pause migration-kit, ship a rigor point release, resume — the dependency direction stays clean. Verdict-logic bikeshedding → the (regression, floor, power) decision table in this plan is the spec; changes require editing the plan first. Report scope creep (dashboards, history, trends) → v0.1 is one comparison, one report; longitudinal tracking is the roadmap's headline item and probably the Opik-integration story. Demo credibility → the bundled demo uses FakeAdapters and says so loudly; a second documented path shows real-adapter usage for readers with keys.

## 5. Definition of done for v0.1

A stranger with no API keys runs `pip install`, then `migkit demo`, and within two minutes is reading an HTML report that shows a NO-GO verdict with confidence intervals and a flip list — and understands from the methodology appendix why the tool refused the migration. A stranger *with* keys can point it at two real models and a ten-item golden set and get the same artifact for their own decision. Exit codes make it a CI gate. Everything else — trend history, Opik experiment logging, cost-per-verdict, multi-judge weighting — is roadmap.
