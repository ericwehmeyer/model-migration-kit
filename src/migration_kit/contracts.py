"""The shared data shapes, frozen before anything is built against them.

This module exists so that the modules written in parallel cannot disagree about
what a golden-set item or a run record is. It holds no behaviour beyond
construction and serialisation, and it imports nothing from the rest of the
package -- everything else depends on it, and it depends on nothing.

Hashing convention, used identically for golden sets, judge configs, and anything
else whose identity must survive a round trip through a file: **sha256 of the
bytes with CRLF normalised to LF**. Two things follow from that choice and both
matter. A Windows checkout and a Linux CI runner agree, so a cross-platform run
does not look like a changed input. And the hash is of *content*, not of a
formatting decision, so re-indenting a JSON file does not invalidate it.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Bumped when the on-disk shape of a run artifact changes incompatibly. Written
#: into every artifact so a future reader can refuse one it does not understand
#: rather than misinterpret it.
ARTIFACT_SCHEMA_VERSION = 1

#: Event types migration-kit writes to the rigor evidence log. rigor's own events
#: (judge.verdict, sample.completed, assertion.evaluated) appear alongside these;
#: the `migkit.` prefix keeps the two namespaces legible in one file.
EVENT_RUN_STARTED = "migkit.run_started"
EVENT_ITEM_COMPLETED = "migkit.item_completed"
EVENT_RUN_COMPLETED = "migkit.run_completed"
EVENT_JUDGING_COMPLETED = "migkit.judging_completed"
EVENT_COMPARISON = "migkit.comparison"
EVENT_VERDICT = "migkit.verdict"


def hash_bytes(data: bytes) -> str:
    """sha256 of ``data`` with CRLF normalised to LF. See the module docstring."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def hash_file(path: str | os.PathLike[str]) -> str:
    """sha256 of a file's content, newline-normalised."""
    return hash_bytes(Path(path).read_bytes())


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Stable serialisation for anything whose hash must be reproducible.

    Sorted keys and no incidental whitespace, so the hash is a function of the
    data and not of how it happened to be written out.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class GoldenItem:
    """One case in a golden set.

    ``reference`` is optional because not every eval has a gold answer; a judge
    with a rubric can grade without one. ``tags`` exist so a report can say which
    slice of the set moved, which is usually the first question asked of a
    regression.
    """

    id: str
    input: str
    reference: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "input": self.input}
        if self.reference is not None:
            out["reference"] = self.reference
        if self.tags:
            out["tags"] = list(self.tags)
        if self.metadata:
            out["metadata"] = dict(self.metadata)
        return out


@dataclass(frozen=True)
class Completion:
    """One sampled response to one golden-set item.

    ``sample_index`` distinguishes the n draws taken per item -- a migration
    decision needs a distribution per item, not a single shot, so the same item id
    appears ``n`` times with different indices.

    ``error`` carries the string form of an exception when the provider did not
    answer. A completion that failed is kept, not dropped: the fact that model B
    times out on three items is part of the migration decision, and discarding it
    would quietly improve B's apparent quality.
    """

    item_id: str
    sample_index: int
    output: str | None
    duration: float
    error: str | None = None
    error_type: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "sample_index": self.sample_index,
            "output": self.output,
            "duration": self.duration,
            "error": self.error,
            "error_type": self.error_type,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Completion:
        return cls(
            item_id=raw["item_id"],
            sample_index=int(raw["sample_index"]),
            output=raw.get("output"),
            duration=float(raw.get("duration", 0.0)),
            error=raw.get("error"),
            error_type=raw.get("error_type"),
            tokens_in=raw.get("tokens_in"),
            tokens_out=raw.get("tokens_out"),
        )


@dataclass(frozen=True)
class RunHeader:
    """Identity of a run artifact. Every field here is part of comparability.

    Two artifacts may only be compared if their ``goldenset_hash`` matches. The
    ``model_id`` is the pinned provider string, not a friendly name, because the
    friendly name is what drifts.
    """

    model_id: str
    goldenset_hash: str
    goldenset_path: str
    n_per_item: int
    created: str
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    adapter: str = ""
    notes: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "goldenset_hash": self.goldenset_hash,
            "goldenset_path": self.goldenset_path,
            "n_per_item": self.n_per_item,
            "created": self.created,
            "adapter": self.adapter,
            "notes": dict(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RunHeader:
        return cls(
            model_id=raw["model_id"],
            goldenset_hash=raw["goldenset_hash"],
            goldenset_path=raw.get("goldenset_path", ""),
            n_per_item=int(raw["n_per_item"]),
            created=raw.get("created", ""),
            schema_version=int(raw.get("schema_version", ARTIFACT_SCHEMA_VERSION)),
            adapter=raw.get("adapter", ""),
            notes=raw.get("notes", {}),
        )


@dataclass(frozen=True)
class Verdict:
    """The three-valued outcome of a comparison.

    ``REVIEW`` is a first-class result, not a fudge. A tool that only says GO or
    NO-GO must guess when the sample is too small to distinguish the two models,
    and it will guess in whichever direction its author felt safer. Saying
    "collect more data" is the honest answer and is the reason this type has three
    members rather than two.
    """

    GO = "GO"
    NO_GO = "NO-GO"
    REVIEW = "REVIEW"
    ERROR = "ERROR"

    #: Exit codes, documented as the CI contract. Changing these is a breaking
    #: change to every pipeline that consumes the tool.
    EXIT_CODES = {GO: 0, NO_GO: 1, REVIEW: 2, ERROR: 3}

    @classmethod
    def exit_code(cls, verdict: str) -> int:
        return cls.EXIT_CODES.get(verdict, cls.EXIT_CODES[cls.ERROR])


def artifact_stem(model_id: str, goldenset_hash: str) -> str:
    """Filename stem keyed by (model, golden set), so mixed runs cannot collide.

    The model id goes through a conservative slug because provider strings contain
    characters that are legal in an id and illegal in a filename on some platform
    or other. The golden-set hash is truncated for readability but kept long
    enough that a collision is not a practical concern for a directory of runs.
    """
    slug = "".join(c if c.isalnum() or c in "-_." else "-" for c in model_id).strip("-")
    return f"{slug}__{goldenset_hash[:16]}"


def utc_now() -> str:
    """RFC3339 UTC timestamp, matching the evidence log's format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def as_sequence(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(value) if value else ()
