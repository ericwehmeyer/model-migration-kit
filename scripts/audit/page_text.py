"""Flatten a rendered HTML report to plain text, so a human can read a diff of it.

The report is one self-contained HTML file with inline CSS and inline SVG. A diff
of two of those is unreadable: a one-word change in a sentence shows up as a
changed 400-character ``<p>`` line, and the interesting words are surrounded by
markup that never changes.

This is not a general HTML-to-text converter and does not try to be. It is tuned
for one job -- making the *sentences* of this report comparable and quotable:

* ``<script>`` and ``<style>`` bodies go entirely, because the CSS is ~600 lines
  that no audit finding has ever been about;
* the block-level closers become newlines, so one rendered paragraph is one line
  and a diff points at a sentence;
* ``</td>`` and ``</th>`` become `` | ``, which keeps a table row on one line and
  keeps its cells distinguishable -- half the findings in this project's audits
  are "this cell says X and that cell says Y";
* everything else is unwrapped and HTML entities are resolved, so hostile text
  that the report correctly escaped reads as the text it is.

What it deliberately loses: attributes. The SVGs carry their numbers in
``data-value`` / ``data-created`` attributes and those vanish here. A finding
about the *chart* has to be made against the raw HTML; this is for the prose.

**An SVG's ``<title>`` and ``<desc>`` are dropped; the document's ``<title>`` is
kept.** That distinction is the whole point of this module being a module, and
getting it wrong cost one round trip: the first version of this fix dropped
*every* ``<title>``, including ``<head><title>``, which is not a tooltip at all --
it is the browser tab, the link preview, and one of the two places a screenshot
cannot crop. "FAKE MODELS" in the flattened demo went from 2 to 1 and the
regression check said "still found", because it counted presence and not
occurrences. An SVG ``<title>`` is a tooltip and an accessible name;
it is *not* rendered prose. The earlier version of this file stripped the tags
and kept their character data, so a screen-reader-only disclosure came back as
visible text -- and "is this sentence on the page?" returned **true** for a
string no sighted reader can see. That is precisely the class of defect these
audits exist to find, so a measurement tool that makes it is worse than no tool.
Two of this project's audit findings are *about* ``<title>``-only disclosures
(the banner bar's "floor not recorded", and the timeline's zero-span note); with
the old behaviour both would have been invisible to the sweep that found them.

If you want the accessible names, parse the raw HTML for them deliberately. Do
not get them by accident from a function whose contract is "what the reader sees".
:func:`accessible_names` is that deliberate parse, and it exists because dropping
the tooltips from :func:`html_to_text` was only half the fix. A field that reaches
the page **only** through an accessible name is not unrendered -- it is rendered
to one audience and withheld from another -- and a sweep that has no name for that
state files it under "never rendered", which is the opposite of what is true. The
two channels are separate functions on purpose: each answers one question, and
neither can quietly answer the other's.
"""

from __future__ import annotations

import html as htmllib
import re
import sys
from pathlib import Path

#: An ``<svg>`` element, whole. ``<title>``/``<desc>`` are stripped *only* inside
#: one of these -- see the module docstring. Non-greedy and non-nested, which is
#: what this renderer emits: two flat ``<svg>`` elements, never one inside another.
_SVG_BLOCK = re.compile(r"(?s)<svg\b.*?</svg>")

#: The accessible-name elements, inside an SVG only.
_SVG_LABEL = re.compile(r"(?s)<(title|desc)\b.*?</\1>")

_BLOCK_CLOSE = re.compile(
    r"(?i)</(p|div|h[1-6]|li|tr|section|table|thead|tbody|details|summary|pre|dd|dt)>"
)

#: Attributes that carry an accessible name or description on *any* element.
#: ``title=`` is here because it is the tooltip and the fallback accessible name;
#: ``alt`` because it is the name of an image. ``aria-labelledby`` and
#: ``aria-describedby`` are deliberately absent -- they hold id references, not
#: text, and resolving them would mean building a DOM. This renderer emits none
#: of these today (only ``<svg role="img"><title>``); they are matched anyway so
#: that a future disclosure moved into an ``aria-label`` does not silently become
#: invisible to the sweep, which is the exact failure this module already made
#: once in the other direction.
_A11Y_ATTR = re.compile(
    r"""(?is)(?<![-\w])(aria-label|aria-description|aria-roledescription"""
    r"""|aria-valuetext|alt|title)\s*=\s*("|')(.*?)\2"""
)


def html_to_text(source: str) -> str:
    """Return the readable text of a rendered report page."""
    text = re.sub(r"(?s)<(script|style).*?</\1>", "", source)
    text = _SVG_BLOCK.sub(lambda m: _SVG_LABEL.sub("", m.group(0)), text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _BLOCK_CLOSE.sub("\n", text)
    text = re.sub(r"(?i)</t[dh]>", " | ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = htmllib.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def accessible_names(source: str) -> str:
    """Return the text a screen reader gets and a sighted reader does not, one per line.

    The complement of :func:`html_to_text`, and the reason the two are separate:
    that function answers "what does the reader see?", this one answers "what is
    announced?", and a tool that conflates them will report a tooltip-only
    disclosure as a rendered sentence -- or, once the tooltips are correctly
    dropped, as no disclosure at all.

    What is collected, in document order:

    * ``<title>`` and ``<desc>`` **inside an ``<svg>``** -- the accessible name
      and description of the picture. The document's ``<head><title>`` is
      excluded: it is not an accessible name, it is the browser tab and the link
      preview, and :func:`html_to_text` already returns it as the visible text it
      is.
    * ``title=``, ``alt=`` and the text-bearing ``aria-*`` attributes on any
      element, wherever they appear.

    What this deliberately does **not** claim: that everything here is
    screen-reader-*only*. An ``alt`` whose text is also printed beside the image
    appears in both channels, and the caller is expected to compare the two --
    presence here is not by itself evidence of a hidden disclosure. It is the
    *difference* between this and :func:`html_to_text` that carries the finding.

    ``<script>`` and ``<style>`` are removed first, so a CSS string containing
    ``title="..."`` cannot manufacture a name that no element has.
    """
    source = re.sub(r"(?s)<(script|style).*?</\1>", "", source)
    found: list[tuple[int, str]] = []
    for match in _A11Y_ATTR.finditer(source):
        found.append((match.start(), htmllib.unescape(match.group(3))))
    for block in _SVG_BLOCK.finditer(source):
        for label in _SVG_LABEL.finditer(block.group(0)):
            text = re.sub(r"<[^>]+>", "", label.group(0))
            found.append((block.start() + label.start(), htmllib.unescape(text)))
    return "\n".join(text for _, text in sorted(found, key=lambda item: item[0]))


def html_file_to_text(path) -> str:
    """Read a rendered report from disk and flatten it."""
    return html_to_text(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: page_text.py <report.html>", file=sys.stderr)
        return 2
    print(html_file_to_text(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
