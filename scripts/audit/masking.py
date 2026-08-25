"""Mask the three page fields that defeat a differential comparison of two reports.

**Read this before you write any tool that diffs one rendered report against
another.** The first differential sweep of the HTML report compared whole pages
naively, found every single pair of renders different, and reported **zero
findings**. It was not that the report was clean. It was that the report prints

    evidence hash
    867a4d0e3d2a71bc575925c8fca6bde7ca5b0f69fa6eab013699a9beae8c465c

which is a sha256 **of the whole evidence file**. Change one byte anywhere in the
payload -- which is precisely what a differential sweep does, once per leaf path,
thousands of times -- and that line changes too. Every pair differs, every
comparison is "not identical", and a method whose entire signal is *byte-identity
between two renders* silently reports nothing at all. A sweep that finds nothing
looks exactly like a sweep of a correct document.

Two more fields have the same property, for two other tools:

* the ``generated`` timestamp, which is wall-clock unless the caller pins
  ``now=`` on :func:`~model_migration_kit.report.render_html_string`; and
* the absolute paths in the provenance block, which for ``migkit demo`` live in
  a fresh ``/var/folders/.../migkit-demo-XXXXXXXX`` temporary directory on every
  single run.

Neither matters to a sweep that pins ``now`` and keeps one fixture root, and both
matter enormously to a harness that renders the demo twice -- once on clean source
and once on mutated source -- and diffs the two pages.

Masking is lossy, and that cuts the other way
---------------------------------------------
Every mask hides a difference, and a hidden difference is a finding you will not
see. So each is separately switchable and only the evidence hash is on by
default:

* The **evidence hash** is derived from the log, never rendered *from* the field
  under test, so masking it can never hide a real finding. It is unconditional.
* **Timestamps** and **paths** can be real payload content. ``created`` on a
  comparison record is a timestamp; ``artifacts`` are paths. If you mask those
  and then sweep those leaves, the sweep will call them unrendered and you will
  have manufactured a clean result. Prefer pinning ``now=`` and a fixed fixture
  root, exactly as :mod:`differential_render` does, and leave both masks off.

The masks deliberately do **not** touch the golden-set, judges or config hashes,
which are also bare 64-hex strings on their own lines. Those come out of the
payload; a change in one is a real rendered difference and a sweep must be able
to see it. That is why the evidence hash is masked by exact string match against
a freshly computed ``hash_file`` rather than by a regex over "any sha256".
"""

from __future__ import annotations

import re

from model_migration_kit.contracts import hash_file

EVIDENCE_HASH = "<EVIDENCE-HASH>"
TIMESTAMP = "<TIMESTAMP>"
PATH = "<PATH>"

#: ISO-8601 with an offset, which is what ``contracts.py`` writes and what the
#: provenance block prints. Deliberately narrow: a bare date is not matched,
#: because a golden-set item may legitimately contain one.
_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)

#: A POSIX absolute path with at least two segments, ending at whitespace, a
#: quote or a closing bracket. One segment (``/tmp``) is excluded so that a
#: sentence containing a lone slash is left alone.
_ABS_PATH = re.compile(r"/(?:[\w.@+-]+/)+[\w.@+-]*")


def mask_page(
    text: str,
    *,
    evidence_path=None,
    mask_timestamps: bool = False,
    mask_paths: bool = False,
) -> str:
    """Return ``text`` with the volatile fields replaced by stable placeholders.

    ``evidence_path`` is the evidence log the page was rendered from. Its
    ``hash_file`` digest is the string that appears on the page as the evidence
    hash, and replacing it is the whole reason this module exists -- see the
    module docstring. Pass it whenever you have it; passing ``None`` leaves the
    hash in place and, in a differential sweep, will cost you every finding.

    ``mask_timestamps`` and ``mask_paths`` default to off because both can hide a
    real finding. Turn them on when comparing two independent *runs* (two demo
    invocations, clean source against mutated source) rather than two renders of
    one pinned fixture.
    """
    if evidence_path is not None:
        digest = hash_file(evidence_path)
        text = text.replace(digest, EVIDENCE_HASH)
    if mask_timestamps:
        text = _ISO.sub(TIMESTAMP, text)
    if mask_paths:
        text = _ABS_PATH.sub(PATH, text)
    return text


def mask_run_output(text: str, *, evidence_path=None) -> str:
    """Mask everything that varies between two separate runs of the same command.

    The setting the mutation harness works in: ``migkit demo`` twice, once with
    clean source and once with one line changed, diffed line by line. Every run
    gets a new temporary directory and a new wall clock, so without this the diff
    is 100% noise and the one mutated sentence is invisible in it.
    """
    return mask_page(
        text, evidence_path=evidence_path, mask_timestamps=True, mask_paths=True
    )
