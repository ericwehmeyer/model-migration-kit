"""Reading three records out of an evidence log without reading the log.

``ReportModel.from_evidence`` needs exactly three records: the last
``migkit.comparison``, the last ``migkit.verdict``, and whatever came last so the
completeness strip can name it. It used to get them by calling
``EvidenceLog.read()``, which parses the whole file and returns a list, and then
folding that list three times.

That is not a style problem. The evidence log is the **largest artifact this
pipeline produces** -- larger than the run and judged artifacts put together --
because rigor's ``judge.verdict`` record embeds the ``input``, the ``output`` *and*
the judge's ``raw`` reply for every completion, which is the exact duplication
``runner.py`` deliberately refuses for its own ``migkit.completion`` record. At
1000 items and n=50 the log is 86 MB against 45 MB of run plus judged. Measured
amplification through ``read()`` was 5.0 to 5.8 times the log's own bytes
resident: +502 MB on an 86 MB log, and roughly 2.2 GB of evidence -- about 450,000
completions -- is where a machine with 16 GB stops finishing the report.

Two fixes, both here:

* :func:`~model_migration_kit.report._stream_records` parses one line at a time
  and keeps three records. Same parser, same tolerance rules -- they are rigor's,
  imported rather than re-derived -- and none of the list.
* :func:`~model_migration_kit.contracts.hash_file` reads in chunks. The obvious
  spelling holds the file *twice*: once as bytes and once as the copy
  ``bytes.replace`` returns. Every report hashes the evidence log, so that was a
  second full-size allocation on the same path.

The tests below pin the equivalence first -- a faster reader that reads
differently is a worse reader -- and then pin the amplification itself, because
equivalence is exactly what the slow version already had.
"""

from __future__ import annotations

import hashlib
import json
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from opik_rigor import EvidenceError, EvidenceLog, FakeAdapter

from model_migration_kit import contracts
from model_migration_kit.comparison import compare
from model_migration_kit.contracts import hash_bytes, hash_file
from model_migration_kit.demo import judge_script
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, judge_artifact
from model_migration_kit.report import ReportModel, _stream_records
from model_migration_kit.runner import run_goldenset

ITEMS = 6
N_PER_ITEM = 3

RUBRIC = """# Evidence-scale rubric

Score 5 when the answer is exactly the reference.
Score 4 when the reference appears inside a longer answer.
Score 2 when the answer is not supported by the source.
4 and 5 pass; below that fails.
"""

JUDGES_TOML = """
[[judge]]
name   = "accuracy"
model  = "fake-judge-v1"
rubric = "rubric.md"

[thresholds]
pass_rate_floor = 0.90
alpha = 0.05
confidence = 0.95
judge_failure_tolerance = 0.05
min_detectable_effect = 0.10
power_target = 0.80
"""


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A small, real evidence log: every record type this reader has to survive."""
    root = tmp_path_factory.mktemp("evidence-scale")
    goldenset_path = root / "goldenset.jsonl"
    with open(goldenset_path, "w", encoding="utf-8", newline="\n") as handle:
        for index in range(ITEMS):
            handle.write(
                json.dumps(
                    {
                        "id": f"item-{index:04d}",
                        "input": f"canonical answer for case {index}?",
                        "reference": f"answer-{index}",
                        "tags": ["evidence"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    (root / "rubric.md").write_text(RUBRIC, encoding="utf-8")
    (root / "judges.toml").write_text(JUDGES_TOML, encoding="utf-8")

    goldenset = GoldenSet.load(goldenset_path)
    evidence = EvidenceLog(root / "evidence.jsonl")
    runs = [
        run_goldenset(
            goldenset,
            FakeAdapter(
                model_id=f"fake-{side}-v1",
                responses={
                    item.input: (item.reference or "")
                    if side == "baseline"
                    else f"unsupported-{item.id}"
                    for item in goldenset
                },
            ),
            out_dir=root,
            n=N_PER_ITEM,
            evidence=evidence,
        )
        for side in ("baseline", "candidate")
    ]
    config = JudgeConfig.load(root / "judges.toml")
    panel = config.build(
        evidence,
        lambda spec: FakeAdapter(model_id=spec.model, responses=judge_script(goldenset)),
    )
    judged = [
        judge_artifact(run, goldenset, panel, evidence=evidence, out_dir=root)
        for run in runs
    ]
    compare(
        judged[0],
        judged[1],
        thresholds=config.thresholds,
        evidence=evidence,
        baseline_run=runs[0],
        candidate_run=runs[1],
        goldenset_path=str(goldenset_path),
        config_path=str(root / "judges.toml"),
        config_hash=hash_file(root / "judges.toml"),
    )
    return {"root": root, "log": root / "evidence.jsonl", "goldenset": goldenset_path}


# --------------------------------------------------------------------------- #
# equivalence with the reader it replaced
# --------------------------------------------------------------------------- #


def test_streaming_yields_exactly_what_reading_the_whole_log_yields(
    pipeline: dict[str, Any]
) -> None:
    """Record for record, field for field, on a log holding every event type.

    Asserted against ``EvidenceLog.read()`` rather than against a hand-built
    expectation, because ``read()`` is the definition of what this log means and
    the streaming reader's only job is to agree with it.
    """
    log = pipeline["log"]
    expected = EvidenceLog(log).read()
    streamed = list(_stream_records(log))
    assert len(streamed) == len(expected) > 10
    assert [one.event_type for one in streamed] == [one.event_type for one in expected]
    assert [one.ts for one in streamed] == [one.ts for one in expected]
    assert [one.payload for one in streamed] == [one.payload for one in expected]


def test_a_torn_final_line_is_dropped_by_both_readers(
    pipeline: dict[str, Any], tmp_path: Path
) -> None:
    """The signature of a process killed mid-write, and the case the report exists
    to render. Tolerated identically, so a crashed run still reports."""
    torn = tmp_path / "torn.jsonl"
    torn.write_bytes(
        pipeline["log"].read_bytes() + b'{"schema_version": 1, "ts": "2026-'
    )
    assert len(list(_stream_records(torn))) == len(EvidenceLog(torn).read())
    model = ReportModel.from_evidence(
        torn, goldenset=str(pipeline["goldenset"]), now="2026-01-01T00:00:00Z"
    )
    assert model.verdict is not None


def test_a_malformed_line_that_is_not_the_last_is_still_an_error(
    pipeline: dict[str, Any], tmp_path: Path
) -> None:
    """Corruption in the middle of a log is refused, not skipped.

    Streaming makes skipping the tempting implementation: the three records wanted
    are near the end, so a reader could stop early and never look at the damage. A
    change-control tool does not quietly paper over evidence that has been edited,
    and this is the assertion that keeps the fast reader from becoming a lenient
    one.
    """
    lines = pipeline["log"].read_text(encoding="utf-8").split("\n")
    lines[2] = '{"schema_version": 1, "ts": "2026-01-01", "event'
    bad = tmp_path / "bad.jsonl"
    bad.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    with pytest.raises(EvidenceError, match="malformed evidence at line 3"):
        list(_stream_records(bad))
    with pytest.raises(EvidenceError, match="malformed evidence at line 3"):
        ReportModel.from_evidence(bad, goldenset=str(pipeline["goldenset"]))


def test_a_blank_line_in_the_middle_is_an_error_too(
    pipeline: dict[str, Any], tmp_path: Path
) -> None:
    lines = pipeline["log"].read_text(encoding="utf-8").split("\n")
    blanked = tmp_path / "blank.jsonl"
    blanked.write_text(
        "\n".join(lines[:3] + [""] + lines[3:]), encoding="utf-8", newline="\n"
    )
    with pytest.raises(EvidenceError, match="blank line at position 3"):
        list(_stream_records(blanked))


def test_a_bare_carriage_return_inside_a_record_does_not_split_it(
    tmp_path: Path,
) -> None:
    """One record with a ``\\r`` in its payload stays one record.

    ``EvidenceLog.read()`` splits on ``"\\n"`` and nothing else, while Python's
    default text iteration also breaks a line at a lone ``\\r``. A model output
    containing a bare carriage return -- ordinary in transcript data -- would then
    be one record to the writer and two to this reader, and the second half would
    be reported as a malformed log. The reader opens with ``newline="\\n"`` for
    exactly this, and this is the test that says so.
    """
    log = tmp_path / "cr.jsonl"
    payload = {"output": "first half\rsecond half"}
    log.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ts": "2026-01-01T00:00:00Z",
                "event_type": "judge.verdict",
                "payload": payload,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    streamed = list(_stream_records(log))
    assert len(streamed) == 1
    assert streamed[0].payload == payload
    assert [one.payload for one in EvidenceLog(log).read()] == [payload]


# --------------------------------------------------------------------------- #
# the amplification itself
# --------------------------------------------------------------------------- #


def _inflate(log: Path, target_bytes: int) -> int:
    """Append rigor-shaped ``judge.verdict`` records until the log reaches a size.

    Shaped like rigor's, deliberately: ``input``, ``output`` and ``raw`` on every
    record, which is what makes the evidence log larger than the artifacts it
    points at and is the reason this file exists.
    """
    filler = "w" * 4000
    with open(log, "a", encoding="utf-8", newline="\n") as handle:
        index = 0
        while log.stat().st_size < target_bytes:
            for _ in range(50):
                handle.write(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "ts": "2026-01-01T00:00:00Z",
                            "event_type": "judge.verdict",
                            "payload": {
                                "judge": "accuracy",
                                "passed": True,
                                "score": 5,
                                "input": f"input-{index} {filler}",
                                "output": f"output-{index} {filler}",
                                "raw": f'{{"pass": true, "score": 5, "x": "{filler}"}}',
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                index += 1
            handle.flush()
    return log.stat().st_size


def _peak(work: Any) -> int:
    tracemalloc.start()
    try:
        work()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_streaming_does_not_hold_the_log(pipeline: dict[str, Any], tmp_path: Path) -> None:
    """Peak allocation while reading is a record, not a file.

    Asserted as *flat in the size of the log* rather than against a tuned
    constant, because that is the actual claim: the reader this replaced cost 5.0
    to 5.8 times the file, so tripling the file tripled the cost. A ceiling would
    pass on an implementation that had gone back to holding a fifth of the log; a
    slope will not.
    """
    sizes: list[int] = []
    peaks: list[int] = []
    for index, target in enumerate((8_000_000, 24_000_000)):
        log = tmp_path / f"stream-{index}.jsonl"
        log.write_bytes(pipeline["log"].read_bytes())
        sizes.append(_inflate(log, target))
        peaks.append(_peak(lambda log=log: sum(1 for _ in _stream_records(log))))

    assert sizes[1] > sizes[0] * 2.5
    assert peaks[1] < peaks[0] * 1.5, (
        f"peak allocation went {peaks[0]} -> {peaks[1]} bytes while the log went "
        f"{sizes[0]} -> {sizes[1]}; the cost is supposed to be one line, not a "
        f"share of the file"
    )
    assert peaks[1] < 4_000_000


def test_rebuilding_the_report_does_not_hold_the_log_either(
    pipeline: dict[str, Any], tmp_path: Path
) -> None:
    """The end-to-end claim, on the path a report actually takes.

    This covers the hash as well as the parse -- every report hashes the evidence
    log for the provenance block, and the whole-file spelling of that hash held the
    file twice on its own. What is left is the hash's own chunk buffer, which is a
    megabyte whatever the log is, and that is exactly why this is asserted as a
    slope: a flat couple of megabytes on a 24 MB log is the fix working, and the
    same couple of megabytes on an 86 MB log is the fix still working.
    """
    root = tmp_path / "work"
    root.mkdir()
    for name in ("goldenset.jsonl", "rubric.md", "judges.toml"):
        (root / name).write_bytes((pipeline["root"] / name).read_bytes())
    for artifact in pipeline["root"].glob("*.jsonl"):
        if artifact.name != "goldenset.jsonl":
            (root / artifact.name).write_bytes(artifact.read_bytes())

    sizes: list[int] = []
    peaks: list[int] = []
    models: list[ReportModel] = []
    log = root / "evidence.jsonl"
    for target in (8_000_000, 24_000_000):
        sizes.append(_inflate(log, target))
        peaks.append(
            _peak(
                lambda: models.append(
                    ReportModel.from_evidence(
                        log,
                        goldenset=str(root / "goldenset.jsonl"),
                        now="2026-01-01T00:00:00Z",
                    )
                )
            )
        )

    assert models[0].verdict is not None
    assert models[1].hashes["evidence"] == hash_file(log)
    assert sizes[1] > sizes[0] * 2.5
    assert peaks[1] < peaks[0] * 1.5, (
        f"peak allocation went {peaks[0]} -> {peaks[1]} bytes while the log went "
        f"{sizes[0]} -> {sizes[1]}; the measured amplification before this change "
        f"was 5.0-5.8x the log, which is a slope of 5"
    )
    assert peaks[1] < 8_000_000


# --------------------------------------------------------------------------- #
# the hash that reads the same log
# --------------------------------------------------------------------------- #


def test_chunked_hashing_agrees_with_whole_file_hashing(tmp_path: Path) -> None:
    """Including when a CRLF pair straddles a chunk boundary.

    That is the one way a chunked implementation goes wrong: normalising each
    chunk on its own leaves the straddling pair alone, and the hash then depends on
    where the reads happened to land -- which is the opposite of the property the
    newline-normalising convention exists to provide. The chunk size is driven down
    to eight bytes here so the boundary lands inside the pair on purpose.
    """
    for payload in (
        b"",
        b"a",
        b"\r",
        b"\r\n",
        b"line one\r\nline two\r\n",
        b"\r" * 32,
        b"abcdefg\r\nhijklmno\r\n\r\n\r",
        bytes(range(256)) * 4,
    ):
        target = tmp_path / "sample.bin"
        target.write_bytes(payload)
        expected = hash_bytes(payload)
        assert hash_file(target) == expected, payload
        for chunk in (1, 2, 3, 8, 17, 256):
            original = contracts.HASH_CHUNK_BYTES
            contracts.HASH_CHUNK_BYTES = chunk
            try:
                assert hash_file(target) == expected, (payload, chunk)
            finally:
                contracts.HASH_CHUNK_BYTES = original


def test_the_hash_is_still_sha256_of_the_normalised_bytes(tmp_path: Path) -> None:
    """Against stdlib hashlib rather than against the function's own other spelling.

    An oracle that used ``hash_bytes`` would agree with a chunked implementation
    that had drifted, as long as both had drifted the same way.
    """
    target = tmp_path / "sample.bin"
    target.write_bytes(b"alpha\r\nbeta\ngamma\r\n")
    assert hash_file(target) == hashlib.sha256(b"alpha\nbeta\ngamma\n").hexdigest()


def test_hashing_does_not_hold_the_file(tmp_path: Path) -> None:
    """A megabyte at a time, whatever the file is."""
    target = tmp_path / "big.bin"
    with open(target, "wb") as handle:
        for index in range(20):
            handle.write(f"chunk-{index}\r\n".encode() + b"w" * 1_000_000)
    size = target.stat().st_size

    tracemalloc.start()
    try:
        digest = hash_file(target)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert digest == hash_bytes(target.read_bytes())
    assert peak < size // 4, (
        f"hashing a {size}-byte file peaked at {peak} bytes; the whole-file "
        f"spelling this replaced held it twice"
    )
