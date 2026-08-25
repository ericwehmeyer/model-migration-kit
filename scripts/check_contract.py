"""Check a plan's citations before an agent is dispatched against them.

A chunk contract in this project is not prose; it is a set of instructions two
agents follow without seeing each other, and every `file.py:NN` in it is load
bearing. Five errors of this kind shipped into contracts in one session:

- ``judge.py:318-328`` -- there is no ``judge.py`` in this package. The verdict
  is emitted in ``opik_rigor/judge.py``, a different distribution. That citation
  sat in the sentence telling the implementer this was the single most likely way
  to get the chunk wrong, and both agents copied it into their own docstrings.
- ``Item`` -- the class is ``GoldenItem``. Named in a signature.
- ``cli.py:521`` -- the loop is at 522, and a second call site the contract never
  mentions is in ``demo.py``.
- ``judging.py:653-659`` -- it is 652-658.

Each cost an agent real time, and each was found downstream by someone reading
the source. None needed judgement to catch. This finds them in a second.

    python scripts/check_contract.py docs/superpowers/plans/<plan>.md
    python scripts/check_contract.py <plan> --from 2797 --to 2900

Line ranges matter because a plan grows and only the section about to be
dispatched needs to be clean.

**Symbols are advisory and file citations are not.** A missing file or an
out-of-range line is a fact. A backticked identifier that resolves nowhere may
be a type from the standard library, a name from a dependency, or something the
chunk is about to create -- so those are reported as unverified rather than
wrong, and the exit code ignores them.

**A citation is a promise about one tree, and only that tree may answer it.**
This machine carries eighty-odd checkouts of this project side by side, every
one of them holding a ``src/model_migration_kit/report.py`` at a different
length and a different state of done. Resolving a citation against whichever of
them happens to answer first does not check the contract -- it checks a
stranger, and reports the result as if it were about the tree the agent is
about to open. So a path that lands outside the trees below is not a passing
citation; it is an unresolvable one, and it is reported as such however it was
spelled.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: The trees a citation may land in. ``opik_rigor`` is the dependency this
#: package is built on, and half the interesting citations point into it -- so a
#: bare ``judge.py`` must be resolved against both trees before it can be called
#: missing. Nothing else may answer: a neighbouring checkout of *this* project
#: is the most dangerous file a citation can find, because it has the right
#: name, the right path, and the wrong contents.
CITABLE_TREES = (REPO, REPO.parent / "opik-rigor")

#: Kept under its old name because ``index_symbols`` reads it, and narrowed to
#: the trees above. It used to end in ``REPO.parent``, which made every sibling
#: directory a citation surface -- eighty-one of them here. The one thing that entry
#: bought is preserved by name in ``_written_relative_to``: a citation may still
#: be written the way a human reads it, ``opik-rigor/src/opik_rigor/judge.py``,
#: rooted at the project's own directory rather than at a root the reader has to
#: infer.
SIBLINGS = CITABLE_TREES

#: Directories that hold copies of a tree rather than the tree. Matched by
#: shape because a virtualenv is not always called ``.venv`` -- the sibling
#: repo has a ``.venv-opik`` holding a vendored ``judge.py``, which was
#: enough to make the real one look ambiguous and go unreported.
IGNORED_EXACT = frozenset({".claude", ".git", "node_modules", "__pycache__", "site-packages"})


def _is_a_copy(path: Path) -> bool:
    return any(
        part in IGNORED_EXACT or part.startswith(".venv")
        for part in path.parts
    )

#: ``path/to/file.py:12`` or ``file.py:12-34``, with or without backticks.
CITATION = re.compile(r"`?([\w./\\-]+\.py):(\d+)(?:-(\d+))?`?")

#: A backticked identifier that looks like a type or a callable rather than
#: prose: ``SomeClass``, ``some_function``, ``Thing.attr``.
SYMBOL = re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)`")

#: Names that resolve outside this repo and are not worth an index: typing,
#: builtins, and the vocabulary a contract uses to describe shapes.
NOT_OURS = frozenset({
    "Any", "Callable", "Exception", "False", "Iterable", "Iterator", "KeyError", "Mapping",
    "NamedTuple", "None", "OSError", "Optional", "Path", "Sequence", "True", "TypeError",
    "Union", "ValueError", "abstractmethod", "args", "ast", "bool", "bytes", "classmethod",
    "cls", "dataclass", "dataclasses", "default_factory", "dict", "field", "float",
    "frozenset", "git", "int", "json", "kwargs", "list", "os", "property", "pytest", "re",
    "ruff", "self", "set", "staticmethod", "str", "sys", "tuple",
})


def _governed(path: Path) -> Path | None:
    """``path``, normalised, if it lies in a tree a contract here may cite.

    Normalised first, because containment cannot be decided on the spelling. A
    citation may reach out of the tree with ``..``, arrive as an absolute path,
    or -- the way it actually happened -- lose its drive letter to the citation
    regex, which excludes ``:``, and be re-anchored onto this drive as
    ``\\Users\\...\\some-other-checkout\\...``. All three name a real file and
    none of them names ours.
    """
    try:
        found = path.resolve()
    except OSError:  # pragma: no cover - a path the OS will not even normalise
        return None
    if _is_a_copy(found):
        # A copy of the tree is not the tree. ``.claude/worktrees`` and a
        # vendored ``.venv`` both sit *inside* a citable root and both hold
        # whole duplicates of it, right down to the file names.
        return None
    return found if any(found == t or t in found.parents for t in CITABLE_TREES) else None


def _written_relative_to(candidate: Path) -> Iterator[Path]:
    """Every place a citation is allowed to be spelled from.

    Each citable tree, and -- only when the citation leads with that tree's own
    directory name -- the tree's parent. The second is the human spelling,
    ``opik-rigor/src/opik_rigor/judge.py``, and it is a key rather than a
    doorway: the parent is never searched for anything but the tree named in the
    citation itself, so ``mk-some-other-checkout/src/...`` finds nothing there.
    """
    for tree in CITABLE_TREES:
        if not tree.exists():
            continue
        yield tree / candidate
        if candidate.parts[:1] == (tree.name,):
            yield tree.parent / candidate


def resolve(name: str) -> Path | None:
    """Where a citation's file actually lives *in a tree we govern*, or ``None``."""
    candidate = Path(name)
    for direct in _written_relative_to(candidate):
        if direct.is_file() and (found := _governed(direct)) is not None:
            return found
    # A bare filename is only resolved if it is unambiguous in that tree.
    # ``.claude/worktrees`` lives *inside* this repo and holds whole copies of
    # it, so without that filter every real file looks ambiguous and the checker
    # reports the entire contract as broken -- which is how this line was found.
    if candidate.parent not in (Path("."), Path("")):
        return None
    for tree in CITABLE_TREES:
        if not tree.exists():
            continue
        matches = [p for p in tree.rglob(candidate.name) if not _is_a_copy(p)]
        if len(matches) == 1 and (found := _governed(matches[0])) is not None:
            return found
    return None


def _relative(target: Path) -> Path:
    """``target`` spelled the way the citation ought to have been written.

    Rooted at the parent of the citable trees, so a sibling project reads as
    ``opik-rigor/src/opik_rigor/judge.py`` -- which is exactly the spelling the
    message is asking for. The old form counted backwards through ``parents``
    with a fixed index and produced a suffix whose meaning depended on how deep
    the file happened to sit.
    """
    try:
        return target.relative_to(REPO.parent)
    except ValueError:
        return target


def elsewhere(name: str) -> Path | None:
    """A real file this citation names that no contract here may cite.

    Only ever used to say *why* something did not resolve. "No such file" and
    "that file is in somebody else's checkout" are different facts, and the
    second is the one a reader needs in order to fix the citation.
    """
    candidate = Path(name)
    for root in (REPO, REPO.parent):
        probe = root / candidate
        if probe.is_file() and _governed(probe) is None:
            try:
                return probe.resolve()
            except OSError:  # pragma: no cover - see _governed
                return None
    return None


def index_symbols() -> set[str]:
    """Every name defined at module level anywhere in the trees we can see."""
    names: set[str] = set()
    for root in SIBLINGS:
        if not root.exists():
            continue
        for path in list((root / "src").rglob("*.py")) + list((root / "tests").rglob("*.py")):
            if _is_a_copy(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            names.add(path.stem)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    )
                elif isinstance(node, ast.arg):
                    names.add(node.arg)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--from", dest="start", type=int, default=1)
    parser.add_argument("--to", dest="end", type=int, default=None)
    args = parser.parse_args()

    lines = args.plan.read_text(encoding="utf-8").splitlines()
    end = args.end or len(lines)
    section = list(enumerate(lines[args.start - 1 : end], start=args.start))

    bad_files: list[str] = []
    bad_lines: list[str] = []
    for number, line in section:
        for name, first, last in CITATION.findall(line):
            target = resolve(name)
            if target is None:
                stranger = elsewhere(name)
                because = (
                    f"no such file in either tree; {stranger} is not in one of them"
                    if stranger is not None
                    else "no such file in either tree"
                )
                bad_files.append(f"  line {number}: {name}:{first} -- {because}")
                continue
            # A bare filename that only resolves in the *sibling* tree is the
            # judge.py error: it reads as a file in this package, an agent looks
            # for it here, and it is in a different distribution. Resolvable is
            # not the same as unambiguous, so it must be written out in full.
            # This is the *ambiguity* rule, not the containment one -- containment
            # is decided in ``resolve`` for every spelling, which is where it
            # belongs. It used to live here, conjoined to "the citation has no
            # directory component", so a citation that carried one was never
            # checked for containment at all.
            if Path(name).parent in (Path("."), Path("")) and REPO not in target.parents:
                bad_files.append(
                    f"  line {number}: {name}:{first} -- resolves only in "
                    f"{_relative(target)}"
                    f"; write the path out so it is not read as this package"
                )
                continue
            length = len(target.read_text(encoding="utf-8").splitlines())
            for cited in (int(first), int(last) if last else int(first)):
                if cited > length:
                    bad_lines.append(
                        f"  line {number}: {name}:{cited} -- file has {length} lines"
                    )

    known = index_symbols()
    unverified: dict[str, int] = {}
    for number, line in section:
        for symbol in SYMBOL.findall(line):
            head = symbol.split(".")[0]
            if head in NOT_OURS or head in known or "." in symbol and symbol.split(".")[1] in known:
                continue
            if head.islower() and "_" not in head:
                continue  # ordinary prose in backticks
            unverified.setdefault(symbol, number)

    print(f"Checked lines {args.start}-{end} of {args.plan.name}\n")
    if bad_files:
        print(f"[FAIL] {len(bad_files)} citation(s) name a file that does not exist")
        print("\n".join(bad_files))
    else:
        print("[PASS] every cited file exists")
    if bad_lines:
        print(f"[FAIL] {len(bad_lines)} citation(s) point past the end of the file")
        print("\n".join(bad_lines))
    else:
        print("[PASS] every cited line is in range")

    if unverified:
        print(f"\n[note] {len(unverified)} symbol(s) resolve nowhere in src/ or tests/.")
        print("       Advisory only -- a name the chunk is about to create belongs here.")
        for symbol, number in sorted(unverified.items(), key=lambda kv: kv[1])[:25]:
            print(f"  line {number}: {symbol}")

    failed = bool(bad_files or bad_lines)
    print()
    print("Contract citations are wrong." if failed else "Contract citations check out.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
