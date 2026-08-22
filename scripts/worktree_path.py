"""Make the editable install resolve to the worktree you are standing in.

**Not active until installed. See "Installing" at the bottom.**

`pip install -e` wrote a `.pth` holding one hardcoded path — the main checkout's
`src`. That is right when you are in the main checkout and wrong everywhere else,
and most of the work on this project happens in `git worktree`s under
`repos/mk-*`. The consequence has been the most expensive recurring hazard here:
a new module in a worktree is invisible, an edited one is silently the wrong copy,
and a green test run may have tested nothing. One agent reported a false green
from it, and two more designed around it unprompted before anyone named it.

A repo-root `conftest.py` fixed it for `pytest`. It did not fix bare `python`,
because a conftest only runs under pytest — so reading a signature or probing
behaviour from a worktree still silently read the main checkout. The two trees
are usually identical, which is exactly what makes that dangerous rather than
merely wrong.

This module fixes both, at interpreter startup, for every invocation.

**How.** Walk up from the current working directory looking for a `src` that
actually contains `model_migration_kit`. Use the first one found. If there is
none — you are somewhere unrelated — fall back to the main checkout, which is
precisely the old behaviour, so nothing that works today stops working.

**Why the cwd and not `__file__`.** What is being disambiguated is which checkout
the *caller* means, and the working directory is the only expression of that
available at interpreter startup. An agent working in `mk-c14a-impl` has that as
its cwd.

**Why the package check is not decoration.** The sibling `opik-rigor` checkout
also has a `src`. Matching on the directory name alone would point this at a tree
that does not contain the package at all.

**Why this cannot weaken the release gate.** `scripts/verify_release.py` runs its
wheel probe with `python -S -E`. `-S` skips site-packages entirely, so this file
is never imported, and `-E` ignores `PYTHONPATH`. What makes that probe
trustworthy is its own `sys.path.insert(0, extract)` and its `__path__`
assertion, and neither is reachable from here. That isolation was verified
against a doctored wheel across a 2x2 matrix.

**The trade being made.** Import resolution becomes dependent on the working
directory, which is unusual and will surprise someone eventually. That is the
cost. The benefit is that the failure it removes is *silent* — it produces a
passing test run against the wrong source — and a surprising-but-loud behaviour
is worth more than a predictable-but-silent one. `CHOSEN` exists so the surprise
is one command away from being explained.

**Installing** (nothing happens until you do this):

    python scripts/worktree_path.py --install

That copies this file into the venv's `site-packages` and rewrites
`_editable_impl_model_migration_kit.pth` to `import worktree_path`, keeping the
original beside it as `.pth.original`.

    python scripts/worktree_path.py --uninstall     # restores the original
    python scripts/worktree_path.py --status        # what is active right now

**Debugging once active:** `python -c "import worktree_path as w; print(w.CHOSEN)"`
prints the path it picked, or the fallback if it declined.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

#: The main checkout, used when the cwd tells us nothing. Rewritten at install
#: time so this file is not tied to one machine.
FALLBACK = str(Path(__file__).resolve().parent.parent / "src")

#: What this actually chose, for debugging.
CHOSEN: str | None = None

_PTH_NAME = "_editable_impl_model_migration_kit.pth"


def _nearest_src(start: str) -> str | None:
    """The closest ancestor `src` holding `model_migration_kit`, or `None`."""
    here = os.path.abspath(start)
    while True:
        candidate = os.path.join(here, "src")
        if os.path.isdir(os.path.join(candidate, "model_migration_kit")):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def install_path() -> None:
    """Put the right `src` at the front of `sys.path`. Called at import."""
    global CHOSEN
    try:
        chosen = _nearest_src(os.getcwd()) or FALLBACK
    except OSError:
        # A deleted or unreadable cwd must not break every python in this venv.
        chosen = FALLBACK
    CHOSEN = chosen
    if chosen in sys.path:
        sys.path.remove(chosen)
    sys.path.insert(0, chosen)


def _site_packages() -> Path:
    for entry in sys.path:
        if entry.endswith("site-packages") and (Path(entry) / _PTH_NAME).exists():
            return Path(entry)
    raise SystemExit(
        f"could not find a site-packages holding {_PTH_NAME}. Run this with the "
        f"venv's own interpreter: .venv/Scripts/python.exe scripts/worktree_path.py"
    )


def _cli() -> int:
    parser = argparse.ArgumentParser(description="install/remove the worktree path hook")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    group.add_argument("--status", action="store_true")
    args = parser.parse_args()

    sp = _site_packages()
    pth, original = sp / _PTH_NAME, sp / (_PTH_NAME + ".original")

    if args.status:
        print(f"site-packages: {sp}")
        print(f"{_PTH_NAME}: {pth.read_text(encoding='utf-8').strip()!r}")
        print(f"original saved: {'yes' if original.exists() else 'no'}")
        print(f"hook module present: {'yes' if (sp / 'worktree_path.py').exists() else 'no'}")
        print(f"this cwd would resolve to: {_nearest_src(os.getcwd()) or FALLBACK}")
        return 0

    if args.install:
        if not original.exists():
            shutil.copy2(pth, original)
        shutil.copy2(Path(__file__).resolve(), sp / "worktree_path.py")
        pth.write_text("import worktree_path\n", encoding="utf-8")
        print(f"installed. original kept at {original}")
        print("verify: python -c \"import worktree_path as w; print(w.CHOSEN)\"")
        return 0

    if not original.exists():
        raise SystemExit(f"no {original.name} to restore; refusing to guess")
    shutil.copy2(original, pth)
    (sp / "worktree_path.py").unlink(missing_ok=True)
    print(f"restored {pth} from {original.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
else:
    install_path()
