"""Grading a run artifact with pinned judges.

One judged artifact is one run artifact seen through one panel of judges. The
panel is built once per comparison and used for both sides, which is what makes
"the same instruments graded both models" a structural fact rather than a promise
in a README: there is no code path that constructs a second panel.

Three rules here are load-bearing, and each exists because of a measured way the
alternative gets a migration decision backwards.

**A failed completion is graded, not skipped.** It scores ``SCORE_MIN`` with
``imputed=True``. Skipping it looks harmless -- there is no output to grade -- and
it inverts the tool: a candidate that times out on two items and a candidate that
answers those two items badly can post identical pass counts, and if the timeouts
simply vanish from the score arrays the crasher wins the regression test outright.
Passing ``None`` through instead is not an option either; rigor rejects a ``None``
in a score array, so one failure anywhere would abort the whole comparison.

**A judge that cannot parse its own model's answer is an instrument failure, not
a model failure.** Those records are excluded from the pass rate and counted
separately, and if they exceed the tolerance the comparison aborts. An unreliable
judge does not produce a cautious verdict; it produces a meaningless one.

**Judge names are unique, enforced at config load.** Three separate things key on
the name -- the judges hash, the resume key, and rigor's own rubric-drift lookup,
which filters ``judge.init`` records by name alone. Two judges sharing a name make
all three wrong simultaneously.

**Judging is parallel, and the file it writes does not know that.** Grading was
strictly serial while sampling had a thread pool, which is not a small asymmetry
at the scale this tool recommends: the README asks for roughly 200 completions per
side, and two sides under one judge is about 460 provider calls, so at a second per
call that is 460 seconds of unparallelisable judging on top of 92 seconds of
sampling. ``judge_artifact`` now takes a ``concurrency``, and the one property that
may not move is that the artifact is byte-identical whatever it is set to.
:func:`_graded_in_order` is what buys that: work is submitted in the order
``pending`` lists it and consumed in the same order, through a window rather than
all at once, so the writer sees exactly the sequence the serial loop saw. The
records land in one order, the calls happen in another, and only the second one
depends on how many threads there are.
"""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opik_rigor import (
    SCORE_MIN,
    Adapter,
    EvidenceLog,
    JudgeOutputError,
    ModelPinError,
    PinnedJudge,
    hash_rubric_file,
    require_pinned,
)

from .contracts import (
    ARTIFACT_SCHEMA_VERSION,
    EVENT_JUDGING_COMPLETED,
    artifact_stem,
    canonical_json,
    hash_bytes,
    utc_now,
)
from .errors import ArtifactError, ConfigError, JudgeConfigError, JudgeReliabilityError
from .goldenset import GoldenSet
from .runner import RunArtifact

try:  # tomllib is 3.11+; the floor is 3.10 and CI actually runs it.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 in CI
    import tomli as tomllib  # type: ignore[no-redef]

_RECORD_HEADER = "header"
_RECORD_VERDICT = "verdict"

_JUDGE_KEYS = frozenset({"name", "model", "rubric", "adapter"})
_THRESHOLD_KEYS = frozenset(
    {
        "pass_rate_floor",
        "alpha",
        "confidence",
        "judge_failure_tolerance",
        "min_detectable_effect",
        "power_target",
    }
)


@dataclass(frozen=True)
class Thresholds:
    """Every number that can change a verdict, in one place.

    All of them are echoed into the report beside the verdict they produced. A
    gate nobody can see loosened is not a gate, and the defaults are chosen to be
    defensible rather than easy to pass.
    """

    pass_rate_floor: float = 0.90
    alpha: float = 0.05
    confidence: float = 0.95
    judge_failure_tolerance: float = 0.05
    #: The regression this tool promises to be able to notice: a ten-point drop in
    #: pass rate. Detecting it takes roughly 200 completions per side, which is why
    #: a small golden set gets REVIEW rather than GO -- see build-plan.md §6.
    min_detectable_effect: float = 0.10
    power_target: float = 0.80

    def __post_init__(self) -> None:
        for name, value, lo, hi, inclusive in (
            ("pass_rate_floor", self.pass_rate_floor, 0.0, 1.0, True),
            ("alpha", self.alpha, 0.0, 1.0, False),
            ("confidence", self.confidence, 0.0, 1.0, False),
            ("judge_failure_tolerance", self.judge_failure_tolerance, 0.0, 1.0, True),
            ("min_detectable_effect", self.min_detectable_effect, 0.0, 1.0, False),
            ("power_target", self.power_target, 0.0, 1.0, False),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"threshold {name!r} must be a number, got {value!r}")
            ok = lo <= value <= hi if inclusive else lo < value < hi
            if not ok:
                bounds = f"[{lo}, {hi}]" if inclusive else f"({lo}, {hi})"
                raise ConfigError(f"threshold {name!r} must be in {bounds}, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_rate_floor": self.pass_rate_floor,
            "alpha": self.alpha,
            "confidence": self.confidence,
            "judge_failure_tolerance": self.judge_failure_tolerance,
            "min_detectable_effect": self.min_detectable_effect,
            "power_target": self.power_target,
        }


@dataclass(frozen=True)
class JudgeSpec:
    """One judge as the config declares it, before it has an adapter."""

    name: str
    model: str
    rubric: Path
    rubric_hash: str
    adapter: str = ""

    def identity(self, adapter_class: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "adapter_class": adapter_class,
            "rubric_hash": self.rubric_hash,
        }


@dataclass(frozen=True)
class JudgeConfig:
    """The parsed TOML: which judges, and every threshold."""

    specs: tuple[JudgeSpec, ...]
    thresholds: Thresholds
    path: str

    @classmethod
    def load(cls, path: str | Path) -> JudgeConfig:
        target = Path(path)
        try:
            raw = tomllib.loads(target.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read judge config {target}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{target} is not valid TOML: {exc}") from exc
        return cls.parse(raw, source=target)

    @classmethod
    def parse(cls, raw: Mapping[str, Any], *, source: str | Path = "<memory>") -> JudgeConfig:
        where = str(source)
        base = Path(where).parent if Path(where).name else Path()
        unknown = sorted(set(raw) - {"judge", "thresholds"})
        if unknown:
            raise ConfigError(
                f"{where}: unknown top-level key(s) {', '.join(repr(k) for k in unknown)}; "
                f"expected 'judge' and 'thresholds'"
            )

        declared = raw.get("judge") or []
        if not isinstance(declared, list) or not declared:
            raise ConfigError(
                f"{where}: at least one [[judge]] is required. A comparison with no "
                f"judge measures nothing."
            )

        specs: list[JudgeSpec] = []
        seen: set[str] = set()
        for index, entry in enumerate(declared, start=1):
            if not isinstance(entry, Mapping):
                raise ConfigError(f"{where}: [[judge]] #{index} is not a table")
            extra = sorted(set(entry) - _JUDGE_KEYS)
            if extra:
                raise ConfigError(
                    f"{where}: judge #{index} has unknown key(s) "
                    f"{', '.join(repr(k) for k in extra)}; allowed: "
                    f"{', '.join(sorted(_JUDGE_KEYS))}"
                )
            name = _required_str(entry, "name", where, index)
            model = _required_str(entry, "model", where, index)
            try:
                # Refused here, at load, and not left to PinnedJudge construction.
                # Both refuse it, but they refuse it at different moments: this one
                # happens while the operator is still looking at the config they
                # just edited, before any credential is spent. rigor makes the same
                # argument one level down about discovering an alias at analysis
                # time -- a week of verdicts from a moving target is a week wasted.
                require_pinned(model, context=f"judge {name!r} in {where}")
            except ModelPinError as exc:
                raise ConfigError(str(exc)) from exc
            if name in seen:
                raise ConfigError(
                    f"{where}: two judges are named {name!r}. The name keys the judges "
                    f"hash, the resume key, and rigor's rubric-drift lookup, so a "
                    f"duplicate makes all three wrong at once."
                )
            seen.add(name)
            rubric = Path(_required_str(entry, "rubric", where, index))
            if not rubric.is_absolute():
                rubric = base / rubric
            if not rubric.is_file():
                raise ConfigError(f"{where}: judge {name!r} has no rubric at {rubric}")
            specs.append(
                JudgeSpec(
                    name=name,
                    model=model,
                    rubric=rubric,
                    rubric_hash=hash_rubric_file(rubric),
                    adapter=str(entry.get("adapter", "")),
                )
            )

        thresholds_raw = raw.get("thresholds") or {}
        if not isinstance(thresholds_raw, Mapping):
            raise ConfigError(f"{where}: [thresholds] must be a table")
        bad = sorted(set(thresholds_raw) - _THRESHOLD_KEYS)
        if bad:
            raise ConfigError(
                f"{where}: unknown threshold(s) {', '.join(repr(k) for k in bad)}; "
                f"allowed: {', '.join(sorted(_THRESHOLD_KEYS))}. An unknown threshold "
                f"is more likely a typo silently leaving a gate at its default than a "
                f"setting worth ignoring."
            )
        return cls(
            specs=tuple(specs),
            thresholds=Thresholds(**dict(thresholds_raw)),
            path=where,
        )

    def build(
        self,
        evidence: EvidenceLog,
        adapter_for: Callable[[JudgeSpec], Adapter],
        *,
        accept_rubric_change: bool = False,
    ) -> JudgePanel:
        """Construct every judge once. The panel is what grades both sides.

        ``adapter_for`` is supplied by the caller rather than divined from the
        model string: the CLI knows which provider it configured, and a test knows
        it wants a scripted fake. Guessing here would mean this module deciding,
        from a substring, which credential to spend.
        """
        judges: list[PinnedJudge] = []
        identities: list[dict[str, Any]] = []
        for spec in self.specs:
            adapter = adapter_for(spec)
            judges.append(
                PinnedJudge(
                    adapter,
                    spec.rubric,
                    evidence,
                    name=spec.name,
                    accept_rubric_change=accept_rubric_change,
                )
            )
            identities.append(spec.identity(type(adapter).__name__))
        return JudgePanel(
            judges=tuple(judges),
            specs=self.specs,
            identities=tuple(identities),
            thresholds=self.thresholds,
        )


@dataclass(frozen=True)
class JudgePanel:
    """Constructed judges plus the hash that decides comparability."""

    judges: tuple[PinnedJudge, ...]
    specs: tuple[JudgeSpec, ...]
    identities: tuple[Mapping[str, Any], ...]
    thresholds: Thresholds

    @property
    def judges_hash(self) -> str:
        """Identity of the instrument, not of the ruler applied to its readings.

        Covers judge name, model string, adapter class and rubric *content*. The
        adapter class is in there because two adapters pointed at one model id are
        two different instruments -- and without it they hash equal. Thresholds are
        deliberately out: they change what the verdict concludes, not what was
        measured, and they are echoed into the report separately so a loosened gate
        still shows up in the evidence.

        Sorted by the whole identity tuple rather than by name, so ordering cannot
        depend on how the file happened to be written.
        """
        ordered = sorted(
            (dict(one) for one in self.identities),
            key=lambda one: (one["name"], one["model"], one["adapter_class"], one["rubric_hash"]),
        )
        return hash_bytes(canonical_json({"judges": ordered}))

    def named(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs)


@dataclass(frozen=True)
class JudgeRecord:
    """One judge's reading of one completion.

    ``imputed`` marks a score this module supplied rather than a judge produced --
    a completion that failed, and therefore has no output to grade. ``parse_failure``
    marks the opposite kind of hole: the completion was fine and the *judge* could
    not be understood. The two are counted differently everywhere downstream, and
    conflating them would let an unreliable judge read as an unreliable model.
    """

    judge: str
    item_id: str
    sample_index: int
    passed: bool
    score: float | None = None
    imputed: bool = False
    parse_failure: bool = False
    reason: str | None = None
    error: str | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.judge, self.item_id, self.sample_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge": self.judge,
            "item_id": self.item_id,
            "sample_index": self.sample_index,
            "passed": self.passed,
            "score": self.score,
            "imputed": self.imputed,
            "parse_failure": self.parse_failure,
            "reason": self.reason,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> JudgeRecord:
        return cls(
            judge=raw["judge"],
            item_id=raw["item_id"],
            sample_index=int(raw["sample_index"]),
            passed=bool(raw["passed"]),
            score=None if raw.get("score") is None else float(raw["score"]),
            imputed=bool(raw.get("imputed", False)),
            parse_failure=bool(raw.get("parse_failure", False)),
            reason=raw.get("reason"),
            error=raw.get("error"),
        )


@dataclass(frozen=True)
class JudgedArtifact:
    """A run artifact plus every judge's reading of it."""

    model_id: str
    goldenset_hash: str
    judges_hash: str
    n_per_item: int
    records: tuple[JudgeRecord, ...]
    judges: tuple[Mapping[str, Any], ...] = ()
    source: str = ""
    parts: int = 1
    path: str | None = None
    notes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> JudgedArtifact:
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ArtifactError(f"no judged artifact at {target}") from exc
        except OSError as exc:
            raise ArtifactError(f"cannot read judged artifact {target}: {exc}") from exc

        lines = raw.split("\n")
        trailing_complete = raw.endswith("\n")
        if trailing_complete:
            lines.pop()

        headers: list[Mapping[str, Any]] = []
        records: list[JudgeRecord] = []
        seen: set[tuple[str, str, int]] = set()
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
                    headers.append(record)
                elif kind == _RECORD_VERDICT:
                    one = JudgeRecord.from_dict(record)
                    if one.key in seen:
                        raise ArtifactError(
                            f"{target} records {one.key} twice. rigor writes one "
                            f"verdict per call and does not dedupe, so a double "
                            f"entry would be counted twice in every rate."
                        )
                    seen.add(one.key)
                    records.append(one)
                else:
                    raise ValueError(f"unknown record type {kind!r}")
            except ArtifactError:
                raise
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                if is_last and not trailing_complete:
                    continue  # torn write at the end: the run was killed here
                raise ArtifactError(
                    f"malformed record at line {index + 1} of {target}: {exc}"
                ) from None

        if not headers:
            raise ArtifactError(f"{target} has no header record")
        head = headers[0]
        if int(head.get("schema_version", ARTIFACT_SCHEMA_VERSION)) > ARTIFACT_SCHEMA_VERSION:
            raise ArtifactError(
                f"{target} was written with artifact schema "
                f"{head['schema_version']}; this build understands up to "
                f"{ARTIFACT_SCHEMA_VERSION}"
            )
        for extra in headers[1:]:
            for key in ("model_id", "goldenset_hash", "judges_hash", "n_per_item"):
                if head.get(key) != extra.get(key):
                    raise ArtifactError(
                        f"{target} contains header records that disagree on {key}"
                    )
        return cls(
            model_id=head["model_id"],
            goldenset_hash=head["goldenset_hash"],
            judges_hash=head["judges_hash"],
            n_per_item=int(head["n_per_item"]),
            records=tuple(records),
            judges=tuple(head.get("judges", ())),
            source=head.get("source", ""),
            parts=len(headers),
            path=str(target),
            notes=head.get("notes", {}),
        )

    def for_judge(self, judge: str) -> tuple[JudgeRecord, ...]:
        return tuple(one for one in self.records if one.judge == judge)

    def judge_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for one in self.records:
            if one.judge not in names:
                names.append(one.judge)
        return tuple(names)

    def coverage(self) -> dict[tuple[str, str], int]:
        """(judge, item) -> how many samples were graded. The comparability key."""
        counts: dict[tuple[str, str], int] = {}
        for one in self.records:
            key = (one.judge, one.item_id)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def stats(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "judges": list(self.judge_names()),
            "records": len(self.records),
            "imputed": sum(1 for one in self.records if one.imputed),
            "parse_failures": sum(1 for one in self.records if one.parse_failure),
            "parts": self.parts,
        }


def judged_path_for(artifact: RunArtifact, out_dir: str | Path = ".") -> Path:
    """Where a judged artifact lands, beside the run artifact it grades.

    The fallback branch slugs, and that is the whole point of it. A model id is an
    arbitrary provider string, and this branch used it as a filename raw: a
    ``model_id`` of ``../../../evil`` produced
    ``out_dir/../../../evil.judged.jsonl`` and wrote outside the directory the
    caller named. Unreachable from any shipped flow -- ``RunArtifact`` is always
    constructed with a real path, so the first branch always wins -- but a latent
    trap for a library caller, and ``artifact_path_for`` has slugged the same
    string through ``artifact_stem`` all along. The two now agree.
    """
    if artifact.path:
        stem = Path(artifact.path).stem
    else:
        stem = artifact_stem(artifact.header.model_id, artifact.header.goldenset_hash)
    return Path(out_dir) / f"{stem}.judged.jsonl"


def judge_artifact(
    artifact: RunArtifact,
    goldenset: GoldenSet,
    panel: JudgePanel,
    *,
    evidence: EvidenceLog,
    out_dir: str | Path = ".",
    judged: str | Path | None = None,
    fresh: bool = False,
    concurrency: int = 1,
) -> JudgedArtifact:
    """Grade every completion in ``artifact`` with every judge in ``panel``.

    Resumable on ``(judge, item_id, sample_index)``: a judging pass killed halfway
    picks up where it stopped rather than paying for the graded half twice.

    Args:
        concurrency: How many judge calls may be in flight. ``1`` grades in this
            thread and submits nothing. Above that, calls overlap but the records
            are written in the order a serial pass would have written them, so the
            judged artifact is byte-identical at every setting -- see
            :func:`_graded_in_order` and the module docstring. This is a width on
            the *provider*, not on the writer: every record is still appended and
            fsynced one at a time from this thread, which is why a run against a
            zero-latency adapter sees no speedup at all and a run against a real
            provider sees most of one.

    Raises:
        ArtifactError: if the golden set is not the one the run used.
        JudgeReliabilityError: if a judge failed to parse over its tolerance. The
            pass completes first, so the count in the message is the true one and
            not the count at the moment the threshold was crossed.
        ValueError: if ``concurrency`` is not an integer >= 1. Refused rather than
            clamped, as ``run_goldenset`` refuses the same thing: a clamped setting
            is a silently different setting.
    """
    if not isinstance(concurrency, int) or isinstance(concurrency, bool) or concurrency < 1:
        raise ValueError(f"concurrency must be an integer >= 1, got {concurrency!r}")
    if goldenset.hash != artifact.header.goldenset_hash:
        raise ArtifactError(
            f"golden set {goldenset.hash[:16]} is not the one this run used "
            f"({artifact.header.goldenset_hash[:16]}). The inputs a judge grades "
            f"against have to be the inputs the model answered."
        )

    target = Path(judged) if judged is not None else judged_path_for(artifact, out_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if fresh and target.exists():
        target.unlink()

    done: set[tuple[str, str, int]] = set()
    if target.exists() and target.stat().st_size > 0:
        existing = JudgedArtifact.load(target)
        if existing.judges_hash != panel.judges_hash:
            raise JudgeConfigError(
                f"{target} was judged by a different panel "
                f"({existing.judges_hash[:16]} vs {panel.judges_hash[:16]}). Scores "
                f"from two panels are readings from two instruments; resuming would "
                f"mix them in one file."
            )
        done = {one.key for one in existing.records}
    elif target.exists():
        target.unlink()

    header = {
        "record": _RECORD_HEADER,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": artifact.header.model_id,
        "goldenset_hash": artifact.header.goldenset_hash,
        "judges_hash": panel.judges_hash,
        "judges": [dict(one) for one in panel.identities],
        "n_per_item": artifact.header.n_per_item,
        "source": artifact.path or "",
        "created": utc_now(),
        "notes": {"thresholds": panel.thresholds.to_dict()},
    }

    inputs = {item.id: item.input for item in goldenset}
    pending = [
        (judge, completion)
        for judge in panel.judges
        for completion in artifact.completions
        if (judge.name, completion.item_id, completion.sample_index) not in done
    ]

    writer = _JudgedWriter(target)
    if pending:
        writer.append(header)

    graded: dict[str, int] = {}
    parse_failures: dict[str, int] = {}
    imputed: dict[str, int] = {}
    for record in _graded_in_order(pending, inputs, concurrency):
        writer.append({"record": _RECORD_VERDICT, **record.to_dict()})
        graded[record.judge] = graded.get(record.judge, 0) + 1
        if record.parse_failure:
            parse_failures[record.judge] = parse_failures.get(record.judge, 0) + 1
        if record.imputed:
            imputed[record.judge] = imputed.get(record.judge, 0) + 1

    result = JudgedArtifact.load(target)
    evidence.append(
        EVENT_JUDGING_COMPLETED,
        {
            "model_id": artifact.header.model_id,
            "judges_hash": panel.judges_hash,
            "judged": str(target),
            "graded": graded,
            "parse_failures": parse_failures,
            "imputed": imputed,
        },
    )

    tolerance = panel.thresholds.judge_failure_tolerance
    for name in panel.named():
        records = result.for_judge(name)
        failures = sum(1 for one in records if one.parse_failure)
        if records and failures / len(records) > tolerance:
            raise JudgeReliabilityError(name, failures, len(records), tolerance)
    return result


#: How far ahead of the writer :func:`_graded_in_order` may run, as a multiple of
#: the pool width. Two is enough to keep every worker fed while one result is being
#: fsynced, and small enough that a panel grading 450,000 completions holds a
#: handful of verdicts rather than all of them -- the whole point of not calling
#: ``executor.map``, which submits the entire iterable up front.
_WINDOW_FACTOR = 2


def _graded_in_order(
    pending: Sequence[tuple[PinnedJudge, Any]],
    inputs: Mapping[str, str],
    concurrency: int,
) -> Iterator[JudgeRecord]:
    """Grade ``pending`` with ``concurrency`` calls in flight, yielding in order.

    The yielded order is ``pending``'s order, always, which is what makes the
    judged artifact byte-identical at every concurrency: the writer downstream sees
    the same sequence a serial pass produced, and the only thing that changed is
    when each provider call happened. Verified rather than asserted -- the test
    hashes the artifact at 1, 2, 4, 8, 16, 32 and 64 and demands one digest.

    A sliding window rather than ``executor.map``: at the scale this exists for
    (450,000 completions is 2.2 GB of evidence) submitting everything up front
    would allocate a future and a queued call per completion, which trades one
    memory problem for another.

    An exception from a grade propagates out of ``.result()`` here, exactly as it
    propagated out of the serial loop. Leaving the ``with`` block then waits for
    the calls already in flight; at most ``concurrency * _WINDOW_FACTOR`` of them
    exist, and none of their results is written, because the writer is downstream
    of this generator.
    """
    if concurrency == 1:
        for judge, completion in pending:
            yield _grade(judge, completion, inputs.get(completion.item_id, ""))
        return
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="migkit-judge") as pool:
        window: deque[Future[JudgeRecord]] = deque()
        for index, (judge, completion) in enumerate(pending):
            window.append(
                pool.submit(_grade, judge, completion, inputs.get(completion.item_id, ""))
            )
            if index >= concurrency * _WINDOW_FACTOR:
                yield window.popleft().result()
        while window:
            yield window.popleft().result()


def _grade(judge: PinnedJudge, completion: Any, item_input: str) -> JudgeRecord:
    if not completion.ok or completion.output is None:
        # No output to grade. Scored at the floor rather than skipped: skipping is
        # what lets a model that crashes beat a model that answers badly.
        return JudgeRecord(
            judge=judge.name,
            item_id=completion.item_id,
            sample_index=completion.sample_index,
            passed=False,
            score=SCORE_MIN,
            imputed=True,
            error=f"completion failed: {completion.error}",
        )
    try:
        verdict = judge.evaluate(item_input, completion.output)
    except JudgeOutputError as exc:
        # The judge, not the model, is what failed here. Excluded from the pass
        # rate and counted towards the tolerance instead.
        return JudgeRecord(
            judge=judge.name,
            item_id=completion.item_id,
            sample_index=completion.sample_index,
            passed=False,
            score=None,
            parse_failure=True,
            error=str(exc),
        )
    return JudgeRecord(
        judge=judge.name,
        item_id=completion.item_id,
        sample_index=completion.sample_index,
        passed=bool(verdict.passed),
        score=None if verdict.score is None else float(verdict.score),
        reason=verdict.reason,
    )


class _JudgedWriter:
    """Append-only JSONL, one whole line per write, fsynced. As runner.py."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._heal_torn_tail()

    def _heal_torn_tail(self) -> None:
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
            handle.truncate(data.rfind(b"\n") + 1)
            handle.flush()
            os.fsync(handle.fileno())

    def append(self, record: Mapping[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        data = (line + "\n").encode("utf-8")
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


def _required_str(entry: Mapping[str, Any], key: str, where: str, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: judge #{index} needs a non-empty string {key!r}")
    return value
