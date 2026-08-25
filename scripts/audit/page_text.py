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
"""

from __future__ import annotations

import html as htmllib
import re
import sys
from pathlib import Path

_BLOCK_CLOSE = re.compile(
    r"(?i)</(p|div|h[1-6]|li|tr|section|table|thead|tbody|details|summary|pre|dd|dt)>"
)


def html_to_text(source: str) -> str:
    """Return the readable text of a rendered report page."""
    text = re.sub(r"(?s)<(script|style).*?</\1>", "", source)
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
