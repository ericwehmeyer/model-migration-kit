"""The per-dimension view: how a tag did, and when a tag cannot be judged.

Separate from ``series.py`` for two reasons, and only the first is about size.

``series.py`` is past 600 lines, which the build plan named as the trigger for
splitting this out. That alone would be a filing decision. The one that matters
is what each module is allowed to depend on: a series is a sequence of *runs*,
and a dimension is a slice across the *golden set*, so this module needs
``goldenset`` where ``series`` does not, and a dependency the series does not
need is a dependency the series should not carry.

What both share is the rule from ``evidence.py``: the log is read as a stream and
never as a list. ``judge.verdict`` embeds the input, the output and the judge's
raw reply for every completion, and holding one measured 5.0-5.8 times the log's
own bytes resident. Everything here consumes an iterator and holds counters.

This module computes counts and cells. It renders nothing, reads no file, takes
no path, and does not import ``report`` -- ``report`` imports this.
"""

from __future__ import annotations
