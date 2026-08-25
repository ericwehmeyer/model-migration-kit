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
import math
from collections.abc import Iterator
from pathlib import Path

from opik_rigor import EvidenceError, EvidenceRecord

from .errors import ArtifactError

__all__ = [
    "EVIDENCE_FILE",
    "EVIDENCE_SCHEMA_VERSION",
    "SchemaTally",
    "foreign_schema",
    "resolve_evidence",
    "stream_records",
]

#: What ``migkit compare`` and ``migkit demo`` name the log inside a work
#: directory, and therefore what a directory argument resolves to. Spelled once
#: here so that the renderer and the series cannot disagree about which file a
#: reviewer meant when they typed the directory they were handed.
EVIDENCE_FILE = "evidence.jsonl"

#: The highest evidence-envelope schema *this build's reader* was written
#: against. Rigor stamps ``schema_version`` on every line it appends; this is the
#: number that says how far the reading below can be trusted.
#:
#: **A literal, and deliberately not ``opik_rigor.evidence.SCHEMA_VERSION``.**
#: Importing rigor's constant would raise this ceiling the moment rigor shipped a
#: schema 2 -- before anybody had taught this reader to read one -- so the guard
#: would go quiet on exactly the log it exists for, and it would go quiet by
#: upgrading a dependency rather than by anyone deciding anything. The same
#: argument ``runner.ARTIFACT_SCHEMA_VERSION`` makes for the run artifact: the
#: ceiling belongs to the reader, not to the writer.
#:
#: The pinning is a test rather than an import
#: (``test_this_builds_evidence_ceiling_is_pinned_to_the_schema_rigor_writes``),
#: so a rigor release that moves the number turns the suite red and a person
#: decides whether this reader actually understands the new envelope.
EVIDENCE_SCHEMA_VERSION = 1

#: How much of a declared version this reader will quote back into a disclosure.
#: A ``schema_version`` is attacker-influenced like every other field read out of
#: a log, and the sentence that names it is printed above the verdict banner; a
#: 40-character bound keeps a hostile 4 MB string from being the band.
_SCHEMA_LABEL_CHARS = 40


def foreign_schema(record: EvidenceRecord) -> str | None:
    """What ``record`` declares its envelope schema to be, when this build cannot read it.

    ``None`` means the declaration is one this build understands and there is
    nothing to say. Otherwise the return value is the declared version as text,
    for a disclosure to quote -- never a number this function chose.

    **Absent is not unknown, and the two must not converge here.** Rigor's
    ``EvidenceRecord.from_json`` fills a missing ``schema_version`` with its own
    ``SCHEMA_VERSION``, so a record that declared nothing arrives holding ``1``
    and this function returns ``None`` for it. That is the right answer and it is
    not an invention: nothing is printed, no version is attributed to a writer
    that named none, and the log renders exactly as a log that declared ``1``
    does -- which is what rigor's own reader already believes about it. A log
    declaring ``99`` gets a sentence naming ``99``. Silence and a named version
    are different outcomes for different facts, which is the rule this package
    turns on, pointed at its own input.

    **One-directional, like the three guards that came before it.**
    ``runner.py`` and ``judging.py`` both refuse only what is *newer* than they
    understand and read anything older without comment, and this follows them:
    a version at or below :data:`EVIDENCE_SCHEMA_VERSION` is understood. There is
    no schema 0 to find, and a hypothetical older envelope is one whose fields
    this reader either finds or does not -- and a field it does not find is
    already the completeness strip's business, named there rather than banded
    here.

    **A declaration this reader cannot even read as a number is foreign, not
    ``1``.** This is where :func:`series._count` deliberately does not
    generalise. ``_count`` coerces an uninterpretable value to ``0`` because a
    count has no way to say "unavailable" and ``0`` is its only alternative;
    here the alternative is an admission, and an admission is always available.
    ``null``, a mapping, ``NaN`` and ``"two"`` therefore all disclose. A *quoted
    integer* does not: ``"1"`` is ``_count``'s own case, a writer that stringified
    its numbers, and it declared something this build understands. Treating it as
    foreign would band a log for a re-serialisation, and a band a reader learns to
    skip is worse than one that is merely absent.

    ``True`` is excluded from the numeric branch on purpose. Python would read it
    as ``1`` and call the record understood; a JSON ``true`` in this field is a
    writer this reader has never met, not a declaration of schema one.
    """
    declared = record.schema_version
    if isinstance(declared, bool):
        return _schema_label(declared)
    if isinstance(declared, (int, float)):
        return _numeric_verdict(float(declared), declared)
    if isinstance(declared, str):
        try:
            return _numeric_verdict(float(declared), declared)
        except ValueError:
            return _schema_label(declared)
    return _schema_label(declared)


def _numeric_verdict(number: float, declared: object) -> str | None:
    """``None`` when ``number`` is within the ceiling, else ``declared`` as text.

    ``NaN`` and the infinities go through here rather than round the outside,
    because ``float("nan") <= 1`` is ``False`` *and* ``float("nan") > 1`` is
    ``False``: a bare comparison would call a NaN schema understood in one
    spelling and foreign in the other, and which one shipped would be an accident
    of how the condition happened to be written.
    """
    if not math.isfinite(number):
        return _schema_label(declared)
    return None if number <= EVIDENCE_SCHEMA_VERSION else _schema_label(declared)


def _schema_label(declared: object) -> str:
    """A declared version rendered short, printable and ASCII-terminated.

    ``None`` becomes ``"null"`` rather than Python's ``"None"``: the reader of the
    band is looking at a JSON log, and the word in the file is the word to quote.

    The ellipsis is three dots and not ``U+2026`` for the reason
    :func:`~model_migration_kit.report.render_terminal` gives for its hyphen --
    rich substitutes box-drawing characters on a legacy Windows console and not
    arbitrary text, and this string prints in the first panel, before anything
    else has had a chance to fail.
    """
    text = "null" if declared is None else str(declared)
    text = "".join(one if one.isprintable() else " " for one in text)
    if len(text) > _SCHEMA_LABEL_CHARS:
        return text[: _SCHEMA_LABEL_CHARS - 3] + "..."
    return text


#: Distinct declared versions one reading will name. A log is append-only and
#: rigor supports separate processes appending to one path, so two writers in one
#: file is a real shape rather than a hypothetical one -- but a hostile log with a
#: different version on every line must not turn a disclosure into the file. Past
#: this many the reading records that it stopped listing.
_SCHEMA_VERSIONS_NAMED = 4


class SchemaTally:
    """Counts foreign envelope declarations on the way past, holding no records.

    A class rather than four locals in the loop that drives it, for the reason
    :class:`~model_migration_kit.dimensions.DimensionTally` is one: that loop is
    ``report``'s single pass over the largest artifact this pipeline writes,
    guarded by ``test_the_log_is_read_once_for_both_the_headline_and_the_series``
    and by ``test_rebuilding_the_report_does_not_hold_the_log_either``, and
    anything accumulated there has to be bounded in the log's size. This holds at
    most :data:`_SCHEMA_VERSIONS_NAMED` short strings and two integers whatever
    the file is.

    It lives here rather than beside the band it feeds because this module owns
    the envelope: it is the one reader of the log, and a second place that knew
    what ``schema_version`` means is the drift the module docstring above was
    written to prevent. What it yields is facts -- how many, of how many, spelled
    how -- and no wording. The sentence is the renderer's, in ``report``, where
    the terminal and the HTML can be held to one copy of it.
    """

    __slots__ = ("_versions", "_foreign", "_total", "_elided")

    def __init__(self) -> None:
        self._versions: list[str] = []
        self._foreign = 0
        self._total = 0
        self._elided = False

    def add(self, record: EvidenceRecord) -> None:
        """File one record's declared version. Never raises; a disclosure is not a gate."""
        self._total += 1
        label = foreign_schema(record)
        if label is None:
            return
        self._foreign += 1
        if label in self._versions:
            return
        if len(self._versions) < _SCHEMA_VERSIONS_NAMED:
            self._versions.append(label)
        else:
            self._elided = True

    @property
    def foreign_records(self) -> int:
        """Records whose declared version this build could not read."""
        return self._foreign

    @property
    def total_records(self) -> int:
        """Records seen, so a disclosure can say *some* rather than *every*."""
        return self._total

    @property
    def versions(self) -> tuple[str, ...]:
        """The distinct foreign versions, as the log spelled them, first seen first."""
        return tuple(self._versions)

    @property
    def versions_elided(self) -> bool:
        """Whether more distinct versions were found than :attr:`versions` names."""
        return self._elided


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
