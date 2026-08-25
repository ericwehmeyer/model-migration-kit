"""Tests for scripts/check_contract.py -- specifically, what a citation is *about*.

A contract citation is a promise about the tree the contract governs. This box
holds eighty-odd checkouts of this same project side by side, so a citation like
``src/model_migration_kit/report.py:5700`` names a file that exists, at thirty-seven
different lengths, in eighty different states of done. If the checker is willing
to answer the question from any of them, the answer it gives is not about the
contract's tree, and the gate goes green on a fact from somewhere else.

``verify_release.py`` already holds this line -- ``test_a_cli_importable_from_
another_checkout_is_a_skip_not_a_pass`` in ``test_release_checks.py`` refuses to
verify a README against a CLI imported from a different tree, on the grounds that
PASS would be a claim about someone else's code. This file holds the same line
for citations.

**The layout is built here, not borrowed.** The severity of the original defect
depended on what happened to sit in ``REPO.parent`` on one machine; a test that
leaned on that would pass on a laptop with one checkout and prove nothing. Every
test below constructs its own governed tree, its own sibling project, and its own
decoy checkout under ``tmp_path``, and points the module at them.

Loaded the way ``test_release_checks.py`` loads its script: by path, because
``scripts/`` is not a package. That file is the precedent for testing ``scripts/``
here; this one follows it.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_contract", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module()


def _write(path: Path, lines: int) -> Path:
    """A file of exactly ``lines`` lines, each one naming the tree it sits in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tree = path.parents[2].name
    path.write_text("".join(f"# {tree} line {n}\n" for n in range(1, lines + 1)), encoding="utf-8")
    return path


def _layout(tmp_path) -> dict[str, Path]:
    """A governed tree, a genuine sibling project, and a decoy checkout.

    The decoy is the whole point: same package name, same relative paths,
    *different lengths*, so a citation answered by the wrong tree gives a visibly
    different answer instead of accidentally agreeing. A fixture where the broken
    and the correct implementation agree is a fixture that tests nothing.
    """
    root = tmp_path.resolve()
    governed = root / "mk-governed"
    sibling = root / "opik-rigor"
    decoy = root / "mk-some-other-checkout"

    _write(governed / "src" / "model_migration_kit" / "report.py", 40)
    _write(governed / "src" / "model_migration_kit" / "cli.py", 12)
    _write(sibling / "src" / "opik_rigor" / "judge.py", 30)
    # Same package, same relative path, five hundred lines instead of forty.
    _write(decoy / "src" / "model_migration_kit" / "report.py", 500)
    # A name that exists ONLY in the decoy, so "does this resolve at all" has a
    # different answer per tree.
    _write(decoy / "src" / "model_migration_kit" / "invented_here.py", 500)

    return {"root": root, "governed": governed, "sibling": sibling, "decoy": decoy}


@pytest.fixture
def trees(tmp_path, monkeypatch):
    """The layout, wired up the way the module shipped when the hole was found.

    ``SIBLINGS`` keeps the blanket parent entry -- that is the shape that made the
    old code resolve a neighbour, and it is what makes nine of the fifteen tests
    here red against it. Afterwards ``SIBLINGS`` only feeds ``index_symbols`` and
    the answer no longer depends on it, which is the point: where a citation may
    be *written* relative to and what tree it may *land in* are different
    questions, and only the second decides whether the gate is allowed to answer.
    """
    layout = _layout(tmp_path)
    monkeypatch.setattr(cc, "REPO", layout["governed"])
    monkeypatch.setattr(cc, "SIBLINGS", (layout["governed"], layout["sibling"], layout["root"]))
    monkeypatch.setattr(cc, "CITABLE_TREES", (layout["governed"], layout["sibling"]), raising=False)
    return layout


@pytest.fixture
def narrow_trees(tmp_path, monkeypatch):
    """The layout with no blanket parent root at all -- the shipped shape after."""
    layout = _layout(tmp_path)
    monkeypatch.setattr(cc, "REPO", layout["governed"])
    monkeypatch.setattr(cc, "SIBLINGS", (layout["governed"], layout["sibling"]))
    monkeypatch.setattr(cc, "CITABLE_TREES", (layout["governed"], layout["sibling"]), raising=False)
    return layout


def _run(tmp_path, monkeypatch, capsys, text: str) -> tuple[int, str]:
    """Run ``main()`` over a one-off plan file and hand back (exit code, output)."""
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_contract.py", str(plan)])
    code = cc.main()
    return code, capsys.readouterr().out


# ----------------------------------------------------------------------------------
# The defect: a citation carrying a directory component skipped the containment test
# ----------------------------------------------------------------------------------


def test_a_citation_into_another_checkout_does_not_resolve(trees):
    """``mk-some-other-checkout/src/.../report.py`` is a real file and not our file.

    This is the shape the old guard missed: the containment test sat behind "the
    citation has no directory component", so a citation that carried one skipped
    it entirely and was resolved against whatever the parent directory happened to
    hold.
    """
    assert cc.resolve("mk-some-other-checkout/src/model_migration_kit/report.py") is None


def test_a_line_number_is_answered_by_the_governed_file_not_a_neighbour(
    tmp_path, monkeypatch, capsys, trees
):
    """The failure that made this worth fixing.

    Line 400 is past the end of the governed ``report.py`` (40 lines) and well
    inside the decoy's (500). If the neighbour is allowed to answer, the contract
    is certified against a file it does not govern, and the gate goes green on a
    line that does not exist in the tree the agent will open.
    """
    code, out = _run(
        tmp_path,
        monkeypatch,
        capsys,
        "See `mk-some-other-checkout/src/model_migration_kit/report.py:400`.\n",
    )
    assert code == 1
    # It must fail as unresolvable, not slide past into the line-range check.
    assert "[PASS] every cited file exists" not in out
    assert "report.py:400" in out


def test_a_parent_traversal_out_of_the_tree_does_not_resolve(trees):
    """``../`` leaves the governed root while still reading as a path under it.

    Narrowing the list of search roots does not close this on its own: the first
    root is the governed tree itself, and ``governed/../mk-some-other-checkout``
    walks straight out of it. Containment has to be decided on the path that was
    actually found, normalised -- not on the root it was found under.
    """
    assert cc.resolve("../mk-some-other-checkout/src/model_migration_kit/report.py") is None


def test_an_absolute_path_into_another_checkout_does_not_resolve(trees):
    """The same claim, written the way a traceback writes it."""
    absolute = trees["decoy"] / "src" / "model_migration_kit" / "report.py"
    assert cc.resolve(str(absolute)) is None


def test_a_file_that_exists_only_in_another_checkout_reads_as_missing(
    tmp_path, monkeypatch, capsys, trees
):
    """A name the governed tree has never heard of must read as missing *here*.

    ``invented_here.py`` exists, five hundred lines of it, one directory over. The
    honest answer for this contract is that there is no such file, and the gate
    should say so rather than certify a citation against a stranger.
    """
    code, out = _run(
        tmp_path,
        monkeypatch,
        capsys,
        "See `mk-some-other-checkout/src/model_migration_kit/invented_here.py:400`.\n",
    )
    assert code == 1
    assert "invented_here.py" in out
    assert "[PASS] every cited file exists" not in out


@pytest.mark.skipif(os.name != "nt", reason="a drive letter is the thing being lost")
def test_a_citation_that_lost_its_drive_letter_does_not_resolve(trees):
    """R39.3, in the words of the audit that found it.

    ``CITATION`` excludes ``:`` -- it has to, that is the separator before the
    line number -- so a rooted Windows path in a contract arrives with its drive
    shorn off: ``\\Users\\...\\mk-some-other-checkout\\src\\...``. That is still an
    absolute path, and joining it to any root re-anchors it onto *this* drive,
    where the file it names is sitting. It resolved, and it was line-checked.
    """
    absolute = str(trees["decoy"] / "src" / "model_migration_kit" / "report.py")
    assert absolute[1:3] == ":\\"
    assert cc.resolve(absolute[2:]) is None


def test_a_copy_of_the_tree_inside_the_tree_does_not_resolve(tmp_path, monkeypatch, trees):
    """Containment alone is not enough: some strangers live indoors.

    ``.claude/worktrees`` sits inside this repo and holds whole checkouts of it,
    and a vendored ``.venv`` holds a whole copy of a dependency. Both pass any
    "is it under the governed root" test and neither is the tree. The bare-name
    branch already refused them; a citation with a directory component did not
    reach that branch.
    """
    stale = trees["governed"] / ".claude" / "worktrees" / "mk-old"
    _write(stale / "src" / "model_migration_kit" / "report.py", 500)
    assert cc.resolve(".claude/worktrees/mk-old/src/model_migration_kit/report.py") is None


def test_the_shipped_search_roots_do_not_include_the_whole_parent_directory(trees):
    """Asserted against the module as it ships, not against the fixture.

    ``REPO.parent`` as a blanket entry is what turned eighty-one neighbouring
    checkouts into a citation surface. The fixtures deliberately keep a broad root
    to prove containment holds regardless; this one pins the configuration itself,
    and it needs no layout, so it says the same thing on a machine with one
    checkout.
    """
    module = _load_module()
    assert module.REPO.parent not in module.SIBLINGS
    assert module.REPO in module.CITABLE_TREES
    assert all(module.REPO.parent != tree for tree in module.CITABLE_TREES)


# ----------------------------------------------------------------------------------
# What must keep working
# ----------------------------------------------------------------------------------


def test_a_path_in_the_governed_tree_still_resolves(trees):
    found = cc.resolve("src/model_migration_kit/report.py")
    assert found == (trees["governed"] / "src" / "model_migration_kit" / "report.py").resolve()


def test_the_human_spelling_of_a_sibling_project_still_resolves(narrow_trees):
    """``opik-rigor/src/opik_rigor/judge.py`` -- the reason the parent was a root.

    The comment at the head of the module says the parent is searched so a citation
    may be written the way a reader would read it, rooted at the sibling project's
    own name rather than at a root the reader has to infer. That is the legitimate
    half of the parent and it has to survive the narrowing -- so this runs against
    the layout with *no* blanket parent root, where only a rule that knows the
    sibling by name can resolve it.
    """
    found = cc.resolve("opik-rigor/src/opik_rigor/judge.py")
    assert found == (narrow_trees["sibling"] / "src" / "opik_rigor" / "judge.py").resolve()


def test_the_human_spelling_does_not_open_the_parent_back_up(narrow_trees):
    """The sibling's name is a key, not a doorway: its neighbours stay out."""
    assert cc.resolve("mk-some-other-checkout/src/model_migration_kit/report.py") is None


def test_a_bare_name_that_lives_only_in_the_sibling_is_still_flagged(
    tmp_path, monkeypatch, capsys, trees
):
    """The ``judge.py`` error the script was written for, still caught.

    Resolvable is not the same as unambiguous: a bare ``judge.py`` reads as a file
    in this package and is not one. It resolves -- the sibling project is citable
    -- and it is still reported, because the contract must spell the path out.
    """
    code, out = _run(tmp_path, monkeypatch, capsys, "See `judge.py:5`.\n")
    assert code == 1
    assert "judge.py" in out
    assert "write the path out" in out


def test_a_bare_name_in_the_governed_tree_is_not_flagged(tmp_path, monkeypatch, capsys, trees):
    """``cli.py:5`` is unambiguous here and must stay a pass."""
    code, out = _run(tmp_path, monkeypatch, capsys, "See `cli.py:5`.\n")
    assert code == 0
    assert "[PASS] every cited file exists" in out


def test_a_line_past_the_end_of_a_governed_file_still_fails(tmp_path, monkeypatch, capsys, trees):
    """The check's original job, unweakened: 400 > 40 in the tree that counts."""
    code, out = _run(
        tmp_path, monkeypatch, capsys, "See `src/model_migration_kit/report.py:400`.\n"
    )
    assert code == 1
    assert "file has 40 lines" in out


def test_a_line_inside_a_governed_file_passes(tmp_path, monkeypatch, capsys, trees):
    code, out = _run(tmp_path, monkeypatch, capsys, "See `src/model_migration_kit/report.py:12`.\n")
    assert code == 0
    assert "Contract citations check out." in out
