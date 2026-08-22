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

from model_migration_kit.contracts import (
    ARTIFACT_SCHEMA_VERSION,
    EVENT_COMPARISON,
    EVENT_JUDGING_COMPLETED,
    EVENT_RUN_STARTED,
    EVENT_VERDICT,
    Verdict,
)
from model_migration_kit.errors import MigrationKitError
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
# 16. The interval bar. Plan C12, lines 1148-1218.
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
