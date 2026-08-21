"""Tests for scripts/verify_release.py.

The release script is the thing that decides whether a release is allowed to
happen, so its own logic needs checking by something other than itself. Every
expectation below is written from the contracts (`docs/session-4-release-contract.md`
sections 1 and 5, `docs/session-3-contract.md` section 5.1,
`docs/readme-scan-contract.md` in full), not from running the code.

The wheel-shaped tests build synthetic zip files rather than invoking hatchling:
the interesting cases are the *broken* ones -- a wheel with no demo data, a wheel
carrying both an SPDX expression and a deprecated classifier -- and those cannot
be produced from this repository's pyproject.toml on purpose.
"""

from __future__ import annotations

import importlib.util
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_release.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_release", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vr = _load_module()


# ----------------------------------------------------------------------------------
# Name and requirement parsing
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("model-migration-kit", "model-migration-kit"),
        ("model_migration_kit", "model-migration-kit"),
        # Deliberately odd spellings: PEP 503 folds case, runs of separators, and
        # the three separator characters onto one normalised name. The rename
        # sweep rewrote the *expected* value here and left these inputs alone,
        # which is exactly the class of edit a mechanical replacement gets wrong --
        # test data that merely looks like the thing being renamed.
        ("Model.Migration.Kit", "model-migration-kit"),
        ("MODEL--MIGRATION--KIT", "model-migration-kit"),
        ("  model-migration-kit  ", "model-migration-kit"),
    ],
)
def test_pep503_normalisation_collapses_the_three_spellings(raw, expected):
    """Phase 0 leans on this: one URL covers the distribution and import names."""
    assert vr.normalize_project_name(raw) == expected


def test_migkit_is_a_different_name_from_migration_kit():
    """The contract's warning: do not let PEP 503's convenience become an
    assumption. `migkit` is a third, unrelated project name."""
    assert vr.normalize_project_name("migkit") != vr.normalize_project_name("model-migration-kit")


@pytest.mark.parametrize(
    ("requirement", "name"),
    [
        ("opik-rigor<0.2,>=0.1.0", "opik-rigor"),
        ("jinja2>=3.0", "jinja2"),
        ("tomli>=2.0; python_version < '3.11'", "tomli"),
        ("pytest-cov>=4.0; extra == 'dev'", "pytest-cov"),
        ("rich", "rich"),
    ],
)
def test_requirement_name(requirement, name):
    assert vr.requirement_name(requirement) == name


def test_requirement_marker_is_empty_when_unconditional():
    assert vr.requirement_marker("jinja2>=3.0") == ""
    assert vr.requirement_marker("tomli>=2.0; python_version < '3.11'") == "python_version < '3.11'"


def test_markers_compare_across_quote_styles():
    """hatchling normalises quotes into METADATA and pyproject.toml does not, so a
    byte comparison would report a difference that does not exist."""
    assert vr.markers_equivalent("python_version < '3.11'", 'python_version < "3.11"')
    assert vr.markers_equivalent("python_version<'3.11'", "python_version < '3.11'")
    assert not vr.markers_equivalent("python_version < '3.11'", "python_version < '3.12'")
    assert not vr.markers_equivalent("", "python_version < '3.11'")


def test_extras_are_not_runtime_requirements():
    runtime, extras = vr.split_requires_dist(
        [
            "jinja2>=3.0",
            "tomli>=2.0; python_version < '3.11'",
            "pytest>=7.0; extra == 'dev'",
            "ruff>=0.6; extra == \"dev\"",
        ]
    )
    assert [vr.requirement_name(r) for r in runtime] == ["jinja2", "tomli"]
    assert [vr.requirement_name(r) for r in extras] == ["pytest", "ruff"]


# ----------------------------------------------------------------------------------
# Licence identification
# ----------------------------------------------------------------------------------

APACHE_HEAD = """
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION
"""

MIT_TEXT = """MIT License

Copyright (c) 2026 Somebody

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
"""


def test_apache_text_is_identified():
    assert vr.spdx_from_license_text(APACHE_HEAD) == "Apache-2.0"


def test_mit_text_is_identified():
    assert vr.spdx_from_license_text(MIT_TEXT) == "MIT"


def test_unknown_licence_text_yields_none_rather_than_a_guess():
    """A guess here would manufacture agreement between the declared identifier
    and the shipped bytes, which is the one thing this check exists to detect."""
    assert vr.spdx_from_license_text("Do what you like. Seriously, anything.") is None


# ----------------------------------------------------------------------------------
# README claim extraction
# ----------------------------------------------------------------------------------


def test_a_mixed_document_takes_its_targets_from_the_fence_alone():
    """Rule 1 on a whole document rather than a single line: one fenced install and
    one loose one, and only the fence is a claim.

    Renamed and rewritten from test_pip_install_targets_are_extracted_from_prose_and_fences,
    whose name promised targets came from *both*. It passed under the contract, but not
    for any reason it could detect: its prose was `python -m pip install -e ".[dev]"`,
    which yields nothing whether it is scanned or not, so the assertion could not tell
    rule 1 from a flat-text scan. The unfenced line here names `migkit` -- the console
    script, not the distribution -- and sits at the head of its line, where rule 2
    cannot save it either. Scanning this document flat yields
    `['model-migration-kit', 'migkit']`, so rule 1 is the only thing keeping this
    green: mutating `readme_pip_install_targets` to read the whole text turns it red."""
    readme = """
# model-migration-kit

```console
$ pip install model-migration-kit
```

An older draft of this README told people to run

pip install migkit

which names the console script rather than the distribution, and never worked.
"""
    assert vr.readme_pip_install_targets(readme) == ["model-migration-kit"]


def test_bare_prog_name_in_prose_is_not_a_command():
    """The current README says "`migkit` answers ..." -- prose, not an invocation.
    Treating it as a command would make this check fail on English."""
    assert vr.readme_cli_commands("`migkit` answers the question.") == []
    assert vr.readme_cli_commands("migkit --help") == []


# ----------------------------------------------------------------------------------
# The frozen contract clause
# ----------------------------------------------------------------------------------

CONTRACT_CLAUSE = """
7. `Requires-Dist` lists `opik-rigor>=0.1.0,<0.2`, `jinja2>=3.0`, `rich>=13.0` and
   nothing else; `Requires-Python: >=3.10`.
"""


def test_contract_clause_is_parsed_from_the_document_not_copied():
    assert vr.contract_declared_requirements(CONTRACT_CLAUSE) == ["opik-rigor", "jinja2", "rich"]


def test_amended_contract_clause_is_followed():
    """The flag must clear itself when the sentence is amended -- otherwise the
    only way to silence it is to delete the dependency."""
    amended = CONTRACT_CLAUSE.replace(
        "`rich>=13.0` and", "`rich>=13.0`, `tomli>=2.0; python_version < '3.11'` and"
    )
    assert vr.contract_declared_requirements(amended) == [
        "opik-rigor",
        "jinja2",
        "rich",
        "tomli",
    ]


def test_missing_clause_yields_none_so_the_caller_can_skip():
    assert vr.contract_declared_requirements("no such sentence here") is None


# ----------------------------------------------------------------------------------
# Versions
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.1.0.dev0", "0.1.0.dev12", "0.2.0.dev0", "1.0.dev1"])
def test_dev_versions_are_recognised(version):
    assert vr.version_is_dev(version)


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3", "0.1.0rc1", "0.1.0.post1"])
def test_release_versions_are_not_dev(version):
    assert not vr.version_is_dev(version)


def test_wheel_version_comes_from_the_filename_field():
    assert vr.wheel_version_from_filename("model_migration_kit-0.1.0-py3-none-any.whl") == "0.1.0"
    assert (
        vr.wheel_version_from_filename("/tmp/d/model_migration_kit-0.1.0.dev0-py3-none-any.whl")
        == "0.1.0.dev0"
    )


def test_dunder_version_is_read_as_text(tmp_path):
    init = tmp_path / "__init__.py"
    init.write_text('"""doc."""\n\n__version__ = "0.1.0"\n__all__ = ["x"]\n', encoding="utf-8")
    assert vr.read_dunder_version(init) == "0.1.0"


def test_dunder_version_absent_before_phase_2(tmp_path):
    assert vr.read_dunder_version(tmp_path / "__init__.py") is None
    (tmp_path / "__init__.py").write_text("# nothing yet\n", encoding="utf-8")
    assert vr.read_dunder_version(tmp_path / "__init__.py") is None


# ----------------------------------------------------------------------------------
# Wheel-shaped checks, against synthetic archives
# ----------------------------------------------------------------------------------

METADATA_GOOD = """Metadata-Version: 2.5
Name: model-migration-kit
Version: 0.1.0
License-Expression: Apache-2.0
License-File: LICENSE
License-File: NOTICE
Classifier: Development Status :: 3 - Alpha
Requires-Python: >=3.10
Requires-Dist: jinja2>=3.0
Requires-Dist: opik-rigor<0.2,>=0.1.0
Requires-Dist: rich>=13.0
Requires-Dist: tomli>=2.0; python_version < '3.11'

readme body
"""


def _make_wheel(
    path: Path,
    *,
    data_files=vr.DEMO_DATA,
    metadata=METADATA_GOOD,
    licenses=("LICENSE", "NOTICE"),
    license_text=APACHE_HEAD,
    entry_points="[console_scripts]\nmigkit = model_migration_kit.cli:main\n",
    modules=("cli.py",),
) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name in data_files:
            zf.writestr(f"model_migration_kit/data/{name}", f"payload of {name}\n")
        for module in modules:
            zf.writestr(f"model_migration_kit/{module}", "def main():\n    return 0\n")
        zf.writestr("model_migration_kit-0.1.0.dist-info/METADATA", metadata)
        if entry_points is not None:
            zf.writestr("model_migration_kit-0.1.0.dist-info/entry_points.txt", entry_points)
        for name in licenses:
            body = license_text if name == "LICENSE" else "NOTICE body\n"
            zf.writestr(f"model_migration_kit-0.1.0.dist-info/licenses/{name}", body)
    return path


def _make_source_data(root: Path, names=vr.DEMO_DATA) -> Path:
    data = root / "src" / "model_migration_kit" / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in names:
        # write_bytes, not write_text: text mode turns \n into \r\n on Windows, and
        # the byte-for-byte comparison under test would then fail on the fixture
        # rather than on the code.
        (data / name).write_bytes(f"payload of {name}\n".encode())
    return data


def test_demo_data_present_in_the_wheel_passes(tmp_path):
    wheel = _make_wheel(tmp_path / "w.whl")
    result = vr.check_wheel_demo_data(wheel, _make_source_data(tmp_path))
    assert result.status == vr.PASS
    assert all(any(name in line for line in result.evidence) for name in vr.DEMO_DATA)


def test_a_wheel_missing_the_golden_set_fails(tmp_path):
    """The defect two independent reviews found: `.gitignore`'s `*.jsonl` rule
    plus `packages = ["src/model_migration_kit"]` ships a wheel with no demo, while
    every local test and the editable-install CI job still pass."""
    wheel = _make_wheel(tmp_path / "w.whl", data_files=("demo_rubric.md", "demo.toml"))
    result = vr.check_wheel_demo_data(wheel, _make_source_data(tmp_path))
    assert result.status == vr.FAIL
    assert "demo_goldenset.jsonl" in " ".join(result.evidence)


def test_demo_data_in_the_wheel_that_differs_from_source_fails(tmp_path):
    """A stale wheel is a wheel that does not ship what the repo says it does."""
    wheel = _make_wheel(tmp_path / "w.whl")
    data = _make_source_data(tmp_path)
    (data / "demo.toml").write_text("edited after the build\n", encoding="utf-8")
    result = vr.check_wheel_demo_data(wheel, data)
    assert result.status == vr.FAIL
    assert "stale build" in " ".join(result.evidence)


def test_empty_demo_file_in_the_wheel_fails(tmp_path):
    with zipfile.ZipFile(tmp_path / "w.whl", "w") as zf:
        for name in vr.DEMO_DATA:
            zf.writestr(f"model_migration_kit/data/{name}", "")
    result = vr.check_wheel_demo_data(tmp_path / "w.whl", _make_source_data(tmp_path))
    assert result.status == vr.FAIL


def test_coherent_licence_metadata_passes(tmp_path):
    wheel = _make_wheel(tmp_path / "w.whl")
    (tmp_path / "LICENSE").write_bytes(APACHE_HEAD.encode())
    (tmp_path / "NOTICE").write_bytes(b"NOTICE body\n")
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.PASS, result.render()


def test_deprecated_license_classifier_is_rejected(tmp_path):
    """PyPI rejects an upload carrying both an SPDX expression and the classifier;
    the sibling shipped exactly this (387b741)."""
    metadata = METADATA_GOOD.replace(
        "Classifier: Development Status :: 3 - Alpha",
        "Classifier: License :: OSI Approved :: Apache Software License",
    )
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.FAIL
    assert "License ::" in " ".join(result.evidence)


def test_missing_notice_is_rejected(tmp_path):
    """Apache-2.0 section 4(d) makes NOTICE load-bearing in a way MIT never was."""
    wheel = _make_wheel(tmp_path / "w.whl", licenses=("LICENSE",))
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.FAIL
    assert "NOTICE" in " ".join(result.evidence)


def test_spdx_expression_disagreeing_with_shipped_text_is_rejected(tmp_path):
    """The mismatch this check exists for: no packaging tool checks it for you."""
    wheel = _make_wheel(tmp_path / "w.whl", license_text=MIT_TEXT)
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.FAIL
    assert "MIT" in " ".join(result.evidence)


def test_licence_body_in_the_legacy_field_is_rejected(tmp_path):
    metadata = METADATA_GOOD.replace(
        "License-Expression: Apache-2.0",
        "License-Expression: Apache-2.0\nLicense: " + APACHE_HEAD.replace("\n", "\n        "),
    )
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.FAIL


def test_unidentifiable_licence_text_skips_rather_than_passes(tmp_path):
    wheel = _make_wheel(tmp_path / "w.whl", license_text="Do what you like.\n")
    result = vr.check_license_metadata(wheel, tmp_path)
    assert result.status == vr.SKIP
    assert "UNVERIFIED" in " ".join(result.evidence)


def test_tomli_marker_present_and_conditioned(tmp_path):
    wheel = _make_wheel(tmp_path / "w.whl")
    assert vr.check_tomli_marker(wheel).status == vr.PASS


def test_unconditioned_tomli_is_rejected(tmp_path):
    metadata = METADATA_GOOD.replace("tomli>=2.0; python_version < '3.11'", "tomli>=2.0")
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    result = vr.check_tomli_marker(wheel)
    assert result.status == vr.FAIL


def test_missing_tomli_is_rejected(tmp_path):
    """requires-python is >=3.10, CI runs 3.10, and tomllib is stdlib from 3.11."""
    metadata = "\n".join(
        line for line in METADATA_GOOD.splitlines() if "tomli" not in line
    )
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    assert vr.check_tomli_marker(wheel).status == vr.FAIL


def test_wrongly_conditioned_tomli_is_rejected(tmp_path):
    metadata = METADATA_GOOD.replace("python_version < '3.11'", "python_version < '3.10'")
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    assert vr.check_tomli_marker(wheel).status == vr.FAIL


def _write_pyproject(root: Path, extra: str = "") -> None:
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "model-migration-kit"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = [\n"
        '  "opik-rigor>=0.1.0,<0.2",\n'
        '  "jinja2>=3.0",\n'
        '  "rich>=13.0",\n'
        "  \"tomli>=2.0; python_version < '3.11'\",\n"
        "]\n"
        "\n[project.optional-dependencies]\n"
        'dev = ["pytest>=7.0"]\n' + extra,
        encoding="utf-8",
    )


def test_dependencies_match_pyproject(tmp_path):
    _write_pyproject(tmp_path)
    wheel = _make_wheel(tmp_path / "w.whl")
    result = vr.check_dependencies(wheel, tmp_path)
    assert result.status == vr.PASS, result.render()


def test_a_dependency_only_in_the_wheel_is_reported(tmp_path):
    _write_pyproject(tmp_path)
    metadata = METADATA_GOOD.replace(
        "Requires-Dist: rich>=13.0", "Requires-Dist: rich>=13.0\nRequires-Dist: requests>=2.0"
    )
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    result = vr.check_dependencies(wheel, tmp_path)
    assert result.status == vr.FAIL
    assert "requests" in " ".join(result.evidence)


def test_requires_python_disagreement_is_reported(tmp_path):
    _write_pyproject(tmp_path)
    metadata = METADATA_GOOD.replace("Requires-Python: >=3.10", "Requires-Python: >=3.11")
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    assert vr.check_dependencies(wheel, tmp_path).status == vr.FAIL


def test_contract_clause_flags_an_extra_dependency(tmp_path):
    """Neither side is silently accepted: the build is believed right and the
    frozen sentence stale, so this is a FLAG that names both."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "session-4-release-contract.md").write_text(CONTRACT_CLAUSE, encoding="utf-8")
    wheel = _make_wheel(tmp_path / "w.whl")
    result = vr.check_contract_dependency_clause(wheel, tmp_path)
    assert result.status == vr.FLAG
    joined = " ".join(result.evidence)
    assert "tomli" in result.summary
    assert "amend" in joined.lower()


def test_contract_clause_passes_once_amended(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    amended = CONTRACT_CLAUSE.replace(
        "`rich>=13.0` and", "`rich>=13.0`, `tomli>=2.0; python_version < '3.11'` and"
    )
    (docs / "session-4-release-contract.md").write_text(amended, encoding="utf-8")
    wheel = _make_wheel(tmp_path / "w.whl")
    assert vr.check_contract_dependency_clause(wheel, tmp_path).status == vr.PASS


def test_contract_clause_fails_when_the_build_drops_a_required_dependency(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "session-4-release-contract.md").write_text(CONTRACT_CLAUSE, encoding="utf-8")
    metadata = "\n".join(line for line in METADATA_GOOD.splitlines() if "rich" not in line)
    wheel = _make_wheel(tmp_path / "w.whl", metadata=metadata)
    result = vr.check_contract_dependency_clause(wheel, tmp_path)
    assert result.status == vr.FAIL


def test_missing_contract_skips_rather_than_passes(tmp_path):
    wheel = _make_wheel(tmp_path / "w.whl")
    assert vr.check_contract_dependency_clause(wheel, tmp_path).status == vr.SKIP


def test_console_script_target_must_be_in_the_wheel(tmp_path):
    """A wheel declaring `migkit = model_migration_kit.cli:main` without shipping
    cli.py installs a command that dies with ModuleNotFoundError on first use."""
    wheel = _make_wheel(tmp_path / "w.whl", modules=())
    result = vr.check_console_script(wheel)
    assert result.status == vr.FAIL
    assert "model_migration_kit.cli" in " ".join([result.summary, *result.evidence])


def test_console_script_present_passes(tmp_path):
    assert vr.check_console_script(_make_wheel(tmp_path / "w.whl")).status == vr.PASS


def _make_sdist(path: Path, names) -> Path:
    root = path.parent / "stage"
    for name in names:
        target = root / "model_migration_kit-0.1.0" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(root / "model_migration_kit-0.1.0", arcname="model_migration_kit-0.1.0")
    return path


SDIST_FULL = (
    "LICENSE",
    "NOTICE",
    "README.md",
    "pyproject.toml",
    "src/model_migration_kit/runner.py",
    *[f"src/model_migration_kit/data/{n}" for n in vr.DEMO_DATA],
    "tests/test_runner.py",
)


def test_complete_sdist_passes(tmp_path):
    sdist = _make_sdist(tmp_path / "s.tar.gz", SDIST_FULL)
    assert vr.check_sdist_contents(sdist).status == vr.PASS


def test_sdist_without_notice_fails(tmp_path):
    names = tuple(n for n in SDIST_FULL if n != "NOTICE")
    result = vr.check_sdist_contents(_make_sdist(tmp_path / "s.tar.gz", names))
    assert result.status == vr.FAIL
    assert "NOTICE" in " ".join(result.evidence)


def test_sdist_without_tests_fails(tmp_path):
    names = tuple(n for n in SDIST_FULL if not n.startswith("tests/"))
    assert vr.check_sdist_contents(_make_sdist(tmp_path / "s.tar.gz", names)).status == vr.FAIL


# ----------------------------------------------------------------------------------
# README checks against a synthetic repo
# ----------------------------------------------------------------------------------


def test_readme_naming_the_wrong_distribution_fails(tmp_path):
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("```\npip install migkit\n```\n", encoding="utf-8")
    result = vr.check_readme_pip_install(tmp_path)
    assert result.status == vr.FAIL
    assert "migkit" in result.summary


def test_readme_naming_the_real_distribution_passes(tmp_path):
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text(
        "```\npip install model-migration-kit\n```\n", encoding="utf-8"
    )
    assert vr.check_readme_pip_install(tmp_path).status == vr.PASS


def test_an_unfenced_install_line_is_not_a_claim_the_check_makes(tmp_path):
    """Rule 1 at the level of the whole check, not just the extractor.

    Renamed from test_readme_may_install_a_declared_dependency, which said it was
    about the dependency allowlist and after the rule 1 rewrite never reached that
    branch: an unfenced line yields no targets at all, so the PASS came from the
    "nothing to get wrong" path. The summary is asserted for that reason -- it is the
    only thing that distinguishes the two ways this check can say PASS, and without
    it the test would keep passing whichever branch ran. The allowlist branch is
    covered fenced, by test_a_fenced_install_of_a_declared_dependency_is_allowed."""
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("pip install opik-rigor\n", encoding="utf-8")
    result = vr.check_readme_pip_install(tmp_path)
    assert result.status == vr.PASS
    assert "no `pip install" in result.summary


def test_readme_with_no_install_line_passes_and_says_so(tmp_path):
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# model-migration-kit\n\nProse only.\n", encoding="utf-8")
    result = vr.check_readme_pip_install(tmp_path)
    assert result.status == vr.PASS
    assert "no `pip install" in result.summary


def test_readme_without_commands_needs_no_cli(tmp_path):
    (tmp_path / "README.md").write_text("`migkit` answers a question.\n", encoding="utf-8")
    assert vr.check_readme_commands(tmp_path).status == vr.PASS


# ----------------------------------------------------------------------------------
# Result plumbing and exit-code policy
# ----------------------------------------------------------------------------------


def test_render_indents_every_evidence_line():
    result = vr.ok("name", "summary", ["one", "two"])
    lines = result.render().splitlines()
    assert lines[0].startswith("[PASS   ] name: summary")
    assert all(line.startswith(" " * 12) for line in lines[1:])


def test_a_skip_is_not_a_pass():
    """The whole design rule, asserted: SKIPPED and PASS are different statuses
    and the driver maps them to different exit codes."""
    assert vr.skipped("n", "why").status != vr.ok("n", "s").status
    assert vr.FLAG not in (vr.PASS, vr.SKIP)


# ==================================================================================
# The frozen README-scan contract -- docs/readme-scan-contract.md
#
# Everything below is derived from that document, or from reading README.md by
# eye. Nothing below was obtained by running the functions under test; the
# document exists precisely because the implementer and this file were written in
# parallel and could not see each other.
#
# Section headings name the rule; the "F#", "P#", "C#" tags are the row labels in
# the contract's "Hand-derived expected values" tables.
# ==================================================================================


def _fenced(*lines: str, info: str = "bash") -> str:
    """The contract's tables say "fenced: <line>"; this is what that means.

    The info string is a parameter because rule 1 accepts *any* info string --
    ` ```bash `, ` ```console `, or none at all -- and never filters on it.
    """
    return "```" + info + "\n" + "\n".join(lines) + "\n```\n"


# ----------------------------------------------------------------------------------
# Rule 1 -- only fenced code blocks are shell
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # F1: backticks.
        ("a\n```\nb\n```\nc\n", ["b\n"]),
        # F2: tildes, which CommonMark allows and this README does not use --
        # supported anyway, because the rule is about delimiters, not fashion.
        ("a\n~~~\nb\n~~~\nc\n", ["b\n"]),
        # F3: an info string on the opening fence is not part of the body.
        ("```bash\nb\n```", ["b\n"]),
    ],
)
def test_a_fenced_block_yields_its_body_without_the_fence_lines(text, expected):
    """Contract table F1-F3."""
    assert vr.fenced_code_blocks(text) == expected


def test_a_tilde_line_inside_a_backtick_block_is_body_not_a_delimiter():
    """Contract table F4: only a fence of the *same* character can close."""
    assert vr.fenced_code_blocks("```\na\n~~~\nb\n```\n") == ["a\n~~~\nb\n"]


def test_an_unterminated_block_yields_its_body_to_the_end_of_input():
    """Contract table F5. The README would be malformed, but discarding the tail
    would hide commands -- which is the failure mode this whole document is about."""
    assert vr.fenced_code_blocks("```\nx\n") == ["x\n"]


def test_text_with_no_fences_yields_no_blocks():
    """Contract table F6."""
    assert vr.fenced_code_blocks("no fences here") == []


def test_two_blocks_separated_by_prose_are_returned_separately():
    """Contract table F7: the prose between them is neither block's body."""
    assert vr.fenced_code_blocks("```\na\n```\nprose\n```\nb\n```\n") == ["a\n", "b\n"]


def test_a_shorter_inner_run_does_not_close_a_longer_fence():
    """Contract table F8: the closing run must be at least as long as the opening
    one, which is how a block quotes a fence."""
    assert vr.fenced_code_blocks("````\na\n```\nb\n````\n") == ["a\n```\nb\n"]


def test_four_space_indented_code_is_not_recognised_as_a_block():
    """Contract table F9 and rule 1's last clause: CommonMark calls this code, and
    the contract deliberately does not, because an indented *prose* block read as
    shell is the mistake being fixed."""
    assert vr.fenced_code_blocks("    indented code\n") == []


def test_an_indented_install_line_is_invisible_and_that_is_the_accepted_cost():
    """F9 carried through to the check that consumes it, using the exact input of the
    retired test_pip_install_of_the_console_script_name_is_caught.

    The retired test asserted this line yields `['migkit']`; rule 1's last clause says
    it yields nothing. The contract wins -- an indented *prose* block read as shell is
    defect (1) -- but the cost is a blind spot in the direction that fails quiet: a
    README that switched to indented code blocks would take both README checks to a
    silent PASS. Asserted here so the loss is a decision on the record rather than an
    accident, and so that anyone who later changes rule 1 changes this line with it.
    The defect shape itself -- a README naming the console script instead of the
    distribution -- survives fenced, in test_a_fenced_pip_install_names_the_console_script."""
    assert vr.readme_pip_install_targets("    pip install migkit\n") == []


# ----------------------------------------------------------------------------------
# Rule 2 -- only text in command position is a command
#
# The contract names the steps but not the helper's signature. These tests assume
# `command_segments(line) -> list[str]`, returning each segment already reduced to
# command position (prompt, comment marker and label, quote and path prefix
# stripped). If the implementation splits that differently, these four are the
# tests to re-aim -- the behaviour they describe is still rule 2 steps 1-4.
# ----------------------------------------------------------------------------------


def test_a_shell_prompt_is_not_part_of_the_command():
    """Rule 2 step 1: `$`, `>`, or a PowerShell `PS ...>` followed by whitespace."""
    assert vr.command_segments("$ pip install rich") == ["pip install rich"]
    assert vr.command_segments("PS> py -m pip install rich") == ["py -m pip install rich"]


def test_a_line_splits_into_one_segment_per_shell_separator():
    """Rule 2 step 2. Each segment gets its own shot at command position, which is
    why the second half of an `&&` chain is found at all."""
    assert vr.command_segments("migkit run --n 20 && migkit compare --baseline x") == [
        "migkit run --n 20",
        "migkit compare --baseline x",
    ]


def test_a_platform_label_in_a_comment_is_stripped_but_the_command_is_kept():
    """Rule 2 step 3: `# Windows: ...` is where a second platform's command lives,
    so a comment must not be dropped wholesale."""
    assert vr.command_segments("# Windows: python -m pip install .") == [
        "python -m pip install ."
    ]


def test_a_path_prefix_is_stripped_so_the_program_name_is_first():
    """Rule 2 step 4: any run of non-whitespace ending in `/` or `\\`."""
    assert vr.command_segments(".venv/bin/python -m pip install .") == [
        "python -m pip install ."
    ]
    assert vr.command_segments(r".venv\Scripts\migkit.exe demo") == ["migkit.exe demo"]


# ----------------------------------------------------------------------------------
# readme_pip_install_targets -- contract table P1-P10
# ----------------------------------------------------------------------------------


def test_a_prose_pip_install_mention_promises_nothing():
    """REGRESSION, contract table P1 and "Why this document exists" defect (1).

    The README sentence below is a statement that the command does *not* work.
    Read as flat text it yielded twelve package names -- `does`, `not`, `work`,
    `today.`, `Install`, `from`, `a`, `checkout:` among them -- and failed the
    release on English grammar."""
    prose = "So `pip install model-migration-kit` does not work today. Install from a checkout:\n"
    assert vr.readme_pip_install_targets(prose) == []


def test_a_fenced_pip_install_names_its_target():
    """Contract table P2: the check has to keep working, or it protects nothing."""
    assert vr.readme_pip_install_targets(_fenced("pip install model-migration-kit")) == [
        "model-migration-kit"
    ]


def test_installing_a_path_through_a_python_interpreter_names_nothing():
    """Contract table P3: `.` is not a distribution name anyone can get wrong."""
    assert vr.readme_pip_install_targets(_fenced(".venv/bin/python -m pip install .")) == []


def test_an_editable_install_of_an_extra_names_nothing():
    """Contract table P4: `-e` is a flag and `".[dev]"` starts with a dot."""
    assert vr.readme_pip_install_targets(_fenced('python -m pip install -e ".[dev]"')) == []


def test_an_install_inside_a_platform_comment_is_a_real_claim():
    """Contract table P5: the README's Windows variant lives in a trailing comment,
    and a name is just as wrong there as anywhere else."""
    assert vr.readme_pip_install_targets(
        _fenced("# Windows: python -m pip install jinja2")
    ) == ["jinja2"]


def test_a_pip_install_inside_a_string_is_not_in_command_position():
    """Contract table P6: same shape as the C2 regression, on the install side."""
    assert vr.readme_pip_install_targets(_fenced('echo "pip install nonsense"')) == []


def test_both_halves_of_an_and_chain_are_scanned():
    """Contract table P7. The order is order of appearance -- unlike
    `readme_cli_commands`, which the C8 row sorts."""
    assert vr.readme_pip_install_targets(
        _fenced("pip install rich && pip install jinja2")
    ) == ["rich", "jinja2"]


def test_a_prompt_is_stripped_and_a_version_specifier_truncated():
    """Contract table P8: the claim is about the name, not the range."""
    assert vr.readme_pip_install_targets(_fenced("$ pip install opik-rigor>=0.1")) == [
        "opik-rigor"
    ]


def test_installing_a_wheel_by_path_names_nothing():
    """Contract table P9."""
    assert vr.readme_pip_install_targets(_fenced("pip install ./dist/x.whl")) == []


def test_a_powershell_prompt_and_the_py_launcher_are_both_recognised():
    """Contract table P10: `py` is one of the four accepted interpreter spellings."""
    assert vr.readme_pip_install_targets(_fenced("PS> py -m pip install rich")) == ["rich"]


def test_the_argument_filter_still_drops_paths_wheels_and_flag_values():
    """The contract says the existing argument filter is "unchanged and still
    correct", so its coverage has to survive rule 1 -- this is the fenced version of
    the retired test_pip_install_ignores_paths_wheels_and_flag_values, line for line."""
    readme = _fenced(
        "pip install dist/model_migration_kit-0.1.0-py3-none-any.whl",
        "pip install -r requirements.txt",
        "pip install --index-url https://test.pypi.org/simple/ model-migration-kit==0.1.0",
        "pip install .",
        r"pip install C:\wheels\thing.whl",
    )
    assert vr.readme_pip_install_targets(readme) == ["model-migration-kit"]


# The three below were written after mutation-testing the block above: every line in
# it is dropped by *two* filters at once, so deleting any single filter from
# `_pip_argument_names` leaves it green. Each of these isolates one filter on an
# argument no other filter would catch, which is what makes the name of the test
# above true rather than merely lucky. The gap predates the contract rewrite -- the
# unfenced original it replaces had exactly the same five lines.


def test_a_path_that_is_not_a_wheel_is_still_not_a_distribution_name():
    """Isolates the `/` and `\\` filter, one assertion per separator. Every path
    elsewhere in this file also ends in `.whl` or starts with `.`, so the path rule is
    never the only thing standing between it and a bogus package name; a source
    checkout installed by path has neither."""
    posix = "pip install src/model_migration_kit"
    windows = r"pip install C:\checkouts\model_migration_kit"
    assert vr.readme_pip_install_targets(_fenced(posix)) == []
    assert vr.readme_pip_install_targets(_fenced(windows)) == []


def test_a_wheel_in_the_current_directory_is_still_not_a_distribution_name():
    """Isolates the `.whl`/`.tar.gz` suffix filter. Every wheel elsewhere in this file
    is written with a path in front of it, so the path rule catches it first and the
    suffix rule is never the reason -- but `python -m build` leaves artifacts in
    `dist/` and the README's own transcripts `cd` into it."""
    wheel = "pip install model_migration_kit-0.1.0-py3-none-any.whl"
    sdist = "pip install model-migration-kit-0.1.0.tar.gz"
    assert vr.readme_pip_install_targets(_fenced(wheel)) == []
    assert vr.readme_pip_install_targets(_fenced(sdist)) == []


def test_the_value_of_a_flag_is_not_a_distribution_name():
    """Isolates `_PIP_FLAGS_WITH_VALUE`. The flag values elsewhere in this file are a
    URL and a `.txt`, both of which other filters would drop anyway; `--target vendor`
    is a bare word, and reading it as a package name would fail the release on an
    argument that names a directory."""
    assert vr.readme_pip_install_targets(_fenced("pip install --target vendor rich")) == ["rich"]


def test_a_fenced_pip_install_names_the_console_script():
    """The sibling published `pip install opik-opik_rigor`; the fenced version of the
    retired test_pip_install_of_the_console_script_name_is_caught keeps that defect
    shape -- a README naming something that is not the distribution -- covered."""
    targets = vr.readme_pip_install_targets(_fenced("pip install migkit"))
    assert targets == ["migkit"]
    assert vr.normalize_project_name(targets[0]) != vr.normalize_project_name(vr.DIST_NAME)


# ----------------------------------------------------------------------------------
# readme_cli_commands -- contract table C1-C10
# ----------------------------------------------------------------------------------


def test_a_fenced_invocation_yields_its_subcommand():
    """Contract table C1."""
    assert vr.readme_cli_commands(_fenced("migkit demo")) == ["demo"]


def test_a_program_name_inside_a_quoted_string_is_not_an_invocation():
    """REGRESSION, contract table C2 and "Why this document exists" defect (2).

    This is the README's CI gate, showing what to print when the tool errors. It
    yielded the subcommand `failed`, and `migkit failed --help` exits 3 -- so the
    release check failed on a line that is a message, not a command. Restricting the
    scan to fenced blocks does *not* fix this one: it is inside a fence."""
    assert vr.readme_cli_commands(_fenced('*) echo "migkit failed"     ; exit 1 ;;')) == []


def test_a_windows_path_prefix_is_stripped_before_the_program_name_is_matched():
    """Contract table C3: the quickstart types the interpreter out of `.venv`."""
    assert vr.readme_cli_commands(_fenced(r".venv\Scripts\migkit.exe demo")) == ["demo"]


def test_a_call_operator_with_a_quoted_path_still_matches():
    """Contract table C4: `&` splits, the opening quote and the path prefix strip."""
    assert vr.readme_cli_commands(_fenced(r'& "$tmp\Scripts\migkit.exe" demo')) == ["demo"]


def test_the_programs_own_log_prefix_is_not_an_invocation():
    """Contract table C5. `migkit:` prefixes every stderr line in the README's
    pasted output, so reading it as a command would invent a subcommand per line;
    a colon is neither whitespace nor a closing quote."""
    assert vr.readme_cli_commands(_fenced("migkit: sampling fake-baseline-v1")) == []


def test_a_prompt_is_stripped_before_the_program_name():
    """Contract table C6."""
    assert vr.readme_cli_commands(_fenced(r"$ migkit report .\does-not-exist.jsonl")) == [
        "report"
    ]


def test_an_inline_code_span_in_prose_is_a_mention_not_an_invocation():
    """Contract table C7 and rule 1's closing paragraph."""
    assert vr.readme_cli_commands("`migkit demo` runs the whole flow\n") == []


def test_a_forgotten_fence_leaves_an_invocation_unread():
    """Rule 1 for `readme_cli_commands`, isolated -- and it needs isolating, because
    every other prose case in this file is *also* stopped by rule 2 and so cannot tell
    the two rules apart. C7's inline span begins with a backtick and the README's own
    "`migkit` answers ..." begins with one too, which is not command position however
    the text is scanned. A line that lost its fence is the case where rule 1 is the
    only thing standing between an example and the scanner, and it is the likeliest
    one to occur: an edit that drops three backticks looks like nothing in review."""
    assert vr.readme_cli_commands("Run this:\n\nmigkit demo\n\nand read the report.\n") == []


def test_subcommands_are_deduplicated_and_sorted():
    """Contract table C8."""
    assert vr.readme_cli_commands(
        _fenced("migkit run --n 20 && migkit compare --baseline x")
    ) == ["compare", "run"]


def test_every_line_of_a_block_is_read_not_just_the_first():
    """The one thing the retired test_readme_commands_finds_every_invocation_shape
    covered that no contract row does: its block held *two* invocations, on two lines,
    in two different shapes. C1, C3 and C4 each show a single line, so a scanner that
    stopped at the first match in a block would satisfy all three and still miss half
    of the README's quickstart. Without this, the only test that would notice is the
    characterisation one against the real README, and that is hostage to how the
    README happens to be laid out on the day -- move one command into a block of its
    own and the coverage evaporates silently.

    The block is the retired test's, minus the inline code span C7 now excludes."""
    block = _fenced(
        "migkit compare --baseline a.jsonl --candidate b.jsonl",
        r'& "$tmp\Scripts\migkit.exe" report --out r.html',
        info="powershell",
    )
    assert vr.readme_cli_commands(block) == ["compare", "report"]


def test_a_filename_containing_the_program_name_is_not_an_invocation():
    """Contract table C9: the demo prints this path as its last line."""
    assert vr.readme_cli_commands(_fenced(r"...\migkit-demo-report.html")) == []


def test_a_bare_program_name_alone_on_a_fenced_line_is_not_an_invocation():
    """Contract table C10: rule 2 requires a following word."""
    assert vr.readme_cli_commands(_fenced("migkit")) == []


def test_a_fenced_command_still_skips_when_the_cli_is_not_this_tree(tmp_path):
    """Fenced version of the retired test_readme_commands_skip_when_the_cli_is_not_this_tree:
    a README that shows a command, against a tree that is not where the CLI lives,
    must not be reported as verified.

    Which of the two SKIP branches this lands in depends on the machine -- "not
    importable" when the distribution is not installed, "different tree" when it is
    installed from another checkout, which is the normal state in a worktree. Both are
    the same verdict for the same reason and either is correct here; the branch itself
    is pinned, machine-independently, by the test below."""
    (tmp_path / "README.md").write_text("```bash\nmigkit demo\n```\n", encoding="utf-8")
    result = vr.check_readme_commands(tmp_path)
    assert result.status == vr.SKIP
    assert "demo" in " ".join(result.evidence)


def test_a_cli_importable_from_another_checkout_is_a_skip_not_a_pass(tmp_path, monkeypatch):
    """The guard the retired test existed for, isolated so it cannot pass by accident.

    Verifying README commands against an importable CLI from a *different* checkout
    would be a claim about someone else's code, and PASS is the one answer that would
    be a lie -- the README under verification could show a command that only the other
    tree has. The probe is stubbed because the branch is otherwise reachable only when
    the distribution happens to be installed from elsewhere."""
    (tmp_path / "README.md").write_text("```bash\nmigkit demo\n```\n", encoding="utf-8")
    elsewhere = tmp_path.parent / "some-other-checkout" / "model_migration_kit" / "cli.py"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    elsewhere.write_text("def main(argv=None):\n    return 0\n", encoding="utf-8")

    def fake_run(cmd, cwd=None):
        # Success for *every* subprocess, including the per-command `--help` probes:
        # if the guard stops firing, the check walks on and reports PASS, and this
        # test goes red rather than agreeing with it.
        return types.SimpleNamespace(returncode=0, stdout=f"{elsewhere}\n", stderr="")

    monkeypatch.setattr(vr, "run", fake_run)
    result = vr.check_readme_commands(tmp_path)
    assert result.status == vr.SKIP
    assert "different tree" in result.summary
    assert "demo" in " ".join(result.evidence)


def test_a_fenced_install_of_a_declared_dependency_is_allowed(tmp_path):
    """The dependency-allowlist branch, which an unfenced install line no longer
    reaches -- see test_an_unfenced_install_line_is_not_a_claim_the_check_makes, the
    renamed test that used to stand for this one. A declared dependency is a name the
    README is allowed to tell a user to install; the evidence is asserted because it
    is what separates "judged and allowed" from "never looked at"."""
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("```\npip install opik-rigor\n```\n", encoding="utf-8")
    result = vr.check_readme_pip_install(tmp_path)
    assert result.status == vr.PASS
    assert "pip install opik-rigor" in " ".join(result.evidence)


# ----------------------------------------------------------------------------------
# Characterisation -- the real README.md, as it stands
#
# The contract's "Against the real README" section. These are the two values the
# whole change exists to produce, so they are asserted against the file itself
# rather than a fixture: a fixture cannot go stale, and that is the problem.
# ----------------------------------------------------------------------------------

_README = Path(__file__).resolve().parent.parent / "README.md"


def test_the_real_readme_tells_nobody_to_pip_install_anything():
    """Contract, "Against the real README": expected `[]`. Read by eye, every fenced
    `pip install` in README.md targets `.`, `-e ".[dev]"`, or the placeholder
    `<checkout>`; the only bare-name mention is the prose sentence of P1."""
    assert vr.readme_pip_install_targets(_README.read_text(encoding="utf-8")) == []


def test_the_real_readme_types_exactly_four_subcommands():
    """Contract, "Against the real README". Read by eye: `demo` (quickstart and the
    install section), `compare` (the CI gate and the real-models section), `run`
    (real models, wire check, dupes) and `report` (the exit-3 transcript). Every
    other `migkit` in the file is a log prefix, a filename, or prose."""
    assert vr.readme_cli_commands(_README.read_text(encoding="utf-8")) == [
        "compare",
        "demo",
        "report",
        "run",
    ]


# ----------------------------------------------------------------------------------
# Adversarial cases the contract does not tabulate
#
# Each of these is a behaviour the contract *implies*; where it is genuinely silent
# the comment says so, and the accompanying report lists them for a human.
# ----------------------------------------------------------------------------------


def test_a_backtick_fence_inside_a_tilde_block_is_body_not_a_delimiter():
    """F4 in the other direction. The rule is symmetric ("a fence of the *other*
    character inside an open block is body text"), so a README that quotes a
    markdown example inside a tilde block keeps it whole."""
    assert vr.fenced_code_blocks("~~~\na\n```\nb\n~~~\n") == ["a\n```\nb\n"]


def test_a_closing_fence_may_carry_trailing_whitespace():
    """Rule 1 speaks of the line's *stripped* form, so a trailing space -- which no
    editor shows and every reviewer misses -- must still close the block. If it did
    not, the rest of the README would be swallowed as body by the F5 clause."""
    assert vr.fenced_code_blocks("```\nb\n```   \n") == ["b\n"]


def test_a_fence_line_carrying_an_info_string_does_not_close_a_block():
    """Rule 1: the closing line "carries no info string". So ` ```bash ` inside an
    open block opens nothing and closes nothing -- it is body."""
    assert vr.fenced_code_blocks("```\nb\n```bash\nc\n```\n") == ["b\n```bash\nc\n"]


def test_a_longer_closing_run_still_closes_the_block():
    """Rule 1 requires the closing run to be "at least as long" as the opening one,
    not equal to it. F8 covers the shorter case; this is the other side of it."""
    assert vr.fenced_code_blocks("```\na\n````\nb\n") == ["a\n"]


def test_an_empty_fenced_block_yields_an_empty_body():
    """AMBIGUOUS in the contract: it says the function "returns the body of each
    fenced block" and never says empty bodies are dropped, so an empty body is "".
    Nothing downstream can tell the difference; recorded here so the choice is
    visible rather than accidental."""
    assert vr.fenced_code_blocks("```\n```\n") == [""]


def test_an_indented_fence_opens_and_closes_like_any_other():
    """Rule 1 tests the *stripped* form of the fence line, so a block indented under
    a list item is still a block. AMBIGUOUS: the contract does not say whether the
    body is dedented, so this asserts only that the block is found and its command
    read -- both true either way."""
    text = "  ```bash\n  migkit demo\n  ```\n"
    assert len(vr.fenced_code_blocks(text)) == 1
    assert vr.readme_cli_commands(text) == ["demo"]


@pytest.mark.parametrize("info", ["", "bash", "console", "text", "powershell", "sh"])
def test_the_info_string_never_decides_whether_a_block_is_scanned(info):
    """Rule 1 makes the info string optional and never filters on it. This matters
    in both directions for this README: its long ` ``` ` output blocks carry no info
    string at all and must still be scanned -- that is where the C5 log-prefix lines
    live -- while a `text` block is not thereby exempt."""
    assert vr.readme_cli_commands(_fenced("migkit demo", info=info)) == ["demo"]


def test_crlf_line_endings_change_nothing():
    """Not in the contract, and this repo is on Windows with a CRLF bug already in
    its history. A `\\r` left on the end of a line would make the closing fence
    unrecognisable (F5 would then swallow the file) and would strand a `\\r` on the
    last token of every command."""
    text = "```bash\r\nmigkit demo\r\npip install rich\r\n```\r\n"
    assert len(vr.fenced_code_blocks(text)) == 1
    assert vr.readme_cli_commands(text) == ["demo"]
    assert vr.readme_pip_install_targets(text) == ["rich"]


def test_a_second_command_after_a_separator_is_reached_even_when_the_first_is_noise():
    """P6 and P7 composed: the quoted-string half must not poison the real half.
    A whole-line scan sees one `pip install` and stops at the wrong one."""
    assert vr.readme_pip_install_targets(
        _fenced('echo "pip install nonsense" && pip install rich')
    ) == ["rich"]


def test_a_subcommand_followed_by_a_flag_is_still_a_subcommand():
    """Rule 2 matches `[a-z][a-z0-9-]*` after the program name and says nothing
    about what follows it, so the argument list is irrelevant."""
    assert vr.readme_cli_commands(_fenced("migkit demo --help")) == ["demo"]


def test_a_bare_flag_is_not_a_subcommand():
    """`--help` does not match `[a-z][a-z0-9-]*` -- it starts with a hyphen -- so
    `migkit --help` is the C10 case with extra characters."""
    assert vr.readme_cli_commands(_fenced("migkit --help")) == []


def test_the_readme_install_line_with_a_trailing_platform_comment_names_nothing():
    """The line this contract has to get right, copied from README.md's Install
    section. AMBIGUOUS: rule 2 step 2 does not list `#` among the separators, yet
    step 3 and the "Against the real README" expectation of `[]` only both hold if a
    mid-line `#` begins a new segment. Read literally without that, this line yields
    `['#', 'Windows:', 'pip', 'install']` -- which is exactly the tail of the twelve
    bogus names the contract records as defect (1). `[]` is the frozen value, so
    `[]` is what is asserted."""
    line = r'.venv/bin/python -m pip install .      # Windows: .venv\Scripts\python.exe -m pip install .'  # noqa: E501
    assert vr.readme_pip_install_targets(_fenced(line)) == []


def test_a_placeholder_argument_truncates_away_to_nothing():
    """From README.md's quickstart. `<checkout>` truncates at `<` to the empty
    string, and the argument filter drops empty names -- which is the *only* reason
    the real-README expectation of `[]` holds for that block."""
    assert vr.readme_pip_install_targets(
        _fenced(r".venv\Scripts\python.exe -m pip install <checkout>")
    ) == []


def test_a_quoted_command_alone_on_a_line_is_still_in_command_position():
    """A wart, asserted rather than left to chance: rule 2 step 4 strips an opening
    quote unconditionally, so a bare quoted string is indistinguishable from
    `& "path\\to\\thing" arg`. Unlike P6 there is no `echo` in front to disqualify
    it. If this is judged wrong, the contract is what has to change."""
    assert vr.readme_pip_install_targets(_fenced('"pip install nonsense"')) == ["nonsense"]


# ----------------------------------------------------------------------------------
# Reading another tool's human-facing output
# ----------------------------------------------------------------------------------


def test_a_colourised_twine_pass_is_still_counted():
    r"""The exact bytes GitHub Actions produced, which the check read as zero passes.

    `check_twine` counts lines ending in `PASSED`. `twine check` prints a bare
    `PASSED` on a Windows dev shell and a colour-wrapped one on CI, so the line
    ends in `\x1b[0m` there and the count came out 0 -- a FAIL on a build that was
    fine. It passed on every machine it was written on and failed the first time it
    ran where it counts, blocking the 0.1.0 release. Note the wrapped first line:
    twine breaks the path across lines too, so the word is not on the line that
    starts with `Checking`.
    """
    captured = (
        "Checking \n"
        "/home/runner/work/model-migration-kit/model-migration-kit/dist/"
        "model_migration_kit-0.1.0-py3-none-any.whl: \x1b[32mPASSED\x1b[0m\n"
        "Checking /home/runner/work/model-migration-kit/model-migration-kit/dist/"
        "model_migration_kit-0.1.0.tar.gz: \x1b[32mPASSED\x1b[0m\n"
    )
    lines = vr.plain_lines(captured)
    assert sum(1 for line in lines if line.strip().endswith("PASSED")) == 2
    assert "\x1b" not in "".join(lines)


def test_a_plain_twine_pass_is_unchanged():
    """The uncoloured form still has to count, or the strip fixed CI and broke every
    developer machine instead."""
    captured = "Checking dist/model_migration_kit-0.1.0-py3-none-any.whl: PASSED\n"
    assert vr.plain_lines(captured) == [
        "Checking dist/model_migration_kit-0.1.0-py3-none-any.whl: PASSED"
    ]


def test_a_failure_is_not_turned_into_a_pass_by_stripping():
    """`FAILED` must survive the same treatment. A strip that ate the distinction
    would make this check report success on a broken artifact, which is worse than
    the defect it was written to fix."""
    captured = "Checking dist/x.whl: \x1b[31mFAILED\x1b[0m\n  `long_description` is missing\n"
    lines = vr.plain_lines(captured)
    assert sum(1 for line in lines if line.strip().endswith("PASSED")) == 0
    assert lines[0].endswith("FAILED")


# ----------------------------------------------------------------------------------
# PEP 561's marker, in the wheel rather than in the tree
# ----------------------------------------------------------------------------------


def _pt_wheel(tmp_path: Path, members: dict) -> Path:
    path = tmp_path / "model_migration_kit-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for member, payload in members.items():
            zf.writestr(member, payload)
    return path


def _pt_repo(tmp_path: Path) -> Path:
    """A source tree that *does* carry the marker, so the wheel is the only variable."""
    repo = tmp_path / "repo"
    package = repo / "src" / "model_migration_kit"
    package.mkdir(parents=True)
    (package / "py.typed").write_bytes(b"")
    return repo


def _pt_record(members: list) -> bytes:
    return "\n".join(f"{m},," for m in members).encode()


def test_py_typed_in_the_tree_does_not_make_it_present_in_the_wheel(tmp_path):
    """The whole reason this check reads the zip. The tree below carries `py.typed`
    and the wheel does not, which is what an assertion written as
    `Path(model_migration_kit.__file__).parent / "py.typed"` cannot see: in a
    checkout that path *is* the source tree, so it passes while installed copies
    get nothing."""
    repo = _pt_repo(tmp_path)
    wheel = _pt_wheel(
        tmp_path,
        {
            "model_migration_kit/__init__.py": b"",
            "model_migration_kit-0.1.0.dist-info/RECORD": _pt_record(
                ["model_migration_kit/__init__.py"]
            ),
        },
    )
    result = vr.check_wheel_py_typed(wheel, repo)
    assert result.status == vr.FAIL
    assert "not in the wheel" in result.summary
    assert any("exists=True" in line for line in result.evidence)


def test_py_typed_present_in_the_wheel_passes_even_though_it_is_empty(tmp_path):
    """PEP 561's marker is meant to be empty; an emptiness check would be wrong."""
    repo = _pt_repo(tmp_path)
    members = ["model_migration_kit/__init__.py", "model_migration_kit/py.typed"]
    wheel = _pt_wheel(
        tmp_path,
        {
            "model_migration_kit/__init__.py": b"",
            "model_migration_kit/py.typed": b"",
            "model_migration_kit-0.1.0.dist-info/RECORD": _pt_record(members),
        },
    )
    assert vr.check_wheel_py_typed(wheel, repo).status == vr.PASS


def test_py_typed_in_the_zip_but_not_in_record_is_a_failure(tmp_path):
    """RECORD is what an installer copies from. A file in the zip and absent from
    RECORD is in the artifact and not in the install."""
    repo = _pt_repo(tmp_path)
    wheel = _pt_wheel(
        tmp_path,
        {
            "model_migration_kit/__init__.py": b"",
            "model_migration_kit/py.typed": b"",
            "model_migration_kit-0.1.0.dist-info/RECORD": _pt_record(
                ["model_migration_kit/__init__.py"]
            ),
        },
    )
    assert vr.check_wheel_py_typed(wheel, repo).status == vr.FAIL
