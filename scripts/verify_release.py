#!/usr/bin/env python
"""Execute the mechanically checkable parts of the Session 4 release checklist.

    .\\.venv\\Scripts\\python.exe scripts\\verify_release.py

`docs/session-4-release-contract.md` §5 says "every row is a command whose output
is the evidence". This is that command. It builds the distribution and then reads
the *built artifact* rather than the source tree, because every landmine the
contract records was invisible in the source and obvious in the wheel.

Design rules, in order of importance:

1. **A skipped check is never a passing check.** Anything that cannot run yet --
   because `model_migration_kit.cli` does not exist, because `twine` is not installed --
   prints SKIPPED with the reason and pushes the process exit code to 2. A
   verification script that quietly skips manufactures confidence, which is worse
   than no script at all.
2. **Every check prints what it checked**, not just a verdict. The evidence lines
   under each result are the thing you paste into `PROGRESS.md`.
3. **The wheel is the subject.** Local files, editable installs and the repo's own
   `sys.path` all lie in the same direction: they make a wheel that is missing the
   demo data look fine. The resource check therefore runs in a subprocess with
   `-S`, with only the *extracted wheel* on `sys.path`, and asserts that
   `model_migration_kit.__path__` contains nothing else.

Exit codes:

    0   every check ran and passed
    1   at least one check FAILED, or raised a contract FLAG
    2   nothing failed, but at least one check could not run (SKIPPED)

Only stdlib is used, plus `build` and `twine`, which Phase 1 of the contract
already installs into the release venv. Both are optional here and their absence
is reported, never silently tolerated.
"""

from __future__ import annotations

import argparse
import email.parser
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]

# --------------------------------------------------------------------------------------
# What this project is. Changing any of these is a release-contract change, not a tweak.
# --------------------------------------------------------------------------------------

DIST_NAME = "model-migration-kit"
IMPORT_NAME = "model_migration_kit"
CONSOLE_SCRIPT = "migkit"
ENTRY_POINT_MODULE = "model_migration_kit.cli"

# Session 3 contract §5.1. These must be *inside the wheel*, not merely on disk.
DEMO_DATA = ("demo_goldenset.jsonl", "demo_rubric.md", "demo.toml")
DATA_SUBDIR = "data"
PY_TYPED = "py.typed"

# Phase 1 exit criterion 3: Apache-2.0 §4(d) makes NOTICE load-bearing.
LICENSE_FILES = ("LICENSE", "NOTICE")

CONTRACT_PATH = Path("docs") / "session-4-release-contract.md"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIPPED"
FLAG = "FLAG"

# --------------------------------------------------------------------------------------
# Result plumbing
# --------------------------------------------------------------------------------------


@dataclass
class Result:
    """One checklist row: a verdict, and the evidence that produced it."""

    name: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        head = f"[{self.status:7}] {self.name}: {self.summary}"
        body = "".join(f"\n            {line}" for line in self.evidence)
        return head + body


def ok(name: str, summary: str, evidence: list[str] | None = None) -> Result:
    return Result(name, PASS, summary, evidence or [])


def bad(name: str, summary: str, evidence: list[str] | None = None) -> Result:
    return Result(name, FAIL, summary, evidence or [])


def skipped(name: str, reason: str, evidence: list[str] | None = None) -> Result:
    return Result(name, SKIP, reason, evidence or [])


def flagged(name: str, summary: str, evidence: list[str] | None = None) -> Result:
    return Result(name, FLAG, summary, evidence or [])


# --------------------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_release_checks.py)
# --------------------------------------------------------------------------------------


def normalize_project_name(name: str) -> str:
    """PEP 503 normalisation. `model-migration-kit`, `model_migration_kit` and `Migration.Kit`
    are the same project, which is exactly the point Phase 0 leans on."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def requirement_name(requirement: str) -> str:
    """Extract the distribution name from a PEP 508 requirement string.

    >>> requirement_name("opik-rigor<0.2,>=0.1.0")
    'opik-rigor'
    >>> requirement_name("tomli>=2.0; python_version < '3.11'")
    'tomli'
    """
    head = requirement.split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", head)
    return match.group(1) if match else head


def requirement_marker(requirement: str) -> str:
    """The environment marker of a PEP 508 requirement, or '' when there is none."""
    if ";" not in requirement:
        return ""
    return requirement.split(";", 1)[1].strip()


def markers_equivalent(left: str, right: str) -> bool:
    """Compare two environment markers ignoring quote style and whitespace.

    `python_version < "3.11"` and `python_version < '3.11'` are the same marker;
    hatchling normalises quotes on the way into METADATA and pyproject.toml does
    not, so a byte comparison would report a difference that does not exist.
    """

    def canon(text: str) -> str:
        return re.sub(r"\s+", "", text).replace('"', "'")

    return canon(left) == canon(right)


def split_requires_dist(values: list[str]) -> tuple[list[str], list[str]]:
    """Partition Requires-Dist into (runtime, extras-only).

    A requirement guarded by `extra == '...'` is installed only for that extra and
    is not part of the runtime dependency claim the contract makes.
    """
    runtime: list[str] = []
    extras: list[str] = []
    for value in values:
        if re.search(r"\bextra\s*==", requirement_marker(value)):
            extras.append(value)
        else:
            runtime.append(value)
    return runtime, extras


LICENSE_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Apache-2.0", ("apache license", "version 2.0, january 2004")),
    ("MIT", ("mit license", "permission is hereby granted, free of charge")),
    ("BSD-3-Clause", ("redistribution and use in source and binary forms", "neither the name")),
    ("GPL-3.0-only", ("gnu general public license", "version 3, 29 june 2007")),
)


def spdx_from_license_text(text: str) -> str | None:
    """Identify the licence from its shipped text, or None if unrecognised.

    Deliberately conservative: an unrecognised licence yields None so the caller
    reports SKIPPED rather than inventing agreement between text and identifier.
    """
    lowered = " ".join(text.lower().split())
    for spdx, needles in LICENSE_SIGNATURES:
        if all(needle in lowered for needle in needles):
            return spdx
    return None


# --------------------------------------------------------------------------------------
# Reading the README. The rules below are `docs/readme-scan-contract.md`, frozen
# 2026-08-13; that document is the specification and this is only its implementation.
# --------------------------------------------------------------------------------------

_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")


def fenced_code_blocks(text: str) -> list[str]:
    """The body of every fenced code block in `text`, the fence lines excluded.

    Contract rule 1. Prose is not shell. Scanning the whole README as flat text read
    the sentence "So `pip install model-migration-kit` does not work today. Install
    from a checkout:" as an install line and reported the package names `does`,
    `not`, `work`, `today.`. An inline code span is a mention, and that particular
    mention is a claim that the command does *not* work yet.

    A fence closes only on a run of the same character, at least as long as the one
    that opened it, carrying no info string -- so a tilde fence inside a backtick
    block is body text. An unterminated block yields its body to the end of the
    input: the README would be malformed, but silently dropping the tail would hide
    commands instead of reporting them. Four-space indented blocks are deliberately
    not recognised, because reading an indented paragraph as shell is the very
    mistake this rule exists to stop.
    """
    blocks: list[str] = []
    body: list[str] | None = None
    char, length = "", 0
    for line in text.splitlines():
        fence = _FENCE.match(line.strip())
        if body is None:
            if fence:
                char, length = fence.group(1)[0], len(fence.group(1))
                body = []
            continue
        closes = fence and fence.group(1)[0] == char and len(fence.group(1)) >= length
        if closes and not fence.group(2).strip():
            blocks.append("".join(f"{entry}\n" for entry in body))
            body = None
            continue
        body.append(line)
    if body is not None:
        blocks.append("".join(f"{entry}\n" for entry in body))
    return blocks


# `PS C:\...>` as well as `$` and `>`, because the README's transcripts were pasted
# from both shells.
_PROMPT = re.compile(r"^\s*(?:PS[^>]*>|[$>])\s")

# `&&`, `||`, `|`, `;`, `&` end a command; `#` begins a comment, which is where a
# second platform's command lives, so it begins a segment rather than ending the line.
_SEPARATOR = re.compile(r"&&|\|\||[|;&]|(?=#)")

# `Windows:` in `# Windows: python -m pip install .` -- a label naming the platform,
# not the program being run. Capped at 30 characters so a comment written as a
# sentence cannot swallow its own verb.
_COMMENT_LABEL = re.compile(r"^\s*[^\s:][^:]{0,28}:\s")

_PATH_PREFIX = re.compile(r"^\S*[/\\]")


def command_segments(line: str) -> list[str]:
    """Every point on `line` where a command could begin, each cut back to that point.

    Contract rule 2. Restricting the scan to code blocks is not enough on its own:
    the CI gating example contains

        *) echo "migkit failed"                    ; exit 1 ;;

    and a match anywhere in the line turned that string into the subcommand `failed`,
    which does not exist. Only the head of a segment is a command; `migkit` inside an
    argument to `echo` is data.

    Separators inside quotes are not tracked. That is a deliberate limit rather than
    an oversight: a false split can only lose a match, never invent one, and the
    alternative is a shell parser.
    """
    segments: list[str] = []
    for raw in _SEPARATOR.split(_PROMPT.sub("", line, count=1)):
        segment = _COMMENT_LABEL.sub("", raw[1:], count=1) if raw.startswith("#") else raw
        # Both ends: leading whitespace hides the head of the command, and trailing
        # whitespace is only an artifact of where the separator happened to fall.
        segment = segment.strip()
        if segment[:1] in ('"', "'"):
            segment = segment[1:]
        segment = _PATH_PREFIX.sub("", segment, count=1)
        if segment:
            segments.append(segment)
    return segments


_PIP_FLAGS_WITH_VALUE = {
    "-r",
    "-c",
    "-i",
    "--index-url",
    "--extra-index-url",
    "--requirement",
    "--constraint",
    "--find-links",
    "--target",
    "--python-version",
}


# `[<python> -m ] pip[3] install <args...>`, anchored, so it only fires at the head of
# a segment. `python.exe` and `py` are here because the README's Windows lines use them.
_PIP_INSTALL = re.compile(r"^(?:(?:python3?|py)(?:\.exe)?\s+-m\s+)?pip3?\s+install\s+(.*)$")


def _pip_argument_names(tail: str) -> list[str]:
    """The distribution names among the arguments of one `pip install`.

    Local paths, wheels, `-e .` and flag values are not names a user can get wrong,
    so they are dropped. What survives is a claim about what this project is called.
    """
    names: list[str] = []
    tokens = tail.replace("`", " ").split()
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token in _PIP_FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        candidate = token.strip("\"'")
        if not candidate or candidate.startswith("."):
            continue
        if "/" in candidate or "\\" in candidate:
            continue
        if candidate.endswith((".whl", ".tar.gz", ".zip", ".txt")):
            continue
        name = re.split(r"[<>=!~\[]", candidate, maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


def readme_pip_install_targets(text: str) -> list[str]:
    """Every distribution name a `pip install` line in the README would fetch.

    Only fenced code blocks are read, and only at command position, so the README's
    own sentence about `pip install model-migration-kit` *not* working stays prose --
    the exact thing the sibling got wrong when a rename turned `opik-rigor` into
    `opik-opik_rigor` in the published install hint is still caught, because a real
    install line lives in a code block.
    """
    targets: list[str] = []
    for block in fenced_code_blocks(text):
        for line in block.splitlines():
            for segment in command_segments(line):
                match = _PIP_INSTALL.match(segment)
                if match:
                    targets.extend(_pip_argument_names(match.group(1)))
    return targets


def readme_cli_commands(text: str, prog: str = CONSOLE_SCRIPT) -> list[str]:
    """Subcommands the README shows being typed, e.g. `migkit demo` -> `demo`.

    Only a segment that *begins* with the program name counts, in its bare, `.exe`,
    path-prefixed and quoted-path forms (`& "$tmp\\Scripts\\migkit.exe" demo`). A
    match elsewhere in a line is not an invocation: `echo "migkit failed"` in the
    README's CI gating example produced the subcommand `failed`, and `migkit failed
    --help` exits 3.

    A bare `migkit` with no following word is prose. `migkit:`, the program's own log
    prefix, appears throughout the README's pasted output and is not an invocation
    either, because a colon is neither whitespace nor a closing quote.
    """
    pattern = re.compile(
        rf"^{re.escape(prog)}(?:\.exe)?[\"']?\s+([a-z][a-z0-9-]*)",
        re.IGNORECASE,
    )
    found: list[str] = []
    for block in fenced_code_blocks(text):
        for line in block.splitlines():
            for segment in command_segments(line):
                match = pattern.match(segment)
                if match is None:
                    continue
                command = match.group(1).lower()
                if command not in found:
                    found.append(command)
    return sorted(found)


def contract_declared_requirements(contract_text: str) -> list[str] | None:
    """The dependency list the frozen contract asserts, or None if not found.

    Parses Phase 1 exit criterion 7 -- "`Requires-Dist` lists `a`, `b`, `c` and
    nothing else" -- so that when the criterion is amended, this script follows the
    document instead of a copy of it.
    """
    match = re.search(
        r"`Requires-Dist`\s+lists\s+(.*?)\s+and\s+nothing\s+else",
        contract_text,
        re.DOTALL,
    )
    if not match:
        return None
    return [requirement_name(item) for item in re.findall(r"`([^`]+)`", match.group(1))]


def version_is_dev(version: str) -> bool:
    """True for a development version. `.dev` must not survive into a release."""
    return bool(re.search(r"\.?dev\d*", version))


def parse_metadata(raw: str) -> email.message.Message:
    return email.parser.Parser().parsestr(raw)


def wheel_version_from_filename(filename: str) -> str:
    """`model_migration_kit-0.1.0-py3-none-any.whl` -> `0.1.0` (PEP 427 field order)."""
    return Path(filename).name.split("-")[1]


# --------------------------------------------------------------------------------------
# Small process/archive utilities
# --------------------------------------------------------------------------------------


def run(
    cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def plain_lines(text: str) -> list[str]:
    """Non-empty lines with any terminal colouring removed."""
    return [_ANSI_RE.sub("", line) for line in text.splitlines() if line.strip()]


def tail(text: str, limit: int = 12) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def dist_info_member(zf: zipfile.ZipFile, suffix: str) -> str | None:
    for name in zf.namelist():
        if ".dist-info/" in name and name.endswith(suffix):
            return name
    return None


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------


def check_build(
    repo: Path, dist_dir: Path, do_build: bool
) -> tuple[Result, Path | None, Path | None]:
    """Build sdist + wheel, or adopt what is already in dist/ under --no-build."""
    name = "build"
    if do_build:
        if not _module_available("build"):
            return (
                skipped(
                    name,
                    "`python -m build` is unavailable in this interpreter",
                    [
                        f"interpreter: {sys.executable}",
                        "fix: .\\.venv\\Scripts\\python.exe -m pip install --upgrade build twine",
                        "every wheel-derived check below is skipped as a consequence",
                    ],
                ),
                None,
                None,
            )
        dist_dir.mkdir(parents=True, exist_ok=True)
        stale = sorted(glob.glob(str(dist_dir / "*.whl")) + glob.glob(str(dist_dir / "*.tar.gz")))
        for path in stale:
            os.remove(path)
        proc = run([sys.executable, "-m", "build", "--outdir", str(dist_dir)], cwd=repo)
        if proc.returncode != 0:
            return (
                bad(
                    name,
                    f"`python -m build` exited {proc.returncode}",
                    [
                        f"removed {len(stale)} stale artifact(s) first",
                        *tail(proc.stdout + proc.stderr),
                    ],
                ),
                None,
                None,
            )

    wheels = sorted(glob.glob(str(dist_dir / "*.whl")))
    sdists = sorted(glob.glob(str(dist_dir / "*.tar.gz")))
    if not wheels or not sdists:
        return (
            bad(
                name,
                "dist/ does not hold both a wheel and an sdist",
                [f"dist dir: {dist_dir}", f"wheels: {wheels}", f"sdists: {sdists}"],
            ),
            None,
            None,
        )
    if len(wheels) > 1 or len(sdists) > 1:
        return (
            bad(
                name,
                "more than one artifact in dist/ -- which one would be published?",
                [
                    f"wheels: {[Path(w).name for w in wheels]}",
                    f"sdists: {[Path(s).name for s in sdists]}",
                ],
            ),
            None,
            None,
        )
    wheel, sdist = Path(wheels[0]), Path(sdists[0])
    verb = "built" if do_build else "adopted (--no-build)"
    return (
        ok(
            name,
            f"{verb} one sdist and one wheel",
            [
                f"wheel: {wheel.name} ({wheel.stat().st_size:,} bytes)",
                f"sdist: {sdist.name} ({sdist.stat().st_size:,} bytes)",
                f"source tree: {repo}",
            ],
        ),
        sdist,
        wheel,
    )


def _module_available(module: str) -> bool:
    return run([sys.executable, "-c", f"import {module}"]).returncode == 0


def check_wheel_demo_data(wheel: Path, source_data_dir: Path) -> Result:
    """The check that matters most: is the demo actually inside the wheel?

    Two independent reviews found that `.gitignore`'s `*.jsonl` rule plus
    `packages = ["src/model_migration_kit"]` can produce a wheel with no demo golden set
    while every local test and the editable-install CI demo job still pass. The
    only evidence that settles it is the bytes in the zip.
    """
    name = "wheel-demo-data"
    evidence: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        for filename in DEMO_DATA:
            member = f"{IMPORT_NAME}/{DATA_SUBDIR}/{filename}"
            if member not in names:
                missing.append(member)
                evidence.append(f"MISSING from wheel: {member}")
                continue
            payload = zf.read(member)
            evidence.append(f"in wheel: {member} ({len(payload):,} bytes)")
            if not payload.strip():
                mismatched.append(f"{member} is empty")
                continue
            on_disk = source_data_dir / filename
            if not on_disk.is_file():
                mismatched.append(f"{filename} is in the wheel but not at {on_disk}")
            elif on_disk.read_bytes() != payload:
                mismatched.append(f"{member} differs from {on_disk} (stale build?)")

    if missing:
        return bad(
            name,
            f"{len(missing)} demo file(s) absent from the wheel",
            [
                *evidence,
                "this is the failure that passes every local test and fails every user;",
                "check .gitignore whitelists src/model_migration_kit/data/ and rebuild",
            ],
        )
    if mismatched:
        return bad(
            name, "demo data in the wheel does not match the source tree", evidence + mismatched
        )
    evidence.append("each file byte-identical to src/model_migration_kit/data/")
    return ok(name, f"all {len(DEMO_DATA)} demo files present inside {wheel.name}", evidence)


def _extract_wheel(wheel: Path, workdir: Path) -> Path:
    """Unzip the wheel into the scratch directory, once per run, and say where.

    A wheel install is an unzipped one -- `pip install` does not leave a zip on
    `sys.path` -- so a probe importing out of this directory is importing what a
    user would actually get. It is shared by every check that runs an isolated
    probe: two checks extracting the same artifact into two directories can end up
    reporting on different bytes, which is the kind of disagreement that costs an
    afternoon. The marker file makes the reuse safe rather than merely convenient;
    a second wheel in the same scratch directory re-extracts instead of being
    quietly answered by the first.
    """
    extract = workdir / "wheel-extract"
    marker = workdir / "wheel-extract.source"
    if extract.is_dir() and marker.is_file() and marker.read_text(encoding="utf-8") == str(wheel):
        return extract
    if extract.exists():
        shutil.rmtree(extract)
    extract.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(extract)
    marker.write_text(str(wheel), encoding="utf-8")
    return extract


RESOURCE_PROBE = '''
import json, sys

target = sys.argv[1]
sys.path.insert(0, target)
out = {}
try:
    import importlib.resources as ir
    import model_migration_kit
    out["paths"] = [str(p) for p in list(model_migration_kit.__path__)]
    files = ir.files("model_migration_kit.data")
    out["anchor"] = str(files)
    sizes = {}
    for name in sys.argv[2:]:
        entry = files / name
        sizes[name] = len(entry.read_bytes()) if entry.is_file() else -1
    out["sizes"] = sizes
except Exception as exc:  # noqa: BLE001 - reported verbatim to the parent
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
'''


def check_demo_data_importable(wheel: Path, workdir: Path) -> Result:
    """Reach the demo data the way `migkit demo` does: `importlib.resources`.

    Run with `-S` (no site-packages, no .pth files) so an editable install of the
    repo cannot serve the file the wheel forgot. Without that isolation this check
    is worthless: `model_migration_kit` has no `__init__.py` yet, so it is a namespace
    package, and a namespace package *multiplexes* -- the repo's `src/` directory
    silently supplies anything the wheel is missing. The cwd is a temp directory
    for the same reason the contract runs the demo from `$tmp`: a repo-root cwd
    masks a missing package resource.
    """
    name = "wheel-demo-data-importable"
    extract = _extract_wheel(wheel, workdir)

    probe = workdir / "_resource_probe.py"
    probe.write_text(RESOURCE_PROBE, encoding="utf-8")
    proc = run([sys.executable, "-S", str(probe), str(extract), *DEMO_DATA], cwd=workdir)
    if proc.returncode != 0:
        return bad(
            name,
            f"the isolated resource probe exited {proc.returncode}",
            [f"cwd: {workdir}", f"sys.path[0]: {extract}", *tail(proc.stderr)],
        )
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return bad(name, "the resource probe produced no JSON", tail(proc.stdout + proc.stderr))

    if "error" in data:
        return bad(
            name,
            "importlib.resources could not reach the demo data in the wheel",
            [f"cwd: {workdir}", f"sys.path[0]: {extract}", data["error"]],
        )

    evidence = [
        f"probe ran with -S, cwd={workdir}, sys.path[0]={extract}",
        f"anchor: {data.get('anchor')}",
        f"model_migration_kit.__path__ = {data.get('paths')}",
    ]
    expected_path = (extract / IMPORT_NAME).resolve()
    leaked = [p for p in data.get("paths", []) if Path(p).resolve() != expected_path]
    if leaked:
        return bad(
            name,
            "the import resolved outside the extracted wheel, so this proves nothing",
            [*evidence, f"unexpected path entries: {leaked}"],
        )
    bad_sizes = [f"{n} -> {s}" for n, s in sorted(data.get("sizes", {}).items()) if s <= 0]
    evidence += [
        f"{n}: {s:,} bytes via importlib.resources"
        for n, s in sorted(data.get("sizes", {}).items())
        if s > 0
    ]
    if bad_sizes or len(data.get("sizes", {})) != len(DEMO_DATA):
        return bad(
            name,
            "a demo file was unreachable or empty through importlib.resources",
            evidence + bad_sizes,
        )
    return ok(
        name, "importlib.resources reaches all demo data with only the wheel on sys.path", evidence
    )


SDIST_REQUIRED = ("LICENSE", "NOTICE", "README.md", "pyproject.toml")


def check_sdist_contents(sdist: Path) -> Result:
    """Checklist §5 item 4: LICENSE, NOTICE, README, pyproject, src/ and tests/."""
    name = "sdist-contents"
    with tarfile.open(sdist) as tf:
        members = tf.getnames()
    root = Path(members[0]).parts[0] if members else ""
    relative = {str(Path(m).relative_to(root)).replace("\\", "/") for m in members if m != root}

    missing = [f for f in SDIST_REQUIRED if f not in relative]
    has_src = any(r.startswith("src/") for r in relative)
    has_tests = any(r.startswith("tests/") for r in relative)
    demo_in_sdist = [f"src/{IMPORT_NAME}/{DATA_SUBDIR}/{f}" for f in DEMO_DATA]
    missing_demo = [m for m in demo_in_sdist if m not in relative]

    evidence = [
        f"sdist root: {root}/ ({len(relative)} entries)",
        f"required files present: {[f for f in SDIST_REQUIRED if f in relative]}",
        f"src/ present: {has_src}; tests/ present: {has_tests}",
        f"demo data in sdist: {len(demo_in_sdist) - len(missing_demo)}/{len(demo_in_sdist)}",
    ]
    problems = []
    if missing:
        problems.append(f"missing: {missing}")
    if not has_src:
        problems.append("no src/ tree")
    if not has_tests:
        problems.append("no tests/ tree")
    if missing_demo:
        problems.append(f"demo data missing from sdist: {missing_demo}")
    if problems:
        return bad(name, "the sdist is missing required content", evidence + problems)
    return ok(name, "sdist carries licence, readme, pyproject, src/ and tests/", evidence)


def check_license_metadata(wheel: Path, repo: Path) -> Result:
    """Phase 1, exit criteria 2-6, read off the built metadata rather than pyproject.

    The sibling shipped `license = { file = "LICENSE" }`, which embedded the whole
    MIT text into the `License:` field, alongside a deprecated `License ::`
    classifier -- and PyPI rejects an upload that sets both forms.
    """
    name = "license-metadata"
    evidence: list[str] = []
    problems: list[str] = []
    notes: list[str] = []

    with zipfile.ZipFile(wheel) as zf:
        metadata_member = dist_info_member(zf, "METADATA")
        if metadata_member is None:
            return bad(name, "the wheel has no METADATA", [])
        msg = parse_metadata(zf.read(metadata_member).decode("utf-8"))
        names = zf.namelist()

        expression = (msg.get("License-Expression") or "").strip()
        evidence.append(f"License-Expression: {expression or '(absent)'}")
        if not expression:
            problems.append("no License-Expression: the PEP 639 SPDX field is missing")

        legacy = (msg.get("License") or "").strip()
        if legacy:
            evidence.append(f"License: {legacy[:60]!r}... ({len(legacy)} chars)")
            if len(legacy) > 200 or "\n" in legacy:
                problems.append("the legacy License: field holds the licence body -- "
                                "`license = { file = ... }` has crept back")
        else:
            evidence.append("License: (absent, correct under PEP 639)")

        classifiers = [c for c in msg.get_all("Classifier") or [] if c.startswith("License ::")]
        evidence.append(f"deprecated 'License ::' classifiers: {classifiers or 'none'}")
        if classifiers:
            problems.append(f"PyPI rejects SPDX + classifier together: {classifiers}")

        declared_files = [v.strip() for v in msg.get_all("License-File") or []]
        evidence.append(f"License-File: {declared_files or 'none'}")
        for required in LICENSE_FILES:
            if required not in declared_files:
                problems.append(f"License-File: {required} is not declared")
            member = next(
                (n for n in names if ".dist-info/licenses/" in n and Path(n).name == required), None
            )
            if member is None:
                problems.append(f"{required} is not in .dist-info/licenses/ of the wheel")
                continue
            shipped = zf.read(member)
            evidence.append(f"in wheel: {member} ({len(shipped):,} bytes)")
            on_disk = repo / required
            if on_disk.is_file() and on_disk.read_bytes() != shipped:
                problems.append(f"{member} differs from the repo's {required}")

        license_member = next(
            (n for n in names if ".dist-info/licenses/" in n and Path(n).name == "LICENSE"), None
        )
        if license_member and expression:
            text = zf.read(license_member).decode("utf-8", "replace")
            detected = spdx_from_license_text(text)
            first = " / ".join(line.strip() for line in text.splitlines()[:2] if line.strip())
            evidence.append(f"shipped LICENSE begins: {first}")
            if detected is None:
                notes.append(
                    "the shipped licence text matches no known signature, so text-vs-SPDX "
                    "agreement is UNVERIFIED -- check it by hand"
                )
            elif detected not in expression:
                problems.append(
                    f"the shipped text is {detected} but the metadata declares "
                    f"'{expression}' -- declaration and bytes disagree"
                )
            else:
                evidence.append(
                    f"shipped text identified as {detected}, consistent with '{expression}'"
                )

    if problems:
        return bad(name, "licence metadata is not coherent", evidence + problems)
    if notes:
        return skipped(name, "licence text could not be identified mechanically", evidence + notes)
    return ok(name, "SPDX expression, licence files and classifiers are coherent", evidence)


def check_dependencies(wheel: Path, repo: Path) -> Result:
    """Every Requires-Dist is accounted for by pyproject, and vice versa."""
    name = "dependencies-declared"
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(pyproject["project"].get("dependencies", []))
    declared_python = str(pyproject["project"].get("requires-python", "")).strip()

    with zipfile.ZipFile(wheel) as zf:
        member = dist_info_member(zf, "METADATA")
        if member is None:
            return bad(name, "the wheel has no METADATA", [])
        msg = parse_metadata(zf.read(member).decode("utf-8"))

    built_runtime, built_extras = split_requires_dist(list(msg.get_all("Requires-Dist") or []))
    built_python = (msg.get("Requires-Python") or "").strip()

    built_names = {normalize_project_name(requirement_name(r)) for r in built_runtime}
    declared_names = {normalize_project_name(requirement_name(r)) for r in declared}

    evidence = [
        f"pyproject dependencies ({len(declared)}): {declared}",
        f"metadata Requires-Dist, runtime ({len(built_runtime)}): {built_runtime}",
        f"metadata Requires-Dist, extras ({len(built_extras)}): {built_extras}",
        f"Requires-Python: metadata {built_python!r} vs pyproject {declared_python!r}",
    ]
    problems = []
    unexplained = sorted(built_names - declared_names)
    unbuilt = sorted(declared_names - built_names)
    if unexplained:
        problems.append(f"in the wheel but not in pyproject: {unexplained}")
    if unbuilt:
        problems.append(f"in pyproject but not in the wheel: {unbuilt}")
    if built_python != declared_python:
        problems.append("Requires-Python disagrees between metadata and pyproject")

    for requirement in declared:
        marker = requirement_marker(requirement)
        built = next(
            (
                r
                for r in built_runtime
                if normalize_project_name(requirement_name(r))
                == normalize_project_name(requirement_name(requirement))
            ),
            None,
        )
        if built is None:
            continue
        if not markers_equivalent(marker, requirement_marker(built)):
            problems.append(
                f"{requirement_name(requirement)}: marker {requirement_marker(built)!r} in the "
                f"wheel, {marker!r} in pyproject"
            )

    if problems:
        return bad(
            name, "declared dependencies do not match the built metadata", evidence + problems
        )
    return ok(name, f"all {len(built_runtime)} runtime requirements accounted for", evidence)


def check_tomli_marker(wheel: Path) -> Result:
    """`tomli` must be there, and must be conditioned on `python_version < "3.11"`.

    An unconditioned `tomli` installs a redundant package on 3.11+; a missing one
    breaks the config loader on 3.10, which CI actually runs, so the gap is not
    theoretical.
    """
    name = "tomli-marker"
    with zipfile.ZipFile(wheel) as zf:
        member = dist_info_member(zf, "METADATA")
        if member is None:
            return bad(name, "the wheel has no METADATA", [])
        msg = parse_metadata(zf.read(member).decode("utf-8"))
    runtime, _ = split_requires_dist(list(msg.get_all("Requires-Dist") or []))
    entry = next(
        (r for r in runtime if normalize_project_name(requirement_name(r)) == "tomli"), None
    )
    if entry is None:
        return bad(
            name,
            "no runtime `tomli` requirement in the built metadata",
            [
                f"runtime requirements: {runtime}",
                "requires-python is >=3.10 and tomllib is stdlib only from 3.11",
            ],
        )
    marker = requirement_marker(entry)
    evidence = [f"Requires-Dist: {entry}", f"marker: {marker!r}"]
    if not markers_equivalent(marker, 'python_version < "3.11"'):
        return bad(
            name,
            "the tomli marker is missing or is not `python_version < \"3.11\"`",
            evidence,
        )
    return ok(name, "tomli is conditioned on python_version < \"3.11\"", evidence)


def check_contract_dependency_clause(wheel: Path, repo: Path) -> Result:
    """Hold the frozen contract and the built metadata against each other.

    Phase 1 exit criterion 7 says Requires-Dist lists three distributions "and
    nothing else". `tomli` was added deliberately after that document was frozen,
    so a *correct* build now fails a *stale* criterion. Neither side may be
    silently accepted: this raises a FLAG, which is neither a pass nor a build
    failure, and it clears itself the moment the contract sentence is amended.
    """
    name = "contract-dependency-clause"
    contract = repo / CONTRACT_PATH
    if not contract.is_file():
        return skipped(name, f"{CONTRACT_PATH} not found; nothing to hold the build against")
    declared = contract_declared_requirements(contract.read_text(encoding="utf-8"))
    if declared is None:
        return skipped(
            name,
            "could not locate the 'Requires-Dist lists ... and nothing else' clause",
            [f"contract: {contract}"],
        )

    with zipfile.ZipFile(wheel) as zf:
        member = dist_info_member(zf, "METADATA")
        msg = parse_metadata(zf.read(member).decode("utf-8")) if member else None
    if msg is None:
        return bad(name, "the wheel has no METADATA", [])
    runtime, _ = split_requires_dist(list(msg.get_all("Requires-Dist") or []))

    contract_names = {normalize_project_name(n) for n in declared}
    built_names = {normalize_project_name(requirement_name(r)) for r in runtime}
    evidence = [
        f"contract clause ({contract.name}, Phase 1 criterion 7): {sorted(contract_names)}",
        f"built metadata: {sorted(built_names)}",
    ]
    extra = sorted(built_names - contract_names)
    absent = sorted(contract_names - built_names)
    if not extra and not absent:
        return ok(name, "the build matches the frozen contract's dependency clause", evidence)
    if absent:
        return bad(
            name,
            "the build is missing a dependency the contract requires",
            evidence + [f"required by contract, absent from build: {absent}"],
        )
    return flagged(
        name,
        f"the build ships {extra}, which the frozen contract says is 'nothing else'",
        evidence
        + [
            "the build is believed correct and the contract stale: tomli was added",
            "deliberately after Phase 1 was frozen (pyproject records the reasoning).",
            "ACTION: amend the criterion-7 sentence in "
            f"{CONTRACT_PATH.as_posix()} to include it, with the",
            "reason, and this flag clears itself -- the clause is parsed, not copied.",
            "Do not suppress this by deleting the dependency.",
        ],
    )


def read_dunder_version(init_file: Path) -> str | None:
    """`__version__` read as text, never by importing -- an import would need the
    package's dependencies and would give the *installed* answer, not this tree's."""
    if not init_file.is_file():
        return None
    match = re.search(
        r"^__version__\s*[:=]\s*['\"]([^'\"]+)['\"]", init_file.read_text(encoding="utf-8"), re.M
    )
    return match.group(1) if match else None


def check_version_coherence(wheel: Path, sdist: Path, repo: Path) -> list[Result]:
    """Every place the version is written agrees, and none of them says `.dev`."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    sources: dict[str, str] = {}
    notes: list[str] = []

    if "version" in project:
        sources["pyproject [project].version"] = str(project["version"])
    elif "version" in project.get("dynamic", []):
        notes.append("pyproject declares a dynamic version (hatch reads __init__.py)")

    with zipfile.ZipFile(wheel) as zf:
        member = dist_info_member(zf, "METADATA")
        msg = parse_metadata(zf.read(member).decode("utf-8")) if member else None
    if msg is not None:
        sources["wheel METADATA Version"] = (msg.get("Version") or "").strip()
    sources["wheel filename"] = wheel_version_from_filename(wheel.name)
    sources["sdist filename"] = sdist.name.rsplit(".tar.gz", 1)[0].split("-")[-1]

    init_file = repo / "src" / IMPORT_NAME / "__init__.py"
    dunder = read_dunder_version(init_file)
    results: list[Result] = []

    if dunder is None:
        results.append(
            skipped(
                "version-dunder",
                f"{init_file.relative_to(repo).as_posix()} has no __version__ yet",
                [
                    "Phase 2 of the release contract writes it, deliberately last;",
                    "until then __version__ vs importlib.metadata cannot be compared",
                ],
            )
        )
    else:
        sources["src/.../__init__.py __version__"] = dunder

    distinct = sorted(set(sources.values()))
    evidence = [f"{label}: {value}" for label, value in sources.items()] + notes
    if len(distinct) == 1:
        results.append(
            ok(
                "version-coherence",
                f"all {len(sources)} version sources say {distinct[0]}",
                evidence,
            )
        )
    else:
        results.append(
            bad(
                "version-coherence",
                f"version sources disagree: {distinct}",
                evidence
                + ["a user filing a bug quotes __version__; the index serves the metadata"],
            )
        )

    dev = sorted({v for v in sources.values() if version_is_dev(v)})
    if dev:
        results.append(
            bad(
                "version-not-dev",
                f"a development version would be published: {dev}",
                [
                    "Phase 3 replaces 0.1.0.dev0 with 0.1.0 before the tag exists.",
                    "The publish workflow's tag-vs-wheel guard would also reject this,",
                    "but only after a release had been cut. Pass --allow-dev-version",
                    "to acknowledge this while the build is still in progress.",
                ],
            )
        )
    else:
        results.append(ok("version-not-dev", f"no .dev suffix in {distinct[0]}", []))

    if dunder is not None:
        results.append(_check_installed_version(repo, dunder))
    return results


def _check_installed_version(repo: Path, dunder: str) -> Result:
    """`importlib.metadata.version(...)` in this interpreter vs `__version__`.

    Guarded: if the interpreter's install of the distribution is some *other* tree
    (an editable install pointing at a different checkout, which is exactly the
    situation in a worktree), the comparison would be about that tree, not this
    one, so it is reported SKIPPED rather than dressed up as agreement.
    """
    name = "version-matches-installed"
    code = (
        "import json, importlib.metadata as md\n"
        f"d = md.distribution({DIST_NAME!r})\n"
        "print(json.dumps({'version': d.version, 'location': str(getattr(d, '_path', ''))}))"
    )
    proc = run([sys.executable, "-c", code])
    if proc.returncode != 0:
        return skipped(
            name,
            f"{DIST_NAME} is not installed in {Path(sys.executable).name}",
            tail(proc.stderr, 3),
        )
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return skipped(name, "importlib.metadata produced no parsable answer", tail(proc.stdout))
    evidence = [
        f"interpreter: {sys.executable}",
        f"importlib.metadata.version({DIST_NAME!r}) = {data['version']}",
        f"distribution location: {data['location']}",
        f"__version__ in the tree under verification = {dunder}",
    ]
    location = Path(data["location"]).resolve() if data["location"] else None
    elsewhere = location is not None and repo.resolve() not in [location, *location.parents]
    if elsewhere and data["version"] != dunder:
        return skipped(
            name,
            "the installed distribution is a different tree, so a mismatch here proves nothing",
            evidence + [f"tree under verification: {repo}"],
        )
    if data["version"] != dunder:
        return bad(name, "__version__ and the installed metadata disagree", evidence)
    return ok(name, f"__version__ and installed metadata agree on {dunder}", evidence)


def check_console_script(wheel: Path) -> Result:
    """The console script's target module must be in the wheel it is declared in."""
    name = "console-script"
    with zipfile.ZipFile(wheel) as zf:
        member = dist_info_member(zf, "entry_points.txt")
        if member is None:
            return bad(name, "the wheel declares no entry points", [f"wheel: {wheel.name}"])
        text = zf.read(member).decode("utf-8")
        names = set(zf.namelist())
    match = re.search(rf"^{re.escape(CONSOLE_SCRIPT)}\s*=\s*([\w.]+):(\w+)", text, re.M)
    declared = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    evidence = [f"entry_points.txt: {declared}"]
    if not match:
        return bad(name, f"no console script named {CONSOLE_SCRIPT}", evidence)
    module, func = match.group(1), match.group(2)
    candidates = [module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py"]
    present = [c for c in candidates if c in names]
    evidence.append(f"target {module}:{func} -> looked for {candidates}")
    if not present:
        return bad(
            name,
            f"the wheel declares `{CONSOLE_SCRIPT}` but does not contain {module}",
            evidence
            + [
                "installing this wheel yields a `migkit` command that fails with",
                "ModuleNotFoundError on first use",
            ],
        )
    evidence.append(f"found in wheel: {present[0]}")
    return ok(name, f"`{CONSOLE_SCRIPT}` points at a module the wheel ships", evidence)


def check_wheel_py_typed(wheel: Path, repo: Path) -> Result:
    """PEP 561's marker must be in the wheel, not merely in the tree.

    The marker arrived in 30efded and nothing has watched it since. The tree can
    carry the file and the wheel still drop it -- what ships is decided by the
    build backend's package configuration, and a `.gitignore` rule or a backend
    change can silently split the two. A test that reads
    `Path(model_migration_kit.__file__).parent / "py.typed"` cannot tell the
    difference, because in a dev checkout that path *is* the source tree.

    Ported from opik-rigor, which shipped a 0.1.0 wheel without it and had every
    annotation in the library discarded by type checkers in installed copies.
    """
    name = "wheel-py-typed"
    member = f"{IMPORT_NAME}/{PY_TYPED}"
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        record_member = dist_info_member(zf, "RECORD")
        record = zf.read(record_member).decode("utf-8") if record_member else ""
        size = zf.getinfo(member).file_size if member in names else None

    on_disk = repo / "src" / IMPORT_NAME / PY_TYPED
    evidence = [
        f"looked for: {member}",
        f"source tree: {on_disk} exists={on_disk.is_file()}",
        f"listed in RECORD: {member in record}",
    ]
    if size is None:
        return bad(
            name,
            f"{member} is not in the wheel; the library's annotations are dead on arrival",
            evidence
            + [
                "PEP 561: a type checker must ignore annotations in an installed package",
                "with no marker, however complete those annotations are.",
                "An empty file is the whole fix -- but it has to be in `packages`.",
            ],
        )
    evidence.append(f"in wheel: {member} ({size:,} bytes; empty is correct)")
    if member not in record:
        return bad(name, f"{member} is in the zip but absent from RECORD", evidence)
    return ok(name, f"{member} is inside {wheel.name}", evidence)


EXPORTS_PROBE = """
import importlib, json, sys

target = sys.argv[1]
sys.path.insert(0, target)
out = {}
try:
    import model_migration_kit as pkg
    out["paths"] = [str(p) for p in list(pkg.__path__)]
    out["all"] = list(getattr(pkg, "__all__", []))
    symbols = {}
    for name in out["all"]:
        try:
            value = getattr(pkg, name)
        except AttributeError:
            # `from pkg import name` also reaches a submodule that nothing has
            # imported yet, which getattr on its own does not. Try that before
            # calling the name absent, or a shipped submodule reads as missing.
            try:
                value = importlib.import_module("model_migration_kit." + name)
            except Exception:
                symbols[name] = {"found": False, "kind": "missing"}
                continue
        symbols[name] = {"found": True, "kind": type(value).__name__}
    out["symbols"] = symbols
except Exception as exc:  # noqa: BLE001 - reported verbatim to the parent
    out["error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
"""


def check_exports_importable(wheel: Path, workdir: Path, repo: Path) -> Result:
    """Every name in `__all__` must be importable from the package root of the wheel.

    This is `console-script` for the library half of the package. The console script
    check exists because a `migkit` command pointing at a module the wheel omitted
    fails on first use; `__all__` is the same promise in the same artifact -- the
    list this package tells the world is its public surface -- and a name in it that
    an installed copy cannot supply is the same defect with a quieter symptom.

    The surface is small on purpose: `__init__.py` declines a library API for v0.1
    and exports three accessors, `demo_goldenset_path`, `demo_rubric_path` and
    `demo_config_path`. They are the fix for a documented incident -- the README
    told readers to open
    `GoldenSet.load('src/model_migration_kit/data/demo_goldenset.jsonl')`, a path
    that exists in a checkout and in no install -- so a build that loses one of them
    reopens exactly the gap they were added to close, and reopens it silently: the
    README's corrected example still works for everyone who has the repo.

    Checked against the *wheel*, in a subprocess with `-S` and a temp cwd, because
    `from model_migration_kit import demo_rubric_path` in this repository's own
    interpreter is answered by the editable install's `src/` and would pass whatever
    the wheel contained. `__init__.py` already removed the worse version of that
    hazard -- while it was absent the package was a namespace package, whose
    `__path__` multiplexes, so `src/` silently supplied files the wheel had dropped
    -- but a check run in-process would hand the hazard straight back.

    The tree's `__all__` is compared with the wheel's first, so a stale artifact is
    reported as a stale artifact rather than as a missing name. Ported from
    opik-rigor, where the same check guards a much larger curated surface.
    """
    name = "wheel-exports-importable"
    extract = _extract_wheel(wheel, workdir)
    probe = workdir / "_exports_probe.py"
    probe.write_text(EXPORTS_PROBE, encoding="utf-8")
    proc = run([sys.executable, "-S", str(probe), str(extract)], cwd=workdir)
    if proc.returncode != 0:
        return bad(
            name,
            f"the isolated export probe exited {proc.returncode}",
            [f"cwd: {workdir}", f"sys.path[0]: {extract}", *tail(proc.stderr)],
        )
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return bad(name, "the export probe produced no JSON", tail(proc.stdout + proc.stderr))

    evidence = [
        f"probe ran with -S, cwd={workdir}, sys.path[0]={extract}",
        f"{IMPORT_NAME}.__path__ = {data.get('paths')}",
    ]
    if "error" in data:
        return bad(
            name,
            f"the wheel's {IMPORT_NAME} could not be imported at all",
            evidence
            + [
                data["error"],
                "every name below is unreachable as a consequence; this is the wheel",
                "failing to be a package, not one export going missing.",
            ],
        )

    expected_path = (extract / IMPORT_NAME).resolve()
    leaked = [p for p in data.get("paths", []) if Path(p).resolve() != expected_path]
    if leaked:
        return bad(
            name,
            "the import resolved outside the extracted wheel, so this proves nothing",
            evidence + [f"unexpected path entries: {leaked}"],
        )

    exported = sorted(data.get("all", []))
    if not exported:
        return bad(
            name,
            f"the wheel's {IMPORT_NAME} declares no __all__",
            evidence
            + [
                "v0.1 exports three accessors deliberately -- see the module docstring --",
                "so an empty list means the build lost them, and a row reporting PASS",
                "on nothing would be worse than no row at all.",
            ],
        )

    tree_all = _source_dunder_all(repo / "src" / IMPORT_NAME / "__init__.py")
    if tree_all is not None and sorted(tree_all) != exported:
        return bad(
            name,
            "the wheel's __all__ differs from the source tree's -- the artifact is stale",
            evidence
            + [
                f"only in the wheel: {sorted(set(exported) - set(tree_all))}",
                f"only in the tree:  {sorted(set(tree_all) - set(exported))}",
                "rebuild before reading anything else on this row; the wheel predates",
                "the last edit to __init__.py and no export has been proved missing.",
            ],
        )

    answers = data.get("symbols", {})
    missing = sorted(n for n in exported if not answers.get(n, {}).get("found"))
    kinds: dict[str, int] = {}
    for entry in answers.values():
        if entry.get("found"):
            kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    evidence += [
        f"__all__ in the wheel ({len(exported)} names): {exported}",
        f"resolved from the wheel by kind: {dict(sorted(kinds.items()))}",
    ]
    if missing:
        return bad(
            name,
            f"{len(missing)} name(s) in __all__ cannot be imported from the wheel: {missing}",
            evidence
            + [
                f"`from {IMPORT_NAME} import <name>` raises ImportError for these after a",
                "real `pip install`, while passing in this checkout, because the checkout",
                "has src/ on sys.path and an install has only what the wheel shipped.",
            ],
        )
    return ok(name, f"all {len(exported)} names in __all__ import from {wheel.name}", evidence)


def _source_dunder_all(init_file: Path) -> list[str] | None:
    """`__all__` read as text from the tree, for the stale-artifact comparison only.

    Text rather than an import, for the reason the probe exists at all: importing
    `__init__.py` here would be answered by the tree, and the tree is the thing the
    wheel is being compared against.

    The optional annotation in the pattern is not tidiness. `__init__.py` writes
    `__all__: list[str] = [...]`, and a pattern matching only a bare `__all__ = [`
    finds nothing, returns None, and None means "no comparison possible" -- so a
    stale wheel would sail past the staleness branch and be judged on its own
    outdated list. The names come back sorted because `__all__` has no meaningful
    order and every caller compares sets.
    """
    if not init_file.is_file():
        return None
    text = init_file.read_text(encoding="utf-8")
    match = re.search(r"^__all__\s*(?::[^=]+)?=\s*\[(.*?)\]", text, re.M | re.S)
    if not match:
        return None
    return sorted(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def check_twine(sdist: Path, wheel: Path, repo: Path) -> Result:
    name = "twine-check"
    if not _module_available("twine"):
        return skipped(
            name,
            "twine is not installed in this interpreter",
            [
                f"interpreter: {sys.executable}",
                "fix: .\\.venv\\Scripts\\python.exe -m pip install --upgrade build twine",
                "PyPI's own rendering check is therefore UNVERIFIED, not passed",
            ],
        )
    # NO_COLOR so twine emits the plain word, and the ANSI strip below in case a
    # future twine ignores it. Belt and braces on purpose: this check reads
    # another tool's human-facing output, which is not an interface anybody
    # promised to keep stable, and its whole value is that it fails loudly rather
    # than counting wrong. The sibling repository learned this the same way --
    # green on every developer machine, zero passes counted the first time it ran
    # in CI, on a build twine had just passed.
    proc = run(
        [sys.executable, "-m", "twine", "check", str(sdist), str(wheel)],
        cwd=repo,
        env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"},
    )
    lines = plain_lines(proc.stdout + proc.stderr)
    passed = sum(1 for line in lines if line.strip().endswith("PASSED"))
    evidence = [f"checked: {sdist.name}, {wheel.name}", *lines]
    if proc.returncode != 0:
        return bad(name, f"twine check exited {proc.returncode}", evidence)
    if passed < 2:
        return bad(name, f"expected PASSED twice, saw it {passed} time(s)", evidence)
    return ok(name, "twine check PASSED on both sdist and wheel", evidence)


def check_readme_pip_install(repo: Path) -> Result:
    """Any `pip install` line in the README must name the real distribution."""
    name = "readme-pip-install"
    readme = repo / "README.md"
    if not readme.is_file():
        return bad(name, "README.md does not exist", [])
    text = readme.read_text(encoding="utf-8")
    targets = readme_pip_install_targets(text)
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    allowed = {normalize_project_name(DIST_NAME)}
    for requirement in pyproject["project"].get("dependencies", []):
        allowed.add(normalize_project_name(requirement_name(requirement)))
    for group in pyproject["project"].get("optional-dependencies", {}).values():
        for requirement in group:
            allowed.add(normalize_project_name(requirement_name(requirement)))

    if not targets:
        return ok(
            name,
            "no `pip install <name>` line in README.md to get wrong",
            [f"scanned {len(text.splitlines())} lines of {readme.name}"],
        )
    evidence = []
    wrong = []
    for target in targets:
        normalized = normalize_project_name(target)
        verdict = "ok" if normalized in allowed else "NOT A REAL DISTRIBUTION NAME"
        evidence.append(f"pip install {target}  ->  normalises to {normalized}  ->  {verdict}")
        if normalized not in allowed:
            wrong.append(target)
    if wrong:
        return bad(
            name,
            f"README installs {wrong}, which is not {DIST_NAME} nor a declared dependency",
            evidence
            + [f"the console script is `{CONSOLE_SCRIPT}`; the distribution is `{DIST_NAME}`"],
        )
    return ok(name, f"all {len(targets)} pip-install target(s) name a real distribution", evidence)


def check_readme_commands(repo: Path) -> Result:
    """Any command the README shows must exist as a real CLI subcommand."""
    name = "readme-commands"
    readme = repo / "README.md"
    if not readme.is_file():
        return bad(name, "README.md does not exist", [])
    commands = readme_cli_commands(readme.read_text(encoding="utf-8"))
    if not commands:
        return ok(
            name,
            f"README.md shows no `{CONSOLE_SCRIPT} <subcommand>` invocation",
            [
                "nothing to verify; when Phase 4 writes the real README this check",
                "starts asserting every command it shows",
            ],
        )

    probe = run([sys.executable, "-c", f"import {ENTRY_POINT_MODULE} as m; print(m.__file__)"])
    if probe.returncode != 0:
        return skipped(
            name,
            f"{ENTRY_POINT_MODULE} is not importable, so README commands are UNVERIFIED",
            [f"README shows: {commands}", *tail(probe.stderr, 3)],
        )
    cli_file = Path(probe.stdout.strip().splitlines()[-1]).resolve()
    if repo.resolve() not in cli_file.parents:
        return skipped(
            name,
            "the importable CLI belongs to a different tree than the one under verification",
            [
                f"importable {ENTRY_POINT_MODULE}: {cli_file}",
                f"tree under verification: {repo}",
                f"README shows: {commands}",
                "verifying against the wrong tree would be a claim about someone else's code",
            ],
        )

    evidence = [f"CLI introspected: {cli_file}", f"README shows: {commands}"]
    unknown = []
    for command in commands:
        code = (
            "import sys\n"
            f"from {ENTRY_POINT_MODULE} import main\n"
            f"sys.exit(main([{command!r}, '--help']))"
        )
        proc = run([sys.executable, "-c", code], cwd=repo)
        exists = proc.returncode == 0
        evidence.append(f"`{CONSOLE_SCRIPT} {command} --help` -> exit {proc.returncode}")
        if not exists:
            unknown.append(command)
    if unknown:
        return bad(
            name,
            f"README shows commands the CLI does not have: {unknown}",
            evidence
            + ["a documented command that does not exist is the `@rigor.repeat(...)` defect"],
        )
    return ok(name, f"all {len(commands)} README command(s) exist in the CLI", evidence)


# --------------------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_release.py",
        description="Run the mechanically checkable rows of the Session 4 release checklist.",
    )
    default_repo = Path(__file__).resolve().parent.parent
    parser.add_argument("--repo", type=Path, default=default_repo, help="tree to verify")
    parser.add_argument(
        "--dist-dir", type=Path, default=None, help="where artifacts go (default <repo>/dist)"
    )
    parser.add_argument(
        "--no-build", action="store_true", help="use the artifacts already in dist/"
    )
    parser.add_argument(
        "--allow-dev-version",
        action="store_true",
        help="downgrade the .dev version check to a skip, for mid-build runs",
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="do not delete the scratch directory"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo: Path = args.repo.resolve()
    dist_dir: Path = (args.dist_dir or repo / "dist").resolve()

    print("=" * 100)
    # Printed output stays ASCII-only: this runs on Windows consoles whose code
    # page is whatever the machine's locale says, and a UnicodeEncodeError in the
    # banner would take down the check that was about to report a real problem.
    print("model-migration-kit release verification")
    print("docs/session-4-release-contract.md, section 5")
    print("=" * 100)
    print(f"repo        : {repo}")
    print(f"dist dir    : {dist_dir}")
    print(f"interpreter : {sys.executable}")
    print(f"platform    : {sys.platform}")
    print()

    results: list[Result] = []
    workdir = Path(tempfile.mkdtemp(prefix="mk-verify-"))

    def emit(result: Result) -> None:
        results.append(result)
        print(result.render())

    try:
        build_result, sdist, wheel = check_build(repo, dist_dir, do_build=not args.no_build)
        emit(build_result)

        if wheel is None or sdist is None:
            reason = "no wheel was produced, so this could not be checked"
            for pending in (
                "wheel-demo-data",
                "wheel-demo-data-importable",
                "sdist-contents",
                "license-metadata",
                "dependencies-declared",
                "tomli-marker",
                "contract-dependency-clause",
                "version-coherence",
                "version-not-dev",
                "console-script",
                "wheel-py-typed",
                "wheel-exports-importable",
                "twine-check",
            ):
                emit(skipped(pending, reason, [f"see the `{build_result.name}` row above"]))
        else:
            emit(check_wheel_demo_data(wheel, repo / "src" / IMPORT_NAME / DATA_SUBDIR))
            emit(check_demo_data_importable(wheel, workdir))
            emit(check_sdist_contents(sdist))
            emit(check_license_metadata(wheel, repo))
            emit(check_dependencies(wheel, repo))
            emit(check_tomli_marker(wheel))
            emit(check_contract_dependency_clause(wheel, repo))
            for result in check_version_coherence(wheel, sdist, repo):
                is_dev_fail = result.name == "version-not-dev" and result.status == FAIL
                if is_dev_fail and args.allow_dev_version:
                    result = skipped(
                        result.name,
                        "a .dev version is present and --allow-dev-version was passed",
                        [result.summary, "this must be a PASS before Phase 8 cuts the tag"],
                    )
                emit(result)
            emit(check_console_script(wheel))
            emit(check_wheel_py_typed(wheel, repo))
            emit(check_exports_importable(wheel, workdir, repo))
            emit(check_twine(sdist, wheel, repo))

        emit(check_readme_pip_install(repo))
        emit(check_readme_commands(repo))
    finally:
        if args.keep_temp:
            print(f"\nscratch kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    failures = [r for r in results if r.status == FAIL]
    flags = [r for r in results if r.status == FLAG]
    skips = [r for r in results if r.status == SKIP]
    passes = [r for r in results if r.status == PASS]

    print()
    print("=" * 100)
    print(
        f"{len(passes)} passed, {len(failures)} failed, {len(flags)} flagged, "
        f"{len(skips)} skipped, {len(results)} checks total"
    )
    for group, label in ((failures, "FAILED"), (flags, "FLAGGED"), (skips, "SKIPPED")):
        for result in group:
            print(f"  {label:8} {result.name}: {result.summary}")
    print("=" * 100)

    if failures or flags:
        print("Release is blocked. Every line above is reproducible; fix the cause, not the check.")
        return 1
    if skips:
        print(
            "Nothing failed, but a check could not run. A skip is not a pass -- exit code 2 so a\n"
            "release gate cannot mistake this for green."
        )
        return 2
    print("Every check ran and passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
