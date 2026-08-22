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
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Sibling checkouts a contract may legitimately cite. ``opik_rigor`` is the
#: dependency this package is built on, and half the interesting citations point
#: into it -- so a bare ``judge.py`` must be resolved against both trees before
#: it can be called missing.
#: ``REPO.parent`` is included so a citation may be written the way a human
#: would read it -- ``opik-rigor/src/opik_rigor/judge.py`` -- rather than
#: relative to a root the reader has to infer.
SIBLINGS = (REPO, REPO.parent / "opik-rigor", REPO.parent)

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


def resolve(name: str) -> Path | None:
    """Where a citation's file actually lives, or ``None``."""
    candidate = Path(name)
    for root in SIBLINGS:
        if not root.exists():
            continue
        direct = root / candidate
        if direct.is_file():
            return direct
        matches = [
            p for p in root.rglob(candidate.name)
            if not _is_a_copy(p)
        ]
        # A bare filename is only resolved if it is unambiguous in that tree.
        # ``.claude/worktrees`` lives *inside* this repo and holds whole copies
        # of it, so without that filter every real file looks ambiguous and the
        # checker reports the entire contract as broken -- which is how this
        # line was found.
        if len(matches) == 1 and candidate.parent in (Path("."), Path("")):
            return matches[0]
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
                bad_files.append(f"  line {number}: {name}:{first} -- no such file in either tree")
                continue
            # A bare filename that only resolves in the *sibling* tree is the
            # judge.py error: it reads as a file in this package, an agent looks
            # for it here, and it is in a different distribution. Resolvable is
            # not the same as unambiguous, so it must be written out in full.
            if Path(name).parent in (Path("."), Path("")) and REPO not in target.parents:
                bad_files.append(
                    f"  line {number}: {name}:{first} -- resolves only in "
                    f"{target.relative_to(target.parents[len(target.parents) - 4])}"
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
