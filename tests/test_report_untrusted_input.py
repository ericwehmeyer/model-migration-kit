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
5. ``external_urls`` then failed in the other direction: two of its rules judged
   every attribute value by shape rather than by whether the browser dereferences
   the attribute holding it. A recorded verdict reading ``review: n was too small``
   matches ``scheme:``, so an evidence log could make ``render_html`` refuse to
   produce a document at all -- a denial-of-render driven by exactly the untrusted
   input the control exists to defend against, and a larger hole than the one it
   closed. Section 5 pins both halves: the four attribute families a browser
   provably never dereferences are inert, and every name-based rule still fires.

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
from model_migration_kit.report import (
    _NEVER_DEREFERENCED_RE,
    FETCHING_ATTRS,
    ReportModel,
    assert_self_contained,
    external_urls,
    render_html,
    render_html_string,
    render_terminal,
)
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


# --------------------------------------------------------------------------- #
# 5. the detector judged shape where it meant dereference
# --------------------------------------------------------------------------- #

#: A value that looks exactly like a fetch and is not one. Nothing dereferences a
#: ``data-`` attribute, and with ``<script>`` banned outright nothing in the
#: document can read this string back out and turn it into one. It is text.
INERT_SCHEME = "javascript:alert(1)"

#: The string that started this chunk. A verdict recorded in an evidence log,
#: which matches the scheme rule because ``review:`` is shaped like a scheme --
#: and that was enough to make the detector refuse the whole document. Untrusted
#: input that can delete the report is a worse hole than the one it closes.
RECORDED_VERDICT = "review: n was too small"

#: One unambiguous fetch, carried by every exemption test below as a live
#: control. "The scanner did not fire on the exempt attribute" is a sentence a
#: *deleted* scanner satisfies; "the scanner reported this and only this" is not.
#: Every assertion in this section that permits something also demands something.
REAL_FETCH = '<a href="https://evil.example/beacon">x</a>'


def _positions(html: str) -> tuple[tuple[str, str], ...]:
    """Every violation as ``(tag, attribute)``, in the order the scanner found them.

    Compared as a whole tuple rather than by count: an assertion that two
    violations came back is satisfied by the wrong two.
    """
    return tuple((one.tag, one.attribute) for one in external_urls(html))


def _rendered_with(tmp_path: Path, fragment: str) -> str:
    """The real report, with ``fragment`` spliced in after ``<main>``.

    The template emits no attribute carrying evidence-derived text *yet* -- the
    chart that will is a later chunk -- so a fixture is the only way to ask this
    question of a whole document today. It is asked of a whole document
    deliberately: the property is "the report renders and fetches nothing", and
    the fixture is the shape that chart is going to have.
    """
    html = render_html_string(_model(tmp_path))
    marker = "<main>"
    assert marker in html, "the template lost its <main>; this helper needs a new anchor"
    return html.replace(marker, marker + fragment, 1)


# -- the exemption: four families a browser provably never dereferences ------ #


def test_a_data_attribute_holding_a_scheme_is_inert_rather_than_a_violation(
    tmp_path: Path,
) -> None:
    """The chunk's first failing test, asserted on the document, not the scanner.

    Three claims, and the order matters. The document *renders*:
    ``assert_self_contained`` is the gate ``render_html`` runs on itself before
    writing a byte, so a raise here is a report that does not exist. Nothing
    *fetches*: no position in the finished document reaches off the machine. And
    no *script*: the exemption is sound only because nothing in the document can
    read a ``data-`` value and act on it, so that ban is asserted beside the
    exemption rather than trusted from a plan nobody will re-read.
    """
    document = _rendered_with(tmp_path, f'<rect data-verdict="{INERT_SCHEME}"></rect>')
    assert_self_contained(document)
    assert external_urls(document) == ()
    assert "<script" not in document.lower()
    # The control, and the reason this test is four lines rather than three:
    # every assertion above is satisfied by a scanner that has been deleted. The
    # same document with one real fetch in it must still be refused.
    with pytest.raises(ReportError):
        assert_self_contained(_rendered_with(tmp_path, '<img src="https://evil.example/x.png">'))


INERT_ATTRIBUTES = [
    f'data-verdict="{INERT_SCHEME}"',
    f'data-verdict="{RECORDED_VERDICT}"',
    'data-note="//TODO fix this"',
    'data-src="https://evil.example/x.png"',
    f'aria-label="{INERT_SCHEME}"',
    f'aria-valuetext="{RECORDED_VERDICT}"',
    'aria-description="//see the appendix"',
    'xmlns="http://www.w3.org/2000/svg"',
    'xmlns="//www.w3.org/2000/svg"',
    'xmlns:xlink="http://www.w3.org/1999/xlink"',
]


@pytest.mark.parametrize("attribute", INERT_ATTRIBUTES)
def test_an_attribute_a_browser_never_dereferences_is_not_a_violation(attribute: str) -> None:
    # Each case carries REAL_FETCH, so the assertion is "this one and not the
    # other" rather than "nothing at all". A scanner switched off entirely fails
    # every line of this test, which is the point: the risk on a security control
    # is a suite that goes green because the control was weakened rather than
    # because it was corrected.
    document = f"<div {attribute}>{REAL_FETCH}</div>"
    assert _positions(document) == (("a", "href"),)


def test_an_inline_svg_may_carry_the_namespace_that_makes_it_savable() -> None:
    # Two chunks dropped xmlns from their <svg> to get past the detector. That is
    # correct for inline SVG in an HTML5 document and wrong the moment a reviewer
    # saves the chart on its own, which is a thing reviewers do with charts. The
    # <use> reference is same-document and is not a fetch either.
    document = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" role="img" '
        f'aria-label="{RECORDED_VERDICT}">'
        '<use xlink:href="#series"></use>'
        f'<rect data-verdict="{INERT_SCHEME}"></rect>'
        f"</svg>{REAL_FETCH}"
    )
    assert _positions(document) == (("a", "href"),)


def test_a_recorded_verdict_in_a_data_attribute_does_not_refuse_the_document(
    tmp_path: Path,
) -> None:
    # The denial-of-render, in the shape the chart chunk will give it. The second
    # half re-asks the same document whether the gate is still live, so this test
    # cannot pass by the gate having been removed.
    document = _rendered_with(tmp_path, f'<rect data-verdict="{RECORDED_VERDICT}"></rect>')
    assert_self_contained(document)
    with pytest.raises(ReportError):
        assert_self_contained(_rendered_with(tmp_path, '<img src="https://evil.example/x.png">'))


def test_an_evidence_log_whose_verdict_reads_like_a_scheme_still_renders_a_report(
    tmp_path: Path,
) -> None:
    # End to end, because the defect was reachable from data on disk: a verdict
    # somebody's rule wrote last night, read back on a reviewer's machine, and no
    # report at the end of it. render_html rather than render_html_string, so the
    # gate under test is the one in the path a user actually takes.
    log = _write_evidence(tmp_path / "log" / "evidence.jsonl", _payload(), verdict=RECORDED_VERDICT)
    out = render_html(ReportModel.from_evidence(log, now=NOW), tmp_path / "report.html", now=NOW)
    written = out.read_text(encoding="utf-8")
    assert RECORDED_VERDICT in written
    # The gate that had been refusing this document is still watching it.
    assert _positions(written.replace("<main>", f"<main>{REAL_FETCH}", 1)) == (("a", "href"),)


# -- the exemption does not leak: every name-based rule still fires ---------- #


def test_an_inline_event_handler_beside_an_exempt_attribute_is_still_the_violation() -> None:
    # The whole safety argument for the exemption is that no script runs. An on*
    # handler is script by another name, and it is judged on what the attribute
    # is, not on what the element beside it happens to carry.
    document = f'<div data-verdict="{INERT_SCHEME}" onclick="fetch(1)">x</div>'
    assert _positions(document) == (("div", "onclick"),)


def test_a_style_block_beside_an_exempt_attribute_is_still_the_violation() -> None:
    document = (
        '<div data-note="//chart">'
        "<style>.k { background: url(https://evil.example/bg.png) }</style>"
        f"{REAL_FETCH}</div>"
    )
    assert _positions(document) == (("style", ""), ("a", "href"))


DEREFERENCED = [
    ("a", "href"),
    ("img", "src"),
    ("img", "srcset"),
    ("video", "poster"),
    ("div", "data"),
    ("form", "action"),
    ("button", "formaction"),
    ("body", "background"),
    ("blockquote", "cite"),
    ("img", "longdesc"),
    ("html", "manifest"),
    ("img", "usemap"),
    ("a", "ping"),
    ("use", "xlink:href"),
    ("rect", "xml:base"),
]


@pytest.mark.parametrize(("tag", "attribute"), DEREFERENCED)
def test_an_attribute_the_browser_dereferences_is_still_a_violation(
    tag: str, attribute: str
) -> None:
    document = f'<{tag} {attribute}="https://evil.example/x">'
    assert _positions(document) == ((tag, attribute),)


@pytest.mark.parametrize(
    ("tag", "attribute"),
    [("a", "ping"), ("use", "xlink:href"), ("rect", "xml:base")],
)
def test_the_three_newly_named_fetching_attributes_catch_a_bare_relative_path(
    tag: str, attribute: str
) -> None:
    """A value with no scheme, in an attribute that genuinely fetches.

    This is the assertion that distinguishes "named in ``FETCHING_ATTRS``" from
    "caught in passing by the broad rule this chunk has just narrowed". All three
    fetch; none was named; each was resting on a rule that is about to stop
    applying to its neighbours. A relative path is the value that tells the two
    apart, and it resolves against wherever the reviewer saved the file.
    """
    assert _positions(f'<{tag} {attribute}="assets/chart.png">') == ((tag, attribute),)


def test_a_same_document_reference_in_a_fetching_attribute_is_not_a_fetch() -> None:
    # The other half of naming xlink:href: <use xlink:href="#series"> is how an
    # inline chart reuses a shape, and a rule that called that a fetch would be a
    # rule somebody eventually switches off.
    assert _positions(f'<use xlink:href="#series"></use>{REAL_FETCH}') == (("a", "href"),)


@pytest.mark.parametrize(
    "attribute",
    [
        "database-url",
        "datax",
        "dataurl",
        "data",
        "aria",
        "arialabel",
        "ariax",
        "xmlnsfoo",
        "xmlns-x",
    ],
)
def test_an_attribute_that_only_begins_like_an_inert_one_is_still_judged(attribute: str) -> None:
    """The exemption covers four families, not four prefixes.

    ``datax``, ``dataurl`` and ``database-url`` are the sharp ones, and it is
    worth being exact about why, because the obvious answer is wrong. Bare
    ``data`` -- the ``<object data=>`` attribute -- looks like the dangerous case
    and is not: it is in ``FETCHING_ATTRS``, so it stays caught by name even if a
    ``startswith("data")`` exemption swallows it, and it kills no mutant. The
    names that actually detect the dropped hyphen are the ones in neither list,
    where the shape rule is the only guard. The rest are the same mistake spelled
    differently. ``aria`` and ``xmlns-x`` are here on the strict reading
    of the contract: the families are ``data-*``, ``aria-*``, ``xmlns`` and
    ``xmlns:*``, and nothing else earns the exemption by resembling them.
    """
    assert _positions(f'<div {attribute}="https://evil.example/x">') == (("div", attribute),)


@pytest.mark.parametrize(
    "attribute",
    ["HREF", "Src", "PING", "XLINK:HREF", "XML:BASE", "Data", "DATABASE-URL"],
)
def test_a_fetching_attribute_is_caught_whatever_its_casing(attribute: str) -> None:
    # HTML attribute names are case-insensitive and the parser lowers them, so
    # the exemption has to be decided on the lowered name. Asserted through
    # external_urls rather than against the rule in isolation, because that
    # composition is what a document exercises.
    assert _positions(f'<div {attribute}="https://evil.example/x">') == (
        ("div", attribute.lower()),
    )


@pytest.mark.parametrize("attribute", ["href", "src", "poster", "action", "cite"])
def test_a_bare_relative_path_in_a_fetching_attribute_is_still_a_violation(
    attribute: str,
) -> None:
    assert _positions(f'<div {attribute}="assets/chart.png">') == (("div", attribute),)


# -- the bans the exemption rests on, and the rule it must not have widened -- #


@pytest.mark.parametrize(
    ("fragment", "tag"),
    [
        ("<script></script>", "script"),
        ("<script>1</script>", "script"),
        ('<link rel="stylesheet" href="fonts.css">', "link"),
        ("<iframe></iframe>", "iframe"),
        ('<base href="/">', "base"),
        ("<embed>", "embed"),
        ('<object data="x.swf"></object>', "object"),
    ],
)
def test_the_bans_the_exemption_rests_on_are_untouched(fragment: str, tag: str) -> None:
    """If the script ban is ever relaxed, the exemption above becomes unsafe.

    Nothing can read a ``data-`` value into a request while no script runs; that
    coupling is the entire argument for the exemption, and it belongs beside it
    rather than in a plan document. Whoever relaxes ``FORBIDDEN_TAGS`` fails
    these seven cases and, with luck, reads this docstring before deleting them.
    """
    assert _positions(fragment)[0] == (tag, "")


@pytest.mark.parametrize(
    "fragment",
    [
        "<style>.k { background: url(https://evil.example/bg.png) }</style>",
        '<style>@import "https://evil.example/x.css";</style>',
        '<div style="background: url(https://evil.example/bg.png)"></div>',
    ],
)
def test_the_two_css_rules_are_untouched(fragment: str) -> None:
    """CSS is the one place an exempt value could plausibly become a request.

    ``span[data-icon] { background-image: url(attr(data-icon)) }`` is the shape
    that would make this whole exemption unsafe with no script anywhere in the
    document. It does not work, and not by accident: a value produced by
    ``attr()`` is *attr()-tainted*, and css-values-5 makes a declaration invalid
    at computed-value time when a tainted value is used "as or in a ``<url>``".
    That is a constraint on the type rather than a list of functions, so it holds
    for ``src()`` and ``image-set()`` too, and for laundering through ``var()``.
    That is a second coupling the exemption rests on, alongside the script ban,
    and unlike the script ban it is a platform rule rather than one this
    repository controls.

    So these two rules carry more weight after this chunk than before it, not
    less: they are what still catches CSS that fetches. Which is also why
    ``_URL_FN_RE`` matching only ``url(`` -- not ``image-set()``, which ships
    everywhere -- is worth fixing, though it predates this chunk and is not part
    of it.
    """
    assert len(external_urls(fragment)) == 1


def test_the_scheme_rule_is_still_anchored_where_it_was() -> None:
    """``title="see http://example.com"`` was never flagged and still is not.

    The anchoring is the odd part of the old rule -- it fires on values that
    happen to *begin* with a scheme and walks past the same URL one word in --
    but un-anchoring it is a separate question, and a chunk that narrowed the
    rule by name while quietly widening it by position would be two changes
    wearing one commit message. This pins that it was not.
    """
    document = f'<p title="see http://example.com for details">x</p>{REAL_FETCH}'
    assert _positions(document) == (("a", "href"),)


# -- the invariant the exemption's safety actually reduces to ---------------- #


def test_the_exemption_never_covers_an_attribute_the_browser_dereferences() -> None:
    """No name may be both exempt from the shape rules and a known fetching name.

    This is the assertion the rest of section 5 cannot make, and the reason it
    cannot is worth writing down. For any name already in ``FETCHING_ATTRS`` the
    two shape rules are *strictly subsumed* by the ``FETCHING_ATTRS`` rule: every
    value that starts with ``//`` or matches ``_SCHEME_RE`` is also non-empty and
    starts with neither ``#`` nor ``data:``, so it is a violation by name whether
    or not it was one by shape. Adding ``href`` and ``src`` to
    ``_NEVER_DEREFERENCED_RE`` therefore changes no violation *count* anywhere --
    only the ``reason`` string -- and every behavioural test above stays green
    through it.

    So the exemption is not pinned by any test that scans a document. What pins
    it is this: the exemption and ``FETCHING_ATTRS`` must stay disjoint. That is
    the property the safety argument really rests on -- "these families contain
    no member the browser retrieves" -- stated as something a test can check, and
    it is what fails the moment someone widens the regex toward a name that
    fetches.
    """
    covered = sorted(name for name in FETCHING_ATTRS if _NEVER_DEREFERENCED_RE.match(name))
    assert covered == [], (
        f"{covered} are exempt from the shape rules *and* named as fetching. "
        f"The exemption may only cover families with no dereferenced member."
    )


@pytest.mark.parametrize("attribute", ["imagesrcset", "archive", "somefutureurlattr"])
def test_an_attribute_outside_both_lists_is_still_judged_by_shape(attribute: str) -> None:
    """The shape rule is the only guard on every name ``FETCHING_ATTRS`` omits.

    ``ping``, ``xlink:href`` and ``xml:base`` were three such names until this
    chunk promoted them; ``imagesrcset`` and ``archive`` are two it did not, and
    the third is the one nobody has invented yet. None is exempt, so each is
    still caught -- by shape, which is the rule this chunk narrowed.

    That is the real reason widening the exemption is dangerous, and why the
    invariant test above is necessary but not sufficient: the invariant only sees
    names that are in ``FETCHING_ATTRS``. Exempt ``imagesrcset`` instead and the
    invariant passes, every document test passes, and a fetch ships.
    """
    assert _positions(f'<div {attribute}="https://evil.example/x">') == (("div", attribute),)


def test_the_reason_says_which_rule_fired_not_merely_that_one_did() -> None:
    """``reason`` is the whole output of ``_attribute_reason`` and nothing pins it.

    Every other assertion in this section compares ``(tag, attribute)`` pairs,
    which is deliberate -- a count is satisfied by the wrong violation. But it
    leaves the rule *attribution* untested, and attribution is the only thing the
    exemption changes on a name that is also in ``FETCHING_ATTRS``: the violation
    is emitted either way, and only this string says which rule emitted it. That
    makes ``reason`` the one observable difference between the exemption as
    written and an exemption widened to swallow ``href``. It is also what a
    reader debugging a false positive actually reads.
    """
    (shape,) = external_urls('<p title="//evil.example/x">y</p>')
    assert "protocol-relative" in shape.reason

    # href carries a scheme and href is also in FETCHING_ATTRS, so two rules
    # match it. The shape rules run first, so today it is reported as a scheme.
    # That ordering is the one observable difference the exemption makes on a
    # fetching name -- exempt href and this same document comes back reported by
    # name instead -- and it is the only thing in the suite that can tell the two
    # apart, because the violation itself is emitted either way.
    (scheme,) = external_urls('<a href="https://evil.example/x">y</a>')
    assert "URL scheme other than data:" in scheme.reason, (
        "href is not exempt from the shape rules and must still be judged by "
        "them. If this now reads as a FETCHING_ATTRS reason, href has been "
        "added to _NEVER_DEREFERENCED_RE -- see the invariant test above."
    )

    # A relative path in the same attribute has no shape to fail, so it reaches
    # the name-based rule. Both sentences have to stay reachable or the reason
    # stops distinguishing anything.
    (by_name,) = external_urls('<a href="assets/chart.png">y</a>')
    assert "dereferenced by the browser" in by_name.reason

    (handler,) = external_urls('<div onclick="fetch(1)"></div>')
    assert "inline event handler" in handler.reason
