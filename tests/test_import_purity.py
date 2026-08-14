"""Import purity, asserted in a subprocess -- and the suite's own marker declarations.

Two checks the project's frozen contracts require and nothing in the tree made.

**Import purity.** `docs/session-4-release-contract.md` section 2 rule 4: *"A
subprocess test asserts that importing ``model_migration_kit`` loads neither
``model_migration_kit.cli`` nor ``model_migration_kit.report``, and neither
``jinja2`` nor ``rich``."* Section 5 item 8 restates it as a release-checklist
command against the installed wheel, with a wider forbidden set. Rule 4 also says
*why* it has to be a subprocess: *"In-process it would pass or fail depending on
what the rest of the suite imported first; opik-rigor learned this the expensive
way when three tests asserted ``find_spec("openai") is None`` and were testing the
environment rather than the library."* By the time this module runs, pytest has
imported `model_migration_kit.cli` (test_cli.py), `model_migration_kit.report`
(test_report.py), jinja2 and rich. An in-process `sys.modules` assertion here would
therefore be red on a perfectly pure package -- and, worse, an in-process assertion
of the *absence* of something would go green the day the dependency stopped being
installed at all. So every purity claim below is made about a fresh interpreter,
and each one is paired with a check that the same interpreter *can* see the thing
when it is genuinely there. A purity test that passes because nothing is installed
is the skipped-check-dressed-as-a-passing-check this project refuses.

The comparison is on **top-level module names**, per the same rule: *"The check
compares top-level module names, not ``str.startswith``."* The sibling's
`8b6e6a9` had a test flag its own package as an Opik leak because
`"opik_rigor".startswith("opik")`. That trap is live here -- `opik_rigor` really is
loaded once `.report` is imported -- so it is asserted rather than assumed.

**Marker declarations.** The second check has nothing to do with imports and lives
here because it is the same defect: a declaration the project makes about itself
that nothing verified. `pyproject.toml` declared a `requires_network` marker that no
test carried, while `ci.yml` and `drift-canary.yml` both spent a
`-m "not requires_network"` deselecting it -- a flag that reads as protection and
filters nothing. The declaration is gone; this keeps it gone.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomllib arrived in the stdlib in 3.11.
    import tomli as tomllib  # type: ignore[no-redef]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
_TESTS = Path(__file__).resolve().parent
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

#: Verbatim from section 5 item 8's one-liner. Wider than rule 4's pair because the
#: checklist runs against an installed wheel where a provider SDK could plausibly
#: have been dragged in; `jinja2` and `rich` are the two that are actually declared
#: dependencies and so the two that could leak here.
FORBIDDEN_TOP_LEVEL = frozenset({"jinja2", "rich", "anthropic", "openai", "opik"})

#: Rule 4's submodules, by dotted name. Not by prefix: `startswith` is what made the
#: sibling's package report itself (`8b6e6a9`), and the trailing dot matters --
#: `model_migration_kit` itself starts with `model_migration_kit`.
FORBIDDEN_SUBMODULES = ("model_migration_kit.cli", "model_migration_kit.report")

PACKAGE = "model_migration_kit"


def _probe_source(*imports: str) -> str:
    """A program that imports what it is told to and reports the child's module table.

    The child prints; this file asserts. Keeping the assertions in the parent is
    what lets a failure name the leaked module instead of surfacing as a child
    process that exited 1 with an empty message.
    """
    return (
        "import json, sys\n"
        + "".join(f"import {name}\n" for name in imports)
        + "print(json.dumps({\n"
        '    "top_level": sorted({name.split(".")[0] for name in sys.modules}),\n'
        '    "dotted": sorted(n for n in sys.modules if n.startswith("model_migration_kit.")),\n'
        '    "file": getattr(sys.modules.get("model_migration_kit"), "__file__", None),\n'
        "}))\n"
    )


def _run_probe(*imports: str) -> dict:
    """Run the probe in a fresh interpreter that imports *this* checkout.

    Two decisions, both load-bearing.

    ``sys.executable`` rather than a hard-coded venv path: it is by construction an
    interpreter in which this package's dependencies are installed, because it is
    the one already running the suite. A path spelled out in the test would be wrong
    on CI, wrong on POSIX, and wrong for anyone whose venv lives elsewhere.

    ``PYTHONPATH`` pointing at this file's own ``src/`` rather than trusting the
    installed distribution: the interpreter is shared, but the *code* under test
    must be the tree this test file was checked out with. Without it a git worktree
    -- or any environment holding an editable install pointing somewhere else --
    would silently assert purity of a different checkout, which is the failure this
    project keeps finding in other clothes. ``PYTHONPATH`` precedes ``site-packages``
    on ``sys.path``, so it wins over an editable install; the interpreter is *not*
    run with ``-I`` or ``-E``, which would discard it, nor with ``-S``, which would
    hide the dependencies the reverse checks need to see.

    :func:`test_the_probe_imports_the_package_from_this_checkout` asserts that it
    worked, so the arrangement cannot fail open.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(_SRC), existing] if existing else [str(_SRC)])
    # Nothing here is random, but the child's `sys.modules` iteration order is
    # hash-dependent; pinning the seed makes any future diff of raw probe output
    # meaningful. Every value this file asserts on is sorted regardless.
    env["PYTHONHASHSEED"] = "0"
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _probe_source(*imports)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        # Explicit, so the child's implicit `sys.path[0]` is a known directory. The
        # repo root holds `src/`, not `model_migration_kit/`, so nothing shadows.
        cwd=str(_REPO_ROOT),
        env=env,
    )
    assert completed.returncode == 0, (
        f"the import-purity probe ({imports or 'no imports'}) failed to run:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def bare_import() -> dict:
    """One child interpreter that has done nothing but ``import model_migration_kit``."""
    return _run_probe(PACKAGE)


class TestTheProbeItself:
    """Before believing what the probe does not see, check what it does.

    Three ways this could pass while asserting nothing: the child could import a
    different copy of the package, the child could fail to import it at all, or the
    forbidden modules could be absent from the environment entirely. Each is closed
    here, because section 5 item 8's evidence is "prints `[]`, exits 0" -- and an
    empty list is exactly what a broken probe also produces.
    """

    def test_the_probe_imports_the_package_from_this_checkout(self, bare_import: dict) -> None:
        expected = _SRC / PACKAGE / "__init__.py"
        actual = bare_import["file"]
        assert actual is not None, "the child never imported the package at all"
        assert Path(actual).resolve() == expected.resolve(), (
            "the probe imported a different copy of the package than the one this "
            f"test file was checked out with, so its purity verdict is about the "
            f"wrong tree: child imported {actual}, expected {expected}"
        )

    def test_the_probe_sees_a_leak_when_one_is_really_there(self) -> None:
        """The red half of red-green, kept in the suite.

        Same probe, same interpreter, with the leak added on purpose. If this ever
        goes green-by-emptiness the forward checks below are worthless, and nothing
        else in the tree would notice.
        """
        leaked = _run_probe(PACKAGE, "rich", "jinja2")
        found = sorted(FORBIDDEN_TOP_LEVEL.intersection(leaked["top_level"]))
        assert found == ["jinja2", "rich"], (
            "the probe cannot detect a leak it was handed deliberately, so its "
            f"silence proves nothing: got {found}"
        )

    def test_the_probe_sees_a_submodule_when_one_is_really_there(self) -> None:
        loaded = _run_probe(f"{PACKAGE}.report")
        assert "model_migration_kit.report" in loaded["dotted"], (
            "the probe cannot see a submodule it was told to import: "
            f"{loaded['dotted']}"
        )


class TestImportPurity:
    """Contract section 2 rule 4, and section 5 item 8, in a fresh interpreter."""

    def test_importing_the_package_pulls_in_neither_jinja2_nor_rich(
        self, bare_import: dict
    ) -> None:
        """Rule 4's first clause.

        Rule 4 is candid that nothing *breaks* if they load -- jinja2 and rich are
        hard dependencies here, not optional extras -- and keeps the test anyway,
        because "the cheap version of this rule stops being cheap the moment someone
        adds a module-scope side effect to `cli.py`". This is the test that turns
        that from a comment into a tripwire.
        """
        leaked = sorted(FORBIDDEN_TOP_LEVEL.intersection(bare_import["top_level"]))
        assert not leaked, (
            f"`import {PACKAGE}` dragged in {', '.join(leaked)} -- importing this "
            f"package is supposed to cost nothing beyond the stdlib "
            f"(session-4 contract section 2 rule 4, section 5 item 8)"
        )

    @pytest.mark.parametrize("submodule", FORBIDDEN_SUBMODULES)
    def test_importing_the_package_loads_neither_the_cli_nor_the_report_module(
        self, bare_import: dict, submodule: str
    ) -> None:
        """Rule 4's second clause, by dotted name.

        `cli` is the console-script entry point and importing it at package-import
        time would build the argparse tree for every consumer; `report` is what pulls
        jinja2 and rich, which a caller embedding the verdict logic should not pay
        for. Both stay reachable as attributes of the package -- they are just not
        loaded until someone asks.
        """
        assert submodule not in bare_import["dotted"], (
            f"`import {PACKAGE}` loaded {submodule}; the package's own docstring and "
            f"section 2 rule 4 both say it must not. Loaded submodules: "
            f"{bare_import['dotted'] or 'none'}"
        )

    def test_the_bare_import_loads_no_submodule_of_its_own_at_all(
        self, bare_import: dict
    ) -> None:
        """Stronger than rule 4 asks, and true today: `__init__.py` imports nothing
        from its own package. Recorded as a test so that relaxing it is a decision
        someone makes, rather than something that drifts in behind a convenience
        re-export. If a future public API needs one, this is the line to change --
        and rule 4's two names above are the ones that may not be it."""
        assert bare_import["dotted"] == [], (
            f"the bare import now loads {bare_import['dotted']}"
        )


class TestTheDependenciesAreReallyPresent:
    """The reverse direction, so absence above means "not imported", not "not installed".

    Without these, uninstalling jinja2 would turn the purity tests green -- the exact
    shape of the sibling defect rule 4 cites: three tests asserting
    `find_spec("openai") is None` that were "testing the environment rather than the
    library" (`CHANGELOG.md`, "A fourth defect was **not** caught by authorship
    separation").
    """

    def test_jinja2_and_rich_arrive_with_the_report_module(self) -> None:
        loaded = _run_probe(f"{PACKAGE}.report")
        missing = sorted({"jinja2", "rich"}.difference(loaded["top_level"]))
        assert not missing, (
            f"importing {PACKAGE}.report did not load {', '.join(missing)}. Either "
            f"the dependency is not installed in {sys.executable} -- in which case "
            f"the purity checks above are passing vacuously -- or the renderer no "
            f"longer uses it and this test's premise has changed."
        )

    def test_the_cli_module_is_importable_and_is_not_free(self) -> None:
        """`cli` is the console-script target: `migkit = model_migration_kit.cli:main`.
        A wheel whose `cli` does not import is a command that dies on first use, and
        the release checks assert the module is *present* in the wheel without ever
        importing it."""
        loaded = _run_probe(f"{PACKAGE}.cli")
        assert "model_migration_kit.cli" in loaded["dotted"]
        assert "rich" in loaded["top_level"], (
            "the CLI no longer costs anything to import, which would make the "
            "purity check above unable to distinguish 'not imported' from 'free'"
        )


class TestTopLevelNamesNotPrefixes:
    """`8b6e6a9`: a test detected a leaked Opik import with `m.startswith('opik')`,
    and `opik_rigor` starts with `opik`, so the package reported itself as a leak.

    This project ships that exact collision -- `opik_rigor` is its central dependency
    and `opik` is on the forbidden list -- so the discipline is asserted against the
    real module table rather than a fixture.
    """

    def test_opik_rigor_is_loaded_by_report_and_is_not_mistaken_for_opik(self) -> None:
        loaded = _run_probe(f"{PACKAGE}.report")
        assert "opik_rigor" in loaded["top_level"], (
            "opik_rigor is not loaded, so this test is no longer exercising the "
            "collision it exists for"
        )
        assert "opik" not in loaded["top_level"], (
            "opik itself is now loaded; this project has no Opik dependency of its "
            "own and the drift canary's reasoning depends on that staying true"
        )
        assert not FORBIDDEN_TOP_LEVEL.intersection(loaded["top_level"]) - {"jinja2", "rich"}, (
            "a forbidden name other than the two the renderer legitimately uses "
            f"appeared: {sorted(FORBIDDEN_TOP_LEVEL.intersection(loaded['top_level']))}"
        )

    def test_a_set_comparison_on_top_level_names_cannot_repeat_the_prefix_bug(self) -> None:
        """The property in isolation: membership, not prefix matching. Written out
        because the intersection above is only obviously right once you have seen
        what the wrong version does to these two names."""
        top_level = {"opik_rigor", "model_migration_kit", "json"}
        assert not FORBIDDEN_TOP_LEVEL.intersection(top_level)
        assert [name for name in top_level if name.startswith("opik")] == ["opik_rigor"], (
            "the prefix spelling of this check would have flagged opik_rigor"
        )


# ----------------------------------------------------------------------------------
# The suite's own markers
#
# Not about imports. It is here because it is the same failure the file exists to
# close -- something declared, deselected in two workflows, and asserted by nothing.
# ----------------------------------------------------------------------------------

_MARKER_USE = re.compile(r"pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)")


def _declared_markers() -> list[str]:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    declared = config["tool"]["pytest"]["ini_options"]["markers"]
    # Entries are "name: description"; the name is everything before the first colon.
    return [entry.split(":", 1)[0].strip() for entry in declared]


def _applied_markers() -> set[str]:
    """Every `pytest.mark.<name>` written anywhere in the suite.

    Textual on purpose: it sees `pytestmark = [pytest.mark.slow]` and decorators
    alike, without importing every test module or shelling out to `--collect-only`.
    It would not see a marker applied dynamically through `item.add_marker`, which
    this suite does not do and which `--strict-markers` does not police either.
    """
    used: set[str] = set()
    for path in sorted(_TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file names markers in prose; it applies none
        used.update(_MARKER_USE.findall(path.read_text(encoding="utf-8")))
    return used


def test_every_declared_marker_is_applied_by_at_least_one_test() -> None:
    """The gap this file was written to close, made permanent.

    `requires_network` was declared, deselected by `ci.yml` and `drift-canary.yml`
    with `-m "not requires_network"`, and carried by no test -- so the flag deselected
    nothing while reading, in two workflows and in `PROGRESS.md`, as the guarantee
    that CI never touches a provider. The real guarantee is elsewhere and is real:
    both workflows blank `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and run everything.

    `--strict-markers` already makes the opposite mistake loud: applying an
    undeclared marker is a collection error. This is the missing half. Together they
    say a marker's declaration and its first use belong in the same commit -- which
    is the only ordering in which the CI flag that deselects it can be written with
    evidence that it deselects something.
    """
    declared = _declared_markers()
    assert declared, "the markers table is empty; --strict-markers now bans every marker"
    unused = [name for name in declared if name not in _applied_markers()]
    assert not unused, (
        f"pyproject.toml declares {', '.join(unused)} but no test applies "
        f"{'them' if len(unused) > 1 else 'it'}. A declared-and-unused marker is a "
        f"CI `-m` flag that deselects nothing: delete the declaration, or apply it."
    )


def test_the_marker_scan_finds_the_marker_the_suite_actually_uses() -> None:
    """Guards the check above from passing because the scan found nothing at all --
    a regex that matched nothing would report every declared marker as used only if
    the declaration list were empty, and would report all of them unused otherwise,
    so this pins the scan to a known-true fact instead."""
    assert "slow" in _applied_markers()
    assert "parametrize" in _applied_markers(), "the scan is not seeing decorators"
