"""What the report reader must not do with an evidence log it did not write.

An evidence log is *designed* to be shared: ``report.py``'s own docstring says the
happy path is a log rendered later, on another machine, by somebody who was not
there. That makes every string inside it -- recorded paths, model ids, adapter
names, judge notes, thresholds -- input from outside the trust boundary, arriving
on a reviewer's workstation. This file pins the four places that stopped being
true of the reader, each one demonstrated against the build before it was fixed:

1. Recorded paths drove ``open()`` with no constraint at all. A recorded
   ``\\\\192.0.2.111\\share\\x.jsonl`` blocked for 21 seconds attempting an
   outbound SMB connection, which on Windows hands the reviewer's NTLMv2 hash to
   whoever named the host; an absolute path read and parsed ``C:\\Windows\\win.ini``.
   The refusal is asserted to happen *before any loader is reached*, which is both
   the property that matters and the reason this suite stays offline: no test here
   ever attempts a connection.
2. ``render_terminal`` handed raw strings to rich, which parses console markup. A
   ``model_id`` of ``fake-cand-v1[/]`` raised ``MarkupError`` and cost the user the
   ``--html`` file they asked for; ``[bold red]FAKE CLEARED[/bold red]`` rendered as
   styled text. ANSI escapes passed through every site, including the ones already
   wrapped in ``Text``.
3. ``external_urls`` missed ``<meta http-equiv=refresh>`` and inline ``on*``
   handlers -- unreachable from data, reachable from one template edit.
4. ``judged_path_for`` did not slug its fallback stem.

Everything here is offline, deterministic and keyless. Artifacts are written byte
by byte, timestamps are injected, and no test touches the network or the clock.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from model_migration_kit.contracts import ARTIFACT_SCHEMA_VERSION
from model_migration_kit.errors import ReportError
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgedArtifact, judged_path_for
from model_migration_kit.report import ReportModel, external_urls, render_terminal
from model_migration_kit.runner import RunArtifact

NOW = "2026-08-13T00:00:00.000000+00:00"
TS = "2026-08-13T00:00:00.000000+00:00"
ITEMS = ("item-01", "item-02")
BASELINE_MODEL = "baseline-v1"
CANDIDATE_MODEL = "candidate-v1"
JUDGE = "accuracy"

#: The address the original demonstration used: TEST-NET-3, non-routable by
#: definition (RFC 5737), so even a regression that reached the network could not
#: reach anything. No test in this file lets it get that far.
UNC = r"\\192.0.2.111\share\x.jsonl"


# --------------------------------------------------------------------------- #
# fixtures written by hand, so nothing here is produced by the code under test
# --------------------------------------------------------------------------- #


def _write_goldenset(path: Path) -> GoldenSet:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps({"id": item, "input": f"question for {item}"}) + "\n" for item in ITEMS
        ),
        encoding="utf-8",
        newline="\n",
    )
    return GoldenSet.load(path)


def _write_run(path: Path, *, model_id: str, goldenset_hash: str, goldenset_path: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[dict[str, Any]] = [
        {
            "record": "header",
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "model_id": model_id,
            "goldenset_hash": goldenset_hash,
            "goldenset_path": goldenset_path,
            "n_per_item": 1,
            "created": TS,
            "adapter": "FakeAdapter",
            "notes": {"goldenset_items": len(ITEMS)},
        }
    ]
    for item in ITEMS:
        lines.append(
            {
                "record": "completion",
                "item_id": item,
                "sample_index": 0,
                "output": f"{model_id} answers {item}",
                "duration": 0.1,
                "error": None,
                "error_type": None,
                "tokens_in": None,
                "tokens_out": None,
            }
        )
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _write_judged(path: Path, *, model_id: str, goldenset_hash: str, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[dict[str, Any]] = [
        {
            "record": "header",
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "model_id": model_id,
            "goldenset_hash": goldenset_hash,
            "judges_hash": "judges-hash",
            "judges": [
                {
                    "name": JUDGE,
                    "model": "fake-judge-v1",
                    "adapter_class": "FakeAdapter",
                    "rubric_hash": "rubric-hash",
                }
            ],
            "n_per_item": 1,
            "source": source,
            "created": TS,
            "notes": {},
        }
    ]
    for item in ITEMS:
        lines.append(
            {
                "record": "verdict",
                "judge": JUDGE,
                "item_id": item,
                "sample_index": 0,
                "passed": True,
                "score": 5.0,
                "imputed": False,
                "parse_failure": False,
                "reason": f"graded {item}",
                "error": None,
            }
        )
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _payload(
    *,
    baseline_artifact: str = "",
    baseline_judged: str = "",
    candidate_artifact: str = "",
    candidate_judged: str = "",
    goldenset_path: str = "",
    goldenset_hash: str = "",
    baseline_model: str = BASELINE_MODEL,
    candidate_model: str = CANDIDATE_MODEL,
    judges: Sequence[Mapping[str, Any]] = (),
    warnings: Sequence[str] = (),
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "goldenset_path": goldenset_path,
        "goldenset_hash": goldenset_hash,
        "judges_hash": "judges-hash",
        "config_hash": "config-hash",
        "config_path": "migkit.toml",
        "n_per_item": 1,
        "baseline": {
            "model_id": baseline_model,
            "adapter": "FakeAdapter",
            "artifact": baseline_artifact,
            "judged_artifact": baseline_judged,
            "n_per_item": 1,
        },
        "candidate": {
            "model_id": candidate_model,
            "adapter": "FakeAdapter",
            "artifact": candidate_artifact,
            "judged_artifact": candidate_judged,
            "n_per_item": 1,
        },
        "judges": list(judges),
        "warnings": list(warnings),
        "thresholds": dict(thresholds or {"pass_rate_floor": 0.9}),
        "flips": [],
        "gains": [],
        "unstable": [],
    }


def _write_evidence(path: Path, payload: Mapping[str, Any], *, verdict: str = "NO-GO") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"event_type": "migkit.comparison", "payload": dict(payload), "schema_version": 1,
         "ts": TS},
        {
            "event_type": "migkit.verdict",
            "payload": {"verdict": verdict, "reason": "a judge regressed", "decided_by": "rule"},
            "schema_version": 1,
            "ts": TS,
        },
    ]
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )
    return path


class _Loaders:
    """Records every load the reconstruction attempts, and performs none of them.

    Standing in for the three loaders is how "the refusal happens before any I/O"
    is asserted without a network call: a regression that followed the recorded
    UNC path would land here, be recorded, and fail the assertion in milliseconds
    rather than sitting on a 21-second SMB timeout. Deliberately not a
    filesystem-level patch -- these three are the only doors ``from_evidence``
    opens, and naming them keeps the assertion readable.
    """

    def __init__(self) -> None:
        self.paths: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Loaders:
        recorded = self.paths

        def loader(path: str | Path) -> Any:
            recorded.append(str(path))
            raise AssertionError(f"the reader opened {path!r}; it should not have got here")

        for owner in (RunArtifact, JudgedArtifact, GoldenSet):
            monkeypatch.setattr(owner, "load", staticmethod(loader), raising=True)
        return self


def _render(model: ReportModel, **console_kwargs: Any) -> str:
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        width=140,
        legacy_windows=False,
        **{"force_terminal": False, "no_color": True, **console_kwargs},
    )
    render_terminal(model, console=console)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 1. a recorded path may not name a file outside the evidence log's directory
# --------------------------------------------------------------------------- #


def test_a_recorded_unc_path_is_refused_before_any_loader_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The original demonstration: 21 seconds blocked on an SMB connection to a
    # host the log named, and on Windows that connection leaks an NTLMv2 hash.
    # What is asserted is the *ordering* -- nothing is opened at all -- which is
    # the property that makes the leak impossible rather than merely slow.
    loaders = _Loaders().install(monkeypatch)
    log = _write_evidence(tmp_path / "log" / "evidence.jsonl", _payload(baseline_judged=UNC))
    with pytest.raises(ReportError) as raised:
        ReportModel.from_evidence(log, now=NOW)
    assert loaders.paths == []
    message = str(raised.value)
    assert "UNC" in message or "share" in message
    assert "--artifact-dir" in message


@pytest.mark.parametrize(
    "recorded",
    [
        r"\\?\C:\Windows\win.ini",
        r"\\.\pipe\anything",
        "../../secret.jsonl",
        r"..\..\secret.jsonl",
        "sub/../../secret.jsonl",
    ],
)
def test_a_path_form_this_tool_never_writes_is_refused(
    recorded: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A device prefix or a '..' segment cannot come out of this build -- it
    # records the path it just wrote a file to -- so its presence means the log
    # was edited after the fact, and a change-control tool says so out loud.
    loaders = _Loaders().install(monkeypatch)
    log = _write_evidence(
        tmp_path / "log" / "evidence.jsonl", _payload(baseline_artifact=recorded)
    )
    with pytest.raises(ReportError):
        ReportModel.from_evidence(log, now=NOW)
    assert loaders.paths == []


def test_an_absolute_path_outside_the_log_directory_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ``C:\Windows\win.ini`` case, written portably: a real file that really
    # exists, outside the directory holding the log. Before the fix this was
    # opened and parsed. It degrades rather than raising, because an absolute
    # path that no longer resolves is the ordinary consequence of moving a log to
    # another machine -- the workflow the overrides exist for.
    outside = tmp_path / "elsewhere" / "secret.jsonl"
    outside.parent.mkdir(parents=True)
    outside.write_text('{"secret": true}\n', encoding="utf-8")
    loaders = _Loaders().install(monkeypatch)
    log = _write_evidence(
        tmp_path / "log" / "evidence.jsonl",
        _payload(baseline_artifact=str(outside), goldenset_path=str(outside)),
    )
    model = ReportModel.from_evidence(log, now=NOW)
    assert loaders.paths == []
    joined = " ".join(model.warnings)
    assert "--artifact-dir" in joined
    assert "--goldenset" in joined


def test_a_relative_recorded_path_resolves_against_the_log_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before the fix a relative recorded path was passed through verbatim and
    # resolved against whatever directory the reviewer happened to be standing
    # in, which is neither where the log is nor anything the log can vouch for.
    loaders = _Loaders().install(monkeypatch)
    log = _write_evidence(
        tmp_path / "log" / "evidence.jsonl", _payload(baseline_artifact="sub/baseline.jsonl")
    )
    cwd = tmp_path / "somewhere-else-entirely"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    with pytest.raises(AssertionError):
        ReportModel.from_evidence(log, now=NOW)
    assert loaders.paths == [str(tmp_path / "log" / "sub" / "baseline.jsonl")]


def test_an_override_still_rescues_a_path_the_log_records_badly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The override replaces the directory outright and the recorded string is
    # used for its filename alone, so a log full of unusable paths is still
    # renderable by somebody who knows where the files went. Without this, the
    # refusal above would have no escape hatch and the fix would be a wall.
    loaders = _Loaders().install(monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    log = _write_evidence(tmp_path / "log" / "evidence.jsonl", _payload(baseline_judged=UNC))
    with pytest.raises(AssertionError):
        ReportModel.from_evidence(log, artifact_dir=store, now=NOW)
    assert loaders.paths == [str(store / "x.jsonl")]


def test_the_cross_machine_workflow_still_reads_relocated_artifacts(tmp_path: Path) -> None:
    """The acceptance case: a log that moved, and the overrides that fix it.

    A guard rather than a regression test -- it passes before the change as well
    as after. Its job is to fail loudly if the confinement above is ever
    tightened into something the documented cross-machine workflow cannot get
    through.
    """
    store = tmp_path / "machine-b" / "store"
    golden = _write_goldenset(store / "goldenset.jsonl")
    baseline = _write_run(
        store / "baseline.jsonl",
        model_id=BASELINE_MODEL,
        goldenset_hash=golden.hash,
        goldenset_path=str(store / "goldenset.jsonl"),
    )
    _write_judged(
        store / "baseline.judged.jsonl",
        model_id=BASELINE_MODEL,
        goldenset_hash=golden.hash,
        source=str(baseline),
    )
    candidate = _write_run(
        store / "candidate.jsonl",
        model_id=CANDIDATE_MODEL,
        goldenset_hash=golden.hash,
        goldenset_path=str(store / "goldenset.jsonl"),
    )
    _write_judged(
        store / "candidate.judged.jsonl",
        model_id=CANDIDATE_MODEL,
        goldenset_hash=golden.hash,
        source=str(candidate),
    )
    # The paths machine A recorded. They are absolute, they are somewhere else,
    # and on machine B they do not exist -- exactly the shape of a shared log.
    machine_a = Path(tmp_path.anchor or "/") / "machine-a" / "runs"
    log = _write_evidence(
        tmp_path / "machine-b" / "inbox" / "evidence.jsonl",
        _payload(
            baseline_artifact=str(machine_a / "baseline.jsonl"),
            baseline_judged=str(machine_a / "baseline.judged.jsonl"),
            candidate_artifact=str(machine_a / "candidate.jsonl"),
            candidate_judged=str(machine_a / "candidate.judged.jsonl"),
            goldenset_path=str(machine_a / "goldenset.jsonl"),
            goldenset_hash=golden.hash,
        ),
    )

    without = ReportModel.from_evidence(log, now=NOW)
    assert without.baseline.completions == 0
    assert not without.goldenset["available"]

    with_overrides = ReportModel.from_evidence(
        log,
        artifact_dir=store,
        goldenset=store / "goldenset.jsonl",
        now=NOW,
    )
    assert with_overrides.baseline.completions == len(ITEMS)
    assert with_overrides.candidate.completions == len(ITEMS)
    assert with_overrides.goldenset["available"]
    assert with_overrides.goldenset["size"] == len(ITEMS)
    # The substitution is disclosed, which is the condition under which reading
    # different files than the ones recorded is allowed at all.
    assert with_overrides.artifact_dir == str(store)
    assert any("rather than the paths" in one for one in with_overrides.warnings)


def test_the_paths_the_log_records_beside_it_are_read_normally(tmp_path: Path) -> None:
    # The demo's shape, and ``migkit compare``'s: artifacts and evidence in one
    # directory, recorded absolutely. Confinement must not cost this case
    # anything, or the fix breaks every report the tool produces itself.
    root = tmp_path / "run"
    golden = _write_goldenset(root / "goldenset.jsonl")
    _write_run(
        root / "baseline.jsonl",
        model_id=BASELINE_MODEL,
        goldenset_hash=golden.hash,
        goldenset_path=str(root / "goldenset.jsonl"),
    )
    log = _write_evidence(
        root / "evidence.jsonl",
        _payload(
            baseline_artifact=str(root / "baseline.jsonl"),
            goldenset_path=str(root / "goldenset.jsonl"),
            goldenset_hash=golden.hash,
        ),
    )
    model = ReportModel.from_evidence(log, now=NOW)
    assert model.baseline.completions == len(ITEMS)
    assert model.goldenset["available"]


# --------------------------------------------------------------------------- #
# 2. no evidence-derived string reaches rich as a str
# --------------------------------------------------------------------------- #


def _model(tmp_path: Path, **payload_kwargs: Any) -> ReportModel:
    log = _write_evidence(tmp_path / "log" / "evidence.jsonl", _payload(**payload_kwargs))
    return ReportModel.from_evidence(log, now=NOW)


def test_a_model_id_with_broken_markup_does_not_stop_the_render(tmp_path: Path) -> None:
    # ``rich.errors.MarkupError``, exit 3, and -- because cli.py renders the
    # terminal before the HTML -- no report file at all, even with --html passed.
    # A model id chosen by the thing under evaluation could deny the reviewer the
    # artifact they asked for.
    out = _render(_model(tmp_path, candidate_model="fake-cand-v1[/]"))
    assert "fake-cand-v1[/]" in out


@pytest.mark.parametrize(
    "hostile",
    [
        "[bold red]FAKE CLEARED[/bold red]",
        "[blink]urgent[/blink]",
        "[/]",
    ],
)
def test_markup_in_a_model_id_is_printed_as_the_characters_it_is(
    hostile: str, tmp_path: Path
) -> None:
    # The forgery case, and the worse one: well-formed markup did not crash, it
    # *worked* -- the brackets vanished and the attacker's words rendered as
    # styled text in a document whose entire claim is that a scripted model
    # cannot produce a clean-looking report.
    out = _render(_model(tmp_path, candidate_model=hostile))
    assert hostile in out


def test_a_link_in_a_model_id_does_not_become_a_terminal_hyperlink(tmp_path: Path) -> None:
    out = _render(
        _model(tmp_path, candidate_model="[link=https://evil.example]click[/link]"),
        force_terminal=True,
        no_color=False,
    )
    assert "\x1b]8;" not in out
    assert "[link=https://evil.example]click[/link]" in out


@pytest.mark.parametrize(
    ("field", "payload_kwargs"),
    [
        ("model id", {"candidate_model": "cand\x1b[2J\x1b[Hv1"}),
        ("warning", {"warnings": ["\x1b[2J\x1b[Hnothing to see"]}),
        ("threshold name", {"thresholds": {"pass_rate\x1b[2J\x1b[Hfloor": 0.9}}),
        ("golden-set path", {"goldenset_path": "gs\x1b[2J\x1b[H.jsonl"}),
    ],
)
def test_no_control_character_from_the_evidence_reaches_the_terminal(
    field: str, payload_kwargs: dict[str, Any], tmp_path: Path
) -> None:
    # ``Text()`` alone does not strip ESC -- verified -- so the sites that were
    # already wrapped were no safer than the ones that were not. ``\x1b[2J\x1b[H``
    # clears the screen and homes the cursor, which scrolls the closing VERDICT
    # line out of view: the one line a CI reader is promised will always be last.
    out = _render(_model(tmp_path, **payload_kwargs))
    assert "\x1b" not in out, f"an ESC from the {field} reached the terminal"
    assert out.rstrip().splitlines()[-1].strip().startswith("VERDICT:")


def test_a_judge_note_and_title_carry_no_markup_or_escapes(tmp_path: Path) -> None:
    judge = {
        "name": "accuracy[/]",
        "model_id": "judge\x1b[2Jv1",
        "note": "[bold]clean[/bold]\x1b[2J",
        "test_ran": "[red]none[/red]",
        "baseline": {"successes": 1, "n": 1},
        "candidate": {"successes": 1, "n": 1},
    }
    out = _render(_model(tmp_path, judges=[judge]))
    assert "\x1b" not in out
    assert "accuracy[/]" in out


# --------------------------------------------------------------------------- #
# 3. the two constructs the self-containment detector walked past
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fragment", "tag"),
    [
        ('<meta http-equiv="refresh" content="0;url=https://evil.example">', "meta"),
        ("<meta http-equiv='REFRESH' content='0; URL=//evil.example'>", "meta"),
        ('<div onclick="fetch(1)">x</div>', "div"),
        ('<img src="data:," onerror="fetch(1)">', "img"),
        ('<body onload="fetch(1)">', "body"),
    ],
)
def test_a_construct_that_fetches_without_a_fetching_attribute_is_detected(
    fragment: str, tag: str
) -> None:
    # Neither is reachable from data today: nothing in the payload becomes an
    # attribute name, and the template emits neither. The detector's job is the
    # *future template edit*, and both of these sailed through
    # ``assert_self_contained`` unremarked.
    violations = external_urls(fragment)
    assert len(violations) == 1, f"{fragment} produced {violations}"
    assert violations[0].tag == tag


@pytest.mark.parametrize(
    "fragment",
    [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        # A refresh with no url= reloads the local file. Pointless in a report,
        # but not a fetch, and a detector that reports non-fetches is a detector
        # somebody eventually switches off.
        '<meta http-equiv="refresh" content="30">',
        '<meta http-equiv="content-type" content="text/html; charset=utf-8">',
        '<div class="online">not an event handler</div>',
    ],
)
def test_the_new_rules_do_not_fire_on_the_documents_own_markup(fragment: str) -> None:
    assert external_urls(fragment) == ()


def test_the_rendered_report_still_passes_its_own_detector(tmp_path: Path) -> None:
    # The template carries two <meta> elements of its own; a rule that fired on
    # either would make every render raise.
    from model_migration_kit.report import assert_self_contained, render_html_string

    assert_self_contained(render_html_string(_model(tmp_path)))


# --------------------------------------------------------------------------- #
# 4. the judged-artifact filename fallback
# --------------------------------------------------------------------------- #


def _headless_artifact(model_id: str) -> RunArtifact:
    """A ``RunArtifact`` with no path, which no shipped flow produces.

    ``RunArtifact.load`` always sets ``path``, so the fallback branch is dead in
    the CLI and reachable only by a library caller constructing the dataclass
    directly. That is precisely why it was wrong for so long, and why it is worth
    a test: nothing else would ever notice.
    """
    from model_migration_kit.contracts import RunHeader

    return RunArtifact(
        header=RunHeader(
            model_id=model_id,
            goldenset_hash="0123456789abcdef0123456789abcdef",
            goldenset_path="goldenset.jsonl",
            n_per_item=1,
            created=TS,
        ),
        completions=(),
        path=None,
    )


@pytest.mark.parametrize("model_id", ["../../../evil", r"..\..\evil", "/etc/passwd"])
def test_the_judged_path_fallback_cannot_leave_the_output_directory(
    model_id: str, tmp_path: Path
) -> None:
    # ``artifact_path_for`` has always slugged the model id through
    # ``artifact_stem``; this fallback did not, so a model id of ``../../../evil``
    # wrote outside the directory the caller named.
    path = judged_path_for(_headless_artifact(model_id), tmp_path)
    assert path.parent == tmp_path
    assert ".." not in path.parts
    assert path.name.endswith(".judged.jsonl")


def test_the_judged_path_still_follows_the_run_artifacts_own_name(tmp_path: Path) -> None:
    artifact = _headless_artifact(BASELINE_MODEL)
    named = RunArtifact(
        header=artifact.header,
        completions=(),
        path=str(tmp_path / "runs" / "baseline-v1__abcdef.jsonl"),
    )
    assert judged_path_for(named, tmp_path) == tmp_path / "baseline-v1__abcdef.judged.jsonl"
