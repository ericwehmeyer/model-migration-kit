"""The command line: four verbs, four exit codes, and one way to produce them.

``argparse`` rather than ``click``, per the decision recorded in PROGRESS.md: three
verbs plus a demo do not need a dependency, and a tool whose selling point is
auditability is better with a smaller supply chain.

Two rules in this module are the CI contract rather than implementation detail.

**Every exit code comes from :meth:`contracts.Verdict.exit_code`.** There is no
integer exit literal anywhere below. A second copy of the 0/1/2/3 table is a second
thing to forget to update, and the failure mode of forgetting is a pipeline that
gates on a number that no longer means what it did. The mapping this file relies on
is exactly the one the README publishes: GO is 0, NO-GO is 1, REVIEW is 2, and
every error -- including a report whose evidence never recorded a verdict, and a
verdict string this tool does not recognise -- is 3.

**Nothing infers a verdict from an exception.** A ``RegressionError`` reaching this
module is a bug in ``comparison.py``, not a NO-GO: the verdict is read from the
evidence record and from nowhere else. Mapping a statistical exception onto exit 1
would produce a "the migration is unsafe" signal from a tool that had broken, which
is indistinguishable to CI from the real finding.

Streams follow §3.4 of the contract: the report goes to stdout, progress and errors
to stderr, so ``migkit report x.jsonl > report.txt`` captures the document and still
shows progress. ``--quiet`` silences progress and the terminal tables, never the
final verdict line and never an error.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import shutil
import sys
import tempfile
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from opik_rigor import (
    Adapter,
    AdapterError,
    AnthropicAdapter,
    EvidenceLog,
    FakeAdapter,
    OpenAICompatAdapter,
    RigorError,
    SampleTimeout,
)
from opik_rigor import __version__ as RIGOR_VERSION

from . import demo as demo_module
from .comparison import compare
from .contracts import Verdict, hash_file
from .errors import ArtifactError, ConfigError, JudgeConfigError, MigrationKitError
from .goldenset import GoldenSet
from .judging import JudgeConfig, JudgeSpec, judge_artifact
from .report import ReportModel, render_html, render_terminal
from .runner import DEFAULT_N, RunArtifact, run_goldenset

try:  # tomllib is 3.11+; the floor is 3.10 and CI actually runs it.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 in CI
    import tomli as tomllib  # type: ignore[no-redef]

PROG = "migkit"

#: The four codes, named once. Reading them off ``Verdict`` rather than writing the
#: numbers is the whole of §3.2: there is exactly one table, it lives in
#: ``contracts.py``, and this module is a consumer of it like every other.
EXIT_OK = Verdict.exit_code(Verdict.GO)
EXIT_ERROR = Verdict.exit_code(Verdict.ERROR)

#: Artifacts and the evidence log default under ``./.migkit/`` -- already
#: git-ignored. The HTML has no default write outside the demo: a tool that
#: silently drops files in the working directory is a tool people run once.
DEFAULT_DIR = Path(".migkit")
DEFAULT_EVIDENCE = DEFAULT_DIR / "evidence.jsonl"
EVIDENCE_BASENAME = "evidence.jsonl"

#: The demo is the exception, because its whole job is to leave something to open,
#: and the CI job already names this path.
DEMO_HTML = Path("migkit-demo-report.html")

ADAPTER_KINDS = ("fake", "anthropic", "openai-compat")

NO_VERDICT = "NO VERDICT"

#: Caught at the ``main`` boundary and printed as one stderr line, no traceback.
#: ``AdapterError`` and ``SampleTimeout`` are named individually because **they do
#: not inherit from RigorError** -- both subclass ``Exception`` directly, verified
#: from their MROs against the installed 0.1.0. A handler that caught only
#: ``RigorError`` would let a provider failure escape as an unhandled traceback,
#: which is the single mistake this tuple exists to prevent. ``OSError`` covers an
#: unreadable path or a full disk, and ``ValueError`` the argument validation the
#: library modules do at their own boundary.
EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    MigrationKitError,
    RigorError,
    AdapterError,
    SampleTimeout,
    OSError,
    ValueError,
)


# --------------------------------------------------------------------------- #
# configuration: --config, else ./migkit.toml, else built-in defaults
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Settings:
    """``[run]`` and ``[report]``, with the file each value came from.

    Thresholds are deliberately absent. They live in the judge config, are loaded
    by ``judging.JudgeConfig``, and are recorded into the evidence log by
    ``compare`` -- one file, one hash, one place a reader can tie a gate back to
    version control. Holding a second copy here would create exactly the two-hash
    reconciliation problem §4 of the contract exists to prevent.
    """

    n: int = DEFAULT_N
    concurrency: int = 4
    timeout: float = 60.0
    max_output_chars: int = 4000
    path: str = ""
    sources: Mapping[str, str] = field(default_factory=dict)

    def source(self, key: str) -> str:
        """Where a value came from, in the words the report echoes beside it."""
        return self.sources.get(key, "default")


_RUN_KEYS = frozenset({"n", "concurrency", "timeout"})
_REPORT_KEYS = frozenset({"max_output_chars"})


def load_settings(path: str | Path | None) -> Settings:
    """Discovery is ``--config``, else ``./migkit.toml``, else built-in defaults.

    No walking up parent directories and no ``~/.migkit.toml``: a setting inherited
    from a home directory makes the same command behave differently on two machines,
    and the only trace would be an echo nobody compares.
    """
    if path is not None:
        target = Path(path)
        if not target.is_file():
            raise ConfigError(f"no config file at {target}")
    else:
        target = Path("migkit.toml")
        if not target.is_file():
            return Settings()
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target} is not valid TOML: {exc}") from exc

    # 'judge' and 'thresholds' belong to judging.JudgeConfig, which reads the same
    # file. They are skipped rather than rejected precisely because §4 asks for one
    # file holding all four sections.
    unknown = sorted(set(raw) - {"judge", "thresholds", "run", "report"})
    if unknown:
        raise ConfigError(
            f"{target}: unknown top-level key(s) {', '.join(repr(k) for k in unknown)}; "
            f"expected 'judge', 'thresholds', 'run' and 'report'"
        )
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for section, allowed in (("run", _RUN_KEYS), ("report", _REPORT_KEYS)):
        table = raw.get(section) or {}
        if not isinstance(table, dict):
            raise ConfigError(f"{target}: [{section}] must be a table")
        extra = sorted(set(table) - allowed)
        if extra:
            raise ConfigError(
                f"{target}: [{section}] has unknown key(s) "
                f"{', '.join(repr(k) for k in extra)}; allowed: {', '.join(sorted(allowed))}. "
                f"An unknown key is more likely a typo leaving a setting at its default "
                f"than a setting worth ignoring."
            )
        for key, value in table.items():
            values[key] = value
            sources[key] = str(target)

    settings = Settings(path=str(target), sources=sources, **values)
    _validate(settings, target)
    return settings


def _validate(settings: Settings, where: Path) -> None:
    """Out of range is a ``ConfigError``, never a clamp: a clamped setting is a
    silently different setting, and the operator is looking at the file that says
    otherwise."""
    checks: tuple[tuple[str, Any, Callable[[Any], bool], str], ...] = (
        ("n", settings.n, lambda v: _is_int(v) and v >= 1, "an integer >= 1"),
        (
            "concurrency",
            settings.concurrency,
            lambda v: _is_int(v) and v >= 1,
            "an integer >= 1",
        ),
        ("timeout", settings.timeout, lambda v: _is_number(v) and v > 0, "a number > 0"),
        (
            "max_output_chars",
            settings.max_output_chars,
            lambda v: _is_int(v) and v >= 1,
            "an integer >= 1",
        ),
    )
    for name, value, ok, expected in checks:
        if not ok(value):
            raise ConfigError(f"{where}: {name!r} must be {expected}, got {value!r}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --------------------------------------------------------------------------- #
# streams
# --------------------------------------------------------------------------- #


def _write(stream: TextIO, text: str) -> None:
    """Write one line, tolerating a reader that stopped reading.

    ``migkit report | head`` closes the pipe mid-document. The reader chose to
    stop, so the write is dropped and the exit code is left alone -- turning that
    into an error would make a perfectly good report look like a failed one.
    """
    try:
        stream.write(text + "\n")
        stream.flush()
    except BrokenPipeError:
        pass


def _out(text: str) -> None:
    _write(sys.stdout, text)


def _err(text: str) -> None:
    _write(sys.stderr, text)


def _progress(args: argparse.Namespace) -> Callable[[str], None]:
    if getattr(args, "quiet", False):
        return lambda _message: None
    return lambda message: _err(f"{PROG}: {message}")


# --------------------------------------------------------------------------- #
# adapters
# --------------------------------------------------------------------------- #


def _wire_check_response(_prompt: str) -> str:
    """What ``--adapter fake`` answers with, and why it is not a hidden model.

    A fixed sentence, identical for every prompt. ``--adapter fake`` exists to
    check that a golden set loads, an artifact writes and a resume resumes without
    spending a credential; it measures nothing about any model, and the report says
    so on its own -- ``RunHeader.adapter`` records ``FakeAdapter``, which is what
    puts the red band above the verdict banner.
    """
    return (
        "This text was produced by a FakeAdapter for a wiring check. It is not a "
        "model response and says nothing about any model's quality."
    )


#: Which provider SDK each adapter kind needs at call time, and the extra that
#: installs it. rigor imports these lazily -- deliberately, so that ``import
#: opik_rigor.adapters`` works on a machine that has never installed a provider --
#: and raises ``AdapterError`` from inside the first sampling call. That is the
#: right place for *rigor* to raise and the wrong place for this tool to find out:
#: ``migkit compare`` samples nothing, but it grades both sides, so a missing SDK
#: surfaced after the baseline is already graded, minutes in.
#:
#: ``tests/test_cli.py`` asserts these names against ``PACKAGE`` in rigor's own
#: adapter modules, so the pairing is checked rather than copied.
SDK_FOR_ADAPTER: Mapping[str, str] = {"anthropic": "anthropic", "openai-compat": "openai"}
EXTRA_FOR_ADAPTER: Mapping[str, str] = {"anthropic": "anthropic", "openai-compat": "openai"}


def _require_sdk(kind: str) -> None:
    """Fail now, with the install line, if the adapter's SDK is not importable.

    ``find_spec`` rather than ``import``: the question is whether the package is
    installed, and importing a provider SDK to answer it costs a second or more and
    drags the whole client library into a process that may be about to exit 3.

    The check runs *before* the adapter is constructed, so a reader missing both
    the SDK and the credential is told about the SDK first. That ordering is
    deliberate: exporting a key cannot fix a missing package, but installing the
    package plus exporting a key fixes both, so the prerequisite that has to be
    satisfied first is the one named first.
    """
    package = SDK_FOR_ADAPTER.get(kind)
    if package is None or importlib.util.find_spec(package) is not None:
        return
    raise ConfigError(
        f"adapter {kind!r} needs the {package!r} package, which is not installed. "
        f"It is an optional dependency because the keyless paths -- `{PROG} demo` "
        f"and `--adapter fake` -- do not need it. Install it with: "
        f"pip install \"model-migration-kit[{EXTRA_FOR_ADAPTER[kind]}]\"  (or: "
        f"pip install {package})"
    )


def _model_adapter(kind: str, model_id: str) -> Adapter:
    _require_sdk(kind)
    if kind == "anthropic":
        return AnthropicAdapter(model_id)
    if kind == "openai-compat":
        return OpenAICompatAdapter(model_id)
    return FakeAdapter(model_id=model_id, responses=_wire_check_response)


def _judge_adapter(spec: JudgeSpec) -> Adapter:
    """Build one judge's adapter from what its ``[[judge]]`` table declares.

    ``adapter`` is required rather than inferred from the model string, for the
    reason ``judging.py`` gives at the same seam: inferring it would mean deciding
    from a substring which credential to spend.

    ``adapter = "fake"`` is refused here, and that refusal is the point. A fake
    *model* is disclosed by the report's red band, which reads the run artifact; a
    fake *judge* is not, so a scripted judge could hand real completions a clean
    bill of health with nothing in the document saying the grades were invented.
    The supported fake-judge path is ``migkit demo``, which supplies a judge that
    actually grades and labels every number it produces as scripted.

    That last sentence used to end the error message as *"Use `migkit demo` for the
    keyless path"*, and the remedy did not exist: ``demo`` took no golden set, so
    the advice was true for the bundled twelve items and false for the set the
    reader had in front of them -- which is the only set they were asking about.
    ``demo`` now takes ``--goldenset`` and ``--n``, and the message names them.
    """
    kind = spec.adapter.strip().lower()
    if not kind:
        raise JudgeConfigError(
            f"judge {spec.name!r} does not say which adapter to use. Add "
            f"adapter = \"anthropic\" or adapter = \"openai-compat\" to its "
            f"[[judge]] table: which credential a judge spends is not something "
            f"this tool will infer from a model string."
        )
    if kind == "fake":
        raise JudgeConfigError(
            f"judge {spec.name!r} declares adapter = \"fake\". A scripted judge "
            f"grading real completions produces numbers nothing in the report "
            f"marks as invented. Judging is a credentialed verb for that reason. "
            f"The keyless path over your own items is "
            f"`{PROG} demo --goldenset <your set> --n <draws>`, which scripts both "
            f"models as well as the judge and bands the report accordingly -- it "
            f"tells you whether your set and your n are big enough to decide "
            f"anything, not whether your candidate model is good."
        )
    if kind not in ADAPTER_KINDS:
        raise JudgeConfigError(
            f"judge {spec.name!r} declares adapter = {spec.adapter!r}; expected one "
            f"of {', '.join(ADAPTER_KINDS)}"
        )
    return _model_adapter(kind, spec.model)


# --------------------------------------------------------------------------- #
# rendering, shared by compare / report / demo
# --------------------------------------------------------------------------- #


def _render(
    evidence: Path,
    args: argparse.Namespace,
    *,
    html: str | Path | None,
    goldenset: str | Path | None = None,
    artifact_dir: str | Path | None = None,
    max_output_chars: int = 4000,
) -> int:
    """Build the report from the evidence log on disk and return its exit code.

    Every verb that ends in a verdict funnels through here, including ``compare``,
    which has the whole comparison in memory and hands over a path anyway. That is
    invariant 2 doing its job: a partial-render path that only runs after a crash is
    a path that has never run when you need it, so the happy path exercises the same
    reconstruction on every green run.
    """
    model = ReportModel.from_evidence(
        evidence,
        goldenset=goldenset,
        artifact_dir=artifact_dir,
        max_output_chars=max_output_chars,
    )
    quiet = bool(getattr(args, "quiet", False))
    if not getattr(args, "no_terminal", False) and not quiet:
        # A reader who piped into `head` stopped reading; that is their choice and
        # not a failed report, so the verdict line below still tries to write.
        with contextlib.suppress(BrokenPipeError):
            render_terminal(model)
    if html is not None:
        written = render_html(model, html)
        # Printed immediately before the verdict, because opening it is the next
        # thing the reader has to do.
        _out(str(Path(written).resolve()))
    # The terminal rendering ends with a verdict line of its own -- that is the last
    # line of the *document*. This one is the last line of the *process*, after the
    # file path, and it is printed even under --quiet and --no-terminal. A CI log
    # that scrolls past 200 lines of table still ends with the finding.
    verdict = model.verdict or NO_VERDICT
    code = Verdict.exit_code(model.verdict or Verdict.ERROR)
    _out(f"VERDICT: {verdict} (exit {code})")
    return code


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_run(args: argparse.Namespace) -> int:
    """Sample one model over a golden set. Produces no verdict, so it cannot fail
    with 1 or 2 -- its 0 means "the run completed", never "GO"."""
    settings = load_settings(args.config)
    say = _progress(args)
    goldenset = GoldenSet.load(args.goldenset)
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    evidence = EvidenceLog(Path(args.evidence) if args.evidence else DEFAULT_EVIDENCE)
    adapter = _model_adapter(args.adapter, args.model)
    # Precedence is CLI flag > config file > built-in default, and n carries its
    # source into the progress line. n is the one setting here that changes what is
    # measured rather than how fast it is measured, so "where did 5 come from" is a
    # question somebody reading a CI log will actually have.
    n, n_source = (args.n, "--n") if args.n is not None else (settings.n, settings.source("n"))
    concurrency = args.concurrency if args.concurrency is not None else settings.concurrency
    timeout = args.timeout if args.timeout is not None else settings.timeout
    say(
        f"{len(goldenset)} items x n={n} ({n_source}) against {args.model} "
        f"via {type(adapter).__name__}"
    )
    done = 0

    def on_item(item: Any, completions: tuple[Any, ...]) -> None:
        nonlocal done
        done += 1
        failed = sum(1 for one in completions if not one.ok)
        note = f", {failed} failed" if failed else ""
        say(f"[{done}/{len(goldenset)}] {item.id}: {len(completions)} draw(s){note}")

    artifact = run_goldenset(
        goldenset,
        adapter,
        out_dir=out_dir,
        artifact=args.artifact,
        n=n,
        concurrency=concurrency,
        timeout=timeout,
        fresh=args.fresh,
        evidence=evidence,
        on_item=on_item,
    )
    stats = artifact.stats()
    say(
        f"{stats['completions']} completion(s), {stats['failures']} failed, "
        f"{artifact.parts} part(s)"
    )
    _out(str(Path(artifact.path or "").resolve()))
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    """Judge both sides, compare them, record the evidence, then render from it."""
    settings = load_settings(args.config)
    say = _progress(args)
    baseline_run = RunArtifact.load(args.baseline)
    candidate_run = RunArtifact.load(args.candidate)
    goldenset = _goldenset_for(baseline_run)
    config = JudgeConfig.load(args.judges)
    evidence = EvidenceLog(Path(args.evidence) if args.evidence else DEFAULT_EVIDENCE)

    panel = config.build(evidence, _judge_adapter)
    say(f"judging with {', '.join(panel.named())}")
    judged = []
    for run in (baseline_run, candidate_run):
        say(f"grading {run.header.model_id}")
        judged.append(
            judge_artifact(
                run,
                goldenset,
                panel,
                evidence=evidence,
                out_dir=Path(run.path).parent if run.path else DEFAULT_DIR,
            )
        )
    say("comparing")
    compare(
        judged[0],
        judged[1],
        thresholds=config.thresholds,
        evidence=evidence,
        baseline_run=baseline_run,
        candidate_run=candidate_run,
        goldenset_path=goldenset.path,
        config_path=str(args.judges),
        config_hash=hash_file(args.judges),
    )
    return _render(
        Path(evidence.path),
        args,
        html=args.html,
        goldenset=goldenset.path,
        max_output_chars=settings.max_output_chars,
    )


def cmd_report(args: argparse.Namespace) -> int:
    """Re-render a report from an evidence log, on any machine, at any later date."""
    settings = load_settings(None)
    return _render(
        _evidence_path(args.evidence),
        args,
        html=args.html,
        goldenset=args.goldenset,
        artifact_dir=args.artifact_dir,
        max_output_chars=settings.max_output_chars,
    )


def cmd_demo(args: argparse.Namespace) -> int:
    """Two scripted models, a real judge, a real verdict, no credentials.

    The work directory is temporary and removed on the way out unless ``--keep``,
    and it is removed *after* the report is rendered: the renderer reads the run
    and judged artifacts by the paths the evidence log records, so tearing the
    directory down first would leave the demo rendering a partial report of itself.
    """
    say = _progress(args)
    named = args.work_dir is not None
    # A directory the operator named is never deleted, --keep or not: removing a
    # path someone typed is a different act from cleaning up one we invented.
    keep = bool(args.keep) or named
    work_dir = Path(args.work_dir) if named else Path(tempfile.mkdtemp(prefix="migkit-demo-"))
    try:
        result = demo_module.run_demo(
            work_dir, goldenset=args.goldenset, n=args.n, progress=say
        )
        if keep:
            say(f"artifacts kept in {work_dir.resolve()}")
        # No goldenset= or artifact_dir= override: the paths recorded in the
        # evidence log are the ones on disk, and an override would print a
        # provenance note about a substitution that did not happen.
        return _render(result.evidence, args, html=args.out)
    finally:
        if not keep:
            shutil.rmtree(work_dir, ignore_errors=True)


def _goldenset_for(run: RunArtifact) -> GoldenSet:
    """The golden set a run recorded, loaded and checked against its hash.

    ``compare`` needs the item inputs to judge, and the only defensible source is
    the file the run itself named. A mismatch is refused rather than warned about:
    grading today's inputs against last week's outputs produces an exhibit that is
    indistinguishable from a real one.
    """
    recorded = run.header.goldenset_path
    if not recorded:
        raise ArtifactError(
            f"the artifact at {run.path} records no golden-set path, so the inputs "
            f"its completions answered cannot be found. It was written by an older "
            f"version of this tool; re-run it."
        )
    goldenset = GoldenSet.load(recorded)
    if goldenset.hash != run.header.goldenset_hash:
        raise ArtifactError(
            f"the golden set at {recorded} has changed since {run.header.model_id} "
            f"was run ({goldenset.hash[:16]} now, {run.header.goldenset_hash[:16]} "
            f"then). Judging these completions against it would grade answers to "
            f"questions nobody asked."
        )
    return goldenset


def _evidence_path(value: str | Path) -> Path:
    """A directory argument resolves to ``<dir>/evidence.jsonl``.

    ``migkit report`` takes the evidence log, not a separate serialised comparison:
    under invariant 2 the comparison *is* the log plus the artifacts it names, and a
    second file would be a second source of truth.
    """
    target = Path(value)
    return target / EVIDENCE_BASENAME if target.is_dir() else target


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Statistically defensible go/no-go verdicts for LLM model migrations."
        ),
        epilog=(
            "Exit codes: 0 GO, 1 NO-GO, 2 REVIEW, 3 the tool could not produce a "
            "verdict. `run` produces no verdict, so its 0 means the run completed "
            "and never means GO -- a pipeline that gates on `run` gates on nothing."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {_tool_version()} (opik-rigor {RIGOR_VERSION})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="silence progress and the terminal tables; the verdict line and errors still print",
    )
    parser.add_argument(
        "--traceback",
        action="store_true",
        help="print a traceback for expected errors too (unexpected ones always print one)",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    run = subparsers.add_parser(
        "run",
        help="sample one model over a golden set (produces no verdict; exits 0 or 3)",
    )
    run.add_argument("--goldenset", required=True, help="JSONL golden set")
    run.add_argument("--model", required=True, help="the pinned provider model string")
    run.add_argument(
        "--adapter",
        choices=ADAPTER_KINDS,
        default="anthropic",
        help="which provider seam to use (default: anthropic)",
    )
    run.add_argument("--n", type=int, default=None, help="draws per item")
    run.add_argument("--concurrency", type=int, default=None, help="threads within one item")
    run.add_argument("--timeout", type=float, default=None, help="per-draw budget in seconds")
    run.add_argument("--artifact", default=None, help="explicit artifact path")
    run.add_argument("--out-dir", default=None, help=f"artifact directory (default: {DEFAULT_DIR})")
    run.add_argument(
        "--fresh",
        action="store_true",
        help="discard any existing artifact instead of resuming it",
    )
    run.add_argument("--evidence", default=None, help=f"evidence log (default: {DEFAULT_EVIDENCE})")
    run.add_argument(
        "--config", default=None, help="TOML config (default: ./migkit.toml if present)"
    )
    run.set_defaults(handler=cmd_run)

    compare_parser = subparsers.add_parser(
        "compare",
        help="judge two run artifacts, compare them, and report the verdict",
    )
    compare_parser.add_argument("--baseline", required=True, help="the run artifact migrated from")
    compare_parser.add_argument("--candidate", required=True, help="the run artifact migrated to")
    compare_parser.add_argument(
        "--judges",
        required=True,
        help="TOML holding [[judge]] and [thresholds]; its hash is recorded in the report",
    )
    compare_parser.add_argument("--config", default=None, help="TOML config for [run] and [report]")
    compare_parser.add_argument(
        "--evidence", default=None, help=f"evidence log (default: {DEFAULT_EVIDENCE})"
    )
    compare_parser.add_argument("--html", default=None, help="write the HTML report here")
    compare_parser.add_argument(
        "--no-terminal", action="store_true", help="skip the terminal rendering"
    )
    compare_parser.set_defaults(handler=cmd_compare)

    report = subparsers.add_parser(
        "report",
        help="re-render a report from an evidence log",
    )
    report.add_argument(
        "evidence",
        help=f"the evidence log, or a directory holding {EVIDENCE_BASENAME}",
    )
    report.add_argument("--html", default=None, help="write the HTML report here")
    report.add_argument(
        "--goldenset",
        default=None,
        help="override the recorded golden-set path (the override is printed in the report)",
    )
    report.add_argument(
        "--artifact-dir",
        default=None,
        help="override the directory the recorded artifacts are read from",
    )
    report.add_argument("--no-terminal", action="store_true", help="skip the terminal rendering")
    report.set_defaults(handler=cmd_report)

    demo = subparsers.add_parser(
        "demo",
        help="run the keyless, deterministic demo and write its report",
        description=(
            "Two scripted models, a scripted judge, a real verdict, no credentials. "
            "With --goldenset it runs your items instead of the bundled twelve -- "
            "which measures your set, not your models: whether it loads, what the "
            "flip list reads like with your ids in it, and whether --n draws over "
            "this many items is a powerful enough sample for 'no regression "
            "detected' to mean anything. The models and the judge stay scripted "
            "either way, and the report says so on every table."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    demo.add_argument("--out", default=str(DEMO_HTML), help=f"HTML report (default: {DEMO_HTML})")
    demo.add_argument(
        "--goldenset",
        default=None,
        help=(
            "run over your own golden set instead of the bundled one; the rubric and "
            "the thresholds stay the bundled ones, because the judge is scripted"
        ),
    )
    demo.add_argument(
        "--n",
        type=int,
        default=None,
        help=f"draws per item (default: {DEFAULT_N}); the power warning moves with it",
    )
    demo.add_argument(
        "--work-dir",
        default=None,
        help="where the artifacts go (default: a temporary directory, removed at exit)",
    )
    demo.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary work directory instead of removing it",
    )
    demo.add_argument("--no-terminal", action="store_true", help="skip the terminal rendering")
    demo.set_defaults(handler=cmd_demo)
    return parser


def _tool_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("model-migration-kit")
    except PackageNotFoundError:  # pragma: no cover - only when run from a bare checkout
        return "0+unknown"


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and *return* its exit code.

    Returning rather than calling ``sys.exit`` is what lets a unit test assert
    ``main([...]) == 1`` in-process; the console script wraps this in ``sys.exit``,
    and the suite asserts both, because a returned int and a process exit status are
    two different claims.
    """
    argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # --help and --version leave through here with a zero code. argparse's own
        # usage error is 2, which is REVIEW in this tool's contract -- a usage
        # mistake must not be reportable as "the sample was underpowered".
        return EXIT_OK if not exc.code else EXIT_ERROR

    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        # Deliberately not 128+SIGINT: four exit codes are the published CI
        # contract, and a fifth value appearing only on Ctrl-C would break the
        # promise that the code is always one of four.
        _err(
            f"{PROG}: interrupted; the artifact is valid and can be resumed with: "
            f"{_command_line(argv)}"
        )
        return EXIT_ERROR
    except EXPECTED_ERRORS as exc:
        if args.traceback:
            traceback.print_exc()
        _err(f"{PROG}: {type(exc).__name__}: {exc}")
        return EXIT_ERROR
    except Exception:
        # Unclassified: a KeyError or an AttributeError is a bug in this tool, and
        # for an unanticipated failure the traceback is the only diagnostic. It
        # prints regardless of --traceback, because suppressing it costs the bug
        # report and the exit code is 3 either way.
        traceback.print_exc()
        _err(
            f"{PROG}: unexpected internal error; the traceback above is the whole of "
            f"what we know. Please report it."
        )
        return EXIT_ERROR


def _command_line(argv: Sequence[str]) -> str:
    return " ".join([PROG, *argv])


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
