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

So the modules stay importable for anyone who wants them at their own risk, and
``__all__`` names only the three functions below. When the dependency's surface
settles, a considered public API can be added in a minor release; removing one is
a major.

**Why those three are the exception.** The package ships three data files --
``demo_goldenset.jsonl``, ``demo_rubric.md`` and ``demo.toml`` -- and a reader who
installed the wheel had no way to reach any of them. The README's own example said
``GoldenSet.load('src/model_migration_kit/data/demo_goldenset.jsonl')``, which is a
path inside a source checkout and does not exist in an install; ``dir()`` on this
package returned nothing that helped. That is the gap ``opik_rigor``'s
``example_rubric_path`` closes on the other side of the seam, and it is a different
kind of promise from the one the paragraph above declines to make: a filesystem
path to a file this wheel already contains cannot change shape when rigor's report
dictionaries become typed objects. The risk that argument is about is not present
here, so the reason to withhold is not either.

They are also deliberately *not* implemented by importing anything from this
package. ``tests/test_import_purity.py`` asserts that a bare ``import
model_migration_kit`` loads no submodule of its own at all, and a convenience
re-export is exactly how that kind of guarantee erodes; ``demo.py`` imports the
three filenames *from here* rather than the other way round.

One consequence worth knowing, since it caused a real near-miss: this file's
existence is what makes ``model_migration_kit`` a regular package rather than a
namespace package. Namespace packages multiplex their ``__path__``, and while this
file was absent a wheel that had *omitted* the bundled demo data still appeared to
contain it -- because ``importlib.resources`` silently merged the developer's own
``src/`` into the same package. The release checks now assert the wheel in a bare
subprocess for that reason, and this file removes the mechanism entirely.
"""

from __future__ import annotations

from pathlib import Path

__all__: list[str] = [
    "demo_config_path",
    "demo_goldenset_path",
    "demo_rubric_path",
]

#: Directory inside this package holding the three bundled demo files, and their
#: names. ``demo.py`` imports these rather than spelling them a second time.
#: ``scripts/verify_release.py`` keeps its own copy on purpose -- it audits a wheel
#: from the outside and must not import the package it is auditing -- and
#: ``tests/test_release_checks.py`` asserts the two lists agree, so the duplication
#: is a checked one rather than a second source of truth.
DATA_DIR = "data"
DEMO_GOLDENSET_NAME = "demo_goldenset.jsonl"
DEMO_RUBRIC_NAME = "demo_rubric.md"
DEMO_CONFIG_NAME = "demo.toml"


def _data_path(name: str) -> Path:
    """Resolve one bundled data file, or say plainly that the wheel is broken.

    ``Path(__file__).parent`` rather than ``importlib.resources.files``: the caller
    hands the result to :meth:`GoldenSet.load`, to ``JudgeConfig.load`` or to their
    own ``open``, all of which want a filesystem path, and the ``as_file`` context
    manager that would make a zipped install work returns a path valid only inside
    a ``with`` block. A wheel install is unzipped, which is what ``pip install``
    does and what every check in this repository exercises; ``opik_rigor``'s
    ``example_rubric_path`` makes the same trade for the same reason.

    A missing file is a packaging fault rather than user error, and the message
    says so -- the alternative is a ``FileNotFoundError`` on a path the reader has
    never typed and cannot be expected to recognise.
    """
    path = Path(__file__).resolve().parent / DATA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(
            f"the bundled file {name!r} is missing from {path.parent}. This is a "
            f"packaging fault in model-migration-kit, not something you did."
        )
    return path


def demo_goldenset_path() -> Path:
    """Path to the bundled 12-item demo golden set, as installed.

    The set ``migkit demo`` runs, and a worked example of the format documented in
    the README: JSONL, one object per line, ``{id, input, reference?, tags?,
    metadata?}``. Read it, then write your own -- a golden set copied from a
    library measures the library's idea of your workload.
    """
    return _data_path(DEMO_GOLDENSET_NAME)


def demo_rubric_path() -> Path:
    """Path to the bundled demo rubric, as installed.

    A rubric is the measuring instrument: prose, in whatever file the ``[[judge]]``
    table's ``rubric =`` key points at, telling the judge model what a good response
    is and what score a bad one earns. Every ``[[judge]]`` needs one and its hash is
    recorded in the report, so editing it invalidates comparisons taken against the
    old text -- deliberately.

    It deliberately says nothing about JSON or response format: ``opik_rigor``'s
    ``PROMPT_TEMPLATE`` appends the output-format instruction itself, and a rubric
    that restates it sends the same block to the judge twice.
    """
    return _data_path(DEMO_RUBRIC_NAME)


def demo_config_path() -> Path:
    """Path to the bundled demo config, as installed.

    A worked ``[[judge]]`` table and a full ``[thresholds]`` table with every
    default spelled out and commented. Note that its judge declares no ``adapter``
    key, because ``migkit demo`` supplies the judge's adapter itself; a config for
    ``migkit compare`` must declare one, and may not declare ``"fake"``.
    """
    return _data_path(DEMO_CONFIG_NAME)


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
__version__ = "0.1.0"
