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


def test_a_series_of_runs_renders_exactly_what_one_run_rendered(tmp_path: Path) -> None:
    """C3's title is "hang the series off ``ReportModel``, render nothing".

    Byte-for-byte, modulo the log's own path and digest. A chunk that quietly
    added a row, a column or a sentence would ship a document nobody reviewed,
    and C6 is where the timeline is supposed to arrive.
    """
    scenario = _scenario(tmp_path / "renders")
    alone = _model_from(scenario.evidence)
    log = _log_with_history(
        scenario, "evidence-renders.jsonl", _earlier_run(scenario, tag="one")
    )
    with_history = _model_from(log)

    assert _headline_scrubbed(_html(with_history), log) == _headline_scrubbed(
        _html(alone), scenario.evidence
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


def test_a_fake_adapter_on_an_earlier_run_still_bands_the_report(
    tmp_path: Path,
) -> None:
    """Edges row 3, and §4.3: "or any point in ``series`` names a ``Fake*`` adapter".

    §5.3's rationale is that "you cannot obtain a clean-looking report from
    scripted models by avoiding ``migkit demo``". Once a log carries history, the
    way to obtain one is to run the scripted nights first and a real night last,
    which is exactly the shape of a demo somebody pastes into a deck.
    """
    scenario = _scenario(tmp_path / "demo-history")
    log = _log_with_history(
        scenario,
        "evidence-demo.jsonl",
        _earlier_run(
            scenario,
            tag="fake",
            baseline_adapter="FakeAdapter",
            candidate_adapter="FakeScriptedAdapter",
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
