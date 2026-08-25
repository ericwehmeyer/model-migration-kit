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
