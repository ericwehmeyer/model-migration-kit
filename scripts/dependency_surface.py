"""Derive COMPATIBILITY.md's dependency-surface table from the tree, and check it.

Invariant 1 says this project uses opik-rigor's public surface and nothing else.
COMPATIBILITY.md carries the table that makes that auditable -- every module here,
every rigor name it imports -- and for as long as the table was maintained by hand
it was wrong. Three times, all in the same direction:

* PROGRESS.md recorded one violation site; a mechanical audit found three and said
  so loudly, because a gap recorded inaccurately reads as handled.
* When the fix was applied there were **six**. `comparison.py` had been reaching
  into `opik_rigor.judge` in shipped code the whole time and appeared on no list.
* This table was simultaneously missing four rows outright, three of them test
  files written the same night.

The direction is not an accident. A hand-maintained inventory decays toward
understatement every time somebody writes a file, because the pattern a new file
copies is the pattern the tree already contains. So the tree is the record now.

    python scripts/dependency_surface.py            # print the table
    python scripts/dependency_surface.py --check    # exit 1 if COMPATIBILITY.md disagrees

Deliberately AST-based rather than a grep: `from opik_rigor.judge import X` and
`from opik_rigor import X` are the same dependency for this purpose and must land
in one row, and a grep for `opik_rigor` also matches the prose either side of the
table it is checking.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE_HEADER = "| Module | Imported from `opik_rigor` |"


def rigor_imports(path: Path) -> tuple[list[str], bool]:
    """The rigor names this file imports, and whether it imports the module itself.

    A submodule import (`from opik_rigor.judge import SCORE_MIN`) is folded into
    the same row as a root import, because the question this table answers is
    "what does this file depend on rigor for", not "which spelling did it use".
    The spelling is invariant 1's business and `grep -rn "from opik_rigor\\."`
    answers that in one line.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return [], False

    names: set[str] = set()
    imports_module = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "opik_rigor":
                names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imports_module |= any(alias.name == "opik_rigor" for alias in node.names)
    return sorted(names), imports_module


#: Every directory in the tree that may import rigor. `scripts/` is here because
#: it was not, and the omission had already cost a row: `scripts/showcase.py` does
#: `from opik_rigor import FakeAdapter` and was invisible to this gate, so
#: COMPATIBILITY.md was incomplete by exactly one module while `--check` passed.
#: The claim that table is made under -- "so the completeness claim is checkable
#: rather than asserted" -- is not survivable with a glob that only looks where the
#: last violation happened to be. A fourth directory added later must be added
#: here; there is no way to make that automatic without walking the repo root and
#: sweeping up the venv, the build tree and every worktree checkout beside it.
SEARCHED = ("src", "tests", "scripts")


def table_rows() -> list[str]:
    sources = sorted(
        [path for name in SEARCHED for path in (REPO / name).rglob("*.py")],
        key=lambda p: p.relative_to(REPO).as_posix(),
    )
    rows = []
    for path in sources:
        names, imports_module = rigor_imports(path)
        if not names and not imports_module:
            continue
        cells = ", ".join(f"`{name}`" for name in names)
        if imports_module:
            suffix = "the module itself (`import opik_rigor`), read for `__version__`"
            cells = f"{cells} -- **plus** {suffix}" if cells else f"**{suffix}**"
        rows.append(f"| `{path.relative_to(REPO).as_posix()}` | {cells} |")
    return rows


def recorded_rows(doc: str) -> list[str]:
    """The rows currently in COMPATIBILITY.md, between the header and the blank line."""
    lines = doc.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == TABLE_HEADER)
    except StopIteration:
        return []
    out = []
    for line in lines[start + 2 :]:  # skip header and the |---|---| separator
        if not line.startswith("|"):
            break
        out.append(line.rstrip())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare against COMPATIBILITY.md")
    args = parser.parse_args()

    derived = table_rows()
    if not args.check:
        print(TABLE_HEADER)
        print("|---|---|")
        print("\n".join(derived))
        return 0

    doc = (REPO / "COMPATIBILITY.md").read_text(encoding="utf-8")
    recorded = recorded_rows(doc)
    if recorded == derived:
        print(f"dependency-surface table agrees with the tree ({len(derived)} modules)")
        return 0

    print("COMPATIBILITY.md's dependency-surface table disagrees with the tree.\n")
    for line in sorted(set(derived) - set(recorded)):
        print(f"  MISSING FROM THE DOC  {line}")
    for line in sorted(set(recorded) - set(derived)):
        print(f"  STALE IN THE DOC      {line}")
    print("\nRegenerate with: python scripts/dependency_surface.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
