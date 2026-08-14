"""Loading and validating the golden set.

A golden set is JSONL: one JSON object per line, ``{id, input, reference?, tags?,
metadata?}``. It is the fixed input both models are measured against, so its
identity matters as much as its contents -- the file's hash is embedded in every
artifact downstream, and two runs are comparable only if those hashes match.

Validation is strict and loud, and that is a deliberate trade. A golden set with
two items sharing an id produces a per-item flip list that cannot be trusted, and
a duplicated tag quietly double-counts a slice in the report. Both are the kind of
defect that surfaces as a slightly wrong number three stages later, where nobody
will trace it back. So every one of them is an error at load time, naming the line
that caused it, rather than a warning nobody reads.

The one deliberate leniency is blank lines, which are skipped. A blank line is not
malformed data; it is an editor artefact, and rejecting it would fail loads for a
reason that has nothing to do with the eval.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import GoldenItem, canonical_json, hash_bytes
from .errors import GoldenSetError

#: Keys a golden-set line may carry. Anything else is an error rather than an
#: ignored extra: a mistyped ``"tag"`` that silently dropped the tags would show
#: up as an empty slice in the report, with no way to tell it from a real one.
#: ``metadata`` is the sanctioned place to put anything this format does not model.
ALLOWED_KEYS = frozenset({"id", "input", "reference", "tags", "metadata"})


@dataclass(frozen=True)
class GoldenSet:
    """A validated golden set, its identity, and where it came from.

    Two hashes, because there are two different questions and conflating them
    causes a specific, expensive failure.

    ``hash`` is the **content** identity: sha256 over the canonical JSON form of
    the parsed items, sorted by id. It answers "is this the same set of cases?"
    and it is what decides whether two run artifacts may be compared. It is
    deliberately blind to formatting, because the alternative was found to be
    unworkable: hashing the raw bytes means that letting an editor add the
    trailing newline it always adds, or writing ``{"input": ..., "id": ...}``
    instead of ``{"id": ..., "input": ...}``, produces a different identity for a
    byte-for-byte different but semantically identical file -- and the operator
    then has to re-run a baseline that cost real money to establish. It also
    contradicted the convention ``contracts.py`` states for exactly this purpose:
    the hash is of content, not of a formatting decision.

    **Tag order is content, not formatting.** ``["math", "code"]`` and
    ``["code", "math"]`` are two different golden sets with two different hashes.
    This is the one place the paragraph above stops applying, and it reads as an
    oversight unless it is written down, so: tags are consumed as a set everywhere
    that *gates* -- stripped and de-duplicated here, counted order-independently by
    :meth:`stats`, rendered as a ``sorted`` histogram in the report -- but
    ``report.py`` renders an individual item's own tags with ``" ".join`` in file
    order, so the order is visible in the document a reviewer signs off. A hash
    blind to a difference a reader can see would let two distinguishable sets
    claim to be the same evidence, and that costs the audit trail where an
    unnecessary re-run only costs money. Alphabetising a tag list therefore does
    invalidate a baseline, deliberately. Reversing this would change every hash
    ever recorded, including this project's own demo set, whose value is pasted in
    the README.

    ``file_hash`` is the **provenance** identity: sha256 of the file's bytes with
    CRLF normalised to LF. It answers the different question "is this the same
    file I read last time?", which is what a change-control reviewer asks. Both
    go into the report; only ``hash`` gates comparability.
    """

    items: tuple[GoldenItem, ...]
    hash: str
    path: str
    file_hash: str = ""

    @classmethod
    def load(cls, path: str | Path) -> GoldenSet:
        """Read and validate a golden set from disk."""
        target = Path(path)
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise GoldenSetError(f"cannot read golden set {target}: {exc}") from exc
        return cls.parse(data, source=str(target))

    @classmethod
    def parse(cls, data: bytes | str, *, source: str = "<memory>") -> GoldenSet:
        """Validate a golden set already in memory.

        Exists so that a caller with the bytes in hand -- a test, or a future
        reader pulling a set out of an artifact -- gets exactly the same
        validation and the same hash as a load from disk.
        """
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        file_digest = hash_bytes(raw)
        try:
            # utf-8-sig so a BOM written by a Windows editor does not become part
            # of the first item's id. The hash is still of the raw bytes: a BOM is
            # a real difference in the file, even though it is not a difference in
            # the data.
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise GoldenSetError(f"{source} is not valid UTF-8: {exc}") from exc

        items: list[GoldenItem] = []
        seen: dict[str, int] = {}
        for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
            if not line.strip():
                continue
            item = _parse_line(line, source=source, number=number)
            if item.id in seen:
                raise GoldenSetError(
                    f"{source} line {number}: duplicate id {item.id!r}, already "
                    f"defined on line {seen[item.id]}. Ids key the per-item flip "
                    f"list in every comparison; two items sharing one make that "
                    f"list wrong rather than incomplete."
                )
            seen[item.id] = number
            items.append(item)

        if not items:
            raise GoldenSetError(
                f"{source} contains no items. A comparison over an empty golden "
                f"set has no evidence in it."
            )
        return cls(
            items=tuple(items),
            hash=content_hash(items),
            path=source,
            file_hash=file_digest,
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.items)

    def get(self, item_id: str) -> GoldenItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise GoldenSetError(f"no item with id {item_id!r} in {self.path}")

    def stats(self) -> dict[str, Any]:
        """What was actually tested, in the shape the report prints.

        The tag distribution is here because "which slice moved?" is the first
        question asked of any regression, and it cannot be answered after the fact
        if the report only recorded a total.
        """
        tags: dict[str, int] = {}
        for item in self.items:
            for tag in item.tags:
                tags[tag] = tags.get(tag, 0) + 1
        return {
            "size": len(self.items),
            "with_reference": sum(1 for item in self.items if item.reference is not None),
            "untagged": sum(1 for item in self.items if not item.tags),
            "tags": dict(sorted(tags.items())),
        }

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[GoldenItem]:
        return iter(self.items)


def content_hash(items: Iterable[GoldenItem]) -> str:
    """Identity of a set of cases, independent of how the file was formatted.

    Items are sorted by id before hashing, so two files listing the same cases in
    a different order are the same set. That is the honest answer -- nothing
    downstream depends on file order, since every per-item result is keyed by id
    -- and it spares an operator a full baseline re-run for a diff that moved a
    line.

    Uses ``contracts.canonical_json``, which is in the frozen contracts for
    precisely this: sorted keys, no incidental whitespace, so the digest is a
    function of the data rather than of the writer.
    """
    payload = b"\n".join(
        canonical_json(item.to_dict()) for item in sorted(items, key=lambda i: i.id)
    )
    return hash_bytes(payload)


def _parse_line(line: str, *, source: str, number: int) -> GoldenItem:
    where = f"{source} line {number}"
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise GoldenSetError(f"{where}: not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict):
        raise GoldenSetError(
            f"{where}: expected a JSON object, got {type(raw).__name__}"
        )

    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        raise GoldenSetError(
            f"{where}: unknown key(s) {', '.join(repr(k) for k in unknown)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_KEYS))}. Put anything else under "
            f"'metadata', where it survives without being silently ignored."
        )

    item_id = _required_text(raw, "id", where)
    text = _required_text(raw, "input", where)

    reference = raw.get("reference")
    if reference is not None:
        if not isinstance(reference, str):
            raise GoldenSetError(
                f"{where}: 'reference' must be a string or null, got "
                f"{type(reference).__name__}"
            )
        if not reference.strip():
            raise GoldenSetError(
                f"{where}: 'reference' is empty. Omit the key entirely if this "
                f"item has no gold answer -- an empty one grades as a wrong answer."
            )

    tags = _parse_tags(raw.get("tags"), where)
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise GoldenSetError(
            f"{where}: 'metadata' must be an object, got {type(metadata).__name__}"
        )
    # No check that metadata's keys are strings: JSON object member names always
    # are, and this function only ever sees the output of json.loads. A guard here
    # would be unreachable code that reads like a real invariant.

    return GoldenItem(
        id=item_id,
        input=text,
        reference=reference,
        tags=tags,
        metadata=dict(metadata),
    )


def _required_text(raw: Mapping[str, Any], key: str, where: str) -> str:
    if key not in raw:
        raise GoldenSetError(f"{where}: missing required key {key!r}")
    value = raw[key]
    if not isinstance(value, str):
        raise GoldenSetError(f"{where}: {key!r} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise GoldenSetError(
            f"{where}: {key!r} is empty. An empty {key} is a data-entry mistake, "
            f"and it would be sampled n times against both models before anyone "
            f"noticed."
        )
    return value


def _parse_tags(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list):
        raise GoldenSetError(
            f"{where}: 'tags' must be a list of strings, got {type(value).__name__}"
        )
    seen: set[str] = set()
    tags: list[str] = []
    for tag in value:
        if not isinstance(tag, str):
            raise GoldenSetError(
                f"{where}: every tag must be a string, got {type(tag).__name__}"
            )
        # Stripped before the duplicate check, not after. "math" and "math " are
        # the same slice to every human who will read the report, and leaving them
        # distinct produced exactly the double-counted slice this check exists to
        # prevent -- while passing the check, because the raw strings differ.
        clean = tag.strip()
        if not clean:
            raise GoldenSetError(f"{where}: tags cannot be empty strings")
        if clean in seen:
            raise GoldenSetError(
                f"{where}: duplicate tag {clean!r}. Tags are counted per item, so a "
                f"repeat would inflate that slice in the report."
            )
        seen.add(clean)
        tags.append(clean)
    return tuple(tags)
