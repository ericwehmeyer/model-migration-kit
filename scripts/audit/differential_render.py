"""Sweep every leaf of the evidence payload and ask whether the page can tell absence apart.

The rule this exists to test is the central one in ``CLAUDE.md``:

    *An absence must not render as a measurement. A value that was never
    recorded, a comparison that could not be made, and a measured zero must be
    distinguishable on the page.*

Reading the template cannot answer that. The report is ~5,000 lines of Python and
Jinja with fallbacks, roll-ups and ``or 0`` defaults spread across both, and the
question is not what any one line does -- it is whether **the reader** of the
finished page can tell the three states apart. So this asks the page.

The method
----------
For one leaf path in the payload, render five documents that differ in that field
and in nothing else:

======  =====================================================================
``A``   a plausible non-zero **recorded** value
``B``   a genuine **measured zero** (``0`` / ``0.0`` / ``False`` / ``""`` /
        ``[]`` / ``{}``)
``C1``  the key **removed** entirely
``C2``  the key present with value ``null``
``C3``  the **parent object** removed entirely (only where the parent is a dict
        held under a string key)
======  =====================================================================

Flatten each to text and compare whole pages. Because the only difference between
two renders is that one field, any byte difference is attributable to that field,
and byte-identity means the field's region on the page is identical. No selector,
no guess about where the field is supposed to appear, and no way for a finding to
hide in a part of the page nobody thought to look at.

The verdicts:

``COLLISION``
    ``A != B`` and ``B == C1``/``C2``/``C3``. The field reaches the page, and a
    measured zero renders **identically** to never having been recorded. This is
    the rule failing.

``REVERSE``
    ``A != B`` and ``A == C1``/``C2``/``C3``. Absence renders identically to a
    *recorded value* -- which in practice has meant a silent fallback to some
    other field, so the page prints a number that did not come from where the
    reader thinks it came from. Confirm one with ``probe``: remove the field,
    then change the *suspected* fallback source, and watch the page follow it.

``TRIVIAL``
    All five renders identical: the field never reaches the page at all.

``ZERO-AND-ABSENCE-BOTH-INVISIBLE``
    ``A == B`` and absence also matches. The field reaches the page only through
    something derived from it.

THE MASKING TRAP -- read this before changing anything here
-----------------------------------------------------------
The rendered page prints an **evidence hash: a sha256 over the entire evidence
file**. This sweep rewrites that file once per render. So without masking, every
one of the 2,391 renders differs from every other, every comparison says "not
identical", and the sweep reports **zero findings** -- which looks exactly like a
clean report. The first sweep run on this project did precisely that and came
back empty.

:mod:`masking` does the masking and its docstring carries the rest of the
argument. Note what this module does *not* do: it does not mask timestamps and it
does not mask paths, because ``created`` is a timestamp and ``artifacts`` are
paths, and masking those would make the sweep call those leaves unrendered.
Instead ``now`` is pinned to :data:`PINNED_NOW` and the fixture root is fixed for
the whole sweep, so neither varies in the first place.

Running it
----------
::

    python scripts/audit/differential_render.py sweep
    python scripts/audit/differential_render.py sweep --fixture field/comparison
    python scripts/audit/differential_render.py quote single/comparison judges[0].alpha
    python scripts/audit/differential_render.py probe single/comparison \\
        --grep "Mann-Whitney" --del judges[0].alpha --set thresholds.alpha=0.09

The full sweep is 176 leaf paths x 5 variants x 5 fixtures = 2,391 renders and
takes a few minutes. ``--json`` writes the raw result so ``quote`` and your own
analysis do not have to re-run it.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from model_migration_kit.contracts import EVENT_COMPARISON, EVENT_VERDICT
from model_migration_kit.report import ReportModel, render_html_string

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:  # importable as a module, runnable as a script
    sys.path.insert(0, str(_HERE))

from masking import mask_page  # noqa: E402
from page_text import html_to_text  # noqa: E402

import fixtures  # noqa: E402

#: Pinned so the ``generated`` line is constant across a sweep, which is what
#: makes masking timestamps unnecessary -- and masking timestamps would blind the
#: sweep to ``created``.
PINNED_NOW = "2026-08-24T00:00:00+00:00"

RENDER_ERROR = "!!RENDER-ERROR!!"

#: Paths whose "plausible recorded value" has to be a well-formed structure
#: rather than the generic sentinel, or the render simply fails and the leaf is
#: reported as an error instead of being tested.
OVERRIDE_A = {
    "unstable": [
        {
            "item_id": "item-09",
            "judges": ["accuracy"],
            "changes": [
                {
                    "item_id": "item-09",
                    "judge": "accuracy",
                    "baseline_passes": 3,
                    "baseline_n": 5,
                    "candidate_passes": 2,
                    "candidate_n": 5,
                    "baseline_state": "unstable",
                    "candidate_state": "unstable",
                    "label": "3/5 -> 2/5",
                }
            ],
        }
    ],
    "warnings": ["a warning the comparison recorded"],
}


# --------------------------------------------------------------------------- #
# walking the payload
# --------------------------------------------------------------------------- #
def leaves(node, path=()):
    """Yield ``(path, value)`` for every scalar leaf, plus every empty container.

    An empty container is a leaf here on purpose: ``"warnings": []`` is a state
    the page has to render, and a walker that only yielded scalars would never
    generate a variant for it.
    """
    if isinstance(node, dict):
        if not node:
            yield path, node
            return
        for key, value in node.items():
            yield from leaves(value, (*path, key))
    elif isinstance(node, list):
        if not node:
            yield path, node
            return
        for index, value in enumerate(node):
            yield from leaves(value, (*path, index))
    else:
        yield path, node


def get(root, path):
    """The value at ``path``. Raises if it is not there."""
    cursor = root
    for step in path:
        cursor = cursor[step]
    return cursor


def _parent(root, path):
    """The container holding ``path[-1]``, or ``None`` if the path is not present.

    Tolerant by design. The candidate-field fixture holds ten comparison
    payloads of slightly different shapes -- one has no ``pass_rate_floor`` at
    all -- and the same path is applied to every one of them. A path that does
    not exist in one of them is a no-op there, not a crash.
    """
    cursor = root
    for step in path[:-1]:
        try:
            cursor = cursor[step]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(cursor, list):
        if not isinstance(path[-1], int) or path[-1] >= len(cursor):
            return None
    elif isinstance(cursor, dict):
        if path[-1] not in cursor:
            return None
    else:
        return None
    return cursor


def set_at(root, path, value) -> None:
    """Set ``path`` to ``value`` if the path exists; otherwise do nothing."""
    cursor = _parent(root, path)
    if cursor is not None:
        cursor[path[-1]] = value


def del_at(root, path) -> None:
    """Remove ``path`` if it exists; otherwise do nothing."""
    cursor = _parent(root, path)
    if cursor is None:
        return
    if isinstance(cursor, list):
        del cursor[path[-1]]
    else:
        cursor.pop(path[-1], None)


def format_path(path) -> str:
    """``("judges", 0, "alpha")`` -> ``judges[0].alpha``."""
    out = ""
    for step in path:
        out += f"[{step}]" if isinstance(step, int) else ("." + step if out else step)
    return out


def parse_path(dotted: str) -> list:
    """``judges[0].alpha`` -> ``["judges", 0, "alpha"]``, the inverse of the above."""
    out: list = []
    for part in dotted.replace("[", ".").replace("]", "").split("."):
        if part:
            out.append(int(part) if part.isdigit() else part)
    return out


# --------------------------------------------------------------------------- #
# the five variants
# --------------------------------------------------------------------------- #
def variant_a(base):
    """A plausible non-zero **recorded** value of the same type as ``base``.

    ``base`` is kept where it already qualifies, so the A render stays as close
    to the untouched fixture as possible and the diff against B is minimal.
    """
    if isinstance(base, bool):
        return True
    if isinstance(base, int) and base not in (0, 1):
        return base
    if isinstance(base, int):
        return 7
    if isinstance(base, float):
        return base if base != 0.0 else 0.4321
    if isinstance(base, str):
        return base or "SENTINELVALUE"
    if isinstance(base, list):
        return base or ["SENTINELVALUE"]
    if isinstance(base, dict):
        return base or {"baseline": 3, "candidate": 4}
    # ``base`` is None, so the recorded type is unknown. A float is the commonest
    # thing in this payload and the likeliest to be right.
    return 0.4321


def variant_b(base, recorded):
    """A genuine **measured zero** -- present, recorded, and equal to nothing.

    Typed off ``base`` where the fixture recorded one, and off the A value where
    the fixture recorded ``null`` and there is no other evidence of the type.
    """
    reference = base if base is not None else recorded
    if isinstance(reference, bool):
        return False
    if isinstance(reference, int):
        return 0
    if isinstance(reference, float):
        return 0.0
    if isinstance(reference, str):
        return ""
    if isinstance(reference, list):
        return []
    if isinstance(reference, dict):
        return {}
    return 0


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
class Fixture:
    """One evidence log, rendered repeatedly with one payload field mutated.

    ``scope`` decides how many records a mutation touches:

    ``"all"``
        every record of ``target_kind``. Right for the single-run log, and right
        for a question about the document as a whole.
    ``"newest"``
        only the last one in the log. On the ten-comparison log this keeps the
        rendered diff **local** -- mutating all ten collapses the candidate table
        into uniformity and the diff stops pointing anywhere useful.
    """

    def __init__(self, name, evidence_path, records, target_kind, scope="all"):
        self.name = name
        self.evidence = Path(evidence_path)
        self.records = records
        self.target_kind = target_kind
        self.scope = scope

    def _targets(self, records):
        indexes = [i for i, r in enumerate(records) if r["event_type"] == self.target_kind]
        return indexes[-1:] if self.scope == "newest" else indexes

    def base_payload(self):
        """A deep copy of the first payload of the kind under test."""
        for one in self.records:
            if one["event_type"] == self.target_kind:
                return copy.deepcopy(one["payload"])
        raise KeyError(self.target_kind)

    def render(self, mutate) -> str:
        """Apply ``mutate`` to every targeted payload and return the masked page text.

        A render that raises is not a collision, but it *is* a fact about the
        field, so it comes back as a ``!!RENDER-ERROR!!`` string rather than
        propagating and ending the sweep 40 leaves in.
        """
        records = copy.deepcopy(self.records)
        for index in self._targets(records):
            mutate(records[index]["payload"])
        self.evidence.write_bytes(
            b"".join(
                (json.dumps(dict(one), sort_keys=True) + "\n").encode("utf-8")
                for one in records
            )
        )
        try:
            model = ReportModel.from_evidence(self.evidence)
            text = html_to_text(render_html_string(model, now=PINNED_NOW))
        except Exception as exc:  # noqa: BLE001 -- a crash is data, not a failure
            return f"{RENDER_ERROR} {type(exc).__name__}: {str(exc)[:300]}"
        # Without this line the sweep finds nothing. See the module docstring.
        return mask_page(text, evidence_path=self.evidence)


def build_fixture(name: str, work: Path) -> Fixture:
    """Build one of the named fixtures under ``work``.

    Names are ``<log>/<record kind>``: ``single/comparison``,
    ``single/verdict``, ``field/comparison``, ``field/verdict``,
    ``field-newest/comparison``.
    """
    log, _, kind_name = name.partition("/")
    kind = EVENT_VERDICT if kind_name == "verdict" else EVENT_COMPARISON
    work.mkdir(parents=True, exist_ok=True)

    if log == "single":
        scenario = fixtures.standard_scenario(work / f"single_{kind_name}")
        evidence = scenario.evidence
    else:
        source = work / "field" / "evidence.jsonl"
        if not source.exists():
            source = fixtures.candidate_field_log(work / "field")
        # A live copy per fixture, so two fixtures over the same log do not
        # overwrite each other's mutated evidence.
        evidence = work / "field" / f"live_{log}_{kind_name}.jsonl"
        evidence.write_bytes(source.read_bytes())
        records = _read(source)
        return Fixture(name, evidence, records, kind,
                       scope="newest" if log == "field-newest" else "all")

    return Fixture(name, evidence, _read(evidence), kind)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


FIXTURE_NAMES = (
    "single/comparison",
    "single/verdict",
    "field/comparison",
    "field/verdict",
    "field-newest/comparison",
)


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
def sweep(fixture: Fixture, progress=True) -> list[dict]:
    """Render all five variants for every leaf of the fixture's payload."""
    base = fixture.base_payload()
    results = []
    for path, _ in leaves(base):
        recorded = get(base, path)
        dotted = format_path(path)
        value_a = OVERRIDE_A.get(dotted, variant_a(recorded))
        value_b = variant_b(recorded, value_a)

        def setter(value, path=path):
            return lambda payload: set_at(payload, list(path), copy.deepcopy(value))

        texts = {
            "A": fixture.render(setter(value_a)),
            "B": fixture.render(setter(value_b)),
            "C1": fixture.render(lambda payload, path=path: del_at(payload, list(path))),
            "C2": fixture.render(setter(None)),
        }
        # C3 only makes sense where the parent is a dict held under a string key:
        # otherwise "remove the parent" means removing a list element or the root.
        if len(path) >= 2 and isinstance(path[-2], str):
            texts["C3"] = fixture.render(
                lambda payload, path=path: del_at(payload, list(path[:-1]))
            )
        else:
            texts["C3"] = None

        results.append(
            {
                "path": dotted,
                "raw_path": list(path),
                "base": recorded,
                "A": value_a,
                "B": value_b,
                "hashes": {
                    key: None if text is None else hashlib.sha256(text.encode()).hexdigest()[:16]
                    for key, text in texts.items()
                },
                "diffs": {
                    "A|" + key: None
                    if texts[key] is None
                    else "\n".join(
                        difflib.unified_diff(
                            texts["A"].splitlines(), texts[key].splitlines(),
                            "A", key, lineterm="", n=2,
                        )
                    )
                    for key in ("B", "C1", "C2", "C3")
                },
            }
        )
        if progress:
            print(".", end="", flush=True)
    if progress:
        print()
    return results


def classify(results):
    """Bucket sweep results. See the module docstring for what each verdict means."""
    collisions, reverse, trivial, both_invisible, errors, clean = [], [], [], [], [], []
    for result in results:
        digests = result["hashes"]
        present = [value for value in digests.values() if value is not None]
        if any(
            (result["diffs"].get("A|" + key) or "").find(RENDER_ERROR) >= 0
            for key in ("B", "C1", "C2", "C3")
        ):
            errors.append(result)
        if len(set(present)) == 1:
            trivial.append(result)
            continue
        reaches_page = digests["A"] != digests["B"]
        like_b = [
            key for key in ("C1", "C2", "C3")
            if digests[key] is not None and digests[key] == digests["B"]
        ]
        like_a = [
            key for key in ("C1", "C2", "C3")
            if digests[key] is not None and digests[key] == digests["A"]
        ]
        if reaches_page and like_b:
            collisions.append((result, like_b))
        elif reaches_page and like_a:
            reverse.append((result, like_a))
        elif not reaches_page and like_b:
            both_invisible.append((result, like_b))
        else:
            clean.append(result)
    return collisions, reverse, trivial, both_invisible, errors, clean


# --------------------------------------------------------------------------- #
# follow-up tools
# --------------------------------------------------------------------------- #
def quote(fixture: Fixture, dotted: str, context: int = 6) -> None:
    """Print the page region around the change, for A and B side by side.

    A sweep verdict is a hash comparison; a *finding* needs the sentence. This
    renders the five variants for one path and prints every changed region with
    surrounding lines, then names which absence variants are byte-identical to
    the measured zero. That last line is the finding, in one line, quotable.
    """
    path = parse_path(dotted)
    recorded = get(fixture.base_payload(), path)
    value_a = OVERRIDE_A.get(dotted, variant_a(recorded))
    value_b = variant_b(recorded, value_a)

    def setter(value):
        return lambda payload: set_at(payload, list(path), copy.deepcopy(value))

    texts = {
        "A": fixture.render(setter(value_a)),
        "B": fixture.render(setter(value_b)),
        "C1": fixture.render(lambda payload: del_at(payload, list(path))),
        "C2": fixture.render(setter(None)),
    }
    if len(path) >= 2 and isinstance(path[-2], str):
        texts["C3"] = fixture.render(lambda payload: del_at(payload, list(path[:-1])))

    same_as_zero = [key for key in ("C1", "C2", "C3") if texts.get(key) == texts["B"]]
    print(f"# {fixture.name}  {dotted}   base={recorded!r} A={value_a!r} B={value_b!r}")
    print("# absence variants byte-identical to the measured zero:", same_as_zero or "none")

    lines_a = texts["A"].splitlines()
    lines_b = texts["B"].splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lines_a, lines_b).get_opcodes():
        if tag == "equal":
            continue
        for label, lines, start, end in (
            ("A", lines_a, i1, i2),
            ("B", lines_b, j1, j2),
        ):
            lo, hi = max(0, start - context), end + context
            print(f"--- variant {label} (page lines {lo}..{hi}) ---")
            for line in lines[lo:hi]:
                if line.strip():
                    print("   ", line)
        for key in same_as_zero:
            print(f"--- variant {key}: BYTE-IDENTICAL to variant B ---")
        print()


def probe(fixture: Fixture, cases, needle: str) -> None:
    """Render a named set of hand-built payload mutations and grep one line out of each.

    The tool for confirming a ``REVERSE`` verdict, which is always a claim about
    *where the printed number came from*. Remove the field; the sentence still
    prints a number. Now change the field you suspect it fell back to, and see
    whether the sentence follows. If it does, the page is attributing one field's
    value to another, and the reader has no way to know.

    ``cases`` maps a label to a callable taking the payload dict.
    """
    width = max(len(label) for label in cases)
    for label, mutate in cases.items():
        text = fixture.render(mutate)
        hits = [line.strip() for line in text.splitlines() if needle in line]
        print(f"{label:{width}s} : {hits}")


def _mutation_cases(sets, deletes):
    """Turn ``--set``/``--del`` command-line arguments into ``probe`` cases."""
    cases = {"(untouched)": lambda payload: None}
    for dotted in deletes or ():
        path = parse_path(dotted)
        cases[f"del {dotted}"] = lambda payload, path=path: del_at(payload, path)
    for assignment in sets or ():
        dotted, _, raw = assignment.partition("=")
        path = parse_path(dotted)
        value = json.loads(raw)
        cases[f"set {dotted}={raw}"] = (
            lambda payload, path=path, value=value: set_at(payload, path, copy.deepcopy(value))
        )
    if deletes and sets:
        def combined(payload):
            for dotted in deletes:
                del_at(payload, parse_path(dotted))
            for assignment in sets:
                dotted, _, raw = assignment.partition("=")
                set_at(payload, parse_path(dotted), json.loads(raw))

        cases["all of the above"] = combined
    return cases


# --------------------------------------------------------------------------- #
# command line
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--work",
        default=None,
        help="directory for the fixtures (default: a fresh temporary directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("sweep", help="render every variant of every leaf")
    run.add_argument("--fixture", action="append", choices=FIXTURE_NAMES,
                     help="restrict to one fixture (repeatable); default is all five")
    run.add_argument("--json", default=None, help="write the raw results here")

    show = sub.add_parser("quote", help="print the page region for one leaf path")
    show.add_argument("fixture", choices=FIXTURE_NAMES)
    show.add_argument("path", help="e.g. judges[0].alpha")
    show.add_argument("--context", type=int, default=6)

    check = sub.add_parser("probe", help="render hand-built mutations and grep the page")
    check.add_argument("fixture", choices=FIXTURE_NAMES)
    check.add_argument("--grep", required=True, help="substring identifying the line to quote")
    check.add_argument("--set", action="append", dest="sets", metavar="PATH=JSON")
    check.add_argument("--del", action="append", dest="deletes", metavar="PATH")

    args = parser.parse_args(argv)
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="migkit-audit-"))
    print(f"[audit] fixtures under {work}")

    if args.command == "sweep":
        out = {}
        for name in args.fixture or FIXTURE_NAMES:
            print("===", name)
            results = sweep(build_fixture(name, work))
            out[name] = results
            collisions, reverse, trivial, invisible, errors, clean = classify(results)
            print(
                f"{name}: {len(results)} paths | collisions {len(collisions)} | "
                f"reverse {len(reverse)} | trivial-unrendered {len(trivial)} | "
                f"zero-and-absence-both-invisible {len(invisible)} | "
                f"render-errors {len(errors)} | clean {len(clean)}"
            )
            for result, keys in collisions:
                print("   COLLISION", result["path"], "B==" + ",".join(keys))
            for result, keys in reverse:
                print("   REVERSE  ", result["path"], "A==" + ",".join(keys))
        if args.json:
            Path(args.json).write_text(json.dumps(out, default=str))
            print("wrote", args.json)
        return 0

    fixture = build_fixture(args.fixture, work)
    if args.command == "quote":
        quote(fixture, args.path, args.context)
    else:
        probe(fixture, _mutation_cases(args.sets, args.deletes), args.grep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
