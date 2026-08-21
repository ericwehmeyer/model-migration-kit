"""Reading an evidence log: one reader, one line at a time, for every consumer.

The renderer used to be the only thing that read a log back, so the reader lived
in ``report.py`` as a private function. A series reads the same file for a
different reason, and that made the placement a problem rather than a detail.

Two options were closed. ``series`` cannot import ``report``: ``report`` imports
``series`` for the points it hangs off ``ReportModel``, so the pair would be an
import cycle that fails at the first ``import`` rather than at some later edge --
and it would drag jinja2 and rich into every consumer of a module that renders
nothing. Nor may ``series`` grow a reader of its own: two readers of one format
is the arrangement in which a reader and a writer drift apart, and the drift
shows up as a log that one half of this package calls malformed and the other
half parses. So the reader moved *down*, to a module both can depend on and that
depends on neither.

``report._stream_records`` remains as an alias, because the name is what
``tests/test_evidence_scale.py`` measures the amplification through and a rename
would have made the one test that guards the memory claim look like new code.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from opik_rigor import EvidenceError, EvidenceRecord

from .errors import ArtifactError

__all__ = ["EVIDENCE_FILE", "resolve_evidence", "stream_records"]

#: What ``migkit compare`` and ``migkit demo`` name the log inside a work
#: directory, and therefore what a directory argument resolves to. Spelled once
#: here so that the renderer and the series cannot disagree about which file a
#: reviewer meant when they typed the directory they were handed.
EVIDENCE_FILE = "evidence.jsonl"


def resolve_evidence(evidence: str | Path) -> Path:
    """The log file a caller's path names, or a refusal naming the path.

    A directory resolves to ``<dir>/evidence.jsonl`` because that is what the
    reviewer was handed: ``migkit compare`` writes a work directory, and the
    argument people actually type is the directory rather than the file inside
    it.

    A path that names no file is an error here rather than an empty read, and
    that is the whole reason this is a function instead of an ``open`` at the
    call site. opik-rigor reads a missing log as ``[]``, so a mistyped path would
    otherwise flow all the way through to a report of a run that never happened
    -- a document that looks valid, states nothing went wrong, and is about
    nothing at all.

    Raises:
        ArtifactError: if the resolved path is not a file.
    """
    path = Path(evidence)
    if path.is_dir():
        path = path / EVIDENCE_FILE
    if not path.is_file():
        raise ArtifactError(
            f"no evidence log at {path}. opik-rigor reads a missing log as an "
            f"empty one, so this is checked here: a mistyped path would "
            f"otherwise render as a valid report of a run that never happened."
        )
    return path


def stream_records(path: Path) -> Iterator[EvidenceRecord]:
    """Every record in the evidence log, one at a time, holding none of them.

    This exists because of a measured amplification, not a style preference.
    ``EvidenceLog.read()`` reads the whole file as text and returns a list of
    parsed records; reconstruction needs exactly three of them -- the last
    ``migkit.comparison``, the last ``migkit.verdict``, and the final record for
    the completeness strip's "last event" -- and paid for all of them. Measured at
    5.0 to 5.8 times the log's own bytes resident: an 86 MB log cost an extra
    502 MB, and the evidence log is the *largest* artifact this pipeline produces,
    because rigor's ``judge.verdict`` record embeds the input, the output and the
    judge's raw reply for every completion. That is what runs out of memory first.
    Streaming holds one line.

    The parsing rules are rigor's, deliberately: ``EvidenceRecord.from_json`` does
    the decoding, and a torn final line -- the signature of a process killed
    mid-write -- is dropped while anything malformed earlier is an error, which is
    exactly what ``read()`` does. Re-deriving those rules rather than reusing them
    is how a reader and a writer of the same file drift apart.

    ``newline="\n"`` is not decoration. ``read()`` splits on ``"\n"`` and nothing
    else, while Python's default text iteration also breaks lines on a lone
    ``\r``; without it a model output containing a bare carriage return would be
    two lines here and one line there, and this reader would call a valid log
    malformed.
    """
    with open(path, encoding="utf-8", newline="\n") as handle:
        for index, line in enumerate(handle):
            complete = line.endswith("\n")
            text = line[:-1] if complete else line
            if not text.strip():
                if not complete:
                    continue  # torn write at the end of the file
                raise EvidenceError(f"blank line at position {index} in {path}")
            try:
                yield EvidenceRecord.from_json(text)
            except (json.JSONDecodeError, EvidenceError):
                if not complete:
                    continue  # torn write at the end of the file
                raise EvidenceError(
                    f"malformed evidence at line {index + 1} of {path}: {text[:120]!r}"
                ) from None
