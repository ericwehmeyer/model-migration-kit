"""Tests for scripts/verify_release.py.

The release script is the thing that decides whether a release is allowed to
happen, so its own logic needs checking by something other than itself. Every
expectation below is written from the contracts (`docs/session-4-release-contract.md`
sections 1 and 5, `docs/session-3-contract.md` section 5.1), not from running the code.

The wheel-shaped tests build synthetic zip files rather than invoking hatchling:
the interesting cases are the *broken* ones -- a wheel with no demo data, a wheel
carrying both an SPDX expression and a deprecated classifier -- and those cannot
be produced from this repository's pyproject.toml on purpose.
"""

from __future__ import annotations

import importlib.util
import sys
import tarfile
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


def test_pip_install_targets_are_extracted_from_prose_and_fences():
    readme = """
# model-migration-kit

```console
$ pip install model-migration-kit
```

Or from a checkout: `python -m pip install -e ".[dev]"`.
"""
    assert vr.readme_pip_install_targets(readme) == ["model-migration-kit"]


def test_pip_install_of_the_console_script_name_is_caught():
    """The sibling published `pip install opik-opik_rigor`. The shape of that
    defect is a README naming something that is not the distribution."""
    targets = vr.readme_pip_install_targets("    pip install migkit\n")
    assert targets == ["migkit"]
    assert vr.normalize_project_name(targets[0]) != vr.normalize_project_name(vr.DIST_NAME)


def test_pip_install_ignores_paths_wheels_and_flag_values():
    readme = "\n".join(
        [
            "pip install dist/model_migration_kit-0.1.0-py3-none-any.whl",
            "pip install -r requirements.txt",
            "pip install --index-url https://test.pypi.org/simple/ model-migration-kit==0.1.0",
            "pip install .",
            r"pip install C:\wheels\thing.whl",
        ]
    )
    assert vr.readme_pip_install_targets(readme) == ["model-migration-kit"]


def test_readme_commands_finds_every_invocation_shape():
    readme = """
Run `migkit demo` to see it work.

```powershell
migkit compare --baseline a.jsonl --candidate b.jsonl
& "$tmp\\Scripts\\migkit.exe" report --out r.html
```
"""
    assert vr.readme_cli_commands(readme) == ["compare", "demo", "report"]


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


def test_readme_may_install_a_declared_dependency(tmp_path):
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("pip install opik-rigor\n", encoding="utf-8")
    assert vr.check_readme_pip_install(tmp_path).status == vr.PASS


def test_readme_with_no_install_line_passes_and_says_so(tmp_path):
    _write_pyproject(tmp_path)
    (tmp_path / "README.md").write_text("# model-migration-kit\n\nProse only.\n", encoding="utf-8")
    result = vr.check_readme_pip_install(tmp_path)
    assert result.status == vr.PASS
    assert "no `pip install" in result.summary


def test_readme_commands_skip_when_the_cli_is_not_this_tree(tmp_path):
    """Verifying README commands against an importable CLI from a *different*
    checkout would be a claim about someone else's code."""
    (tmp_path / "README.md").write_text("Run `migkit demo` now.\n", encoding="utf-8")
    result = vr.check_readme_commands(tmp_path)
    assert result.status == vr.SKIP
    assert "demo" in " ".join(result.evidence)


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
