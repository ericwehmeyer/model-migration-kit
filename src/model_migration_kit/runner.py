"""Running a golden set against one model, resumably.

One run is one model against one golden set. The result is a *run artifact*: a
JSONL file on disk, named for the pair it belongs to, holding one header record
per part and one record per completion. Two artifacts can be compared later only
because the header says exactly which model and which golden set produced them.

Three properties are load-bearing here, and each one exists because of a way this
could otherwise lie:

**Failed completions are kept.** If model B times out on three items, that is part
of the migration decision. Dropping the failures would quietly improve B's
apparent quality, which is the exact direction a tool like this must never err in.

**The file is written as the run goes, not at the end.** Every item is appended
and fsynced before the next one starts, so a run killed halfway leaves a valid
partial artifact that the report can still render -- and that this module can
resume from.

**Resuming appends a second header rather than rewriting the first.** The file
stays append-only, and the artifact can then say "completed in 2 parts" instead of
looking like one clean run. A resumed run is a perfectly good run; hiding the
seam would be the only dishonest option.

Sampling itself is opik-rigor's ``sample``: n draws per item, per-run durations,
errors captured rather than raised. Nothing statistical is reimplemented here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opik_rigor import Adapter, EvidenceLog, sample

from .contracts import (
    ARTIFACT_SCHEMA_VERSION,
    EVENT_COMPLETION,
    EVENT_ITEM_COMPLETED,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_STARTED,
    Completion,
    GoldenItem,
    RunHeader,
    artifact_stem,
    utc_now,
)
from .errors import ArtifactError
from .goldenset import GoldenSet

#: Five draws per item by default. A migration decision needs a distribution per
#: item, not a single shot: one sample cannot distinguish "model B is worse" from
#: "model B was unlucky once", and that distinction is the entire product.
DEFAULT_N = 5

_RECORD_HEADER = "header"
_RECORD_COMPLETION = "completion"

#: rigor's scripted stand-in, named rather than imported: the check that uses it
#: compares a string recorded in a header written by some earlier process, which
#: may have run on another machine entirely.
_FAKE_ADAPTER = "FakeAdapter"


def _answered(value: Any) -> bool:
    """Whether one draw produced an answer at all.

    This must be passed to rigor's ``sample`` explicitly, and the reason is worth
    stating because getting it wrong is silent. rigor's default classifier raises
    ``TypeError`` on a plain string -- it refuses to guess whether "Paris" is a
    pass -- and ``sample`` records an exception raised while classifying in the
    same place as an exception raised by the call itself. Leave it to the default
    and every successful completion arrives here carrying an error, which this
    module would faithfully record as a provider failure. Every number downstream
    would then describe a model that answered nothing.

    At this layer "passed" only means the provider returned a string. Whether the
    answer is any *good* is the judge's question, two stages later.
    """
    return isinstance(value, str)


@dataclass(frozen=True)
class RunArtifact:
    """One model's completions over one golden set, as read back from disk.

    ``parts`` is the number of header records in the file: 1 for a run that
    finished in one go, 2 for one that was interrupted and resumed once. The
    report prints it rather than hiding it.
    """

    header: RunHeader
    completions: tuple[Completion, ...]
    parts: int = 1
    path: str | None = None
    #: Every distinct adapter that contributed, in the order it first appears.
    #: Usually one. More than one means the run was resumed under a different
    #: adapter -- permitted between real providers, and disclosed here rather than
    #: hidden, because a reader deciding whether to trust a migration is entitled
    #: to know the evidence came from two client paths.
    adapters: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | Path) -> RunArtifact:
        """Read an artifact, tolerating a torn final line but nothing else.

        A truncated last line is the signature of a process killed mid-write, and
        it is exactly the case resumption has to survive; anything malformed
        earlier in the file is corruption and is refused. This mirrors how
        opik-rigor's evidence log reads.
        """
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ArtifactError(f"no run artifact at {target}") from exc
        except OSError as exc:
            raise ArtifactError(f"cannot read run artifact {target}: {exc}") from exc

        lines = raw.split("\n")
        trailing_complete = raw.endswith("\n")
        if trailing_complete:
            lines.pop()

        headers: list[RunHeader] = []
        completions: list[Completion] = []
        seen: set[tuple[str, int]] = set()
        for index, line in enumerate(lines):
            is_last = index == len(lines) - 1
            if not line.strip():
                if is_last and not trailing_complete:
                    continue
                raise ArtifactError(f"blank line at position {index + 1} in {target}")
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record is not a JSON object")
                kind = record.get("record")
                if kind == _RECORD_HEADER:
                    headers.append(RunHeader.from_dict(record))
                elif kind == _RECORD_COMPLETION:
                    completion = Completion.from_dict(record)
                    key = (completion.item_id, completion.sample_index)
                    if key in seen:
                        # Not a torn write -- two writers shared this path, and
                        # every count downstream would be inflated by the overlap.
                        raise ArtifactError(
                            f"{target} records {key[0]!r} sample {key[1]} twice. "
                            f"Two runs wrote to one artifact; re-run with fresh=True."
                        )
                    seen.add(key)
                    completions.append(completion)
                else:
                    raise ValueError(f"unknown record type {kind!r}")
            except ArtifactError:
                raise
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                if is_last and not trailing_complete:
                    continue  # torn write at end of file: the run was killed here
                raise ArtifactError(
                    f"malformed record at line {index + 1} of {target}: {exc}"
                ) from None

        if not headers:
            raise ArtifactError(
                f"{target} has no header record, so there is nothing to say which "
                f"model or golden set produced it"
            )
        head = headers[0]
        if head.schema_version > ARTIFACT_SCHEMA_VERSION:
            raise ArtifactError(
                f"{target} was written with artifact schema {head.schema_version}; "
                f"this build understands up to {ARTIFACT_SCHEMA_VERSION}. Refusing "
                f"to read it rather than misinterpret it."
            )
        for extra in headers[1:]:
            _require_same_identity(head, extra, target)
        adapters: list[str] = []
        for one in headers:
            if one.adapter and one.adapter not in adapters:
                adapters.append(one.adapter)
        return cls(
            header=head,
            completions=tuple(completions),
            parts=len(headers),
            path=str(target),
            adapters=tuple(adapters),
        )

    def counts(self) -> dict[str, int]:
        """Completions recorded per item id, failures included."""
        counts: dict[str, int] = {}
        for completion in self.completions:
            counts[completion.item_id] = counts.get(completion.item_id, 0) + 1
        return counts

    def completions_for(self, item_id: str) -> tuple[Completion, ...]:
        return tuple(c for c in self.completions if c.item_id == item_id)

    def failures(self) -> tuple[Completion, ...]:
        return tuple(c for c in self.completions if not c.ok)

    @property
    def items_expected(self) -> int | None:
        """How many items the golden set held when this run started, if recorded.

        Written into the header's notes so that completeness can be judged from
        the artifact alone. Without it, a run killed halfway loads perfectly
        cleanly and looks exactly like a complete run over a smaller set -- and
        because a run usually dies on the slow or timing-out items, the ones
        missing are disproportionately the hard ones. Comparing a complete
        baseline against a truncated candidate would then flatter the candidate,
        which is the single direction this tool must never err in.

        ``None`` for artifacts written before this field existed.
        """
        value = self.header.notes.get("goldenset_items")
        return int(value) if value is not None else None

    def is_complete(self, goldenset: GoldenSet | None = None) -> bool:
        """Whether every item has its full complement of draws.

        Takes the golden set when the caller has it, and falls back to the item
        count recorded in the header when it does not -- ``compare`` generally
        does not, since an artifact travels without the set that produced it.
        """
        counts = self.counts()
        n = self.header.n_per_item
        if goldenset is not None:
            return all(counts.get(item.id, 0) >= n for item in goldenset)
        expected = self.items_expected
        if expected is None:
            raise ArtifactError(
                f"{self.path} does not record how many items its golden set held, "
                f"so completeness cannot be judged without the set itself. Pass the "
                f"golden set, or re-run to produce an artifact that records it."
            )
        return len(counts) >= expected and all(count >= n for count in counts.values())

    def stats(self) -> dict[str, Any]:
        failures = self.failures()
        return {
            "model_id": self.header.model_id,
            "goldenset_hash": self.header.goldenset_hash,
            "n_per_item": self.header.n_per_item,
            "adapters": list(self.adapters),
            "items": len(self.counts()),
            "items_expected": self.items_expected,
            "completions": len(self.completions),
            "failures": len(failures),
            "parts": self.parts,
        }


def artifact_path_for(
    goldenset: GoldenSet, model_id: str, out_dir: str | Path = "."
) -> Path:
    """Where a run's artifact lives: keyed by (model, golden set), so two
    different sets cannot quietly land in one file and be compared later."""
    return Path(out_dir) / f"{artifact_stem(model_id, goldenset.hash)}.jsonl"


def run_goldenset(
    goldenset: GoldenSet,
    adapter: Adapter,
    *,
    out_dir: str | Path = ".",
    artifact: str | Path | None = None,
    n: int = DEFAULT_N,
    concurrency: int = 1,
    timeout: float | None = None,
    fresh: bool = False,
    evidence: EvidenceLog | None = None,
    prompt_builder: Callable[[GoldenItem], str] | None = None,
    on_item: Callable[[GoldenItem, tuple[Completion, ...]], None] | None = None,
) -> RunArtifact:
    """Sample every item in ``goldenset`` ``n`` times against ``adapter``.

    Args:
        goldenset: The validated set. Its hash keys the artifact.
        adapter: Any opik-rigor adapter -- ``FakeAdapter`` offline, a provider
            adapter with keys. Only the public ``model_id``/``complete`` seam is
            used.
        out_dir: Directory for the artifact when ``artifact`` is not given.
        artifact: Explicit artifact path, overriding ``out_dir``.
        n: Draws per item.
        concurrency: Thread-pool width *within* one item's n draws. Items
            themselves are processed in order, so the artifact grows in a
            predictable shape and a resume is easy to reason about.
        timeout: Per-draw budget in seconds. opik-rigor detects rather than
            enforces it -- a thread cannot be killed -- so an over-running call
            still completes and is then recorded as having missed its budget.
        fresh: Delete any existing artifact and start over instead of resuming.
            Refuses to delete an artifact belonging to a different model or golden
            set -- see ``_require_resumable``.
        evidence: Optional rigor evidence log. rigor writes its own
            ``sample.completed`` records to it; this module adds ``migkit.*``.
        prompt_builder: Turns an item into the prompt actually sent. Defaults to
            the item's ``input`` verbatim.
        on_item: Progress callback, called once per item with its completions.

    Returns:
        The artifact, re-read from disk rather than assembled in memory, so what
        the caller gets is exactly what a later reader will see.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"n must be an integer >= 1, got {n!r}")
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError(f"concurrency must be an integer >= 1, got {concurrency!r}")
    if timeout is not None and timeout <= 0:
        raise ValueError(f"timeout must be > 0 seconds or None, got {timeout!r}")

    model_id = adapter.model_id
    if not model_id or not model_id.strip():
        raise ValueError(
            "adapter.model_id is empty. The model string is half the artifact's "
            "identity and the whole of its filename; an empty one produces an "
            "artifact nothing can attribute."
        )
    target = Path(artifact) if artifact is not None else artifact_path_for(
        goldenset, model_id, out_dir
    )
    target.parent.mkdir(parents=True, exist_ok=True)

    header = RunHeader(
        model_id=model_id,
        goldenset_hash=goldenset.hash,
        goldenset_path=goldenset.path,
        n_per_item=n,
        created=utc_now(),
        adapter=type(adapter).__name__,
        notes={
            "goldenset_items": len(goldenset),
            "goldenset_file_hash": goldenset.file_hash,
        },
    )

    done: dict[str, int] = {}
    if target.exists() and target.stat().st_size > 0:
        existing = RunArtifact.load(target)
        # Checked even when overwriting. `fresh=True` is the remedy this module's
        # own error messages recommend, and two model ids that differ only in
        # characters the filename slug flattens (`gpt/4o` and `gpt-4o`) resolve to
        # one path -- so the recommended remedy would silently delete a baseline
        # that cost real money, and the operator would find out at compare time.
        _require_resumable(existing.header, header, target)
        if fresh:
            target.unlink()
        else:
            # Every recorded draw counts against the budget, including the ones
            # that errored. A failed draw is a draw: if model B times out on one
            # item in five, that is a fact about model B, and re-drawing until the
            # timeouts disappeared would launder an unreliable model into a
            # reliable one. The cost of that choice is real and is recorded in
            # PROGRESS.md: a provider outage that fails every draw bakes into the
            # artifact, and the only remedy in v0.1 is fresh=True.
            done = existing.counts()
    elif target.exists():
        # Zero bytes: a run killed between creating the file and finishing its
        # first header write, or a heal that truncated a lone fragment. There is
        # nothing to resume and nothing to lose, so start it rather than making
        # the crash-recovery path produce a state recovery cannot handle.
        target.unlink()

    writer = _ArtifactWriter(target)
    resumed = sum(min(count, n) for count in done.values())
    work_remains = any(done.get(item.id, 0) < n for item in goldenset)
    if work_remains:
        # Only when there is something to record. A header record per invocation
        # would make `parts` -- which the report prints as "completed in N parts"
        # -- count re-runs of an already-finished job, so a green CI step re-run
        # twice would attest to a run that was interrupted twice.
        writer.append({"record": _RECORD_HEADER, **header.to_dict()})
    if evidence is not None:
        evidence.append(
            EVENT_RUN_STARTED,
            {
                "model_id": model_id,
                "adapter": header.adapter,
                "goldenset_hash": goldenset.hash,
                "goldenset_path": goldenset.path,
                "items": len(goldenset),
                "n_per_item": n,
                "concurrency": concurrency,
                "timeout": timeout,
                "artifact": str(target),
                "resumed_from": resumed,
            },
        )

    build_prompt = prompt_builder or (lambda item: item.input)
    written = 0
    failures = 0
    for item in goldenset:
        already = done.get(item.id, 0)
        remaining = n - already
        if remaining <= 0:
            continue
        prompt = build_prompt(item)
        result = sample(
            lambda prompt=prompt: adapter.complete(prompt),
            remaining,
            concurrency=concurrency,
            timeout=timeout,
            outcome=_answered,
            evidence=evidence,
            label=f"{model_id}:{item.id}",
        )
        completions = tuple(
            _completion_from_run(run, item_id=item.id, offset=already) for run in result.runs
        )
        for completion in completions:
            writer.append({"record": _RECORD_COMPLETION, **completion.to_dict()})
            if evidence is not None:
                # One evidence line per completion, carrying the model string, as
                # the acceptance contract requires. The output text is deliberately
                # not duplicated here -- it is in the artifact, and copying every
                # response into the audit log would double the bytes for no fact
                # the log does not already point at.
                evidence.append(
                    EVENT_COMPLETION,
                    {
                        "model_id": model_id,
                        "item_id": completion.item_id,
                        "sample_index": completion.sample_index,
                        "duration": completion.duration,
                        "ok": completion.ok,
                        "error": completion.error,
                        "error_type": completion.error_type,
                        "tokens_in": completion.tokens_in,
                        "tokens_out": completion.tokens_out,
                    },
                )
        written += len(completions)
        item_failures = sum(1 for c in completions if not c.ok)
        failures += item_failures
        if evidence is not None:
            evidence.append(
                EVENT_ITEM_COMPLETED,
                {
                    "model_id": model_id,
                    "item_id": item.id,
                    "samples": len(completions),
                    "failures": item_failures,
                    "resumed_from": already,
                    "wall_clock": result.wall_clock,
                },
            )
        if on_item is not None:
            on_item(item, completions)

    if evidence is not None:
        evidence.append(
            EVENT_RUN_COMPLETED,
            {
                "model_id": model_id,
                "goldenset_hash": goldenset.hash,
                "artifact": str(target),
                "written": written,
                "skipped": resumed,
                "failures": failures,
            },
        )
    return RunArtifact.load(target)


def _completion_from_run(run: Any, *, item_id: str, offset: int) -> Completion:
    """Map one rigor ``Run`` onto a ``Completion``.

    An adapter that returns a non-string has broken the provider protocol, and the
    only place that can be noticed is here. Recording it as a failed completion
    keeps it in the denominator, where a silently stringified value would have
    counted as a real answer.
    """
    error = run.error
    if error is not None:
        message = str(error) or type(error).__name__
        return Completion(
            item_id=item_id,
            sample_index=offset + run.index,
            output=None,
            duration=run.duration,
            error=message,
            error_type=type(error).__name__,
        )
    value = run.value
    if not isinstance(value, str):
        return Completion(
            item_id=item_id,
            sample_index=offset + run.index,
            output=None,
            duration=run.duration,
            error=f"adapter returned {type(value).__name__}, not a string",
            error_type="AdapterProtocolError",
        )
    return Completion(
        item_id=item_id,
        sample_index=offset + run.index,
        output=value,
        duration=run.duration,
    )


def _require_same_identity(first: RunHeader, other: RunHeader, target: Path) -> None:
    for field_name in ("model_id", "goldenset_hash", "n_per_item", "schema_version"):
        if getattr(first, field_name) != getattr(other, field_name):
            raise ArtifactError(
                f"{target} contains header records that disagree on {field_name}: "
                f"{getattr(first, field_name)!r} then {getattr(other, field_name)!r}. "
                f"Parts of two different runs are in one file."
            )


def _require_resumable(existing: RunHeader, wanted: RunHeader, target: Path) -> None:
    """Refuse to append to an artifact that is not the same run.

    Every one of these would produce a file that *looks* like one run and is not,
    and the mismatch would only surface as a comparison between things that were
    never comparable.
    """
    if existing.schema_version > ARTIFACT_SCHEMA_VERSION:
        raise ArtifactError(
            f"{target} uses artifact schema {existing.schema_version}, newer than "
            f"this build's {ARTIFACT_SCHEMA_VERSION}"
        )
    if existing.model_id != wanted.model_id:
        raise ArtifactError(
            f"{target} holds a run of {existing.model_id!r}, not {wanted.model_id!r}. "
            f"Use a different artifact path, or fresh=True to overwrite."
        )
    if existing.goldenset_hash != wanted.goldenset_hash:
        raise ArtifactError(
            f"{target} was run against golden set {existing.goldenset_hash[:16]}, "
            f"but this run uses {wanted.goldenset_hash[:16]}. The set changed since "
            f"that run; resuming would mix two sets in one artifact. Re-run with "
            f"fresh=True."
        )
    if wanted.adapter == _FAKE_ADAPTER and existing.adapter not in ("", _FAKE_ADAPTER):
        # Asymmetric on purpose, and the asymmetry is the honest part.
        #
        # The hazard is synthetic completions joining real ones under one model
        # string: the artifact loads clean, reports no failures, and its header
        # attests to a provider that produced half of it. The one case that can be
        # identified with certainty is this one -- rigor's scripted fake, named as
        # itself, being pointed at a run that was not fake.
        #
        # What this cannot catch: a FakeAdapter behind any wrapper, since the
        # recorded name is a class name and a proxy reports its own. What it must
        # not do is fire on legitimate wrapping -- a retry proxy, an instrumentation
        # shim, the counting proxy a test uses to prove a resume did not re-sample
        # -- which is the same data from the same model. So a check that tried to
        # be thorough here would earn false positives on honest use and still miss
        # a determined one. Everything it does not refuse is instead disclosed:
        # RunArtifact.adapters lists every adapter that contributed, and the report
        # prints it. Recorded in PROGRESS.md as a known limit, not a solved problem.
        raise ArtifactError(
            f"{target} was produced by {existing.adapter}, and this run uses the "
            f"scripted {_FAKE_ADAPTER}. Resuming across that boundary would put "
            f"fabricated completions and real ones in one artifact, under one model "
            f"string, with nothing downstream able to tell them apart. Use "
            f"fresh=True and a separate path for the fake run."
        )
    if existing.n_per_item != wanted.n_per_item:
        raise ArtifactError(
            f"{target} was run at n={existing.n_per_item} per item, not "
            f"n={wanted.n_per_item}. Mixing sample sizes within one artifact would "
            f"weight some items more than others in every statistic downstream. "
            f"Re-run with fresh=True, or resume at n={existing.n_per_item}."
        )


class _ArtifactWriter:
    """Append-only JSONL writer, one whole line per write, fsynced.

    Same shape as opik-rigor's evidence log and for the same reason: a run that
    dies between items must leave a file whose every complete line is valid, so
    that resumption and partial reporting both work on whatever survived.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._heal_torn_tail()

    def _heal_torn_tail(self) -> None:
        """Drop a trailing partial line before appending anything after it.

        A run killed mid-write leaves a fragment with no newline at the end of the
        file. Reading tolerates that -- the fragment is dropped. Appending after it
        does not: the next record would be concatenated onto the fragment, and the
        result is one malformed line *in the middle* of the file, which is
        corruption the reader is right to refuse. So the fragment is truncated
        here, at the one moment it is provably worthless.

        This is the only write in this module that is not an append, and it is
        confined to bytes that were never a complete record. Nothing that was ever
        readable is removed.
        """
        if not self._path.exists():
            return
        with open(self._path, "r+b") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size == 0:
                return
            handle.seek(size - 1)
            if handle.read(1) == b"\n":
                return
            data = self._path.read_bytes()
            cut = data.rfind(b"\n") + 1  # 0 when the whole file is one fragment
            handle.truncate(cut)
            handle.flush()
            os.fsync(handle.fileno())

    def append(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        data = (line + "\n").encode("utf-8")
        # O_BINARY on Windows, absent (and unnecessary) elsewhere. Without it the
        # C runtime expands every \n to \r\n on the way out, so the same run
        # produces different bytes on Windows and Linux -- which defeats the whole
        # point of the newline-normalised hashing convention the moment anything
        # hashes an artifact as change-control evidence.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(self._path, flags, 0o644)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
            os.fsync(fd)
        finally:
            os.close(fd)

    def append_all(self, records: Iterable[Mapping[str, Any]]) -> None:
        for record in records:
            self.append(record)
