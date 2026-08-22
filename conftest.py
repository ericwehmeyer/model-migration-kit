"""Make a bare `pytest` test the checkout it was launched from.

The problem this solves has cost this project more than any other single thing.
`pip install -e .` writes `_editable_impl_model_migration_kit.pth` into the venv,
and that file names one absolute path: the main checkout's `src`. Every git
worktree shares that venv, so inside a worktree `import model_migration_kit`
resolves to the *main checkout*. A module added in a worktree is invisible; a
module edited in a worktree is silently the wrong copy. One agent reported a
false green from exactly this, and the workaround -- a hand-spelled `PYTHONPATH`
prefix on every command -- had spread into every agent brief.

`tests/test_import_purity.py` already solved it for its own child processes. This
file generalises that fix to the whole suite.

Two paths need redirecting, and they are not the same mechanism:

* `sys.path` covers everything that runs inside pytest's own interpreter.
* `PYTHONPATH` covers child processes, which `sys.path` does not reach. Several
  tests spawn the `migkit` console script or a fresh interpreter; without this
  they would resolve the package through the editable install and assert things
  about the main checkout while claiming to test this one.

Setting `PYTHONPATH` for children is safe with respect to the release gate, and
it is worth being specific rather than trusting the claim. `scripts/verify_release.py`
is never run as a subprocess by anything under `tests/` -- the two test modules
that touch it import it in-process -- so this variable is simply not in scope for
it. And if some future test did shell out to it, the check that matters would
still hold: its demo-data probe pins the extracted wheel at `sys.path[0]`, runs
the child with `-S -E` so `PYTHONPATH` is discarded before the child starts, and
then asserts `model_migration_kit.__path__` contains the wheel and nothing else.
Three independent reasons. No environment variable can make that gate agree with
a wheel that ships nothing.

Everything below is derived from `__file__`, never from the cwd: the whole point
is to identify the checkout *this file* belongs to, and the cwd is whatever
directory the person happened to type `pytest` in.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_PACKAGE = "model_migration_kit"
_SRC = Path(__file__).resolve().parent / "src"


def _current_package_src() -> Path | None:
    """The `src` directory `_PACKAGE` resolves to *before* this file changes anything.

    Returns `None` when the package cannot be located at all, which is a fresh
    checkout with no install -- also a case where redirecting is the right move.
    """
    if _PACKAGE in sys.modules:  # already imported; ask the module, not the finder
        module_file = getattr(sys.modules[_PACKAGE], "__file__", None)
        return Path(module_file).resolve().parent.parent if module_file else None
    try:
        spec = importlib.util.find_spec(_PACKAGE)
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return None
    if spec is None:
        return None
    if spec.origin:
        return Path(spec.origin).resolve().parent.parent
    # A namespace package has no origin but does carry search locations.
    locations = list(spec.submodule_search_locations or [])
    return Path(locations[0]).resolve().parent if locations else None


def _prepend(value: str, existing: str | None) -> str:
    """`value` first, then whatever was already there, with no duplicate entry."""
    rest = [part for part in (existing or "").split(os.pathsep) if part and part != value]
    return os.pathsep.join([value, *rest])


# A no-op in the main checkout, deliberately. There `_SRC` is already the path the
# editable install points at, so `sys.path` is correct as it stands and reordering
# it would be an unrequested change to the one layout that never had the bug.
if _SRC.is_dir() and _current_package_src() != _SRC:
    _src = str(_SRC)
    if sys.path[:1] != [_src]:
        sys.path.insert(0, _src)
    os.environ["PYTHONPATH"] = _prepend(_src, os.environ.get("PYTHONPATH"))
