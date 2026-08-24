"""Acceptance tests for :mod:`model_migration_kit.report`.

Written from the frozen contract, never from the module. The specification is
``docs/session-3-contract.md`` §§1-4 (§1.2 pins the evidence payload, §2.2 the
HTML structure and its order, §2.4 self-containment, §2.5 the golden-set gate,
§2.6 the partial-render path, §4 the threshold echo) together with
``docs/build-plan.md`` §6 Amendment 1, whose 2026-08-13 paragraph forbids an
item-level rate and requires three item counts beside the completion-level rate.
The author of this file did not write ``report.py`` and did not read it while
deriving a single expected value.

**Where every expectation comes from.** Nothing here was produced by running the
code under test.

* The three-violation negative control, its tags, and the "exactly three" count
  are stated verbatim in contract §2.4 and §6 item 1. They are not counted from a
  detector's output.
* HTML is inspected with stdlib :mod:`html.parser`, which is the same tool the
  contract requires the detector itself to be built on, used here independently:
  tags, attributes, ``id`` order and *text* are collected by
  :class:`_Document`. Counting ``<script>`` by parser rather than by substring is
  contract §6 item 4; asserting that hostile text is *visible as text* while no
  ``script`` element exists is §6 item 3, and the two assertions are deliberately
  complementary -- neither alone distinguishes escaping from deletion.
* ``wilson_interval(0, 0)`` raising ``ValueError`` is asserted directly against
  the installed ``opik-rigor`` in
  :func:`test_a_rate_over_zero_runs_raises_in_rigor_which_is_why_it_is_a_state`,
  rather than taken on trust from contract §0. What is pinned about the report is
  the *rendering* -- an em dash -- not the exception.
* Hashes are computed with stdlib :mod:`hashlib` under the convention
  ``contracts.py`` states for the whole project (sha256 of the bytes with CRLF
  normalised to LF), so the provenance block is checked against an oracle outside
  ``model_migration_kit``.
* Every statistic that reaches the report is a hand-chosen literal in the
  evidence payload, deliberately inconsistent with what a recomputation would
  produce, so "the report never recomputes a statistic" (§1.2) is testable rather
  than assumed. 17 of 20 recomputes to 0.85; the payload says 0.4242, and the
  report must print the payload.

Everything is offline, keyless and free of RNG. Every artifact is written out
byte by byte, and every timestamp is injected.

The module under test is being written in parallel against the same contract. The
accessors below (:func:`_get`, :func:`_judge_row`) accept a small set of
plausible *names* for a value the contract requires to exist, and fail loudly
listing what they looked for when none is present. They never adapt an expected
value, and absence of the module is a finding rather than a reason to skip.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import io
import json
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from opik_rigor import EvidenceLog, wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

from model_migration_kit.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    EVENT_COMPARISON,
    EVENT_JUDGING_COMPLETED,
    EVENT_RUN_STARTED,
    EVENT_VERDICT,
    Verdict,
)
from model_migration_kit.dimensions import (
    MIN_ITEMS_FOR_A_VERDICT,
    MIN_N_FOR_A_VERDICT,
    UNTAGGED,
    DimensionCounts,
    DimensionTally,
    TagCount,
    dimension_cell,
)
from model_migration_kit.errors import MigrationKitError
from model_migration_kit.evidence import stream_records
from model_migration_kit.goldenset import GoldenSet

try:  # The module is written in parallel with this file; absence is a finding,
    from model_migration_kit import report as _report  # not a reason to skip.
except Exception as exc:  # pragma: no cover - exercised only while it is missing
    _report = None
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


# --------------------------------------------------------------------------- #
# Fixed identities and fixed numbers. Every one is a literal, chosen to be
# distinctive enough that finding it in the document is not a coincidence.
# --------------------------------------------------------------------------- #

JUDGES_HASH = "b" * 64
CONFIG_HASH = "c" * 64
RUBRIC_HASH = "e" * 64
JUDGE_MODEL = "fake-judge-v1"

J = "accuracy"
BASELINE_MODEL = "model-a-20260101"
CANDIDATE_MODEL = "model-b-20260101"

N_PER_ITEM = 5
ITEM_IDS = tuple(f"item-{index:02d}" for index in range(1, 13))
EXPECTED_COMPLETIONS = len(ITEM_IDS) * N_PER_ITEM  # 12 x 5 = 60

NOW_A = "2026-08-13T09:00:00.000000+00:00"
NOW_B = "2026-08-14T21:30:00.000000+00:00"
TS_COMPARISON = "2026-08-13T08:59:58.000000+00:00"
TS_VERDICT = "2026-08-13T08:59:59.000000+00:00"
TS_JUDGING = "2026-08-13T08:59:57.000000+00:00"

#: Deliberately not the defaults, and deliberately all distinct, so an echo that
#: printed a built-in default instead of the recorded config would be visible.
THRESHOLDS = {
    "pass_rate_floor": 0.87,
    "alpha": 0.03,
    "confidence": 0.99,
    "judge_failure_tolerance": 0.07,
    "min_detectable_effect": 0.13,
    "power_target": 0.77,
}

#: The odd values. None of these is what a recomputation from the counts would
#: give, which is the entire point: contract §1.2 forbids the report deriving a
#: statistic for itself.
ODD_PASS_RATE = 0.4242
ODD_LOWER_BOUND = 0.3131
ODD_INTERVAL = (0.2121, 0.6161)
ODD_P_VALUE = 0.012345
#: 17 of 20 is 0.85 exactly. If 0.85 appears anywhere, something recomputed.
ODD_SUCCESSES = 17
ODD_N = 20
RECOMPUTED_RATE = 0.85

LATENCY = {
    "baseline": {"n": EXPECTED_COMPLETIONS, "median": 0.1234, "p90": 0.5678},
    "candidate": {"n": EXPECTED_COMPLETIONS, "median": 0.2345, "p90": 0.6789},
}

HOSTILE_IMG = '<img src="https://evil.example/pixel.png">'
HOSTILE_SCRIPT = '<script>fetch("https://evil.example/x")</script>'

#: Contract §2.2 item 0 fixes the band's wording. Both halves are asserted so a
#: band that dropped the explanation still fails.
FAKE_BAND_MARKERS = ("FAKE MODELS", "scripted responses")

_MISSING = object()


# --------------------------------------------------------------------------- #
# Oracles. stdlib only -- nothing in this section may import model_migration_kit.
# --------------------------------------------------------------------------- #


#: Attributes a browser fetches, per contract §2.4.
FETCHING_ATTRIBUTES = (
    "src",
    "href",
    "srcset",
    "poster",
    "data",
    "action",
    "formaction",
    "background",
    "cite",
    "longdesc",
    "manifest",
    "usemap",
    # Added by C20, which named the three attributes that were previously caught
    # only in passing by the shape rules it narrowed. This list is deliberately
    # an independent copy rather than an import, so that a change to
    # ``FETCHING_ATTRS`` has to be made twice on purpose -- and it worked: the
    # C12/C20 merge failed here, eight times, rather than silently.
    "ping",
    "xlink:href",
    "xml:base",
)

#: Elements whose mere presence is a violation, per contract §2.4.
FORBIDDEN_ELEMENTS = ("script", "link", "iframe", "object", "embed", "base")


#: Elements whose character data is machinery rather than content. A number that
#: appears only inside a stylesheet is not a number the reader was shown, so
#: value assertions are made against :attr:`_Document.text`, which excludes them.
_OPAQUE_ELEMENTS = frozenset({"style", "script"})

#: Elements that hold one printed value each. §2.6's em dash and §1.2's
#: no-recomputation rule are both claims about what is in a *cell*.
_CELL_ELEMENTS = frozenset({"td", "th", "dd"})


class _Document(HTMLParser):
    """An independent read of a rendered document: tags, ids, text, in order.

    Built on the stdlib parser so that "the literal text is visible in the page"
    and "there is no ``script`` element" are two separate readings of the same
    bytes. A substring search cannot tell those apart, which is exactly the
    distinction contract §2.4 is about -- and it also cannot tell a rate the
    reader was shown from the ``font-size: 0.85em`` in the inline stylesheet.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.ids: list[str] = []
        self.cells: list[str] = []
        self._chunks: list[str] = []
        self._title: list[str] = []
        self._in_title = False
        self._opaque = 0
        self._cell: list[str] | None = None

    # -- HTMLParser hooks ---------------------------------------------------- #

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        self.tags.append((tag, mapping))
        found = mapping.get("id")
        if found:
            self.ids.append(found)
        if tag == "title":
            self._in_title = True
        if tag in _OPAQUE_ELEMENTS:
            self._opaque += 1
        if tag in _CELL_ELEMENTS:
            self._close_cell()
            self._cell = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        self.tags.append((tag, mapping))
        found = mapping.get("id")
        if found:
            self.ids.append(found)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _OPAQUE_ELEMENTS and self._opaque:
            self._opaque -= 1
        if tag in _CELL_ELEMENTS:
            self._close_cell()

    def handle_data(self, data: str) -> None:
        if self._opaque:
            return
        self._chunks.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._in_title:
            self._title.append(data)

    def close(self) -> None:
        super().close()
        self._close_cell()

    # -- readings ------------------------------------------------------------ #

    def _close_cell(self) -> None:
        if self._cell is not None:
            self.cells.append("".join(self._cell).strip())
            self._cell = None

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    @property
    def title(self) -> str:
        return "".join(self._title)

    def count(self, tag: str) -> int:
        return sum(1 for name, _ in self.tags if name == tag)


def _parse(html: str) -> _Document:
    document = _Document()
    document.feed(html)
    document.close()
    return document


def _visible(html: str) -> str:
    """Everything the reader is shown, and nothing the stylesheet says."""
    return _parse(html).text


def _squeeze(text: str) -> str:
    """Collapse runs of whitespace, so ``47 / 60`` survives any indentation."""
    return re.sub(r"\s+", " ", text)


def _renderings(value: float) -> tuple[str, ...]:
    """Every plausible printed form of a probability, each one anchored.

    Anchored means each candidate contains a ``.`` or a ``%``, so a bare ``85``
    inside a 64-character hex digest can never satisfy -- or falsify -- one of
    these checks. Formatting is not fixed by the contract; the *value* is.
    """
    return (
        f"{value:.2f}",
        f"{value:.3f}",
        f"{value:.4f}",
        f"{value:.6f}",
        f"{value:g}" if "." in f"{value:g}" else f"{value:.1f}",
        f"{value * 100:.0f}%",
        f"{value * 100:.1f}%",
        f"{value * 100:.2f}%",
    )


def _shows(html: str, value: float) -> bool:
    return any(form in html for form in _renderings(value))


def _durations(value: float) -> tuple[str, ...]:
    """Printed forms of a latency. The contract fixes no precision for these.

    Latency is descriptive-only (§2.2 item 4), so rounding it is legitimate in a
    way that rounding a gate's lower bound would not be; what is asserted is that
    the recorded number reached the page, not how many decimals it kept.
    """
    return (
        f"{value:.2f}",
        f"{value:.3f}",
        f"{value:.4f}",
        f"{value:g}",
        f"{value * 1000:.0f} ms",
        f"{value * 1000:.0f}ms",
    )


def _hash_bytes(data: bytes) -> str:
    """The project's hashing convention, re-implemented from stdlib hashlib.

    ``contracts.py`` states it in its own docstring: sha256 of the bytes with
    CRLF normalised to LF. Computed here rather than imported so the provenance
    block is checked against something outside ``model_migration_kit``.
    """
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def test_the_hashing_oracle_agrees_with_the_projects_stated_convention() -> None:
    """Guards the oracle: if this fails, every hash expectation below is wrong."""
    from model_migration_kit.contracts import hash_bytes as _theirs

    assert _hash_bytes(b"line one\r\nline two\n") == _theirs(b"line one\r\nline two\n")
    assert _hash_bytes(b"a\nb\n") == hashlib.sha256(b"a\nb\n").hexdigest()


# --------------------------------------------------------------------------- #
# Builders: everything the report reads, written to disk byte by byte.
# --------------------------------------------------------------------------- #


def _write_goldenset(path: Path, items: Sequence[Mapping[str, Any]]) -> GoldenSet:
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in items),
        encoding="utf-8",
        newline="\n",
    )
    return GoldenSet.load(path)


def _default_items(
    *, ids: Sequence[str] = ITEM_IDS, hostile: bool = False
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, item_id in enumerate(ids):
        text = f"INPUT-TEXT for {item_id}"
        if hostile and index == 0:
            text = f"{text} {HOSTILE_IMG} {HOSTILE_SCRIPT}"
        out.append(
            {
                "id": item_id,
                "input": text,
                "tags": ["arithmetic"] if index % 2 == 0 else ["extraction"],
            }
        )
    return out


def _write_run(
    path: Path,
    *,
    model_id: str,
    adapter: str,
    goldenset_hash: str,
    goldenset_path: str,
    item_ids: Sequence[str],
    outputs: Mapping[str, Sequence[str | None]],
    n_per_item: int = N_PER_ITEM,
    parts: int = 1,
    duration: float = 0.25,
    items_expected: int | None = None,
) -> Path:
    """A run artifact in ``runner.py``'s on-disk format.

    ``parts`` writes that many header records, which is how ``RunArtifact``
    counts a resumed run -- contract §2.2 item 2 requires the report to disclose
    it rather than hide it.
    """
    header = {
        "record": "header",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": model_id,
        "goldenset_hash": goldenset_hash,
        "goldenset_path": goldenset_path,
        "n_per_item": n_per_item,
        "created": TS_JUDGING,
        "adapter": adapter,
        "notes": {
            "goldenset_items": len(item_ids) if items_expected is None else items_expected
        },
    }
    lines: list[dict[str, Any]] = [dict(header) for _ in range(parts)]
    for item_id in item_ids:
        for index, output in enumerate(outputs.get(item_id, ())):
            lines.append(
                {
                    "record": "completion",
                    "item_id": item_id,
                    "sample_index": index,
                    "output": output,
                    "duration": duration,
                    "error": None if output is not None else "timeout after 30s",
                    "error_type": None if output is not None else "SampleTimeout",
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


def _write_judged(
    path: Path,
    *,
    model_id: str,
    goldenset_hash: str,
    source: str,
    item_ids: Sequence[str],
    passes: Mapping[str, int],
    reasons: Mapping[str, str],
    n_per_item: int = N_PER_ITEM,
    judges: Sequence[str] = (J,),
) -> Path:
    """A judged artifact in ``judging.py``'s on-disk format."""
    header = {
        "record": "header",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": model_id,
        "goldenset_hash": goldenset_hash,
        "judges_hash": JUDGES_HASH,
        "judges": [
            {
                "name": name,
                "model": JUDGE_MODEL,
                "adapter_class": "FakeAdapter",
                "rubric_hash": RUBRIC_HASH,
            }
            for name in judges
        ],
        "n_per_item": n_per_item,
        "source": source,
        "created": TS_JUDGING,
        "notes": {"thresholds": dict(THRESHOLDS)},
    }
    lines: list[dict[str, Any]] = [header]
    for name in judges:
        for item_id in item_ids:
            for index in range(n_per_item):
                ok = index < passes.get(item_id, 0)
                lines.append(
                    {
                        "record": "verdict",
                        "judge": name,
                        "item_id": item_id,
                        "sample_index": index,
                        "passed": ok,
                        "score": 5.0 if ok else 1.0,
                        "imputed": False,
                        "parse_failure": False,
                        "reason": reasons.get(item_id, f"graded {item_id}"),
                        "error": None,
                    }
                )
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _gate(
    *,
    successes: int,
    n: int,
    pass_rate: float | None,
    lower_bound: float | None,
    interval: tuple[float, float] | None,
    label: str,
    floor: float = THRESHOLDS["pass_rate_floor"],
    confidence: float = THRESHOLDS["confidence"],
    underpowered: bool = False,
    runs_needed: int | None = None,
) -> dict[str, Any]:
    """One side's ``assert_pass_rate`` dict, exactly as §0 lists its keys.

    Passed through verbatim by ``comparison.py`` (contract §1.2), so every number
    the per-judge table prints has to come from here and from nowhere else.
    """
    return {
        "gate": "pass_rate",
        "label": label,
        "passed": lower_bound is not None and lower_bound >= floor,
        "n": n,
        "successes": successes,
        "failures": n - successes,
        "pass_rate": pass_rate,
        "lower_bound": lower_bound,
        "interval_lower": None if interval is None else interval[0],
        "interval_upper": None if interval is None else interval[1],
        "min_rate": floor,
        "confidence": confidence,
        "method": "wilson-one-sided",
        "underpowered": underpowered,
        "runs_needed": runs_needed,
    }


def _judge_payload(
    *,
    name: str = J,
    baseline: Mapping[str, Any] | None = None,
    candidate: Mapping[str, Any] | None = None,
    p_value: float | None = ODD_P_VALUE,
    test_ran: str = "mann-whitney-u",
    note: str = "",
    regressed: bool = True,
    floor_cleared: bool = False,
    underpowered: bool = False,
    thresholds: Mapping[str, Any] | None = None,
    item_counts_baseline: Mapping[str, int] | None = None,
    item_counts_candidate: Mapping[str, int] | None = None,
    items: int = len(ITEM_IDS),
) -> dict[str, Any]:
    """One row of the ``judges`` array, in ``comparison.JudgeComparison`` shape.

    ``thresholds`` flows into the gate dicts because ``min_rate`` and
    ``confidence`` travel *inside* rigor's report (§0), so a run at a different
    floor must not leave the old floor lying in the payload for the echo test to
    trip over.
    """
    active = dict(thresholds or THRESHOLDS)
    floor = active["pass_rate_floor"]
    confidence = active["confidence"]
    alpha = active["alpha"]
    return {
        "name": name,
        "model_id": JUDGE_MODEL,
        "rubric_hash": RUBRIC_HASH,
        "baseline": dict(
            baseline
            if baseline is not None
            else _gate(
                successes=ODD_SUCCESSES,
                n=ODD_N,
                pass_rate=ODD_PASS_RATE,
                lower_bound=ODD_LOWER_BOUND,
                interval=ODD_INTERVAL,
                label=f"{name}:baseline",
                floor=floor,
                confidence=confidence,
            )
        ),
        "candidate": dict(
            candidate
            if candidate is not None
            else _gate(
                successes=11,
                n=ODD_N,
                pass_rate=0.5353,
                lower_bound=0.3838,
                interval=(0.2929, 0.7474),
                label=f"{name}:candidate",
                floor=floor,
                confidence=confidence,
            )
        ),
        "regression": None
        if p_value is None
        else {
            "p_value": p_value,
            "u_statistic": 1234.0,
            "alpha": alpha,
            "median_current": 3.0,
            "median_baseline": 4.0,
            "test": "mann-whitney-u",
            "alternative": "less",
            "degenerate": False,
        },
        "p_value": p_value,
        "holm_threshold": alpha,
        "alpha": alpha,
        "regressed": regressed,
        "floor_cleared": floor_cleared,
        "underpowered": underpowered,
        "runs_needed": None,
        "mw_powered": True,
        "power": {
            "n_observed": EXPECTED_COMPLETIONS,
            "n_required": 137,
            "powered": False,
            "baseline_rate": ODD_PASS_RATE,
            "min_detectable_effect": active["min_detectable_effect"],
            "power_target": active["power_target"],
            "alpha": alpha,
            "method": "two-proportion-normal-approximation",
        },
        "test_ran": test_ran,
        "note": note,
        "imputed": {"baseline": 0, "candidate": 0},
        "parse_failures": {"baseline": 0, "candidate": 0},
        "missing_scores": {"baseline": 0, "candidate": 0},
        "item_counts": {
            "baseline": dict(
                item_counts_baseline or {"passing": 9, "failing": 1, "unstable": 2}
            ),
            "candidate": dict(
                item_counts_candidate or {"passing": 6, "failing": 3, "unstable": 3}
            ),
            "items": items,
        },
    }


def _side(
    *,
    model_id: str,
    adapter: str,
    artifact: Path,
    judged_artifact: Path,
    parts: int = 1,
    records: int = EXPECTED_COMPLETIONS,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "adapter": adapter,
        "adapters": [adapter],
        "artifact": str(artifact),
        "judged_artifact": str(judged_artifact),
        "n_per_item": N_PER_ITEM,
        "parts": parts,
        "run_parts": parts,
        "records": records,
        "imputed": 0,
        "parse_failures": 0,
    }


def _grouped(changes: Sequence[tuple[str, int, int, int, int]]) -> list[dict[str, Any]]:
    """``flips``/``gains`` in ``comparison._grouped``'s item-major shape."""
    return [
        {
            "item_id": item_id,
            "judges": [J],
            "changes": [
                {
                    "item_id": item_id,
                    "judge": J,
                    "baseline_passes": bp,
                    "baseline_n": bn,
                    "candidate_passes": cp,
                    "candidate_n": cn,
                    "baseline_state": "pass",
                    "candidate_state": "fail",
                    "label": f"{bp}/{bn} -> {cp}/{cn}",
                }
            ],
        }
        for item_id, bp, bn, cp, cn in changes
    ]


def _write_evidence(path: Path, records: Sequence[Mapping[str, Any]]) -> Path:
    """The evidence log in rigor's own on-disk format, with injected timestamps.

    Written by hand rather than through ``EvidenceLog.append`` because ``append``
    stamps wall-clock time, and contract §2.6 requires the completeness strip to
    name *the* last timestamp found in the log. A test that could not choose the
    timestamp could not assert it.
    """
    path.write_bytes(
        b"".join(
            (json.dumps(dict(record), sort_keys=True) + "\n").encode("utf-8")
            for record in records
        )
    )
    return path


def _record(event_type: str, payload: Mapping[str, Any], ts: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "payload": dict(payload),
        "schema_version": 1,
        "ts": ts,
    }


@dataclasses.dataclass
class Scenario:
    """Everything one report reads, on disk, plus the payloads that produced it."""

    root: Path
    evidence: Path
    goldenset: Path
    goldenset_hash: str
    items: tuple[str, ...]
    comparison: dict[str, Any]
    verdict: dict[str, Any] | None


def _scenario(
    root: Path,
    *,
    baseline_adapter: str = "AnthropicAdapter",
    candidate_adapter: str = "OpenAICompatAdapter",
    judges: Sequence[Mapping[str, Any]] | None = None,
    thresholds: Mapping[str, Any] | None = None,
    hostile: bool = False,
    item_ids: Sequence[str] = ITEM_IDS,
    with_verdict: bool = True,
    verdict: str = Verdict.NO_GO,
    baseline_parts: int = 1,
    candidate_parts: int = 1,
    candidate_completions: int | None = None,
    recorded_goldenset_hash: str | None = None,
    candidate_output: str | None = None,
    flips: Sequence[tuple[str, int, int, int, int]] = (
        ("item-03", 5, 5, 0, 5),
        ("item-07", 4, 5, 1, 5),
    ),
    gains: Sequence[tuple[str, int, int, int, int]] = (("item-11", 0, 5, 5, 5),),
) -> Scenario:
    """The standard run: 12 items x 5 draws a side, one judge, two flips.

    Every path recorded in the payload is the real path of a file written here,
    so the reconstruction of contract §1.1 -- log, then run artifacts, then
    judged artifacts, then golden set -- has something to find at each step.
    """
    root.mkdir(parents=True, exist_ok=True)
    goldenset_path = root / "goldenset.jsonl"
    golden = _write_goldenset(goldenset_path, _default_items(ids=item_ids, hostile=hostile))

    per_side = len(item_ids) * N_PER_ITEM
    base_outputs = {
        item_id: [f"BASE-OUT {item_id} #{index}" for index in range(N_PER_ITEM)]
        for item_id in item_ids
    }
    cand_text = candidate_output
    cand_outputs = {
        item_id: [
            f"CAND-OUT {item_id} #{index}" if cand_text is None else cand_text
            for index in range(N_PER_ITEM)
        ]
        for item_id in item_ids
    }
    if candidate_completions is not None:
        remaining = candidate_completions
        trimmed: dict[str, list[str | None]] = {}
        for item_id in item_ids:
            take = max(0, min(N_PER_ITEM, remaining))
            trimmed[item_id] = cand_outputs[item_id][:take]
            remaining -= take
        cand_outputs = trimmed

    baseline_run = _write_run(
        root / "baseline.jsonl",
        model_id=BASELINE_MODEL,
        adapter=baseline_adapter,
        goldenset_hash=golden.hash,
        goldenset_path=str(goldenset_path),
        item_ids=item_ids,
        outputs=base_outputs,
        parts=baseline_parts,
        duration=LATENCY["baseline"]["median"],
    )
    candidate_run = _write_run(
        root / "candidate.jsonl",
        model_id=CANDIDATE_MODEL,
        adapter=candidate_adapter,
        goldenset_hash=golden.hash,
        goldenset_path=str(goldenset_path),
        item_ids=item_ids,
        outputs=cand_outputs,
        parts=candidate_parts,
        duration=LATENCY["candidate"]["median"],
    )
    baseline_judged = _write_judged(
        root / "baseline.judged.jsonl",
        model_id=BASELINE_MODEL,
        goldenset_hash=golden.hash,
        source=str(baseline_run),
        item_ids=item_ids,
        passes={item_id: N_PER_ITEM for item_id in item_ids},
        reasons={item_id: f"BASE-REASON {item_id}" for item_id in item_ids},
    )
    candidate_judged = _write_judged(
        root / "candidate.judged.jsonl",
        model_id=CANDIDATE_MODEL,
        goldenset_hash=golden.hash,
        source=str(candidate_run),
        item_ids=item_ids,
        passes={item_id: 0 for item_id in item_ids},
        reasons={item_id: f"CAND-REASON {item_id}" for item_id in item_ids},
    )

    active_thresholds = dict(thresholds or THRESHOLDS)
    judge_rows = [
        dict(one)
        for one in (
            judges if judges is not None else [_judge_payload(thresholds=active_thresholds)]
        )
    ]

    payload: dict[str, Any] = {
        "created": TS_COMPARISON,
        "goldenset_hash": recorded_goldenset_hash or golden.hash,
        "goldenset_path": str(goldenset_path),
        "judges_hash": JUDGES_HASH,
        "config_hash": CONFIG_HASH,
        "config_path": str(root / "migkit.toml"),
        "baseline": _side(
            model_id=BASELINE_MODEL,
            adapter=baseline_adapter,
            artifact=baseline_run,
            judged_artifact=baseline_judged,
            parts=baseline_parts,
            records=per_side,
        ),
        "candidate": _side(
            model_id=CANDIDATE_MODEL,
            adapter=candidate_adapter,
            artifact=candidate_run,
            judged_artifact=candidate_judged,
            parts=candidate_parts,
            records=per_side if candidate_completions is None else candidate_completions,
        ),
        "thresholds": dict(active_thresholds),
        "judges": judge_rows,
        "flips": _grouped(flips),
        "gains": _grouped(gains),
        "unstable": _grouped(()),
        "latency": {side: dict(stat) for side, stat in LATENCY.items()},
        "completion_rates": {
            "baseline": {"passes": ODD_SUCCESSES, "n": ODD_N},
            "candidate": {"passes": 11, "n": ODD_N},
            "unit": "completion",
        },
        "item_counts": {
            "unit": "item",
            "per_judge": {one["name"]: dict(one["item_counts"]) for one in judge_rows},
        },
        "n_per_item": N_PER_ITEM,
        "warnings": [],
    }
    verdict_payload = {
        "verdict": verdict,
        "exit_code": Verdict.exit_code(verdict),
        "reason": "REASON-SENTENCE: judge 'accuracy' shows a significant regression.",
        "decided_by": "rule 1",
        "rule": 1,
        "thresholds": dict(active_thresholds),
        "judges": [{"name": J, "regressed": True}],
        "baseline_model": BASELINE_MODEL,
        "candidate_model": CANDIDATE_MODEL,
    }

    records = [
        _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING),
        _record(EVENT_JUDGING_COMPLETED, {"model_id": CANDIDATE_MODEL}, TS_JUDGING),
        _record(EVENT_COMPARISON, payload, TS_COMPARISON),
    ]
    if with_verdict:
        records.append(_record(EVENT_VERDICT, verdict_payload, TS_VERDICT))

    evidence = _write_evidence(root / "evidence.jsonl", records)
    return Scenario(
        root=root,
        evidence=evidence,
        goldenset=goldenset_path,
        goldenset_hash=golden.hash,
        items=tuple(item_ids),
        comparison=payload,
        verdict=verdict_payload if with_verdict else None,
    )


# --------------------------------------------------------------------------- #
# Accessors. These adapt to *names*, never to values.
# --------------------------------------------------------------------------- #


def _module() -> Any:
    if _report is None:
        raise AssertionError(
            f"model_migration_kit.report could not be imported: {_IMPORT_ERROR!r}"
        )
    return _report


def _surface(obj: Any) -> list[str]:
    if isinstance(obj, Mapping):
        return sorted(str(key) for key in obj)
    return sorted(name for name in dir(obj) if not name.startswith("_"))


def _get(obj: Any, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    if default is not _MISSING:
        return default
    raise AssertionError(
        f"the contract requires one of {list(names)} on {type(obj).__name__}; "
        f"it exposes {_surface(obj)}"
    )


def _from_evidence(scenario: Scenario, **kwargs: Any) -> Any:
    model_class = _get(_module(), "ReportModel")
    builder = _get(model_class, "from_evidence")
    kwargs.setdefault("now", NOW_A)
    return builder(scenario.evidence, **kwargs)


def _html(model: Any, **kwargs: Any) -> str:
    kwargs.setdefault("now", NOW_A)
    return _get(_module(), "render_html_string")(model, **kwargs)


def _rendered(root: Path, **scenario_kwargs: Any) -> tuple[Any, str]:
    scenario = _scenario(root, **scenario_kwargs)
    model = _from_evidence(scenario)
    return model, _html(model)


def _judge_row(model: Any, name: str = J) -> Any:
    rows = _get(model, "judges", "judge_rows", "rows", "per_judge")
    if isinstance(rows, Mapping):
        assert name in rows, f"no row for judge {name!r} in {sorted(rows)}"
        return rows[name]
    for row in rows:
        if _get(row, "name", "judge", default=None) == name:
            return row
    raise AssertionError(f"no row for judge {name!r} among {[_surface(r) for r in rows]}")


def _rate_stat(row: Any, side: str) -> Any:
    return _get(row, side, f"{side}_stats", f"{side}_rate")


def _ids(rows: Any) -> list[str]:
    return [row if isinstance(row, str) else _get(row, "item_id", "id") for row in rows]


def _urls(html: str) -> tuple[Any, ...]:
    return tuple(_get(_module(), "external_urls")(html))


def _violation_tags(violations: Sequence[Any]) -> list[str]:
    return [str(_get(one, "tag", "element")) for one in violations]


def _module_source() -> str:
    return inspect.getsource(_module())


# --------------------------------------------------------------------------- #
# 0. The module exists, and exposes what the contract names.
# --------------------------------------------------------------------------- #


PUBLIC_NAMES = (
    "RateStat",
    "JudgeRow",
    "FlipRow",
    "RunSummary",
    "Completeness",
    "MethodologySection",
    "ReportModel",
    "UrlViolation",
    "render_terminal",
    "render_html",
    "render_html_string",
    "methodology_sections",
    "external_urls",
    "assert_self_contained",
)


@pytest.mark.parametrize("name", PUBLIC_NAMES)
def test_the_public_surface_the_contract_names_exists(name: str) -> None:
    """Contract §2.1 lists this API in full. Every name in it is load-bearing."""
    module = _module()
    assert hasattr(module, name), (
        f"contract §2.1 requires {name!r} on model_migration_kit.report; it exposes "
        f"{_surface(module)}"
    )


def test_from_evidence_takes_paths_and_never_a_live_object() -> None:
    """Invariant 2's signature rule, contract §1.1.

    "No function in ``report.py`` accepts a ``ComparisonReport``, a
    ``JudgedArtifact``, or any other live object produced earlier in the same
    process. Its inputs are paths." A renderer handed the in-memory result is a
    renderer whose partial-render path only executes after a crash -- which is to
    say, a path that has never run when it is needed.
    """
    forbidden = ("ComparisonReport", "JudgedArtifact", "JudgePanel", "GoldenSet")
    module = _module()
    signature = inspect.signature(_get(_get(module, "ReportModel"), "from_evidence"))
    annotations = " ".join(str(one.annotation) for one in signature.parameters.values())
    for name in forbidden:
        assert name not in annotations, (
            f"from_evidence declares a {name} parameter; §1.1 says its inputs are paths"
        )
    first = [one for one in signature.parameters.values() if one.name != "cls"][0]
    assert "Path" in str(first.annotation) or "str" in str(first.annotation), (
        f"the first parameter of from_evidence is {first.annotation!r}, not a path"
    )


# --------------------------------------------------------------------------- #
# 1. Self-containment: the detector, and the proof that it is not vacuous.
#    Contract §2.4; test inventory items 1-4.
# --------------------------------------------------------------------------- #


#: Contract §2.4's fixture, verbatim. Three offences, three tags, three
#: violations -- the number is stated in the contract, not counted from a run.
NEGATIVE_CONTROL = """<!doctype html>
<html><head>
<link rel="stylesheet" href="https://cdn.example/x.css">
<style>@import url(https://f/f.css)</style>
</head><body>
<img src="//cdn.example/logo.png">
</body></html>"""


def test_the_detector_is_not_vacuous() -> None:
    """Contract §2.4 test 2 and §6 item 1: exactly three, at ``link``/``img``/``style``.

    This is the test that matters. A detector returning ``()`` unconditionally
    passes the rendered-report test forever and is worth nothing; so does one
    that reports a single violation and stops at the first. The count and the
    tags are both fixed by the contract.
    """
    violations = _urls(NEGATIVE_CONTROL)
    tags = _violation_tags(violations)
    assert len(violations) == 3, (
        f"contract §2.4 fixes this fixture at exactly 3 violations; got "
        f"{len(violations)} at {tags}"
    )
    assert sorted(tags) == ["img", "link", "style"], tags


def test_the_negative_control_names_a_line_and_an_attribute_for_each_violation() -> None:
    """``UrlViolation(line, column, tag, attribute, value, reason)``, §2.4.

    "so a template author sees the offending line rather than 'the report is not
    self-contained'". A violation that cannot say where it is does not do the job
    the contract gives it.

    ``value`` is required only of the violations that *have* a URL. One of §2.4's
    clauses fires on the presence of an element rather than on anything it
    carries, and there is no URL to name in that case.
    """
    violations = _urls(NEGATIVE_CONTROL)
    for one in violations:
        line = int(_get(one, "line", "lineno"))
        assert line >= 1, f"violation reports line {line}"
        assert str(_get(one, "tag", "element")).strip(), one
        assert str(_get(one, "reason", "why", "message")).strip(), one

    by_tag = {str(_get(one, "tag", "element")): one for one in violations}
    assert "//cdn.example/logo.png" in str(_get(by_tag["img"], "value", "url", "target")), (
        "a URL-valued violation that cannot say which URL is not actionable"
    )


@pytest.mark.parametrize(
    ("fragment", "tag"),
    [
        ('<link rel="stylesheet" href="https://cdn.example/x.css">', "link"),
        ('<img src="//cdn.example/logo.png">', "img"),
        ("<style>@import url(https://f/f.css)</style>", "style"),
        ("<style>body{background:url(https://f/bg.png)}</style>", "style"),
        ('<div style="background:url(https://f/bg.png)">x</div>', "div"),
        ('<img src="http://plain.example/p.gif">', "img"),
        ('<a href="https://example.com/doc">doc</a>', "a"),
        ('<iframe src="page.html"></iframe>', "iframe"),
        ("<object></object>", "object"),
        ("<embed>", "embed"),
        ('<base href="/">', "base"),
        ("<script>1</script>", "script"),
        ('<video poster="https://f/p.png"></video>', "video"),
        ('<form action="https://f/collect"></form>', "form"),
    ],
)
def test_each_forbidden_construct_is_detected_on_its_own(fragment: str, tag: str) -> None:
    """Every clause of contract §2.4's list, one at a time.

    Each fixture carries exactly one offending element, so exactly one violation
    is the contract's own arithmetic: the three-element fixture above yields
    three. A detector that merges or drops any of these clauses fails here rather
    than silently in production.
    """
    violations = _urls(f"<html><body>{fragment}</body></html>")
    assert len(violations) == 1, (
        f"{fragment!r} carries one offence; got {len(violations)} "
        f"at {_violation_tags(violations)}"
    )
    assert _violation_tags(violations) == [tag]


@pytest.mark.parametrize("attribute", FETCHING_ATTRIBUTES)
def test_every_fetching_attribute_named_in_the_contract_is_checked(attribute: str) -> None:
    """§2.4's third clause, one attribute at a time.

    "any fetching attribute whose value is neither a ``#``-fragment nor a
    ``data:`` URI". A relative path is the interesting case: it carries no
    scheme, so the first two clauses miss it, and a browser opening the file from
    a compliance reviewer's downloads folder still tries to fetch it.

    The rule §2.4 states is scoped to the *attribute*, not to the element that
    carries it, so the carrier here is a neutral ``<span>``. A detector that only
    looks at attributes on the elements they conventionally belong to is a
    narrower detector than the contract specifies.
    """
    fragment = f'<span {attribute}="assets/thing.bin">x</span>'
    violations = _urls(f"<html><body>{fragment}</body></html>")
    assert len(violations) == 1, (
        f"{attribute}= is a fetching attribute per §2.4 and a relative path in it "
        f"was not flagged; got {violations}"
    )
    assert str(_get(violations[0], "attribute", "attr")) == attribute


@pytest.mark.parametrize(
    "fragment",
    [
        '<img src="data:image/png;base64,iVBORw0KGgo=">',
        '<a href="#flip-item-03">jump</a>',
        '<a href="#">top</a>',
        "<style>body{font-family:system-ui,-apple-system,\"Segoe UI\",sans-serif}</style>",
        "<style>.b{background:url(data:image/gif;base64,R0lGOD)}</style>",
        '<details><summary id="s">more</summary><p>body</p></details>',
        '<td class="num">0.4242</td>',
    ],
)
def test_a_self_contained_construct_is_not_reported(fragment: str) -> None:
    """The false-positive control, and it is as necessary as the vacuity one.

    A detector that flags everything also passes "the fixture yields three" only
    by accident, and would make ``render_html`` unable to write any document at
    all. ``data:`` URIs and ``#``-fragments are named as permitted in §2.4.
    """
    violations = _urls(f"<html><body>{fragment}</body></html>")
    assert violations == (), f"{fragment!r} is self-contained; flagged {violations}"


def test_a_url_appearing_as_escaped_text_is_not_a_violation() -> None:
    """§2.4's stated reason for a parser rather than a regex.

    "a regex cannot tell a URL that appears as escaped text (harmless: no fetch)
    from one that appears as an attribute value (a fetch)". This document
    contains the same URL twice over in its *text*, and a browser fetches
    neither.
    """
    html = (
        "<html><body><p>the model said &lt;img src=&quot;https://evil.example/p.png"
        "&quot;&gt; and https://evil.example/x</p></body></html>"
    )
    assert _urls(html) == ()
    assert "https://evil.example/p.png" in _parse(html).text


def test_assert_self_contained_raises_and_lists_every_violation() -> None:
    """§2.4: "raises with every violation listed, each naming line, tag and attribute"."""
    checker = _get(_module(), "assert_self_contained")
    with pytest.raises(Exception) as caught:
        checker(NEGATIVE_CONTROL, source="fixture.html")
    message = str(caught.value)
    assert isinstance(caught.value, MigrationKitError), (
        f"a self-containment failure should be a model-migration-kit error, got "
        f"{type(caught.value).__name__}"
    )
    for tag in ("link", "img", "style"):
        assert tag in message, f"the message does not name the {tag!r} violation: {message}"
    assert "fixture.html" in message


def test_assert_self_contained_accepts_a_document_that_is_self_contained() -> None:
    checker = _get(_module(), "assert_self_contained")
    assert (
        checker(
            '<html><head><meta charset="utf-8"><style>body{color:#111}</style></head>'
            '<body><a href="#x">x</a></body></html>'
        )
        is None
    )


def test_the_rendered_report_has_no_external_url(tmp_path: Path) -> None:
    """Contract §2.4 test 1 and §6 item 2, on a report built from real evidence."""
    _, html = _rendered(tmp_path / "clean")
    assert _urls(html) == ()


def test_the_rendered_report_has_zero_script_and_zero_link_elements(tmp_path: Path) -> None:
    """§2.2: "The page contains zero ``<script>`` elements"; §6 item 4 counts by parser.

    Substring counting would be satisfied by an escaped ``&lt;script&gt;`` in a
    model output, which is precisely the case that must *not* count.
    """
    _, html = _rendered(tmp_path / "noscript")
    document = _parse(html)
    for tag in FORBIDDEN_ELEMENTS:
        assert document.count(tag) == 0, f"the document contains {document.count(tag)} <{tag}>"


# --------------------------------------------------------------------------- #
# 2. Hostile model output. Contract §2.4 test 3, §6 item 3.
# --------------------------------------------------------------------------- #


def test_hostile_model_output_is_escaped_rather_than_rendered(tmp_path: Path) -> None:
    """The real path: model outputs are rendered into this page.

    jinja2's default is ``autoescape=False`` (§0), and a golden-set input or a
    completion containing ``<img src="https://...">`` therefore becomes a genuine
    network fetch from a document sitting inside a compliance review. Two
    assertions, and they only mean something together: no fetching element
    survives, *and* the literal text is still visible -- deleting the output
    would satisfy the first alone.
    """
    scenario = _scenario(
        tmp_path / "hostile", hostile=True, candidate_output=f"{HOSTILE_IMG}{HOSTILE_SCRIPT}"
    )
    html = _html(_from_evidence(scenario))

    assert _urls(html) == (), "hostile output produced a fetchable URL in the document"
    document = _parse(html)
    assert document.count("script") == 0
    assert document.count("img") == 0
    assert HOSTILE_SCRIPT in document.text, (
        "the hostile completion is neither rendered nor visible as text; escaping "
        "is not the same as deletion, and an exhibit that drops what the model said "
        "is not an exhibit"
    )
    assert HOSTILE_IMG in document.text


def test_hostile_text_in_a_golden_set_input_is_escaped_too(tmp_path: Path) -> None:
    """The input side of §6 item 3: the golden set is attacker-influenced as well."""
    scenario = _scenario(tmp_path / "hostile-input", hostile=True)
    html = _html(_from_evidence(scenario))
    assert _urls(html) == ()
    assert _parse(html).count("img") == 0


# --------------------------------------------------------------------------- #
# 3. No recomputation. Contract §1.2's rule.
# --------------------------------------------------------------------------- #


def test_the_report_prints_the_recorded_rate_and_never_a_recomputed_one(
    tmp_path: Path,
) -> None:
    """§1.2: "the report never recomputes a statistic".

    The payload says 17 successes of 20 and a ``pass_rate`` of 0.4242, which is
    deliberately not 17/20. A renderer that derives the rate from the counts
    prints 0.85 and disagrees with the verdict that was recorded -- "the one
    thing a change-control document may never do is contradict itself".

    Asserted against the visible text rather than the raw bytes, because the
    inline stylesheet is full of decimals that no reader ever sees.
    """
    _, html = _rendered(tmp_path / "odd")
    visible = _visible(html)
    assert _shows(visible, ODD_PASS_RATE), (
        f"the recorded pass rate {ODD_PASS_RATE} does not appear in the document"
    )
    assert not _shows(visible, RECOMPUTED_RATE), (
        f"{RECOMPUTED_RATE} appears in the document: that is {ODD_SUCCESSES}/{ODD_N} "
        f"recomputed, not the {ODD_PASS_RATE} the evidence records"
    )


@pytest.mark.parametrize(
    "value", [ODD_LOWER_BOUND, ODD_INTERVAL[0], ODD_INTERVAL[1], ODD_P_VALUE]
)
def test_every_recorded_statistic_reaches_the_document(
    tmp_path: Path, value: float
) -> None:
    """§2.2 item 3: interval, one-sided bound and p-value are all printed."""
    _, html = _rendered(tmp_path / f"stat-{value}")
    assert _shows(_visible(html), value), (
        f"{value} is in the evidence payload and not in the document"
    )


def test_the_two_sided_interval_and_the_one_sided_bound_are_labelled_separately(
    tmp_path: Path,
) -> None:
    """§2.2 item 3, and it is a correctness point rather than a layout one.

    rigor's own docstring warns the two are not interchangeable: for 14/20 the
    one-sided bound is 0.5162 and the two-sided lower end is 0.4810, so a reader
    who conflates them thinks the gate is looser than it is.
    """
    _, html = _rendered(tmp_path / "bounds")
    lowered = html.lower()
    assert "one-sided" in lowered or "one sided" in lowered
    assert "two-sided" in lowered or "two sided" in lowered


def test_the_report_module_computes_no_statistic_of_its_own() -> None:
    """§1.2's rule, checked structurally as well as behaviourally.

    A renderer that re-derives a p-value from the artifacts can disagree with the
    verdict that was recorded. The cheapest guarantee that it cannot is that the
    module never *calls* a statistical primitive.

    Read with :mod:`ast` rather than by substring, so that a docstring explaining
    why ``wilson_interval(0, 0)`` is never reached does not read as a call to it.
    """
    forbidden = {
        "wilson_interval",
        "wilson_lower_bound",
        "assert_pass_rate",
        "assert_no_regression",
        "mannwhitneyu",
        "holm_bonferroni",
        "required_sample_size",
        "compare",
    }
    tree = ast.parse(_module_source())
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    offenders = sorted(forbidden & called)
    assert not offenders, (
        f"report.py calls {offenders}; §1.2 says the report never recomputes a "
        f"statistic, and a number it derives can contradict the recorded verdict"
    )


# --------------------------------------------------------------------------- #
# 4. n == 0 is a rendering state, not a computation. §2.6; §6 item 21.
# --------------------------------------------------------------------------- #


def test_a_rate_over_zero_runs_raises_in_rigor_which_is_why_it_is_a_state() -> None:
    """Verified here against the installed opik-rigor, not taken from §0.

    This is the fact the rendering rule exists for. What the report is pinned on
    is the em dash below; this test only establishes that the alternative -- ask
    rigor -- is not available.
    """
    with pytest.raises(ValueError) as caught:
        wilson_interval(0, 0)
    assert "n must be >= 1" in str(caught.value)


def _zero_judge() -> dict[str, Any]:
    return _judge_payload(
        baseline=_gate(
            successes=0,
            n=0,
            pass_rate=None,
            lower_bound=None,
            interval=None,
            label=f"{J}:baseline",
            underpowered=True,
        ),
        candidate=_gate(
            successes=0,
            n=0,
            pass_rate=None,
            lower_bound=None,
            interval=None,
            label=f"{J}:candidate",
            underpowered=True,
        ),
        p_value=None,
        test_ran="not-run",
        note="no scores on one or both sides; the regression test could not run",
        regressed=False,
        item_counts_baseline={"passing": 0, "failing": 0, "unstable": 0},
        item_counts_candidate={"passing": 0, "failing": 0, "unstable": 0},
        items=0,
    )


def test_a_judge_with_no_observed_completions_carries_none_not_a_number(
    tmp_path: Path,
) -> None:
    """§2.1's ``RateStat`` docstring: every optional field is ``None`` exactly when
    there was nothing to measure."""
    scenario = _scenario(tmp_path / "zero", judges=[_zero_judge()])
    row = _judge_row(_from_evidence(scenario))
    for side in ("baseline", "candidate"):
        stat = _rate_stat(row, side)
        assert int(_get(stat, "n")) == 0
        assert _get(stat, "rate") is None
        assert _get(stat, "interval") is None
        assert _get(stat, "lower_bound") is None


def test_zero_observed_completions_render_as_an_em_dash(tmp_path: Path) -> None:
    """§2.6: "the cell prints ``—``", and §6 item 21.

    The failure mode this forbids is not an exception -- it is a plausible-looking
    ``0.0%`` in a change-control document, invented for a judge that measured
    nothing.

    Six cells go blank, not one: rate, printed interval and one-sided bound, on
    each of the two sides. So the count is compared against the same document
    with the same layout and real numbers in it, which cancels out whatever em
    dashes the page uses decoratively.
    """
    zero = _parse(_html(_from_evidence(_scenario(tmp_path / "zero-html", judges=[_zero_judge()]))))
    measured = _parse(_html(_from_evidence(_scenario(tmp_path / "measured"))))

    assert zero.text.count("—") >= measured.text.count("—") + 6, (
        f"a judge with n == 0 has six unavailable cells; the document shows "
        f"{zero.text.count('—')} em dashes against {measured.text.count('—')} when "
        f"the same cells carry numbers"
    )
    for cell in zero.cells:
        assert cell.strip(". ") not in {"None", "nan", "null", "NaN"}, (
            f"a cell reads {cell!r}; §2.6 says n == 0 is a rendering state, so the "
            f"absence prints as an em dash rather than leaking a Python value"
        )
    invented = {"0%", "0.0%", "0.00%", "0.0", "0.00", "0.000", "0.0000"}
    for cell in zero.cells:
        assert cell.strip() not in invented, (
            f"a cell reads {cell!r} for a judge that measured nothing; a rate over "
            f"zero runs is not a rate, and an invented zero is worse than a blank"
        )


def test_a_zero_judge_still_shows_its_observed_over_expected_counts(
    tmp_path: Path,
) -> None:
    """§2.6: counts are observed over expected everywhere, including at zero."""
    scenario = _scenario(tmp_path / "zero-counts", judges=[_zero_judge()])
    html = _html(_from_evidence(scenario))
    assert "0" in _parse(html).text


# --------------------------------------------------------------------------- #
# 5. Partial rendering. Contract §2.6; §6 items 17-22.
# --------------------------------------------------------------------------- #


def test_a_log_with_a_torn_final_line_still_renders(tmp_path: Path) -> None:
    """§0 and §6 item 17: rigor drops the torn line, and the report is unaffected.

    The record count the report works from must equal ``len(EvidenceLog.read())``
    -- not one more, which would mean the renderer read the fragment.
    """
    scenario = _scenario(tmp_path / "torn")
    data = scenario.evidence.read_bytes()
    scenario.evidence.write_bytes(data + b'{"event_type": "migkit.ver')

    read = EvidenceLog(scenario.evidence).read()
    assert len(read) == 4, "the oracle itself is wrong about how rigor reads a torn tail"

    model = _from_evidence(scenario)
    assert _get(model, "verdict") == Verdict.NO_GO
    assert _urls(_html(model)) == ()


def test_a_comparison_without_a_verdict_renders_everything_and_exits_three(
    tmp_path: Path,
) -> None:
    """§2.6 row 3 and §6 item 18: killed between the comparison and the verdict.

    "A partial report is evidence, never a decision." Every table renders; the
    banner says so; the exit code is 3, which is the README's "the tool could not
    produce a verdict" and never a quiet GO.
    """
    scenario = _scenario(tmp_path / "noverdict", with_verdict=False)
    model = _from_evidence(scenario)

    assert _get(model, "verdict") is None
    assert int(_get(model, "exit_code")) == Verdict.exit_code(Verdict.ERROR) == 3

    html = _html(model)
    assert "NO VERDICT" in _parse(html).text
    for marker in (J, "item-03", str(THRESHOLDS["pass_rate_floor"])):
        assert marker in html, f"{marker!r} is missing: a partial report still renders"


def test_a_log_with_no_comparison_record_is_refused(tmp_path: Path) -> None:
    """§2.1 and §2.6 row 2 and §6 item 19: the one refusal.

    A run that started and died before comparing has nothing to report *on*. This
    is wrong data rather than partial data, and it is the only case that raises.
    """
    root = tmp_path / "nocomparison"
    root.mkdir()
    evidence = _write_evidence(
        root / "evidence.jsonl",
        [_record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING)],
    )
    with pytest.raises(MigrationKitError):
        _get(_get(_module(), "ReportModel"), "from_evidence")(evidence, now=NOW_A)


def test_a_missing_evidence_file_is_refused_rather_than_rendered_empty(
    tmp_path: Path,
) -> None:
    """§0 and §6 item 19: rigor returns ``[]`` for a missing file, so the report
    must check the path itself or a typo renders as "nothing happened"."""
    missing = tmp_path / "not-here" / "evidence.jsonl"
    assert EvidenceLog(missing).read() == [], "the oracle for rigor's behaviour is wrong"
    with pytest.raises(MigrationKitError):
        _get(_get(_module(), "ReportModel"), "from_evidence")(missing, now=NOW_A)


def test_the_two_refusals_raise_the_type_the_contract_names(tmp_path: Path) -> None:
    """§2.1 and §6 item 19 both say ``ArtifactError``, and that is what is pinned.

    There is a live tension here worth recording rather than smoothing over. The
    contract was written while ``errors.py`` was frozen and open decision D2 was
    unresolved; D2 was later resolved by unfreezing it once to add
    ``ReportError``, whose own docstring describes precisely these two cases --
    "evidence that cannot be read or does not describe a comparison at all". So
    ``ReportError`` is arguably the sharper type, and §2.1 is arguably stale.

    The contract is what this suite tests, so ``ArtifactError`` is the
    expectation. The invariant both readings agree on -- a ``MigrationKitError``,
    which is what the CLI maps to exit 3 -- is pinned by the two tests above and
    holds whichever way the lead settles it.
    """
    from model_migration_kit.errors import ArtifactError

    root = tmp_path / "types"
    root.mkdir()
    with pytest.raises(ArtifactError):
        _get(_get(_module(), "ReportModel"), "from_evidence")(
            root / "gone" / "evidence.jsonl", now=NOW_A
        )

    evidence = _write_evidence(
        root / "evidence.jsonl",
        [_record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING)],
    )
    with pytest.raises(ArtifactError):
        _get(_get(_module(), "ReportModel"), "from_evidence")(evidence, now=NOW_A)


def test_a_short_artifact_shows_observed_over_expected_and_scales_nothing_up(
    tmp_path: Path,
) -> None:
    """§2.6 and §6 item 20: 47 of 60, in the run summary and the judge table.

    "A rate over the items that finished is biased whenever the run died on a
    slow or hard item -- the exact circumstance that kills runs", so the
    shortfall travels next to the number rather than in a footnote.
    """
    scenario = _scenario(tmp_path / "short", candidate_completions=47)
    model = _from_evidence(scenario)

    candidate = _get(model, "candidate")
    assert int(_get(candidate, "completions", "observed", "n")) == 47
    assert int(_get(candidate, "expected", "expected_completions")) == 60

    completeness = _get(model, "completeness")
    assert _get(completeness, "complete") is False
    assert int(_get(completeness, "expected_completions", "expected")) >= 60
    missing = " ".join(str(one) for one in _get(completeness, "missing", default=()))
    assert "47" in missing and "60" in missing, (
        f"the completeness strip does not say what is missing: {missing!r}"
    )

    squeezed = _squeeze(_parse(_html(model)).text)
    assert "47 / 60" in squeezed or "47/60" in squeezed, (
        "the document does not print observed over expected"
    )


def test_the_completeness_strip_names_the_last_event_and_its_timestamp(
    tmp_path: Path,
) -> None:
    """§2.6 and §6 item 22: the reader has to know where the run stopped."""
    scenario = _scenario(tmp_path / "last-event", with_verdict=False)
    model = _from_evidence(scenario)
    completeness = _get(model, "completeness")

    assert _get(completeness, "last_event") == EVENT_COMPARISON
    assert _get(completeness, "last_ts", "last_timestamp") == TS_COMPARISON
    assert TS_COMPARISON in _html(model)


def test_a_complete_run_says_so(tmp_path: Path) -> None:
    """The happy path through the same reader, which is §1.1's whole argument.

    "Routing the happy path through the same reader means every green test run
    exercises the reconstruction."
    """
    model, _ = _rendered(tmp_path / "complete")
    completeness = _get(model, "completeness")
    observed = int(_get(completeness, "observed_completions", "observed"))
    expected = int(_get(completeness, "expected_completions", "expected"))
    assert _get(completeness, "complete") is True
    assert observed == expected, f"a complete run reports {observed} of {expected}"
    assert observed in (EXPECTED_COMPLETIONS, 2 * EXPECTED_COMPLETIONS), (
        f"{observed} is neither one side's 60 completions nor both sides' 120"
    )


# --------------------------------------------------------------------------- #
# 6. The fake-model band. Contract §5.3; §6 item 13.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("baseline_adapter", "candidate_adapter", "is_demo"),
    [
        ("FakeAdapter", "FakeAdapter", True),
        ("AnthropicAdapter", "FakeAdapter", True),
        ("FakeAdapter", "AnthropicAdapter", True),
        ("FakeScriptedAdapter", "AnthropicAdapter", True),
        ("AnthropicAdapter", "OpenAICompatAdapter", False),
        ("OpenAICompatAdapter", "AnthropicAdapter", False),
    ],
)
def test_the_fake_band_appears_iff_an_adapter_name_starts_with_fake(
    tmp_path: Path, baseline_adapter: str, candidate_adapter: str, is_demo: bool
) -> None:
    """§5.3: demo-ness is derived from the artifacts, never from a flag.

    "you cannot obtain a clean-looking report from fake models by avoiding
    ``migkit demo`` -- anyone wiring a ``FakeAdapter`` by hand gets the same red
    band. A flag-driven banner would be exactly the banner that goes missing in
    the screenshot someone pastes into a deck."
    """
    model, html = _rendered(
        tmp_path / f"band-{baseline_adapter}-{candidate_adapter}",
        baseline_adapter=baseline_adapter,
        candidate_adapter=candidate_adapter,
    )
    assert bool(_get(model, "is_demo")) is is_demo

    document = _parse(html)
    for marker in FAKE_BAND_MARKERS:
        found = marker.lower() in document.text.lower()
        assert found is is_demo, (
            f"band marker {marker!r} {'missing' if is_demo else 'present'} for "
            f"adapters {baseline_adapter}/{candidate_adapter}"
        )
    assert ("FAKE" in document.title.upper()) is is_demo, (
        f"§2.2 item 0 repeats the warning in the <title>; got {document.title!r}"
    )


def test_no_flag_anywhere_can_turn_the_fake_band_on_or_off() -> None:
    """§5.3, checked on the signature: the band has no switch to forget.

    A ``demo=``/``fake=`` keyword would reintroduce the exact failure the rule is
    written against, since the default would then be the clean-looking report.
    """
    module = _module()
    signature = inspect.signature(_get(_get(module, "ReportModel"), "from_evidence"))
    for name in signature.parameters:
        assert "demo" not in name.lower() and "fake" not in name.lower(), (
            f"from_evidence takes {name!r}; §5.3 derives demo-ness from the artifacts"
        )
    for renderer in ("render_html", "render_html_string", "render_terminal"):
        parameters = inspect.signature(_get(module, renderer)).parameters
        for name in parameters:
            assert "demo" not in name.lower() and "fake" not in name.lower(), (
                f"{renderer} takes {name!r}; the band is not a rendering option"
            )


def test_the_fake_band_sits_above_the_verdict_banner(tmp_path: Path) -> None:
    """§2.2: "Nothing may be inserted above the banner except the demo warning"."""
    _, html = _rendered(
        tmp_path / "band-order",
        baseline_adapter="FakeAdapter",
        candidate_adapter="FakeAdapter",
    )
    upper = html.upper()
    body = upper.find("<BODY")
    assert body >= 0, "the document has no <body> element"
    band = upper.index("FAKE MODELS", body)
    banner = upper.index(Verdict.NO_GO, band)
    assert band < banner


def test_the_model_ids_and_the_adapter_row_also_say_the_models_are_fake(
    tmp_path: Path,
) -> None:
    """§5.3: five places say it, and none of them is a footnote."""
    _, html = _rendered(
        tmp_path / "band-places",
        baseline_adapter="FakeAdapter",
        candidate_adapter="FakeAdapter",
    )
    text = _parse(html).text
    assert text.count("FakeAdapter") >= 2, (
        "the adapter row in 'what was compared' must name the adapter on both sides"
    )


# --------------------------------------------------------------------------- #
# 7. Thresholds echoed with their source. Contract §4; §6 item 8.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(THRESHOLDS))
def test_every_threshold_in_the_config_appears_in_the_document(
    tmp_path: Path, name: str
) -> None:
    """§4: "Every threshold echoes into the report so nobody can quietly loosen a
    gate without it showing in the evidence"."""
    _, html = _rendered(tmp_path / "thresholds")
    assert _shows(_visible(html), THRESHOLDS[name]), (
        f"threshold {name} = {THRESHOLDS[name]} does not appear in the document"
    )


def test_each_threshold_is_echoed_with_the_source_it_came_from(tmp_path: Path) -> None:
    """§4: "an echoed number without its provenance does not achieve that".

    The reader has to be able to tell a deliberate project policy from a flag
    somebody added to make the build go green.
    """
    scenario = _scenario(tmp_path / "sources")
    model = _from_evidence(scenario)
    sources = _get(model, "threshold_sources")

    assert sources, "threshold_sources is empty; §4 requires a source per threshold"
    assert "pass_rate_floor" in sources, sorted(sources)

    html = _html(model)
    for key, source in sources.items():
        assert str(source) in html, (
            f"the source {source!r} of threshold {key!r} is not printed beside it"
        )
    assert scenario.comparison["config_path"] in html, (
        "§2.2 item 2 requires the config path in 'what was compared'"
    )


def test_changing_a_threshold_changes_both_the_echo_and_the_appendix(
    tmp_path: Path,
) -> None:
    """§2.3 and §6 item 8: the appendix is generated, not pasted.

    "A hardcoded appendix passes a 'contains the word Wilson' test forever,
    including after the confidence is changed to 0.80."
    """
    loose = dict(THRESHOLDS, pass_rate_floor=0.55)
    strict_model, strict_html = _rendered(tmp_path / "strict")
    loose_model, loose_html = _rendered(tmp_path / "loose", thresholds=loose)

    assert _shows(_visible(strict_html), THRESHOLDS["pass_rate_floor"])
    assert _shows(_visible(loose_html), 0.55)
    assert not _shows(_visible(loose_html), THRESHOLDS["pass_rate_floor"]), (
        "the old floor is still printed after the config changed"
    )

    strict_body = _appendix_text(strict_model)
    loose_body = _appendix_text(loose_model)
    assert strict_body != loose_body, (
        "the methodology appendix is identical under two different floors, so it is "
        "pasted rather than substituted from the model"
    )
    assert _shows(loose_body, 0.55)


# --------------------------------------------------------------------------- #
# 8. The methodology appendix. Contract §2.3; §6 item 9.
# --------------------------------------------------------------------------- #


def _appendix_text(model: Any) -> str:
    sections = _get(_module(), "methodology_sections")(model)
    assert sections, "methodology_sections returned nothing; §2.3 requires six sections"
    parts: list[str] = []
    for section in sections:
        parts.append(str(_get(section, "heading", "title")))
        parts.extend(str(one) for one in _get(section, "body", "paragraphs", "text"))
    return "\n".join(parts)


@pytest.mark.parametrize(
    ("label", "keywords"),
    [
        ("what was tested", ("what was tested",)),
        ("why these tests", ("why these tests", "why this test")),
        ("why nonparametric", ("nonparametric", "non-parametric")),
        ("what REVIEW means", ("what review means", "review means")),
        ("the decision table", ("decision table",)),
        ("what this report is not", ("is not",)),
    ],
)
def test_the_appendix_carries_each_section_the_contract_lists(
    tmp_path: Path, label: str, keywords: Sequence[str]
) -> None:
    """§2.3 lists six sections; the appendix is the SR 11-7 artifact."""
    model, _ = _rendered(tmp_path / "appendix")
    headings = " | ".join(
        str(_get(one, "heading", "title")).lower()
        for one in _get(_module(), "methodology_sections")(model)
    )
    assert any(word in headings for word in keywords), (
        f"no appendix section for {label!r}; headings are {headings}"
    )


def test_the_appendix_names_the_tests_that_actually_ran(tmp_path: Path) -> None:
    """§2.3 and §6 item 9: Wilson, Mann-Whitney, one-sided, and this run's alpha."""
    model, html = _rendered(tmp_path / "appendix-tests")
    body = _appendix_text(model).lower()
    assert "wilson" in body
    assert "mann-whitney" in body or "mann whitney" in body
    assert "one-sided" in body or "one sided" in body or "one-tailed" in body
    assert _shows(body, THRESHOLDS["alpha"]), "the appendix does not use this run's alpha"
    assert _shows(body, THRESHOLDS["confidence"]), (
        "the appendix does not use this run's confidence"
    )
    assert "wilson" in html.lower(), "the appendix is not in the rendered document"


def test_the_appendix_says_what_review_means_and_that_it_never_becomes_go(
    tmp_path: Path,
) -> None:
    """§2.3 and invariant 5: REVIEW is never silently converted to GO."""
    model, _ = _rendered(tmp_path / "appendix-review")
    body = _appendix_text(model)
    assert Verdict.REVIEW in body
    assert Verdict.GO in body, (
        "the appendix must say that REVIEW is never silently converted to GO"
    )
    assert "underpowered" in body.lower() or "power" in body.lower()


def test_the_appendix_states_the_ordinal_argument_for_a_nonparametric_test(
    tmp_path: Path,
) -> None:
    """§2.3: judge scores are a bounded 1-5 ordinal scale (verified in §0).

    "The distance between 3 and 4 is not the distance between 4 and 5, so a
    t-test's interval-scale assumption is not merely unmet, it is unmeetable."
    That is the actual argument, and a generic one would not survive this.
    """
    model, _ = _rendered(tmp_path / "appendix-ordinal")
    body = _appendix_text(model).lower()
    assert "ordinal" in body
    assert "1-5" in body or "1 to 5" in body or "1–5" in body


def test_a_judge_tested_on_outcomes_says_so_in_its_own_row(tmp_path: Path) -> None:
    """§2.2 item 3 and §6 item 9: the note lives inside the table, not in a legend.

    A reader told "no regression" is entitled to know whether that was measured
    on a 1-5 ordinal scale or on pass/fail outcomes, which carry far less
    information.
    """
    scenario = _scenario(
        tmp_path / "outcomes",
        judges=[
            _judge_payload(
                test_ran="mann-whitney-u-on-outcomes",
                note="scores absent; tested on pass/fail outcomes",
            )
        ],
    )
    model = _from_evidence(scenario)
    row = _judge_row(model)
    assert _get(row, "test_ran") == "mann-whitney-u-on-outcomes"
    assert "outcomes" in str(_get(row, "note"))

    text = _parse(_html(model)).text
    assert "mann-whitney-u-on-outcomes" in text
    assert "scores absent; tested on pass/fail outcomes" in text


# --------------------------------------------------------------------------- #
# 9. Structure and order of the document. Contract §2.2; §6 item 10.
# --------------------------------------------------------------------------- #


#: §2.2's order, as landmarks findable in an ``id``. Keywords rather than exact
#: ids because the contract fixes the order and the sections, not the spelling.
SECTION_ORDER = (
    ("verdict banner", ("verdict", "banner")),
    ("what was compared", ("compared",)),
    ("per-judge tables", ("judge",)),
    ("latency", ("latency",)),
    ("flips", ("flip",)),
    ("gains", ("gain",)),
    ("methodology appendix", ("appendix", "methodology")),
    ("provenance footer", ("provenance", "footer")),
)


def test_the_sections_appear_in_the_order_the_plan_fixes(tmp_path: Path) -> None:
    """§2.2 and §6 item 10, "asserted by comparing the order of their anchor ids".

    The first screenful is the only part some readers see, so the order is part
    of the specification rather than a layout preference.
    """
    _, html = _rendered(tmp_path / "order")
    ids = [one.lower() for one in _parse(html).ids]
    assert ids, "the document carries no id attributes to anchor its sections"

    positions: list[tuple[str, int]] = []
    for label, keywords in SECTION_ORDER:
        found = [i for i, one in enumerate(ids) if any(word in one for word in keywords)]
        assert found, f"no anchor id for the {label!r} section; ids are {ids}"
        positions.append((label, found[0]))

    ordered = [label for label, _ in positions]
    by_position = [label for label, _ in sorted(positions, key=lambda pair: pair[1])]
    assert ordered == by_position, (
        f"sections are out of order: §2.2 fixes {ordered}, the document has {by_position}"
    )


def test_latency_is_labelled_descriptive_only(tmp_path: Path) -> None:
    """§2.2 item 4: "It is never a gate, and saying so in the table stops it
    becoming one by habit"."""
    _, html = _rendered(tmp_path / "latency")
    visible = _visible(html)
    lowered = visible.lower()
    assert "descriptive" in lowered
    assert "gate" in lowered, "the table must say latency is never a gate"
    for value in (
        LATENCY["baseline"]["median"],
        LATENCY["baseline"]["p90"],
        LATENCY["candidate"]["median"],
        LATENCY["candidate"]["p90"],
    ):
        assert any(form in visible for form in _durations(value)), (
            f"latency {value} is recorded and not printed"
        )


def test_gains_are_shown_and_are_said_not_to_offset_flips(tmp_path: Path) -> None:
    """§2.2 item 5: the number is shown because its absence would make the report
    an argument rather than a measurement, and the sentence is there because
    someone will otherwise net them."""
    model, html = _rendered(tmp_path / "gains")
    assert _ids(_get(model, "gains")) == ["item-11"]
    text = _parse(html).text
    assert "item-11" in text
    assert "offset" in text.lower() or "net" in text.lower()


def test_the_verdict_banner_carries_the_reason_and_the_exit_code(
    tmp_path: Path,
) -> None:
    """§1.2: "a banner with no stated reason is a colour, not a finding"; §2.2 item 1."""
    scenario = _scenario(tmp_path / "banner")
    model = _from_evidence(scenario)
    assert _get(model, "reason") == scenario.verdict["reason"]
    assert _get(model, "decided_by") == "rule 1"
    assert int(_get(model, "exit_code")) == 1

    text = _parse(_html(model)).text
    assert Verdict.NO_GO in text
    assert "REASON-SENTENCE" in text
    assert re.search(r"\b1\b", text), "the exit code a CI system would receive is missing"
    assert NOW_A in _html(model), "§2.2 item 1 requires the generation timestamp"


# --------------------------------------------------------------------------- #
# 10. Flips, the golden-set gate, and truncation. §2.5; §6 items 14-16.
# --------------------------------------------------------------------------- #


def test_flip_rows_carry_their_ids_in_goldenset_order(tmp_path: Path) -> None:
    """§2.2 item 5 and §6 item 14: "Flips are ordered by golden-set order, which
    is stable across runs".

    The golden set here is deliberately written out of alphabetical order, and
    the flips are recorded in yet another order, so neither the file order of the
    evidence nor a sort can be mistaken for the golden-set order.
    """
    ids = ("zeta", "alpha", "mid", "beta")
    scenario = _scenario(
        tmp_path / "fliporder",
        item_ids=ids,
        flips=(("beta", 5, 5, 0, 5), ("zeta", 5, 5, 1, 5), ("alpha", 4, 5, 0, 5)),
        gains=(),
    )
    model = _from_evidence(scenario)
    assert _ids(_get(model, "flips")) == ["zeta", "alpha", "beta"]


def test_a_flip_row_shows_every_draw_from_both_sides_and_the_candidate_reason(
    tmp_path: Path,
) -> None:
    """§2.2 item 5 and §6 item 14: the input, all n outputs a side, and the reason.

    The flip list is the artifact a human actually reads, and D6 fixes its volume
    at every sample of both models behind ``<details>``.
    """
    scenario = _scenario(tmp_path / "flipbody")
    model = _from_evidence(scenario)
    row = next(one for one in _get(model, "flips") if _get(one, "item_id", "id") == "item-03")

    assert _get(row, "input") == "INPUT-TEXT for item-03"
    assert len(_get(row, "baseline_outputs")) == N_PER_ITEM
    assert len(_get(row, "candidate_outputs")) == N_PER_ITEM
    assert tuple(_get(row, "judges")) == (J,)
    reasons = _get(row, "reasons")
    assert reasons[J] == "CAND-REASON item-03", (
        "the flip must carry the *candidate*-side reason: the baseline's reason "
        "explains the state the migration is leaving, not the one it arrives at"
    )

    document = _parse(_html(model))
    assert document.count("details") >= 1, "§2.2 item 5 fixes <details> as the mechanism"
    for index in range(N_PER_ITEM):
        assert f"BASE-OUT item-03 #{index}" in document.text
        assert f"CAND-OUT item-03 #{index}" in document.text


def test_a_changed_golden_set_suppresses_the_inputs_but_not_the_outputs(
    tmp_path: Path,
) -> None:
    """§2.5 and §6 item 15.

    "Pairing today's file with last week's outputs would be a fabricated exhibit,
    and it would be indistinguishable from a real one." The ids, the outputs and
    the tags survive; only the inputs go.
    """
    scenario = _scenario(tmp_path / "drifted", recorded_goldenset_hash="f" * 64)
    model = _from_evidence(scenario)

    golden = _get(model, "goldenset")
    assert _get(golden, "available") is False

    row = next(one for one in _get(model, "flips") if _get(one, "item_id", "id") == "item-03")
    assert _get(row, "input") is None

    document = _parse(_html(model))
    assert "INPUT-TEXT for item-03" not in document.text, (
        "the golden set on disk is not the one that was run; showing its text would "
        "be a fabricated exhibit"
    )
    assert "item-03" in document.text
    assert "CAND-OUT item-03 #0" in document.text
    warnings = " ".join(str(one) for one in _get(model, "warnings", default=()))
    assert "golden" in warnings.lower(), (
        f"§2.5 requires a visible band naming the mismatch; warnings are {warnings!r}"
    )


def test_a_missing_golden_set_degrades_the_same_way(tmp_path: Path) -> None:
    """§1.1: "Anything a step cannot supply degrades that section and is named"."""
    scenario = _scenario(tmp_path / "nogolden")
    scenario.goldenset.unlink()
    model = _from_evidence(scenario)

    assert _get(_get(model, "goldenset"), "available") is False
    row = next(iter(_get(model, "flips")))
    assert _get(row, "input") is None
    assert _urls(_html(model)) == ()


def test_a_long_output_is_truncated_and_says_so(tmp_path: Path) -> None:
    """§4 and §6 item 16: "Invisible truncation in an exhibit is a misquotation"."""
    long_output = "LONGTAIL" * 200  # 1600 characters
    scenario = _scenario(tmp_path / "truncated", candidate_output=long_output)
    model = _from_evidence(scenario, max_output_chars=100)
    html = _html(model)

    assert long_output not in html, "max_output_chars did not truncate the output"
    assert "LONGTAIL" in _parse(html).text, "truncation removed the output entirely"
    squeezed = _squeeze(_parse(html).text).lower()
    assert "truncated" in squeezed
    assert "100" in squeezed, "the truncation marker does not name the limit"

    row = next(iter(_get(model, "flips")))
    assert _get(row, "truncated") is True


# --------------------------------------------------------------------------- #
# 11. Item counts, not an item-level rate. build-plan §6, amended 2026-08-13.
# --------------------------------------------------------------------------- #


def test_the_report_prints_three_item_counts_and_never_an_item_level_rate(
    tmp_path: Path,
) -> None:
    """build-plan §6: "The report therefore prints three item counts -- passing,
    failing, unstable -- beside the completion-level rate, never an item-level
    rate."

    "There is no single item-level *rate*, because a three-state classification
    does not reduce to one fraction without smuggling the ambiguous items into
    one bucket or the other."
    """
    counts = {"passing": 2, "failing": 3, "unstable": 7}
    scenario = _scenario(
        tmp_path / "itemcounts",
        judges=[_judge_payload(item_counts_candidate=counts, items=12)],
    )
    text = _parse(_html(_from_evidence(scenario))).text.lower()
    for label in ("passing", "failing", "unstable"):
        assert label in text, f"the three item counts must be named; {label!r} is missing"

    module = _module()
    for class_name in ("ReportModel", "JudgeRow"):
        holder = _get(module, class_name)
        names = [one for one in dir(holder) if not one.startswith("_")]
        if dataclasses.is_dataclass(holder):
            names += [field.name for field in dataclasses.fields(holder)]
        offenders = [
            one
            for one in names
            if re.search(r"item.*rate|rate.*item", one, flags=re.IGNORECASE)
        ]
        assert not offenders, (
            f"{class_name} exposes {offenders}; build-plan §6 forbids an item-level "
            f"rate, which is a fraction that can only exist by putting the unstable "
            f"items into one bucket or the other"
        )


def test_the_constructible_case_from_the_amendment_reads_correctly(
    tmp_path: Path,
) -> None:
    """build-plan §6's own worked example: ten items each passing 3 of 5.

    "a pooled completion rate of 0.60, an empty flip list, an empty gain list, and
    ten unstable items -- which tells the reader what is actually true, that this
    migration cannot be judged from this evidence at this n."
    """
    ids = tuple(f"item-{index:02d}" for index in range(1, 11))
    counts = {"passing": 0, "failing": 0, "unstable": 10}
    scenario = _scenario(
        tmp_path / "threefifths",
        item_ids=ids,
        flips=(),
        gains=(),
        judges=[
            _judge_payload(
                candidate=_gate(
                    successes=30,
                    n=50,
                    pass_rate=0.60,
                    lower_bound=0.4823,
                    interval=(0.4624, 0.7223),
                    label=f"{J}:candidate",
                ),
                item_counts_baseline=counts,
                item_counts_candidate=counts,
                items=10,
            )
        ],
    )
    model = _from_evidence(scenario)
    assert _ids(_get(model, "flips")) == []
    assert _ids(_get(model, "gains")) == []

    html = _html(model)
    assert _shows(_visible(html), 0.60), "the pooled completion rate of 0.60 is not printed"
    squeezed = _squeeze(_parse(html).text).lower()
    assert "10" in squeezed and "unstable" in squeezed


# --------------------------------------------------------------------------- #
# 12. Provenance, hashes, parts. §2.2 item 7; §6 items 11-12.
# --------------------------------------------------------------------------- #


def test_every_hash_is_printed_in_full_character_for_character(
    tmp_path: Path,
) -> None:
    """§6 item 11: "no truncation in the machine-readable positions".

    A hash truncated where a reader would compare it is a hash that cannot be
    compared, which makes the provenance block decorative.
    """
    scenario = _scenario(tmp_path / "hashes")
    model = _from_evidence(scenario)
    hashes = _get(model, "hashes")

    expected = {
        "goldenset": scenario.goldenset_hash,
        "judges": JUDGES_HASH,
        "config": CONFIG_HASH,
    }
    html = _html(model)
    for key, value in expected.items():
        assert _get(hashes, key, f"{key}_hash") == value
        assert value in html, f"the {key} hash is not printed in full"


def test_the_evidence_hash_is_the_hash_of_the_evidence_file(tmp_path: Path) -> None:
    """§2.2 item 7: the evidence log's path and hash, under the project convention.

    The oracle is stdlib hashlib, not ``contracts.hash_file``, so this checks the
    report against something outside ``model_migration_kit``.
    """
    scenario = _scenario(tmp_path / "evhash")
    model = _from_evidence(scenario)
    expected = _hash_bytes(scenario.evidence.read_bytes())

    assert _get(model, "evidence_hash") == expected
    html = _html(model)
    assert expected in html
    assert str(scenario.evidence) in html


def test_a_resumed_run_is_disclosed_rather_than_hidden(tmp_path: Path) -> None:
    """§2.2 item 2 and §6 item 12: "candidate completed in 2 parts"."""
    scenario = _scenario(tmp_path / "parts", candidate_parts=2)
    model = _from_evidence(scenario)
    assert int(_get(_get(model, "candidate"), "parts")) == 2
    squeezed = _squeeze(_parse(_html(model)).text)
    assert "2 parts" in squeezed, "a resumed run must be noted, not hidden"


def test_the_provenance_footer_names_both_versions(tmp_path: Path) -> None:
    """§2.2 item 7: tool version and ``opik_rigor`` version."""
    import opik_rigor

    model, html = _rendered(tmp_path / "versions")
    assert _get(model, "rigor_version") == opik_rigor.__version__
    assert opik_rigor.__version__ in html
    assert str(_get(model, "tool_version")).strip()
    assert str(_get(model, "tool_version")) in html


def test_what_was_compared_names_both_models_and_the_golden_set(
    tmp_path: Path,
) -> None:
    """§2.2 item 2: a definition list, not prose."""
    scenario = _scenario(tmp_path / "compared")
    html = _html(_from_evidence(scenario))
    text = _parse(html).text
    for marker in (BASELINE_MODEL, CANDIDATE_MODEL, str(scenario.goldenset)):
        assert marker in text, f"{marker!r} is missing from 'what was compared'"
    assert str(len(ITEM_IDS)) in text, "the golden set's size is not stated"
    assert "arithmetic" in text and "extraction" in text, (
        "§2.2 item 2 requires the tag distribution"
    )


# --------------------------------------------------------------------------- #
# 13. Writing the file. §2.1, §2.4; §6 items 5-7.
# --------------------------------------------------------------------------- #


def test_render_html_checks_its_own_output_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§2.4 and §6 item 5: detection at render time, not only in tests.

    "A template edit that adds a font link fails the render rather than shipping
    a file that only CI notices." The check is stubbed to fail so that both
    halves of the claim are observable: it raises, and the destination file does
    not exist afterwards.
    """
    module = _module()
    boom = MigrationKitError("stubbed self-containment failure")

    def _refuse(html: str, **kwargs: Any) -> None:
        raise boom

    monkeypatch.setattr(module, "assert_self_contained", _refuse)

    scenario = _scenario(tmp_path / "refuse")
    model = _from_evidence(scenario)
    out = tmp_path / "refuse" / "report.html"
    with pytest.raises(MigrationKitError):
        _get(module, "render_html")(model, out, now=NOW_A)
    assert not out.exists(), (
        "render_html wrote the file before validating it, so a document with an "
        "external URL reaches disk and only CI notices"
    )


def test_render_html_is_the_one_that_validates() -> None:
    """The structural half of §2.4: ``render_html`` calls ``assert_self_contained``."""
    source = _module_source()
    match = re.search(r"\ndef render_html\b.*?(?=\ndef |\Z)", source, flags=re.DOTALL)
    assert match, "render_html is not a module-level function"
    assert "assert_self_contained" in match.group(0), (
        "render_html does not call assert_self_contained; §2.4 requires the check "
        "at render time rather than only in the test suite"
    )


def test_the_same_model_and_the_same_now_render_byte_identically(
    tmp_path: Path,
) -> None:
    """§6 item 6: reproducibility is what makes the file comparable across runs."""
    scenario = _scenario(tmp_path / "determinism")
    model = _from_evidence(scenario)
    render = _get(_module(), "render_html")

    first = Path(render(model, tmp_path / "determinism" / "a.html", now=NOW_A))
    second = Path(render(model, tmp_path / "determinism" / "b.html", now=NOW_A))
    assert first.read_bytes() == second.read_bytes()


def test_a_different_now_changes_only_the_timestamp(tmp_path: Path) -> None:
    """§6 item 6: "with a different ``now``, exactly the timestamp differs"."""
    scenario = _scenario(tmp_path / "now")
    render = _get(_module(), "render_html")
    model_a = _from_evidence(scenario, now=NOW_A)
    model_b = _from_evidence(scenario, now=NOW_B)

    a = Path(render(model_a, tmp_path / "now" / "a.html", now=NOW_A)).read_text(
        encoding="utf-8"
    )
    b = Path(render(model_b, tmp_path / "now" / "b.html", now=NOW_B)).read_text(
        encoding="utf-8"
    )
    assert a != b, "the generation timestamp is not in the document at all"
    assert a.replace(NOW_A, "<T>") == b.replace(NOW_B, "<T>"), (
        "two renders differ in more than the timestamp"
    )


def test_the_written_file_is_utf8_with_lf_and_declares_its_charset(
    tmp_path: Path,
) -> None:
    """§2.1 and §6 item 7, asserted on the bytes.

    On Windows ``Path.write_text`` defaults to the ANSI code page, which mangles
    or refuses non-ASCII model output, and CRLF would make the file's hash differ
    per platform -- the same reason ``.gitattributes`` forces LF.
    """
    accented = "café naïve — 你好"
    scenario = _scenario(tmp_path / "bytes", candidate_output=accented)
    model = _from_evidence(scenario)
    out = Path(_get(_module(), "render_html")(model, tmp_path / "bytes" / "r.html", now=NOW_A))

    data = out.read_bytes()
    assert b"\r\n" not in data, "the file was written with CRLF; its hash is platform-dependent"
    text = data.decode("utf-8")
    assert accented in text
    assert re.search(r'<meta[^>]+charset=["\']?utf-8', text, flags=re.IGNORECASE), (
        "the document does not declare its encoding"
    )


def test_render_html_returns_the_path_it_wrote(tmp_path: Path) -> None:
    """§2.1: ``render_html(...) -> Path``. The CLI prints it as the next action."""
    scenario = _scenario(tmp_path / "returned")
    model = _from_evidence(scenario)
    out = tmp_path / "returned" / "r.html"
    written = _get(_module(), "render_html")(model, out, now=NOW_A)
    assert Path(written) == out
    assert out.is_file()


# --------------------------------------------------------------------------- #
# 14. Terminal rendering. Contract §2.7.
# --------------------------------------------------------------------------- #


def test_the_terminal_render_carries_the_verdict_without_colour(
    tmp_path: Path,
) -> None:
    """§2.7: "Colour must never be the only carrier of a fact".

    The last line of stdout is always ``VERDICT: <X> (exit <n>)``, so a CI log
    that scrolls past 200 lines of table still ends with the finding.
    """
    from rich.console import Console

    scenario = _scenario(tmp_path / "terminal")
    model = _from_evidence(scenario)
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, no_color=True, force_terminal=False)
    _get(_module(), "render_terminal")(model, console=console)

    output = buffer.getvalue()
    assert Verdict.NO_GO in output
    lines = [one.rstrip() for one in output.splitlines() if one.strip()]
    assert lines, "render_terminal wrote nothing"
    assert lines[-1].startswith("VERDICT:"), f"last line is {lines[-1]!r}"
    assert Verdict.NO_GO in lines[-1] and "1" in lines[-1]


def test_the_terminal_render_carries_the_fake_band_too(tmp_path: Path) -> None:
    """§5.3: "The terminal rendering carries the same band above its verdict panel"."""
    from rich.console import Console

    scenario = _scenario(
        tmp_path / "terminal-fake",
        baseline_adapter="FakeAdapter",
        candidate_adapter="FakeAdapter",
    )
    buffer = io.StringIO()
    _get(_module(), "render_terminal")(
        _from_evidence(scenario),
        console=Console(file=buffer, width=100, no_color=True, force_terminal=False),
    )
    assert "FAKE" in buffer.getvalue().upper()


# --------------------------------------------------------------------------- #
# 15. Path overrides. §2.1.
# --------------------------------------------------------------------------- #


def test_an_overridden_goldenset_path_is_used_and_disclosed(tmp_path: Path) -> None:
    """§2.1: "a report that quietly read a different file than the one recorded is
    worse than one that failed"."""
    scenario = _scenario(tmp_path / "override")
    moved = tmp_path / "elsewhere" / "goldenset.jsonl"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_bytes(scenario.goldenset.read_bytes())
    scenario.goldenset.unlink()

    model = _from_evidence(scenario, goldenset=moved)
    assert _get(_get(model, "goldenset"), "available") is True
    row = next(iter(_get(model, "flips")))
    assert _get(row, "input") == "INPUT-TEXT for item-03"
    assert str(moved) in _html(model), "the override must be printed in the provenance block"


# --------------------------------------------------------------------------- #
# 16. The series. Plan C3: ``ReportModel`` gains ``series: tuple[RunPoint, ...]``,
#     populated by ``from_evidence`` in one pass, while every headline field goes
#     on meaning the *last* comparison and the *last* verdict.
#
#     The failure this section exists to catch is silent. Every test above this
#     line uses a log holding one comparison, and on such a log "the first
#     comparison" and "the last comparison" are the same record -- so a
#     ``from_evidence`` that quietly started reducing to the first would keep the
#     whole suite green. Every fixture below therefore holds more than one run,
#     and every earlier run is poisoned in every field a reader would notice:
#     different models, different adapters, different hashes, different config
#     path, different judge, different flips, different draw count, and artifact
#     paths that are not on disk, so a report built from the wrong record does
#     not merely differ -- it degrades, loudly.
# --------------------------------------------------------------------------- #


EARLIER_BASELINE_MODEL = "model-a-20250101"
EARLIER_CANDIDATE_MODEL = "model-b-20250101"

#: Timestamps for the *envelopes* of an older run. Nothing in this section
#: asserts on ordering by time -- see
#: :func:`test_the_series_is_in_log_order_and_is_not_sorted_by_time` -- so these
#: exist only to be distinct from ``TS_COMPARISON``/``TS_VERDICT``.
EARLIER_TS_COMPARISON = "2026-08-01T01:00:00.000000+00:00"
EARLIER_TS_VERDICT = "2026-08-01T01:00:01.000000+00:00"
EARLIER_CREATED = "2026-08-01T00:59:00.000000+00:00"


def _tag_hash(tag: str, kind: str) -> str:
    """A distinct 64-character digest per (run, kind), so a leak names its source.

    An earlier run's hashes must not be mistakable for the headline's, and they
    must still look like hashes: a report that printed ``EARLIER`` where a
    ``goldenset_hash`` belongs would fail for the wrong reason.
    """
    return hashlib.sha256(f"{tag}:{kind}".encode()).hexdigest()


def _earlier_comparison(
    scenario: Scenario,
    *,
    tag: str,
    created: str = EARLIER_CREATED,
    baseline_adapter: str = "AnthropicAdapter",
    candidate_adapter: str = "OpenAICompatAdapter",
    pass_rate: float = 0.9191,
) -> dict[str, Any]:
    """A ``migkit.comparison`` payload for a run older than ``scenario``'s.

    Deep-copied through JSON -- the payload is JSON by construction -- and then
    contradicted field by field, so that any single value of it reaching a
    headline field is visible without having to know which field leaked.
    """
    payload: dict[str, Any] = json.loads(json.dumps(scenario.comparison))
    name = f"judge-{tag}"
    payload["created"] = created
    payload["goldenset_hash"] = _tag_hash(tag, "goldenset")
    payload["goldenset_path"] = str(scenario.root / f"goldenset-{tag}.jsonl")
    payload["judges_hash"] = _tag_hash(tag, "judges")
    payload["config_hash"] = _tag_hash(tag, "config")
    payload["config_path"] = str(scenario.root / f"migkit-{tag}.toml")
    payload["n_per_item"] = 3
    payload["warnings"] = [f"EARLIER-WARNING-{tag}"]

    for side, model_id, adapter in (
        ("baseline", f"{EARLIER_BASELINE_MODEL}-{tag}", baseline_adapter),
        ("candidate", f"{EARLIER_CANDIDATE_MODEL}-{tag}", candidate_adapter),
    ):
        payload[side]["model_id"] = model_id
        payload[side]["adapter"] = adapter
        payload[side]["adapters"] = [adapter] if adapter else []
        payload[side]["artifact"] = str(scenario.root / f"{side}-{tag}.jsonl")
        payload[side]["judged_artifact"] = str(scenario.root / f"{side}-{tag}.judged.jsonl")

    judge = payload["judges"][0]
    judge["name"] = name
    judge["rubric_hash"] = _tag_hash(tag, "rubric")
    judge["baseline"]["label"] = f"{name}:baseline"
    judge["candidate"]["label"] = f"{name}:candidate"
    judge["candidate"]["pass_rate"] = pass_rate
    payload["flips"] = _grouped(((f"earlier-{tag}", 5, 5, 0, 5),))
    payload["gains"] = _grouped(())
    payload["item_counts"]["per_judge"] = {name: dict(judge["item_counts"])}
    return payload


def _earlier_verdict(tag: str, verdict: str) -> dict[str, Any]:
    """The ``migkit.verdict`` payload that closes an earlier run."""
    return {
        "verdict": verdict,
        "exit_code": Verdict.exit_code(verdict),
        "reason": f"EARLIER-REASON-{tag}: an older night, and not this report's finding.",
        "decided_by": "rule 9",
        "rule": 9,
        "thresholds": dict(THRESHOLDS),
        "judges": [{"name": f"judge-{tag}", "regressed": False}],
        "baseline_model": f"{EARLIER_BASELINE_MODEL}-{tag}",
        "candidate_model": f"{EARLIER_CANDIDATE_MODEL}-{tag}",
    }


def _earlier_run(
    scenario: Scenario,
    *,
    tag: str,
    verdict: str | None = Verdict.GO,
    ts: str = EARLIER_TS_COMPARISON,
    ts_verdict: str = EARLIER_TS_VERDICT,
    **comparison: Any,
) -> list[dict[str, Any]]:
    """The evidence records one older run wrote: a comparison, and maybe a verdict.

    ``verdict=None`` is the run that died between the two records. It is not an
    error anywhere -- ``series.read_series`` documents it as a point whose
    ``verdict`` is ``None`` -- and it is the fixture that separates a first-in-
    first-out pairing from the two obvious wrong ones.
    """
    records = [_record(EVENT_COMPARISON, _earlier_comparison(scenario, tag=tag, **comparison), ts)]
    if verdict is not None:
        records.append(_record(EVENT_VERDICT, _earlier_verdict(tag, verdict), ts_verdict))
    return records


def _log_with_history(
    scenario: Scenario, name: str, *earlier: Sequence[Mapping[str, Any]]
) -> Path:
    """``scenario``'s own log, with older runs appended *before* it.

    Written beside the scenario's ``evidence.jsonl`` and reading the same
    artifacts, so the only difference between a model built from this log and one
    built from ``scenario.evidence`` is the history in front of it.
    """
    records: list[Mapping[str, Any]] = [
        _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING),
        _record(EVENT_JUDGING_COMPLETED, {"model_id": CANDIDATE_MODEL}, TS_JUDGING),
    ]
    for group in earlier:
        records.extend(group)
    records.append(_record(EVENT_COMPARISON, scenario.comparison, TS_COMPARISON))
    if scenario.verdict is not None:
        records.append(_record(EVENT_VERDICT, scenario.verdict, TS_VERDICT))
    return _write_evidence(scenario.root / name, records)


def _model_from(path: Path, **kwargs: Any) -> Any:
    """``ReportModel.from_evidence`` on a log this section wrote by hand."""
    kwargs.setdefault("now", NOW_A)
    return _get(_get(_module(), "ReportModel"), "from_evidence")(path, **kwargs)


def _series(model: Any) -> tuple[Any, ...]:
    series = _get(model, "series")
    assert isinstance(series, tuple), (
        f"C3 declares series as tuple[RunPoint, ...]; got {type(series).__name__}"
    )
    return series


def _fingerprint(model: Any, evidence: Path) -> str:
    """Every field of the model except ``series``, as text, log identity removed.

    Two things legitimately differ between a one-run log and the same log with a
    night of history in front of it: the path it was read from and its sha256.
    Both are replaced by a placeholder rather than dropped, so a field that
    *stopped* carrying them would still show up as a difference.
    """
    assert dataclasses.is_dataclass(model), (
        f"ReportModel is {type(model).__name__}, not a dataclass; C3's contract "
        f"spells its new member as a dataclass field"
    )
    values = {
        field.name: getattr(model, field.name)
        for field in dataclasses.fields(model)
        if field.name != "series"
    }
    text = json.dumps(values, sort_keys=True, default=str)
    text = text.replace(json.dumps(str(evidence))[1:-1], "<EVIDENCE-PATH>")
    return text.replace(_hash_bytes(evidence.read_bytes()), "<EVIDENCE-HASH>")


def _headline_scrubbed(html: str, evidence: Path) -> str:
    """A rendered document with the log's path and digest replaced, nothing else."""
    scrubbed = html.replace(str(evidence), "<EVIDENCE-PATH>")
    return scrubbed.replace(_hash_bytes(evidence.read_bytes()), "<EVIDENCE-HASH>")


# -- the named first failure ------------------------------------------------- #


def test_a_log_holding_two_comparisons_still_reports_on_the_last_one(
    tmp_path: Path,
) -> None:
    """C3's named first failure: two comparisons, two different verdicts.

    "The headline run's every existing field is unchanged -- the existing
    reduction keeps the last comparison and the last verdict." A GO on the first
    of the two is the value a reduction that grabbed the first record would
    print, and printing GO for a NO-GO migration is the worst single thing this
    document can do.
    """
    scenario = _scenario(tmp_path / "two", verdict=Verdict.NO_GO)
    log = _log_with_history(
        scenario, "evidence-two.jsonl", _earlier_run(scenario, tag="one", verdict=Verdict.GO)
    )
    model = _model_from(log)

    assert _get(model, "verdict") == Verdict.NO_GO, (
        "the headline verdict came from the first comparison in the log"
    )
    series = _series(model)
    assert len(series) == 2, f"two comparisons, {len(series)} point(s)"
    assert series[0].verdict == Verdict.GO
    assert series[-1].verdict == Verdict.NO_GO
    assert series[-1].verdict == _get(model, "verdict"), (
        "the last point and the banner disagree about the same run"
    )


# -- Edges row 1: the single-comparison log, which is every log today --------- #


def test_a_single_comparison_log_yields_exactly_one_point(tmp_path: Path) -> None:
    """Edges row 1. Every log this tool has ever written holds one comparison."""
    from model_migration_kit.series import RunPoint

    scenario = _scenario(tmp_path / "single")
    series = _series(_from_evidence(scenario))

    assert len(series) == 1, f"one comparison, {len(series)} point(s)"
    assert isinstance(series[0], RunPoint), (
        f"C3 says tuple[RunPoint, ...]; the element is a {type(series[0]).__name__}"
    )


def test_the_series_field_is_declared_with_an_empty_default(tmp_path: Path) -> None:
    """C3 spells it ``series: tuple[RunPoint, ...] = ()``.

    The default is load-bearing rather than cosmetic: every other constructor of
    a ``ReportModel`` in this codebase and in anyone else's predates the field,
    and a required argument would break each one.
    """
    fields = {field.name: field for field in dataclasses.fields(_get(_module(), "ReportModel"))}
    assert "series" in fields, (
        f"ReportModel has no series field; it declares {sorted(fields)}"
    )
    field = fields["series"]
    factory = field.default_factory
    default = field.default if factory is dataclasses.MISSING else factory()
    assert default == (), f"series defaults to {default!r}, not ()"
    assert "RunPoint" in str(field.type), (
        f"series is annotated {field.type!r}; C3 spells the element type, and the "
        f"annotation is the only place it is stated -- C6 renders this tuple and has "
        f"nothing else to read it from"
    )


def test_a_single_comparison_log_still_reports_every_headline_it_did_before(
    tmp_path: Path,
) -> None:
    """Edges row 1's second half: "every other field byte-identical to before".

    Pinned as values rather than as a diff, because the "before" this row names
    is a tree that no longer exists by the time anyone runs this. Each of these
    is asserted elsewhere in this file too; gathering them here is deliberate, so
    that a C3 that moved the reduction shows up as one failure that names the
    chunk rather than as fourteen scattered ones.
    """
    scenario = _scenario(tmp_path / "unchanged")
    model = _from_evidence(scenario)

    assert _get(model, "verdict") == Verdict.NO_GO
    assert _get(model, "reason") == scenario.verdict["reason"]
    assert _get(model, "decided_by") == "rule 1"
    assert int(_get(model, "exit_code")) == 1
    assert _get(model, "is_demo") is False
    assert int(_get(model, "n_per_item")) == N_PER_ITEM
    assert _ids(_get(model, "flips")) == ["item-03", "item-07"]
    assert _ids(_get(model, "gains")) == ["item-11"]
    assert _get(_get(model, "completeness"), "complete") is True

    hashes = _get(model, "hashes")
    assert _get(hashes, "goldenset", "goldenset_hash") == scenario.goldenset_hash
    assert _get(hashes, "judges", "judges_hash") == JUDGES_HASH
    assert _get(hashes, "config", "config_hash") == CONFIG_HASH

    stat = _rate_stat(_judge_row(model), "candidate")
    assert _get(stat, "rate") == 0.5353

    assert len(_series(model)) == 1, (
        "the assertions above pass on a tree with no series at all; this one is "
        "what makes them assertions about C3"
    )


# -- Edges row 4: the last point and the headline describe one run ------------ #


@pytest.mark.parametrize("history", [0, 1, 3])
def test_the_last_point_describes_the_same_run_as_the_headline(
    tmp_path: Path, history: int
) -> None:
    """Edges row 4, at one, two and four runs.

    A timeline whose right-hand end disagrees with the banner printed above it is
    worse than no timeline: the reader has two numbers for one night and no way
    to tell which one the gate used.
    """
    scenario = _scenario(tmp_path / f"agree-{history}")
    if history:
        log = _log_with_history(
            scenario,
            f"evidence-{history}.jsonl",
            *(_earlier_run(scenario, tag=f"n{index}") for index in range(history)),
        )
    else:
        log = scenario.evidence
    model = _model_from(log)
    series = _series(model)
    assert len(series) == history + 1
    point = series[-1]

    assert point.verdict == _get(model, "verdict")
    assert point.reason == _get(model, "reason")
    assert point.baseline_model == BASELINE_MODEL == _get(
        _get(model, "baseline"), "model_id", "model"
    )
    assert point.candidate_model == CANDIDATE_MODEL == _get(
        _get(model, "candidate"), "model_id", "model"
    )
    assert point.adapter_baseline == "AnthropicAdapter"
    assert point.adapter_candidate == "OpenAICompatAdapter"
    assert point.n_per_item == N_PER_ITEM

    hashes = _get(model, "hashes")
    assert point.goldenset_hash == _get(hashes, "goldenset", "goldenset_hash")
    assert point.judges_hash == _get(hashes, "judges", "judges_hash")
    assert point.config_hash == _get(hashes, "config", "config_hash")

    stat = _rate_stat(_judge_row(model), "candidate")
    assert point.pass_rate == _get(stat, "rate")
    assert point.judge_name == J


# -- the headline is not moved by anything in front of it -------------------- #


def test_prepending_an_earlier_run_changes_no_field_but_the_series(
    tmp_path: Path,
) -> None:
    """C3's "Must not": "Change any existing field's value on any existing fixture".

    Stated relationally, because that is the only form of it a test can hold: the
    same run, read from a log with a night of history in front of it, must
    produce the same report. Every field is compared, including the ones no test
    above this line reads, so a value that leaks out of the earlier record into
    any corner of the model is a failure here even if nobody has thought to
    assert on that corner yet.
    """
    scenario = _scenario(tmp_path / "prepend")
    alone = _model_from(scenario.evidence)
    log = _log_with_history(
        scenario, "evidence-history.jsonl", _earlier_run(scenario, tag="one")
    )
    with_history = _model_from(log)

    assert _fingerprint(with_history, log) == _fingerprint(alone, scenario.evidence)
    assert len(_series(with_history)) == 2, (
        "the comparison above is satisfied by a tree that reads no series at all"
    )


def test_the_judge_table_the_flips_and_the_provenance_come_from_the_last_run(
    tmp_path: Path,
) -> None:
    """C3 names the four places by hand: banner, judge table, flips, provenance.

    The fingerprint test above catches all of this in one comparison and says
    nothing about *what* moved. This one names the four, so the failure a
    reviewer reads points at a section of the document.
    """
    scenario = _scenario(tmp_path / "sections")
    log = _log_with_history(
        scenario, "evidence-sections.jsonl", _earlier_run(scenario, tag="old")
    )
    model = _model_from(log)

    assert _ids(_get(model, "flips")) == ["item-03", "item-07"]
    assert _ids(_get(model, "gains")) == ["item-11"]
    assert _judge_row(model, J) is not None
    hashes = _get(model, "hashes")
    for kind, expected in (
        ("goldenset", scenario.goldenset_hash),
        ("judges", JUDGES_HASH),
        ("config", CONFIG_HASH),
    ):
        assert _get(hashes, kind, f"{kind}_hash") == expected
        assert _get(hashes, kind, f"{kind}_hash") != _tag_hash("old", kind)

    html = _html(model)
    assert "earlier-old" not in html, "an earlier run's flip is in the document"
    assert f"{EARLIER_CANDIDATE_MODEL}-old" not in html, (
        "an earlier run's candidate model is in the document"
    )
    assert _tag_hash("old", "config") not in html

    assert len(_series(model)) == 2, (
        "every assertion above passes on a tree that reads no series at all"
    )


def _without_timeline(html: str) -> str:
    """The document with its run-history section and nav entry cut out.

    Everything C14a added lives between ``<h2 id="timeline">`` and the next
    heading, plus one ``<li>`` in the nav. Cutting exactly that leaves the rest of
    the document to be compared byte for byte.
    """
    start = html.find('<h2 id="timeline">')
    if start != -1:
        end = html.find('<h2 id="judges">', start)
        assert end != -1, "the timeline section is not followed by the judges heading"
        html = html[:start] + html[end:]
    kept = [line for line in html.splitlines() if 'href="#timeline"' not in line]
    return chr(10).join(kept)


def test_a_series_of_runs_changes_the_run_history_and_nothing_else(tmp_path: Path) -> None:
    """C3 said "render nothing"; C14a is the chunk where the timeline arrives.

    So the original byte-for-byte assertion is kept and *narrowed* rather than
    deleted: outside the run-history section the two documents must still be
    identical, modulo the log's own path and digest. What C3's test was really
    protecting is the promise in ``ReportModel.series``' own docstring -- that the
    timeline can gain, lose or re-derive a field "without the banner, the judge
    table, the flips or the provenance block moving with it" -- and that promise
    is not weakened by rendering the series. It is only now testable.

    The second assertion is what stops this from becoming a test that passes by
    cutting out everything that differs: the section that *was* excised must
    genuinely differ between the two documents. Without it, a bug that rendered
    the timeline identically for one run and two would sail through.
    """
    scenario = _scenario(tmp_path / "renders")
    alone = _model_from(scenario.evidence)
    log = _log_with_history(
        scenario, "evidence-renders.jsonl", _earlier_run(scenario, tag="one")
    )
    with_history = _model_from(log)

    two_runs = _headline_scrubbed(_html(with_history), log)
    one_run = _headline_scrubbed(_html(alone), scenario.evidence)

    assert _without_timeline(two_runs) == _without_timeline(one_run), (
        "a second comparison in the log changed something outside the run-history "
        "section; the banner, the judge table, the flips and the provenance block "
        "are read from the records and must not move with the series"
    )
    assert two_runs != one_run, (
        "the two documents are identical, so the run history is not being rendered "
        "at all and the comparison above is vacuous"
    )
    assert len(_series(with_history)) == 2, (
        "the documents match because neither model has a series"
    )


# -- pairing, and order ------------------------------------------------------ #


def test_a_run_that_died_before_its_verdict_is_a_point_with_no_verdict(
    tmp_path: Path,
) -> None:
    """The pairing hazard ``series.read_series`` names, reached through the report.

    A log reading comparison, comparison, verdict has exactly one verdict in it,
    and it belongs to the *second* comparison. The two obvious implementations
    both get this wrong -- keeping one "last comparison" pairs the verdict with
    the dead run, and zipping two lists pairs it with whichever comes first --
    and both failures draw a real verdict on a night that never produced one.
    """
    scenario = _scenario(tmp_path / "died", verdict=Verdict.NO_GO)
    log = _log_with_history(
        scenario, "evidence-died.jsonl", _earlier_run(scenario, tag="dead", verdict=None)
    )
    model = _model_from(log)
    series = _series(model)

    assert len(series) == 2, f"two comparisons, {len(series)} point(s)"
    assert series[0].verdict is None, (
        f"the dead run was given a verdict of {series[0].verdict!r}, which belongs "
        f"to the run after it"
    )
    assert series[0].reason is None
    assert series[-1].verdict == Verdict.NO_GO
    assert _get(model, "verdict") == Verdict.NO_GO
    assert _get(model, "reason") == scenario.verdict["reason"]


def test_the_series_is_in_log_order_and_is_not_sorted_by_time(tmp_path: Path) -> None:
    """File order is the series order; ``series.read_series`` says so in full.

    "the timestamps are written by whichever machine ran each night, a sorted
    series would silently reorder a log whose clock stepped backwards over a
    daylight-saving boundary". So the fixture is deliberately anti-chronological:
    a sort by ``created`` would put the headline run in the middle.
    """
    scenario = _scenario(tmp_path / "order")
    log = _log_with_history(
        scenario,
        "evidence-order.jsonl",
        _earlier_run(scenario, tag="a", created="2027-01-01T00:00:00.000000+00:00"),
        _earlier_run(scenario, tag="b", created="2024-01-01T00:00:00.000000+00:00"),
        _earlier_run(scenario, tag="c", created="2026-12-31T00:00:00.000000+00:00"),
    )
    series = _series(_model_from(log))

    assert [point.candidate_model for point in series] == [
        f"{EARLIER_CANDIDATE_MODEL}-a",
        f"{EARLIER_CANDIDATE_MODEL}-b",
        f"{EARLIER_CANDIDATE_MODEL}-c",
        CANDIDATE_MODEL,
    ]
    assert [point.created for point in series] == [
        "2027-01-01T00:00:00.000000+00:00",
        "2024-01-01T00:00:00.000000+00:00",
        "2026-12-31T00:00:00.000000+00:00",
        TS_COMPARISON,
    ]


def test_every_earlier_run_keeps_its_own_numbers(tmp_path: Path) -> None:
    """Two points from one log must not share one run's values.

    A loop that built each point from an accumulating dict, or that appended the
    same object twice, passes every length assertion above and produces a
    timeline of one night repeated.
    """
    scenario = _scenario(tmp_path / "distinct")
    log = _log_with_history(
        scenario,
        "evidence-distinct.jsonl",
        _earlier_run(scenario, tag="a", pass_rate=0.1111),
        _earlier_run(scenario, tag="b", pass_rate=0.2222),
    )
    series = _series(_model_from(log))

    assert [point.pass_rate for point in series] == [0.1111, 0.2222, 0.5353]
    assert [point.n_per_item for point in series] == [3, 3, N_PER_ITEM]
    assert [point.judge_name for point in series] == ["judge-a", "judge-b", J]
    assert [point.goldenset_hash for point in series] == [
        _tag_hash("a", "goldenset"),
        _tag_hash("b", "goldenset"),
        scenario.goldenset_hash,
    ]
    assert len(set(series)) == 3, "two of the three points are the same record"


# -- Edges row 3: demo-ness reaches back through the series ------------------- #


@pytest.mark.parametrize(
    ("baseline_adapter", "candidate_adapter"),
    [
        ("AnthropicAdapter", "FakeScriptedAdapter"),
        ("FakeAdapter", "OpenAICompatAdapter"),
    ],
    ids=["scripted-candidate", "scripted-baseline"],
)
def test_a_fake_adapter_on_an_earlier_run_still_bands_the_report(
    tmp_path: Path, baseline_adapter: str, candidate_adapter: str
) -> None:
    """Edges row 3, and §4.3: "or any point in ``series`` names a ``Fake*`` adapter".

    §5.3's rationale is that "you cannot obtain a clean-looking report from
    scripted models by avoiding ``migkit demo``". Once a log carries history, the
    way to obtain one is to run the scripted nights first and a real night last,
    which is exactly the shape of a demo somebody pastes into a deck.

    **One side at a time, which is the shape a demo actually has.** Scripting both
    sides of the earlier run made this test blind to half of what it checks: the
    disjunct reads ``adapter_baseline`` *or* ``adapter_candidate``, and a reader
    that dropped either term still banded a log where both were ``Fake*``. The
    realistic case is a real baseline against a scripted candidate -- the demo's
    own shape, and the one where dropping a term costs the band entirely.
    """
    slug = f"{baseline_adapter}-{candidate_adapter}"
    scenario = _scenario(tmp_path / f"demo-history-{slug}")
    log = _log_with_history(
        scenario,
        "evidence-demo.jsonl",
        _earlier_run(
            scenario,
            tag="fake",
            baseline_adapter=baseline_adapter,
            candidate_adapter=candidate_adapter,
        ),
    )
    model = _model_from(log)

    assert _get(model, "is_demo") is True
    document = _parse(_html(model))
    for marker in FAKE_BAND_MARKERS:
        assert marker.lower() in document.text.lower(), (
            f"band marker {marker!r} is missing on a log whose history is scripted"
        )
    assert "FAKE" in document.title.upper()


@pytest.mark.parametrize(
    ("baseline_adapter", "candidate_adapter"),
    [
        ("AnthropicAdapter", "OpenAICompatAdapter"),
        ("", ""),
        ("faked-out-adapter", "unfakeable"),
    ],
)
def test_a_series_of_real_runs_does_not_band_the_report(
    tmp_path: Path, baseline_adapter: str, candidate_adapter: str
) -> None:
    """The other half of §4.3's disjunct, which is the half that can rot silently.

    C3's reviewer note asks for exactly this: "check the new disjunct cannot be
    made false by an input, including a series whose adapter strings are empty".
    A disjunct that is always true bands every report, and a band that is always
    on is a band nobody reads -- which costs the demo warning its whole value.
    The two adapters that merely *contain* "fake" are here because the rule §5.3
    states is a prefix.
    """
    slug = f"{baseline_adapter or 'blank'}-{candidate_adapter or 'blank'}"
    scenario = _scenario(tmp_path / f"real-{slug}")
    log = _log_with_history(
        scenario,
        "evidence-real.jsonl",
        _earlier_run(
            scenario,
            tag="old",
            baseline_adapter=baseline_adapter,
            candidate_adapter=candidate_adapter,
        ),
    )
    model = _model_from(log)

    assert _get(model, "is_demo") is False, (
        f"adapters {baseline_adapter!r}/{candidate_adapter!r} on an earlier run "
        f"banded a report whose models are both real"
    )
    document = _parse(_html(model))
    for marker in FAKE_BAND_MARKERS:
        assert marker.lower() not in document.text.lower()
    assert len(_series(model)) == 2, (
        "the assertions above pass on a tree that reads no series at all"
    )


def test_a_fake_headline_is_still_banded_when_the_history_is_real(
    tmp_path: Path,
) -> None:
    """§4.3 widened the rule; it must not have replaced it.

    An implementation that moved demo-ness onto the series alone would pass every
    test above and drop the band from the case the rule was written for.
    """
    scenario = _scenario(
        tmp_path / "fake-headline",
        baseline_adapter="FakeAdapter",
        candidate_adapter="FakeAdapter",
    )
    log = _log_with_history(
        scenario, "evidence-fakehead.jsonl", _earlier_run(scenario, tag="real")
    )
    model = _model_from(log)

    assert _get(model, "is_demo") is True
    assert "FAKE" in _parse(_html(model)).title.upper()
    assert len(_series(model)) == 2, (
        "the assertions above pass on a tree that reads no series at all"
    )


# -- Edges row 2: the refusal, unchanged ------------------------------------- #


def test_a_log_with_no_comparison_is_refused_in_the_same_words(tmp_path: Path) -> None:
    """Edges row 2: "``ArtifactError``, unchanged wording".

    Two logs with nothing to report on -- one empty of everything, one carrying a
    verdict that opened no point, which is what the tail of a log rotated
    mid-run looks like. Both must raise, both must raise the same type, and both
    must raise the same sentence: a refusal whose wording forked on which
    records happened to be present is a refusal that has started describing the
    implementation instead of the problem.

    The exact sentence *is* pinned, as a literal. "Unchanged" needs a copy of
    the tree that came before C3 to mean anything, and this branch is that tree:
    the sentence below was lifted from ``report.py`` at ``main``, where it has
    stood since before this chunk was written, and it is quoted here so that a
    reduction rebuilt around pairing cannot reword the refusal on its way past.
    The event name is spelled through ``EVENT_COMPARISON`` rather than typed
    out, because the message interpolates it and a renamed event should move
    both together.
    """
    from model_migration_kit.errors import ArtifactError

    root = tmp_path / "norecord"
    root.mkdir()
    bare = _write_evidence(
        root / "bare.jsonl",
        [_record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING)],
    )
    orphaned = _write_evidence(
        root / "orphaned.jsonl",
        [
            _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING),
            _record(EVENT_JUDGING_COMPLETED, {"model_id": CANDIDATE_MODEL}, TS_JUDGING),
            _record(EVENT_VERDICT, _earlier_verdict("orphan", Verdict.GO), TS_VERDICT),
        ],
    )

    messages = []
    for log in (bare, orphaned):
        with pytest.raises(ArtifactError) as caught:
            _model_from(log)
        messages.append(str(caught.value).replace(str(log), "<LOG>"))

    assert messages[0] == messages[1], (
        f"the refusal has two wordings: {messages[0]!r} and {messages[1]!r}"
    )
    assert messages[0] == (
        f"<LOG> contains no {EVENT_COMPARISON} record, so there is nothing to "
        f"report on. A run that died before comparing produced evidence of an "
        f"attempt, not of a comparison."
    ), f"the refusal was reworded; Edges row 2 says it is unchanged: {messages[0]!r}"
    for word in ("series", "run point", "runpoint", "timeline"):
        assert word not in messages[0].lower(), (
            f"the refusal now names {word!r}; §2.6 row 2 is about a report having "
            f"nothing to report on, which is unchanged by C3"
        )


def test_a_log_whose_only_comparison_has_no_verdict_is_still_not_refused(
    tmp_path: Path,
) -> None:
    """The line either side of Edges row 2, which C3 must not move.

    §2.6 row 3 renders; row 2 refuses. The difference is a comparison record, not
    a verdict record, and a loop rewritten around pairing is exactly the place
    that difference gets blurred.
    """
    scenario = _scenario(tmp_path / "noverdict-series", with_verdict=False)
    model = _from_evidence(scenario)

    assert _get(model, "verdict") is None
    assert int(_get(model, "exit_code")) == 3
    series = _series(model)
    assert len(series) == 1
    assert series[0].verdict is None
    assert series[0].candidate_model == CANDIDATE_MODEL


# -- the "Must not" that is about bytes, not values -------------------------- #


def test_the_log_is_read_once_for_both_the_headline_and_the_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3: "do **not** make two passes over the log", stated as a contract term.

    It is observable, so it is observed. ``evidence.stream_records`` opens the
    log in text mode and ``contracts.hash_file`` opens it in binary, so counting
    text-mode opens of this one path counts passes over the records and ignores
    the hashing that every report has always done. Both ``builtins.open`` and
    ``io.open`` are replaced because ``Path.open`` reaches the latter directly.

    A second pass is cheap on the 12-item fixture and is not cheap on the log
    this measurement is about: the evidence log is the largest artifact the
    pipeline writes, and ``stream_records`` exists because an 86 MB one already
    cost 502 MB once.
    """
    import builtins
    import os

    scenario = _scenario(tmp_path / "onepass")
    log = _log_with_history(
        scenario, "evidence-onepass.jsonl", _earlier_run(scenario, tag="one")
    )
    target = log.resolve()
    opened: list[str] = []
    real_open = builtins.open

    def counting(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        try:
            same = Path(os.fspath(file)).resolve() == target
        except (TypeError, ValueError, OSError):
            same = False
        if same and "b" not in mode:
            opened.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting)
    monkeypatch.setattr(io, "open", counting)
    try:
        model = _model_from(log)
    finally:
        monkeypatch.undo()

    assert len(_series(model)) == 2, "no series was built, so no pass count is meaningful"
    assert len(opened) == 1, (
        f"the evidence log was read {len(opened)} times in text mode; C3 requires "
        f"one pass that accumulates the points and keeps the last two records"
    )
# 17. The timeline. Plan C13: `timeline_svg`, whose x-axis is time.
#
# Written from the C13 contract (plan lines 1220-1287) and its Edges table,
# never from the module: when these were written `report.timeline_svg` did not
# exist in this worktree at all. Three of the test names below are the
# contract's own words and are not this file's to choose.
#
# The return type is an orchestrator ruling, not an invention here. The contract
# gives the signature as `-> str` while its prose requires a count of runs with
# no floor, and its Edges table a second count for points with no `pass_rate`.
# The resolution is `Timeline(svg, runs_without_floor, runs_without_rate)`, and
# both fields are counts of *points*, never of segments.
#
# Two failure modes are what most of this section is aimed at, because they are
# the ones that ship:
#
#   * The floor drawn as one `<polyline>` through the floor values. That renders
#     a diagonal ramp between two different floors, and a floor that ramps is a
#     floor that never existed. So the step test asserts every floor segment is
#     axis-aligned and that the rule touches exactly two heights -- not merely
#     that both floors appear somewhere, which a ramp also satisfies.
#   * The zero-span division by zero. It is not a curiosity: a seed generator
#     that patches `utc_now` to a constant produces runs sharing a timestamp to
#     the microsecond, which is how the showcase series is built.
#
# An unparseable `created` is deliberately *not* tested. The contract does not
# cover it, and a test written against a guess pins the guess rather than the
# contract.
#
# The SVG is parsed with stdlib :mod:`xml.etree.ElementTree` rather than matched
# with a pattern, because an assertion on a parsed attribute survives a
# whitespace or attribute-order change that a regular expression does not.
# --------------------------------------------------------------------------- #


#: The contract's own tolerance, in the words of its first named test: "within a
#: pixel".
ONE_PIXEL = 1.0

#: Two coordinates this close are the same coordinate. Nothing asserted here is
#: a near miss -- a ramp between floors 0.10 of rate apart is tens of pixels.
FLAT = 0.5

#: Every marker carries all four, per the contract. Requiring all four is what
#: separates a marker from the whisker or the floor segment beside it.
MARKER_ATTRS = ("data-created", "data-rate", "data-verdict", "data-floor")

#: The contract names these four verdict classes and no others.
VERDICT_CLASSES = frozenset({"go", "nogo", "review", "none"})

_NUMBER_RE = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")
_PATH_RE = re.compile(r"([A-Za-z])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_TRANSLATE_RE = re.compile(r"translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)")


def _timeline_day(offset: int, *, hour: int = 12) -> str:
    """A `created` `offset` days after 2026-07-01, shaped as `series` records it.

    July has 31 days, so every offset used here lands inside one month and the
    arithmetic a reader must do to check a fixture is subtraction.
    """
    return f"2026-07-{1 + offset:02d}T{hour:02d}:00:00.000000+00:00"


def _point(
    created: str,
    *,
    pass_rate: float | None = 0.82,
    floor: float | None = 0.80,
    verdict: str | None = "go",
    interval: tuple[float, float] | None = None,
) -> Any:
    """One :class:`series.RunPoint`, varying only what a timeline reads.

    Imported inside the function so this section adds no import to the top of a
    file two chunks are appending to at once.
    """
    from model_migration_kit.series import RunPoint

    return RunPoint(
        created=created,
        created_source="payload",
        verdict=verdict,
        reason=None if verdict is None else f"reason for {verdict}",
        baseline_model=BASELINE_MODEL,
        candidate_model=CANDIDATE_MODEL,
        adapter_baseline="AnthropicAdapter",
        adapter_candidate="AnthropicAdapter",
        goldenset_hash="a" * 64,
        judges_hash=JUDGES_HASH,
        config_hash=CONFIG_HASH,
        config_path="migkit.toml",
        n_per_item=N_PER_ITEM,
        items=len(ITEM_IDS),
        judged_baseline=ODD_N,
        judged_candidate=ODD_N,
        judge_failures_baseline=1,
        judge_failures_candidate=2,
        pass_rate=pass_rate,
        interval=interval,
        lower_bound=None if pass_rate is None else ODD_LOWER_BOUND,
        floor=floor,
        floor_source="unrecorded" if floor is None else "gate",
        confidence=THRESHOLDS["confidence"],
        alpha=THRESHOLDS["alpha"],
        judge_name=J,
        judge_model_id=JUDGE_MODEL,
        rubric_hashes=(RUBRIC_HASH,),
        p_value=ODD_P_VALUE,
        latency_median_candidate=0.2345,
        runs_needed=None,
        n_required=None,
        warnings=(),
    )


def _timeline(points: Sequence[Any], **kwargs: Any) -> Any:
    return _get(_module(), "timeline_svg")(tuple(points), **kwargs)


def _timeline_parts(points: Sequence[Any], **kwargs: Any) -> tuple[str, int, int]:
    """The ruled return, read by name so a bare `str` fails loudly and early."""
    result = _timeline(points, **kwargs)
    return (
        _get(result, "svg"),
        _get(result, "runs_without_floor"),
        _get(result, "runs_without_rate"),
    )


def _svg_tag(element: Any) -> str:
    """The local name, with any `{http://www.w3.org/2000/svg}` prefix removed."""
    return str(element.tag).rsplit("}", 1)[-1]


def _svg_root(svg: Any) -> Any:
    import xml.etree.ElementTree as ElementTree

    assert isinstance(svg, str), f"the svg field is a {type(svg).__name__}, not a string"
    assert svg.strip(), "timeline_svg returned an empty document; the contract forbids it"
    try:
        root = ElementTree.fromstring(svg)
    except ElementTree.ParseError as exc:
        raise AssertionError(
            f"timeline_svg returned XML that will not parse ({exc}): {svg[:300]!r}"
        ) from exc
    assert _svg_tag(root) == "svg", f"the root element is <{_svg_tag(root)}>, not <svg>"
    return root


def _svg_text(element: Any) -> str:
    return _squeeze("".join(element.itertext()))


def _numbers(text: str) -> list[float]:
    return [float(one) for one in _NUMBER_RE.findall(text)]


def _classes(element: Any) -> list[str]:
    return str(element.get("class") or "").split()


def _is_marker(element: Any) -> bool:
    return all(name in element.attrib for name in MARKER_ATTRS)


def _marker_x(element: Any) -> float:
    if "cx" in element.attrib:
        return float(element.attrib["cx"])
    if "x" in element.attrib:
        left = float(element.attrib["x"])
        if _svg_tag(element) == "rect":
            return left + float(element.get("width") or 0.0) / 2
        return left
    match = _TRANSLATE_RE.search(str(element.get("transform") or ""))
    if match:
        return float(match.group(1))
    raise AssertionError(
        "the Edges table requires a marker's horizontal position to be readable from "
        f"the document; <{_svg_tag(element)}> carries none of cx, x or a translate: "
        f"{sorted(element.attrib)}"
    )


def _marker_y(element: Any) -> float:
    if "cy" in element.attrib:
        return float(element.attrib["cy"])
    if "y" in element.attrib:
        top = float(element.attrib["y"])
        if _svg_tag(element) == "rect":
            return top + float(element.get("height") or 0.0) / 2
        return top
    match = _TRANSLATE_RE.search(str(element.get("transform") or ""))
    if match:
        return float(match.group(2))
    raise AssertionError(
        f"<{_svg_tag(element)}> carries none of cy, y or a translate: {sorted(element.attrib)}"
    )


def _markers(root: Any) -> list[Any]:
    """Every marker, left to right.

    Identified by the four data attributes the contract requires on each one,
    because the contract names no element for a marker.
    """
    found = [element for element in root.iter() if _is_marker(element)]
    return sorted(found, key=_marker_x)


def _path_segments(data: str) -> list[tuple[float, float, float, float]]:
    """The straight runs of a path. A curve command is itself a finding.

    M/L/H/V/Z in both cases is every command a step function needs. Anything
    else raises rather than being silently skipped: a floor rule containing a
    bezier is the diagonal ramp this section exists to catch, and skipping the
    command it was drawn with would let it through.
    """
    tokens = [letter or number for letter, number in _PATH_RE.findall(data)]
    segments: list[tuple[float, float, float, float]] = []
    x = y = 0.0
    start = (0.0, 0.0)
    command = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in {"Z", "z"}:
                segments.append((x, y, start[0], start[1]))
                x, y = start
            continue
        assert command, f"path data begins with a number: {data!r}"
        upper = command.upper()
        relative = command.islower()
        if upper in {"M", "L"}:
            first, second = float(tokens[index]), float(tokens[index + 1])
            index += 2
            new = (x + first, y + second) if relative else (first, second)
        elif upper == "H":
            first = float(tokens[index])
            index += 1
            new = (x + first if relative else first, y)
        elif upper == "V":
            first = float(tokens[index])
            index += 1
            new = (x, y + first if relative else first)
        else:
            raise AssertionError(
                f"the path uses command {command!r}. A step function is made of "
                f"horizontal and vertical runs only: {data!r}"
            )
        if upper == "M":
            start = new
            command = "l" if relative else "L"
        else:
            segments.append((x, y, new[0], new[1]))
        x, y = new
    return segments


def _element_segments(element: Any) -> list[tuple[float, float, float, float]]:
    """Every straight run this element draws, as (x1, y1, x2, y2)."""
    tag = _svg_tag(element)
    if tag == "line":
        return [
            (
                float(element.get("x1") or 0.0),
                float(element.get("y1") or 0.0),
                float(element.get("x2") or 0.0),
                float(element.get("y2") or 0.0),
            )
        ]
    if tag in {"polyline", "polygon"}:
        flat = _numbers(str(element.get("points") or ""))
        pairs = list(zip(flat[0::2], flat[1::2], strict=False))
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pairs, pairs[1:], strict=False)]
    if tag == "path":
        return _path_segments(str(element.get("d") or ""))
    if tag == "rect":
        left = float(element.get("x") or 0.0)
        top = float(element.get("y") or 0.0)
        wide = float(element.get("width") or 0.0)
        tall = float(element.get("height") or 0.0)
        if wide >= tall:  # a bar's centre line runs along its longer axis
            middle = top + tall / 2
            return [(left, middle, left + wide, middle)]
        middle = left + wide / 2
        return [(middle, top, middle, top + tall)]
    return []


def _geometry(root: Any) -> list[tuple[Any, tuple[float, float, float, float]]]:
    """Every drawn straight run, paired with the element that drew it.

    Markers are excluded: a marker is a position, not a segment, and a square one
    would otherwise contribute a spurious centre line.
    """
    drawn: list[tuple[Any, tuple[float, float, float, float]]] = []
    for element in root.iter():
        if _is_marker(element):
            continue
        drawn.extend((element, segment) for segment in _element_segments(element))
    return drawn


def _floor_drawn(root: Any) -> list[tuple[Any, tuple[float, float, float, float]]]:
    """The floor rule's segments, each paired with the element that drew it.

    The contract requires a step function and names no element for it, so the
    handle used here is the one thing a drawn-and-styled rule must carry: a class
    naming it. When nothing matches, every tag and class in the document is
    listed, so the failure says what is missing rather than merely that something
    is.
    """
    drawn = [
        (element, segment)
        for element, segment in _geometry(root)
        if any("floor" in one.lower() for one in _classes(element))
    ]
    if not drawn:
        present = sorted({f"<{_svg_tag(one)} class={one.get('class')!r}>" for one in root.iter()})
        raise AssertionError(
            "no element of the timeline is classed as the floor rule, so a step cannot be "
            "told from a single rule. The document holds: " + ", ".join(present)
        )
    return drawn


def _floor_segments(root: Any) -> list[tuple[float, float, float, float]]:
    return [segment for _, segment in _floor_drawn(root)]


def _length(segment: tuple[float, float, float, float]) -> float:
    """How long a segment is. Zero is a finding, not a segment.

    A zero-length segment satisfies both :func:`_horizontal` and :func:`_vertical`
    and draws nothing at all, so anything that classifies segments has to be able
    to say that one is degenerate rather than quietly counting it as both.
    Measured along the axes rather than as a hypotenuse, so this section adds no
    import to the top of a file two chunks are appending to at once; the two agree
    on the only question asked of them, which is whether the length is zero.
    """
    x1, y1, x2, y2 = segment
    return abs(x2 - x1) + abs(y2 - y1)


def _horizontal(
    segments: Sequence[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    return [one for one in segments if abs(one[1] - one[3]) <= FLAT]


def _vertical(
    segments: Sequence[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    return [one for one in segments if abs(one[0] - one[2]) <= FLAT]


def _levels(segments: Sequence[tuple[float, float, float, float]]) -> list[float]:
    """The distinct heights the segments touch, clustered at half a pixel."""
    found: list[float] = []
    for value in sorted(y for one in segments for y in (one[1], one[3])):
        if not found or abs(value - found[-1]) > FLAT:
            found.append(value)
    return found


def _instant(created: str) -> float:
    """`data-created` as seconds, read back out of the document itself."""
    from datetime import datetime

    return datetime.fromisoformat(created).timestamp()


def _by_created(root: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for marker in _markers(root):
        key = str(marker.get("data-created"))
        assert key not in found, f"two markers share data-created={key!r}"
        found[key] = marker
    return found


# --------------------------------------------------------------------------- #


def test_the_timeline_returns_an_svg_and_a_count_of_each_kind_of_missing_run() -> None:
    """The shape ruled over the contract's contradictory `-> str`.

    The prose requires that "the count of such runs is returned to the caller for
    a sentence beneath the chart"; the Edges table requires a second count for
    points with no `pass_rate`. A bare string can carry neither.
    """
    points = [
        _point(_timeline_day(0)),
        _point(_timeline_day(1), floor=None),
        _point(_timeline_day(2), pass_rate=None),
    ]
    svg, without_floor, without_rate = _timeline_parts(points)

    assert isinstance(svg, str)
    assert without_floor == 1, "one point of three has floor=None"
    assert without_rate == 1, "one point of three has pass_rate=None"
    assert isinstance(without_floor, int) and not isinstance(without_floor, bool)
    assert isinstance(without_rate, int) and not isinstance(without_rate, bool)


def test_a_timeline_of_no_points_is_a_single_text_and_not_an_empty_string() -> None:
    """Edges row 1, quoting the spec: "a single point and no candidate table,
    rather than an empty chart or a crash"."""
    svg, without_floor, without_rate = _timeline_parts([])

    root = _svg_root(svg)
    texts = [one for one in root.iter() if _svg_tag(one) == "text"]
    assert len(texts) == 1, f"expected exactly one <text>, found {len(texts)}"
    said = _svg_text(texts[0]).lower()
    assert re.search(r"\bno\b", said) and "run" in said, (
        f"the empty timeline must say there are no dated runs; it says {said!r}"
    )
    assert not _markers(root), "an empty series draws no marker"
    assert (without_floor, without_rate) == (0, 0)

    # An `<svg role="img">` with no `<title>` is an image with no name: a screen
    # reader announces "image" and stops, so the one fact this branch exists to
    # deliver is the one fact it does not deliver. Every other branch names itself.
    titles = [_svg_text(one) for one in root.iter() if _svg_tag(one) == "title"]
    assert any(one.strip() for one in titles), (
        "the empty chart carries no <title>, so it has no accessible name at all; the "
        f"document holds {titles}"
    )


def test_a_single_run_is_drawn_at_the_horizontal_centre_and_nothing_is_interpolated() -> None:
    """Edges row 2: "one marker, drawn at the horizontal centre; no interpolation".

    Asserted at two widths, because a centre that is only ever right at the
    default width is a hard-coded 450.
    """
    for width in (900, 640):
        svg, _, _ = _timeline_parts([_point(_timeline_day(3))], width=width)
        root = _svg_root(svg)
        markers = _markers(root)
        assert len(markers) == 1, f"one point must draw one marker, not {len(markers)}"
        placed = _marker_x(markers[0])
        assert abs(placed - width / 2) <= ONE_PIXEL, (
            f"at width={width} the single marker sits at x={placed}, not the centre {width / 2}"
        )


def test_two_runs_three_weeks_apart_are_drawn_three_weeks_apart() -> None:
    """The contract's "test that fails first", verbatim: three points at day 0,
    day 1 and day 22, and the second gap is 21 times the first, within a pixel.

    This is the whole reason the x-axis is time. Evenly spaced dots put the two
    gaps at 1:1 and hide a three-week CI outage, and the ratio is the only
    assertion that tells the two renderings apart.

    The expectation is derived from `data-created` as the document reports it
    rather than from the fixture, so the mapping is checked against what the
    chart claims about itself.
    """
    days = (0, 1, 22)
    points = [_point(_timeline_day(one)) for one in days]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    markers = _markers(root)
    assert len(markers) == 3, f"three points must draw three markers, not {len(markers)}"

    placed = sorted((_instant(str(one.get("data-created"))), _marker_x(one)) for one in markers)
    first_gap = placed[1][1] - placed[0][1]
    second_gap = placed[2][1] - placed[1][1]
    assert first_gap > 0, f"the day-0 and day-1 runs were drawn at one x: {placed}"

    elapsed_first = placed[1][0] - placed[0][0]
    elapsed_second = placed[2][0] - placed[1][0]
    assert abs(elapsed_second / elapsed_first - 21) < 1e-6, (
        f"the fixture is wrong, not the code: data-created reports a ratio of "
        f"{elapsed_second / elapsed_first}"
    )
    assert abs(second_gap - 21 * first_gap) <= ONE_PIXEL, (
        f"a 21-day gap is drawn {second_gap:.2f}px wide and a 1-day gap {first_gap:.2f}px: "
        f"a ratio of {second_gap / first_gap:.2f}, not 21. Evenly spaced dots hide the outage."
    )


def test_the_earliest_and_latest_runs_anchor_the_time_axis_at_the_padding() -> None:
    """"Map parsed `created` linearly from the earliest to the latest across
    `points`" -- so the two ends of the series are the two ends of the plot area.

    `TIMELINE_PAD` is read from the module rather than written down here. A test
    that hard-codes the constant it is checking cannot tell a changed constant
    from a broken projection.
    """
    pad = float(_get(_module(), "TIMELINE_PAD"))
    width = 720
    points = [_point(_timeline_day(one)) for one in (0, 4, 17)]
    svg, _, _ = _timeline_parts(points, width=width)

    markers = _markers(_svg_root(svg))
    assert len(markers) == 3
    assert abs(_marker_x(markers[0]) - pad) <= ONE_PIXEL, (
        f"the earliest run sits at x={_marker_x(markers[0])}, not the left edge {pad}"
    )
    assert abs(_marker_x(markers[-1]) - (width - pad)) <= ONE_PIXEL, (
        f"the latest run sits at x={_marker_x(markers[-1])}, not the right edge {width - pad}"
    )


def test_runs_that_all_share_one_timestamp_are_evenly_spaced_and_the_chart_says_so() -> None:
    """Edges row 3, and the reviewer's division by zero.

    Reachable rather than theoretical: a seed generator that patches `utc_now` to
    a constant writes every run at the same microsecond, and that is how the
    showcase series is built. The required rendering is evenly spaced markers
    plus a `<title>` saying the runs share a timestamp -- not a crash, and not
    four markers stacked on one x.
    """
    stamp = _timeline_day(9)
    width = 720
    pad = float(_get(_module(), "TIMELINE_PAD"))
    arrived = (0.74, 0.71, 0.73, 0.72)
    points = [_point(stamp, pass_rate=rate) for rate in arrived]
    svg, _, _ = _timeline_parts(points, width=width)

    root = _svg_root(svg)
    markers = _markers(root)
    assert len(markers) == 4, f"four points must draw four markers, not {len(markers)}"

    xs = [_marker_x(one) for one in markers]
    gaps = [second - first for first, second in zip(xs, xs[1:], strict=False)]
    assert min(gaps) > 0, f"a zero span stacked the markers on one x: {xs}"
    assert max(gaps) - min(gaps) <= ONE_PIXEL, f"markers are not evenly spaced: {xs}"

    # Even spacing is half the requirement; the other half is that the spacing
    # fills the axis the mapping would have used. Dividing the width by the number
    # of runs rather than by the number of gaps keeps every gap equal and leaves
    # the last run short of the right-hand edge, which reads as a series that
    # stopped early -- a claim about elapsed time, from the one chart that has
    # none to make.
    assert abs(xs[0] - pad) <= ONE_PIXEL, f"the first marker sits at x={xs[0]}, not {pad}"
    assert abs(xs[-1] - (width - pad)) <= ONE_PIXEL, (
        f"the last of four evenly spaced markers sits at x={xs[-1]}, not the right edge "
        f"{width - pad}: the spacing is even but it does not fill the axis"
    )

    # Which run is where. The clock cannot separate these four, so the only
    # ordering evidence is the order the log recorded them in, and left-to-right
    # must be that order -- not the order of any *value* on the point. Re-sorting
    # ties by pass rate draws a series that climbs, from a series that did not.
    drawn = [float(str(one.get("data-rate"))) for one in markers]
    assert drawn == pytest.approx(list(arrived)), (
        f"runs sharing one timestamp are drawn left to right as {drawn}, not in the order "
        f"they were recorded, {list(arrived)}: the tie was broken by something other than "
        "the log"
    )

    titles = [_svg_text(one).lower() for one in root.iter() if _svg_tag(one) == "title"]
    assert any("timestamp" in one for one in titles), (
        "a zero-span chart must carry a <title> saying the runs share a timestamp; its "
        f"titles are {titles}"
    )
    assert any(
        "timestamp" in one and any(word in one for word in ("share", "same", "identical"))
        for one in titles
    ), f"the <title> must say the runs *share* the timestamp; it says {titles}"


def test_a_series_whose_floor_changed_draws_a_step_and_not_one_rule() -> None:
    """The contract's second named test, and its reviewer's warning.

    The easy implementation is a `<polyline>` through the floor values, which
    draws a diagonal ramp from 0.90 down to 0.80 between two runs. A floor that
    ramps is a floor that never existed: there is no date on which this series
    was held to 0.85. So the assertion is not that both floors appear somewhere
    -- a ramp satisfies that -- but that every floor segment is axis-aligned and
    that the rule touches exactly two heights.
    """
    floors = (0.90, 0.90, 0.80, 0.80)
    points = [
        _point(_timeline_day(index * 3), floor=floor, pass_rate=floor)
        for index, floor in enumerate(floors)
    ]
    svg, without_floor, _ = _timeline_parts(points)
    assert without_floor == 0, "every point here records a floor"

    root = _svg_root(svg)
    segments = _floor_segments(root)

    for x1, y1, x2, y2 in segments:
        assert abs(x1 - x2) <= FLAT or abs(y1 - y2) <= FLAT, (
            f"the floor rule contains the diagonal ({x1:.2f},{y1:.2f})-({x2:.2f},{y2:.2f}). "
            "A floor that ramps between two floors is a floor that never existed."
        )

    heights = _levels(segments)
    assert len(heights) == 2, (
        "a floor that moved from 0.90 to 0.80 must be drawn at exactly two heights; the "
        f"rule touches {len(heights)}: {heights}. One height is the single rule the "
        "contract calls a lie; three or more is a ramp."
    )

    steps = _vertical(segments)
    assert steps, f"a floor change must be a vertical step; the rule has none: {segments}"
    assert any(
        abs(min(one[1], one[3]) - heights[0]) <= FLAT
        and abs(max(one[1], one[3]) - heights[1]) <= FLAT
        for one in steps
    ), f"no vertical segment joins the two floor heights {heights}: {steps}"

    xs = [_marker_x(one) for one in _markers(root)]
    step_x = [one[0] for one in steps]
    # Half way between the two runs, and not at either of them. The evidence says
    # the floor was 0.90 on the day of one run and 0.80 on the day of the next,
    # and says nothing whatever about the days between; a step drawn *at* the
    # earlier marker claims the floor moved on that run's own day, and one drawn
    # at the later marker claims it held until that day. Both are dates the log
    # does not contain. Anywhere inside the interval passes a test that only
    # brackets it, which is the whole interval a wrong answer lives in.
    changed = (xs[1] + xs[2]) / 2
    assert any(abs(one - changed) <= ONE_PIXEL for one in step_x), (
        f"the step sits at x={step_x}, not half way between the last run held to 0.90 "
        f"(x={xs[1]:.2f}) and the first held to 0.80 (x={xs[2]:.2f}), which is x={changed:.2f}"
    )

    flats = _horizontal(segments)
    assert any(
        abs(one[1] - heights[0]) <= FLAT
        and abs(min(one[0], one[2]) - xs[0]) <= ONE_PIXEL
        and abs(max(one[0], one[2]) - changed) <= ONE_PIXEL
        for one in flats
    ), (
        f"the higher floor does not run from the first run held to it (x={xs[0]:.2f}) to the "
        f"step (x={changed:.2f}): {flats}"
    )
    assert any(
        abs(one[1] - heights[1]) <= FLAT
        and abs(min(one[0], one[2]) - changed) <= ONE_PIXEL
        and abs(max(one[0], one[2]) - xs[3]) <= ONE_PIXEL
        for one in flats
    ), (
        f"the lower floor does not run from the step (x={changed:.2f}) to the last run held "
        f"to it (x={xs[3]:.2f}): {flats}"
    )

    # The rule says which floor it is drawing, and says it as the recorded number.
    # A `data-` value carrying the mapped y instead is a pixel that reads as a
    # rate, and it is wrong in a way no picture shows: the chart looks right.
    ruled = sorted(
        float(str(element.get("data-rule")))
        for element, _ in _floor_drawn(root)
        if element.get("data-rule") is not None
    )
    assert ruled, (
        "no floor segment records the floor it draws; the number is recoverable only by "
        "inverting the projection"
    )
    assert ruled == pytest.approx([0.80, 0.90]), (
        f"the floor rule reports the floors as {ruled}, not the recorded 0.80 and 0.90"
    )


def test_the_floor_rule_and_the_markers_are_drawn_on_one_vertical_scale() -> None:
    """A rule at a height the rate axis does not agree with cannot be read.

    The point of a floor drawn through a series of rates is that a reader can see
    which runs cleared it. Asserted without knowing the projection: a run whose
    `pass_rate` is exactly the floor must have its marker on the rule.
    """
    points = [_point(_timeline_day(one), pass_rate=0.90, floor=0.90) for one in (0, 6)]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    heights = _levels(_floor_segments(root))
    assert len(heights) == 1, f"an unchanged floor is drawn at one height, not {heights}"
    for marker in _markers(root):
        assert abs(_marker_y(marker) - heights[0]) <= ONE_PIXEL, (
            f"a run whose rate is 0.90 is drawn at y={_marker_y(marker)} while the 0.90 "
            f"floor is drawn at y={heights[0]}: the rule and the markers use two scales"
        )


def test_a_run_with_no_recorded_floor_leaves_a_gap_in_the_rule_and_is_counted() -> None:
    """The contract's third named test, and Edges row 5: "the rule breaks and
    resumes".

    This is where the spec's fallback survives. A run whose evidence never
    recorded the floor it was held to must not be covered by its neighbours'
    floor -- drawing 0.90 across it asserts a number the log does not contain --
    and the count of such runs is returned so the caller can say so in prose.
    """
    floors = (0.90, 0.90, None, 0.90, 0.90)
    points = [
        _point(_timeline_day(index * 2), floor=floor, pass_rate=0.93)
        for index, floor in enumerate(floors)
    ]
    svg, without_floor, without_rate = _timeline_parts(points)

    assert without_floor == 1, f"one of five runs has no floor; the count says {without_floor}"
    assert without_rate == 0, "every point here records a rate"

    root = _svg_root(svg)
    markers = _markers(root)
    assert len(markers) == 5, "a missing floor does not remove the run's own marker"
    gap_x = _marker_x(markers[2])

    segments = _floor_segments(root)
    # A segment of zero length is not a segment: it draws nothing, and it answers
    # yes to both "is this horizontal" and "is this vertical", so a step emitted
    # across the gap between two runs held to the *same* floor slips through every
    # shape assertion below while sitting exactly on the run that recorded none.
    degenerate = [one for one in segments if _length(one) <= FLAT]
    assert not degenerate, (
        f"the floor rule contains {len(degenerate)} zero-length segment(s) {degenerate}: a "
        f"step drawn where the rule is supposed to break, at the run that recorded no floor"
    )

    flats = _horizontal(segments)
    spanning = [
        one for one in flats if min(one[0], one[2]) + FLAT < gap_x < max(one[0], one[2]) - FLAT
    ]
    assert not spanning, (
        f"a floor segment runs straight across the run at x={gap_x:.2f} that recorded no "
        f"floor: {spanning}. The rule must break there rather than bridge the neighbours."
    )
    assert any(max(one[0], one[2]) <= gap_x + FLAT for one in flats), (
        f"nothing is drawn to the left of the gap at x={gap_x:.2f}: {flats}"
    )
    assert any(min(one[0], one[2]) >= gap_x - FLAT for one in flats), (
        f"the rule does not resume to the right of the gap at x={gap_x:.2f}: {flats}"
    )


def test_a_run_with_no_pass_rate_is_not_drawn_and_is_counted() -> None:
    """Edges row 6: "no marker; counted and reported".

    A point with no rate has no height, and inventing one -- a zero, a carried
    neighbour -- draws a value nothing measured.
    """
    dated = [_timeline_day(one) for one in (0, 5, 11)]
    points = [
        _point(dated[0]),
        _point(dated[1], pass_rate=None, verdict=None),
        _point(dated[2]),
    ]
    svg, without_floor, without_rate = _timeline_parts(points)

    assert without_rate == 1, f"one of three points has no rate; the count says {without_rate}"
    assert without_floor == 0, "every point here records a floor"

    root = _svg_root(svg)
    drawn = _by_created(root)
    assert dated[1] not in drawn, "the run with no rate was given a marker anyway"
    assert set(drawn) == {dated[0], dated[2]}, (
        f"the markers are dated {sorted(drawn)}; expected only {[dated[0], dated[2]]}"
    )


def test_the_position_of_a_marker_is_its_date_and_not_its_place_in_the_sequence() -> None:
    """"Must not: sort by index."

    The points arrive out of chronological order, which is what a log stitched
    from two machines looks like. Position must come from `created` either way.
    """
    early, late, middle = _timeline_day(0), _timeline_day(22), _timeline_day(1)
    svg, _, _ = _timeline_parts([_point(early), _point(late), _point(middle)])

    drawn = _by_created(_svg_root(svg))
    assert set(drawn) == {early, late, middle}
    placed = {key: _marker_x(value) for key, value in drawn.items()}
    assert placed[early] < placed[middle] < placed[late], (
        f"the markers are laid out in the order the points arrived, not by date: {placed}"
    )


def test_every_marker_carries_the_four_data_attributes_and_a_verdict_class() -> None:
    """"Each marker carries `data-created`, `data-rate`, `data-verdict`,
    `data-floor`", and "a `class` naming the verdict (go/nogo/review/none)".

    The data attributes are what makes the projection assertable at all, and the
    class is what makes the verdict readable without colour.
    """
    # The contract names the *class* tokens go/nogo/review/none; the values a
    # `RunPoint.verdict` actually carries are `Verdict`'s, which is a different
    # vocabulary -- `NO-GO` is classed `nogo`. Seeding this with the class names
    # would test a verdict string no evidence log contains.
    cases = (
        (Verdict.GO, "go"),
        (Verdict.NO_GO, "nogo"),
        (Verdict.REVIEW, "review"),
        (None, "none"),
    )
    points = [
        _point(_timeline_day(index * 2), verdict=verdict)
        for index, (verdict, _class) in enumerate(cases)
    ]
    svg, _, _ = _timeline_parts(points)

    markers = _markers(_svg_root(svg))
    assert len(markers) == len(cases)
    for marker, (verdict, expected) in zip(markers, cases, strict=True):
        named = VERDICT_CLASSES.intersection(one.lower() for one in _classes(marker))
        assert named, (
            f"the marker for verdict {verdict!r} carries classes {_classes(marker)}, none of "
            f"which names a verdict out of {sorted(VERDICT_CLASSES)}"
        )
        assert expected in named, f"the marker for verdict {verdict!r} is classed {sorted(named)}"
        assert float(str(marker.get("data-rate"))) == pytest.approx(0.82)
        assert float(str(marker.get("data-floor"))) == pytest.approx(0.80)


def test_a_marker_whisker_spans_the_recorded_interval() -> None:
    """"a vertical whisker spanning the mapped `interval`".

    Asserted as a comparison rather than against a projection this file does not
    know: the run with the wider interval must carry the taller whisker, and each
    whisker must bracket its own marker. A whisker of constant height is a
    decoration, not an interval.
    """
    wide, narrow = _timeline_day(0), _timeline_day(4)
    points = [
        _point(wide, pass_rate=0.62, interval=(0.30, 0.94)),
        _point(narrow, pass_rate=0.62, interval=(0.60, 0.64)),
    ]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    heights: dict[str, float] = {}
    for created, marker in _by_created(root).items():
        at = _marker_x(marker)
        uprights = [
            one
            for _, one in _geometry(root)
            if abs(one[0] - one[2]) <= FLAT and abs(one[0] - at) <= ONE_PIXEL
        ]
        assert uprights, f"the run at {created} carries no vertical whisker at x={at:.2f}"
        tallest = max(uprights, key=lambda one: abs(one[1] - one[3]))
        assert min(tallest[1], tallest[3]) <= _marker_y(marker) + ONE_PIXEL
        assert max(tallest[1], tallest[3]) >= _marker_y(marker) - ONE_PIXEL
        heights[created] = abs(tallest[1] - tallest[3])

    assert heights[wide] > heights[narrow] + ONE_PIXEL, (
        f"an interval 0.64 wide is drawn {heights[wide]:.2f}px tall and one 0.04 wide "
        f"{heights[narrow]:.2f}px: the whisker does not span the interval"
    )


def test_the_timeline_draws_nothing_outside_the_box_it_was_given() -> None:
    """`width` and `height` are the caller's, and the chart is embedded in a page.

    Run at neither default, so a projection that hard-coded 900x260 puts markers
    off the right-hand edge rather than merely being wrong by a scale factor.

    **The points arrive out of chronological order**, and that is what makes this
    a bounds test rather than a restatement of the projection. A mapping that
    spans "the first record to the last record" instead of "the earliest instant
    to the latest" is correct on sorted input and unbounded on unsorted input: the
    day-13 run divided by a two-day span lands at x=2312 in a chart 400 wide,
    twenty times off the right edge and invisible. Every other test in this
    section reads relative order, which such a mapping preserves, so this is the
    one that has to see the absolute number.

    The count is asserted before the bounds are: a document that drew nothing
    satisfies every bound there is, and against an inert stub that is exactly
    what this test passed on before the count was added.
    """
    width, height = 400, 180
    arrived = ((0, 0.02), (13, 0.99), (2, 0.51))
    points = [_point(_timeline_day(one), pass_rate=rate) for one, rate in arrived]
    svg, _, _ = _timeline_parts(points, width=width, height=height)

    markers = _markers(_svg_root(svg))
    assert len(markers) == 3, f"three points must draw three markers, not {len(markers)}"
    for marker in markers:
        at_x, at_y = _marker_x(marker), _marker_y(marker)
        assert 0 <= at_x <= width, f"a marker sits at x={at_x} in a chart {width} wide"
        assert 0 <= at_y <= height, f"a marker sits at y={at_y} in a chart {height} tall"


def test_the_timeline_emits_no_script_element() -> None:
    """"Must not: emit `<script>`."

    The document this is embedded in is asserted self-contained elsewhere; the
    timeline must not be the thing that breaks it.

    Every assertion here is an absence, so the presence of the three markers is
    asserted first. An empty `<svg/>` emits no script either, and against an
    inert stub that is what this test passed on before the count was added.
    """
    points = [_point(_timeline_day(one)) for one in (0, 2, 9)]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    assert len(_markers(root)) == 3, "three points must be drawn before absence means anything"
    forbidden = [_svg_tag(one) for one in root.iter() if _svg_tag(one) in FORBIDDEN_ELEMENTS]
    assert not forbidden, f"the timeline emits {forbidden}, which self-containment forbids"

    # `xmlns` is a namespace name, not a fetch: no browser requests it. The
    # report's own `external_urls` was written for HTML and does not know that,
    # so it is excluded here rather than the timeline being forbidden the
    # declaration a standalone SVG needs.
    fetched = [one for one in _urls(svg) if not str(_get(one, "attribute")).startswith("xmlns")]
    assert not fetched, f"the timeline fetches {fetched}"


# --------------------------------------------------------------------------- #
# 17b. What the section above computes correctly and did not yet pin.
#
# Added after a mutation run over the timeline: 29 of 61 mutants survived, and
# every one of them was a hole in this suite rather than a defect in the module.
# The tests below are those survivors written down. Each names the wrong picture
# it exists to reject, because "this assertion kills a mutant" is a fact about a
# tool that will not be in the room, and the wrong picture is a fact about the
# report.
# --------------------------------------------------------------------------- #


def test_a_lone_run_still_draws_a_floor_rule_across_itself() -> None:
    """One run is where "no rule drawn" and "no floor recorded" collide.

    A group of one begins and ends at the same marker, so the arithmetically
    honest width of its rule is zero -- and a zero-width rule is invisible, which
    is precisely what a run whose floor was never recorded looks like. Those are
    two different facts about a run, the chart returns a separate count for one of
    them, and the picture may not render them identically. So the rule for a lone
    run is drawn *across* it: beginning to its left and ending to its right.

    No other test in this section calls the floor helpers on a one-run series, and
    a one-run series is not a curiosity -- it is what the first night of a
    migration produces.
    """
    svg, without_floor, _ = _timeline_parts([_point(_timeline_day(5), floor=0.88)])
    assert without_floor == 0, "the one run in this series records a floor"

    root = _svg_root(svg)
    markers = _markers(root)
    assert len(markers) == 1, f"one point must draw one marker, not {len(markers)}"
    at = _marker_x(markers[0])

    segments = _floor_segments(root)
    assert all(_length(one) > FLAT for one in segments), (
        f"the lone run's floor rule is drawn as a point of zero length: {segments}. "
        "Invisible is what 'this run recorded no floor' looks like, and this run recorded "
        "one -- the count above says so."
    )
    flats = _horizontal(segments)
    assert any(min(one[0], one[2]) < at < max(one[0], one[2]) for one in flats), (
        f"no floor segment runs across the single run at x={at:.2f}: {flats}"
    )


def test_a_floor_rule_reaches_half_way_to_the_run_on_either_side() -> None:
    """Where the rule ends and where the step stands, as numbers.

    The step test above asserts that the rule is a step and not a ramp, which is
    the failure the contract names. It does not say *where*. A rule that stops at
    its own outermost marker, or reaches a quarter of the way to the next run
    instead of half, or a step drawn at one of the two runs rather than between
    them, all draw a step function -- a wrong one. The last of those is a claim
    about a date: a step at the earlier marker says the floor moved on that run's
    own day, and the evidence names no day at all. Half way claims nothing, and it
    is the only position symmetric between the two runs whose floors differ.

    Three floors and three runs, so the middle group is bounded by a midpoint on
    *both* sides -- the case no other fixture in this section contains. The days
    are uneven so that no midpoint can coincide with a marker.
    """
    floors = (0.90, 0.85, 0.80)
    points = [
        _point(_timeline_day(day), floor=floor, pass_rate=0.95)
        for day, floor in zip((0, 4, 10), floors, strict=True)
    ]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    xs = [_marker_x(one) for one in _markers(root)]
    assert len(xs) == 3, f"three points must draw three markers, not {len(xs)}"
    first_change, second_change = (xs[0] + xs[1]) / 2, (xs[1] + xs[2]) / 2

    segments = _floor_segments(root)
    heights = _levels(segments)
    assert len(heights) == 3, (
        f"three distinct floors must be drawn at three heights, not {len(heights)}: {heights}"
    )

    # `_levels` sorts by y and y grows downward, so the highest floor comes first.
    # Pairing them this way asserts the vertical order too: a rule that drew 0.80
    # above 0.90 would land these spans on the wrong heights.
    spans = ((xs[0], first_change), (first_change, second_change), (second_change, xs[2]))
    flats = _horizontal(segments)
    for floor, height, (start, end) in zip(floors, heights, spans, strict=True):
        matched = [
            one
            for one in flats
            if abs(one[1] - height) <= FLAT
            and abs(min(one[0], one[2]) - start) <= ONE_PIXEL
            and abs(max(one[0], one[2]) - end) <= ONE_PIXEL
        ]
        assert matched, (
            f"the rule for floor {floor} is not drawn from x={start:.2f} to x={end:.2f} at "
            f"y={height:.2f}. A rule reaches half way to the run on each side, so that a "
            f"group of one is visible and so that neither neighbour's day is claimed. The "
            f"horizontal segments are {flats}"
        )

    steps = [one[0] for one in _vertical(segments) if _length(one) > FLAT]
    for change in (first_change, second_change):
        assert any(abs(one - change) <= ONE_PIXEL for one in steps), (
            f"no vertical step stands at x={change:.2f}, half way between the two runs whose "
            f"floors differ; the steps are at {steps}"
        )


def test_a_run_that_recorded_no_interval_carries_no_whisker() -> None:
    """An interval nothing measured may not be drawn.

    `interval` is `None` on any run whose evidence recorded no bounds, and a
    whisker invented for it -- from zero to the rate, from the floor to the rate,
    from anywhere to anywhere -- is a confidence interval this document made up.
    It is the most quotable object on the chart and the least checkable: a reader
    sees an error bar and believes a measurement was taken.

    Every other whisker test hands in runs that all recorded one, so an invented
    whisker is invisible to them.
    """
    points = [_point(_timeline_day(one), interval=None) for one in (0, 6)]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    markers = _markers(root)
    assert len(markers) == 2, f"two points must draw two markers, not {len(markers)}"
    for marker in markers:
        at = _marker_x(marker)
        uprights = [
            one
            for _, one in _geometry(root)
            if abs(one[0] - one[2]) <= FLAT
            and abs(one[0] - at) <= ONE_PIXEL
            and _length(one) > FLAT
        ]
        assert not uprights, (
            f"the run at x={at:.2f} recorded no interval and was given a whisker anyway: "
            f"{uprights}. An error bar is read as a measurement."
        )


def test_a_whisker_reaches_both_ends_of_the_recorded_interval() -> None:
    """Not merely taller than its neighbour, and not merely bracketing its marker.

    A whisker drawn from the interval's lower bound up to the *rate* -- half the
    interval -- is taller when the interval is wider and does bracket the marker
    it belongs to, so the comparison above passes on it. It also understates the
    uncertainty of every run on the chart, in the direction that makes a
    borderline candidate look decided.

    Asserted without knowing the projection, by giving two other runs the rates at
    the two ends of the interval: the whisker's ends must land on their markers.
    """
    lower, upper, measured = 0.30, 0.94, 0.62
    low_run, wide_run, high_run = _timeline_day(0), _timeline_day(3), _timeline_day(6)
    points = [
        _point(low_run, pass_rate=lower),
        _point(wide_run, pass_rate=measured, interval=(lower, upper)),
        _point(high_run, pass_rate=upper),
    ]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    drawn = _by_created(root)
    assert set(drawn) == {low_run, wide_run, high_run}
    bottom_of_interval = _marker_y(drawn[low_run])
    top_of_interval = _marker_y(drawn[high_run])
    at = _marker_x(drawn[wide_run])

    uprights = [
        one
        for _, one in _geometry(root)
        if abs(one[0] - one[2]) <= FLAT and abs(one[0] - at) <= ONE_PIXEL and _length(one) > FLAT
    ]
    assert uprights, f"the run at x={at:.2f} recorded an interval and carries no whisker"
    tallest = max(uprights, key=lambda one: abs(one[1] - one[3]))

    assert abs(max(tallest[1], tallest[3]) - bottom_of_interval) <= ONE_PIXEL, (
        f"the whisker's lower end is at y={max(tallest[1], tallest[3]):.2f}; the run whose "
        f"rate is {lower} is drawn at y={bottom_of_interval:.2f}"
    )
    assert abs(min(tallest[1], tallest[3]) - top_of_interval) <= ONE_PIXEL, (
        f"the whisker's upper end is at y={min(tallest[1], tallest[3]):.2f}; the run whose "
        f"rate is {upper} is drawn at y={top_of_interval:.2f}. A whisker that stops at the "
        "measured rate draws half an interval and understates every run on the chart."
    )


def test_a_rate_the_axis_cannot_hold_is_clamped_to_it_and_still_reported() -> None:
    """`series` refuses a NaN and an infinity, and accepts a 1.5.

    `pass_rate` is a fraction, and a corrupt log carrying 1.5 is a log this
    package builds a `RunPoint` from without complaint. The axis is fixed at 0 to
    1 -- deliberately, so that height can be read as rate -- so an unclamped 1.5
    draws the marker above the plot area: over the chart's own text, or off the
    top of the viewBox entirely, where the run is not mispositioned but missing.

    The number is not the picture's to correct, though. `data-rate` reports what
    was recorded, so a reader can see that the chart and the log disagree instead
    of being shown a tidy 1.0 that no evidence contains.
    """
    pad = float(_get(_module(), "TIMELINE_PAD"))
    width, height = 720, 240
    corrupt = (1.5, -0.25)
    points = [
        _point(_timeline_day(index * 4), pass_rate=rate) for index, rate in enumerate(corrupt)
    ]
    svg, _, without_rate = _timeline_parts(points, width=width, height=height)

    assert without_rate == 0, "a rate outside 0 to 1 is a recorded rate, not a missing one"
    markers = _markers(_svg_root(svg))
    assert len(markers) == 2, f"two points must draw two markers, not {len(markers)}"
    for marker, rate in zip(markers, corrupt, strict=True):
        at_y = _marker_y(marker)
        assert pad - ONE_PIXEL <= at_y <= height - pad + ONE_PIXEL, (
            f"a recorded rate of {rate} is drawn at y={at_y:.2f}, outside the plot area "
            f"{pad} to {height - pad}: the marker is off the chart rather than at its edge"
        )
        assert float(str(marker.get("data-rate"))) == pytest.approx(rate), (
            f"the marker reports data-rate={marker.get('data-rate')!r}; the log recorded "
            f"{rate}, and the projection is the picture's business alone"
        )


def test_both_counts_count_points_and_not_what_the_chart_managed_to_draw() -> None:
    """R6: both counts are counts of *points*, never of segments or of markers.

    The sentence beneath the chart is the only place a reader learns what the
    picture could not show, so it has to count the runs the picture dropped for a
    second reason as well. A run with an unreadable date and no recorded floor is
    still a run whose floor is unknown; counting only the runs that reached the
    axis would hide it behind the undated note and undercount the very thing the
    fallback exists to disclose.

    Every other fixture in this section varies one absence at a time, so no
    existing test can tell "points" from "points that were drawn".
    """
    points = [
        _point(_timeline_day(0)),
        _point("not a timestamp at all", floor=None, pass_rate=None, verdict=None),
        _point(_timeline_day(3), floor=None),
        _point(_timeline_day(5), pass_rate=None, verdict=None),
    ]
    svg, without_floor, without_rate = _timeline_parts(points)

    assert without_floor == 2, (
        f"two of four points record no floor -- one of them undated -- and the count says "
        f"{without_floor}"
    )
    assert without_rate == 2, (
        f"two of four points record no rate -- one of them undated -- and the count says "
        f"{without_rate}"
    )
    assert len(_markers(_svg_root(svg))) == 2, "only the two dated runs with a rate are drawn"


def test_a_run_whose_date_will_not_parse_is_left_off_and_the_picture_says_how_many() -> None:
    """An axis that is time has no position for a run that carries no instant.

    The contract does not name this case and the blind suite deliberately left it
    alone rather than pin a guess. It is no longer a guess: the module's docstring
    records the decision -- left off, "and the picture says how many were" -- and
    an undrawn run the picture does not mention is the uncounted absence this
    whole document is built against. Both alternatives are worse and both are
    reachable by accident: placing it at a fixed instant puts an invented date on
    the axis, and dropping it in silence loses a defect in the log.

    The `<title>` counts what was drawn, for the same reason. It is the chart's
    accessible name, and "over 3 runs" spoken over a picture of two is the version
    of this failure that only a screen-reader user meets.
    """
    dated = (_timeline_day(0), _timeline_day(8))
    points = [_point(dated[0]), _point("2026-07-99T99:99:99+00:00"), _point(dated[1])]
    svg, _, _ = _timeline_parts(points)

    root = _svg_root(svg)
    drawn = _by_created(root)
    assert set(drawn) == set(dated), (
        f"the markers are dated {sorted(drawn)}; a run whose date will not parse was given "
        "a position on an axis that is time"
    )

    notes = [_svg_text(one).lower() for one in root.iter() if _svg_tag(one) == "text"]
    assert any("1" in one and "date" in one for one in notes), (
        f"the chart never says that one run was left off for want of a usable date: {notes}"
    )

    titles = [_svg_text(one).lower() for one in root.iter() if _svg_tag(one) == "title"]
    assert any("2 run" in one for one in titles), (
        f"the chart names itself as covering a number of runs it did not draw: {titles}"
    )


def test_a_series_of_nothing_but_unparseable_dates_says_how_many_it_dropped() -> None:
    """The all-undated chart is not the empty chart and may not say the same thing.

    "No dated runs to plot" over an empty series is a complete statement: there
    were none. Over four runs whose timestamps this package could not read it is
    an omission that reads as reassurance -- the reader is told the series was
    empty when what happened is that every record in it was malformed.
    """
    points = [_point("no date here") for _ in range(4)]
    svg, without_floor, without_rate = _timeline_parts(points)

    root = _svg_root(svg)
    assert not _markers(root), "an undated run cannot be placed on an axis that is time"
    said = " ".join(_svg_text(one).lower() for one in root.iter() if _svg_tag(one) == "text")
    assert "4" in said, (
        f"four runs were dropped for want of a usable date and the picture says {said!r}, "
        "which a reader takes to mean the series was empty"
    )
    assert (without_floor, without_rate) == (0, 0), "every point here records both"


def test_the_charts_own_styles_cannot_escape_the_chart() -> None:
    """A `<style>` inside inline SVG is not scoped: it styles the whole page.

    The chart carries its own stylesheet because an SVG `<line>` with no `stroke`
    is invisible rather than black, so a timeline that inherited its colours from
    the report would render as an empty rectangle on the offline machine this
    project promises the document still works on. The price of carrying one is
    that every rule in it is a rule about the *report*: an unprefixed `text{...}`
    restyles every paragraph around the chart, and `rect{...}` reaches into the
    other SVG this module draws.

    Both halves are asserted, because they fail in opposite directions and neither
    failure raises anything. Unprefix the rules and the chart's colours leak
    outward; drop the class from the root and every rule selects nothing, so the
    chart loses the colours it inlined a stylesheet to keep.

    An at-rule is reported as unscoped too. That is not an oversight: rules nested
    inside one need the same prefix, and this check cannot see into it.
    """
    points = [_point(_timeline_day(one)) for one in (0, 3, 9)]
    svg, _, _ = _timeline_parts(points)
    root = _svg_root(svg)

    scopes = _classes(root)
    assert scopes, (
        "the root <svg> carries no class, so nothing in the document can be selected as "
        "'this chart' and every scoped rule in its stylesheet matches nothing"
    )

    sheets = [one for one in root.iter() if _svg_tag(one) == "style"]
    assert sheets, "the timeline inlines no <style>, so its lines inherit no stroke at all"

    selectors: list[str] = []
    for sheet in sheets:
        for rule in "".join(sheet.itertext()).split("}"):
            head = rule.split("{")[0].strip()
            selectors.extend(one.strip() for one in head.split(",") if one.strip())
    assert selectors, f"the <style> declares no rule: {[_svg_text(one) for one in sheets]}"

    leaking = [
        one
        for one in selectors
        if not any(one.startswith(f".{scope}") for scope in scopes)
    ]
    assert not leaking, (
        f"{len(leaking)} of {len(selectors)} rules are not scoped to the chart: {leaking}. "
        f"The root carries {scopes}, and an inline SVG stylesheet applies to the whole "
        "document, so these restyle the report around the chart."
    )


def test_recorded_text_is_escaped_before_it_becomes_markup() -> None:
    """This module's posture is that everything that came off disk is hostile.

    Nothing on this chart can reach the escaper with a quote *today*. A marker is
    drawn only for a `created` that parsed as a timestamp, and a verdict travels
    verbatim only when it is one this report already knows; both filters happen to
    exclude a `"`, and neither exists for that reason -- one is a date parser and
    the other is a class-name whitelist. So the escape is unreachable, every other
    test in this section passes without it, and the day either filter is loosened
    it is the only thing standing between an evidence log and the markup of a
    document somebody signs off on.
    """
    escape = _get(_module(), "_svg_attr")

    written = escape('x" onload="alert(1)')
    assert '"' not in written, (
        f"a recorded quote survives into an attribute value as {written!r}: the attribute "
        "closes there and the rest of the record becomes markup"
    )
    assert escape("<b>&") == "&lt;b&gt;&amp;", (
        f"recorded angle brackets and ampersands are written straight through: {escape('<b>&')!r}"
    )


def test_parse_created_is_the_packages_one_timestamp_reader() -> None:
    """`parse_created` is public, exported, and had no test of its own.

    It is public because the timeline needs the parsed instant rather than a
    predicate -- an axis that is time has to subtract two of these -- so the chunk
    that made it public is the chunk that owes it a test. Two of its decisions are
    invisible from the chart and are recorded here rather than only in its
    docstring: a trailing `Z` means UTC, and a timestamp carrying no offset is
    *read* as UTC rather than left naive.

    The offsetless case is not a nicety. Python refuses to compare a naive
    datetime with an aware one, so one offsetless record in a log full of `+00:00`
    ones raises `TypeError` out of the sort the timeline performs before it draws
    anything: the whole chart, lost to one malformed field.
    """
    from datetime import datetime, timedelta, timezone

    from model_migration_kit import series

    assert "parse_created" in series.__all__, (
        f"parse_created is the only timestamp parser in this package and is not exported: "
        f"{series.__all__}"
    )

    noon = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert series.parse_created("2026-07-01T12:00:00+00:00") == noon
    assert series.parse_created("2026-07-01T12:00:00.000000+00:00") == noon

    zulu = series.parse_created("2026-07-01T12:00:00Z")
    assert zulu is not None and zulu.utcoffset() == timedelta(0), (
        f"a trailing Z is UTC and nothing else; this one parsed as {zulu!r}"
    )
    assert zulu == noon, f"the Z form and the +00:00 form name different instants: {zulu!r}"

    naive = series.parse_created("2026-07-01T12:00:00")
    assert naive is not None, "an offsetless timestamp is still a timestamp"
    assert naive.tzinfo is not None, (
        f"an offsetless timestamp was left naive as {naive!r}; sorting it beside an aware "
        "one raises TypeError, which loses the chart rather than misplacing one marker"
    )
    assert naive == noon, f"an offsetless timestamp is read as UTC; this one read as {naive!r}"
    assert sorted([naive, noon]) == [naive, noon], "two parsed instants must be comparable"

    east = series.parse_created("2026-07-01T14:00:00+02:00")
    assert east == noon, f"a recorded offset is not applied: {east!r}"

    for refused in ("", "not a timestamp", "2026-07-99T12:00:00+00:00", "2026-07-01 noon"):
        assert series.parse_created(refused) is None, (
            f"{refused!r} was read as an instant, so a malformed record becomes a marker at "
            "a date on which nothing ran"
        )


# --------------------------------------------------------------------------- #
# 18. C19: the verdict belongs to the comparison before it.
#
#     Two rules that are one rule. In the series, every ``migkit.verdict`` record
#     updates the most recently opened point. In the headline, ``from_evidence``
#     clears the verdict it is holding on every ``migkit.comparison`` record, so
#     a verdict only ever describes the comparison it followed.
#
#     Shipping either alone leaves the banner and the timeline free to disagree
#     about which night a NO-GO belongs to, so the assertion that matters in
#     every test below is that the two **agree**. Each half can be individually
#     plausible and still disagree -- that is the whole defect -- and a test that
#     reads only the banner, or only the last point, cannot see it. Every shape
#     therefore goes through :func:`_agreed_verdict`, which asserts the pair
#     before any test is allowed to look at either half of it.
# --------------------------------------------------------------------------- #


#: Reason sentences for two verdict records in one log. Two records carrying the
#: same word prove nothing about *which* of them a field was read from, so the
#: reason is what tells them apart in an assertion.
FIRST_VERDICT_REASON = "C19-REASON-FIRST: the first verdict record written to this log."
SECOND_VERDICT_REASON = "C19-REASON-SECOND: the last verdict record written to this log."

#: A distinct envelope stamp for the second of two verdicts on one run, so that
#: nothing in this section depends on two records sharing a timestamp.
TS_VERDICT_AGAIN = "2026-08-13T08:59:59.500000+00:00"


def _log_of(scenario: Scenario, name: str, *records: Mapping[str, Any]) -> Path:
    """Exactly these records, in this order, beside ``scenario``'s own log.

    Unlike :func:`_log_with_history` this appends nothing of its own. The shapes
    in this section are entirely about which record follows which, so the log has
    to be spelled out rather than assembled around a fixed headline run.
    """
    return _write_evidence(scenario.root / name, list(records))


def _headline_comparison(scenario: Scenario) -> dict[str, Any]:
    """``scenario``'s own comparison: the run every shape below reports on."""
    return _record(EVENT_COMPARISON, scenario.comparison, TS_COMPARISON)


def _headline_verdict(
    scenario: Scenario, verdict: str, *, reason: str, ts: str = TS_VERDICT
) -> dict[str, Any]:
    """A ``migkit.verdict`` for the headline run, named by its own reason string."""
    assert scenario.verdict is not None, "this section builds its logs from the payloads"
    return _record(
        EVENT_VERDICT,
        dict(
            scenario.verdict,
            verdict=verdict,
            exit_code=Verdict.exit_code(verdict),
            reason=reason,
        ),
        ts,
    )


def _older_comparison(scenario: Scenario, *, tag: str = "old") -> dict[str, Any]:
    """An earlier night's comparison, contradicted in every field. See section 16."""
    return _record(
        EVENT_COMPARISON, _earlier_comparison(scenario, tag=tag), EARLIER_TS_COMPARISON
    )


def _older_verdict(*, tag: str = "old", verdict: str = Verdict.GO) -> dict[str, Any]:
    """An earlier night's verdict, whose reason names the night it belongs to."""
    return _record(EVENT_VERDICT, _earlier_verdict(tag, verdict), EARLIER_TS_VERDICT)


def _older_reason(tag: str) -> str:
    """The sentence :func:`_earlier_verdict` writes, so a test can name one night."""
    return _earlier_verdict(tag, Verdict.GO)["reason"]


def _missing_verdict_sentence(model: Any) -> str | None:
    """The completeness sentence that names the absent ``migkit.verdict`` record.

    Matched on the event type rather than on the wording, because the sentence is
    prose that may be reworded and the record name is the fact the Edges row
    requires the strip to name.
    """
    for sentence in _get(_get(model, "completeness"), "missing"):
        if EVENT_VERDICT in str(sentence):
            return str(sentence)
    return None


def _agreed_verdict(model: Any) -> tuple[Any, Any]:
    """The banner's verdict and reason, and the last point's -- asserted equal, once.

    Returns the pair only after asserting the two *are* one pair, so that no test
    in this section can accidentally check one half of the thing the chunk is
    about. The reason travels beside the verdict because a document can carry the
    right word next to the wrong sentence: ``from_evidence`` reads both out of one
    record, and a timeline that agrees on GO while disagreeing about which night
    said GO is the same defect one field over.

    The candidate model is checked first. "The banner and the last point agree" is
    worth nothing unless the last point is the run the banner is about, and on a
    series that was sorted, or short a point, it would not be.
    """
    series = _series(model)
    assert series, "a report was built, so the log held a comparison, and yet no point"
    point = series[-1]
    headline_model = _get(_get(model, "candidate"), "model_id")
    assert point.candidate_model == headline_model, (
        f"series[-1] describes {point.candidate_model!r} and the headline describes "
        f"{headline_model!r}; two things that disagree about the run cannot be "
        f"checked for agreement about its verdict"
    )
    headline = (_get(model, "verdict"), _get(model, "reason"))
    assert headline == (point.verdict, point.reason), (
        f"the banner says {headline!r} and the last point of the timeline says "
        f"{(point.verdict, point.reason)!r}, about the same run of the same log"
    )
    return headline


#: The agreement table, transcribed. ``shape`` is the log in the contract's own
#: notation; ``build`` writes it; ``verdict`` and ``reason`` are what both halves
#: must report; ``points`` is every point's verdict, in log order, which is what
#: separates "the last one happens to be right" from "the rule is right".
_AGREEMENT_TABLE = (
    (
        "C V",
        lambda s: [
            _headline_comparison(s),
            _headline_verdict(s, Verdict.NO_GO, reason=SECOND_VERDICT_REASON),
        ],
        Verdict.NO_GO,
        SECOND_VERDICT_REASON,
        [Verdict.NO_GO],
    ),
    (
        "C1 V1 C2 V2",
        lambda s: [
            _older_comparison(s),
            _older_verdict(verdict=Verdict.GO),
            _headline_comparison(s),
            _headline_verdict(s, Verdict.NO_GO, reason=SECOND_VERDICT_REASON),
        ],
        Verdict.NO_GO,
        SECOND_VERDICT_REASON,
        [Verdict.GO, Verdict.NO_GO],
    ),
    (
        "C",
        lambda s: [_headline_comparison(s)],
        None,
        None,
        [None],
    ),
    (
        "V C",
        lambda s: [
            _older_verdict(tag="stray", verdict=Verdict.GO),
            _headline_comparison(s),
        ],
        None,
        None,
        [None],
    ),
    (
        "C1 C2 V",
        lambda s: [
            _older_comparison(s),
            _headline_comparison(s),
            _headline_verdict(s, Verdict.NO_GO, reason=SECOND_VERDICT_REASON),
        ],
        Verdict.NO_GO,
        SECOND_VERDICT_REASON,
        [None, Verdict.NO_GO],
    ),
    (
        "C1 V1 C2",
        lambda s: [
            _older_comparison(s),
            _older_verdict(verdict=Verdict.GO),
            _headline_comparison(s),
        ],
        None,
        None,
        [Verdict.GO, None],
    ),
    (
        "C1 C2 V1 V2",
        lambda s: [
            _older_comparison(s),
            _headline_comparison(s),
            _headline_verdict(s, Verdict.GO, reason=FIRST_VERDICT_REASON),
            _headline_verdict(
                s, Verdict.NO_GO, reason=SECOND_VERDICT_REASON, ts=TS_VERDICT_AGAIN
            ),
        ],
        Verdict.NO_GO,
        SECOND_VERDICT_REASON,
        [None, Verdict.NO_GO],
    ),
    (
        "C V1 V2",
        lambda s: [
            _headline_comparison(s),
            _headline_verdict(s, Verdict.GO, reason=FIRST_VERDICT_REASON),
            _headline_verdict(
                s, Verdict.NO_GO, reason=SECOND_VERDICT_REASON, ts=TS_VERDICT_AGAIN
            ),
        ],
        Verdict.NO_GO,
        SECOND_VERDICT_REASON,
        [Verdict.NO_GO],
    ),
)


@pytest.mark.parametrize(
    ("shape", "build", "verdict", "reason", "points"),
    _AGREEMENT_TABLE,
    ids=[row[0].replace(" ", "-") for row in _AGREEMENT_TABLE],
)
def test_the_banner_and_the_last_point_agree_on_every_shape_the_table_names(
    tmp_path: Path,
    shape: str,
    build: Any,
    verdict: str | None,
    reason: str | None,
    points: list[str | None],
) -> None:
    """C19's agreement table, all eight rows, asserted as agreement rather than as
    two separately right answers.

    Three of these rows read the same under C2's rule and are here as the "must
    not" the chunk carries: a complete log renders exactly what it rendered
    before, and only a log holding a crashed run may change. The other five are
    the chunk. ``points`` is asserted in full rather than only at its last entry
    because on ``C1 C2 V1 V2`` the last point is right under either rule and the
    *first* is not, and a run whose verdict silently moved to another night is the
    failure whether or not it moved to the last one.

    When the headline has no verdict the exit code and the completeness strip are
    checked too: ``cli.py:435`` derives the exit code from the verdict alone, so a
    banner reading "no verdict" over an exit code of 0 is a pipeline seeing green.
    """
    slug = shape.replace(" ", "-")
    scenario = _scenario(tmp_path / f"shape-{slug}", verdict=Verdict.NO_GO)
    log = _log_of(scenario, "evidence-shape.jsonl", *build(scenario))
    model = _model_from(log)

    assert _agreed_verdict(model) == (verdict, reason)
    assert [point.verdict for point in _series(model)] == points, (
        f"log {shape} put the verdicts on the wrong nights"
    )
    if verdict is None:
        assert int(_get(model, "exit_code")) == Verdict.exit_code(Verdict.ERROR) == 3
        assert _get(_get(model, "completeness"), "complete") is False
        assert _missing_verdict_sentence(model) is not None, (
            f"log {shape} produced no verdict and the completeness strip does not "
            f"name the missing {EVENT_VERDICT} record"
        )


def test_a_run_that_died_before_deciding_is_not_reported_as_last_nights_verdict(
    tmp_path: Path,
) -> None:
    """C19's named first failure, and the defect shipped in 0.1.1.

    The log is ``C V C``: last night compared and decided, tonight compared and
    the process died before the verdict record was written. ``from_evidence``
    keeps the last comparison and the last verdict in two *independent* last-wins
    variables, so tonight's comparison arrives beside last night's GO and the two
    are printed as one run. The document renders a clean GO, exit code 0, and
    ``completeness.complete is True`` -- a run that decided nothing, reported as a
    decision, to a pipeline that reads only the exit status.

    The fix is one line of reduction: clear the held verdict on every comparison
    record, so a verdict can only ever describe the comparison it followed.
    """
    scenario = _scenario(tmp_path / "died-before-deciding", verdict=Verdict.NO_GO)
    log = _log_of(
        scenario,
        "evidence-cvc.jsonl",
        _older_comparison(scenario, tag="last-night"),
        _older_verdict(tag="last-night", verdict=Verdict.GO),
        _headline_comparison(scenario),
    )
    model = _model_from(log)

    assert _get(model, "verdict") is None, (
        "last night's verdict was reported as tonight's, for a run that decided "
        "nothing at all"
    )
    assert _get(model, "reason") is None
    assert _get(model, "decided_by") is None
    assert int(_get(model, "exit_code")) == Verdict.exit_code(Verdict.ERROR) == 3, (
        "cli.py:435 derives the exit code from the verdict alone, so a pipeline "
        "reads this run as green"
    )
    assert _get(_get(model, "completeness"), "complete") is False
    assert _missing_verdict_sentence(model) is not None, (
        f"the completeness strip does not name the absent {EVENT_VERDICT} record, "
        f"so the one place the document could disclose this says nothing"
    )
    assert _agreed_verdict(model) == (None, None)
    assert [point.verdict for point in _series(model)] == [Verdict.GO, None]

    html = _html(model)
    assert "NO VERDICT" in _parse(html).text
    assert _older_reason("last-night") not in html, (
        "last night's decision sentence is in tonight's document"
    )


def test_a_run_decided_twice_is_reported_as_the_decision_the_log_ends_on(
    tmp_path: Path,
) -> None:
    """Row eight, the row that says "updates" and not "closes the most recent open".

    A close-once rule is the tempting middle position: it fixes the crashed night
    and looks conservative. It is not conservative here. On ``C V1 V2`` it drops
    V2 and leaves the point reading GO, while the headline reduction takes the
    last verdict record unconditionally and prints NO-GO -- so the banner and the
    right-hand end of the timeline contradict each other about tonight, which is
    the failure the chunk exists to make impossible.

    A suite that cannot separate the two rules has not tested what was decided, so
    this asserts the second verdict on the point *and* the first one nowhere in
    the document.
    """
    scenario = _scenario(tmp_path / "decided-twice", verdict=Verdict.NO_GO)
    log = _log_of(
        scenario,
        "evidence-cv1v2.jsonl",
        _headline_comparison(scenario),
        _headline_verdict(scenario, Verdict.GO, reason=FIRST_VERDICT_REASON),
        _headline_verdict(
            scenario, Verdict.NO_GO, reason=SECOND_VERDICT_REASON, ts=TS_VERDICT_AGAIN
        ),
    )
    model = _model_from(log)
    series = _series(model)

    assert len(series) == 1, f"one comparison, {len(series)} point(s): a verdict opened one"
    assert series[0].verdict == Verdict.NO_GO, (
        "the point kept the first of the two verdicts, which is what a close-once "
        "rule does and what the headline never does"
    )
    assert _agreed_verdict(model) == (Verdict.NO_GO, SECOND_VERDICT_REASON)
    assert FIRST_VERDICT_REASON not in _html(model)


def test_one_crashed_night_in_the_middle_of_a_log_moves_no_later_verdict(
    tmp_path: Path,
) -> None:
    """The Edges row, through the report rather than through ``read_series``.

    Four nights, the second of them crashed between its comparison and its
    verdict. Under first-in-first-out night two takes night three's NO-GO, night
    three takes night four's REVIEW, and night four -- the run the banner reports
    on -- is left with nothing, so the banner and the timeline disagree about
    tonight *and* two earlier nights are relabelled. The shift is cumulative and
    the log only ever grows, so it is permanent; a two-night log moves one verdict
    and reads as a mispairing rather than as a drift.
    """
    scenario = _scenario(tmp_path / "crashed-midlog", verdict=Verdict.REVIEW)
    log = _log_of(
        scenario,
        "evidence-midlog.jsonl",
        _older_comparison(scenario, tag="one"),
        _older_verdict(tag="one", verdict=Verdict.GO),
        _older_comparison(scenario, tag="two"),
        _older_comparison(scenario, tag="three"),
        _older_verdict(tag="three", verdict=Verdict.NO_GO),
        _headline_comparison(scenario),
        _headline_verdict(scenario, Verdict.REVIEW, reason=SECOND_VERDICT_REASON),
    )
    model = _model_from(log)
    series = _series(model)

    assert [point.candidate_model for point in series] == [
        f"{EARLIER_CANDIDATE_MODEL}-one",
        f"{EARLIER_CANDIDATE_MODEL}-two",
        f"{EARLIER_CANDIDATE_MODEL}-three",
        CANDIDATE_MODEL,
    ]
    assert [point.verdict for point in series] == [
        Verdict.GO,
        None,
        Verdict.NO_GO,
        Verdict.REVIEW,
    ]
    assert [point.reason for point in series] == [
        _older_reason("one"),
        None,
        _older_reason("three"),
        SECOND_VERDICT_REASON,
    ]
    assert _agreed_verdict(model) == (Verdict.REVIEW, SECOND_VERDICT_REASON)


# -- the headline half of ``is_demo``, which the series cannot reach ---------- #


def _log_calling_a_scripted_run_real(scenario: Scenario, side: str, real: str) -> Path:
    """``scenario``'s log with one side's *payload* adapter rewritten to a real name.

    The run artifact on disk is untouched and still records ``Fake*``. This is the
    one shape that separates the two readings of "was this run scripted":
    :func:`_run_summary` prefers ``run.header.adapter`` off the artifact, while a
    ``RunPoint`` reads the payload and nothing else.
    """
    payload = json.loads(json.dumps(scenario.comparison))
    payload[side]["adapter"] = real
    payload[side]["adapters"] = [real]
    return _write_evidence(
        scenario.root / f"evidence-scripted-{side}.jsonl",
        [
            _record(EVENT_COMPARISON, payload, TS_COMPARISON),
            _record(EVENT_VERDICT, scenario.verdict or {}, TS_VERDICT),
        ],
    )


@pytest.mark.parametrize(
    ("side", "real"),
    [("baseline", "AnthropicAdapter"), ("candidate", "OpenAICompatAdapter")],
)
def test_a_payload_that_calls_a_scripted_run_real_is_still_banded(
    tmp_path: Path, side: str, real: str
) -> None:
    """The two disjuncts of ``is_demo`` that no other test in this file can reach.

    ``series[-1]`` *is* the headline run on every log ``from_evidence`` produces,
    so on every fixture in this file the first two terms of ``is_demo`` are
    redundant with the third and a reader that dropped them stays green. They are
    not redundant in the case they exist for. The two halves read different files:
    ``_run_summary`` prefers ``run.header.adapter`` from the artifact on disk,
    while the series reads the comparison payload and never opens an artifact. A
    payload that records a real adapter over an artifact that records
    ``FakeScriptedAdapter`` is caught by the headline and invisible to the series.

    Which is not a hypothetical shape: it is a hand-edited log, a payload written
    by an older writer, or a run wired up by hand -- and §5.3's whole claim is
    that a clean-looking report cannot be obtained from scripted models. A mutant
    spelling ``is_fake`` as ``self.adapter == _FAKE_PREFIX`` survived C3's entire
    suite for want of this test.
    """
    scenario = _scenario(
        tmp_path / f"scripted-{side}", **{f"{side}_adapter": "FakeScriptedAdapter"}
    )
    model = _model_from(_log_calling_a_scripted_run_real(scenario, side, real))

    for point in _series(model):
        assert not point.adapter_baseline.startswith("Fake"), point.adapter_baseline
        assert not point.adapter_candidate.startswith("Fake"), point.adapter_candidate
    assert _get(_get(model, side), "is_fake") is True, (
        f"the {side} summary read its adapter from the payload, which is the one "
        f"place this run does not say it was scripted"
    )
    assert _get(model, "is_demo") is True, (
        "every point of the series names a real adapter, so this report is banded "
        "by the headline's own sides or it is not banded at all"
    )
    document = _parse(_html(model))
    for marker in FAKE_BAND_MARKERS:
        assert marker.lower() in document.text.lower(), (
            f"band marker {marker!r} is missing from a document whose {side} run "
            f"was produced by a scripted adapter"
        )
# 19. The interval bar. Plan C12, lines 1148-1218.
#
# Written from the chunk contract alone. Nothing below was derived by running
# ``interval_bar_svg``; the projection is the one sentence the contract states
# ("the x-axis maps [0.0, 1.0] to [PAD, width - PAD] linearly"), written once as
# :func:`_mapped_x` and used everywhere a number is expected.
#
# Two rules govern this whole section, both from the contract's own reviewer
# note -- "a picture whose accessible text is right and whose geometry is wrong
# passes a lazy test":
#
#   * the geometry tests never read the ``<title>``;
#   * the title test never reads a coordinate.
#
# Neither may stand in for the other, so a bar whose title says 0.72 while its
# point sits at 0.27 fails a test in each half rather than passing both.
#
# The SVG is parsed rather than matched. ``xml.etree.ElementTree`` is stdlib and
# an assertion on a parsed attribute survives a whitespace or attribute-order
# change that a regex does not -- and, unlike a substring search, it can tell an
# element that exists from an em dash that merely mentions one. It is imported
# inside :class:`_Svg` rather than at the top of the file so that this section
# appends cleanly beside the other chunks landing in the same place.
# --------------------------------------------------------------------------- #


#: Distinctive and mutually distinct, so that an element found by its
#: ``data-value`` is the element that value belongs to and not a coincidence.
#: The floor deliberately sits *above* the upper end of the interval, which is
#: the verdict-bearing arrangement the contract calls the dangerous one to get
#: wrong.
BAR_RATE = 0.72
BAR_INTERVAL = (0.61, 0.83)
BAR_FLOOR = 0.9
BAR_WIDTH = 480
BAR_HEIGHT = 44

#: Every combination of the three recorded-or-not values, not only the four rows
#: the contract's table spells out. Three independent absences make eight states,
#: and the three the table leaves implicit are where a weakened guard hides: with
#: the all-missing branch written as ``rate is None and interval is None``, the
#: "floor only" row silently discards a floor that *was* recorded and paints an em
#: dash over it, while every parametrised test here still passes -- an em dash is a
#: legal picture. The keys are the keyword-only parameters verbatim.
BAR_VARIANTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("all recorded", {"rate": BAR_RATE, "interval": BAR_INTERVAL, "floor": BAR_FLOOR}),
    ("no interval", {"rate": BAR_RATE, "interval": None, "floor": BAR_FLOOR}),
    ("no rate", {"rate": None, "interval": BAR_INTERVAL, "floor": BAR_FLOOR}),
    ("no floor", {"rate": BAR_RATE, "interval": BAR_INTERVAL, "floor": None}),
    ("floor only", {"rate": None, "interval": None, "floor": BAR_FLOOR}),
    ("rate only", {"rate": BAR_RATE, "interval": None, "floor": None}),
    ("interval only", {"rate": None, "interval": BAR_INTERVAL, "floor": None}),
    ("nothing recorded", {"rate": None, "interval": None, "floor": None}),
)

BAR_VARIANT_IDS = tuple(name for name, _ in BAR_VARIANTS)
BAR_VARIANT_CASES = tuple(kwargs for _, kwargs in BAR_VARIANTS)

#: Elements that put ink on the canvas. ``text`` is excluded: the all-missing row
#: is *required* to be a ``<text>``, so counting it as a mark would make "nothing
#: else is drawn" unsatisfiable.
_SVG_MARKS = frozenset({"rect", "line", "circle", "ellipse", "path", "polyline", "polygon"})

#: What the reader is allowed to see besides marks and text: structure and
#: accessible text. Anything outside this union in the all-missing row is the
#: "and nothing else" clause being broken.
_SVG_STRUCTURE = frozenset({"g", "title", "desc", "defs", "metadata"})

#: Marks whose ink lies along a path rather than inside one. A ``fill`` on a
#: ``<line>`` paints nothing at all, so ``fill="#000" stroke="none"`` is an
#: invisible line and not a black one; these are checked for a stroke alone.
_STROKE_ONLY_MARKS = frozenset({"line", "polyline"})

#: Paint values that put no ink down. ``""`` covers the attribute being absent,
#: which is a failure here rather than a fallback to the SVG default: the contract
#: says "presentation via inline ``fill``/``stroke`` attributes", so a mark relying
#: on a user-agent default is out of contract even where it happens to be visible.
_INVISIBLE_PAINTS = frozenset({"", "none", "transparent"})

_BAR_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%?)")


def _bar_tag(element: Any) -> str:
    """The local name, so a document with ``xmlns`` reads the same as one without."""
    return str(element.tag).rsplit("}", 1)[-1]


def _bar_attrs(element: Any) -> dict[str, str]:
    return {str(name).rsplit("}", 1)[-1]: value for name, value in element.attrib.items()}


def _six_places(value: float) -> str:
    """The contract's ``data-value`` format: the unmapped float, six places."""
    return f"{value:.6f}"


class _Svg:
    """One parsed ``interval_bar_svg`` return value, namespaces stripped.

    The constructor is itself an assertion: the contract says the function
    returns *one* ``<svg>`` element, and a well-formedness failure or a second
    root element is a finding rather than a parse error the reader has to
    decode.
    """

    def __init__(self, markup: str) -> None:
        from xml.etree import ElementTree

        assert isinstance(markup, str), (
            f"interval_bar_svg must return a string; got {type(markup).__name__}"
        )
        self.markup = markup
        stripped = markup.strip()
        assert stripped.startswith("<svg"), (
            f"the contract requires one <svg> element as the whole return value; "
            f"it begins {stripped[:60]!r}"
        )
        try:
            root = ElementTree.fromstring(stripped)
        except ElementTree.ParseError as exc:
            raise AssertionError(
                f"interval_bar_svg must return one well-formed <svg> element; "
                f"parsing raised {exc}. The markup was {stripped[:400]!r}"
            ) from exc
        assert _bar_tag(root) == "svg", f"the root element is <{_bar_tag(root)}>, not <svg>"

        self.root = root
        self.root_attrs = _bar_attrs(root)
        self.elements: list[dict[str, Any]] = []
        for element in root.iter():
            if element is root:
                continue
            self.elements.append(
                {
                    "tag": _bar_tag(element),
                    "attrs": _bar_attrs(element),
                    "raw": dict(element.attrib),
                    "text": "".join(element.itertext()),
                }
            )

    @property
    def marks(self) -> list[dict[str, Any]]:
        return [one for one in self.elements if one["tag"] in _SVG_MARKS]

    @property
    def texts(self) -> list[dict[str, Any]]:
        return [one for one in self.elements if one["tag"] == "text"]

    @property
    def title(self) -> str:
        titles = [one["text"] for one in self.elements if one["tag"] == "title"]
        assert titles, (
            "the contract requires the <svg> to carry a <title>: it is what a "
            "screen reader announces and what a blind test reads. Elements "
            f"present: {sorted({one['tag'] for one in self.elements})}"
        )
        return titles[0].strip()

    @property
    def data_values(self) -> list[str]:
        return [
            one["attrs"]["data-value"] for one in self.elements if "data-value" in one["attrs"]
        ]

    def classed(self, name: str) -> list[dict[str, Any]]:
        return [one for one in self.elements if name in one["attrs"].get("class", "").split()]

    def valued(self, value: float) -> list[dict[str, Any]]:
        wanted = _six_places(value)
        return [one for one in self.elements if one["attrs"].get("data-value") == wanted]


def _interval_bar(**kwargs: Any) -> str:
    return _get(_module(), "interval_bar_svg")(**kwargs)


def _bar(**kwargs: Any) -> _Svg:
    kwargs.setdefault("width", BAR_WIDTH)
    kwargs.setdefault("height", BAR_HEIGHT)
    return _Svg(_interval_bar(**kwargs))


def _bar_pad() -> int:
    """``report.INTERVAL_BAR_PAD``, imported rather than hard-coded.

    A test that writes ``8`` where the module writes a name cannot tell a
    changed constant from a broken projection: both surface as the same wrong
    number. Exactly one test below pins the value, and it is the only one.
    """
    pad = _get(_module(), "INTERVAL_BAR_PAD")
    assert isinstance(pad, int), f"INTERVAL_BAR_PAD must be an int; got {pad!r}"
    return pad


def _mapped_x(value: float, width: int = BAR_WIDTH) -> float:
    """The contract's projection: ``[0.0, 1.0] -> [PAD, width - PAD]``, linearly."""
    pad = _bar_pad()
    return pad + value * (width - 2 * pad)


def _x_candidates(element: Mapping[str, Any]) -> list[float]:
    """Every x this element could reasonably be said to sit at.

    A ``<line>`` marking a position on a horizontal axis is vertical, so its
    ``x1`` and ``x2`` coincide -- asserted here, because a *slanted* floor line
    is a wrong picture whose ``x1`` alone would still read correctly. A
    ``<rect>`` used as a tick may be positioned by its left edge or centred on
    the value; the contract does not say which, so both are offered and the
    caller requires one of them to land.
    """
    attrs = element["attrs"]
    candidates: list[float] = []
    if "x1" in attrs and "x2" in attrs:
        x1, x2 = float(attrs["x1"]), float(attrs["x2"])
        assert abs(x1 - x2) <= 0.5, (
            f"a <{element['tag']}> marking a position on the rate axis must be "
            f"vertical; x1={x1} and x2={x2} differ"
        )
        candidates.append(x1)
    if "x" in attrs:
        left = float(attrs["x"])
        candidates.append(left)
        if "width" in attrs:
            candidates.append(left + float(attrs["width"]) / 2.0)
    assert candidates, (
        f"a <{element['tag']}> carrying data-value {attrs.get('data-value')!r} has "
        f"no x or x1/x2 position: its attributes are {sorted(attrs)}"
    )
    return candidates


def _assert_sits_at(element: Mapping[str, Any], expected: float, what: str) -> None:
    candidates = _x_candidates(element)
    assert min(abs(one - expected) for one in candidates) <= 1.0, (
        f"{what} must sit at x={expected:.3f} (PAD + value * (width - 2*PAD)); the "
        f"element is at {candidates}, more than a pixel away. A bar whose numbers "
        f"are right and whose geometry is wrong is the failure the contract calls "
        f"the dangerous one, because the position of the band relative to the "
        f"floor *is* the verdict."
    )


def _one_element(elements: Sequence[Mapping[str, Any]], what: str) -> Mapping[str, Any]:
    assert len(elements) == 1, f"expected exactly one {what}; found {len(elements)}"
    return elements[0]


def _span(element: Mapping[str, Any], axis: str) -> tuple[float, float]:
    """The stretch of canvas this element occupies along ``axis``, as (low, high).

    Written for the shapes the contract permits -- a ``<rect>``, a ``<line>`` and
    the em dash's ``<text>``. Anything else is an assertion failure rather than a
    silent pass, because an unmeasurable mark is exactly the mark a bounds check
    would otherwise wave through.
    """
    attrs = element["attrs"]
    one, two = f"{axis}1", f"{axis}2"
    if one in attrs and two in attrs:
        low, high = sorted((float(attrs[one]), float(attrs[two])))
        return low, high
    if axis in attrs:
        start = float(attrs[axis])
        extent = "width" if axis == "x" else "height"
        return start, start + float(attrs.get(extent, 0.0))
    raise AssertionError(
        f"a <{element['tag']}> carrying {attrs.get('data-value')!r} states no "
        f"{axis} position: its attributes are {sorted(attrs)}. A mark whose "
        f"geometry cannot be read cannot be checked for being on the canvas."
    )


def _paints(element: Mapping[str, Any]) -> bool:
    """Whether this mark puts ink on the canvas at all.

    Invisible reads as absent, and absent is a different verdict: a floor line
    with ``stroke="none"`` says the run had no floor to clear, which is the one
    reading the contract forbids outright.
    """
    attrs = element["attrs"]
    stroke = attrs.get("stroke", "").strip().lower()
    strokes = stroke not in _INVISIBLE_PAINTS and float(attrs.get("stroke-width", 1) or 0) > 0
    if element["tag"] in _STROKE_ONLY_MARKS:
        return strokes
    return strokes or attrs.get("fill", "").strip().lower() not in _INVISIBLE_PAINTS


def _bar_numbers_stated(text: str) -> set[float]:
    """Every rate the words could be stating, read as a fraction or as a percent.

    The contract fixes that the title "states the same numbers in words" without
    fixing the notation, so ``0.72``, ``72%`` and ``72.0%`` are all accepted and
    ``0.7`` is not: a title rounded past the second place is no longer stating
    the number the picture was drawn from.
    """
    values: set[float] = set()
    for digits, percent in _BAR_NUMBER_RE.findall(text):
        value = float(digits)
        if percent:
            values.add(value / 100.0)
            continue
        values.add(value)
        if value > 1.0:
            values.add(value / 100.0)
    return values


def _assert_stated(text: str, value: float, what: str) -> None:
    stated = _bar_numbers_stated(text)
    assert any(abs(one - value) <= 0.005 for one in stated), (
        f"the accessible title must state {what} ({value}); it reads {text!r}, "
        f"whose numbers are {sorted(stated)}"
    )


def _bar_numbers_in_order(text: str) -> list[set[float]]:
    """The same readings as :func:`_bar_numbers_stated`, but kept in reading order.

    A set cannot tell "interval 0.61 to 0.83" from "interval 0.83 to 0.61", and the
    second is a different picture stated in the same words -- the one place the
    accessible text can lie while every number in it is correct.
    """
    readings: list[set[float]] = []
    for digits, percent in _BAR_NUMBER_RE.findall(text):
        value = float(digits)
        if percent:
            readings.append({value / 100.0})
            continue
        candidates = {value}
        if value > 1.0:
            candidates.add(value / 100.0)
        readings.append(candidates)
    return readings


def _first_stating(readings: Sequence[set[float]], value: float, text: str, what: str) -> int:
    for index, candidates in enumerate(readings):
        if any(abs(one - value) <= 0.005 for one in candidates):
            return index
    raise AssertionError(f"the title states no number that could be {what} ({value}): {text!r}")


def _minimal_document(svg: str) -> str:
    """The smallest complete page that can hold the bar, and nothing that fetches."""
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        "<title>interval bar</title>\n</head>\n<body>\n"
        f"{svg}\n"
        "</body>\n</html>\n"
    )


# -- the constant, pinned in exactly one place ------------------------------- #


def test_the_interval_bar_pad_is_the_eight_pixels_the_contract_names() -> None:
    """C12: "the x-axis maps [0.0, 1.0] to [PAD, width - PAD] linearly, ``PAD = 8``".

    The one test allowed to write the literal. Every other test imports the
    constant, so changing it moves exactly one failure here rather than
    scattering identical arithmetic failures across the section, and a
    projection broken *without* touching the constant fails there and not here.
    """
    pad = _bar_pad()
    assert pad == 8, f"the contract fixes PAD at 8; INTERVAL_BAR_PAD is {pad}"
    assert 2 * pad < BAR_WIDTH, "the padding must leave a drawable range"


# -- geometry. Nothing in this block reads the <title>. ---------------------- #


@pytest.mark.parametrize(
    ("floor", "width"),
    [
        (0.9, 480),
        (0.87, 480),
        (0.5, 480),
        (0.0, 480),
        (1.0, 480),
        (0.9, 300),
        (0.9, 1000),
        (0.87, 137),
    ],
)
def test_the_interval_bar_places_the_floor_line_at_the_same_fraction_of_the_width_as_the_floor_is_of_the_range(  # noqa: E501
    floor: float, width: int
) -> None:
    """C12's first named test: ``x == PAD + floor * (width - 2*PAD)``, within a pixel.

    Several widths, because a projection that ignored ``width`` and one that used
    it agree at the default and nowhere else. Several floors, because a
    projection that ignored ``floor`` agrees with the truth at exactly one of
    them.

    ``floor=0.0`` is in the table on purpose: a *recorded* floor of zero is a
    real floor and must be drawn at ``PAD``. It is the case the next test is the
    other half of -- an absent floor must not produce this same picture.

    No assertion here touches the ``<title>``. The title's job is checked
    separately, and the two are kept apart so that neither can excuse the other.
    """
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=floor, width=width)
    line = _one_element(bar.classed("floor"), 'element with class="floor"')
    _assert_sits_at(line, _mapped_x(floor, width), "the floor line")
    assert line["attrs"].get("data-value") == _six_places(floor), (
        "the floor line must carry its unmapped value as data-value, so a test "
        "can watch the model's number reach the drawing without re-deriving the "
        f"projection; it carries {line['attrs'].get('data-value')!r}"
    )


def test_an_interval_bar_with_no_recorded_floor_says_so_rather_than_drawing_a_line_at_zero() -> None:  # noqa: E501
    """C12's second named test, and the missing-value table's third row.

    "no floor line, **and** an ``<title>`` saying the floor was not recorded --
    an absent rule must not read as a floor of zero". Four separate readings,
    because each catches a different way of getting it wrong:

    * no element claims the ``floor`` class -- the line is gone, not merely moved;
    * nothing carries ``data-value="0.000000"`` -- the "Must not" list's third
      clause, which catches a line drawn at zero under some other class;
    * the title says so in words, and states no zero;
    * the band and the point are still drawn where they belong -- without which
      the whole test would pass against a function returning ``<svg></svg>``.

    The contrast render with an explicitly recorded ``floor=0.0`` is the control:
    absence and a recorded zero must not produce the same picture, and a
    conditional that treated ``None`` as ``0.0`` would make them identical.
    """
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=None)

    assert bar.classed("floor") == [], (
        "an absent floor must draw no floor line; found "
        f"{[one['tag'] for one in bar.classed('floor')]}"
    )
    assert _six_places(0.0) not in bar.data_values, (
        'the bar carries data-value="0.000000" for a floor that was never '
        "recorded, which is the reading the contract forbids: an absent rule must "
        "not read as a floor of zero"
    )

    title = bar.title.lower()
    assert "floor" in title, (
        f"the title must name the floor it is reporting on; it reads {bar.title!r}"
    )
    sayings = (
        "not recorded",
        "no recorded",
        "not been recorded",
        "none recorded",
        "unrecorded",
        "was not",
        "no floor",
        "not available",
        "not set",
    )
    assert any(saying in title for saying in sayings), (
        f"the title must say the floor was not recorded; it reads {bar.title!r}. "
        f"Any of {sayings} satisfies this test -- the contract fixes the fact, not "
        f"the wording."
    )
    assert not any(abs(one) <= 0.005 for one in _bar_numbers_stated(bar.title)), (
        f"the title states a number indistinguishable from a floor of zero: {bar.title!r}"
    )

    band = _one_element(bar.valued(BAR_INTERVAL[0]), "band carrying the interval's lower end")
    _assert_sits_at(band, _mapped_x(BAR_INTERVAL[0]), "the interval band")
    point = _one_element(bar.valued(BAR_RATE), "point element carrying the rate")
    _assert_sits_at(point, _mapped_x(BAR_RATE), "the point estimate")

    recorded_zero = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=0.0)
    zero_line = _one_element(recorded_zero.classed("floor"), 'element with class="floor"')
    _assert_sits_at(zero_line, _mapped_x(0.0), "a recorded floor of zero")


def test_the_interval_band_spans_the_mapped_ends_of_the_interval() -> None:
    """C12: "the interval band is a ``<rect>`` whose ``x`` is the mapped
    ``interval[0]`` and whose ``width`` is the mapped span".

    The band is found by its ``data-value`` -- the contract's stated seam -- and
    only then measured against the projection, so the test learns nothing from
    the element's position about which element it is.
    """
    lower, upper = BAR_INTERVAL
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    band = _one_element(bar.valued(lower), "band carrying the interval's lower end")
    assert band["tag"] == "rect", f"the contract fixes the band as a <rect>; got <{band['tag']}>"

    left = float(band["attrs"]["x"])
    assert abs(left - _mapped_x(lower)) <= 1.0, (
        f"the band starts at x={left}, not at the mapped lower end {_mapped_x(lower):.3f}"
    )
    span = float(band["attrs"]["width"])
    expected_span = _mapped_x(upper) - _mapped_x(lower)
    assert abs(span - expected_span) <= 1.0, (
        f"the band is {span}px wide; the mapped span of {BAR_INTERVAL} is "
        f"{expected_span:.3f}px. A band of the right width from the wrong origin, "
        f"and one of the wrong width from the right origin, both put the upper end "
        f"somewhere the numbers do not say it is."
    )
    assert abs((left + span) - _mapped_x(upper)) <= 1.0, (
        f"the band ends at x={left + span}, not at the mapped upper end {_mapped_x(upper):.3f}"
    )


def test_the_upper_end_of_the_interval_reaches_the_drawing_as_a_number() -> None:
    """C12: "every element carries ``data-value`` with the unmapped float to 6 places".

    A band has two unmapped floats and one ``data-value``. The contract does not
    say where the second goes, so this test only requires that it goes
    *somewhere* an attribute can be read from, rather than into a pixel width a
    reader would have to invert the projection to recover.
    """
    upper = _six_places(BAR_INTERVAL[1])
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    carried = {
        (one["tag"], name, value)
        for one in bar.elements
        for name, value in one["attrs"].items()
        if value == upper
    }
    assert carried, (
        f"no attribute anywhere in the bar carries the interval's upper end {upper}. "
        f"It is a number the model measured, and a reader who cannot find it has to "
        f"invert the projection from a pixel width to get it back."
    )


def test_the_point_estimate_sits_at_the_mapped_rate() -> None:
    """C12: "the point estimate is a ``<line>`` or ``<rect>`` at the mapped ``rate``"."""
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    point = _one_element(bar.valued(BAR_RATE), "point element carrying the rate")
    assert point["tag"] in {"line", "rect"}, (
        f"the contract fixes the point estimate as a <line> or a <rect>; got <{point['tag']}>"
    )
    _assert_sits_at(point, _mapped_x(BAR_RATE), "the point estimate")


def test_each_interval_bar_element_carries_its_unmapped_value_to_six_places() -> None:
    """C12's "Must not": "Round the underlying value before putting it in ``data-value``".

    The values here are chosen so that a pre-rounding implementation is visibly
    different rather than coincidentally equal: ``round(0.987654321, 3)`` formats
    as ``0.988000`` and ``round(0.123456789, 3)`` as ``0.123000``, neither of
    which is the six-place rendering of the number that was passed in.
    """
    rate = 0.987654321
    lower = 0.123456789
    upper = 0.876543211
    floor = 0.909090909
    bar = _bar(rate=rate, interval=(lower, upper), floor=floor)

    for value, what in (
        (rate, "the rate"),
        (lower, "the interval's lower end"),
        (floor, "the floor"),
    ):
        assert _six_places(value) in bar.data_values, (
            f"{what} reaches the drawing as {_six_places(value)!r}; the bar carries "
            f"{sorted(set(bar.data_values))}"
        )
    assert "0.988000" not in bar.data_values, "the rate was rounded before it was written out"
    assert "0.123000" not in bar.data_values, (
        "the interval's lower end was rounded before it was written out"
    )
    for written in bar.data_values:
        assert re.fullmatch(r"-?\d+\.\d{6}", written), (
            f"data-value must be a plain float to exactly six decimal places; got {written!r}"
        )


def test_the_interval_bar_honours_the_width_and_height_it_was_given() -> None:
    """The canvas the projection is computed against must be the canvas that is drawn.

    A bar that projects into a 300px width and then declares ``width="480"`` puts
    every mark at the wrong fraction of the picture the reader sees, and every
    coordinate assertion above would still pass.

    Both halves are required, not either. Written with ``or`` -- as this test was
    -- one half may be wrong and the test still passes: a ``width="480"`` on a
    viewBox of 300 scales the whole picture by 1.6, every mark keeps its fraction
    of the drawing, and every coordinate assertion in this file still holds while
    the reader sees a bar the projection was never computed for.
    """
    width, height = 300, 20
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR, width=width, height=height)
    box = bar.root_attrs.get("viewBox", "")
    declared = bar.root_attrs.get("width")
    declared_height = bar.root_attrs.get("height")
    assert declared == str(width), (
        f"the svg must declare the width it was drawn for; it carries width={declared!r}"
    )
    assert declared_height == str(height), (
        f"the svg must declare the height it was drawn for; it carries "
        f"height={declared_height!r}"
    )
    assert box == f"0 0 {width} {height}", (
        f"the user-space box the marks were projected into must be the box the svg "
        f"declares; it carries viewBox={box!r}, not {f'0 0 {width} {height}'!r}"
    )
    aspect = bar.root_attrs.get("preserveAspectRatio", "")
    assert aspect and not aspect.strip().lower().startswith("none"), (
        f"the bar carries preserveAspectRatio={aspect!r}. With 'none' the two axes "
        f"scale independently, so a container of any other shape stretches the "
        f"picture and the mapped positions stop meaning what the numbers say."
    )
    _assert_sits_at(
        _one_element(bar.classed("floor"), 'element with class="floor"'),
        _mapped_x(BAR_FLOOR, width),
        "the floor line on a 300-unit canvas",
    )


@pytest.mark.parametrize(
    ("rate", "edge"),
    [
        (-0.25, "low"),
        (-1.0, "low"),
        (0.0, "low"),
        (1.0, "high"),
        (1.4, "high"),
        (3.0, "high"),
    ],
)
def test_a_rate_outside_the_unit_interval_is_pinned_to_the_edge_it_ran_past(
    rate: float, edge: str
) -> None:
    """C12's reviewer note: "an SVG that draws off-canvas is invisible rather than
    wrong-looking".

    The mark must sit *at* the edge, not merely somewhere between the edges. A
    range check alone -- which is what this test used to be -- is satisfied by an
    ``abs()``, which puts ``rate=-0.25`` at x=124, and by a mirror, which puts it
    at x=356. Neither is a clamp; both are plausible wrong positions in the middle
    of the picture, and a reader has no way to tell one from a real rate of 0.25.

    The clamp is on the *drawing*, not on the number: ``data-value`` must still
    carry what was passed in, because the contract's other rule is that the
    unmapped float reaches the drawing unaltered. So an out-of-range rate stays
    visible at the edge and readable as itself, rather than silently absent.
    """
    pad = _bar_pad()
    expected = pad if edge == "low" else BAR_WIDTH - pad
    bar = _bar(rate=rate, interval=None, floor=BAR_FLOOR)
    point = _one_element(bar.valued(rate), f"point element carrying rate={rate}")
    for candidate in _x_candidates(point):
        assert abs(candidate - expected) <= 0.01, (
            f"rate={rate} is off the [0, 1] axis, so it must be pinned to the "
            f"{edge} edge at x={expected}; it was drawn at x={candidate}. Anywhere "
            f"else on the canvas is a wrong position a reader cannot tell from a "
            f"right one."
        )
    assert point["attrs"].get("data-value") == _six_places(rate), (
        f"the clamp is on the geometry, not on the number: data-value must still "
        f"read {_six_places(rate)}, so the disagreement between the pinned mark and "
        f"the out-of-range value is legible rather than erased"
    )


@pytest.mark.parametrize("height", [44, 20, 120])
@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_every_interval_bar_mark_is_visible_inside_the_canvas_it_declares(
    variant: dict[str, Any], height: int
) -> None:
    """The vertical axis, which nothing else in this section touches.

    The contract states the projection on x and says nothing about y, so every
    assertion above is satisfied by a bar whose band sits at ``y = height * 3.4``,
    or has ``height="0"``, or whose floor is stroked ``none``. All three render as
    an empty box with correct numbers in the markup, and the contract's reviewer
    note is explicit about why that is the bad kind of wrong: "an SVG that draws
    off-canvas is invisible rather than wrong-looking". Invisible reads as absent,
    and for the floor line in particular absent is a *different verdict*.

    Three properties, each killing a different way of drawing nothing: the mark
    lies within the declared box, it has a non-zero rendered extent, and it puts
    ink down. Several heights, because ``y`` values that are fractions of the
    height and ``y`` values that are constants agree at exactly one of them.
    """
    bar = _bar(**variant, height=height)
    recorded = [name for name, value in variant.items() if value is not None]
    if recorded:
        assert bar.marks, f"{recorded} were recorded and nothing was drawn for them"

    for mark in bar.marks + bar.texts:
        tag, values = mark["tag"], mark["attrs"].get("data-value")
        top, bottom = _span(mark, "y")
        assert top >= -0.5 and bottom <= height + 0.5, (
            f"the <{tag}> carrying {values!r} spans y={top} to y={bottom}, outside "
            f"the declared canvas [0, {height}]. It is clipped away rather than "
            f"drawn wrong, and a mark nobody can see reads as a mark nobody drew."
        )
        left, right = _span(mark, "x")
        assert left >= -0.5 and right <= BAR_WIDTH + 0.5, (
            f"the <{tag}> carrying {values!r} spans x={left} to x={right}, outside "
            f"the declared canvas [0, {BAR_WIDTH}]"
        )
        if tag == "text":
            continue
        assert _paints(mark), (
            f"the <{tag}> carrying {values!r} paints nothing: it has "
            f"stroke={mark['attrs'].get('stroke')!r} fill={mark['attrs'].get('fill')!r}. "
            f"The contract requires presentation to be carried by inline attributes, "
            f"and a mark with neither is an absence dressed as a drawing."
        )
        if tag in _STROKE_ONLY_MARKS:
            assert (bottom - top) + (right - left) > 0.5, (
                f"the <{tag}> carrying {values!r} runs from ({left}, {top}) to "
                f"({right}, {bottom}) -- a zero-length line, which renders as nothing"
            )
            continue
        assert bottom - top > 0.5 and right - left > 0.5, (
            f"the <{tag}> carrying {values!r} is {right - left} by {bottom - top}; a "
            f"shape with a zero dimension is not rendered at all by an SVG renderer"
        )


def test_the_interval_bar_names_its_parts_the_way_a_reader_will_ask_for_them() -> None:
    """The three names C14 and a stylesheet will reach for, pinned.

    The contract fixes ``class="floor"`` and leaves the band, the point and the
    interval's upper end unnamed, so the tests written to it accepted the upper
    end in *any* attribute -- under which a rename to ``data-hi`` is invisible.
    R7's reasoning about ``INTERVAL_BAR_PAD`` is the same reasoning: a seam that
    another chunk will address by name is part of the contract whether or not the
    contract wrote it down, and the moment to write it down is before the second
    caller exists.
    """
    lower, upper = BAR_INTERVAL
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)

    band = _one_element(bar.classed("interval"), 'element with class="interval"')
    assert band["attrs"].get("data-value") == _six_places(lower), (
        f"the band must carry the interval's lower end as data-value; it carries "
        f"{band['attrs'].get('data-value')!r}"
    )
    assert band["attrs"].get("data-value-upper") == _six_places(upper), (
        f"the interval's upper end belongs in data-value-upper, the name a "
        f"stylesheet and C14 will ask for; the band carries "
        f"{sorted(band['attrs'])}"
    )
    point = _one_element(bar.classed("rate"), 'element with class="rate"')
    assert point["attrs"].get("data-value") == _six_places(BAR_RATE), (
        f"the point estimate must carry the rate; it carries "
        f"{point['attrs'].get('data-value')!r}"
    )
    floor = _one_element(bar.classed("floor"), 'element with class="floor"')
    assert floor["attrs"].get("data-value") == _six_places(BAR_FLOOR), (
        f"the floor line must carry the floor; it carries "
        f"{floor['attrs'].get('data-value')!r}"
    )


@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_each_of_the_eight_recorded_states_draws_exactly_what_it_was_given(
    variant: dict[str, Any],
) -> None:
    """All eight states, each asserted against the same rule: drawn iff recorded.

    The contract's table names four of the eight, and the three it leaves out are
    where a weakened guard survives -- "floor only" in particular, where an
    all-missing branch that forgot to check the floor throws a recorded floor away
    and draws an em dash in its place.
    """
    bar = _bar(**variant)
    for name, klass, value in (
        ("rate", "rate", variant["rate"]),
        ("interval", "interval", variant["interval"]),
        ("floor", "floor", variant["floor"]),
    ):
        found = bar.classed(klass)
        if value is None:
            assert found == [], (
                f"{name} was not recorded, yet the bar draws "
                f"{[one['tag'] for one in found]} claiming class={klass!r}"
            )
            continue
        element = _one_element(found, f'element with class="{klass}"')
        expected = value[0] if name == "interval" else value
        _assert_sits_at(element, _mapped_x(expected), f"the {name}")

    if all(value is None for value in variant.values()):
        assert bar.marks == [], f"nothing was recorded; found {[one['tag'] for one in bar.marks]}"
        assert _one_element(bar.texts, "<text> element")["text"].strip() == "—"
        assert bar.data_values == []
        return

    assert bar.texts == [], (
        f"the em dash stands for a bar with nothing to say; this one recorded "
        f"{[name for name, value in variant.items() if value is not None]} and still "
        f"drew {[one['text'] for one in bar.texts]!r}. A recorded value replaced by "
        f"an em dash is the whole picture lost to one over-broad guard."
    )
    assert len(bar.marks) == sum(value is not None for value in variant.values()), (
        f"{variant} should draw one mark per recorded value; it drew "
        f"{[one['tag'] for one in bar.marks]}"
    )


def test_a_degenerate_interval_still_puts_a_band_on_the_canvas() -> None:
    """An interval of zero span is a measurement, not an absence.

    ``width="0"`` is not rendered by an SVG renderer at all, so a band computed
    straight from the projection disappears -- and a reader who sees no band reads
    "no interval was recorded", which is a different row of the contract's table.
    """
    bar = _bar(rate=0.5, interval=(0.5, 0.5), floor=None)
    band = _one_element(bar.classed("interval"), 'element with class="interval"')
    assert abs(float(band["attrs"]["x"]) - _mapped_x(0.5)) <= 1.0, (
        "the band's x is still the mapped lower end, exactly as the geometry "
        f"contract says; it is at {band['attrs']['x']}"
    )
    assert float(band["attrs"]["width"]) > 0.0, (
        f'the band is {band["attrs"]["width"]} wide, which an SVG renderer skips '
        f"entirely; a recorded interval that draws nothing is indistinguishable "
        f"from an interval that was never recorded"
    )


# -- the accessible title. Nothing in this block reads a coordinate. --------- #


def test_the_interval_bar_is_an_image_whose_title_states_the_same_numbers() -> None:
    """C12: the ``<svg>`` carries ``role="img"`` and a ``<title>`` stating the numbers.

    The other half of the pair the reviewer note is about. This test reads no
    ``x``, no ``width`` and no ``data-value``; the geometry tests read no title.
    A bar that got one of the two right passes exactly one of them.
    """
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    assert bar.root_attrs.get("role") == "img", (
        'the <svg> must carry role="img" so a screen reader announces it as a '
        f"picture with a name; its attributes are {sorted(bar.root_attrs)}"
    )
    title = bar.title
    assert title, "the <title> is empty, so the picture announces itself as nothing"
    _assert_stated(title, BAR_RATE, "the point estimate")
    _assert_stated(title, BAR_INTERVAL[0], "the interval's lower end")
    _assert_stated(title, BAR_INTERVAL[1], "the interval's upper end")
    _assert_stated(title, BAR_FLOOR, "the floor")


def test_the_interval_bar_label_reaches_the_reader() -> None:
    """The ``label`` parameter exists to be read; a label accepted and dropped is a
    caller silently getting nothing.

    The contract does not say whether the label belongs in the ``<title>`` or in
    a ``<text>``, so either satisfies this. What it may not do is vanish.
    """
    label = "candidate accuracy on the golden set"
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR, label=label)
    visible = " ".join([bar.title] + [one["text"] for one in bar.texts])
    assert label in visible, (
        f"the label {label!r} appears neither in the <title> nor in any <text>; what "
        f"the picture says is {visible!r}"
    )


def test_the_interval_bar_title_states_the_interval_low_end_first() -> None:
    """The phrase "interval 0.83 to 0.61" is a different claim in the same numbers.

    :func:`_bar_numbers_stated` collects into a *set*, so every other title
    assertion in this file is blind to the order they appear in -- and the order
    is the whole content of the phrase. This test reads the readings in sequence
    and requires the lower end to come first.
    """
    lower, upper = BAR_INTERVAL
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    title = bar.title
    readings = _bar_numbers_in_order(title)
    at_lower = _first_stating(readings, lower, title, "the interval's lower end")
    at_upper = _first_stating(readings, upper, title, "the interval's upper end")
    assert at_lower < at_upper, (
        f"the title states the interval's ends in the order {upper}, {lower}: "
        f"{title!r}. A reader takes the first as the low end, so the words describe "
        f"an interval running the wrong way while every number in them is correct."
    )


def test_the_interval_bar_title_speaks_the_notation_the_rest_of_the_document_speaks() -> None:
    """A screen-reader user must hear what the sighted reader sees.

    The document around this bar renders a pass rate as ``72.0%``, an interval as
    ``[0.6100, 0.8300]`` and the banner's floor as ``90.0%``. A title reading
    ``0.720000`` announces six zeros nobody else is shown, and six-place fractions
    are the one notation the surrounding page never uses. ``data-value`` is the
    other audience and keeps its six places -- R7 pins that, and this test asserts
    the two formats have not collapsed back into one.
    """
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    title = bar.title
    # Spelled out rather than computed through the module's own formatter: a test
    # that asks report._pct what report._pct produced would agree with any answer.
    for value, spelling in ((BAR_RATE, "72.0%"), (0.61, "61.0%"), (0.83, "83.0%"), (0.9, "90.0%")):
        assert spelling in title, (
            f"the title must state {value} as {spelling}, the notation the pass-rate "
            f"cell and the verdict banner already use; it reads {title!r}"
        )
    assert _six_places(BAR_RATE) not in title, (
        f"the title still speaks unmapped six-place fractions ({title!r}), a "
        f"notation that appears nowhere else in the document a reader is holding"
    )
    assert _six_places(BAR_RATE) in bar.data_values, (
        "data-value is the machine seam and R7 pins it at six places; changing the "
        "title's notation must not have moved it"
    )


@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_the_title_says_not_recorded_for_every_value_that_was_not(
    variant: dict[str, Any],
) -> None:
    """The contract's rule about the floor, applied to all three values.

    "An absent rule must not read as a floor of zero" is not a fact about floors.
    An absent *rate* rendered as 0.000000 says the run failed everything, and an
    absent interval rendered as 0 to 0 says the estimate was certain. Both are
    worse than saying nothing, and both were invisible to this suite.

    The phrases are imported, never spelled out here. A test that guesses at the
    wording -- as this file once did, with nine spellings of the floor phrase --
    cannot tell a deliberate rewording from a bar that started printing numbers
    for values nobody measured: it fails for both and says which for neither.
    """
    module = _module()
    bar = _bar(**variant)
    title = bar.title
    for name, phrase in (
        ("rate", _get(module, "INTERVAL_BAR_NO_RATE")),
        ("interval", _get(module, "INTERVAL_BAR_NO_INTERVAL")),
        ("floor", _get(module, "INTERVAL_BAR_NO_FLOOR")),
    ):
        if variant[name] is None:
            assert phrase in title, (
                f"{name} was not recorded, so the title must say so in the words "
                f"{phrase!r}; it reads {title!r}"
            )
        else:
            assert phrase not in title, (
                f"{name} was recorded as {variant[name]}, yet the title says "
                f"{phrase!r}: {title!r}"
            )
    if any(value is None for value in variant.values()):
        zeroish = [
            one
            for candidates in _bar_numbers_in_order(title)
            for one in candidates
            if abs(one) <= 0.005
        ]
        assert not zeroish, (
            f"the title states {zeroish}, indistinguishable from zero, for a value "
            f"nobody measured: {title!r}"
        )


def test_a_whitespace_only_label_does_not_open_the_title_with_a_bare_separator() -> None:
    """``label="  "`` is a caller with no label, spelled slightly differently.

    Taken literally it produces a title beginning ``" : "``, which a screen reader
    announces as a pause and a colon before any content -- the accessible name
    starting with punctuation that stands for a name nobody supplied.
    """
    for label in ("", "   ", "\t\n", "\x1b"):
        bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR, label=label)
        assert ":" not in bar.title, (
            f"label={label!r} carries no name, so the title must not carry a "
            f"separator for one; it reads {bar.title!r}"
        )
        assert bar.title.startswith("pass rate"), (
            f"label={label!r} produced the title {bar.title!r}, which opens with "
            f"something other than the first thing the bar has to say"
        )


def test_the_interval_bar_escapes_and_strips_a_hostile_label() -> None:
    """The label is caller-supplied text going straight between two tags.

    ``assert_self_contained`` will not catch this. ``</title><rect
    data-value="0.999999"/>`` is neither a fetching position nor a forbidden tag,
    so the scanner passes it -- and C14 injects this markup with ``| safe``, which
    is the whole reason the escaping has to be checked here rather than downstream.
    Dropping the ``&<>`` escaping and dropping the control strip both survived the
    entire suite, because no test ever handed this function a hostile label.
    """
    label = (
        '</title><rect class="floor" data-value="0.999999"/> & '
        "<script>alert(1)</script>\x1b[31m"
    )
    markup = _interval_bar(
        rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR, label=label, width=BAR_WIDTH
    )
    bar = _Svg(markup)

    assert "<script" not in markup.lower(), f"a <script> reached the markup verbatim: {markup!r}"
    assert "&lt;/title&gt;" in markup, (
        f"the label's </title> must be escaped, or it closes the accessible name "
        f"and everything after it becomes markup: {markup!r}"
    )
    assert "&amp;" in markup, f"a bare & in the label was not escaped: {markup!r}"
    assert "\x1b" not in markup, (
        f"an ESC from the label reached the document unstripped: {markup!r}. The "
        f"same strip the terminal renderer applies applies here."
    )
    assert len(bar.marks) == 3, (
        f"the label injected {len(bar.marks) - 3} extra mark(s): the bar draws "
        f"{[one['tag'] for one in bar.marks]}"
    )
    assert _six_places(0.999999) not in bar.data_values, (
        f"the label's forged data-value became a real attribute: {bar.data_values}"
    )
    assert len(bar.classed("floor")) == 1, (
        "the label forged a second element claiming to be the floor line, which is "
        "the one element in this picture that carries a verdict"
    )
    assert "alert(1)" in bar.title, (
        f"the label is escaped, not censored -- what the caller passed must still be "
        f"readable as text; the title reads {bar.title!r}"
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("where", ["rate", "floor", "interval-low", "interval-high"])
def test_a_non_finite_number_is_reported_as_not_recorded_rather_than_projected(
    bad: float, where: str
) -> None:
    """``json.loads`` accepts a bare ``NaN``, so a malformed log can produce one here.

    The arithmetic is the finding: ``min(1.0, max(0.0, nan))`` is ``0.0``, so an
    unguarded NaN rate draws at ``INTERVAL_BAR_PAD`` -- pixel-identical to a rate of
    0.0 -- with ``data-value="nan"`` beside it breaking R7's six-place format. An
    infinity clamps to 1.0 and draws a perfect score. Both are the contract's own
    "silently wrong projection": a picture stating a result nobody measured.

    The recorded-zero contrast is the same control the floor test uses. Absence and
    a measured zero must not produce the same picture, and here the two renders are
    compared as whole strings.
    """
    module = _module()
    kwargs: dict[str, Any] = {"rate": BAR_RATE, "interval": BAR_INTERVAL, "floor": BAR_FLOOR}
    klass, phrase = {
        "rate": ("rate", "INTERVAL_BAR_NO_RATE"),
        "floor": ("floor", "INTERVAL_BAR_NO_FLOOR"),
        "interval-low": ("interval", "INTERVAL_BAR_NO_INTERVAL"),
        "interval-high": ("interval", "INTERVAL_BAR_NO_INTERVAL"),
    }[where]
    if where == "rate":
        kwargs["rate"] = bad
    elif where == "floor":
        kwargs["floor"] = bad
    elif where == "interval-low":
        kwargs["interval"] = (bad, BAR_INTERVAL[1])
    else:
        kwargs["interval"] = (BAR_INTERVAL[0], bad)

    bar = _bar(**kwargs)
    assert bar.classed(klass) == [], (
        f"a {bad} {where} was projected onto the canvas as "
        f"{[one['attrs'] for one in bar.classed(klass)]}; it is not a position, and "
        f"the clamp turns it into one silently"
    )
    for written in bar.data_values:
        assert re.fullmatch(r"-?\d+\.\d{6}", written), (
            f"data-value must be a plain float to exactly six places; a {bad} {where} "
            f"put {written!r} in the markup"
        )
    assert _get(module, phrase) in bar.title, (
        f"a {bad} {where} is a value that was not recorded, and the title must say "
        f"so; it reads {bar.title!r}"
    )
    assert str(bad) not in bar.markup and "nan" not in bar.markup.lower(), (
        f"the literal {bad} reached the document: {bar.markup!r}"
    )

    zeroed = dict(kwargs)
    zeroed[{"rate": "rate", "floor": "floor"}.get(where, "interval")] = (
        0.0 if where in {"rate", "floor"} else (0.0, 0.0)
    )
    assert bar.markup != _interval_bar(**zeroed, width=BAR_WIDTH, height=BAR_HEIGHT), (
        f"a {bad} {where} renders identically to a recorded zero, which is the "
        f"reading the contract forbids: an unmeasured value must not read as 0"
    )


# -- the missing-value table, one row at a time ------------------------------ #


def test_an_interval_bar_with_no_interval_still_draws_the_point() -> None:
    """The table's first row: "no band element; the point estimate still drawn".

    Three readings. The point is where it belongs; neither end of an interval
    that does not exist appears anywhere; and the picture has strictly fewer
    marks than the complete one, which is what "no band element" means when the
    test is not allowed to assume which element the band was.
    """
    bar = _bar(rate=BAR_RATE, interval=None, floor=BAR_FLOOR)
    point = _one_element(bar.valued(BAR_RATE), "point element carrying the rate")
    _assert_sits_at(point, _mapped_x(BAR_RATE), "the point estimate")
    _assert_sits_at(
        _one_element(bar.classed("floor"), 'element with class="floor"'),
        _mapped_x(BAR_FLOOR),
        "the floor line",
    )
    for end in BAR_INTERVAL:
        assert _six_places(end) not in bar.data_values, (
            f"the bar carries {_six_places(end)} for an interval that was never recorded"
        )

    complete = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    assert len(bar.marks) < len(complete.marks), (
        f"a bar with no interval draws {len(bar.marks)} marks and a complete one "
        f"draws {len(complete.marks)}; the band is still being drawn, presumably "
        f"over a default"
    )


def test_an_interval_bar_with_no_rate_draws_no_point() -> None:
    """The table's second row: "no point element".

    The band and the floor stay, so the test cannot pass by the function
    returning nothing, and the mark count must fall, so it cannot pass by the
    point being drawn somewhere unrecognised.
    """
    bar = _bar(rate=None, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    band = _one_element(bar.valued(BAR_INTERVAL[0]), "band carrying the interval's lower end")
    _assert_sits_at(band, _mapped_x(BAR_INTERVAL[0]), "the interval band")
    _assert_sits_at(
        _one_element(bar.classed("floor"), 'element with class="floor"'),
        _mapped_x(BAR_FLOOR),
        "the floor line",
    )
    assert _six_places(BAR_RATE) not in bar.data_values, (
        "the bar carries the rate it was told was not recorded"
    )

    complete = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    assert len(bar.marks) < len(complete.marks), (
        f"a bar with no rate draws {len(bar.marks)} marks and a complete one draws "
        f"{len(complete.marks)}; the point estimate is still being drawn"
    )


def test_an_interval_bar_with_nothing_recorded_is_a_single_em_dash() -> None:
    """The table's fourth row: "a single ``<text>`` element reading the em dash
    ``—``, and nothing else".

    "Nothing else" is read as nothing *drawn*: no rect, line, circle, path or
    polygon, and no number anywhere. The ``<title>`` is not counted against the
    clause, because ``role="img"`` requires one and a picture that announces
    itself as nothing is worse than one that announces itself as absent.
    """
    bar = _bar(rate=None, interval=None, floor=None)
    assert bar.marks == [], (
        f"nothing was recorded, so nothing may be drawn; found "
        f"{[one['tag'] for one in bar.marks]}"
    )
    text = _one_element(bar.texts, "<text> element")
    assert text["text"].strip() == "—", (
        f"the whole picture is the em dash; the <text> reads {text['text']!r}"
    )
    assert bar.data_values == [], (
        f"nothing was recorded, so no data-value may be written; found {bar.data_values}"
    )
    unexpected = sorted(
        {
            one["tag"]
            for one in bar.elements
            if one["tag"] not in _SVG_STRUCTURE and one["tag"] != "text"
        }
    )
    assert unexpected == [], f'"and nothing else", yet the bar also contains {unexpected}'


# -- self-containment, the machine-checkable half ---------------------------- #


@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_every_interval_bar_variant_passes_assert_self_contained(variant: dict[str, Any]) -> None:
    """C12's third named test, over the missing-value table.

    Each bar is wrapped in the smallest complete document that can hold it and
    put through ``report.assert_self_contained`` -- the same function
    ``render_html`` runs on itself, so what passes here is what ships.

    The two assertions before the wrapping are not decoration. A function
    returning ``"<svg></svg>"``, or the empty string, would sail through
    self-containment while drawing nothing: an empty document fetches nothing.
    ``role="img"`` and a non-empty ``<title>`` are required of every variant
    including the all-missing one, so no row can pass by being empty.
    """
    bar = _bar(**variant)
    assert bar.root_attrs.get("role") == "img"
    assert bar.title, "every variant announces itself, including the one with nothing to say"

    document = _minimal_document(bar.markup)
    assert _get(_module(), "external_urls")(document) == (), (
        f"the {variant} bar puts a fetching position in the document"
    )
    _get(_module(), "assert_self_contained")(document, source="<interval bar>")


def test_the_interval_bar_self_containment_harness_still_catches_a_real_fetch() -> None:
    """The control for the test above: the wrapper is live, not inert.

    Without this, a mis-built ``_minimal_document`` -- one the scanner never
    reaches into -- would make every row of the table pass for the wrong reason.
    """
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR)
    poisoned = _minimal_document(bar.markup + HOSTILE_IMG)
    with pytest.raises(MigrationKitError):
        _get(_module(), "assert_self_contained")(poisoned, source="<interval bar>")


@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_no_interval_bar_variant_emits_a_forbidden_tag_or_a_fetching_attribute(
    variant: dict[str, Any],
) -> None:
    """C12's "Must not" list, read off the parsed element tree rather than the string.

    ``assert_self_contained`` is the shipping check, but it is deliberately
    scoped to what *fetches*: a ``<style>`` with no ``url()`` passes it and the
    contract still forbids one. So the tags are checked directly, and
    ``xlink:href`` is checked against the raw markup as well, because an
    undeclared ``xlink`` prefix makes the document unparseable rather than
    visibly wrong.
    """
    assert set(FETCHING_ATTRIBUTES) == set(_get(_module(), "FETCHING_ATTRS")), (
        "this file's independent list of fetching attributes has drifted from "
        "report.FETCHING_ATTRS"
    )
    bar = _bar(**variant)
    assert bar.root_attrs.get("role") == "img" and bar.elements, (
        "a picture with nothing in it emits no forbidden tag either, so without "
        "this guard the whole test passes against a function that returned "
        '"<svg></svg>". A "must not" assertion is only worth its run against a '
        "bar that was actually drawn."
    )
    for element in bar.elements:
        assert element["tag"] not in {"script", "style"}, (
            f"the {variant} bar emits a <{element['tag']}>, which the contract forbids"
        )
        for name in element["attrs"]:
            assert name not in set(FETCHING_ATTRIBUTES), (
                f"the {variant} bar puts {name!r} on a <{element['tag']}>; every "
                f"attribute in FETCHING_ATTRS is dereferenced by the browser"
            )
        for raw in element["raw"]:
            assert "xlink" not in str(raw).lower(), (
                f"the {variant} bar carries {raw!r} on a <{element['tag']}>"
            )
    assert "xlink:href" not in bar.markup.lower(), f"the {variant} bar carries an xlink:href"
    for tag in FORBIDDEN_ELEMENTS:
        assert f"<{tag}" not in bar.markup.lower(), (
            f"the {variant} bar carries a <{tag}>, which §2.4 forbids outright"
        )


@pytest.mark.parametrize("variant", BAR_VARIANT_CASES, ids=BAR_VARIANT_IDS)
def test_every_interval_bar_variant_is_one_well_formed_svg_element(
    variant: dict[str, Any],
) -> None:
    """C12: "Returns one ``<svg>`` element as a string."

    Parsing is the assertion -- ``_Svg`` refuses anything that is not a single
    well-formed root -- and the two checks below are what stop a do-nothing
    implementation from satisfying it.
    """
    bar = _bar(**variant)
    assert bar.root_attrs, (
        "the <svg> carries no attributes at all, so it declares neither its size "
        "nor its role; this is what a stub returns"
    )
    assert bar.elements, "the <svg> is empty: there is nothing in it to read"


# --------------------------------------------------------------------------- #
# C12, closed against its mutants.
#
# The fixes made in response to C12's review were argued from the review and
# never re-run against the mutants they targeted. 115 mutants were applied to
# ``interval_bar_svg`` and everything it depends on; 99 died. Eight of the
# sixteen survivors were real holes and are closed below, each test named for the
# mutant that motivated it. The other eight are recorded in the handoff as
# equivalent, or as behaviour the contract deliberately leaves open.
#
# Nothing here duplicates a check above. Each one asserts a property no existing
# test states: that an integral value is a measurement, that a bool is not, that
# the em dash puts ink on the canvas, that the minimum band width never overrides
# a real one, that two marks for the same value land in the same place, and that
# the names C14 will reach for are the names that are there.
# --------------------------------------------------------------------------- #


def test_a_rate_or_floor_that_arrives_as_an_int_is_a_measurement_not_an_absence() -> None:
    """``json.loads('{"pass_rate": 1}')`` yields an ``int``, not a ``float``.

    Mutant: narrowing ``_is_number``'s ``isinstance(value, (int, float))`` to
    ``float`` alone passed all 238 tests. Under it, a pass rate of exactly 1 and a
    floor of exactly 0 -- the two values most likely to be written without a
    decimal point, by a hand-authored log or by an encoder that drops a trailing
    zero -- render as *not recorded*.

    That is the contract's dangerous failure mode running backwards: instead of
    an absence drawn as a zero, a real and perfect score drawn as an absence. A
    run that passed every case would show an empty bar saying nothing was
    measured.

    It is also where the two ends of the axis get exercised together: 0.0 and 1.0
    exactly, which the projection maps to ``PAD`` and ``width - PAD``.
    """
    bar = _bar(rate=1, interval=(0, 1), floor=0)
    pad = _bar_pad()

    point = _one_element(bar.classed("rate"), 'element with class="rate"')
    assert point["attrs"].get("data-value") == _six_places(1.0), (
        f"an integral rate of 1 must reach the drawing as the number it is; the "
        f"point carries {point['attrs'].get('data-value')!r}"
    )
    _assert_sits_at(point, BAR_WIDTH - pad, "a rate of exactly 1")

    line = _one_element(bar.classed("floor"), 'element with class="floor"')
    assert line["attrs"].get("data-value") == _six_places(0.0), (
        f"an integral floor of 0 is a recorded floor; the line carries "
        f"{line['attrs'].get('data-value')!r}"
    )
    _assert_sits_at(line, pad, "a floor of exactly 0")

    band = _one_element(bar.classed("interval"), 'element with class="interval"')
    assert float(band["attrs"]["width"]) >= (BAR_WIDTH - 2 * pad) - 1.0, (
        f"an interval of (0, 1) spans the whole axis; the band is "
        f"{band['attrs']['width']} wide of {BAR_WIDTH - 2 * pad}"
    )

    assert "not recorded" not in bar.title.lower(), (
        f"every value here was recorded, two of them as ints; the title says "
        f"something was not: {bar.title!r}"
    )


def test_a_bool_is_not_a_pass_rate_however_much_it_looks_like_one() -> None:
    """Mutant: dropping ``and not isinstance(value, bool)`` passed all 238 tests.

    ``isinstance(True, int)`` is ``True`` in Python, so without that clause a
    ``rate=True`` -- an adapter answering "did it pass" where the caller asked
    "how often" -- silently draws a pass rate of 100%. A wrong kind is the one
    error that cannot be told from a right answer once it has been projected, so
    it is refused at the door rather than rendered.
    """
    bar = _bar(rate=True, interval=None, floor=BAR_FLOOR)

    assert bar.classed("rate") == [], (
        "a bool is not a measured rate and must not be drawn as one; the bar drew "
        f"{[one['tag'] for one in bar.classed('rate')]}"
    )
    assert _six_places(1.0) not in bar.data_values, (
        'the bar carries data-value="1.000000" for a rate that was handed over as '
        "True, which draws a perfect score nobody measured"
    )
    assert "not recorded" in bar.title.lower(), (
        f"a rate that could not be read is a rate that was not recorded, and the "
        f"title must say so; it reads {bar.title!r}"
    )


def test_the_em_dash_standing_in_for_an_empty_bar_actually_puts_ink_on_the_canvas() -> None:
    """Mutants: ``font-size="0"`` on the em dash, and ``fill="none"``. Both lived.

    The canvas-bounds test skips ``_paints`` for ``<text>``, because text is
    painted by rules a coordinate check cannot read. That left the one element
    which *is* the entire picture in the nothing-recorded state free to be
    invisible: a bar rendering as a blank box, which a reader cannot tell from a
    failed render or a missing image.

    The section's own rule applies here more than anywhere -- invisible reads as
    absent -- and here absent is the whole of what there is to read.
    """
    for height in (44, 20, 120):
        bar = _bar(rate=None, interval=None, floor=None, height=height)
        mark = _one_element(bar.texts, "<text> element")
        attrs = mark["attrs"]

        size = attrs.get("font-size")
        if size is not None:
            assert float(size) > 0.0, (
                f"the em dash is set at font-size={size!r}, which renders nothing at "
                f"all; the bar is a blank box claiming to say {mark['text']!r}"
            )
            assert float(size) <= height, (
                f"the em dash is set at font-size={size!r} in a {height}-unit canvas"
            )

        fill = attrs.get("fill", "").strip().lower()
        stroke = attrs.get("stroke", "").strip().lower()
        assert fill not in _INVISIBLE_PAINTS or stroke not in _INVISIBLE_PAINTS, (
            f"the em dash is painted fill={attrs.get('fill')!r} stroke="
            f"{attrs.get('stroke')!r} -- no ink either way. An unrecorded bar that "
            f"renders empty is indistinguishable from a bar that failed to render."
        )


@pytest.mark.parametrize("span", [0.005, 0.02, 0.05])
def test_a_narrow_but_real_interval_is_drawn_at_its_own_width_not_a_minimum(span: float) -> None:
    """Mutant: ``INTERVAL_BAR_MIN_SPAN`` raised from 1.0 to 40.0. It lived.

    The guard exists so a *degenerate* interval still puts a hairline on the
    canvas, and the test for that asks only that the band be wider than zero. So
    nothing stopped the minimum from growing until it swallowed real widths: at
    40 user units a Wilson interval two points wide would be drawn nine points
    wide, and the band's upper edge would sit far past the number beneath it.

    The rule the guard must obey is asserted here rather than its value: it may
    only ever widen a band that would otherwise be too thin to see. Where the
    mapped span is already visible, the band is exactly the mapped span.
    """
    lower = 0.5
    upper = lower + span
    mapped = _mapped_x(upper) - _mapped_x(lower)
    assert mapped > 2.0, "this case is meant to exercise a span that is already visible"

    bar = _bar(rate=lower, interval=(lower, upper), floor=None)
    band = _one_element(bar.classed("interval"), 'element with class="interval"')
    drawn = float(band["attrs"]["width"])

    assert abs(drawn - mapped) <= 1.0, (
        f"the interval ({lower}, {upper}) maps to a span of {mapped:.3f} units and "
        f"the band is drawn {drawn} wide. A minimum width that overrides a measured "
        f"one puts the upper end where the numbers do not say it is, and a band "
        f"drawn wider than it was measured overstates the uncertainty it depicts."
    )
    assert abs((float(band["attrs"]["x"]) + drawn) - _mapped_x(upper)) <= 1.0, (
        f"the band ends at x={float(band['attrs']['x']) + drawn}, not at the mapped "
        f"upper end {_mapped_x(upper):.3f}"
    )


@pytest.mark.parametrize("value", [0.0, 0.25, 0.72, 1.0])
def test_two_marks_drawn_for_the_same_value_land_at_the_same_x(value: float) -> None:
    """Mutants: the floor line shifted by one pixel, and by half a pixel. Both lived.

    Every positional assertion in this section compares one mark against a
    re-derivation of the projection and allows a pixel of slack -- which the
    contract's own named test asks for, and which is right, because pinning the
    coordinate *format* is not this section's business. The cost is that a
    constant offset applied to one mark and not the others slips underneath all
    of them.

    C13 shipped exactly this defect: one side of a marker convention used
    ``transform="translate(-3.5 -3.5)"`` and the other used ``x + width/2``, and
    the tests of either side passed. So the property asserted here is not
    precision but *agreement* -- marks standing for the same number must stand in
    the same place, far tighter than a pixel, whatever the projection rounds to.
    A rounding that moves them moves them together; an offset applied to one of
    them does not.

    The band is included deliberately: it is a ``<rect>`` where the others are
    ``<line>``s, and a rect positioned by its centre beside a line positioned by
    its axis is the C13 mismatch in this function's own vocabulary.
    """
    bar = _bar(rate=value, interval=(value, min(1.0, value + 0.1)), floor=value)

    point = _one_element(bar.classed("rate"), 'element with class="rate"')
    line = _one_element(bar.classed("floor"), 'element with class="floor"')
    band = _one_element(bar.classed("interval"), 'element with class="interval"')

    at_point = float(point["attrs"]["x1"])
    at_floor = float(line["attrs"]["x1"])
    at_band = float(band["attrs"]["x"])

    assert abs(at_floor - at_point) <= 0.01, (
        f"a floor of {value} and a rate of {value} are the same number, so the two "
        f"marks must coincide; the point is at x={at_point} and the floor at "
        f"x={at_floor}. The relationship between the band and the floor *is* the "
        f"verdict, and an offset applied to one mark alone falsifies it while every "
        f"number in the markup stays right."
    )
    assert abs(at_band - at_point) <= 0.01, (
        f"the band's lower end is {value}, the number the point stands for, so the "
        f"band's x must be the point's x; they are at {at_band} and {at_point}. A "
        f"<rect> positioned by its centre beside a <line> positioned by its axis is "
        f"a marker-convention mismatch, which is a defect C13 shipped."
    )


def test_the_interval_bar_names_itself_the_way_a_stylesheet_will_ask_for_it() -> None:
    """Mutant: renaming the root's ``class="interval-bar"``. It lived.

    The same argument the section already makes for ``class="interval"``,
    ``"rate"`` and ``"floor"``: C14 embeds this markup and will select it by
    name, and a seam another chunk addresses by name is part of the contract
    whether or not the contract wrote it down. The root is the one selector that
    reaches the whole picture, and it was the one nothing pinned.
    """
    for variant in BAR_VARIANT_CASES:
        bar = _bar(**variant)
        classes = bar.root_attrs.get("class", "").split()
        assert "interval-bar" in classes, (
            f"the <svg> must name itself so a stylesheet and C14 can select it; the "
            f"{variant} bar carries class={bar.root_attrs.get('class')!r}"
        )


def test_the_label_opens_the_accessible_name_rather_than_trailing_it() -> None:
    """Mutant: ``f"{sentence}: {named}"`` instead of ``f"{named}: {sentence}"``. It lived.

    The existing label test asks only that the label not vanish, on the correct
    grounds that the contract does not say where it goes. But the ``<title>`` of
    a ``role="img"`` element is its *accessible name*, and a name is announced
    before its content is read. A listener given "pass rate 72.0%, interval 61.0%
    to 83.0%, floor 90.0%: candidate accuracy" has to hold four numbers in mind
    before learning what they are about, and on a page of several bars cannot tell
    which bar has begun until it has ended.

    So the order is pinned where the contract left it open, in the direction the
    implementation already chose and its own docstring already promises.
    """
    label = "candidate accuracy on the golden set"
    bar = _bar(rate=BAR_RATE, interval=BAR_INTERVAL, floor=BAR_FLOOR, label=label)
    assert bar.title.startswith(label), (
        f"the label must open the accessible name, not trail the numbers it names; "
        f"the title reads {bar.title!r}"
    )


# --------------------------------------------------------------------------- #
# 20. The per-tag counts, wired into the one streaming pass. Plan C21.
#
# `dimensions.py` is tested exhaustively in `tests/test_dimensions.py`, against
# hand-built record streams. Nothing there touches `report.py`, and until this
# section existed nothing anywhere did: the field, the `tally.add(record)` in
# `from_evidence`'s loop, and `_close_the_tally`'s two branches were carried
# entirely by tests that never imported `report`. These are the tests for the
# wiring, and only for the wiring -- the arithmetic is not re-litigated here.
#
# The join is by *input text*, because a `judge.verdict` carries no item id. So
# every verdict below spells `_default_items`'s input exactly, and a test that
# breaks because that helper changed its wording is telling the truth.
# --------------------------------------------------------------------------- #


def _dim_verdict(item_id: str, *, passed: bool, judge: str = J) -> dict[str, Any]:
    """A ``judge.verdict`` shaped as ``judge.py`` writes one.

    Note what is absent: an ``item_id``. That absence is the whole reason the
    counting had to be split into two phases -- the join is by ``input``, and the
    golden set that would resolve it is named on a record written later.
    """
    return _record(
        "judge.verdict",
        {
            "judge": judge,
            "model_id": JUDGE_MODEL,
            "rubric_hash": RUBRIC_HASH,
            "passed": passed,
            "score": 1.0 if passed else 0.0,
            "reason": f"JUDGE-REASON {item_id}",
            "input": f"INPUT-TEXT for {item_id}",
            "output": f"OUT {item_id}",
            "raw": '{"passed": true}',
        },
        TS_JUDGING,
    )


def _judging_pass(
    model_id: str,
    item_ids: Sequence[str],
    *,
    passed: bool,
    draws: int = N_PER_ITEM,
    judge: str = J,
) -> list[dict[str, Any]]:
    """One side's verdicts, and the ``migkit.judging_completed`` that closes them.

    ``graded`` has to agree with the number of verdicts written or the counter
    refuses the whole run -- that guard is ``dimensions.py``'s, and stating the
    number here rather than deriving it silently is what makes a miscount in this
    helper show up as a refusal instead of as a quietly wrong cell.
    """
    records = [
        _dim_verdict(item_id, passed=passed, judge=judge)
        for item_id in item_ids
        for _ in range(draws)
    ]
    records.append(
        _record(
            EVENT_JUDGING_COMPLETED,
            {
                "model_id": model_id,
                "graded": {judge: len(item_ids) * draws},
                "imputed": {},
                "parse_failures": {},
            },
            TS_JUDGING,
        )
    )
    return records


def _counted_log(
    scenario: Scenario, name: str, *, before: Sequence[Mapping[str, Any]] = ()
) -> Path:
    """``scenario``'s log with a real judging pass in it, optionally after another.

    The standard ``_scenario`` log records that judging *happened* but carries no
    ``judge.verdict`` records, which is faithful to what the rest of this file
    asserts and is why the counter refuses it. Counting needs the verdicts, so
    this writes them: baseline passes everything, candidate fails everything, which
    is the same asymmetry the judged artifacts already encode.
    """
    records: list[Mapping[str, Any]] = [
        _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING)
    ]
    records.extend(before)
    records.extend(_judging_pass(BASELINE_MODEL, scenario.items, passed=True))
    records.extend(_judging_pass(CANDIDATE_MODEL, scenario.items, passed=False))
    records.append(_record(EVENT_COMPARISON, scenario.comparison, TS_COMPARISON))
    if scenario.verdict is not None:
        records.append(_record(EVENT_VERDICT, scenario.verdict, TS_VERDICT))
    return _write_evidence(scenario.root / name, records)


def _counts(model: Any) -> Any:
    """The matrix, which is where these counts live now. Plan C10.

    C21 wired the raw ``DimensionCounts`` onto ``ReportModel.dimension_counts``
    and C10 replaced that field with ``dimensions: DimensionMatrix``, whose cells
    carry ``tag``, ``passes``, ``n`` and ``items`` -- every fact the raw counts
    held. So these tests are re-pointed rather than deleted: they exist because
    deleting ``tally.add(record)`` from ``from_evidence``'s loop once left the
    entire suite green, and that hole is no smaller under the new field.
    """
    return _get(model, "dimensions")


def _tag_cell(model: Any, model_id: str, tag: str) -> tuple[int, int, int]:
    counts = _counts(model)
    assert counts.available is True, counts.reason
    column = counts.column(model_id)
    assert column is not None, f"the matrix has no column for {model_id!r}"
    one = column.cell(tag)
    assert one is not None, f"the matrix has no {tag!r} cell for {model_id!r}"
    return (one.passes, one.n, one.items)


#: ``_default_items`` tags every other item ``arithmetic`` and the rest
#: ``extraction``, so twelve items split six and six, and six items at five draws
#: is thirty completions a tag a side.
ARITHMETIC_ITEMS = 6
ARITHMETIC_N = ARITHMETIC_ITEMS * N_PER_ITEM


def test_the_report_counts_the_tags_out_of_the_log_it_already_streams(tmp_path: Path) -> None:
    """The happy path through the wiring, which nothing exercised before.

    Both halves of the join have to line up for this to pass: the verdicts have to
    be read on the way past and filed under something, and the golden set named on
    the comparison record -- which arrives *after* every one of them -- has to
    resolve what they were filed under back to item ids and then to tags.
    """
    scenario = _scenario(tmp_path / "counted")
    model = _model_from(_counted_log(scenario, "evidence-counted.jsonl"))

    assert _counts(model).available is True, _counts(model).reason
    assert _tag_cell(model, BASELINE_MODEL, "arithmetic") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    )
    assert _tag_cell(model, CANDIDATE_MODEL, "arithmetic") == (0, ARITHMETIC_N, ARITHMETIC_ITEMS)
    assert _tag_cell(model, BASELINE_MODEL, "extraction") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    )


def test_the_matrix_is_the_headline_runs_and_not_the_whole_logs(tmp_path: Path) -> None:
    """The per-run ruling, asserted where the two numbers would actually collide.

    ``tests/test_dimensions.py`` settles this for the counter in isolation. It has
    to be settled again here because this is the only place the conflict is
    visible: the banner above the matrix reports one run, and a log of fourteen
    nightly runs holds fourteen judging passes. A cumulative matrix would print
    fourteen nights' completions directly beneath a banner reporting the last, and
    nothing on the page could reconcile the two numbers.

    The earlier pass below fails every item where the headline pass passes every
    item, so summing is not merely a different number -- it would halve the
    reported pass rate of a run that passed everything.
    """
    scenario = _scenario(tmp_path / "two-runs")
    earlier = [
        *_judging_pass(BASELINE_MODEL, scenario.items, passed=False),
        *_judging_pass(CANDIDATE_MODEL, scenario.items, passed=False),
        *_earlier_run(scenario, tag="one"),
    ]
    model = _model_from(_counted_log(scenario, "evidence-two-runs.jsonl", before=earlier))

    assert _tag_cell(model, BASELINE_MODEL, "arithmetic") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    ), (
        "the matrix summed both runs: it reports more completions than the "
        "headline run judged, under a banner that reports only the headline run"
    )


def test_the_series_still_sees_the_history_the_matrix_deliberately_drops(
    tmp_path: Path,
) -> None:
    """The other half of the ruling, and the reason it is a ruling and not a bug.

    The timeline is the one deliberately cumulative thing in the document. If the
    matrix being per-run were an accident of the counter never seeing the earlier
    records, the series would be short too. It is not: both are fed by the same
    single pass, and they disagree about history on purpose.
    """
    scenario = _scenario(tmp_path / "two-runs-series")
    earlier = [
        *_judging_pass(BASELINE_MODEL, scenario.items, passed=False),
        *_judging_pass(CANDIDATE_MODEL, scenario.items, passed=False),
        *_earlier_run(scenario, tag="one"),
    ]
    model = _model_from(_counted_log(scenario, "evidence-two-series.jsonl", before=earlier))

    assert len(_series(model)) == 2, (
        "the earlier run never reached the series, so this log does not test what "
        "it claims to test"
    )
    assert _counts(model).available is True, _counts(model).reason


def test_a_golden_set_that_cannot_be_trusted_hands_back_its_own_sentence(
    tmp_path: Path,
) -> None:
    """Reused verbatim, never re-worded -- the disclosure is written in one place.

    Asserted against ``model.goldenset["reason"]`` rather than against a quoted
    sentence, because a quoted sentence here would be a fourth copy of the same
    disclosure and this test exists to stop there being a third.
    """
    scenario = _scenario(tmp_path / "mismatch", recorded_goldenset_hash="d" * 64)
    model = _model_from(_counted_log(scenario, "evidence-mismatch.jsonl"))

    counts = _counts(model)
    assert counts.available is False
    assert counts.reason == model.goldenset["reason"], (
        "the golden set's refusal was re-worded on its way into the counts; three "
        "copies of a disclosure are three chances for one to go stale"
    )
    assert (counts.tags, counts.baseline.cells, counts.candidates) == ((), (), ()), (
        "a refusal arrived carrying a partial matrix, which is what the caller must "
        "never be able to render"
    )


def test_a_log_that_records_judging_without_recording_verdicts_is_refused(
    tmp_path: Path,
) -> None:
    """Which is every log this file writes anywhere else, so it is worth pinning.

    ``_scenario`` records that judging happened and writes judged artifacts, but
    puts no ``judge.verdict`` in the evidence log. The counter reads the log and
    only the log -- that is R1's whole point -- so it declines, and the sentence it
    declines with names the judge rather than blaming the golden set.
    """
    scenario = _scenario(tmp_path / "no-verdicts")
    model = _from_evidence(scenario)

    counts = _counts(model)
    assert counts.available is False
    assert model.goldenset["available"] is True, (
        "the golden set is fine here; if it were not, this test would be asserting "
        "the wrong refusal"
    )
    assert J in counts.reason, (
        f"the refusal should name the judge whose verdicts are missing; it reads "
        f"{counts.reason!r}"
    )


def test_the_counts_survive_a_log_whose_artifacts_moved_away(tmp_path: Path) -> None:
    """R1, at the level of the wiring: the cross-machine render still counts.

    Everything the counting reads is in the evidence log and in the golden set.
    Neither is an artifact, so moving the artifacts away has to leave the counts
    intact -- and this is the render ``report.py``'s own docstring calls the
    designed workflow, a reviewer opening a shared log with no artifact directory
    beside it.
    """
    scenario = _scenario(tmp_path / "moved")
    log = _counted_log(scenario, "evidence-moved.jsonl")
    for artifact in (tmp_path / "moved").glob("*.judged.jsonl"):
        artifact.unlink()

    model = _model_from(log)

    assert _counts(model).available is True, _counts(model).reason
    assert _tag_cell(model, BASELINE_MODEL, "arithmetic") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    )


# --------------------------------------------------------------------------- #
# Which judge the matrix is counted under
#
# A panel writes one verdict per judge per completion, so the matrix has to pick
# one -- ``_close_the_tally``'s docstring says "``judge`` is the panel's first
# judge" and ``from_evidence`` spells that ``judges[0].name``. Nothing tested it.
# Every log in this file until now carried a one-judge panel, where the first
# judge and the last judge are the same judge and the selection cannot be seen at
# all: ``judges[0].name`` mutated to ``judges[-1].name`` survived the whole file.
#
# The panel below is deliberately asymmetric. The first judge passes every draw
# and the second fails every draw, so the two selections do not merely differ,
# they are each other's opposite -- and a cell counted under the wrong one is a
# model that got everything wrong reported as a model that got everything right.
# --------------------------------------------------------------------------- #

#: The second judge on the panel. Never equal to :data:`J`, so a matrix counted
#: under the wrong one cannot coincidentally agree with the right answer.
SECOND_JUDGE = "strictness"


def _panel_judging_pass(
    model_id: str,
    item_ids: Sequence[str],
    *,
    passed_by: Mapping[str, bool],
    draws: int = N_PER_ITEM,
) -> list[dict[str, Any]]:
    """One side judged by a whole panel: every judge's verdicts, then one close.

    A real panel writes one ``migkit.judging_completed`` per *model*, whose
    ``graded`` names every judge -- not one close per judge. Getting that wrong
    would show up as the counter refusing the run rather than as a wrong cell,
    which is the shape ``_judging_pass`` above is careful about for the same
    reason.
    """
    records = [
        _dim_verdict(item_id, passed=passed, judge=judge)
        for judge, passed in passed_by.items()
        for item_id in item_ids
        for _ in range(draws)
    ]
    records.append(
        _record(
            EVENT_JUDGING_COMPLETED,
            {
                "model_id": model_id,
                "graded": {judge: len(item_ids) * draws for judge in passed_by},
                "imputed": {},
                "parse_failures": {},
            },
            TS_JUDGING,
        )
    )
    return records


def _panel_scenario(root: Path) -> Scenario:
    """The standard run, judged by two judges that disagree about everything."""
    return _scenario(
        root,
        judges=[
            _judge_payload(name=J),
            _judge_payload(name=SECOND_JUDGE, regressed=False),
        ],
    )


def _panel_log(scenario: Scenario, name: str) -> Path:
    """``_counted_log``'s shape, with both judges' verdicts in it.

    The first judge passes every draw for both sides and the second fails every
    draw for both sides, so which judge was counted is legible from any cell.
    """
    passed_by = {J: True, SECOND_JUDGE: False}
    records: list[Mapping[str, Any]] = [
        _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING),
        *_panel_judging_pass(BASELINE_MODEL, scenario.items, passed_by=passed_by),
        *_panel_judging_pass(CANDIDATE_MODEL, scenario.items, passed_by=passed_by),
        _record(EVENT_COMPARISON, scenario.comparison, TS_COMPARISON),
    ]
    if scenario.verdict is not None:
        records.append(_record(EVENT_VERDICT, scenario.verdict, TS_VERDICT))
    return _write_evidence(scenario.root / name, records)


def test_the_matrix_is_counted_under_the_panels_first_judge(tmp_path: Path) -> None:
    """The selection ``_close_the_tally``'s docstring states, asserted.

    Both judges graded every draw of both sides. The first passed all of them and
    the second failed all of them, so a matrix counted under the panel's *last*
    judge reports zero passes everywhere -- a complete, available, plausible
    matrix saying both models got everything wrong.
    """
    scenario = _panel_scenario(tmp_path / "panel")
    model = _model_from(_panel_log(scenario, "evidence-panel.jsonl"))

    counts = _counts(model)
    assert counts.available is True, counts.reason
    assert _tag_cell(model, BASELINE_MODEL, "arithmetic") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    ), (
        f"the matrix was not counted under {J!r}, the panel's first judge. "
        f"{SECOND_JUDGE!r} failed every draw in this log, so a cell of zero "
        f"passes is the other judge's answer wearing this one's label"
    )
    assert _tag_cell(model, CANDIDATE_MODEL, "extraction") == (
        ARITHMETIC_N,
        ARITHMETIC_N,
        ARITHMETIC_ITEMS,
    )


def test_the_second_judges_verdicts_are_read_and_not_counted(tmp_path: Path) -> None:
    """The other half: the panel's size never reaches a denominator.

    Both judges' verdicts go past the same tally on the same single pass -- the
    filter cannot be applied on the way past, because which judge the document
    wants is named on the ``migkit.comparison`` record at the *end*. So the second
    judge's draws are accumulated and then not counted, and ``n`` is the number of
    draws rather than the number of draws times the panel size.
    """
    scenario = _panel_scenario(tmp_path / "panel-n")
    model = _model_from(_panel_log(scenario, "evidence-panel-n.jsonl"))

    passes, n, items = _tag_cell(model, BASELINE_MODEL, "arithmetic")

    assert n == ARITHMETIC_N, (
        f"n is {n} against {ARITHMETIC_N} draws: a two-judge panel doubled the "
        f"denominator, so both judges' verdicts were counted as one population"
    )
    assert (passes, items) == (ARITHMETIC_N, ARITHMETIC_ITEMS)


def test_the_panel_the_document_reports_is_the_panel_the_matrix_chose_from(
    tmp_path: Path,
) -> None:
    """The two tests above are only meaningful if the panel really has two judges.

    Asserted through ``model.judges`` rather than through the fixture, because the
    fixture is the thing that would silently stop building a panel.
    """
    scenario = _panel_scenario(tmp_path / "panel-rows")
    model = _model_from(_panel_log(scenario, "evidence-panel-rows.jsonl"))

    names = [_get(one, "name") for one in _get(model, "judges")]

    assert names == [J, SECOND_JUDGE], (
        f"the panel is {names}; the judge-selection tests above are asserting "
        f"nothing unless it holds two judges in this order"
    )


def test_a_model_built_by_any_other_route_says_no_counts_were_taken(tmp_path: Path) -> None:
    """The default is a sentence, because ``{}`` is not something a renderer can print.

    ``from_evidence`` is the only thing that counts, so every other constructor --
    and there are several, in this file and in anyone else's -- produces a model
    that has to be able to answer the question anyway.
    """
    scenario = _scenario(tmp_path / "default")
    model = _from_evidence(scenario)
    fields = {
        one.name: getattr(model, one.name)
        for one in dataclasses.fields(model)
        if one.name != "dimensions"
    }
    bare = type(model)(**fields)

    counts = _get(bare, "dimensions")
    assert counts.available is False
    assert counts.reason, "the default has to say something, and it says nothing"


# --------------------------------------------------------------------------- #
# C14a -- the two charts that exist, and the evidence made legible.
#
# The two SVG helpers return trusted markup and must be injected unescaped;
# everything derived from model output must stay escaped. The set of ``| safe``
# filters in the template is therefore a fixed, enumerable list, and this section
# opens by pinning it. A ``| safe`` that appears anywhere else is the failure the
# contract names as the one that ships: a path that can carry model output turns
# an escaped ``<img src="https://tracker/x.png">`` in a completion into a real
# fetch.
# --------------------------------------------------------------------------- #

#: The only expressions the document is allowed to mark safe, by name. Each is a
#: hand-rolled SVG helper's injection point -- ``interval_bar_svg`` reached
#: through the ``interval_bar`` filter, and ``timeline_svg``'s ``.svg`` member
#: reached through the ``timeline`` filter. Written as a set of *descriptors*
#: rather than a count, because "two safes" is satisfied by two safes in the
#: wrong places.
SAFE_INJECTION_POINTS = frozenset({"interval_bar", "timeline.svg"})


def _safe_descriptor(node: Any) -> str:
    """Name the expression a ``| safe`` was applied to.

    A filter chain (``model | interval_bar | safe``) is named by the filter
    underneath the ``safe``; an attribute access (``tl.svg | safe``) by its
    dotted path. Anything else returns its node type, which is what makes an
    unexpected ``safe`` fail with a legible name rather than a bare count.
    """
    from jinja2 import nodes

    if isinstance(node, nodes.Filter):
        return str(node.name)
    if isinstance(node, nodes.Getattr):
        inner = node.node
        stem = inner.name if isinstance(inner, nodes.Name) else type(inner).__name__
        return f"{stem}.{node.attr}"
    if isinstance(node, nodes.Name):
        return str(node.name)
    return type(node).__name__


def _safes_in_template() -> list[str]:
    """Every ``| safe`` in the document's own source, by the expression it marks."""
    from jinja2 import Environment, nodes

    source = _report._CHANGES_MACRO + _report._TEMPLATE
    tree = Environment().parse(source)
    return [
        _safe_descriptor(node.node)
        for node in tree.find_all(nodes.Filter)
        if node.name == "safe"
    ]


def test_the_document_marks_exactly_one_expression_safe_per_hand_rolled_svg_and_no_others() -> (
    None
):
    """C14a's test that fails first: the ``| safe`` set, by name, not by count.

    Parsed with jinja2's own parser rather than grepped, for the same reason the
    self-containment detector is an HTML parser rather than a regex: ``| safe``
    inside a quoted string, or inside a comment, is not a filter, and a grep
    cannot tell the difference. The assertion is on the *set* so that a ``safe``
    moved from the timeline onto a row's model text fails here even though the
    count is unchanged -- which is exactly the mutation that would ship the
    fetching hole.
    """
    found = _safes_in_template()
    assert len(found) == len(set(found)), f"a safe is applied twice to one expression: {found}"
    assert set(found) == SAFE_INJECTION_POINTS, (
        f"the document marks {sorted(set(found))} safe; the only expressions that "
        f"may be marked safe are {sorted(SAFE_INJECTION_POINTS)}. A safe on anything "
        f"derived from model output turns an escaped tag in a completion into a fetch."
    )


def _render_context(model: Any) -> dict[str, Any]:
    """The keyword arguments ``render_html_string`` passes to the template."""
    return {
        "model": model,
        "title": _report._default_title(model),
        "generated": NOW_A,
        "verdict_class": _report._VERDICT_CLASS.get(model.verdict_word, "none"),
        "sections": _report.methodology_sections(model),
        "dash": _report.EM_DASH,
        "ellipsis": "…",
        "unrecorded": _report.THRESHOLD_SOURCE_UNRECORDED,
        "config_path": model.config_path,
        "baseline_parts": _report._parts_phrase(model.baseline, "baseline"),
        "candidate_parts": _report._parts_phrase(model.candidate, "candidate"),
        "max_output_chars": model.max_output_chars,
        "max_report_chars": model.max_report_chars,
    }


def test_stripping_a_safe_escapes_the_chart_instead_of_drawing_it(tmp_path: Path) -> None:
    """The reviewer's third question, as a test: mutate each ``| safe`` off.

    Both helpers return trusted markup and are the only expressions allowed
    through unescaped. If a ``| safe`` were removed the document would not fail --
    it would render the SVG source as visible text, which is a broken page rather
    than a raised exception, and a broken page is the kind of thing a green suite
    ships. So each one is removed here and the escaping is asserted, which proves
    the filter is load-bearing rather than decorative.
    """
    from jinja2 import DictLoader, Environment, StrictUndefined, select_autoescape

    model, _ = _rendered(tmp_path / "safe")
    source = _report._CHANGES_MACRO + _report._TEMPLATE

    for injection in sorted(SAFE_INJECTION_POINTS):
        stem = injection.split(".")[0]
        mutated = source.replace(injection + " | safe", injection).replace(
            stem + " | safe", stem
        )
        assert mutated != source, "no " + injection + " | safe was found to remove"
        env = Environment(
            loader=DictLoader({_report._TEMPLATE_NAME: mutated}),
            autoescape=select_autoescape(default_for_string=True, default=True),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        for name, one in _report._environment().filters.items():
            env.filters[name] = one
        html = env.get_template(_report._TEMPLATE_NAME).render(**_render_context(model))
        assert "&lt;svg" in html, (
            f"removing the {injection} | safe did not escape anything, so that "
            f"filter is not what puts the chart in the document and the safe-set "
            f"test above is pinning the wrong thing"
        )


# -- defect 1: repetition presented as evidence ------------------------------ #


def test_identical_draws_are_printed_once_and_the_count_is_stated(tmp_path: Path) -> None:
    """Five byte-identical draws are one block plus a sentence, not five blocks.

    The sentence is not decoration. Collapsing without it would remove the count
    from the page entirely, and "how many draws agreed" is what says how much
    weight one draw carries.
    """
    repeated = "the candidate said exactly this, five times over"
    model, html = _rendered(tmp_path / "identical", candidate_output=repeated)

    # One block per row that embedded it, not one per document: every changed row
    # quotes its own draws, and collapsing across rows would merge two different
    # items' evidence into one claim.
    rows = [
        row
        for row in (*model.flips, *model.gains, *model.unstable)
        if row.detail_embedded and set(row.candidate_outputs) == {repeated}
    ]
    assert rows, "this fixture is supposed to give some row five identical draws"

    printed = [text for text in _pre_texts(html) if text.strip() == repeated]
    assert len(printed) == len(rows), (
        f"the identical candidate draws were printed {len(printed)} times across "
        f"{len(rows)} row(s); repetition is not evidence and each row should show "
        f"the text once"
    )
    assert len(printed) < len(rows) * N_PER_ITEM, (
        "nothing was collapsed, so this test would pass against the old template"
    )
    assert f"all {N_PER_ITEM} draws identical" in _visible(html), (
        "the draws were collapsed without saying how many there were, which "
        "removes the count from the document rather than de-duplicating it"
    )


def test_differing_draws_are_every_one_printed_and_the_distinct_count_is_stated(
    tmp_path: Path,
) -> None:
    """The other half, and the half a collapse could silently break.

    The default scenario gives every draw a distinct suffix. All of them must
    still be printed -- dropping a draw to shorten the page would remove evidence
    -- and the document must say how many were distinct, because that is the fact
    the reader cannot otherwise see.
    """
    _, html = _rendered(tmp_path / "differing")
    blocks = _pre_texts(html)

    for index in range(N_PER_ITEM):
        marker = "#" + str(index)
        assert any(marker in text for text in blocks), (
            f"draw {marker} is not in the document; collapsing must not drop a draw"
        )
    assert f"{N_PER_ITEM} draws, {N_PER_ITEM} distinct" in _visible(html), (
        "draws that all differ must say so; uniformity and variation rendering "
        "identically is the defect this chunk exists to fix"
    )


class _PreBlocks(HTMLParser):
    """The text of every ``<pre>``, block by block.

    ``_Document`` flattens the page into one string, which cannot answer "how
    many separate blocks hold this text" -- and that count is exactly what the
    draws collapse changes. Read independently, on the stdlib parser, for the
    same reason ``_Document`` is: an escaped ``<pre>`` inside a model completion
    is text, not a block, and only a parser can tell those apart.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "pre":
            self._depth += 1
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._depth:
            self._depth -= 1
            self.blocks.append("".join(self._buffer))
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buffer.append(data)


def _pre_texts(html: str) -> list[str]:
    """The text of every ``<pre>`` block, read with the stdlib parser."""
    parser = _PreBlocks()
    parser.feed(html)
    parser.close()
    return parser.blocks


def test_only_total_agreement_collapses() -> None:
    """A pure-function check on the rule, including the case that must not collapse.

    Partial grouping was rejected deliberately: it reorders the draws, and the
    order they were recorded in is the only evidence a reader has about when a
    model changed its answer within a run.
    """
    draws = _report._draws

    assert draws(("a", "a", "a")).texts == ("a",)
    assert draws(("a", "a", "a")).total == 3
    assert draws(("a", "a", "a")).sentence == "all 3 draws identical"

    mixed = draws(("a", "b", "a"))
    assert mixed.texts == ("a", "b", "a"), (
        "a partially repeating side must print every draw in the recorded order"
    )
    assert mixed.distinct == 2
    assert mixed.sentence == "3 draws, 2 distinct"

    lone = draws(("a",))
    assert lone.texts == ("a",)
    assert lone.sentence == "", "one draw needs no sentence; there is nothing to compare"
    assert draws(()).texts == ()
    assert draws(()).sentence == ""


def test_the_completeness_claim_still_counts_what_the_models_produced(
    tmp_path: Path,
) -> None:
    """R5's failure in a new coat, and the one thing the collapse could break.

    The budget sentence certifies completeness. If collapsing identical draws made
    it count what was *printed*, it would certify a smaller thing in exactly the
    same words. So the produced figure must survive unchanged, and where the
    printed total differs the document must give both numbers rather than swapping
    one for the other.
    """
    repeated = "identical draw text"
    model, html = _rendered(tmp_path / "budget", candidate_output=repeated)
    visible = _squeeze(_visible(html))

    produced = model.detail.embedded
    printed = _report._printed_chars(model)
    assert printed < produced, (
        "this fixture is supposed to collapse something; if nothing collapsed the "
        "assertions below are vacuous"
    )
    assert f"{produced:,}" in visible, (
        f"the budget sentence no longer states the {produced:,} characters the "
        f"models produced, so it is certifying what survived the presentation layer"
    )
    assert f"{printed:,}" in visible, (
        f"the document collapses draws but never states the {printed:,} characters "
        f"it actually prints, so the two numbers cannot be reconciled by a reader"
    )


# -- defect 2: the finding behind a closed triangle --------------------------- #


def _section_details(html: str, section_id: str) -> list[bool]:
    """Whether each ``<details>`` under one ``<h2 id=...>`` carries ``open``."""
    states: list[bool] = []
    inside = False
    for tag, attrs in _parse(html).tags:
        if tag == "h2":
            inside = attrs.get("id") == section_id
        elif tag == "details" and inside:
            states.append("open" in attrs)
    return states


def test_flips_are_open_by_default_and_gains_are_not(tmp_path: Path) -> None:
    """Flips are the point of the document; gains are context.

    Opening gains too would give a reader's eye the same weight for both, and this
    document's own argument is that netting the two lists is how a bad migration
    ships.
    """
    model, html = _rendered(tmp_path / "open")
    assert model.flips, "this fixture needs a flip"
    assert model.gains, "this fixture needs a gain"

    flips = _section_details(html, "flips")
    gains = _section_details(html, "gains")

    assert flips
    assert all(flips), (
        f"a flip renders closed: {flips}. The run's most important result sits "
        f"inside one of these, and a reader who does not click sees only a verdict"
    )
    assert gains
    assert not any(gains), (
        f"a gain renders open: {gains}. Gains are context, not the finding"
    )


# -- defect 3: a path printed eight times ------------------------------------- #


def test_the_thresholds_table_names_its_source_by_filename(tmp_path: Path) -> None:
    """One absolute path, repeated once per threshold row, in a 60rem document."""
    model, html = _rendered(tmp_path / "paths")
    config = model.config_path
    assert config, "this fixture needs a recorded config path"
    visible = _visible(html)
    seen = visible.count(config)

    assert seen <= 2, (
        f"the full config path appears {seen} times in the visible text; it belongs "
        f"at full length in 'What was compared' and the provenance block, and "
        f"nowhere else. Before this chunk it appeared once per threshold row"
    )
    assert _report._basename(config) in visible, (
        "the source column must still name the file it came from"
    )
    assert model.thresholds, (
        "no thresholds are recorded, so the repetition this test measures cannot "
        "occur and the count above is vacuous"
    )


def test_the_full_path_is_still_shown_where_it_can_be_checked(tmp_path: Path) -> None:
    """Shortening must not become hiding.

    A reviewer signing a migration decision has to be able to check which file the
    thresholds came from, so the whole path stays in the document -- once, where a
    path belongs.
    """
    model, html = _rendered(tmp_path / "shown")
    config = model.config_path
    assert config, "this fixture needs a recorded config path"
    assert config in _visible(html), (
        "the full config path is nowhere in the document; the basename in the "
        "thresholds table is a shortening, not a substitute"
    )


def test_a_windows_path_in_a_title_attribute_would_fail_the_self_containment_gate() -> None:
    """Why C14a's contract clause about ``title=`` was rejected rather than built.

    The contract says to keep the full path in a ``title=``, on the grounds that
    ``title`` is not in ``FETCHING_ATTRS`` and is not dereferenced. Both halves are
    true and the conclusion does not follow: ``title`` is also not exempt under
    ``_NEVER_DEREFERENCED_RE``, so it is still judged by *shape* -- and a Windows
    drive letter is a URL scheme by ``_SCHEME_RE``'s reading, because ``C:``
    matches ``[a-zA-Z][a-zA-Z0-9+.-]*:``.

    So the tooltip would make ``assert_self_contained`` refuse the entire document,
    which runs inside ``render_html`` before the file is written -- and it would do
    so on Windows only, passing everywhere it was written. That is the platform
    trap this project has already been bitten by twice, in opposite directions,
    once in each repository.

    Pinned as a test rather than left as a paragraph so that a future editor who
    reaches for ``title=`` on a path finds out here instead of from a user.
    """
    backslash = chr(92)
    windows = "C:" + backslash + "work" + backslash + "run-config.toml"

    tooltipped = _report.external_urls('<td title="' + windows + '">run-config.toml</td>')
    assert len(tooltipped) == 1, (
        "a Windows path in a title= is no longer flagged; if the scanner was "
        "deliberately widened, this test and _source_label's docstring are the two "
        "places that argue from the old behaviour"
    )
    assert tooltipped[0].attribute == "title"

    assert _report.external_urls("<td>" + windows + "</td>") == (), (
        "the same path as element text must stay clean -- that is why the "
        "thresholds table prints a basename rather than a tooltip"
    )
    assert _report.external_urls('<td title="/work/run-config.toml">x</td>') == (), (
        "the POSIX form passes, which is exactly why this defect would have "
        "shipped: it is invisible on the platform most contributors test on"
    )


# -- defect 4: a row that can never say anything ------------------------------ #


def _visible_between(html: str, start_id: str, end_id: str) -> str:
    """Visible text between two ``<h2 id=...>`` headings."""
    start = html.find('<h2 id="' + start_id + '"')
    end = html.find('<h2 id="' + end_id + '"', start + 1)
    assert start != -1
    assert end > start, f"no {start_id}..{end_id} span in the document"
    return _visible(html[start:end])


def test_a_wholly_scripted_run_omits_the_latency_table_and_says_why(
    tmp_path: Path,
) -> None:
    """``0.000 / 0.000`` is not a fast model; it is the absence of a measurement."""
    model, html = _rendered(
        tmp_path / "fake",
        baseline_adapter="FakeAdapter",
        candidate_adapter="FakeAdapter",
    )
    assert model.baseline.is_fake
    assert model.candidate.is_fake

    start = html.find('<h2 id="latency"')
    end = html.find('<h2 id="flips"', start + 1)
    tags = [tag for tag, _ in _parse(html[start:end]).tags]
    assert "table" not in tags, (
        "the latency table is still rendered for a wholly scripted run; every cell "
        "in it is a few microseconds of local dictionary lookup"
    )

    latency = _visible_between(html, "latency", "flips")
    assert "not measured" in latency.lower(), (
        "the table was removed without saying why, which is an absence a reader "
        "cannot distinguish from a rendering bug"
    )


def test_a_real_run_still_gets_its_latency_table(tmp_path: Path) -> None:
    """The suppression must key off ``is_fake`` and not off the numbers.

    A real provider that genuinely answered in under a millisecond would round to
    ``0.000`` too, and suppressing *that* would hide a measurement rather than an
    absence.
    """
    model, html = _rendered(tmp_path / "real")
    assert not model.baseline.is_fake
    assert not model.candidate.is_fake

    latency = _visible_between(html, "latency", "flips")
    assert "median" in latency.lower(), (
        "a real run lost its latency table; the suppression is meant to fire on "
        "scripted adapters only"
    )


# -- the charts themselves ---------------------------------------------------- #


def test_the_banner_bar_draws_the_numbers_the_judge_table_prints(tmp_path: Path) -> None:
    """The bar and the table must not be able to disagree.

    Two pictures of one number is two chances to be wrong. The bar is drawn from
    ``series[-1]`` and the table from the judge records, so this is a real
    cross-check between two paths through the evidence rather than a tautology.
    """
    model, html = _rendered(tmp_path / "agree")
    row = _judge_row(model)
    point = model.series[-1]

    assert point.pass_rate == pytest.approx(_rate_stat(row, "candidate").rate), (
        "the banner's bar and the judge table are reading different pass rates"
    )

    opened = html.find('<div class="bar">')
    bar = html[opened : html.find("</div>", opened)]
    assert f'data-value="{point.pass_rate:.6f}"' in bar, (
        f"the bar's drawn value is not the headline run's pass rate {point.pass_rate}"
    )


def test_neither_chart_emits_a_css_url_in_a_presentation_attribute() -> None:
    """The reviewer's second question, pinned.

    C20 narrowed the self-containment scanner and its reviewer found that SVG
    presentation attributes taking a CSS ``<url>`` -- ``fill``, ``filter``,
    ``mask``, ``clip-path``, ``marker-end``, ``cursor`` -- are invisible to it.
    This chunk injects inline SVG into the document for the first time, so
    ``fill="url(...)"`` became reachable in a way it was not before.

    Both helpers emit only literal colours today, which is what makes the gap
    unreachable rather than merely unexercised. That is a property of the current
    implementation and nothing enforces it, so it is enforced here: the day someone
    reaches for a gradient, this fails and the scanner gap has to be closed first.
    """
    bar = _report.interval_bar_svg(rate=0.72, interval=(0.61, 0.81), floor=0.8, label="x")
    chart = _report.timeline_svg(())

    for name, svg in (("interval_bar_svg", bar), ("timeline_svg", chart.svg)):
        assert "url(" not in svg.lower(), (
            f"{name} emits a CSS url(), which the self-containment scanner does not "
            f"see in a presentation attribute -- close that gap before shipping this"
        )
        assert 'style="' not in svg, (
            f"{name} emits a style attribute; presentation must stay in inline "
            f"attributes and the chart's own <style> block, both of which the "
            f"scanner does read"
        )


def test_the_self_containment_fixtures_exercise_both_new_sections(
    tmp_path: Path,
) -> None:
    """C14a's "then" clause: the two must-pass tests must not be vacuous.

    ``test_the_rendered_report_has_no_external_url`` and its neighbour are required
    to pass unchanged against a fixture that exercises both new sections. They
    render the standard scenario, so this asserts that the standard scenario
    actually contains both -- otherwise the two tests would keep passing while
    covering none of this chunk.
    """
    model, html = _rendered(tmp_path / "fixture")
    assert model.series, "the standard fixture has no series, so no timeline renders"
    assert '<h2 id="timeline">' in html, "the fixture does not exercise the timeline"
    assert '<div class="bar">' in html, "the fixture does not exercise the banner bar"
    assert "migkit-timeline" in html, "timeline_svg does not reach the document"
    assert "interval-bar" in html, "interval_bar_svg does not reach the document"
    assert _urls(html) == (), "the fixture that exercises both sections is not clean"


# --------------------------------------------------------------------------- #
# C14a review -- the survivors of a 27-mutant run, and two false sentences.
#
# Every test below was written against a mutant that the suite as merged left
# alive. The first two are not coverage: they are documents that said something
# untrue about themselves, which in a compliance artifact is the defect and not
# the omission.
# --------------------------------------------------------------------------- #


def _timeline_markers(html: str) -> list[dict[str, str]]:
    """Every run marker in the run-history chart, in the order the markup lists."""
    start = html.find('<h2 id="timeline">')
    assert start != -1, "no run-history section in this document"
    end = html.find("</svg>", start)
    return [
        dict(re.findall(r'([a-z-]+)="([^"]*)"', one))
        for one in re.findall(r"<rect [^>]*/>", html[start:end])
    ]


def _pct_in(markup: str, value: float) -> bool:
    return f"{value * 100:.1f}%" in markup


def test_the_run_history_prose_does_not_claim_the_banner_is_the_newest_marker(
    tmp_path: Path,
) -> None:
    """The chart is sorted by clock; the banner is read in write order.

    ``series.read_series`` sorts nothing on purpose -- "a sorted series would
    silently reorder a log whose clock stepped backwards over a daylight-saving
    boundary" -- while ``timeline_svg`` sorts by parsed ``created``, also on
    purpose, because its axis is time. Both are right. What follows is that the
    banner describes ``series[-1]``, the last comparison *written*, while the
    rightmost marker is the last comparison *dated*, and on any log where those
    disagree a sentence promising they are the same run is false.

    Proved rather than argued: this log's first-written comparison carries a 2027
    date and a GO, and its last-written one carries the scenario's 2026 date and a
    NO-GO. The banner reads NO-GO; the rightmost marker is the GO. A reader told
    the banner reports "the most recent of these runs" reads the chart backwards.
    """
    scenario = _scenario(tmp_path / "clock", verdict=Verdict.NO_GO)
    log = _log_with_history(
        scenario,
        "out-of-order.jsonl",
        _earlier_run(
            scenario,
            tag="future",
            verdict=Verdict.GO,
            created="2027-01-01T00:00:00.000000+00:00",
        ),
    )
    model = _model_from(log)
    html = _html(model)

    assert model.verdict_word == Verdict.NO_GO
    assert _series(model)[-1].verdict == Verdict.NO_GO, (
        "series[-1] must stay the run the banner reports; it is read in log order"
    )

    markers = _timeline_markers(html)
    assert len(markers) == 2, markers
    by_x = sorted(markers, key=lambda one: float(one["x"]))
    assert by_x[-1]["data-created"].startswith("2027"), (
        f"the chart is not sorted by time, so this fixture no longer separates "
        f"write order from clock order: {[one['data-created'] for one in markers]}"
    )
    assert by_x[-1]["class"] != by_x[0]["class"], "the two runs must be visibly different"

    visible = _squeeze(_visible(html))
    assert "reports the most recent of these runs" not in visible, (
        "the document claims the banner reports the newest run on the chart. On "
        "this log it reports the oldest one, and the newest marker carries the "
        "opposite verdict. Say 'the last comparison this log records' -- which is "
        "true of every log -- or say nothing"
    )
    assert "last comparison this log records" in visible, (
        "the run-history prose must still say which of the markers the banner "
        "above it describes; dropping the sentence trades a false claim for none"
    )


def test_the_thresholds_prose_names_a_section_the_full_path_is_actually_in(
    tmp_path: Path,
) -> None:
    """Shortening to a basename is only honest if the pointer is right.

    The merged text sent the reader to "the provenance block" for the full path.
    The provenance block carries the evidence log's path and the config *hash*;
    it has never carried ``config_path``. So the one sentence justifying the
    shortening pointed at the one place the path is not.
    """
    model, html = _rendered(tmp_path / "pointer")
    config = model.config_path
    assert config, "this fixture needs a recorded config path"

    footer = html[html.find('<footer id="provenance">') :]
    assert config not in footer, (
        "the provenance block now carries the config path; if that was added "
        "deliberately this test and the prose beneath the thresholds table are "
        "the two places that argue from its absence"
    )
    compared = html[html.find('<h2 id="compared">') : html.find('<h2 id="thresholds"')]
    assert config in compared, "the full path must stay in 'What was compared'"

    visible = _squeeze(_visible(html))
    assert "again in the provenance block" not in visible, (
        "the thresholds prose still sends a reviewer to the provenance block for "
        "a path that is not there"
    )
    assert "What was compared" in visible


def test_the_banner_bar_draws_the_floor_and_the_interval_and_not_only_the_rate(
    tmp_path: Path,
) -> None:
    """Three numbers reach ``interval_bar_svg``; the merged suite pinned one.

    Dropping ``floor=point.floor`` or ``interval=point.interval`` from
    ``_banner_bar`` left all 347 tests green. The floor is the only thing in the
    picture that makes the rate mean anything -- the bar exists to show whether
    the candidate cleared the gate -- and ``interval_bar_svg`` renders a missing
    floor as *no line at all*, so the loss is a silently emptier picture rather
    than a visibly wrong one.
    """
    model, html = _rendered(tmp_path / "bar-values")
    point = model.series[-1]
    assert point.floor is not None, "this fixture needs a recorded floor"
    assert point.interval is not None, "this fixture needs a recorded interval"

    opened = html.find('<div class="bar">')
    bar = html[opened : html.find("</div>", opened)]

    assert '<line class="floor"' in bar, (
        f"the banner bar does not draw the floor {point.floor} the run was held "
        f"to; a rate with no gate beside it is a number, not a verdict"
    )
    assert f'data-value="{point.floor:.6f}"' in bar, (
        f"the banner bar draws a floor line at some other value than {point.floor}"
    )
    assert f'data-value="{point.interval[0]:.6f}"' in bar, (
        "the banner bar does not draw the interval's lower end"
    )
    assert f'data-value-upper="{point.interval[1]:.6f}"' in bar, (
        "the banner bar does not draw the interval's upper end"
    )
    assert _pct_in(bar, point.floor), (
        "the bar's accessible title must speak the floor too, or a screen-reader "
        "user is told the rate and not the rule"
    )
    assert "candidate" in bar and point.judge_name in bar, (
        "the bar's accessible name must say which side and which judge it draws"
    )


def test_the_run_history_chart_draws_a_marker_for_every_run_it_counts(
    tmp_path: Path,
) -> None:
    """The chunk's headline feature, which the merged suite did not pin at all.

    Replacing ``timeline_svg(tuple(points))`` with ``timeline_svg(())`` in the
    filter left 347 tests green: the document rendered a heading reading
    "Run history -- 2 comparison(s) in this log" above a chart reading "No dated
    runs to plot", and nothing anywhere noticed. A heading that counts runs over a
    picture that draws none is worse than no picture.
    """
    scenario = _scenario(tmp_path / "drawn")
    log = _log_with_history(scenario, "two.jsonl", _earlier_run(scenario, tag="one"))
    model = _model_from(log)
    html = _html(model)

    assert len(_series(model)) == 2
    markers = _timeline_markers(html)
    assert len(markers) == 2, (
        f"the chart draws {len(markers)} marker(s) for the {len(_series(model))} "
        f"run(s) its own heading counts"
    )
    assert "No dated runs to plot" not in html
    drawn = {one["data-created"] for one in markers}
    assert drawn == {point.created for point in _series(model)}, (
        f"the markers are not this log's runs: drew {sorted(drawn)}"
    )


def test_a_model_with_no_series_renders_no_run_history_section_at_all(
    tmp_path: Path,
) -> None:
    """The contract's presence rule, which nothing tested.

    "timeline -- present when ``len(model.series) >= 1``". Removing the guard left
    the suite green and put an empty chart, a nav entry and a heading reading
    "0 comparison(s) in this log" into every document built by some route other
    than ``from_evidence`` -- which is a placeholder, and this document's own
    argument is that a placeholder is worse than an absence.
    """
    model, _ = _rendered(tmp_path / "guard")
    empty = dataclasses.replace(model, series=())
    html = _html(empty)

    assert '<h2 id="timeline">' not in html, "an empty series still renders a chart"
    assert 'href="#timeline"' not in html, "an empty series still gets a nav entry"
    assert "migkit-timeline" not in html
    assert '<div class="bar">' in html, (
        "the banner's bar must still render for a model with no series; "
        "interval_bar_svg draws each absent value as its own named picture"
    )
    assert _urls(html) == ()


def test_the_nav_offers_the_run_history_exactly_when_there_is_one(
    tmp_path: Path,
) -> None:
    """A section with no way to reach it is a section half the readers never see."""
    model, html = _rendered(tmp_path / "nav")
    assert model.series
    nav = html[html.find("<nav>") : html.find("</nav>")]
    assert nav.count('href="#timeline"') == 1, (
        "the run-history section is rendered but the contents list does not offer it"
    )


def test_a_half_scripted_run_keeps_the_table_and_names_the_side_it_could_not_measure(
    tmp_path: Path,
) -> None:
    """The suppression is ``and``, and the per-side cells exist for this run.

    Turning it into ``or`` left the suite green, because nothing rendered a run
    with one scripted side and one real one -- so the two per-side branches inside
    the table were unreachable from any test. Under ``or`` a real, measured
    candidate loses its latency numbers because the *baseline* was scripted, which
    hides a measurement rather than an absence.
    """
    model, html = _rendered(
        tmp_path / "half",
        baseline_adapter="FakeAdapter",
        candidate_adapter="OpenAICompatAdapter",
    )
    assert model.baseline.is_fake
    assert not model.candidate.is_fake

    start = html.find('<h2 id="latency"')
    end = html.find('<h2 id="flips"', start + 1)
    section = html[start:end]
    assert "<table" in section, (
        "a run with one measured side lost its latency table; the suppression is "
        "for a run where neither side was measured"
    )
    assert "scripted adapter" in section, (
        "the scripted side is printed as a number rather than named as unmeasured"
    )
    assert f"{model.candidate.latency_median:.3f}" in section, (
        "the measured side's median is missing from the table"
    )
    assert f"{model.baseline.latency_median:.3f}" not in section, (
        "the scripted side's timing is printed anyway, which is the 0.000 this "
        "chunk exists to stop printing"
    )


def test_a_threshold_source_that_is_not_a_path_is_printed_whole() -> None:
    """``_source_label`` shortens paths only, and that guard was untested.

    Making it shorten unconditionally left the suite green, because the only
    non-path value the fixtures produce is ``THRESHOLD_SOURCE_UNRECORDED``, which
    has no separator in it to be cut at. A source recorded as prose -- and the
    docstring argues from exactly this -- would be truncated at its last slash and
    read as a filename.
    """
    label = _report._source_label
    prose = "CLI flag --pass-rate-floor, overriding config/migkit.toml"
    assert label(prose) == prose, (
        "a sentence containing a slash is not a path and must not be cut at one"
    )
    assert label(_report.THRESHOLD_SOURCE_UNRECORDED) == _report.THRESHOLD_SOURCE_UNRECORDED
    assert label("migkit.toml") == "migkit.toml"
    assert label("") == ""
    assert label(None) == ""

    backslash = chr(92)
    assert label("C:" + backslash + "work" + backslash + "migkit.toml") == "migkit.toml"
    assert label("/etc/migkit/migkit.toml") == "migkit.toml"


def test_the_run_history_gaps_are_a_list_beside_the_paragraph_not_inside_it(
    tmp_path: Path,
) -> None:
    """``<ul>`` inside ``<p>`` is not nesting a parser will honour.

    An HTML parser closes the open ``<p>`` when it meets ``<ul>`` and drops the
    ``</p>`` that follows as a stray end tag, so the list loses the ``.secondary``
    styling the paragraph was carrying, and the document is invalid where it says
    it is auditable.
    """
    scenario = _scenario(tmp_path / "gaps")
    log = _log_with_history(
        scenario,
        "gappy.jsonl",
        _earlier_run(scenario, tag="norate", pass_rate=None),
    )
    html = _html(_model_from(log))
    start = html.find('<h2 id="timeline">')
    end = html.find('<h2 id="judges">', start)
    section = html[start:end]
    assert "<ul" in section, "this fixture is supposed to produce a counted gap"

    # Raw markup, not the parsed tree: the parser is the thing that *repairs*
    # this, so asking it what it saw would report the repaired document rather
    # than the one written to disk.
    depth = 0
    for token in re.findall(r"</?(?:p|ul)", section):
        if token == "<p":
            depth += 1
        elif token == "</p":
            depth -= 1
        elif token == "<ul":
            assert depth == 0, (
                "the gap list opens inside an unclosed <p>; a parser closes the "
                "paragraph there and drops the </p> that follows"
            )
    assert depth == 0, "the run-history section leaves a <p> unclosed"

    opened = section[section.find("<ul") :]
    assert opened.split(">")[0].endswith('class="secondary"'), (
        "the gap list must keep the secondary styling the paragraph gave it"
    )


def test_the_printed_figure_is_read_back_off_the_page_it_describes(
    tmp_path: Path,
) -> None:
    """The second number is a measurement, and the suite never measured it.

    ``_printed_chars`` was pinned only by ``printed < produced``. Under that
    assertion one side of the sum can stop collapsing -- the mutation is a single
    identifier -- and the document goes on printing a "characters printed below"
    figure that is larger than the text below it, with 359 tests green. A number
    a reader uses to reconcile two claims is worth exactly as much as the check
    that it matches the page.

    So the draws-and-input half is re-derived from the rendered ``<pre>`` blocks,
    which is the document itself and not another reading of the model.
    """
    repeated = "the candidate said exactly this, five times over"
    model, html = _rendered(tmp_path / "measured", candidate_output=repeated)

    rows = [
        row
        for row in (*model.flips, *model.gains, *model.unstable)
        if row.detail_embedded
    ]
    assert rows, "this fixture needs embedded rows"
    assert any(len(set(row.candidate_outputs)) == 1 for row in rows), (
        "nothing collapsed, so an uncollapsed sum would agree and this is vacuous"
    )

    reasons = sum(len(text) for row in rows for text in row.reasons.values())
    on_the_page = sum(len(text) for text in _pre_texts(html))

    # This fixture collapses the candidate only -- ``_scenario`` gives the
    # baseline a distinct suffix per draw. The baseline half of the same sum is
    # pinned in ``tests/test_report_scale.py``, on the fixture where both sides
    # are byte-identical; neither fixture alone reaches both terms.

    assert _report._printed_chars(model) - reasons == on_the_page, (
        f"the document says it prints "
        f"{_report._printed_chars(model) - reasons:,} characters of inputs and "
        f"draws; its <pre> blocks hold {on_the_page:,}. One of the two sides "
        f"stopped collapsing, and the figure beside the budget is now wrong in "
        f"the direction that overstates what a reader can see"
    )


# --------------------------------------------------------------------------- #
# 21. The dimension matrix on the model. Plan C10 as restated, under R16.
#
# C21 (section 20) wired the *counting* into the one streaming pass and hung the
# raw `DimensionCounts` on the model. C10 is the matrix: cells rather than
# counts, the golden set's own tag order with the untagged bucket last, a
# baseline column against a per-candidate column set, both of R9's floors carried
# so a refused cell can say what it refused against, and the six ways there can
# be no matrix at all.
#
# `dimensions: DimensionMatrix` *replaces* `dimension_counts` -- R16.3, on the
# ground that keeping both would put the same facts on the model at two
# fidelities. `DimensionCell` carries `tag`, `passes`, `n` and `items`, so
# nothing `TagCount` held is lost.
#
# Written without reading the implementation. Every expected count is a literal
# computed by hand from the fixture; every expected *sentence* is taken from the
# place the contract says it must be quoted from, because a sentence written out
# again here would be the fourth copy of a disclosure that is only allowed one.
#
# What is deliberately not re-litigated here: the arithmetic of `dimension_cell`
# and of `DimensionCounts`, which `tests/test_dimensions.py` owns, and the
# wiring of the tally into the pass, which section 20 owns.
# --------------------------------------------------------------------------- #


#: The two floors, written out rather than imported, so that a change to either
#: constant shows up as a failing expectation here and not as a test that quietly
#: agrees with whatever the module now says. R9 fixed both numbers:
#: `MIN_N_FOR_A_VERDICT = 20` completions and `MIN_ITEMS_FOR_A_VERDICT = 10`
#: distinct items. `test_the_floors_this_section_hard_codes_are_the_ones_dimensions_exports`
#: guards the pair against the module.
MIN_N = 20
MIN_ITEMS = 10


def test_the_floors_this_section_hard_codes_are_the_ones_dimensions_exports() -> None:
    """Guards the oracle: if this fails, every floor expectation below is wrong.

    The same shape as
    :func:`test_the_hashing_oracle_agrees_with_the_projects_stated_convention` --
    the literals are what the tests assert against, and this is the one place they
    are checked against the module that defines them.
    """
    assert (MIN_N, MIN_ITEMS) == (MIN_N_FOR_A_VERDICT, MIN_ITEMS_FOR_A_VERDICT)


# -- fixtures ---------------------------------------------------------------- #


def _matrix_log(
    scenario: Scenario,
    name: str,
    *,
    judging: Sequence[Mapping[str, Any]],
    before: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """``scenario``'s log with an arbitrary judging pass in the middle of it.

    ``_counted_log`` in section 20 writes the one judging pass its own tests
    need. This section needs several shapes of broken and short pass, so the
    records go in from the caller -- everything around them is the same log.
    """
    records: list[Mapping[str, Any]] = [
        _record(EVENT_RUN_STARTED, {"model_id": BASELINE_MODEL}, TS_JUDGING)
    ]
    records.extend(before)
    records.extend(judging)
    records.append(_record(EVENT_COMPARISON, scenario.comparison, TS_COMPARISON))
    if scenario.verdict is not None:
        records.append(_record(EVENT_VERDICT, scenario.verdict, TS_VERDICT))
    return _write_evidence(scenario.root / name, records)


def _both_sides(scenario: Scenario, *, draws: int = N_PER_ITEM) -> list[dict[str, Any]]:
    """Both sides judged in full: the baseline passes everything, the candidate fails.

    The same asymmetry the judged artifacts already encode, and the reason a cell
    counted under the wrong column is legible rather than a coincidence.
    """
    return [
        *_judging_pass(BASELINE_MODEL, scenario.items, passed=True, draws=draws),
        *_judging_pass(CANDIDATE_MODEL, scenario.items, passed=False, draws=draws),
    ]


def _mixed_pass(
    model_id: str,
    item_ids: Sequence[str],
    *,
    passing: Sequence[str],
    draws: int = N_PER_ITEM,
) -> list[Mapping[str, Any]]:
    """One side that passes some items and fails the rest, closed once.

    ``_judging_pass`` passes or fails a whole side, which is enough while a log
    holds two models and they are each other's opposite. A third model needs to be
    distinguishable from *both* of them, and "passed some of them" is the only
    remaining answer -- so its cells differ from the baseline's on one tag and from
    the candidate's on the other.

    One ``migkit.judging_completed`` for the whole side, not one per outcome: the
    counter attributes a group of verdicts to the model whose close follows them,
    and two closes for one model is a shape a real run never writes.
    """
    records: list[Mapping[str, Any]] = [
        _dim_verdict(item_id, passed=item_id in set(passing))
        for item_id in item_ids
        for _ in range(draws)
    ]
    records.append(
        _record(
            EVENT_JUDGING_COMPLETED,
            {
                "model_id": model_id,
                "graded": {J: len(item_ids) * draws},
                "imputed": {},
                "parse_failures": {},
            },
            TS_JUDGING,
        )
    )
    return records


def _retag(scenario: Scenario, tags_by_id: Mapping[str, Sequence[str]]) -> Scenario:
    """Rewrite the scenario's golden set with different tags, ids and inputs unchanged.

    The join is by input text, so leaving every ``input`` exactly as
    ``_default_items`` wrote it keeps every verdict in this section joinable while
    the tag universe moves. The recorded hash on the comparison payload is moved
    with the file, because a set that no longer matches is a *different* test --
    it is the first of the six refusals below, and it must not leak into the ones
    that are about tags.
    """
    items = [
        {
            "id": item_id,
            "input": f"INPUT-TEXT for {item_id}",
            "tags": list(tags_by_id[item_id]),
        }
        for item_id in scenario.items
    ]
    golden = _write_goldenset(scenario.goldenset, items)
    scenario.comparison["goldenset_hash"] = golden.hash
    scenario.goldenset_hash = golden.hash
    return scenario


#: Four items a tag and four carrying none, over the standard twelve. Chosen so
#: that the golden set's tag order and the alphabetical order of its tags are the
#: same order -- what this fixture is for is the *untagged* bucket's position, and
#: a fixture that also moved the real tags would be asserting two rulings at once
#: while only one of them is written down.
MIXED_TAGS: Mapping[str, Sequence[str]] = {
    **{item_id: ("arithmetic",) for item_id in ITEM_IDS[:4]},
    **{item_id: ("extraction",) for item_id in ITEM_IDS[4:8]},
    **{item_id: () for item_id in ITEM_IDS[8:]},
}

#: Every item untagged. "You tagged nothing" is a different fact from "the golden
#: set is gone", so this one is available and has exactly one row.
NO_TAGS: Mapping[str, Sequence[str]] = {item_id: () for item_id in ITEM_IDS}

#: Every item under one tag, so a pass at one draw an item produces a tag that
#: clears the item floor and fails the completions floor -- the case a single
#: combined floor could not see.
ONE_TAG: Mapping[str, Sequence[str]] = {item_id: ("solo",) for item_id in ITEM_IDS}

#: ``_default_items`` alternates two tags over twelve items, so each tag holds six
#: items and, at five draws an item, thirty completions.
DEFAULT_TAG_ITEMS = 6
DEFAULT_TAG_N = DEFAULT_TAG_ITEMS * N_PER_ITEM

#: Eight items under one tag and four under the other, so **no two cells in a
#: column hold the same three numbers**. Every other fixture in this section
#: splits the twelve evenly, and R27.4 records what that cost: ``TagColumn.cell()``
#: returning the first cell whose tag does *not* match survived all 1998 tests,
#: because the wrong cell and the right one were the same cell by value. This is
#: C5's M01 exactly -- a fixture set that hard-codes one value everywhere cannot
#: tell the correct computation from the broken one.
SPLIT_TAGS: Mapping[str, Sequence[str]] = {
    **{item_id: ("arithmetic",) for item_id in ITEM_IDS[:8]},
    **{item_id: ("extraction",) for item_id in ITEM_IDS[8:]},
}
#: The two halves of every uneven split in this section, so that a fixture whose
#: two tags must differ can say which of them is which.
LARGER_TAG_ITEMS = 8
SMALLER_TAG_ITEMS = 4
LARGER_TAG_N = LARGER_TAG_ITEMS * N_PER_ITEM
SMALLER_TAG_N = SMALLER_TAG_ITEMS * N_PER_ITEM

#: Two tags whose alphabetical order is the reverse of the order they are written
#: in, so the ordering claim is asserted on a fixture that can fail it. R27.3
#: replaced the contract's "golden-set tag order" with "alphabetical, ``UNTAGGED``
#: last", and every other fixture here names its tags in alphabetical order
#: already, which is a fixture that agrees with any ordering rule at all.
#: Split eight/four for the same reason ``SPLIT_TAGS`` is: two rows carrying the
#: same three numbers cannot tell a reordering from a relabelling.
ZETA_TAGS: Mapping[str, Sequence[str]] = {
    **{item_id: ("zeta",) for item_id in ITEM_IDS[:8]},
    **{item_id: ("alpha",) for item_id in ITEM_IDS[8:]},
}

#: A third model, judged in the same log. R27.4: ``candidates`` was a 1-tuple in
#: every test anywhere, so its plurality was untested and both "reverse the
#: candidate order" and "make the extra models non-deterministic" survived.
#:
#: The id sorts **between** the baseline's and the candidate's -- ``model-a-`` <
#: ``model-a2-`` < ``model-b-``, because ``'-' < '2' < 'b'``. That is the whole
#: point of the name: the contract says the comparison's candidate comes first and
#: the rest follow in sorted order, so a matrix that simply sorted everything that
#: is not the baseline would put this model in front of the candidate, and a
#: fixture whose third model sorted last could not see the difference.
THIRD_MODEL = "model-a2-20260101"

#: And a fourth, judged *after* the third and sorting *before* it. The contract is
#: "the payload's candidate first, then the rest in sorted order", and with only
#: one extra model there is no difference between sorted order and the order the
#: counter happened to file them in. Two extras is the smallest fixture in which
#: dropping the ``sorted()`` shows.
FOURTH_MODEL = "model-a1-20260101"


# -- accessors. As everywhere in this file, they adapt to names, never values -- #


def _matrix(model: Any) -> Any:
    return _get(model, "dimensions")


def _available_matrix(model: Any) -> Any:
    matrix = _matrix(model)
    assert _get(matrix, "available") is True, _get(matrix, "reason")
    assert _get(matrix, "reason") == "", (
        f"an available matrix carries no refusal, and this one says {_get(matrix, 'reason')!r}"
    )
    return matrix


def _cells(column: Any) -> tuple[Any, ...]:
    """Every cell in one column, whatever shape a column turned out to be.

    A column is **not** a mapping. ``DimensionCounts`` files the hazard on its own
    ``by_model``: the mapping has a real ``.items()`` and ``DimensionCell.items``
    is an int, so ``column.items`` and ``cell.items`` are one keystroke apart and
    both work -- one is a number, the other a bound method printed into the page.
    ``report.py:1206`` already renamed a field to escape exactly this, and a
    mapping is the one thing that cannot be renamed away. So the shape is a tuple
    of cells, or something frozen holding one, and this refuses a mapping rather
    than reaching through it.
    """
    assert not isinstance(column, Mapping), (
        f"a matrix column is a {type(column).__name__}, which is a Mapping: "
        f"`column.items` in the template is then dict.items and renders as a bound "
        f"method, one keystroke from `cell.items` which is an int"
    )
    if isinstance(column, tuple):
        return column
    inner = _get(column, "cells")
    assert isinstance(inner, tuple), f"a column's cells are a tuple; got {type(inner).__name__}"
    return inner


def _tags_of(column: Any) -> tuple[str, ...]:
    return tuple(_get(one, "tag") for one in _cells(column))


def _cell(column: Any, tag: str) -> Any:
    for one in _cells(column):
        if _get(one, "tag") == tag:
            return one
    raise AssertionError(f"no cell for tag {tag!r} in a column holding {list(_tags_of(column))}")


def _cell_counts(column: Any, tag: str) -> tuple[int, int, int]:
    """``(passes, n, items)`` for one tag, the three counts a reader is shown."""
    one = _cell(column, tag)
    return (_get(one, "passes"), _get(one, "n"), _get(one, "items"))


def _baseline_column(matrix: Any) -> Any:
    return _get(matrix, "baseline")


def _candidates(matrix: Any) -> tuple[Any, ...]:
    """The candidate columns, in the order the matrix publishes them.

    These three helpers each used to accept a ``Mapping`` of columns and reach
    through it, which is how a regression from ``tuple[TagColumn, ...]`` back to
    ``Mapping[str, TagColumn]`` survived all of C10's tests: it died only in
    section 20, and only because ``column()`` unpacked the dict to its keys and
    crashed on ``str.model_id``. A crash is not an assertion, and refactoring
    ``column()`` would have reopened the hazard silently (R27.4). The shape is
    settled now, so the fallback is gone and
    ``test_the_candidate_columns_are_a_tuple_and_never_a_mapping_of_them`` asserts
    it directly.
    """
    candidates = _get(matrix, "candidates")
    assert isinstance(candidates, tuple), (
        f"the matrix's candidates are a {type(candidates).__name__}; the contract "
        f"says tuple[TagColumn, ...], and a Mapping puts `.items` back within one "
        f"keystroke of `cell.items`"
    )
    return candidates


def _candidate_ids(matrix: Any) -> list[str]:
    return sorted(str(_get(one, "model_id")) for one in _candidates(matrix))


def _candidate_column(matrix: Any, model_id: str) -> Any:
    for one in _candidates(matrix):
        if _get(one, "model_id") == model_id:
            return one
    raise AssertionError(
        f"no candidate column for {model_id!r}; the matrix holds {_candidate_ids(matrix)}"
    )


def _all_columns(matrix: Any) -> list[Any]:
    return [_baseline_column(matrix), *_candidates(matrix)]


def _counter_reason(log: Path, goldenset: Path, judge: str = J) -> str:
    """What ``dimensions`` itself says about this log, driven the way report must.

    Not a re-derivation of the value under test: the value under test is
    ``from_evidence``'s wiring, and this is the module it is required to quote
    *verbatim*. Written as the two-phase form rather than as ``dimension_counts``
    because the two-phase form is the one a single streaming pass can use (R16.1),
    so this is the same sentence produced by the same code path.

    Asserting the sentence rather than a keyword is the point. "Contains the
    judge's name" would pass a re-worded refusal, and a re-worded refusal is the
    third copy of a disclosure that already has two.
    """
    tally = DimensionTally()
    for record in stream_records(log):
        tally.add(record)
    counts = tally.counts({item.id: item for item in GoldenSet.load(goldenset)}, judge=judge)
    assert counts.available is False, (
        "this fixture was built to make the counter decline and it did not, so the "
        "test using it is asserting nothing"
    )
    assert counts.reason, "the counter declined without saying why, which it may not do"
    return counts.reason


# -- the field, and what shape it is ----------------------------------------- #


def test_the_model_carries_a_dimension_matrix_in_place_of_the_raw_counts(
    tmp_path: Path,
) -> None:
    """R16.3: ``dimensions`` *replaces* ``dimension_counts``; it does not sit beside it.

    Keeping both would put the same facts on the model at two fidelities, which is
    two chances for them to disagree -- the identical reasoning the contract gives
    for never re-wording a decline reason. ``DimensionCell`` carries ``tag``,
    ``passes``, ``n`` and ``items``, so the matrix subsumes every fact
    ``TagCount`` held and nothing is lost by the removal.
    """
    scenario = _scenario(tmp_path / "field")
    model = _from_evidence(scenario)
    names = {one.name for one in dataclasses.fields(model)}

    assert "dimensions" in names, (
        f"C10 gives ReportModel a `dimensions` field; it carries {sorted(names)}"
    )
    assert "dimension_counts" not in names, (
        "`dimension_counts` is still on the model beside `dimensions`: the same "
        "per-tag facts at two fidelities, which R16.3 rules out"
    )


def test_the_matrix_is_the_frozen_dimension_matrix_the_contract_declares(
    tmp_path: Path,
) -> None:
    """The type is named and public, and an instance cannot be edited after the fact.

    Frozen because everything else the reconstruction hands the renderers is
    frozen: a document assembled from a mutable table is a document a filter can
    quietly rewrite between the banner and the appendix.
    """
    scenario = _scenario(tmp_path / "type")
    matrix = _matrix(_from_evidence(scenario))
    declared = _get(_module(), "DimensionMatrix")

    assert isinstance(matrix, declared), (
        f"`dimensions` is a {type(matrix).__name__}, not the DimensionMatrix the contract declares"
    )
    assert dataclasses.is_dataclass(matrix)
    with pytest.raises(dataclasses.FrozenInstanceError):
        matrix.available = False


def test_a_matrix_column_is_not_a_mapping_whose_items_is_a_bound_method(
    tmp_path: Path,
) -> None:
    """The Reviewer's third note, which is the one that is new and the one that ships.

    ``cell.items`` is an int -- how many distinct golden-set questions stand behind
    the cell -- and on a mapping ``column.items`` is ``dict.items``. Both spell
    correctly, both type-check, and in a Jinja template the wrong one renders as
    ``<built-in method items of dict object at 0x...>`` in the middle of a
    published document. ``report.py:1206`` fixed this once by renaming a field;
    a mapping is the case where renaming is not available, so the shape has to
    change instead.
    """
    scenario = _scenario(tmp_path / "notamapping")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-shape.jsonl")))

    for column in _all_columns(matrix):
        assert not isinstance(column, Mapping), (
            f"a column is a {type(column).__name__}, a Mapping: `column.items` is "
            f"dict.items and `cell.items` is an int, and the two are one keystroke "
            f"apart"
        )
        assert not callable(getattr(column, "items", None)), (
            f"`column.items` on a {type(column).__name__} is callable, so a template "
            f"writing it prints a bound method where a count was meant"
        )


def test_the_matrix_names_both_floors_it_refused_its_cells_against(tmp_path: Path) -> None:
    """``min_items`` is not decoration, and neither of the two is the other's proxy.

    A document that refuses a cell has to be able to say what it refused against,
    and R9 gave it two floors to refuse against because neither subsumes the
    other: twelve items at one draw each clears the item floor and fails the
    completions floor, and four items at five draws each does the reverse.
    """
    scenario = _scenario(tmp_path / "floors")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-floors.jsonl")))

    assert _get(matrix, "min_n") == MIN_N
    assert _get(matrix, "min_items") == MIN_ITEMS


# -- the columns ------------------------------------------------------------- #


def test_the_baseline_column_is_the_side_the_comparison_payload_calls_the_baseline(
    tmp_path: Path,
) -> None:
    """Which side is which comes from the payload, never from position in ``by_model``.

    ``dimension_counts`` keys by ``model_id`` and does not know which side is
    which, so a matrix that took the first key it found would be right by
    accident on a dict that happened to be ordered the useful way. The baseline
    passes every draw in this log and the candidate fails every draw, so a
    swapped pair is not a near miss -- it reports the regression backwards.
    """
    scenario = _scenario(tmp_path / "sides")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-sides.jsonl")))

    assert _cell_counts(_baseline_column(matrix), "arithmetic") == (
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    ), "the baseline column holds the candidate's numbers: the two sides are swapped"
    assert _cell_counts(_candidate_column(matrix, CANDIDATE_MODEL), "arithmetic") == (
        0,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    )


def test_the_candidate_columns_are_keyed_by_model_and_hold_no_second_baseline(
    tmp_path: Path,
) -> None:
    """The baseline has its own column and does not also appear among the candidates.

    A baseline listed twice is a comparison of a model against itself sitting
    beside the real one, and on a two-model run the duplicate reads as a third
    result nobody ran.
    """
    scenario = _scenario(tmp_path / "keys")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-keys.jsonl")))

    assert _candidate_ids(matrix) == [CANDIDATE_MODEL], (
        f"the candidate columns are {_candidate_ids(matrix)}; the baseline "
        f"{BASELINE_MODEL!r} has its own column and belongs in no other"
    )


def test_the_candidate_columns_are_a_tuple_and_never_a_mapping_of_them(
    tmp_path: Path,
) -> None:
    """The shape, asserted rather than inferred from something that crashes.

    R27.4: regressing ``candidates`` to ``Mapping[str, TagColumn]`` survived all
    twenty-two of this section's tests. It died in section 20 alone, and only
    because ``DimensionMatrix.column()`` iterates ``candidates`` and a dict yields
    its *keys*, so the loop asked a ``str`` for ``.model_id`` and crashed. That is
    an incidental crash, not an assertion: rewrite ``column()`` to iterate
    ``.values()`` when it is handed a mapping -- a reasonable-looking fix -- and
    the hazard reopens with nothing anywhere to notice.

    What the shape is protecting is in
    ``test_a_matrix_column_is_not_a_mapping_whose_items_is_a_bound_method``: a
    mapping's ``.items`` is a bound method and a cell's ``.items`` is an int.
    """
    scenario = _scenario(tmp_path / "tupleshape")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-tuple.jsonl")))
    candidates = _get(matrix, "candidates")

    assert isinstance(candidates, tuple), (
        f"`candidates` is a {type(candidates).__name__}; the contract says "
        f"tuple[TagColumn, ...]"
    )
    assert not isinstance(candidates, Mapping)
    assert candidates, "this fixture judged a candidate, so the tuple is not empty"
    for one in candidates:
        assert _get(one, "model_id"), "a column in the tuple carries no model id"


def test_a_columns_cell_lookup_answers_for_the_tag_it_was_asked_about(
    tmp_path: Path,
) -> None:
    """``TagColumn.cell(tag)`` returns *that* tag's cell, on a column where it matters.

    R27.4, and C5's M01 a second time: ``cell()`` returning the first cell whose
    tag does not match survived all 1998 tests, because every fixture in the file
    gave both tags identical counts and the wrong answer was numerically the right
    one. Eight items under ``arithmetic`` and four under ``extraction`` here, so
    the two cells of one column disagree in all three numbers and a lookup that
    ignores its argument is legible.

    Driven through the production method rather than through this section's
    ``_cell`` helper, because the method is the thing under test -- ``_cell``
    walks the tuple itself and would pass over a broken ``cell()``.
    """
    scenario = _retag(_scenario(tmp_path / "identity"), SPLIT_TAGS)
    log = _matrix_log(scenario, "evidence-identity.jsonl", judging=_both_sides(scenario))
    matrix = _available_matrix(_model_from(log))
    column = _baseline_column(matrix)

    assert _cell_counts(column, "arithmetic") != _cell_counts(column, "extraction"), (
        "the two tags hold the same counts in this fixture, so a cell lookup that "
        "returned the wrong one would be indistinguishable from a correct one"
    )
    for tag, expected in (
        ("arithmetic", (LARGER_TAG_N, LARGER_TAG_N, LARGER_TAG_ITEMS)),
        ("extraction", (SMALLER_TAG_N, SMALLER_TAG_N, SMALLER_TAG_ITEMS)),
    ):
        one = column.cell(tag)
        assert one is not None, f"the column has no cell for {tag!r}"
        assert _get(one, "tag") == tag, (
            f"`cell({tag!r})` came back with the cell for {_get(one, 'tag')!r}"
        )
        assert (_get(one, "passes"), _get(one, "n"), _get(one, "items")) == expected

    assert column.cell("no-such-tag") is None, (
        "a tag that was in no golden set has to come back as None rather than as a "
        "cell of zeros, which would say it was measured and produced nothing"
    )


def _extra_models_log(scenario: Scenario, name: str) -> Path:
    """``_counted_log``'s shape with two more models judged beside the two sides.

    The third passes the ``arithmetic`` items and fails the ``extraction`` ones and
    the fourth does the reverse, so all four columns hold different pairs of cells:
    each extra matches the baseline on one tag and the candidate on the other and
    neither of them overall. A matrix that dropped an extra, duplicated one column
    into another, or reordered the candidates has to show it.

    The fourth is judged last and sorts first, which is the only way to tell "the
    rest, sorted" from "the rest, in the order the counter filed them".
    """
    return _matrix_log(
        scenario,
        name,
        judging=[
            *_both_sides(scenario),
            *_mixed_pass(THIRD_MODEL, scenario.items, passing=scenario.items[::2]),
            *_mixed_pass(FOURTH_MODEL, scenario.items, passing=scenario.items[1::2]),
        ],
    )


def test_a_matrix_lookup_by_model_answers_for_the_model_it_was_asked_about(
    tmp_path: Path,
) -> None:
    """``DimensionMatrix.column(model_id)`` reaches both sides, and the right one.

    The same shape as the cell lookup above and the same fixture problem: with two
    models whose counts differ only in ``passes``, a ``column()`` that returned
    the baseline whatever it was asked is caught, but a log with more than one
    candidate is what makes "the first candidate" and "some candidate"
    distinguishable.
    """
    scenario = _scenario(tmp_path / "columnlookup")
    matrix = _available_matrix(_model_from(_extra_models_log(scenario, "evidence-lookup.jsonl")))

    for model_id in (BASELINE_MODEL, CANDIDATE_MODEL, THIRD_MODEL, FOURTH_MODEL):
        column = matrix.column(model_id)
        assert column is not None, f"the matrix has no column for {model_id!r}"
        assert _get(column, "model_id") == model_id, (
            f"`column({model_id!r})` came back with {_get(column, 'model_id')!r}"
        )

    assert matrix.column("model-nobody-ran") is None


def test_a_log_that_judged_four_models_carries_a_column_for_each_of_them(
    tmp_path: Path,
) -> None:
    """``candidates`` is plural, and until now no fixture anywhere made it plural.

    R27.4: it was a 1-tuple in every test in the suite, so nothing pinned that a
    third model reaches the page at all -- ``by_model`` holds every model a
    ``migkit.judging_completed`` named, and dropping one because the payload did
    not call it the candidate discards a column of real measurements in silence.

    Each extra model's two cells are asserted, and the two extras are each other's
    inverse: a matrix that filled every extra column from one model's counts would
    hold four columns and still be wrong about two of them.
    """
    scenario = _scenario(tmp_path / "extras")
    matrix = _available_matrix(_model_from(_extra_models_log(scenario, "evidence-extras.jsonl")))
    passed = (DEFAULT_TAG_N, DEFAULT_TAG_N, DEFAULT_TAG_ITEMS)
    failed = (0, DEFAULT_TAG_N, DEFAULT_TAG_ITEMS)

    assert _candidate_ids(matrix) == sorted([CANDIDATE_MODEL, THIRD_MODEL, FOURTH_MODEL]), (
        f"the matrix holds candidate columns for {_candidate_ids(matrix)}; this log "
        f"judged three models beside the baseline"
    )
    for model_id, arithmetic, extraction in (
        (CANDIDATE_MODEL, failed, failed),
        (THIRD_MODEL, passed, failed),
        (FOURTH_MODEL, failed, passed),
    ):
        column = _candidate_column(matrix, model_id)
        assert (
            _cell_counts(column, "arithmetic"),
            _cell_counts(column, "extraction"),
        ) == (arithmetic, extraction), (
            f"{model_id!r}'s column holds another model's numbers; no two sides in "
            f"this log passed the same items"
        )


def test_the_first_candidate_column_is_the_one_the_comparison_names(
    tmp_path: Path,
) -> None:
    """R27.8.1: ``candidates[0]`` is the comparison's candidate, by construction.

    C14 will read it that way, and C14 is told to read it that way rather than to
    be given a second accessor -- a second way to name one side is a second thing
    to disagree. So the construction has to be pinned here, and only a log with
    more than one candidate can pin it.

    Both extras sort *before* ``CANDIDATE_MODEL``, so "the payload's candidate
    first, then the rest in sorted order" and "everything that is not the baseline,
    sorted" put different models in front. And the fourth is judged after the third
    and sorts before it, so the tail tells sorted order from the order the counter
    filed them in -- with only one extra model those two are the same order and the
    ``sorted()`` is unfalsifiable.
    """
    scenario = _scenario(tmp_path / "candidate-first")
    matrix = _available_matrix(_model_from(_extra_models_log(scenario, "evidence-first.jsonl")))
    candidates = _candidates(matrix)

    assert FOURTH_MODEL < THIRD_MODEL < CANDIDATE_MODEL, (
        "the extra models no longer sort in front of the candidate and of each "
        "other in this order, so this test cannot tell payload order from sorted "
        "order or sorted order from log order"
    )
    assert [_get(one, "model_id") for one in candidates] == [
        CANDIDATE_MODEL,
        FOURTH_MODEL,
        THIRD_MODEL,
    ], (
        f"the candidate columns are {[_get(one, 'model_id') for one in candidates]}; "
        f"the comparison payload's candidate comes first and the rest follow in "
        f"sorted order"
    )


def test_an_extra_model_the_payload_never_named_is_still_counted_the_same_way(
    tmp_path: Path,
) -> None:
    """The extra column is a measurement, not a placeholder: it moves with its log.

    A column that is present but always reads the same is a column nobody can act
    on. This is the same log with the third model's outcomes inverted -- it fails
    the arithmetic items and passes the extraction ones -- and the two runs have to
    disagree in the cells that changed and agree in the ones that did not.
    """
    scenario = _scenario(tmp_path / "three-inverted")
    log = _matrix_log(
        scenario,
        "evidence-three-inverted.jsonl",
        judging=[
            *_both_sides(scenario),
            *_mixed_pass(THIRD_MODEL, scenario.items, passing=scenario.items[1::2]),
        ],
    )
    matrix = _available_matrix(_model_from(log))
    column = _candidate_column(matrix, THIRD_MODEL)

    assert _cell_counts(column, "arithmetic") == (0, DEFAULT_TAG_N, DEFAULT_TAG_ITEMS)
    assert _cell_counts(column, "extraction") == (
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    ), (
        "the third model's cells did not follow its verdicts: this log inverts the "
        "outcomes the fixture above records and the column reads the same"
    )


def test_a_side_that_was_judged_and_produced_nothing_is_a_column_of_zeros(
    tmp_path: Path,
) -> None:
    """Zeros are a finding; a missing column is a silence.

    The two columns are rendered next to each other, so a vanishing one turns a
    comparison into a single reading with nothing on the page to say where the
    other went. The candidate below was judged -- a ``migkit.judging_completed``
    names it -- and wrote no verdict at all.
    """
    scenario = _scenario(tmp_path / "zeros")
    judging = [
        *_judging_pass(BASELINE_MODEL, scenario.items, passed=True),
        _record(
            EVENT_JUDGING_COMPLETED,
            {"model_id": CANDIDATE_MODEL, "graded": {J: 0}, "imputed": {}, "parse_failures": {}},
            TS_JUDGING,
        ),
    ]
    log = _matrix_log(scenario, "evidence-zeros.jsonl", judging=judging)
    matrix = _available_matrix(_model_from(log))
    column = _candidate_column(matrix, CANDIDATE_MODEL)

    assert _tags_of(column) == _tags_of(_baseline_column(matrix)), (
        "the judged-but-silent side lost rows the other side has, so the two "
        "columns no longer line up beside each other"
    )
    assert [_cell_counts(column, tag) for tag in _tags_of(column)] == [
        (0, 0, 0),
        (0, 0, 0),
    ]
    assert _get(_cell(column, "arithmetic"), "verdict_refused") is True


def test_the_dimension_matrix_still_renders_when_the_artifacts_are_not_beside_the_log(
    tmp_path: Path,
) -> None:
    """The contract's named first-failing test, and R1 inverted its original answer.

    Everything the matrix is built from is in the evidence log and in the golden
    set. Neither is a run or judged artifact, so a reviewer opening a shared log
    on another machine with no artifact directory beside it -- which
    ``report.py``'s own docstring calls the designed workflow -- gets the whole
    matrix rather than a refusal.
    """
    scenario = _scenario(tmp_path / "stranger")
    log = _counted_log(scenario, "evidence-stranger.jsonl")
    for artifact in (tmp_path / "stranger").glob("*.judged.jsonl"):
        artifact.unlink()
    for artifact in (tmp_path / "stranger").glob("*.jsonl"):
        if artifact.name in {"baseline.jsonl", "candidate.jsonl"}:
            artifact.unlink()

    matrix = _available_matrix(_model_from(log))

    assert _cell_counts(_baseline_column(matrix), "arithmetic") == (
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    )
    assert _cell_counts(_candidate_column(matrix, CANDIDATE_MODEL), "extraction") == (
        0,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    )


# -- the tag order, and the sentinel ----------------------------------------- #


def test_the_tags_are_the_golden_sets_own_with_the_untagged_bucket_last(
    tmp_path: Path,
) -> None:
    """``UNTAGGED`` is the empty string, so every sort puts it *first* unless told.

    That is the whole reason the contract writes the position down. The counter
    hands its keys back through ``sorted(index.tags)``, and ``"" < "arithmetic"``,
    so a matrix that takes the counter's order without moving the bucket opens
    every table with a nameless row. Four items a tag here and four carrying none.
    """
    scenario = _retag(_scenario(tmp_path / "order"), MIXED_TAGS)
    log = _matrix_log(scenario, "evidence-order.jsonl", judging=_both_sides(scenario))
    model = _model_from(log)
    matrix = _available_matrix(model)

    assert tuple(model.goldenset["tags"]) == ("arithmetic", "extraction"), (
        "the golden set this fixture wrote does not hold the tags it claims to, so "
        "the order below is asserting nothing"
    )
    assert _get(matrix, "tags") == ("arithmetic", "extraction", UNTAGGED)
    assert _tags_of(_baseline_column(matrix)) == ("arithmetic", "extraction", UNTAGGED), (
        "the column's rows are in a different order from the matrix's tags, so the "
        "header and the body of the table disagree"
    )


def test_the_tags_are_alphabetical_on_a_set_whose_tags_are_not_written_that_way(
    tmp_path: Path,
) -> None:
    """R27.3 corrected the contract's phrase, and this is the fixture that can fail it.

    "Golden-set tag order" is not reachable from anything ``report.py`` sees:
    ``GoldenSet.stats()`` hands back ``dict(sorted(...))`` and the counter keys its
    inner mapping through ``sorted(index.tags)``, so a regression to *file* order is
    unimplementable and what is left to promise is alphabetical, ``UNTAGGED`` last.

    Every other fixture in this section names its tags in alphabetical order to
    begin with, which is a fixture that agrees with any ordering rule at all. Here
    ``zeta`` is written first and has to come out second.
    """
    scenario = _retag(_scenario(tmp_path / "zeta"), ZETA_TAGS)
    log = _matrix_log(scenario, "evidence-zeta.jsonl", judging=_both_sides(scenario))
    matrix = _available_matrix(_model_from(log))

    assert _get(matrix, "tags") == ("alpha", "zeta"), (
        f"the tags came back as {_get(matrix, 'tags')}; this golden set writes zeta "
        f"first and the matrix publishes them alphabetically"
    )
    assert _tags_of(_baseline_column(matrix)) == ("alpha", "zeta")
    assert _cell_counts(_baseline_column(matrix), "zeta") == (
        LARGER_TAG_N,
        LARGER_TAG_N,
        LARGER_TAG_ITEMS,
    ), "the rows were relabelled rather than reordered, so zeta holds alpha's counts"


def test_the_report_orders_the_tags_itself_and_does_not_inherit_the_counters_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zeta fixture above does not close this, and it was ruled that it would.

    R27.3 asked for a ``zeta``/``alpha`` fixture on the ground that deleting
    ``report.py``'s own ``sorted()`` and taking the counter's key order "is
    invisible only because ``dimensions.py`` happens to sort". The fixture is
    above; it does not close it, and that was measured rather than argued --
    ``dimensions.py`` keys every column through ``sorted(index.tags)``, so on
    *every* input ``from_evidence`` can build, the counter's key order and
    alphabetical order are the same order. The mutant survives the zeta fixture.

    Two modules agreeing today is not one module ordering for itself: the
    counter's contract is what its keys *mean*, not what order they arrive in, and
    a day it stops sorting is a day this table opens with a nameless row. So the
    counting is replaced with one that does not sort -- the one input no log can
    produce -- and everything downstream of it runs unchanged.
    """
    module = _module()
    scenario = _scenario(tmp_path / "unsorted")
    log = _counted_log(scenario, "evidence-unsorted.jsonl")
    by_model = {
        BASELINE_MODEL: {
            "zeta": TagCount(3, 3, 1),
            UNTAGGED: TagCount(2, 2, 1),
            "alpha": TagCount(1, 1, 1),
        },
        CANDIDATE_MODEL: {"middle": TagCount(0, 4, 1)},
    }

    def unsorted_counts(_tally: Any, _view: Any, _judge: str) -> Any:
        return DimensionCounts(available=True, reason="", by_model=by_model)

    monkeypatch.setattr(module, "_close_the_tally", unsorted_counts)
    matrix = _available_matrix(_model_from(log))

    assert tuple(by_model[BASELINE_MODEL]) != ("alpha", "middle", "zeta", UNTAGGED), (
        "the stubbed counts are already in the order the matrix must publish, so "
        "this test cannot tell the two apart"
    )
    assert _get(matrix, "tags") == ("alpha", "middle", "zeta", UNTAGGED), (
        f"the matrix published {_get(matrix, 'tags')}: it took the order the "
        f"counting handed it rather than ordering the tags for itself"
    )
    assert _tags_of(_baseline_column(matrix)) == ("alpha", "middle", "zeta", UNTAGGED)
    assert _cell_counts(_baseline_column(matrix), "zeta") == (3, 3, 1), (
        "the rows were relabelled rather than reordered, so a cell carries another "
        "tag's counts"
    )


def test_a_golden_set_in_which_every_item_is_untagged_is_available_and_not_a_refusal(
    tmp_path: Path,
) -> None:
    """The Reviewer's most likely subtle wrong: "no tags in the set" read as unavailable.

    "You tagged nothing" is a different fact from "the golden set is gone", and
    the two have different fixes. One row keyed by the sentinel is the honest
    rendering; a refusal here would tell an operator their evidence was
    unreadable when it was complete.
    """
    scenario = _retag(_scenario(tmp_path / "untagged"), NO_TAGS)
    log = _matrix_log(scenario, "evidence-untagged.jsonl", judging=_both_sides(scenario))
    matrix = _available_matrix(_model_from(log))

    assert _get(matrix, "tags") == (UNTAGGED,)
    assert _cell_counts(_baseline_column(matrix), UNTAGGED) == (
        len(ITEM_IDS) * N_PER_ITEM,
        len(ITEM_IDS) * N_PER_ITEM,
        len(ITEM_IDS),
    )


def test_the_untagged_row_is_keyed_by_the_sentinel_dimensions_exports(
    tmp_path: Path,
) -> None:
    """Imported, never typed as ``""`` inline, and the sentinel is empty on purpose.

    ``"untagged"`` is a legal tag. A golden set that used it would collide with
    this bucket and the collision would read as a larger slice rather than as an
    error, so the reserved key is the one string no tag can be -- and the only
    safe way to spell it is to import the name that carries that reasoning.
    """
    scenario = _retag(_scenario(tmp_path / "sentinel"), MIXED_TAGS)
    log = _matrix_log(scenario, "evidence-sentinel.jsonl", judging=_both_sides(scenario))
    matrix = _available_matrix(_model_from(log))

    assert _get(_cell(_baseline_column(matrix), UNTAGGED), "tag") == UNTAGGED
    assert "untagged" not in _get(matrix, "tags"), (
        "the untagged bucket is spelled as the word rather than as the reserved "
        "empty key, so a golden set that really used the tag `untagged` would "
        "silently merge into it"
    )


def test_the_report_module_names_the_untagged_sentinel_rather_than_typing_it() -> None:
    """The value assertions above cannot tell an import from a typed ``""``.

    They are the same string, which is exactly why the contract says to import it:
    an inline ``""`` is correct today and is one edit away from being wrong, with
    nothing at that edit site to say what the empty string meant.

    **Parsed rather than grepped, and R27.2 is why.** This was
    ``"UNTAGGED" in inspect.getsource(report)``. C10's reviewer replaced every
    executable use of the sentinel with ``""`` *and deleted the import*, and the
    test still passed -- because ``report.py``'s docstrings name ``UNTAGGED``
    several times and a docstring survives that regression untouched. A source-text
    assertion cannot tell code from commentary, and this project writes long
    docstrings, so the better a module is documented the weaker such a test gets.

    Matching ``Name`` and ``Attribute`` and nothing else is deliberate: it accepts
    both spellings the contract allows -- the bare name after a ``from ... import``
    and ``dimensions.UNTAGGED`` -- and rejects the one it does not, importing the
    sentinel and then typing ``""`` anyway, because the ``ImportFrom`` alias is not
    either node type.
    """
    tree = ast.parse(inspect.getsource(_module()))
    reached = any(
        (isinstance(node, ast.Name) and node.id == "UNTAGGED")
        or (isinstance(node, ast.Attribute) and node.attr == "UNTAGGED")
        for node in ast.walk(tree)
    )

    assert reached, (
        "report.py never evaluates UNTAGGED, so the untagged row is keyed by an "
        "inline empty string; dimensions.py exports the sentinel for exactly this "
        "and carries the comment explaining why it is empty rather than the word"
    )


# -- the floors, which are two and are independent --------------------------- #


def test_a_tag_that_clears_the_completions_floor_but_not_the_item_floor_is_refused(
    tmp_path: Path,
) -> None:
    """Thirty completions from six questions, and six questions is not a slice.

    The draws within an item are correlated by construction -- same prompt, same
    reference, same rubric clause -- so a dimension verdict that generalises over
    questions has six observations here and not thirty. The cell shows its
    interval and declines to colour it, and the shortfall it names is in items,
    which is the unit the reader can act on.
    """
    scenario = _scenario(tmp_path / "itemfloor")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-itemfloor.jsonl")))
    one = _cell(_baseline_column(matrix), "arithmetic")

    assert (_get(one, "n"), _get(one, "items")) == (DEFAULT_TAG_N, DEFAULT_TAG_ITEMS)
    assert _get(one, "verdict_refused") is True
    assert (_get(one, "needed"), _get(one, "needed_unit")) == (
        MIN_ITEMS - DEFAULT_TAG_ITEMS,
        "items",
    )
    assert "10 items needed for a verdict here; you have 6." in _get(one, "note")
    assert "completions needed" not in _get(one, "note"), (
        "the completions floor is met here, so naming it would send the reader "
        "after a shortfall that does not exist"
    )


def test_a_tag_that_clears_the_item_floor_but_not_the_completions_floor_is_refused(
    tmp_path: Path,
) -> None:
    """Twelve questions asked once each: the case a single combined floor cannot see.

    This is the direction R3's completions floor was already right about and R9's
    item floor is blind to, and it is why the two floors are independent rather
    than one number. Here the shortfall a reader can act on really is more draws,
    so the pair names completions.
    """
    scenario = _retag(_scenario(tmp_path / "nfloor"), ONE_TAG)
    log = _matrix_log(scenario, "evidence-nfloor.jsonl", judging=_both_sides(scenario, draws=1))
    matrix = _available_matrix(_model_from(log))
    one = _cell(_baseline_column(matrix), "solo")

    assert (_get(one, "n"), _get(one, "items")) == (len(ITEM_IDS), len(ITEM_IDS))
    assert _get(one, "verdict_refused") is True
    assert (_get(one, "needed"), _get(one, "needed_unit")) == (
        MIN_N - len(ITEM_IDS),
        "completions",
    )
    assert "20 completions needed for a verdict here; you have 12." in _get(one, "note")


def test_a_tag_that_clears_both_floors_is_not_refused_a_verdict(tmp_path: Path) -> None:
    """The other side of the two tests above, without which they pin only refusal.

    Twelve items at five draws each is sixty completions over twelve questions,
    which clears twenty and ten. A matrix that refused every cell would satisfy
    both tests above and would never publish a dimension claim at all.
    """
    scenario = _retag(_scenario(tmp_path / "cleared"), NO_TAGS)
    log = _matrix_log(scenario, "evidence-cleared.jsonl", judging=_both_sides(scenario))
    matrix = _available_matrix(_model_from(log))
    one = _cell(_baseline_column(matrix), UNTAGGED)

    assert _get(one, "verdict_refused") is False, _get(one, "note")
    assert (_get(one, "needed"), _get(one, "needed_unit")) == (None, "")


def test_the_published_floors_are_the_floors_the_cells_were_actually_refused_against(
    tmp_path: Path,
) -> None:
    """One expression, not three that agree today. R27.7.

    ``min_n`` and ``min_items`` travel on the matrix so that a document refusing a
    cell can say what it refused against -- which is worth nothing if the number it
    publishes and the number the cell was judged by are two separate references
    that happen to name the same constant. Moving the constant here has to move
    both, and a cell that was not refused before has to be refused after.

    The constant is patched on ``report`` rather than on ``dimensions``, because
    ``report`` is what threads the floors into ``dimension_cell`` and patching the
    definition would only test that ``dimensions`` reads its own default.
    """
    module = _module()
    scenario = _scenario(tmp_path / "onefloor")
    log = _counted_log(scenario, "evidence-onefloor.jsonl")

    raised = DEFAULT_TAG_ITEMS + 1
    before = _cell(_baseline_column(_available_matrix(_model_from(log))), "arithmetic")
    assert (_get(before, "items"), _get(before, "needed")) == (
        DEFAULT_TAG_ITEMS,
        MIN_ITEMS - DEFAULT_TAG_ITEMS,
    ), (
        "this tag does not hold the item count the shortfall below is computed "
        "from, so moving the floor would not be visible as a different shortfall"
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(module, "MIN_ITEMS_FOR_A_VERDICT", raised)
        matrix = _available_matrix(_model_from(log))

    one = _cell(_baseline_column(matrix), "arithmetic")

    assert _get(matrix, "min_items") == raised, (
        f"the matrix publishes {_get(matrix, 'min_items')} as the item floor while "
        f"the module's constant says {raised}"
    )
    assert _get(one, "needed") == raised - DEFAULT_TAG_ITEMS, (
        f"the cell was refused against a different item floor from the one the "
        f"matrix publishes: it wants {_get(one, 'needed')} more items to reach "
        f"{_get(matrix, 'min_items')} from {DEFAULT_TAG_ITEMS}"
    )
    assert f"{raised} items needed for a verdict here" in _get(one, "note")


# -- the confidence and the floor the run recorded, threaded and not re-derived - #
#
# R27.1. The wiring is four lines of `from_evidence` and six mutants of it
# survived all 1998 tests, each publishing a false document: an interval at the
# wrong level, an empty floor column, the two swapped, rigor's default applied
# twice, `min_detectable_effect` used as the pass-rate floor, and a string
# threshold reaching `wilson_interval` unconverted. `THRESHOLDS` is deliberately
# all-distinct and deliberately not rigor's defaults, which is what makes each of
# those visible from a cell.
# --------------------------------------------------------------------------- #


def test_the_cells_carry_the_pass_rate_floor_this_run_recorded(tmp_path: Path) -> None:
    """The floor is echoed onto every cell, and it is *this* run's floor.

    A document that refuses a cell has to be able to say what it refused against.
    ``floor=None`` empties that column and the page loses the sentence; the floor
    read out of ``min_detectable_effect`` fills it with 0.13 while the gate above
    ran at 0.87, which is two floors in one document and neither of them flagged.
    """
    scenario = _scenario(tmp_path / "cellfloor")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-cellfloor.jsonl")))

    assert len(set(THRESHOLDS.values())) == len(THRESHOLDS), (
        "two thresholds in this file share a value, so a cell reading the wrong one "
        "would be indistinguishable from a cell reading the right one"
    )
    for column in _all_columns(matrix):
        for one in _cells(column):
            assert _get(one, "floor") == THRESHOLDS["pass_rate_floor"], (
                f"a {_get(one, 'tag')!r} cell of {_get(column, 'model_id')!r} carries "
                f"floor={_get(one, 'floor')!r}; this run recorded "
                f"{THRESHOLDS['pass_rate_floor']}"
            )


def test_the_floor_on_a_cell_follows_the_run_and_is_not_a_constant(tmp_path: Path) -> None:
    """The other half: a second run at a different floor produces different cells.

    The test above passes on any implementation that hard-codes 0.87, which is the
    number this file happens to use everywhere.
    """
    loose = dict(THRESHOLDS, pass_rate_floor=0.55)
    scenario = _scenario(tmp_path / "loosefloor", thresholds=loose)
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-loosefloor.jsonl")))

    assert _get(_cell(_baseline_column(matrix), "arithmetic"), "floor") == 0.55


def test_the_interval_on_a_cell_is_wilson_at_the_runs_confidence(tmp_path: Path) -> None:
    """0.99, not rigor's 0.95, and the difference is on the page as a wider bar.

    ``confidence=None`` widens the baseline's ``arithmetic`` interval from
    (0.819, 1.0) to (0.886, 1.0) and adds a sentence to every cell saying rigor's
    default of 95% was used -- about a run whose evidence log records 99%. The
    swap with ``pass_rate_floor`` computes the interval at 87%. All three are
    complete, plausible, unflagged documents.
    """
    scenario = _scenario(tmp_path / "conf")
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-conf.jsonl")))
    one = _cell(_baseline_column(matrix), "arithmetic")

    assert THRESHOLDS["confidence"] != DEFAULT_CONFIDENCE, (
        "this file's confidence is rigor's own default, so an interval computed at "
        "either would be the same interval and this test asserts nothing"
    )
    assert _get(one, "interval") == wilson_interval(
        DEFAULT_TAG_N, DEFAULT_TAG_N, THRESHOLDS["confidence"]
    )
    assert _get(one, "interval") != wilson_interval(
        DEFAULT_TAG_N, DEFAULT_TAG_N, DEFAULT_CONFIDENCE
    ), "the interval is at rigor's default; this run recorded a confidence of its own"
    assert _get(one, "interval") != wilson_interval(
        DEFAULT_TAG_N, DEFAULT_TAG_N, THRESHOLDS["pass_rate_floor"]
    ), "the interval is at 87%: the confidence and the pass-rate floor are swapped"
    assert "rigor's default" not in _get(one, "note"), (
        f"the cell discloses a defaulted confidence on a run that recorded one: "
        f"{_get(one, 'note')!r}"
    )


def _without_confidence(tmp_path: Path, name: str) -> Any:
    """A run whose threshold block records no confidence level at all.

    ``thresholds`` is the only place ``from_evidence`` looks, so the judge rows
    keep this file's ordinary gates -- a fixture that also emptied them would be
    exercising a different absence in the same test.
    """
    thresholds = {key: value for key, value in THRESHOLDS.items() if key != "confidence"}
    scenario = _scenario(tmp_path / name, thresholds=thresholds, judges=[_judge_payload()])
    assert "confidence" not in scenario.comparison["thresholds"], (
        "the fixture recorded a confidence after all, so the disclosure below would "
        "be asserting nothing"
    )
    return _model_from(_counted_log(scenario, f"evidence-{name}.jsonl"))


def test_a_run_that_recorded_no_confidence_discloses_rigors_default_exactly_once(
    tmp_path: Path,
) -> None:
    """An absent confidence stays absent all the way to the cell, which discloses it.

    Two mutants live on either side of this. Defaulting in ``report.py`` -- ``or
    DEFAULT_CONFIDENCE`` on the way past -- computes the identical interval and
    drops the sentence, so the reader is shown a bar whose level nothing on the
    page states. Applying the default twice would print the sentence twice. The
    count is what separates the three, so it is a count and not a substring.

    The expected note comes from ``dimension_cell`` driven directly, which is the
    module ``report.py`` is required to delegate this to and shares none of the
    path under test -- the thresholds, ``_number``, the matrix and the column are
    all skipped.
    """
    model = _without_confidence(tmp_path, "noconf")
    matrix = _available_matrix(model)
    one = _cell(_baseline_column(matrix), "arithmetic")
    expected = dimension_cell(
        "arithmetic",
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
        confidence=None,
        floor=THRESHOLDS["pass_rate_floor"],
    )

    assert "rigor's default" in expected.note, (
        "dimensions no longer discloses a defaulted confidence, so this test is "
        "asserting the absence of something that was never there"
    )
    note = str(_get(one, "note"))
    said = note.count("rigor's default")

    assert note == expected.note
    assert said == 1, f"the default-confidence disclosure appears {said} times: {note!r}"
    assert _get(one, "interval") == wilson_interval(
        DEFAULT_TAG_N, DEFAULT_TAG_N, DEFAULT_CONFIDENCE
    )


def test_the_disclosure_is_absent_from_every_cell_of_a_run_that_recorded_one(
    tmp_path: Path,
) -> None:
    """The count above is zero when there is nothing to disclose, on every cell.

    A disclaimer that is true of the run above is false of this one, and a
    published false disclaimer is worse than a missing true one: it names a number
    the evidence log contradicts.
    """
    scenario = _scenario(tmp_path / "nodisclosure")
    matrix = _available_matrix(
        _model_from(_counted_log(scenario, "evidence-nodisclosure.jsonl"))
    )

    for column in _all_columns(matrix):
        for one in _cells(column):
            assert "rigor's default" not in _get(one, "note"), (
                f"a {_get(one, 'tag')!r} cell of {_get(column, 'model_id')!r} says a "
                f"default confidence was used; this run recorded "
                f"{THRESHOLDS['confidence']}"
            )


def test_a_threshold_recorded_as_a_string_is_not_threaded_into_the_statistics(
    tmp_path: Path,
) -> None:
    """``_number`` is what stands between a JSON string and ``wilson_interval``.

    An evidence log is written by one machine and read by another, and every value
    in it is input from outside the trust boundary -- ``"0.99"`` is a shape a
    hand-edited or re-serialised payload really produces. Without the conversion
    the string reaches the interval arithmetic unconverted; with it the run reads
    as one that recorded no usable threshold, which is the true statement and is
    disclosed as one.
    """
    thresholds = dict(THRESHOLDS, confidence="0.99", pass_rate_floor="0.87")
    scenario = _scenario(tmp_path / "strings", thresholds=thresholds, judges=[_judge_payload()])
    matrix = _available_matrix(_model_from(_counted_log(scenario, "evidence-strings.jsonl")))
    one = _cell(_baseline_column(matrix), "arithmetic")

    assert _get(one, "floor") is None, (
        f"a string floor was carried onto the cell as {_get(one, 'floor')!r}, so the "
        f"page prints a threshold it cannot compare anything against"
    )
    assert _get(one, "interval") == wilson_interval(
        DEFAULT_TAG_N, DEFAULT_TAG_N, DEFAULT_CONFIDENCE
    )
    assert "rigor's default" in _get(one, "note"), (
        "the string confidence was consumed silently: the cell shows an interval "
        "and says nothing about what level it is at"
    )


# -- the judge, which the raw counts erased ---------------------------------- #


def test_the_matrix_names_the_panels_first_judge(tmp_path: Path) -> None:
    """``DimensionCounts`` carries no judge name, so it is a per-judge table anonymised.

    A panel writes one verdict per judge per completion and the matrix counts one
    of them, so a table that does not say which one is a number a reader cannot
    attribute. ``judges[0]`` swapped for ``judges[-1]`` survived undetected until
    C21's fix pass, and only a panel of more than one can see the difference.
    """
    scenario = _panel_scenario(tmp_path / "judge-named")
    matrix = _available_matrix(_model_from(_panel_log(scenario, "evidence-judged.jsonl")))

    assert [_get(one, "name") for one in _get(_from_evidence(scenario), "judges")] == [
        J,
        SECOND_JUDGE,
    ], "the panel does not hold two judges in this order, so the name below is free"
    assert _get(matrix, "judge") == J, (
        f"the matrix says it was counted under {_get(matrix, 'judge')!r}; the "
        f"panel's first judge is {J!r}"
    )


def test_the_judge_the_matrix_names_is_the_judge_its_cells_were_counted_under(
    tmp_path: Path,
) -> None:
    """A label is worse than no label if the numbers under it are someone else's.

    In this log the first judge passes every draw of both sides and the second
    fails every draw of both sides, so a matrix labelled with one judge and
    counted under the other is a complete, available, plausible table saying both
    models got everything wrong.
    """
    scenario = _panel_scenario(tmp_path / "judge-counted")
    matrix = _available_matrix(_model_from(_panel_log(scenario, "evidence-counted-by.jsonl")))

    assert _get(matrix, "judge") == J
    assert _cell_counts(_baseline_column(matrix), "arithmetic") == (
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    ), (
        f"the cells were counted under {SECOND_JUDGE!r}, which failed every draw in "
        f"this log, while the matrix is labelled {J!r}"
    )


# -- the six ways there is no matrix ----------------------------------------- #


def test_a_golden_set_that_no_longer_matches_hands_back_its_own_sentence(
    tmp_path: Path,
) -> None:
    """Reused verbatim from ``gs_view["reason"]``, because a second phrasing goes stale.

    Asserted against ``model.goldenset["reason"]`` rather than against a sentence
    quoted here, since a quoted sentence would itself be the extra copy this rule
    exists to prevent. The completeness strip, the warnings list and the matrix
    all print the same words or the document contradicts itself about why an
    exhibit is missing.
    """
    scenario = _scenario(tmp_path / "stale-set", recorded_goldenset_hash="d" * 64)
    model = _model_from(_counted_log(scenario, "evidence-stale.jsonl"))
    matrix = _matrix(model)

    assert _get(matrix, "available") is False
    assert _get(matrix, "reason") == model.goldenset["reason"], (
        "the golden set's refusal was re-worded on its way into the matrix; three "
        "copies of a disclosure are three chances for one to go stale"
    )


def test_an_unavailable_matrix_carries_no_cells_at_all(tmp_path: Path) -> None:
    """Nothing may be fabricated, and ``item_counts`` is sitting right there.

    ``item_counts`` is an aggregate over the whole run: splitting it across tags
    by any rule at all is invention, and a matrix half-filled from it renders as a
    matrix. ``DimensionCounts`` guarantees an empty ``by_model`` on a refusal
    precisely so a caller cannot be tempted, and that guarantee is worth nothing
    if the caller re-fills it.
    """
    scenario = _scenario(tmp_path / "nocells", recorded_goldenset_hash="d" * 64)
    model = _model_from(_counted_log(scenario, "evidence-nocells.jsonl"))
    matrix = _matrix(model)

    assert _get(matrix, "available") is False
    assert model.item_counts, (
        "this fixture records no item counts, so it does not demonstrate that the "
        "tempting source of fabricated cells was available and left alone"
    )
    assert _cells(_baseline_column(matrix)) == ()
    assert _candidate_ids(matrix) == [], (
        f"a refused matrix still holds columns for {_candidate_ids(matrix)}; a "
        f"partial matrix renders as the matrix"
    )
    assert _get(matrix, "tags") == ()


def _declines(tmp_path: Path) -> list[tuple[str, Any, str]]:
    """The six ways there is no matrix, each with the sentence it must quote.

    One of them belongs to the golden set and five belong to the counter. R1
    claimed building from the log collapsed the two independent decline reasons
    into one; R12.3 records that it does not, and this is the list.

    Each entry is ``(what went wrong, the model, the sentence the source produced)``.
    """
    cases: list[tuple[str, Any, str]] = []

    # 1. The golden set is not the one that was run. The counter never runs.
    stale = _scenario(tmp_path / "d-stale", recorded_goldenset_hash="d" * 64)
    stale_model = _model_from(_counted_log(stale, "evidence-d-stale.jsonl"))
    cases.append(("the golden set changed", stale_model, stale_model.goldenset["reason"]))

    # 2. No judging pass reached the log at all.
    none_ran = _scenario(tmp_path / "d-nojudging")
    log = _matrix_log(none_ran, "evidence-d-nojudging.jsonl", judging=())
    cases.append(("no judging pass", _model_from(log), _counter_reason(log, none_ran.goldenset)))

    # 3. Judging ran and this judge wrote nothing under that name.
    silent = _scenario(tmp_path / "d-silent")
    log = _matrix_log(
        silent,
        "evidence-d-silent.jsonl",
        judging=[
            _record(
                EVENT_JUDGING_COMPLETED,
                {"model_id": BASELINE_MODEL, "graded": {}, "imputed": {}, "parse_failures": {}},
                TS_JUDGING,
            )
        ],
    )
    cases.append(
        ("the judge wrote nothing", _model_from(log), _counter_reason(log, silent.goldenset))
    )

    # 4. Verdicts left open at the end: nothing names which model they belong to.
    open_group = _scenario(tmp_path / "d-open")
    log = _matrix_log(
        open_group,
        "evidence-d-open.jsonl",
        judging=[
            *_judging_pass(BASELINE_MODEL, open_group.items, passed=True),
            _dim_verdict(open_group.items[0], passed=True),
            _dim_verdict(open_group.items[1], passed=True),
        ],
    )
    cases.append(
        ("verdicts left open", _model_from(log), _counter_reason(log, open_group.goldenset))
    )

    # 5. A verdict whose input is in no golden-set item: log and set disagree in a
    #    way the recorded hash did not catch.
    stranger = _scenario(tmp_path / "d-unjoinable")
    verdicts = [
        _dim_verdict(item_id, passed=True) for item_id in stranger.items for _ in range(N_PER_ITEM)
    ]
    verdicts.append(_dim_verdict("item-99", passed=True))
    log = _matrix_log(
        stranger,
        "evidence-d-unjoinable.jsonl",
        judging=[
            *verdicts,
            _record(
                EVENT_JUDGING_COMPLETED,
                {
                    "model_id": BASELINE_MODEL,
                    "graded": {J: len(verdicts)},
                    "imputed": {},
                    "parse_failures": {},
                },
                TS_JUDGING,
            ),
        ],
    )
    cases.append(
        ("a verdict joins to nothing", _model_from(log), _counter_reason(log, stranger.goldenset))
    )

    # 6. A model the log names only in the completions that failed.
    from model_migration_kit.contracts import EVENT_COMPLETION

    only_failed = _scenario(tmp_path / "d-failedonly")
    log = _matrix_log(
        only_failed,
        "evidence-d-failedonly.jsonl",
        judging=[
            *_judging_pass(BASELINE_MODEL, only_failed.items, passed=True),
            _record(
                EVENT_COMPLETION,
                {"ok": False, "model_id": CANDIDATE_MODEL, "item_id": only_failed.items[0]},
                TS_JUDGING,
            ),
        ],
    )
    cases.append(
        (
            "a model seen only in failures",
            _model_from(log),
            _counter_reason(log, only_failed.goldenset),
        )
    )

    return cases


def test_the_matrix_declines_in_six_distinguishable_ways_and_re_words_none_of_them(
    tmp_path: Path,
) -> None:
    """Six causes, six sentences, each quoted from the place that produced it.

    The contract's list, asserted as a list rather than as six independent tests,
    because the claim that makes it worth writing down is that the six are
    *different* -- a matrix that answered every failure with one sentence would
    pass six separate tests for containing a keyword and would still have told the
    reader nothing about which fix to apply.

    Byte-identical, not "mentions the judge" and not "is non-empty". A re-worded
    refusal is a third copy of a disclosure that already has two, and the copy
    that goes stale is never the one anybody is looking at.
    """
    seen: dict[str, str] = {}
    for label, model, expected in _declines(tmp_path):
        matrix = _matrix(model)
        assert _get(matrix, "available") is False, f"{label}: the matrix claims to be available"
        assert _get(matrix, "reason") == expected, (
            f"{label}: the matrix re-worded the refusal.\n"
            f"  source: {expected!r}\n"
            f"  matrix: {_get(matrix, 'reason')!r}"
        )
        assert _cells(_baseline_column(matrix)) == (), f"{label}: a refusal carried cells"
        assert _candidate_ids(matrix) == [], f"{label}: a refusal carried candidate columns"
        seen[label] = _get(matrix, "reason")

    assert len(set(seen.values())) == len(seen) == 6, (
        f"the six causes did not produce six distinguishable sentences: "
        f"{sorted(seen)} gave {len(set(seen.values()))} distinct reasons"
    )


# -- and still one pass over the log ----------------------------------------- #


def test_building_the_matrix_does_not_read_the_evidence_log_a_second_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join needs the golden set, which is named on the last record. It waits.

    ``test_the_log_is_read_once_for_both_the_headline_and_the_series`` counts the
    same opens for the series; this counts them with a *populated matrix* on the
    model, which is the case where reading the log again is the obvious
    implementation and is the one C3 forbids. The other road -- buffering the
    verdicts until the golden set arrives -- was measured at 5.0-5.8 times the
    log's own bytes resident, so both shortcuts are closed and only the two-phase
    tally is left.
    """
    import builtins
    import os

    scenario = _scenario(tmp_path / "onepass-matrix")
    log = _counted_log(scenario, "evidence-onepass-matrix.jsonl")
    target = log.resolve()
    opened: list[str] = []
    real_open = builtins.open

    def counting(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        try:
            same = Path(os.fspath(file)).resolve() == target
        except (TypeError, ValueError, OSError):
            same = False
        if same and "b" not in mode:
            opened.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting)
    monkeypatch.setattr(io, "open", counting)
    try:
        model = _model_from(log)
    finally:
        monkeypatch.undo()

    matrix = _available_matrix(model)
    assert _cell_counts(_baseline_column(matrix), "arithmetic") == (
        DEFAULT_TAG_N,
        DEFAULT_TAG_N,
        DEFAULT_TAG_ITEMS,
    ), "no matrix was built, so this log's open count measures nothing"
    assert len(opened) == 1, (
        f"the evidence log was read {len(opened)} times in text mode; the matrix "
        f"has to be built out of the pass that is already happening"
    )
