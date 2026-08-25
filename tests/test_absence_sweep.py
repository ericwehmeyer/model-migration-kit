"""A differential-render sweep: an absence must never render as a measured zero.

``CLAUDE.md`` states the project's central rule -- "a value that was never
recorded, a comparison that could not be made, and a measured zero must be
distinguishable on the page" -- and five chunks in a row have turned on it. Every
test that has caught a violation so far named the field it was checking, which
means the rule was only ever enforced where somebody thought to enforce it.

This file enforces it everywhere, by a technique an outside auditor used to find
a class of defects the suite had missed. For each leaf path in the comparison
payload it renders **five whole documents** that differ only in that field:

* ``plausible``      -- a value of the same type that is not the recorded one,
* ``zero``           -- a genuine measured zero,
* ``key-removed``    -- the key is not in the payload,
* ``key-null``       -- the key is present and ``null``,
* ``parent-removed`` -- the object holding the key is gone.

Then it compares the rendered text byte for byte. **Where ``zero`` and any of the
three absences render identically, the rule is broken**: a report reading a log
that never recorded the number prints the same page as one reading a log that
measured it to be nothing.

Two things about the technique had to be got right, and both cost the original
auditor a sweep.

**The mask.** The page prints a sha256 over the whole evidence file, so *every*
pair of documents differs no matter what was mutated, and a naive diff reports
zero findings while looking like a clean bill of health. The digest, the injected
``generated`` timestamp and the absolute scenario paths are replaced with
placeholders before comparing -- replaced, not dropped, so a field that stopped
carrying one would still show as a difference. That the mask is load-bearing is
not taken on faith: :func:`test_the_mask_is_what_lets_the_sweep_see_anything`
renders two documents that must be equal, shows they differ before masking, and
shows they are equal after.

**The paths come from the payload.** :func:`_sweep_leaf_paths` walks the payload
that ``tests/test_report.py`` builds. A hand-written list of fields would go stale
the day someone adds one, and silently -- which is the failure mode this file
exists to close, so it may not reintroduce it.

**What is swept and what is not.** Every leaf is enumerated; the ones whose
recorded value is a number (``bool`` excluded -- ``False`` is a measured negative,
not a zero, and the payload already uses it as a flag default) are rendered. A
measured zero does not exist for a string, a hash or a path, so those leaves have
no ``zero`` variant to compare against and are not rendered. That partition is
asserted total by
:func:`test_every_enumerated_leaf_is_either_swept_or_declared_out_of_scope`, so a
leaf of some third kind is a failure rather than a silent omission. The sweep also
sees only the leaves the standard scenario produces: ``warnings`` and ``unstable``
are empty there and contribute nothing, and no REVIEW/GO shape or multi-judge
shape is swept.

**Cost.** 93 numeric leaves at five variants is 465 documents, deduplicated to 361
distinct payloads (siblings share a ``parent-removed``), measured at 9.4 s wall /
2.7 s CPU on a loaded machine. The affordability came from deleting redundant work
rather than from sweeping fewer fields: ``report._environment()`` builds a fresh
jinja2 ``Environment`` per call, so every render recompiles the template, and 100
ms of each 109 ms render was compilation. Memoising it for the duration of the
sweep is worth ~25x, where the best path-subset rule considered was worth ~2x --
so this file gives up no coverage at all. The memoisation is not assumed to be
harmless either: :func:`test_the_memoised_environment_does_not_change_the_bytes`
renders four different payloads both ways and compares.
"""

from __future__ import annotations

import copy
import dataclasses
import difflib
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

import test_report as _sweep_fixtures
from model_migration_kit import report as _sweep_report
from model_migration_kit.contracts import (
    EVENT_COMPARISON,
    EVENT_JUDGING_COMPLETED,
    EVENT_RUN_STARTED,
    EVENT_VERDICT,
)

#: The three ways a field can be absent. ``key-null`` and ``parent-removed`` are
#: kept apart from ``key-removed`` on evidence, not on principle: the sweep finds
#: paths where a missing key renders as ``0`` while an explicit ``null`` does not.
SWEEP_ABSENCES = ("key-removed", "key-null", "parent-removed")

#: Values distinctive enough that finding them in a document is not a coincidence.
SWEEP_PLAUSIBLE_INT = 7919
SWEEP_PLAUSIBLE_FLOAT = 0.5309
SWEEP_PLAUSIBLE_SUFFIX = "-SWEEPMARK"

#: The paths where this project's central rule is broken on ``main`` today, each
#: mapped to the absences that render as the measured zero. Recorded rather than
#: fixed: this file is test infrastructure, and every entry is a live defect in
#: ``report.py`` for somebody else to rule on.
#:
#: Read it as five families:
#:
#: * ``n_per_item`` (three spellings) -- a log that never recorded how many draws
#:   were taken renders the same as one that took none.
#: * ``judges[*].{baseline,candidate}.{n,successes}`` -- a judge row with no ``n``
#:   prints "0 graded on the baseline ... a side that graded nothing has nothing
#:   for the other to be compared against", which is a measurement, stated.
#: * ``judges[*].{imputed,parse_failures}.*`` -- "imputed (failed completions
#:   scored at the floor) 0" is printed whether the count was zero or was never
#:   recorded at all, and also when the whole block is missing.
#: * ``judges[*].item_counts.*`` and ``item_counts.per_judge.*.*`` -- a missing key
#:   prints as ``0`` in the "items passing / failing / unstable" triple, while an
#:   explicit ``null`` prints ``None`` and a missing parent prints an em dash. Three
#:   different renderings of three kinds of nothing, one of which is a number.
SWEEP_RECORDED_CONFLATIONS: Mapping[str, tuple[str, ...]] = {
    "n_per_item": ("key-removed", "key-null"),
    "baseline.n_per_item": ("key-removed", "key-null"),
    "candidate.n_per_item": ("key-removed", "key-null"),
    "judges[0].baseline.n": ("key-removed", "key-null", "parent-removed"),
    "judges[0].baseline.successes": ("key-removed", "key-null"),
    "judges[0].candidate.n": ("key-removed", "key-null", "parent-removed"),
    "judges[0].candidate.successes": ("key-removed", "key-null"),
    "judges[0].imputed.baseline": ("key-removed", "key-null", "parent-removed"),
    "judges[0].imputed.candidate": ("key-removed", "key-null", "parent-removed"),
    "judges[0].parse_failures.baseline": ("key-removed", "key-null", "parent-removed"),
    "judges[0].parse_failures.candidate": ("key-removed", "key-null", "parent-removed"),
    "judges[0].item_counts.items": ("key-removed", "key-null"),
    "judges[0].item_counts.baseline.passing": ("key-removed",),
    "judges[0].item_counts.baseline.failing": ("key-removed",),
    "judges[0].item_counts.baseline.unstable": ("key-removed",),
    "judges[0].item_counts.candidate.passing": ("key-removed",),
    "judges[0].item_counts.candidate.failing": ("key-removed",),
    "judges[0].item_counts.candidate.unstable": ("key-removed",),
    "item_counts.per_judge.accuracy.baseline.passing": ("key-removed",),
    "item_counts.per_judge.accuracy.baseline.failing": ("key-removed",),
    "item_counts.per_judge.accuracy.baseline.unstable": ("key-removed",),
    "item_counts.per_judge.accuracy.candidate.passing": ("key-removed",),
    "item_counts.per_judge.accuracy.candidate.failing": ("key-removed",),
    "item_counts.per_judge.accuracy.candidate.unstable": ("key-removed",),
}

#: A floor, not a count. If the mask were over-broad -- masking the numbers as well
#: as the digest -- every variant would render alike, the sweep would report no
#: conflations, and that clean sheet would be a bug in this file. 48 numeric leaves
#: were visible when this was written; the floor is set well under it so that a
#: change to ``report.py`` moving a field off the page is not a failure here, while
#: a mask that blinds the sweep still is.
SWEEP_MIN_VISIBLE_LEAVES = 20


# --------------------------------------------------------------------------- #
# Walking the payload
# --------------------------------------------------------------------------- #


def _sweep_leaf_paths(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every leaf of ``node``, as ``("a.b[0].c", value)``.

    Derived from the payload, never from a list of field names. Anything that is
    not a ``dict`` or a ``list`` is a leaf, including ``None`` and the empty
    string; an empty container yields nothing, which is why an empty ``warnings``
    is invisible to the sweep and is called out in this module's docstring.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _sweep_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _sweep_leaf_paths(value, f"{prefix}[{index}]")
    else:
        yield prefix, node


def _sweep_steps(path: str) -> list[str | int]:
    """``"a.b[0].c"`` back into ``["a", "b", 0, "c"]``."""
    steps: list[str | int] = []
    token = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if token:
                steps.append(token)
            token = ""
        elif char == "[":
            if token:
                steps.append(token)
            token = ""
            close = path.index("]", index)
            steps.append(int(path[index + 1 : close]))
            index = close
        else:
            token += char
        index += 1
    if token:
        steps.append(token)
    return steps


def _sweep_container(payload: Any, steps: Sequence[str | int]) -> tuple[Any, str | int]:
    """The object holding ``steps[-1]``, and that last step."""
    node = payload
    for step in steps[:-1]:
        node = node[step]
    return node, steps[-1]


def _sweep_is_measured(value: Any) -> bool:
    """Is a *measured zero* a thing this value could have been?

    Numbers only. ``bool`` is excluded deliberately: ``False`` is a measured
    negative rather than a zero, and this payload uses it as the default for every
    flag, so a ``False``/absent pair says nothing about the rule. Strings, hashes
    and paths have no zero either -- and the codebase itself uses ``""`` to mean
    "nothing to say" (``judges[*].note``), so asserting that ``""`` must differ
    from absent would contradict the design rather than test it.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sweep_plausible(value: Any) -> Any:
    """A different value of the same type -- the control that proves the field renders."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return SWEEP_PLAUSIBLE_INT
    if isinstance(value, float):
        return SWEEP_PLAUSIBLE_FLOAT
    if isinstance(value, str):
        return value + SWEEP_PLAUSIBLE_SUFFIX
    return SWEEP_PLAUSIBLE_INT


def _sweep_five_payloads(payload: Mapping[str, Any], path: str) -> dict[str, dict[str, Any]]:
    """The five whole payloads for one leaf, keyed by variant name.

    ``parent-removed`` is omitted for a top-level leaf, where the parent is the
    payload itself and removing it would not be a variant of anything.
    """
    steps = _sweep_steps(path)
    built: dict[str, dict[str, Any]] = {}

    for name, make in (
        ("plausible", _sweep_plausible),
        ("zero", lambda _value: 0),
        ("key-null", lambda _value: None),
    ):
        one = copy.deepcopy(dict(payload))
        container, key = _sweep_container(one, steps)
        container[key] = make(container[key])
        built[name] = one

    one = copy.deepcopy(dict(payload))
    container, key = _sweep_container(one, steps)
    del container[key]
    built["key-removed"] = one

    if len(steps) > 1:
        one = copy.deepcopy(dict(payload))
        container, key = _sweep_container(one, steps[:-1])
        del container[key]
        built["parent-removed"] = one
    return built


# --------------------------------------------------------------------------- #
# Rendering, and the mask
# --------------------------------------------------------------------------- #


def _sweep_mask(text: str, evidence: Path, root: Path) -> str:
    """Everything derived from the whole file, or from where the file happens to be.

    Replaced with placeholders rather than deleted, so a document that *stopped*
    printing its evidence hash would still differ from one that prints it. The
    scenario root is masked, not whole paths, so a mutated ``artifact`` leaf is
    still visible as ``<ROOT>\\baseline.jsonl-SWEEPMARK``.
    """
    text = text.replace(_sweep_fixtures._hash_bytes(evidence.read_bytes()), "<EVIDENCE-HASH>")
    text = text.replace(_sweep_fixtures.NOW_A, "<GENERATED>")
    root_text = str(root)
    for spelling in (root_text, root_text.replace("\\", "/"), root_text.replace("\\", "\\\\")):
        text = text.replace(spelling, "<ROOT>")
    return text


def _sweep_render(scenario: Any, payload: Mapping[str, Any], *, mask: bool = True) -> str:
    """One whole document from one whole payload, through the real reader and renderer.

    A payload the reader refuses is an outcome, not an error: it is recorded as a
    distinct string so that "raises" can never be mistaken for "renders the same".
    """
    _sweep_fixtures._write_evidence(
        scenario.evidence,
        [
            _sweep_fixtures._record(
                EVENT_RUN_STARTED,
                {"model_id": _sweep_fixtures.BASELINE_MODEL},
                _sweep_fixtures.TS_JUDGING,
            ),
            _sweep_fixtures._record(
                EVENT_JUDGING_COMPLETED,
                {"model_id": _sweep_fixtures.CANDIDATE_MODEL},
                _sweep_fixtures.TS_JUDGING,
            ),
            _sweep_fixtures._record(EVENT_COMPARISON, payload, _sweep_fixtures.TS_COMPARISON),
            _sweep_fixtures._record(
                EVENT_VERDICT, scenario.verdict, _sweep_fixtures.TS_VERDICT
            ),
        ],
    )
    try:
        html = _sweep_fixtures._html(_sweep_fixtures._from_evidence(scenario))
    except Exception as exc:  # the refusal is an observation, not an error
        html = f"<<RAISED {type(exc).__name__}: {exc}>>"
    if not mask:
        return html
    return _sweep_mask(html, scenario.evidence, scenario.root)


def _sweep_excerpt(zero_text: str, plausible_text: str, limit: int = 14) -> str:
    """What the field looks like on the page: the zero side against the plausible side."""

    def lines(text: str) -> list[str]:
        if text.startswith("<<RAISED"):
            return [text]
        return [one.strip() for one in _sweep_fixtures._visible(text).splitlines() if one.strip()]

    diff = difflib.unified_diff(
        lines(zero_text),
        lines(plausible_text),
        "measured zero",
        "plausible value",
        n=1,
        lineterm="",
    )
    kept = list(diff)[:limit]
    return "\n".join(f"        {one}" for one in kept)


class _sweep_memoised_environment:
    """``report._environment()`` built once instead of once per render.

    It takes no arguments and closes over module constants, so every call returns
    an equivalent object; building it afresh recompiles the jinja2 template, which
    was 100 ms of each 109 ms render. Equivalence of the *bytes* is not assumed
    here -- :func:`test_the_memoised_environment_does_not_change_the_bytes` renders
    four payloads both ways.

    If the private name ever goes, the sweep still runs and still decides the same
    thing; it just takes about ten times as long, and
    :func:`test_the_sweep_can_still_reach_the_thing_that_makes_it_affordable` says
    so by name rather than leaving somebody to wonder why the suite got slower.
    """

    def __init__(self) -> None:
        self._original = getattr(_sweep_report, "_environment", None)

    def __enter__(self) -> None:
        if self._original is None:
            return
        cached = self._original()
        _sweep_report._environment = lambda: cached

    def __exit__(self, *exc_info: object) -> None:
        if self._original is not None:
            _sweep_report._environment = self._original


# --------------------------------------------------------------------------- #
# The sweep itself
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class SweepResult:
    """What one pass over the payload found."""

    leaves: tuple[str, ...]
    swept: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    visible: tuple[str, ...]
    conflations: Mapping[str, tuple[str, ...]]
    excerpts: Mapping[str, str]
    renders: int


def _sweep_scenario(tmp_path_factory: pytest.TempPathFactory) -> Any:
    return _sweep_fixtures._scenario(tmp_path_factory.mktemp("absence-sweep") / "run")


@pytest.fixture(scope="module")
def absence_sweep(tmp_path_factory: pytest.TempPathFactory) -> SweepResult:
    """Every numeric leaf, five documents each, deduplicated and masked.

    Module-scoped because it is the expensive thing in this file; under ``xdist``
    it runs once per worker that collects a test needing it.
    """
    scenario = _sweep_scenario(tmp_path_factory)
    payload = copy.deepcopy(scenario.comparison)
    leaves = list(_sweep_leaf_paths(payload))

    cache: dict[str, str] = {}

    def render(one: Mapping[str, Any]) -> str:
        key = json.dumps(one, sort_keys=True, default=str)
        if key not in cache:
            cache[key] = _sweep_render(scenario, one)
        return cache[key]

    swept: list[str] = []
    out_of_scope: list[str] = []
    visible: list[str] = []
    conflations: dict[str, tuple[str, ...]] = {}
    excerpts: dict[str, str] = {}

    with _sweep_memoised_environment():
        for path, value in leaves:
            if not _sweep_is_measured(value):
                out_of_scope.append(path)
                continue
            swept.append(path)
            rendered = {
                name: render(one) for name, one in _sweep_five_payloads(payload, path).items()
            }
            if rendered["zero"] == rendered["plausible"]:
                continue  # the report never puts this field on the page
            visible.append(path)
            hits = tuple(
                name for name in SWEEP_ABSENCES if rendered.get(name) == rendered["zero"]
            )
            if hits:
                conflations[path] = hits
                excerpts[path] = _sweep_excerpt(rendered["zero"], rendered["plausible"])

    return SweepResult(
        leaves=tuple(path for path, _value in leaves),
        swept=tuple(swept),
        out_of_scope=tuple(out_of_scope),
        visible=tuple(visible),
        conflations=conflations,
        excerpts=excerpts,
        renders=len(cache),
    )


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #


@pytest.mark.slow
@pytest.mark.xfail(
    strict=True,
    reason=(
        "24 paths render an absence as a measured zero on main -- n_per_item and its "
        "two per-side spellings, judges[0].{baseline,candidate}.{n,successes}, "
        "judges[0].{imputed,parse_failures}.{baseline,candidate}, "
        "judges[0].item_counts.* and item_counts.per_judge.accuracy.*. They are listed "
        "one by one in SWEEP_RECORDED_CONFLATIONS with the absences that conflate, and "
        "pinned as a set by test_the_open_conflations_are_exactly_the_ones_recorded. "
        "This marker comes off with the fix, not with a narrower sweep."
    ),
)
def test_an_absence_never_renders_as_a_measured_zero(absence_sweep: SweepResult) -> None:
    """The rule in ``CLAUDE.md``, over every numeric leaf of the payload at once.

    This assertion is the whole of the claim: not one conflation anywhere. It is
    expected to fail, and the ``xfail`` is ``strict`` so that fixing the last of
    them turns the pass into a failure telling somebody to delete the marker.
    """
    report_lines = [
        f"{len(absence_sweep.conflations)} of {len(absence_sweep.visible)} rendered leaves "
        f"print an absence as a measured zero:"
    ]
    for path, hits in sorted(absence_sweep.conflations.items()):
        report_lines.append(f"\n    {path}  zero == {', '.join(hits)}")
        report_lines.append(absence_sweep.excerpts[path])
    assert not absence_sweep.conflations, "\n".join(report_lines)


@pytest.mark.slow
def test_the_open_conflations_are_exactly_the_ones_recorded(absence_sweep: SweepResult) -> None:
    """The regression half, which is not ``xfail`` and must stay green.

    It asserts only what its name says: that the broken set is the recorded one.
    It does *not* assert the rule holds -- that is the test above. A new conflation
    fails here rather than disappearing into the ``xfail``; a fixed one fails here
    rather than sitting in the ledger forever.
    """
    found = {path: tuple(hits) for path, hits in absence_sweep.conflations.items()}
    recorded = {path: tuple(hits) for path, hits in SWEEP_RECORDED_CONFLATIONS.items()}

    unrecorded = sorted(set(found) - set(recorded))
    detail = "\n".join(
        f"\n    NEW {path}  zero == {', '.join(found[path])}\n{absence_sweep.excerpts[path]}"
        for path in unrecorded
    )
    assert not unrecorded, f"absence-as-measurement in paths nobody has ruled on:{detail}"

    stale = sorted(set(recorded) - set(found))
    assert not stale, (
        "these paths no longer conflate; delete them from SWEEP_RECORDED_CONFLATIONS "
        f"and check whether the xfail above can come off too: {stale}"
    )
    assert found == recorded, (
        "the same paths conflate but on different absences -- "
        f"{[(p, recorded[p], found[p]) for p in sorted(found) if found[p] != recorded[p]]}"
    )


@pytest.mark.slow
def test_every_enumerated_leaf_is_either_swept_or_declared_out_of_scope(
    absence_sweep: SweepResult,
) -> None:
    """The selection rule, asserted rather than described.

    R37 named the failure this guards against: a test whose docstring claims more
    than its assertion checks. The sweep renders the numeric leaves and skips the
    rest, and the two sets must add up to every leaf the payload has -- so a leaf
    of some third kind cannot be dropped on the floor unnoticed. It also pins the
    floor on how many leaves were seen on the page at all, because a sweep that
    sees nothing reports nothing and looks like good news.
    """
    assert set(absence_sweep.swept) | set(absence_sweep.out_of_scope) == set(
        absence_sweep.leaves
    ), "a leaf was neither swept nor declared out of scope"
    assert not set(absence_sweep.swept) & set(absence_sweep.out_of_scope)
    assert absence_sweep.leaves, "the payload enumerated no leaves at all -- harness bug"
    assert len(absence_sweep.visible) >= SWEEP_MIN_VISIBLE_LEAVES, (
        f"only {len(absence_sweep.visible)} numeric leaves changed the document when their "
        f"value changed; below {SWEEP_MIN_VISIBLE_LEAVES} this sweep is not looking at "
        f"anything and its silence means nothing"
    )


def test_the_leaf_paths_come_from_the_payload_and_not_from_a_list() -> None:
    """A field added tomorrow is swept tomorrow, with no edit to this file.

    The whole point of enumerating rather than listing. Checked on a payload shape
    this file has never seen, so it cannot be passing by recognising something.
    """
    payload = {
        "top": 1,
        "nested": {"deep": {"newly_added_field": 0}},
        "rows": [{"a": 1}, {"a": 2, "b": {"c": 3}}],
        "empty_list": [],
        "empty_map": {},
    }
    found = dict(_sweep_leaf_paths(payload))

    assert set(found) == {
        "top",
        "nested.deep.newly_added_field",
        "rows[0].a",
        "rows[1].a",
        "rows[1].b.c",
    }
    assert found["nested.deep.newly_added_field"] == 0
    for path in found:
        container, key = _sweep_container(payload, _sweep_steps(path))
        assert container[key] == found[path], f"{path} did not round-trip through _sweep_steps"


def test_the_mask_is_what_lets_the_sweep_see_anything(tmp_path: Path) -> None:
    """The trap that returned an empty sweep for the auditor who found this class.

    Two payloads that must render the same document -- a key removed, and the same
    key set to ``null``, at a path where the sweep has recorded that both print the
    identical page -- are rendered twice. Unmasked they differ, because the page
    prints a sha256 over the whole evidence file and the two files are not the same
    bytes. Masked they are equal. Without this, every comparison in the file would
    report "different" and the sweep would find nothing while looking clean.
    """
    scenario = _sweep_fixtures._scenario(tmp_path / "mask")
    payload = copy.deepcopy(scenario.comparison)
    path = "judges[0].parse_failures.baseline"
    assert path in SWEEP_RECORDED_CONFLATIONS, "this test is pinned to a recorded conflation"
    built = _sweep_five_payloads(payload, path)

    # Masked *while* each document's own log is still the one on disk. Masking both
    # afterwards would mask only the last one's digest, which is a trap of its own:
    # this test failed that way first, and the message it printed was the message it
    # prints when the mask does not work at all.
    raw: dict[str, str] = {}
    masked: dict[str, str] = {}
    for name in ("key-removed", "key-null"):
        raw[name] = _sweep_render(scenario, built[name], mask=False)
        masked[name] = _sweep_mask(raw[name], scenario.evidence, scenario.root)

    assert raw["key-removed"] != raw["key-null"], (
        "the two documents are already identical unmasked, so this test is no longer "
        "demonstrating the trap it was written for"
    )
    assert masked["key-removed"] == masked["key-null"], (
        "masking did not remove the whole-file digest, so every pair in the sweep "
        "differs and the sweep can never report anything"
    )
    assert "<EVIDENCE-HASH>" in masked["key-removed"], "nothing was actually masked"


def test_the_memoised_environment_does_not_change_the_bytes(tmp_path: Path) -> None:
    """The speed-up may not be allowed to change what is being tested.

    ``report._environment()`` takes no arguments and closes over module constants,
    so reusing one is expected to be byte-neutral -- but "expected" is how a
    harness starts lying. Four different payloads are rendered both ways.
    """
    scenario = _sweep_fixtures._scenario(tmp_path / "memo")
    payload = copy.deepcopy(scenario.comparison)
    built = _sweep_five_payloads(payload, "judges[0].baseline.n")
    payloads = [payload, built["plausible"], built["zero"], built["key-removed"]]

    plain = [_sweep_render(scenario, one) for one in payloads]
    with _sweep_memoised_environment():
        memoised = [_sweep_render(scenario, one) for one in payloads]

    assert memoised == plain, "the memoised jinja environment changed the rendered bytes"


def test_the_sweep_can_still_reach_the_thing_that_makes_it_affordable() -> None:
    """A rename that costs the sweep ten times its runtime should say so by name.

    ``_environment`` is private and this file has no claim on it. The sweep is
    correct without it and this is not a failure of the rule -- it is a notice that
    someone should re-point :class:`_sweep_memoised_environment` or accept ~90
    seconds in the suite.
    """
    assert hasattr(_sweep_report, "_environment"), (
        "report._environment is gone; the sweep now recompiles the jinja template "
        "once per rendered document, which was measured at 100 ms of each 109 ms"
    )
