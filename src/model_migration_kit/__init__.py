"""model-migration-kit: statistically defensible go/no-go verdicts for model migrations.

v0.1 is a command-line tool and exports no public Python API. That is a decision
rather than an omission, and it is worth stating where a reader will look for it.

The definition of done for this version is entirely a CLI story: a stranger with
no keys runs ``migkit demo`` and reads a report; a stranger with keys points it at
two models and a golden set and gets an artifact and an exit code. Nothing in that
requires importing this package. Meanwhile the objects a library API would have to
expose -- the comparison report, the judge rows, the per-gate statistics -- are
built on opik-rigor's report dictionaries, which are ``dict[str, Any]`` today and
scheduled to become typed objects in its 0.2. Re-exporting them now would turn a
surface that is about to move into a compatibility promise, and the first thing
that promise would do is prevent taking the improvement.

So ``__all__`` is empty and the modules stay importable for anyone who wants them
at their own risk. When the dependency's surface settles, a considered public API
can be added in a minor release; removing one is a major.

One consequence worth knowing, since it caused a real near-miss: this file's
existence is what makes ``model_migration_kit`` a regular package rather than a
namespace package. Namespace packages multiplex their ``__path__``, and while this
file was absent a wheel that had *omitted* the bundled demo data still appeared to
contain it -- because ``importlib.resources`` silently merged the developer's own
``src/`` into the same package. The release checks now assert the wheel in a bare
subprocess for that reason, and this file removes the mechanism entirely.
"""

from __future__ import annotations

__all__: list[str] = []

#: The single source of truth for the version, at runtime *and* at build time.
#: ``pyproject.toml`` declares ``dynamic = ["version"]`` and points
#: ``[tool.hatch.version]`` at this file, so the packaging metadata is derived
#: from this line rather than being a second copy of it. Two version numbers that
#: can disagree is a bug waiting for the worst possible moment to surface: the
#: number a user quotes when filing a bug is this one, and the number the index
#: serves is the metadata, so a drift between them is invisible to everyone in a
#: position to notice. ``scripts/verify_release.py`` still compares every place
#: the number appears -- this line, the wheel metadata, both filenames -- because
#: single-sourcing removes the failure mode and the check proves it stayed removed.
#:
#: Bumping is one edit to this line, in its own commit, with the ``CHANGELOG.md``
#: section for that version in the same commit -- never in the release commit,
#: never after the tag.
__version__ = "0.1.0.dev0"
