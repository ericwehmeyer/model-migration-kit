"""Rendering a migration verdict, from the evidence log and nothing else.

This module is the only part of model-migration-kit a stranger sees, and it is written
to protect two claims the definition of done makes in public: that a keyless
reader understands the verdict in under two minutes, and that the HTML file is
change-control evidence a compliance reviewer can open on a machine with no route
to the internet. Both of those are broken by *defaults* rather than by bugs -- an
unescaped template, a webfont, a demo that quietly reads as a real provider -- so
most of what is load-bearing here is a default being overridden on purpose.

**Everything is reconstructed from disk (invariant 2).** No function in this
module accepts a ``ComparisonReport``, a ``JudgedArtifact``, or any other live
object produced earlier in the same process; the inputs are paths.
``migkit compare`` writes its evidence and then calls the renderer with the log's
path, exactly as ``migkit report`` would tomorrow on another machine. That is not
purity for its own sake: a partial-render path that only runs after a crash is a
path that has never run when you need it. Routing the happy path through the same
reader means every green run exercises the reconstruction, and the crashed-run
case differs only in how many records it finds.

**No statistic is ever recomputed.** Every rate, interval, bound and p-value is
passed through from the ``migkit.comparison`` payload verbatim. A renderer that
re-derives a number can disagree with the gate that decided the verdict, and the
one thing a change-control document may never do is contradict itself. Where a
number is absent from the payload it is printed as unavailable rather than
reconstructed -- including per-threshold provenance, which the payload does not
carry (see :data:`THRESHOLD_SOURCE_UNRECORDED`).

**``n == 0`` is a rendering state, not a computation.** ``wilson_interval(0, 0)``
raises ``ValueError`` ("a rate over zero runs is not a rate"), which is correct
and which a truncated run reaches routinely. :class:`RateStat` carries ``None``
for every derived field in that case and the cell prints an em dash. rigor is
never handed a zero denominator from here; this module imports no statistical
function at all.

**Self-containment is enforced at render time, not only in tests.**
:func:`render_html` runs :func:`assert_self_contained` over its own output
*before* writing, so a template edit that adds a font link fails the render rather
than shipping a file CI notices later. The detector walks the document with the
stdlib ``HTMLParser`` rather than regexing the raw text, because a regex cannot
tell a URL that appears as escaped text (harmless -- no fetch) from one that
appears as an attribute value (a fetch). And jinja2 is configured with
``autoescape`` and ``StrictUndefined`` explicitly: jinja2's default is
``autoescape=False``, model outputs are arbitrary attacker-influenced text, and an
``<img src="https://tracker/x.png">`` inside a completion is a real network fetch
in an unescaped template.

**Reconstruction fetches nothing either.** The paragraph above is about the
document; this one is about the reader that builds it, and for a long time the two
disagreed. Sharing an evidence log across machines is the *designed* workflow, so
the paths inside it are attacker-influenced input that arrives on a reviewer's
machine -- and they were handed straight to ``open()``. A recorded
``C:\\Windows\\win.ini`` was read and parsed; a recorded
``\\\\192.0.2.111\\share\\x.jsonl`` blocked for 21 seconds attempting an outbound
SMB connection, which on Windows is how an attacker collects an NTLMv2 hash by
naming a host. A module that works this hard to guarantee the rendered document
fetches nothing may not fetch during *reconstruction*. :func:`_resolve` now
confines every recorded path to the evidence log's own directory; ``--artifact-dir``
and ``--goldenset`` are how the cross-machine case says where the files went, which
is what those flags were always for.

**The fake-model band derives from the artifacts, never from a flag.**
``RunHeader.adapter`` records ``type(adapter).__name__``, so ``is_demo`` is true
whenever either side's adapter name starts with ``Fake``. The consequence is the
point: you cannot obtain a clean-looking report from scripted models by avoiding
``migkit demo``. A flag-driven banner is exactly the banner that goes missing from
the screenshot someone pastes into a deck.

**The document is bounded, and says by how much.** The report used to grow as
``changed_items x 2n x max_output_chars`` with nothing looking at the total: 200
items at n=20 rendered 32.4 MB, 1000 items at n=20 rendered 161.8 MB holding
41,000 ``<pre>`` blocks, and every guard in this module passed on both --
self-contained, well-formed, every per-block truncation notice present. A
generated, valid, attested, unopenable artifact is the shape of failure this
project says it cares most about, so :data:`DEFAULT_MAX_REPORT_CHARS` now bounds
the quoted model text and :class:`DetailBudget` reports what that cost.

What is bounded is deliberately the *quoted text* and never the *row*. Every item
that changed state still gets its row, its id, its tags, its judges and its
margins; rows past the budget carry no input, no draws and no judge reasons, and
say so where the outputs would have been. Dropping rows to fit a byte budget would
remove findings, which is worse than a large file; dropping some of a row's draws
would misrepresent the distribution the whole tool exists to show. So a row's
detail is embedded whole or not at all, rows are visited round-robin across flips,
gains and unstable so that no section can crowd out another, and the first row that
does not fit stops embedding for the rest of the document -- rather than skipping
it and embedding whichever later rows happen to be short, which would make the
sample of quoted evidence a function of output length.

**There is no item-level rate.** build-plan §6, as amended on 2026-08-13: a
three-state classification (passing / failing / unstable) does not reduce to one
fraction without smuggling the ambiguous items into one bucket or the other. The
report prints three item counts beside the completion-level rate and never a
fourth number derived from them.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import opik_rigor
from jinja2 import DictLoader, Environment, StrictUndefined, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .contracts import EVENT_COMPARISON, EVENT_VERDICT, Verdict, hash_file, utc_now
from .errors import ArtifactError, GoldenSetError, ReportError
from .evidence import resolve_evidence, stream_records
from .goldenset import GoldenSet
from .judging import JudgedArtifact
from .runner import RunArtifact

__all__ = [
    "DEFAULT_MAX_REPORT_CHARS",
    "Completeness",
    "DetailBudget",
    "FlipRow",
    "JudgeRow",
    "MethodologySection",
    "RateStat",
    "ReportModel",
    "RunSummary",
    "UrlViolation",
    "assert_self_contained",
    "external_urls",
    "methodology_sections",
    "render_html",
    "render_html_string",
    "render_terminal",
]

#: Printed wherever a number does not exist, rather than a zero or a guess. The
#: two are different facts and a reader who cannot tell them apart will read the
#: wrong one: "0.0%" is a measured failure, "--" is an unmeasured cell.
EM_DASH = "—"
TERMINAL_DASH = "-"

#: What the report says instead of inventing per-threshold provenance. §4 of the
#: Session 3 contract asks for "0.90 (./migkit.toml)" versus "(default)" versus
#: "(--floor)", and the ``migkit.comparison`` payload (§1.2) carries the config
#: path but not which of the three set any individual number. Deriving it would be
#: exactly the recomputation this module refuses, so the source is reported at the
#: granularity the evidence actually has.
THRESHOLD_SOURCE_UNRECORDED = "source not recorded in the evidence"

#: An adapter class name starting with this is rigor's scripted stand-in. Compared
#: as a recorded string, not as a type: the header was written by some earlier
#: process, possibly on another machine.
_FAKE_PREFIX = "Fake"

_NO_VERDICT = "NO VERDICT"
_NO_VERDICT_REASON = "the run ended before a verdict was recorded"

#: Characters of quoted model text one report may embed, across every change
#: section. Not a byte cap on the file: it is counted against the text actually
#: embedded, so a golden set whose outputs are short is never truncated for a size
#: it would not have reached. Markup, the statistics tables and the methodology
#: appendix sit outside it and are a fixed cost of roughly 30 KB.
#:
#: Ten million is set from measurement rather than picked round. Rendered on this
#: build with every item changed and 4000-character outputs: 40 items at n=5 is
#: 1.65 MB, 200 at n=5 is 8.18 MB, 200 at n=20 is 32.4 MB produced in 40 s, and
#: 1000 at n=20 is 161.8 MB holding 41,000 ``<pre>`` blocks. The first two open and
#: scroll; the third is painful; the fourth is not a document. So the line is drawn
#: where the file stops being one a reviewer can open, and everything at or below
#: 200 items at n=5 -- five times the completions per side the methodology asks for
#: -- still renders whole. What the budget catches is n, which is the factor
#: nothing downstream accounted for: 200 items at n=20 needs 32.8 M characters and
#: is told so.
DEFAULT_MAX_REPORT_CHARS = 10_000_000

#: The three change sections, in the order the document prints them and the order
#: the budget visits them within one round.
_CHANGE_SECTIONS = ("flips", "gains", "unstable")


# --------------------------------------------------------------------------- #
# self-containment: the detector, and the assertion render_html runs on itself
# --------------------------------------------------------------------------- #

#: Elements whose mere presence defeats the guarantee. ``<script>`` is banned even
#: with inline content: ``<details>``/``<summary>`` is the whole expansion
#: mechanism, and "no script at all" is a property a parser can check in one line,
#: where "no script that fetches" erodes one convenience at a time.
FORBIDDEN_TAGS = frozenset({"script", "link", "iframe", "object", "embed", "base"})

#: Attributes a browser dereferences. A value here that is not a ``#``-fragment
#: and not a ``data:`` URI is a request leaving the machine -- including a bare
#: relative path, which resolves against wherever the reviewer saved the file.
FETCHING_ATTRS = frozenset(
    {
        "src",
        "href",
        "srcset",
        "poster",
        "data",
        "action",
        "formaction",
        "background",
        "cite",
        "longdesc",
        "manifest",
        "usemap",
    }
)

#: ``<meta http-equiv="refresh" content="0;url=https://evil.example">`` is a
#: navigation the browser performs on its own, with no element a fetching
#: attribute belongs to, so the rules above walk straight past it. Nothing in the
#: template emits one today; the detector's job is to catch the *future template
#: edit*, and this one would have sailed through ``assert_self_contained``.
_META_REFRESH_URL_RE = re.compile(r"(^|[;,\s])url\s*=", re.IGNORECASE)

#: Inline event handlers. Forbidden as a class rather than checked for a URL:
#: ``<script>`` is already banned outright on the argument that "no script at all"
#: is a property a parser can check in one line, and an ``onload="fetch(...)"`` is
#: script by another name. Same gap, same reasoning -- unreachable from data
#: today, reachable from one template edit tomorrow. Anchored rather than a bare
#: ``startswith("on")`` so that a future ``data-*``-style attribute is judged on
#: what it is, not on two leading characters.
_EVENT_HANDLER_RE = re.compile(r"^on[a-z]+$")

#: Any scheme other than ``data:``. Applied to *attribute values only*, which is
#: the distinction a regex over the raw document cannot make.
_SCHEME_RE = re.compile(r"^\s*(?!data:)[a-zA-Z][a-zA-Z0-9+.-]*:")
_URL_FN_RE = re.compile(r"url\(\s*['\"]?\s*", re.IGNORECASE)
_IMPORT_RE = re.compile(r"@import", re.IGNORECASE)


@dataclass(frozen=True)
class UrlViolation:
    """One place the rendered document would reach off the machine.

    Carries the position so a template author sees the offending line rather than
    "the report is not self-contained", which is a sentence nobody can act on.
    """

    line: int
    column: int
    tag: str
    attribute: str
    value: str
    reason: str

    def __str__(self) -> str:
        where = f"line {self.line}, col {self.column}"
        what = f"<{self.tag}>" if not self.attribute else f"<{self.tag} {self.attribute}=>"
        value = f" {self.value!r}" if self.value else ""
        return f"{where}: {what}{value} -- {self.reason}"


class _UrlScanner(HTMLParser):
    """Walks the document and records every fetching position.

    Deliberately built on the stdlib parser. The alternative -- a regex over the
    raw text -- cannot distinguish ``<img src="https://x">`` written as an element
    (a fetch) from the same characters appearing as escaped text inside a model
    completion (harmless, and the thing the escaping test asserts is neutralised).
    A regex detector would either miss real violations or fail every report that
    quotes a URL, and the second is the failure that gets a check switched off.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[UrlViolation] = []
        self._style_depth = 0

    # -- reporting ---------------------------------------------------------- #

    def _add(self, tag: str, attribute: str, value: str, reason: str) -> None:
        line, column = self.getpos()
        self.violations.append(
            UrlViolation(
                line=line,
                column=column,
                tag=tag,
                attribute=attribute,
                value=value,
                reason=reason,
            )
        )

    # -- rules -------------------------------------------------------------- #

    def _attribute_reason(self, name: str, value: str) -> str | None:
        """The first rule this attribute breaks, or None.

        At most one reason per attribute: a ``<link href="https://...">`` breaks
        three rules simultaneously and reporting it three times would make the
        violation count a measure of how many rules overlap rather than of how
        many places fetch.
        """
        stripped = value.strip()
        if _EVENT_HANDLER_RE.match(name):
            return (
                f"{name} is an inline event handler; it is script, and script may "
                f"not appear in a self-contained report"
            )
        if stripped.startswith("//"):
            return "protocol-relative URL; it fetches over whatever scheme the page was opened with"
        if _SCHEME_RE.match(value):
            return "URL scheme other than data:; the document would fetch it"
        if name in FETCHING_ATTRS and stripped and not stripped.startswith(("#", "data:")):
            return (
                f"{name} is dereferenced by the browser and this value is neither a "
                f"#-fragment nor a data: URI"
            )
        return None

    def _scan_css(self, tag: str, attribute: str, css: str) -> None:
        """``@import`` and non-``data:`` ``url(`` inside CSS, one report per block."""
        if _IMPORT_RE.search(css):
            self._add(tag, attribute, "@import", "@import pulls in a stylesheet at view time")
            return
        for match in _URL_FN_RE.finditer(css):
            rest = css[match.end() :]
            if not rest.lower().startswith("data:"):
                snippet = rest[:60].split(")")[0]
                self._add(
                    tag,
                    attribute,
                    f"url({snippet}",
                    "CSS url() referencing something other than a data: URI",
                )
                return

    # -- HTMLParser hooks --------------------------------------------------- #

    def _meta_refresh_reason(self, attrs: Sequence[tuple[str, str | None]]) -> str | None:
        """``<meta http-equiv=refresh content="0;url=...">``, or None.

        Checked on the element rather than the attribute because the fetch is not
        in any one attribute: ``content`` is inert next to any other
        ``http-equiv``, and ``http-equiv=refresh`` is inert without a ``url=`` in
        ``content``. A same-page refresh with no ``url=`` reloads the local file
        and is left alone -- pointless in a report, but not a fetch, and a
        detector that reports non-fetches is a detector someone switches off.
        """
        values = {name.lower(): (value or "") for name, value in attrs}
        if values.get("http-equiv", "").strip().lower() != "refresh":
            return None
        content = values.get("content", "")
        if not _META_REFRESH_URL_RE.search(content):
            return None
        return (
            "meta http-equiv=refresh navigates on its own; this one carries a url= "
            "and the document would fetch it at view time"
        )

    def _handle_tag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in FORBIDDEN_TAGS:
            self._add(lowered, "", "", f"<{lowered}> may not appear in a self-contained report")
            return
        if lowered == "meta":
            reason = self._meta_refresh_reason(attrs)
            if reason is not None:
                content = next(
                    (value or "" for name, value in attrs if name.lower() == "content"), ""
                )
                self._add(lowered, "content", content, reason)
                return
        for raw_name, raw_value in attrs:
            name = raw_name.lower()
            value = raw_value or ""
            if name == "style":
                self._scan_css(lowered, "style", value)
                continue
            reason = self._attribute_reason(name, value)
            if reason is not None:
                self._add(lowered, name, value, reason)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "style":
            self._style_depth += 1
        self._handle_tag(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self._scan_css("style", "", data)


def external_urls(html: str) -> tuple[UrlViolation, ...]:
    """Every position in ``html`` a browser would fetch from off the machine.

    Returns an empty tuple for a document that is genuinely self-contained. The
    test that matters is not this one returning ``()`` on a good document -- a
    detector that always returned ``()`` would pass that forever -- but the one
    that feeds it a fixture with a CDN stylesheet, a protocol-relative image and
    an ``@import`` and demands exactly three violations back.
    """
    scanner = _UrlScanner()
    scanner.feed(html)
    scanner.close()
    return tuple(scanner.violations)


def assert_self_contained(html: str, *, source: str = "<rendered>") -> None:
    """Raise unless ``html`` fetches nothing. Called by ``render_html`` on itself.

    Raises:
        ReportError: listing every violation with its line, tag and attribute.
            ``ReportError`` rather than ``ArtifactError`` because nothing is wrong
            with the *evidence* here -- the document this tool just produced is
            the thing that fails, and the CLI maps the two to the same exit code
            but a reader should not be sent looking at their artifacts.
    """
    violations = external_urls(html)
    if not violations:
        return
    listed = "\n".join(f"  - {one}" for one in violations)
    raise ReportError(
        f"{source} is not self-contained: {len(violations)} position(s) would fetch "
        f"from the network.\n{listed}\n"
        f"This file is opened inside a compliance review on a machine with no route "
        f"to the internet, where a missing stylesheet renders the document as "
        f"unstyled text and an outbound request from a document full of model "
        f"outputs is itself the finding."
    )


# --------------------------------------------------------------------------- #
# the model -- every field is read from the evidence log or from disk
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RateStat:
    """A pass rate with its intervals, or the absence of one.

    ``n == 0`` is a real state in a truncated run, and rigor raises ``ValueError``
    rather than returning a rate over zero runs. So every optional field here is
    ``None`` exactly when there was nothing to measure, and the renderer prints an
    em dash instead of a number it would have had to invent.

    Nothing in this class computes anything: the values are lifted out of the
    ``assert_pass_rate`` dict that the verdict itself was measured against.
    """

    passes: int
    n: int
    rate: float | None
    interval: tuple[float, float] | None  # two-sided, for printing
    lower_bound: float | None  # one-sided, the number the gate used

    @classmethod
    def from_gate(cls, gate: Mapping[str, Any]) -> RateStat:
        """Lift one side's numbers out of rigor's pass-rate dict, verbatim."""
        n = int(gate.get("n") or 0)
        if n <= 0:
            return cls(passes=0, n=0, rate=None, interval=None, lower_bound=None)
        lower = gate.get("interval_lower")
        upper = gate.get("interval_upper")
        interval = None if lower is None or upper is None else (float(lower), float(upper))
        rate = gate.get("pass_rate")
        bound = gate.get("lower_bound")
        return cls(
            passes=int(gate.get("successes") or 0),
            n=n,
            rate=None if rate is None else float(rate),
            interval=interval,
            lower_bound=None if bound is None else float(bound),
        )

    @property
    def observed(self) -> str:
        """``19 / 20``. Counts are observed-against-expected everywhere."""
        return f"{self.passes} / {self.n}"


@dataclass(frozen=True)
class JudgeRow:
    """One judge's row of the report: the numbers, and the flags they produced."""

    name: str
    model_id: str
    rubric_hash: str
    baseline: RateStat
    candidate: RateStat
    p_value: float | None
    test_ran: str  # "mann-whitney-u" | "mann-whitney-u-on-outcomes" | "not-run"
    regressed: bool | None
    floor_cleared: bool | None
    underpowered: bool
    note: str
    # Beyond the frozen list in the contract, and every one of them is a number
    # already in the payload that the reader cannot interpret the row without.
    alpha: float | None = None
    holm_threshold: float | None = None
    runs_needed: int | None = None
    mw_powered: bool | None = None
    power: Mapping[str, Any] = field(default_factory=dict)
    #: build-plan §6 as amended: three counts, never a fourth derived from them.
    items_baseline: Mapping[str, int] = field(default_factory=dict)
    items_candidate: Mapping[str, int] = field(default_factory=dict)
    items: int = 0
    imputed_baseline: int = 0
    imputed_candidate: int = 0
    parse_failures_baseline: int = 0
    parse_failures_candidate: int = 0


@dataclass(frozen=True)
class FlipRow:
    """One item that changed state, with both models' actual words beside it."""

    item_id: str
    tags: tuple[str, ...]
    input: str | None  # None when the golden set is unavailable/changed
    baseline_outputs: tuple[str, ...]
    candidate_outputs: tuple[str, ...]
    judges: tuple[str, ...]  # judges under which it flipped
    reasons: Mapping[str, str]  # judge name -> the candidate-side reason
    truncated: bool
    #: judge name -> ``4/5 -> 1/5``. The margin is the finding: a 5/5 -> 0/5 flip
    #: and a 4/5 -> 1/5 flip are different, and printing only "flipped" hides which.
    labels: Mapping[str, str] = field(default_factory=dict)
    #: False when the document's :class:`DetailBudget` was spent before this row.
    #: The row is still here, and so are its id, tags, judges and margins: what is
    #: missing is the quoted text, and the document says so in place of it rather
    #: than reusing the "golden set unavailable" wording, which would be a
    #: different fact. Defaults True so that every other construction site --
    #: tests, and any caller building a row directly -- keeps the old meaning.
    detail_embedded: bool = True

    @property
    def quoted_chars(self) -> int:
        """Characters of model text this row embeds. What the budget counts.

        The input, both sides' draws and the judge reasons -- each already cut to
        ``max_output_chars``, so this is the post-truncation size and not the size
        of what the models actually said.
        """
        return (
            len(self.input or "")
            + sum(len(one) for one in self.baseline_outputs)
            + sum(len(one) for one in self.candidate_outputs)
            + sum(len(one) for one in self.reasons.values())
        )

    @property
    def summary(self) -> str:
        tags = " ".join(f"#{tag}" for tag in self.tags)
        margins = ", ".join(
            f"{name} {self.labels[name]}" for name in self.judges if name in self.labels
        )
        parts = [self.item_id]
        if tags:
            parts.append(tags)
        parts.append(margins or ", ".join(self.judges))
        return " · ".join(parts)


@dataclass(frozen=True)
class RunSummary:
    """One side of the comparison, as the run artifact on disk records it."""

    model_id: str
    adapter: str
    n_per_item: int
    items: int
    completions: int
    expected: int
    failures: int
    parts: int
    artifact_path: str
    latency_median: float | None
    latency_p90: float | None

    @property
    def is_fake(self) -> bool:
        return self.adapter.startswith(_FAKE_PREFIX)

    @property
    def observed(self) -> str:
        """``47 / 60 completions`` -- or ``47 / ? `` when nothing recorded the total.

        The shortfall travels next to the number rather than in a footnote,
        because a rate over the items that finished is biased whenever the run
        died on a slow or hard item, which is the exact circumstance that kills
        runs.
        """
        expected = str(self.expected) if self.expected else "?"
        return f"{self.completions} / {expected}"


@dataclass(frozen=True)
class Completeness:
    """Why this report may be short, in the report itself rather than a footnote."""

    complete: bool
    observed_completions: int
    expected_completions: int
    missing: tuple[str, ...]  # human sentences
    last_event: str | None
    last_ts: str | None


@dataclass(frozen=True)
class DetailBudget:
    """How much quoted model text the document embedded, against what it was allowed.

    Present on every report, capped or not, because "this document is complete" is
    a fact a reviewer signing a migration decision needs stated rather than
    inferred from the absence of a warning.
    """

    #: ``max_report_chars`` in force. ``0`` when the caller asked for no bound.
    limit: int
    #: Characters of quoted text actually embedded, summed over every row.
    embedded: int
    #: Changed items in the document, over all three sections.
    rows: int
    #: Of those, how many carry their input, draws and judge reasons.
    rows_embedded: int
    #: section name -> ``{"rows": int, "embedded": int, "chars": int}``.
    sections: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    @property
    def capped(self) -> bool:
        return self.rows_embedded < self.rows

    @property
    def rows_summarised(self) -> int:
        return self.rows - self.rows_embedded

    @property
    def sentence(self) -> str:
        """One sentence naming exactly what was left out, or why nothing was.

        Written here rather than in the template so that the terminal renderer,
        the HTML band and ``warnings`` all say the same words: three copies of a
        disclosure are three chances for one of them to go stale.
        """
        if not self.capped:
            return (
                f"Every one of the {self.rows} changed item(s) carries its full "
                f"outputs: {self.embedded:,} characters of quoted model text "
                f"against a budget of {self.limit:,}."
            )
        listed = ", ".join(
            f"{name} {self.sections[name]['embedded']} of {self.sections[name]['rows']}"
            for name in _CHANGE_SECTIONS
            if self.sections.get(name, {}).get("rows")
        )
        return (
            f"The budget for quoted model text ({self.limit:,} characters, "
            f"[report] max_report_chars) was reached: {self.rows_embedded} of "
            f"{self.rows} changed item(s) carry their outputs ({listed}). The other "
            f"{self.rows_summarised} are listed in full with their ids, tags, judges "
            f"and margins, and their model text is not embedded -- it is in the run "
            f"artifacts named in the provenance block. No row was dropped."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "embedded": self.embedded,
            "rows": self.rows,
            "rows_embedded": self.rows_embedded,
            "capped": self.capped,
            "sections": {name: dict(one) for name, one in self.sections.items()},
        }


@dataclass(frozen=True)
class MethodologySection:
    heading: str
    body: tuple[str, ...]  # paragraphs, already substituted with real numbers


@dataclass(frozen=True)
class ReportModel:
    """Everything the two renderers print, reconstructed from disk.

    Built only by :meth:`from_evidence`. There is deliberately no constructor
    taking a ``ComparisonReport``: see the module docstring and invariant 2.
    """

    verdict: str | None  # None when the run never reached a verdict
    reason: str | None
    decided_by: str | None
    generated: str  # RFC3339
    evidence_path: str
    evidence_hash: str
    tool_version: str
    rigor_version: str
    goldenset: Mapping[str, Any]
    baseline: RunSummary
    candidate: RunSummary
    judges: tuple[JudgeRow, ...]
    flips: tuple[FlipRow, ...]
    gains: tuple[FlipRow, ...]
    thresholds: Mapping[str, Any]
    threshold_sources: Mapping[str, str]
    hashes: Mapping[str, str]
    completeness: Completeness
    warnings: tuple[str, ...]
    #: Beyond the contract's list. ``unstable`` exists because build-plan §6 as
    #: amended requires an item that is a coin toss under *both* models to be
    #: named -- it is the single most interesting row in the report and the first
    #: implementation left it in no list at all.
    unstable: tuple[FlipRow, ...] = ()
    completion_rates: Mapping[str, Any] = field(default_factory=dict)
    item_counts: Mapping[str, Any] = field(default_factory=dict)
    n_per_item: int = 0
    max_output_chars: int = 4000
    #: What the document embedded against what it was allowed to. Never ``None``:
    #: a report that was not bounded says so, in the same place as one that was.
    detail: DetailBudget = field(
        default_factory=lambda: DetailBudget(
            limit=DEFAULT_MAX_REPORT_CHARS, embedded=0, rows=0, rows_embedded=0
        )
    )
    max_report_chars: int = DEFAULT_MAX_REPORT_CHARS
    config_path: str = ""
    command: str = ""
    #: Non-empty when ``--artifact-dir`` redirected the recorded artifact paths.
    #: Printed in the provenance block, because a report that quietly read
    #: different files than the ones recorded is worse than one that failed.
    artifact_dir: str = ""

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_evidence(
        cls,
        evidence: str | Path,
        *,
        goldenset: str | Path | None = None,
        artifact_dir: str | Path | None = None,
        max_output_chars: int = 4000,
        max_report_chars: int = DEFAULT_MAX_REPORT_CHARS,
        now: str | None = None,
    ) -> ReportModel:
        """Rebuild the report from an evidence log and the files it names.

        Args:
            evidence: Path to the ``.jsonl`` evidence log, or to a directory
                holding ``evidence.jsonl``.
            goldenset: Override for the recorded golden-set path, for the one real
                case where paths written on machine A do not resolve on machine B.
                The override is printed in the provenance block: a report that
                quietly read a different file than the one recorded is worse than
                one that failed. Required whenever the recorded path points
                outside the evidence log's own directory -- see :func:`_resolve`,
                which is where a shared log stopped being able to name any file it
                liked on the reviewer's machine.
            artifact_dir: Same, for the run and judged artifacts. Resolved by
                basename inside this directory.
            max_output_chars: Per output block. Truncation is always marked.
            max_report_chars: Characters of quoted model text the whole document
                may embed, across every change section. ``0`` or less means no
                bound, matching :func:`_truncate`'s convention for
                ``max_output_chars``. What it costs when it binds is disclosed in
                :attr:`detail`, in ``warnings``, in the terminal render and in a
                band above the change sections -- see :data:`DEFAULT_MAX_REPORT_CHARS`.
            now: Injected generation timestamp, so two renders can be compared
                byte for byte.

        Raises:
            ArtifactError: if the evidence file does not exist -- rigor reads a
                missing log as ``[]``, so a typo'd path would otherwise render as
                a blank "nothing happened" report -- or if the log contains no
                ``migkit.comparison`` record, in which case there is nothing to
                report *on*. Everything else degrades and is named in the
                completeness strip.
            ReportError: if the log records an artifact or golden-set path in a
                form this tool never writes -- a UNC share, a ``\\\\?\\`` device
                prefix, a ``..`` segment -- and no override was given. That is an
                edited log rather than a moved one, and nothing is opened before
                the refusal.
        """
        path = resolve_evidence(evidence)

        # One streaming pass, keeping three records and never the log. See
        # :func:`~model_migration_kit.evidence.stream_records`: the list-returning
        # read this replaced cost 5.0 to 5.8 times the log's own bytes, and the
        # evidence log is the largest artifact the pipeline writes.
        comparison = None
        verdict_record = None
        last = None
        for record in _stream_records(path):
            last = record
            if record.event_type == EVENT_COMPARISON:
                comparison = record
            elif record.event_type == EVENT_VERDICT:
                verdict_record = record
        if comparison is None:
            raise ArtifactError(
                f"{path} contains no {EVENT_COMPARISON} record, so there is nothing "
                f"to report on. A run that died before comparing produced evidence "
                f"of an attempt, not of a comparison."
            )
        payload: Mapping[str, Any] = comparison.payload
        verdict_payload: Mapping[str, Any] = (
            {} if verdict_record is None else verdict_record.payload
        )

        warnings: list[str] = [str(one) for one in payload.get("warnings", ())]
        missing: list[str] = []

        # Every path this reconstruction reads out of the log is resolved against
        # the log's own directory and may not leave it. See :func:`_resolve`.
        base_dir = path.parent
        gs_view = _load_goldenset(payload, goldenset, warnings, base_dir)
        latency = payload.get("latency", {}) or {}

        base_side = dict(payload.get("baseline", {}) or {})
        cand_side = dict(payload.get("candidate", {}) or {})
        base_run, base_judged = _load_side(
            base_side, artifact_dir, "baseline", warnings, base_dir
        )
        cand_run, cand_judged = _load_side(
            cand_side, artifact_dir, "candidate", warnings, base_dir
        )

        gs_size = gs_view["size"] if gs_view["available"] else None
        baseline = _run_summary(
            base_side, base_run, base_judged, latency.get("baseline"), gs_size
        )
        candidate = _run_summary(
            cand_side, cand_run, cand_judged, latency.get("candidate"), gs_size
        )
        for label, summary in (("baseline", baseline), ("candidate", candidate)):
            if summary.expected and summary.completions < summary.expected:
                missing.append(
                    f"{label} run has {summary.completions} of {summary.expected} completions"
                )
            elif not summary.expected:
                missing.append(
                    f"{label} run does not record how many completions were expected, "
                    f"so its shortfall cannot be measured"
                )

        judges = tuple(_judge_row(one) for one in payload.get("judges", ()))
        rows = _ChangeContext(
            goldenset=gs_view,
            base_run=base_run,
            cand_run=cand_run,
            cand_judged=cand_judged,
            limit=max_output_chars,
            order=gs_view["order"] if gs_view["available"] else {},
        )
        sections, detail = _change_sections(payload, rows, budget=max_report_chars)
        flips = sections["flips"]
        gains = sections["gains"]
        unstable = sections["unstable"]
        if detail.capped:
            # Disclosed three times over, in the same words: here, so a library
            # caller reading `warnings` sees it; in the terminal render; and in a
            # band above the change sections. A truncated report that does not say
            # it was truncated is worse than a large one.
            warnings.append(detail.sentence)

        if verdict_record is None:
            missing.append(
                "no migkit.verdict record: the run ended between the comparison and "
                "the verdict, so this report is evidence and not a decision"
            )

        observed = baseline.completions + candidate.completions
        expected = baseline.expected + candidate.expected
        completeness = Completeness(
            complete=bool(expected) and observed >= expected and not missing,
            observed_completions=observed,
            expected_completions=expected,
            missing=tuple(missing),
            last_event=None if last is None else last.event_type,
            last_ts=None if last is None else last.ts,
        )

        thresholds = dict(payload.get("thresholds", {}) or {})
        config_path = str(payload.get("config_path", "") or "")
        # Per-threshold provenance -- CLI flag versus config file versus built-in
        # default -- is not carried in the comparison payload (contract §1.2), and
        # deriving it would be exactly the invention this module refuses. The
        # config file the comparison recorded is the granularity the evidence has.
        source = config_path or THRESHOLD_SOURCE_UNRECORDED
        evidence_hash = hash_file(path)
        return cls(
            verdict=verdict_payload.get("verdict"),
            reason=verdict_payload.get("reason"),
            decided_by=verdict_payload.get("decided_by"),
            generated=now or utc_now(),
            evidence_path=str(path),
            evidence_hash=evidence_hash,
            tool_version=_tool_version(),
            rigor_version=str(getattr(opik_rigor, "__version__", "unknown")),
            goldenset=gs_view,
            baseline=baseline,
            candidate=candidate,
            judges=judges,
            flips=flips,
            gains=gains,
            unstable=unstable,
            thresholds=thresholds,
            threshold_sources={name: source for name in thresholds},
            hashes={
                "goldenset": str(payload.get("goldenset_hash", "") or ""),
                "judges": str(payload.get("judges_hash", "") or ""),
                "config": str(payload.get("config_hash", "") or ""),
                "evidence": evidence_hash,
            },
            completeness=completeness,
            warnings=tuple(warnings),
            completion_rates=dict(payload.get("completion_rates", {}) or {}),
            item_counts=dict(payload.get("item_counts", {}) or {}),
            n_per_item=int(payload.get("n_per_item", 0) or 0),
            max_output_chars=max_output_chars,
            detail=detail,
            max_report_chars=max_report_chars,
            config_path=config_path,
            command=str(payload.get("command", "") or ""),
            artifact_dir="" if artifact_dir is None else str(artifact_dir),
        )

    # -- derived ------------------------------------------------------------ #

    @property
    def is_demo(self) -> bool:
        """True when either side was produced by a ``Fake*`` adapter.

        Derived from the artifacts and never from a flag, so a hand-wired
        ``FakeAdapter`` run cannot produce a clean-looking report.
        """
        return self.baseline.is_fake or self.candidate.is_fake

    @property
    def exit_code(self) -> int:
        return Verdict.exit_code(self.verdict or Verdict.ERROR)

    @property
    def verdict_word(self) -> str:
        return self.verdict or _NO_VERDICT

    @property
    def verdict_reason(self) -> str:
        return self.reason or _NO_VERDICT_REASON


# --------------------------------------------------------------------------- #
# reconstruction helpers
# --------------------------------------------------------------------------- #


#: The reader now lives in :mod:`model_migration_kit.evidence`, one module below
#: this one, so that ``series`` can read a log without importing the renderer --
#: which it cannot do, since this module imports ``series``. The private name
#: stays because ``tests/test_evidence_scale.py`` measures the memory claim
#: through it, and renaming the thing a guard points at is how a guard quietly
#: stops guarding.
_stream_records = stream_records


def _tool_version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    try:
        return _version("model-migration-kit")
    except PackageNotFoundError:  # pragma: no cover - only in a non-installed tree
        return "unknown"


# --------------------------------------------------------------------------- #
# recorded paths: the one place this module opens a file the evidence named
# --------------------------------------------------------------------------- #

#: Both separators, always, on both platforms. A recorded path is a *string some
#: other machine wrote*, so ``pathlib`` is the wrong tool for inspecting it: on
#: POSIX, ``Path(r"C:\\Windows\\win.ini")`` is a single relative filename with no
#: separators at all, and every rule below would pass it.
_SEPARATORS = re.compile(r"[\\/]")

#: ``C:``, ``c:``. Matched textually for the same reason: on POSIX a drive letter
#: is not a drive letter to ``pathlib``, and this check has to survive a log
#: written on Windows and read on Linux.
_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def _segments(recorded: str) -> list[str]:
    """The recorded path split on either separator, empties dropped."""
    return [part for part in _SEPARATORS.split(recorded) if part]


def _basename(recorded: str) -> str:
    """The last segment, under either separator.

    ``Path(...).name`` is not used: it answers for the platform doing the reading
    rather than the platform that did the writing, so a Windows-recorded
    ``C:\\work\\baseline.jsonl`` read on Linux would come back whole and defeat
    the ``--artifact-dir`` override -- the one mechanism the cross-machine
    workflow depends on.
    """
    parts = _segments(recorded)
    return parts[-1] if parts else ""


def _tampered_form(recorded: str) -> str | None:
    """Why this recorded path may not be used verbatim, or None if it may.

    Only forms that ``model_migration_kit`` never writes: this build records the
    path it just wrote a file to, which is a plain relative or absolute path with
    no ``..`` in it and no share in front of it. Anything here therefore means the
    log was edited after the fact.
    """
    if recorded.startswith(("\\\\", "//")):
        # Covers the ``\\?\`` and ``\\.\`` device prefixes as well as a plain
        # share. Demonstrated: a recorded ``\\192.0.2.111\share\x.jsonl`` blocked
        # for 21 seconds attempting an SMB connection, and on Windows an SMB
        # connection to a host an attacker named hands over the reviewer's
        # NTLMv2 hash.
        return "it names a UNC share or a device path"
    if ".." in _segments(recorded):
        return "it contains a '..' segment"
    return None


def _contained(recorded: str, base_dir: Path) -> bool:
    """True if an absolute recorded path is inside the evidence log's directory.

    String comparison after ``abspath``/``normcase``, deliberately, and never
    ``Path.resolve()``: resolving touches the filesystem, and doing I/O to decide
    whether I/O is allowed is the bug this function exists to prevent.
    """
    if not _is_absolute(recorded):
        return True
    base = os.path.normcase(os.path.abspath(str(base_dir)))
    target = os.path.normcase(os.path.abspath(recorded))
    return target == base or target.startswith(base + os.sep)


def _is_absolute(recorded: str) -> bool:
    return bool(_DRIVE_RE.match(recorded)) or recorded.startswith(("/", "\\"))


def _resolve(
    recorded: str,
    override: str | Path | None,
    base_dir: Path,
    what: str,
) -> tuple[str, bool, str]:
    """Where to read ``what`` from: the path, whether an override applied, or a refusal.

    This module spends its whole length guaranteeing that the *document* it
    produces fetches nothing -- no webfont, no CDN, no ``@import`` -- and then, in
    its previous form, fetched whatever the evidence log named during
    *reconstruction*. Sharing an evidence log across machines is the designed
    workflow (see the module docstring), so the log is attacker-influenced input
    on the reviewer's machine, and it drove ``open()`` with no constraint at all.
    Demonstrated on this build: a recorded ``C:\\Windows\\win.ini`` was opened and
    parsed, and a recorded ``\\\\192.0.2.111\\share\\x.jsonl`` blocked for 21
    seconds attempting an outbound SMB connection.

    So exactly one directory is reachable from a recorded path: the one the
    evidence log itself lives in. Everything else needs ``--artifact-dir`` or
    ``--goldenset``, which is what those flags were already for, and which is
    already disclosed in the provenance block.

    Returns:
        ``(path, overridden, refusal)``. A non-empty ``refusal`` means the caller
        must not open anything and should record the sentence as a warning: an
        absolute path that no longer resolves is the *ordinary* consequence of
        moving a log to another machine, and a partial report is this module's
        answer to missing evidence, not an error.

    Raises:
        ReportError: if the recorded path has a form this tool never writes -- a
            UNC share, a device prefix, a ``..`` segment. That is an edited log
            rather than a moved one, and a change-control tool does not quietly
            paper over evidence somebody has been editing. An override is still
            honoured, because it replaces the directory outright and the recorded
            string is then used for its filename alone.
    """
    if not recorded:
        return "", False, ""
    if override is not None:
        return str(Path(override) / _basename(recorded)), True, ""
    tampered = _tampered_form(recorded)
    if tampered is not None:
        raise ReportError(
            f"the evidence log records {recorded!r} as the {what}, and {tampered}. "
            f"model-migration-kit never writes a path in that form, so this log has "
            f"been edited since it was written. Nothing was opened. If the file is "
            f"genuinely there, name its directory explicitly with --artifact-dir "
            f"(or --goldenset for the golden set) and the override will be printed "
            f"in the provenance block."
        )
    if not _contained(recorded, base_dir):
        return (
            "",
            False,
            f"the {what} is recorded as {recorded}, which is outside the directory "
            f"holding the evidence log; a path recorded on another machine is not "
            f"followed. Pass --artifact-dir (or --goldenset for the golden set) to "
            f"say where the file is now.",
        )
    if _is_absolute(recorded):
        return recorded, False, ""
    return str(base_dir.joinpath(*_segments(recorded))), False, ""


def _load_goldenset(
    payload: Mapping[str, Any],
    override: str | Path | None,
    warnings: list[str],
    base_dir: Path,
) -> dict[str, Any]:
    """Load the golden set, but only trust it if it is still the one that was run.

    Before any input text is shown, the set's content hash is recomputed and
    compared to the one recorded in the evidence. On a mismatch -- or if the file
    is gone -- ids, tags and both models' outputs still render and ``input`` is
    ``None`` with a visible band. Pairing today's file with last week's outputs
    would be a fabricated exhibit, and it would be indistinguishable from a real
    one.
    """
    recorded_hash = str(payload.get("goldenset_hash", "") or "")
    recorded_path = str(payload.get("goldenset_path", "") or "")
    # The override still names the file directly rather than a directory, which is
    # what ``--goldenset`` has always meant; only the *recorded* path is
    # constrained, because only the recorded path came out of the log.
    if override is not None:
        used, refusal = str(override), ""
    else:
        used, _, refusal = _resolve(recorded_path, None, base_dir, "golden set")
    view: dict[str, Any] = {
        "hash": recorded_hash,
        "path": used,
        "recorded_path": recorded_path,
        "overridden": override is not None,
        "available": False,
        "reason": "",
        "size": 0,
        "with_reference": 0,
        "untagged": 0,
        "tags": {},
        "current_hash": "",
        # Named ``by_id`` rather than ``items`` so that a template writing
        # ``goldenset.items`` cannot silently reach dict.items instead.
        "by_id": {},
        "order": {},
    }
    if refusal:
        view["reason"] = refusal
        warnings.append(refusal + " Item inputs are not shown.")
        return view
    if not used:
        view["reason"] = "the evidence log does not record where the golden set lived"
        warnings.append(
            "no golden-set path in the evidence, so item inputs are not shown. Pass "
            "--goldenset to point at the set that was run."
        )
        return view
    try:
        loaded = GoldenSet.load(used)
    except GoldenSetError as exc:
        view["reason"] = f"the golden set at {used} could not be read ({exc})"
        warnings.append(view["reason"] + "; item inputs are not shown.")
        return view
    if recorded_hash and loaded.hash != recorded_hash:
        view["current_hash"] = loaded.hash
        view["reason"] = (
            f"the golden set at {used} no longer matches the one that was run "
            f"({loaded.hash[:16]} now, {recorded_hash[:16]} then), so the inputs are "
            f"not shown. Pairing today's file with last week's outputs would be a "
            f"fabricated exhibit."
        )
        warnings.append(view["reason"])
        return view
    stats = loaded.stats()
    view.update(
        {
            "available": True,
            "size": int(stats["size"]),
            "with_reference": int(stats["with_reference"]),
            "untagged": int(stats["untagged"]),
            "tags": dict(stats["tags"]),
            "current_hash": loaded.hash,
            "by_id": {item.id: item for item in loaded},
            "order": {item.id: index for index, item in enumerate(loaded)},
        }
    )
    return view


def _load_side(
    side: Mapping[str, Any],
    artifact_dir: str | Path | None,
    label: str,
    warnings: list[str],
    base_dir: Path,
) -> tuple[RunArtifact | None, JudgedArtifact | None]:
    """The two artifacts one side named, or None with the reason recorded."""
    run_path, run_overridden, run_refusal = _resolve(
        str(side.get("artifact", "") or ""), artifact_dir, base_dir, f"{label} run artifact"
    )
    judged_path, judged_overridden, judged_refusal = _resolve(
        str(side.get("judged_artifact", "") or ""),
        artifact_dir,
        base_dir,
        f"{label} judged artifact",
    )
    for refusal in (run_refusal, judged_refusal):
        if refusal:
            warnings.append(refusal)
    run = (
        None
        if run_refusal
        else _load_artifact(RunArtifact, run_path, f"{label} run artifact", warnings)
    )
    judged = (
        None
        if judged_refusal
        else _load_artifact(JudgedArtifact, judged_path, f"{label} judged artifact", warnings)
    )
    if run_overridden or judged_overridden:
        warnings.append(
            f"{label} artifacts were read from {artifact_dir} rather than the paths "
            f"recorded in the evidence; the override is printed in the provenance block."
        )
    return run, judged


def _load_artifact(loader: Any, path: str, label: str, warnings: list[str]) -> Any | None:
    if not path:
        warnings.append(f"the evidence log records no path for the {label}.")
        return None
    try:
        return loader.load(path)
    except (ArtifactError, OSError) as exc:
        warnings.append(f"the {label} at {path!r} could not be read ({exc}).")
        return None


def _run_summary(
    side: Mapping[str, Any],
    run: RunArtifact | None,
    judged: JudgedArtifact | None,
    latency: Mapping[str, Any] | None,
    goldenset_size: int | None,
) -> RunSummary:
    """One side's counts, observed against expected, with nothing pro-rated.

    Every number here is counted off a file rather than derived: the run
    artifact's records when it is readable, the judged artifact's per-judge
    coverage when it is not, and zero -- printed as ``?`` -- when neither exists.
    Extrapolating a partial run up to its intended size is the one thing a
    completeness strip may never do.
    """
    n_per_item = int(side.get("n_per_item", 0) or 0)
    adapters = [str(one) for one in side.get("adapters", ()) or ()]
    adapter = str(side.get("adapter", "") or "")
    if run is not None:
        adapters = list(run.adapters) or adapters
        adapter = run.header.adapter or adapter
        items = len(run.counts())
        completions = len(run.completions)
        failures = len(run.failures())
        parts = run.parts
        expected_items = run.items_expected
    elif judged is not None:
        per_judge = _per_judge_counts(judged)
        items = len(per_judge["items"])
        completions = per_judge["completions"]
        failures = per_judge["imputed"]
        parts = int(side.get("run_parts") or side.get("parts", 1) or 1)
        expected_items = None
    else:
        items = 0
        completions = 0
        failures = 0
        parts = int(side.get("run_parts") or side.get("parts", 1) or 1)
        expected_items = None
    if expected_items is None and goldenset_size is not None:
        expected_items = goldenset_size
    expected = int(expected_items) * n_per_item if expected_items and n_per_item else 0
    stat = dict(latency or {})
    if len(adapters) > 1:
        adapter = ", ".join(adapters)
    return RunSummary(
        model_id=str(side.get("model_id", "") or ""),
        adapter=adapter,
        n_per_item=n_per_item,
        items=items,
        completions=completions,
        expected=expected,
        failures=failures,
        parts=parts,
        artifact_path=str(side.get("artifact", "") or ""),
        latency_median=_number(stat.get("median")),
        latency_p90=_number(stat.get("p90")),
    )


def _per_judge_counts(judged: JudgedArtifact) -> dict[str, Any]:
    """Completions a judged artifact attests to, counted under one judge.

    The fallback when the run artifact is gone. Records are counted per judge and
    the widest judge wins rather than summing across the panel, because two judges
    grading the same 60 completions are 120 records and 60 completions.
    """
    names = judged.judge_names()
    best = 0
    items: set[str] = set()
    imputed = 0
    for name in names:
        records = judged.for_judge(name)
        if len(records) > best:
            best = len(records)
            items = {one.item_id for one in records}
            imputed = sum(1 for one in records if one.imputed)
    return {"completions": best, "items": items, "imputed": imputed}


def _judge_row(raw: Mapping[str, Any]) -> JudgeRow:
    """One judge's row, every number lifted from the payload without arithmetic."""
    regression = raw.get("regression") or {}
    counts = raw.get("item_counts") or {}
    imputed = raw.get("imputed") or {}
    parse_failures = raw.get("parse_failures") or {}
    return JudgeRow(
        name=str(raw.get("name", "") or ""),
        model_id=str(raw.get("model_id", "") or ""),
        rubric_hash=str(raw.get("rubric_hash", "") or ""),
        baseline=RateStat.from_gate(raw.get("baseline") or {}),
        candidate=RateStat.from_gate(raw.get("candidate") or {}),
        p_value=_number(raw.get("p_value")),
        test_ran=str(raw.get("test_ran", "not-run") or "not-run"),
        regressed=_bool_or_none(raw.get("regressed")),
        floor_cleared=_bool_or_none(raw.get("floor_cleared")),
        underpowered=bool(raw.get("underpowered", False)),
        note=str(raw.get("note", "") or ""),
        alpha=_number(raw.get("alpha", regression.get("alpha"))),
        holm_threshold=_number(raw.get("holm_threshold")),
        runs_needed=None if raw.get("runs_needed") is None else int(raw["runs_needed"]),
        mw_powered=_bool_or_none(raw.get("mw_powered")),
        power=dict(raw.get("power") or {}),
        items_baseline=dict(counts.get("baseline") or {}),
        items_candidate=dict(counts.get("candidate") or {}),
        items=int(counts.get("items", 0) or 0),
        imputed_baseline=int(imputed.get("baseline", 0) or 0),
        imputed_candidate=int(imputed.get("candidate", 0) or 0),
        parse_failures_baseline=int(parse_failures.get("baseline", 0) or 0),
        parse_failures_candidate=int(parse_failures.get("candidate", 0) or 0),
    )


@dataclass(frozen=True)
class _ChangeContext:
    """Everything the flip/gain/unstable lists are built from, gathered once.

    Three lists are built from identical inputs, and passing seven positional
    arguments to each of them three times is how one of them ends up reading a
    different artifact than the other two.
    """

    goldenset: Mapping[str, Any]
    base_run: RunArtifact | None
    cand_run: RunArtifact | None
    cand_judged: JudgedArtifact | None
    limit: int
    order: Mapping[str, int]


def _change_sections(
    payload: Mapping[str, Any], context: _ChangeContext, *, budget: int
) -> tuple[dict[str, tuple[FlipRow, ...]], DetailBudget]:
    """The three change sections, and what embedding them cost against ``budget``.

    Built together rather than one call per section, because the budget is a
    property of the *document*: three independent builders would each have to be
    told what the other two had already spent, which is how one of them ends up
    spending it twice.

    The allocation rule, stated so a reader can predict the document from it:

    * Entries are put in golden-set order within each section first. Ordering by
      golden-set position is stable across runs; ordering by "severity" would need
      a magnitude the comparison does not produce, and inventing one here would be
      a statistic this module is not allowed to compute. Where the golden set is
      unavailable the payload's own order is kept.
    * Rows are then visited **round-robin** -- flips[0], gains[0], unstable[0],
      flips[1], ... -- so that no section can crowd out another. Flips are the
      items that stopped working and gains are the ones that started, and a rule
      that spent the whole budget on flips would make the document an argument
      rather than a measurement, which is the same reason gains are never netted
      against flips.
    * A row's quoted text is embedded **whole or not at all**. Half a row's draws
      would misrepresent the distribution the tool exists to show.
    * The **first row that does not fit stops embedding for the rest of the
      document**. Skipping it and embedding whichever later rows happen to be
      short would make the sample of quoted evidence a function of output length,
      and a reader could not say what the document contains without knowing how
      long every model's answers were.
    * ``budget <= 0`` means no bound, matching :func:`_truncate`'s convention.

    No row is dropped in either case: a row past the budget keeps its id, tags,
    judges and margins and loses only the quotations.
    """
    order = context.order
    ordered: dict[str, list[Mapping[str, Any]]] = {}
    for name in _CHANGE_SECTIONS:
        entries = [dict(one) for one in payload.get(name, ()) or ()]
        if order:
            entries.sort(key=lambda one: order.get(str(one.get("item_id", "") or ""), len(order)))
        ordered[name] = entries

    bounded = budget > 0
    spent = 0
    stopped = False
    built: dict[str, list[FlipRow]] = {name: [] for name in _CHANGE_SECTIONS}
    counts = {name: {"rows": len(ordered[name]), "embedded": 0, "chars": 0} for name in ordered}
    for index in range(max((len(one) for one in ordered.values()), default=0)):
        for name in _CHANGE_SECTIONS:
            entries = ordered[name]
            if index >= len(entries):
                continue
            if stopped:
                built[name].append(_change_row(entries[index], context, detail=False))
                continue
            row = _change_row(entries[index], context, detail=True)
            cost = row.quoted_chars
            if bounded and spent + cost > budget:
                # This row, and every row after it in every section. The row is
                # rebuilt without its quotations rather than kept and hidden: a
                # FlipRow carrying text the document does not show is a field two
                # renderers can disagree about.
                stopped = True
                built[name].append(_change_row(entries[index], context, detail=False))
                continue
            spent += cost
            counts[name]["embedded"] += 1
            counts[name]["chars"] += cost
            built[name].append(row)

    sections = {name: tuple(rows) for name, rows in built.items()}
    detail = DetailBudget(
        limit=max(budget, 0),
        embedded=spent,
        rows=sum(one["rows"] for one in counts.values()),
        rows_embedded=sum(one["embedded"] for one in counts.values()),
        sections={name: dict(one) for name, one in counts.items()},
    )
    return sections, detail


def _change_row(
    entry: Mapping[str, Any], context: _ChangeContext, *, detail: bool
) -> FlipRow:
    """One expandable row. ``detail=False`` builds it without opening an artifact.

    The cheap branch matters as much as the expensive one: past the budget there
    are potentially thousands of rows left, and building each one's outputs only to
    discard them would keep the memory cost this cap exists to remove.
    """
    items = context.goldenset["by_id"] if context.goldenset["available"] else {}
    limit = context.limit
    item_id = str(entry.get("item_id", "") or "")
    judges = tuple(str(one) for one in entry.get("judges", ()) or ())
    changes = entry.get("changes", ()) or ()
    labels = {
        str(one.get("judge", "")): str(one.get("label", ""))
        for one in changes
        if one.get("label")
    }
    item = items.get(item_id)
    tags = tuple(item.tags) if item is not None else ()
    if not detail:
        return FlipRow(
            item_id=item_id,
            tags=tags,
            input=None,
            baseline_outputs=(),
            candidate_outputs=(),
            judges=judges,
            reasons={},
            truncated=False,
            labels=labels,
            detail_embedded=False,
        )
    text, text_cut = _truncate(item.input, limit) if item is not None else (None, False)
    base_outputs, base_cut = _outputs(context.base_run, item_id, limit)
    cand_outputs, cand_cut = _outputs(context.cand_run, item_id, limit)
    reasons = _reasons(context.cand_judged, item_id, judges, limit)
    return FlipRow(
        item_id=item_id,
        tags=tags,
        input=text,
        baseline_outputs=base_outputs,
        candidate_outputs=cand_outputs,
        judges=judges,
        reasons=reasons,
        truncated=bool(text_cut or base_cut or cand_cut),
        labels=labels,
    )


def _outputs(
    run: RunArtifact | None, item_id: str, limit: int
) -> tuple[tuple[str, ...], bool]:
    """All n draws for one item, failures included and labelled as such.

    A failed completion has no text, and printing nothing for it would make the
    side that crashed look like the side that was not asked.
    """
    if run is None:
        return (), False
    out: list[str] = []
    truncated = False
    for completion in sorted(run.completions_for(item_id), key=lambda c: c.sample_index):
        if completion.output is None:
            out.append(f"[no output - {completion.error_type or 'error'}: {completion.error}]")
            continue
        text, cut = _truncate(completion.output, limit)
        truncated = truncated or cut
        out.append(text or "")
    return tuple(out), truncated


def _reasons(
    judged: JudgedArtifact | None, item_id: str, judges: Sequence[str], limit: int
) -> dict[str, str]:
    """The candidate-side sentence each judge wrote about this item.

    A failing reason is preferred over a passing one: the row exists because the
    item stopped working, and the reason a reader needs is the one attached to the
    draw that failed.
    """
    if judged is None:
        return {}
    reasons: dict[str, str] = {}
    for name in judges:
        chosen = ""
        for record in judged.for_judge(name):
            if record.item_id != item_id or not record.reason:
                continue
            if not record.passed:
                chosen = record.reason
                break
            chosen = chosen or record.reason
        if chosen:
            text, _ = _truncate(chosen, limit)
            reasons[name] = text or ""
    return reasons


def _truncate(text: str | None, limit: int) -> tuple[str | None, bool]:
    """Cut to ``limit`` characters and say so. Invisible truncation misquotes."""
    if text is None:
        return None, False
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _bool_or_none(value: Any) -> bool | None:
    return None if value is None else bool(value)


# --------------------------------------------------------------------------- #
# the methodology appendix -- generated from the model so it cannot go stale
# --------------------------------------------------------------------------- #


def methodology_sections(model: ReportModel) -> tuple[MethodologySection, ...]:
    """The SR 11-7 artifact, substituted with this run's actual numbers.

    Generated rather than pasted, and the difference is testable: a test can
    change a threshold and assert the appendix text changed. A hardcoded appendix
    passes a "contains the word Wilson" test forever, including after the
    confidence has been moved to 0.80.
    """
    thresholds = model.thresholds
    alpha = _number(thresholds.get("alpha"))
    confidence = thresholds.get("confidence")
    floor = thresholds.get("pass_rate_floor")
    effect = thresholds.get("min_detectable_effect")
    power = thresholds.get("power_target")
    fired = model.decided_by or ""

    tested: list[str] = []
    if model.is_demo:
        tested.append(
            "These numbers describe scripted responses, not a real provider. At "
            f"least one side of this comparison was produced by a Fake adapter "
            f"({model.baseline.adapter or 'unknown'} for the baseline, "
            f"{model.candidate.adapter or 'unknown'} for the candidate). The only "
            "real thing in this document is the machinery: the sampling, the "
            "judging, the statistics and the decision rules are the production "
            "paths, exercised end to end. The quality difference they measure was "
            "written into the script."
        )
    tested.append(
        f"{model.n_per_item or model.baseline.n_per_item} draws per item over "
        f"{model.goldenset.get('size') or model.baseline.items} items, giving "
        f"{model.baseline.observed} completions on the baseline "
        f"({model.baseline.model_id or 'unknown model'}) and "
        f"{model.candidate.observed} on the candidate "
        f"({model.candidate.model_id or 'unknown model'}), each shown as observed "
        f"over expected. A migration decision needs a distribution per item rather "
        f"than a single shot: one sample cannot separate 'the candidate is worse' "
        f"from 'the candidate was unlucky once'."
    )
    if model.judges:
        listed = "; ".join(
            f"{one.name} ({one.model_id or 'unknown'}, "
            f"rubric {one.rubric_hash[:16] or 'unrecorded'})"
            for one in model.judges
        )
        tested.append(f"{len(model.judges)} judge(s) graded both sides: {listed}.")
    else:
        tested.append(
            "No judge row survived into the evidence, so nothing in this report "
            "measures quality."
        )
    tested.append(
        "Item counts are reported in three states -- passing, failing and unstable "
        "-- and never as an item-level rate. An item passes at 80% or more of its "
        "draws and fails at 20% or less; between those it is unstable and is named "
        "rather than counted. A three-state classification does not reduce to one "
        "fraction without pushing the ambiguous items into one bucket or the "
        "other, and whichever bucket is chosen, the resulting number lies in that "
        "direction."
    )

    why_tests = [
        f"The pass rate is reported two ways because they answer different "
        f"questions and are not interchangeable. The printed interval is the "
        f"two-sided Wilson interval at {_pct(confidence)} confidence, which is the "
        f"range of rates compatible with what was observed. The gate uses the "
        f"one-sided Wilson lower bound against a floor of {_pct(floor)}: the "
        f"candidate must demonstrate that rate, not merely be consistent with it. "
        f"A reader who conflates the two will believe the gate is looser than it is."
    ]
    ran = sorted({one.test_ran for one in model.judges}) or ["not-run"]
    why_tests.append(
        f"The regression test is the Mann-Whitney U statistic, one-sided with "
        f"alternative='less' at alpha={_num(alpha)}, asking only whether the "
        f"candidate is stochastically smaller than the baseline. It is deliberately "
        f"one-tailed: an improvement is not a regression, and a two-tailed test "
        f"would spend half its power looking for one. What actually ran on this "
        f"evidence: {', '.join(ran)}."
    )
    if len(model.judges) > 1:
        why_tests.append(
            f"With {len(model.judges)} judges the family of regression tests is "
            f"corrected for multiplicity by Holm-Bonferroni before any p-value is "
            f"compared to alpha. Uncorrected, the false-alarm rate on two identical "
            f"models climbs from about 2% at one judge to about 9% at four, and this "
            f"tool's own acceptance contract requires identical models to produce GO."
        )

    nonparametric = [
        "Judge scores are a bounded 1-5 ordinal scale -- that is opik-rigor's own "
        "rubric contract, not a convention adopted here. The distance between 3 and "
        "4 is not the distance between 4 and 5, so a t-test's interval-scale "
        "assumption is not merely unmet, it is unmeetable. Ranks are the only thing "
        "this data supports, which is why the comparison is a rank test rather than "
        "a comparison of means.",
        "A completion that failed has no output and therefore no judge score. It is "
        "imputed at the rubric's minimum rather than dropped, because dropping it "
        "makes a model that crashes beat a model that answers badly: both post the "
        "same pass count, and the crasher's missing scores leave the rank test with "
        "nothing to notice.",
    ]

    review: list[str] = [
        f"REVIEW means the sample could not have detected the regression being "
        f"asked about -- not that the pass-rate floor is unreachable. The "
        f"configured minimum detectable effect is a {_pct(effect)} drop in pass "
        f"rate at {_pct(power)} power, and a sample that cannot reach that target "
        f"yields REVIEW and never GO."
    ]
    for one in model.judges:
        needed = one.power.get("n_required")
        observed = one.power.get("n_observed")
        if needed is None:
            continue
        review.append(
            f"Judge {one.name}: {observed} completions per side observed against "
            f"roughly {needed} required for that effect at that power, so this judge "
            f"is {'powered' if one.mw_powered else 'not powered'} for the question. "
            f"The requirement comes from a two-proportion normal approximation, "
            f"which is the right order of magnitude for the rank test that actually "
            f"decides `regressed` rather than an exact figure for it."
        )
    review.append(
        "REVIEW is never silently converted to GO. There is no path in the "
        "resolution from 'we cannot tell' back to 'ship it', and a partial report "
        "with no verdict record exits 3 rather than defaulting to anything."
    )

    rules = [
        ("rule 1", "any judge shows a Holm-corrected significant regression", "NO-GO"),
        (
            "rule 2",
            f"else any judge's one-sided lower bound misses the {_pct(floor)} floor "
            f"and rigor does not call that sample underpowered",
            "NO-GO",
        ),
        (
            "rule 3",
            f"else any judge misses the {_pct(floor)} floor while rigor reports the "
            f"sample underpowered",
            "REVIEW",
        ),
        (
            "rule 4",
            f"else any judge cannot detect a {_pct(effect)} drop at {_pct(power)} power",
            "REVIEW",
        ),
        ("rule 5", "else no judge regressed and every judge cleared the floor", "GO"),
    ]
    table = [
        "Precedence is strict: the first rule that fires decides, and NO-GO "
        "outranks REVIEW because a regression that reached significance was, for "
        "that question, powered enough."
    ]
    for name, condition, outcome in rules:
        marker = "  <-- fired on this run" if name == fired else ""
        table.append(f"{name}: {condition} -> {outcome}{marker}")
    if fired and fired not in {name for name, _, _ in rules}:
        table.append(f"Recorded as decided by {fired}, which is outside the table above.")

    not_this = [
        "This report contains no cost model, no longitudinal trend, and no claim "
        "about any item outside the golden set named in the provenance block. It "
        "compares two models on one fixed set of cases under one panel of judges, "
        "once.",
        "Latency is descriptive only and is never a gate. A migration that is 30ms "
        "slower per call is a product decision, not a quality regression, and "
        "putting latency behind the verdict would let a faster-but-worse model pass.",
        "Gains are shown and are never netted against flips. Two items that started "
        "working do not undo two that stopped, because the two that stopped are the "
        "ones a user will hit tomorrow.",
    ]

    return (
        MethodologySection("What was tested", tuple(tested)),
        MethodologySection("Why these tests", tuple(why_tests)),
        MethodologySection("Why nonparametric", tuple(nonparametric)),
        MethodologySection("What REVIEW means", tuple(review)),
        MethodologySection("The decision table", tuple(table)),
        MethodologySection("What this report is not", tuple(not_this)),
    )


# --------------------------------------------------------------------------- #
# formatting helpers, shared by both renderers
# --------------------------------------------------------------------------- #


def _num(value: float | None, digits: int = 4, dash: str = EM_DASH) -> str:
    if value is None:
        return dash
    return f"{value:.{digits}f}"


def _pct(value: Any, dash: str = EM_DASH) -> str:
    number = _number(value)
    if number is None:
        return dash
    return f"{number * 100:.1f}%"


def _interval(value: tuple[float, float] | None, dash: str = EM_DASH) -> str:
    if value is None:
        return dash
    return f"[{value[0]:.4f}, {value[1]:.4f}]"


def _flag(value: bool | None, dash: str = EM_DASH) -> str:
    if value is None:
        return dash
    return "yes" if value else "no"


def _latency_cell(summary: RunSummary) -> str:
    median = _num(summary.latency_median, 3, TERMINAL_DASH)
    p90 = _num(summary.latency_p90, 3, TERMINAL_DASH)
    return f"{median} / {p90}"


def _parts_phrase(summary: RunSummary, side: str) -> str:
    if summary.parts > 1:
        return f"{side} completed in {summary.parts} parts"
    return f"{side} completed in 1 part"


# --------------------------------------------------------------------------- #
# terminal rendering
# --------------------------------------------------------------------------- #

_VERDICT_STYLE = {
    Verdict.GO: "green",
    Verdict.NO_GO: "red",
    Verdict.REVIEW: "yellow",
}

#: Every C0 control character, plus DEL. Removed from anything the evidence
#: supplied before it reaches a terminal -- see :func:`_cell`.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _cell(value: object) -> Text:
    """Evidence-derived text as a renderable that cannot become anything else.

    Two defects in one function, both demonstrated against this build.

    A bare ``str`` handed to rich is parsed as *console markup*. A recorded
    ``model_id`` of ``fake-cand-v1[/]`` raised ``rich.errors.MarkupError`` and took
    the whole ``migkit report`` run down with it: exit 3, and no HTML file written
    even though ``--html`` was passed, because the terminal render runs first.
    Well-formed markup is worse than a crash. ``[bold red]FAKE CLEARED[/bold red]``
    rendered as styled text with the brackets gone, and
    ``[link=https://evil.example]click[/link]`` became a live hyperlink. This is a
    tool whose stated claim is that you cannot obtain a clean-looking report from
    scripted models, so a model id that renders as text of the attacker's choosing
    is a forgery vector rather than a cosmetic bug. ``_print_changes`` and the
    warnings loop already wrapped their strings in ``Text``; nothing else did, and
    the inconsistency was the defect.

    ``Text`` alone is not enough, which is the second half and the reason the
    already-wrapped sites are routed through here too: ESC passes through ``Text``
    unchanged -- verified -- so ``\\x1b[2J\\x1b[H`` anywhere in the payload clears
    the reviewer's screen and scrolls the final ``VERDICT:`` line out of view.
    Nothing in a model id, an adapter name, a path, a hash or a judge note needs a
    control character, so each one becomes a space rather than being deleted: the
    words either side of it stay separate words.
    """
    return Text(_CONTROL_RE.sub(" ", str(value)))


def render_terminal(model: ReportModel, *, console: Console | None = None) -> None:
    """Write the report to a terminal through one rich Console and nothing else.

    Never a bare ``print()`` of glyphs: rich degrades to ASCII box characters on
    its own when the target encoding cannot represent them, where ``print()``
    raises ``UnicodeEncodeError`` on a legacy Windows console -- and raises it
    after all the work is done.

    Colour is never the only carrier of a fact. Every verdict, flag and shortfall
    is a word as well as a colour, so a ``no_color`` render and a photocopy both
    still say what happened.

    **No evidence-derived string is ever handed to rich as a ``str``.** Everything
    that came out of the log, an artifact or a golden set goes through
    :func:`_cell`, which is where the reasoning lives. The module literals here --
    row labels, table titles, the closing sentence -- are the only bare strings
    left, and a future edit that adds a payload value as one is the thing the
    tests around this function are watching for.
    """
    out = console or Console()
    if model.is_demo:
        out.print(
            Panel(
                Text(
                    "FAKE MODELS - these numbers describe scripted responses, "
                    "not a real provider",
                    style="bold",
                ),
                border_style="red",
            )
        )
    verdict = model.verdict_word
    banner = Text.assemble(
        (_CONTROL_RE.sub(" ", verdict), "bold"),
        (f"  (exit {model.exit_code})\n", ""),
        (_CONTROL_RE.sub(" ", model.verdict_reason), ""),
    )
    out.print(
        Panel(
            banner,
            title="VERDICT",
            subtitle=_cell(f"decided by {model.decided_by or 'no recorded rule'}"),
            border_style=_VERDICT_STYLE.get(verdict, "white"),
        )
    )

    compared = Table(title="What was compared", show_header=True, header_style="bold")
    compared.add_column("")
    compared.add_column("baseline")
    compared.add_column("candidate")
    compared.add_row("model", _cell(model.baseline.model_id), _cell(model.candidate.model_id))
    compared.add_row(
        "adapter",
        _cell(model.baseline.adapter or TERMINAL_DASH),
        _cell(model.candidate.adapter or TERMINAL_DASH),
    )
    compared.add_row(
        "completions", _cell(model.baseline.observed), _cell(model.candidate.observed)
    )
    compared.add_row(
        "failed completions",
        str(model.baseline.failures),
        str(model.candidate.failures),
    )
    compared.add_row("parts", str(model.baseline.parts), str(model.candidate.parts))
    compared.add_row(
        "latency median / p90 (descriptive only, never a gate)",
        _latency_cell(model.baseline),
        _latency_cell(model.candidate),
    )
    out.print(compared)

    gs = model.goldenset
    facts = Table(show_header=False, box=None)
    facts.add_column("")
    facts.add_column("")
    facts.add_row(
        "golden set",
        _cell(f"{gs['path'] or TERMINAL_DASH} ({gs['hash'][:16] or TERMINAL_DASH})"),
    )
    facts.add_row("golden-set size", str(gs["size"]) if gs["available"] else "not available")
    facts.add_row("judges hash", _cell(model.hashes.get("judges", "")[:16] or TERMINAL_DASH))
    facts.add_row(
        "config",
        _cell(
            f"{model.config_path or TERMINAL_DASH} "
            f"({model.hashes.get('config', '')[:16] or TERMINAL_DASH})"
        ),
    )
    facts.add_row("n per item", str(model.n_per_item or TERMINAL_DASH))
    for name, value in model.thresholds.items():
        facts.add_row(
            _cell(f"threshold {name}"),
            _cell(f"{value} ({model.threshold_sources.get(name, THRESHOLD_SOURCE_UNRECORDED)})"),
        )
    out.print(facts)

    for judge in model.judges:
        table = Table(
            title=_cell(f"judge: {judge.name} ({judge.model_id or 'unknown'})"),
            show_header=True,
            header_style="bold",
        )
        table.add_column("")
        table.add_column("baseline")
        table.add_column("candidate")
        table.add_row(
            "passed / observed", _cell(judge.baseline.observed), _cell(judge.candidate.observed)
        )
        table.add_row(
            "pass rate",
            _pct(judge.baseline.rate, TERMINAL_DASH),
            _pct(judge.candidate.rate, TERMINAL_DASH),
        )
        table.add_row(
            "Wilson interval (two-sided)",
            _interval(judge.baseline.interval, TERMINAL_DASH),
            _interval(judge.candidate.interval, TERMINAL_DASH),
        )
        table.add_row(
            "Wilson lower bound (one-sided, the gate)",
            _num(judge.baseline.lower_bound, 4, TERMINAL_DASH),
            _num(judge.candidate.lower_bound, 4, TERMINAL_DASH),
        )
        table.add_row(
            "items passing / failing / unstable",
            _item_counts(judge.items_baseline, TERMINAL_DASH),
            _item_counts(judge.items_candidate, TERMINAL_DASH),
        )
        table.add_row(
            "p-value (alpha)",
            f"{_num(judge.p_value, 6, TERMINAL_DASH)} ({_num(judge.alpha, 3, TERMINAL_DASH)})",
            "",
        )
        table.add_row("test that ran", _cell(judge.test_ran), "")
        table.add_row(
            "regressed / floor cleared / underpowered",
            f"{_flag(judge.regressed, TERMINAL_DASH)} / "
            f"{_flag(judge.floor_cleared, TERMINAL_DASH)} / "
            f"{_flag(judge.underpowered, TERMINAL_DASH)}",
            "",
        )
        if judge.note:
            table.add_row("note", _cell(judge.note), "")
        out.print(table)

    _print_changes(out, "Flips (passing -> failing)", model.flips)
    _print_changes(out, "Gains (failing -> passing; never netted against flips)", model.gains)
    _print_changes(out, "Unstable items (a coin toss on one or both sides)", model.unstable)
    # Beside the tables it describes, because that is where it is actionable. It
    # prints in both states: "this document is complete" is a fact a reviewer
    # signing a migration decision should read rather than infer from silence.
    out.print(_cell(model.detail.sentence))

    if model.completeness.missing:
        strip = Table(title="Completeness", show_header=False, box=None)
        strip.add_column("")
        for sentence in model.completeness.missing:
            strip.add_row(_cell(sentence))
        strip.add_row(
            _cell(
                f"last event: {model.completeness.last_event or 'none'} at "
                f"{model.completeness.last_ts or 'unknown time'}"
            )
        )
        out.print(strip)
    for warning in model.warnings:
        if warning == model.detail.sentence:
            # Already printed beside the change tables above. It is in `warnings`
            # so that a library caller and the HTML warnings list both see it; a
            # terminal that says the same sentence twice teaches the reader to
            # skim the second one.
            continue
        out.print(_cell(f"warning: {warning}"))

    out.print(
        Text(
            f"Full outputs, the flip list and the methodology appendix are in the "
            f"HTML report; a terminal is not where anyone reads {model.n_per_item or 'n'} "
            f"pairs of model outputs."
        )
    )
    # Always the last line of stdout, including under --quiet: a CI log that
    # scrolls past 200 lines of table still ends with the finding.
    out.print(
        Text(
            f"VERDICT: {_CONTROL_RE.sub(' ', verdict)} (exit {model.exit_code})",
            style="bold",
        )
    )


def _item_counts(counts: Mapping[str, int], dash: str = EM_DASH) -> str:
    if not counts:
        return dash
    return (
        f"{counts.get('passing', 0)} / {counts.get('failing', 0)} / "
        f"{counts.get('unstable', 0)}"
    )


def _print_changes(console: Console, title: str, rows: Sequence[FlipRow]) -> None:
    """Ids and margins only. The full text is the HTML's job.

    A terminal is not where anyone reads twenty pairs of model outputs, and a
    renderer that tried would push the verdict off the top of the scrollback.
    """
    table = Table(title=f"{title}: {len(rows)}", show_header=True, header_style="bold")
    table.add_column("item")
    table.add_column("margin")
    table.add_column("judges")
    if not rows:
        table.add_row(Text("none"), Text(""), Text(""))
    for row in rows:
        margins = ", ".join(row.labels.get(name, "") for name in row.judges)
        # ``Text`` was already here; ``_cell`` adds the control-character strip,
        # because an item id read out of a golden set is as attacker-influenced as
        # a model id and ``Text`` does not touch ESC.
        table.add_row(_cell(row.item_id), _cell(margins), _cell(", ".join(row.judges)))
    console.print(table)


# --------------------------------------------------------------------------- #
# the interval bar -- a pure function, drawn so the geometry is the claim
# --------------------------------------------------------------------------- #

#: Breathing room in user units at each end of an interval bar. The x-axis maps
#: ``[0.0, 1.0]`` onto ``[INTERVAL_BAR_PAD, width - INTERVAL_BAR_PAD]``, so a rate
#: of 1.0 lands a stroke inside the viewport instead of half of it outside.
#: Exported rather than spelled ``8`` at the call sites because a test that
#: hard-codes the padding has stopped checking the projection and started
#: restating it; importing this keeps the test honest if the number ever moves.
INTERVAL_BAR_PAD = 8

#: The narrowest interval band that still puts ink on the canvas, in user units.
#: An SVG ``<rect>`` with ``width="0"`` is not rendered at all, so a degenerate
#: interval -- ``(0.5, 0.5)``, which a Wilson interval approaches as n grows --
#: would vanish rather than draw a hairline at the value it does have. An absent
#: interval and a zero-width one are different claims and must not arrive as the
#: same picture; the band's ``x`` stays the mapped ``interval[0]``, so the widening
#: is rightward and the contract's positioning rule is untouched.
INTERVAL_BAR_MIN_SPAN = 1.0

#: Decimals in every ``data-value``. R7 pins this at the *unmapped* float to
#: exactly six places: the attribute is the seam a test uses to watch the model's
#: number reach the drawing without re-deriving the projection, and a rounded or
#: projected one would make the check circular. The ``<title>`` deliberately does
#: *not* share the format -- it speaks percents, like every other number the
#: reader of the surrounding document sees. The machine seam and the human
#: sentence are two audiences, and one format cannot serve both.
_INTERVAL_BAR_PLACES = 6

#: The three not-recorded phrases, named rather than left as literals. R7's
#: argument for naming ``INTERVAL_BAR_PAD`` applies verbatim to a string a test
#: has to match: a test that guesses at the wording -- and C12's tester had to
#: accept nine spellings of the floor phrase -- cannot tell a deliberate rewording
#: from a bar that quietly started printing a number for a value nobody measured.
#: All three exist because the contract's rule for the floor ("an absent rule must
#: not read as a floor of zero") is not about floors; it is about absence.
INTERVAL_BAR_NO_RATE = "pass rate not recorded"
INTERVAL_BAR_NO_INTERVAL = "interval not recorded"
INTERVAL_BAR_NO_FLOOR = "floor not recorded"


def _is_number(value: Any) -> bool:
    """A real, finite number. ``bool``, ``None``, ``NaN`` and the infinities are not.

    Deliberately a second copy of ``comparison._is_number`` rather than an import
    of another module's private name. ``report._number`` is *not* this predicate:
    it widens an ``int``/``float`` out of an evidence record and is finiteness-blind
    on purpose, because the model fields it feeds carry their own handling. Nothing
    in this module tested finiteness before, and the interval bar is the first place
    that must.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _interval_bar_number(value: Any) -> float | None:
    """``value`` as a float, or ``None`` if it is not a number the bar may project.

    ``json.loads`` accepts a bare ``NaN``, so a malformed evidence log can put one
    in a pass rate and it arrives here intact. ``min(1.0, max(0.0, nan))`` is
    ``0.0`` -- a NaN rate would draw at ``INTERVAL_BAR_PAD``, pixel-identical to a
    rate of 0.0, with ``data-value="nan"`` beside it violating R7's six-place
    contract. That is the contract's own "silently wrong projection": a picture
    saying the run scored zero when nothing was measured.

    So a non-finite value renders as *not recorded* rather than being projected,
    and rather than being refused. Three reasons for degrading instead of raising.
    The module already answers an unusable number this way -- ``_number`` returns
    ``None`` and ``_pct`` prints an em dash. This function returns a ``str`` and has
    no channel for a warning, so a raise would take a whole document down over one
    bar. And "not recorded" is the *true* statement about a NaN, which is the line
    R5 draws: missing data stated as missing, never as zero.
    """
    return float(value) if _is_number(value) else None


def _interval_bar_clamp(value: float) -> float:
    """``value`` pulled into ``[0.0, 1.0]``.

    Cannot fire on today's inputs -- a pass rate and a Wilson interval are both
    fractions by construction. It is here because the failure it prevents is the
    quiet kind: an ``x`` of ``-40`` raises nothing, it draws off-canvas, and an
    element nobody can see reads as an element nobody drew.
    """
    return min(1.0, max(0.0, value))


def _interval_bar_x(value: float, width: int) -> float:
    """The one projection. Every element's geometry is computed through here.

    Four call sites each open-coding ``PAD + v * (width - 2 * PAD)`` is four
    places to drift, and the drift is invisible rather than loud: the band still
    renders, it just sits on the wrong side of the floor, and the spec says that
    relationship *is* the verdict.
    """
    return INTERVAL_BAR_PAD + _interval_bar_clamp(value) * (width - 2 * INTERVAL_BAR_PAD)


def _interval_bar_value(value: float) -> str:
    """The model's own number to six places -- not rounded, not projected.

    ``data-value`` exists so a test can assert that the number the model produced
    reached the drawing *without* re-deriving the projection, which is precisely
    the check a re-derivation would not make. Deliberately unclamped: if a caller
    ever hands over 1.4, the markup should say 1.400000 next to geometry pinned at
    the right edge, so the disagreement is legible instead of being erased here.
    """
    return f"{value:.{_INTERVAL_BAR_PLACES}f}"


def _interval_bar_coord(value: float) -> str:
    """A geometry number, at a resolution finer than anyone can see."""
    return f"{value:.3f}"


def _interval_bar_escape(text: str) -> str:
    """Element text, made safe to concatenate into markup.

    ``&<>`` only -- this goes between tags, never into an attribute. The control
    strip is the same one the terminal renderer applies: a label can come from a
    metric name lifted out of an evidence log, and ``ESC`` in a document is a
    finding in its own right.
    """
    stripped = _CONTROL_RE.sub(" ", text)
    return stripped.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def interval_bar_svg(
    *,
    rate: float | None,
    interval: tuple[float, float] | None,
    floor: float | None,
    width: int = 480,
    height: int = 44,
    label: str = "",
) -> str:
    """One ``<svg>`` element drawing a pass rate, its interval and its floor.

    Pure: no I/O, no globals, no model object, no template. Presentation is inline
    ``fill``/``stroke`` and geometry, so the result survives ``assert_self_contained``
    wherever it is embedded. Note the absent ``xmlns``: the value
    ``http://www.w3.org/2000/svg`` matches the URL-scheme rule in ``_UrlScanner``
    and would be reported as a fetching position even though no browser fetches
    it. Inline SVG in an HTML5 document does not need the declaration anyway.

    Each of the four missing-value states is a different picture, and the third is
    the one that matters. ``floor is None`` draws no line at all and says so in
    the title, because a rule that was never set, rendered as a floor of 0.0,
    makes the document claim a bar cleared a bar that does not exist. The same
    reasoning governs an absent rate and an absent interval, so all three get a
    named phrase (``INTERVAL_BAR_NO_RATE`` and friends) rather than a number.

    A non-finite ``rate``, ``floor`` or interval endpoint is treated as *not
    recorded* rather than projected -- see :func:`_interval_bar_number`, which
    carries the reasoning and the arithmetic that makes it necessary.

    Two number formats, on purpose. The ``<title>`` speaks percents through
    ``_pct``, matching everything else a reader of the surrounding document meets:
    the pass-rate cell, the interval cell and the banner's floor. ``data-value``
    keeps the unmapped six-place fraction R7 pins, because that is the machine
    seam. A screen-reader user should hear ``72.0%`` where the sighted reader sees
    ``72.0%``, not six zeros nobody else is shown.

    Args:
        rate: the point estimate as a fraction, or None if nothing was measured.
        interval: ``(low, high)`` as fractions, or None.
        floor: the gate's threshold as a fraction, or None if no rule was set.
        width: viewport width in user units.
        height: viewport height in user units.
        label: prefixed to the accessible title; may be empty or whitespace.

    Returns:
        One ``<svg>`` element as a string, on a single line.
    """
    rate = _interval_bar_number(rate)
    floor = _interval_bar_number(floor)
    if interval is not None:
        low_end = _interval_bar_number(interval[0])
        high_end = _interval_bar_number(interval[1])
        # Half an interval is not an interval. A band drawn from a good lower end
        # to a NaN upper end would claim a span nobody measured, which is the
        # failure this whole guard exists to prevent.
        interval = None if low_end is None or high_end is None else (low_end, high_end)

    rate_words = INTERVAL_BAR_NO_RATE if rate is None else f"pass rate {_pct(rate)}"
    if interval is None:
        interval_words = INTERVAL_BAR_NO_INTERVAL
    else:
        interval_words = f"interval {_pct(interval[0])} to {_pct(interval[1])}"
    floor_words = INTERVAL_BAR_NO_FLOOR if floor is None else f"floor {_pct(floor)}"
    sentence = f"{rate_words}, {interval_words}, {floor_words}"
    # Trimmed and control-stripped before the test for emptiness, because a label
    # of "  " -- or of a lone ESC lifted out of an evidence log -- would otherwise
    # open the accessible name with a bare " : " and announce nothing.
    named = _CONTROL_RE.sub(" ", label).strip()
    if named:
        sentence = f"{named}: {sentence}"

    # The title is unconditional -- it is the accessible name of a `role="img"`
    # element, and it is where the floor-was-never-recorded state is stated in
    # words. "Nothing else" in the all-None row of the contract is about drawn
    # elements; a document whose only picture had no accessible name would trade
    # one silent failure for another.
    parts = [f"<title>{_interval_bar_escape(sentence)}</title>"]

    if rate is None and interval is None and floor is None:
        parts.append(
            f'<text x="{_interval_bar_coord(width / 2)}" '
            f'y="{_interval_bar_coord(height / 2)}" text-anchor="middle" '
            f'dominant-baseline="middle" font-family="system-ui, sans-serif" '
            f'font-size="{_interval_bar_coord(height * 0.45)}" '
            f'fill="#4a5058">{EM_DASH}</text>'
        )
    else:
        if interval is not None:
            low = _interval_bar_x(interval[0], width)
            high = _interval_bar_x(interval[1], width)
            parts.append(
                f'<rect class="interval" x="{_interval_bar_coord(low)}" '
                f'y="{_interval_bar_coord(height * 0.34)}" '
                # The floor guards two ways of drawing nothing: a reversed pair,
                # whose negative `width` is invalid SVG, and a degenerate one,
                # whose `width="0"` an SVG renderer skips outright. `x` stays the
                # mapped `interval[0]`, exactly as the geometry contract says.
                f'width="{_interval_bar_coord(max(INTERVAL_BAR_MIN_SPAN, high - low))}" '
                f'height="{_interval_bar_coord(height * 0.32)}" '
                f'fill="#cfd4da" stroke="#7b838d" stroke-width="1" '
                f'data-value="{_interval_bar_value(interval[0])}" '
                f'data-value-upper="{_interval_bar_value(interval[1])}"/>'
            )
        if rate is not None:
            at_rate = _interval_bar_x(rate, width)
            parts.append(
                f'<line class="rate" x1="{_interval_bar_coord(at_rate)}" '
                f'y1="{_interval_bar_coord(height * 0.22)}" '
                f'x2="{_interval_bar_coord(at_rate)}" '
                f'y2="{_interval_bar_coord(height * 0.78)}" '
                f'stroke="#16191d" stroke-width="2" '
                f'data-value="{_interval_bar_value(rate)}"/>'
            )
        if floor is not None:
            at_floor = _interval_bar_x(floor, width)
            parts.append(
                f'<line class="floor" x1="{_interval_bar_coord(at_floor)}" '
                f'y1="{_interval_bar_coord(height * 0.10)}" '
                f'x2="{_interval_bar_coord(at_floor)}" '
                f'y2="{_interval_bar_coord(height * 0.90)}" '
                f'stroke="#a1141a" stroke-width="2" stroke-dasharray="3 2" '
                f'data-value="{_interval_bar_value(floor)}"/>'
            )

    return (
        f'<svg class="interval-bar" role="img" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet">'
        f"{''.join(parts)}</svg>"
    )


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #

#: The template lives in this module rather than in ``src/model_migration_kit/templates/``
#: so that ``report.py`` is one self-contained file; the Environment is otherwise
#: configured exactly as the contract specifies. ``select_autoescape`` keys off the
#: name, so the key must keep an ``.html`` suffix.
_TEMPLATE_NAME = "report.html"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
:root {
  color-scheme: light;
}
body {
  margin: 0;
  padding: 0 0 4rem 0;
  background: #ffffff;
  color: #16191d;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 16px;
  line-height: 1.5;
}
main {
  max-width: 60rem;
  margin: 0 auto;
  padding: 0 1.25rem;
}
h1, h2, h3 {
  line-height: 1.2;
}
h2 {
  margin-top: 2.5rem;
  border-bottom: 2px solid #d6dae0;
  padding-bottom: 0.3rem;
}
.band {
  padding: 0.9rem 1.25rem;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.band.fake {
  background: #a1141a;
  color: #ffffff;
}
.band.mismatch {
  background: #fbe9c8;
  color: #4a3400;
  border: 2px solid #a8760a;
  margin: 1rem 0;
}
.banner {
  margin: 1.25rem 0 0 0;
  padding: 1.25rem;
  border: 3px solid #4a5058;
  border-radius: 6px;
}
.banner .word {
  font-size: 2.6rem;
  font-weight: 800;
  margin: 0;
}
.banner .reason {
  margin: 0.4rem 0 0 0;
  font-size: 1.05rem;
}
.banner .meta {
  margin: 0.6rem 0 0 0;
  font-size: 0.9rem;
  color: #3d434a;
}
.banner.go {
  border-color: #1d6b32;
  background: #e9f6ec;
}
.banner.nogo {
  border-color: #a1141a;
  background: #fbeceb;
}
.banner.review {
  border-color: #8a6100;
  background: #fdf4e0;
}
.banner.none {
  border-color: #4a5058;
  background: #f0f2f4;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.75rem 0 1.25rem 0;
  font-size: 0.95rem;
}
th, td {
  border: 1px solid #cfd4da;
  padding: 0.4rem 0.6rem;
  text-align: left;
  vertical-align: top;
}
th {
  background: #f0f2f4;
}
td.num {
  font-variant-numeric: tabular-nums;
}
dl.facts {
  display: grid;
  grid-template-columns: minmax(11rem, 18rem) 1fr;
  gap: 0.35rem 1rem;
  margin: 0.75rem 0;
}
dl.facts dt {
  font-weight: 600;
}
dl.facts dd {
  margin: 0;
}
code, pre, .hash {
  font-family: ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;
  font-size: 0.88em;
}
pre.output {
  white-space: pre-wrap;
  word-wrap: break-word;
  background: #f6f7f9;
  border: 1px solid #d6dae0;
  border-left: 4px solid #7b838d;
  padding: 0.6rem 0.75rem;
  margin: 0.35rem 0;
}
details {
  border: 1px solid #cfd4da;
  border-radius: 4px;
  margin: 0.5rem 0;
  padding: 0.35rem 0.75rem;
  background: #fbfcfd;
}
summary {
  cursor: pointer;
  font-weight: 600;
  padding: 0.25rem 0;
}
.tag {
  display: inline-block;
  background: #e6eaee;
  border-radius: 3px;
  padding: 0 0.35rem;
  margin-right: 0.25rem;
  font-size: 0.85em;
}
.note, .warning {
  font-size: 0.92rem;
  color: #46301b;
  background: #fdf4e0;
  border-left: 4px solid #a8760a;
  padding: 0.5rem 0.75rem;
  margin: 0.5rem 0;
}
.secondary {
  color: #4a5058;
  font-size: 0.92rem;
}
.truncated {
  color: #7a2f00;
  font-weight: 600;
  font-size: 0.88rem;
}
nav ol {
  padding-left: 1.2rem;
}
footer {
  margin-top: 3rem;
  border-top: 2px solid #d6dae0;
  padding-top: 1rem;
  font-size: 0.9rem;
}
@media print {
  body {
    font-size: 11pt;
  }
  details {
    break-inside: avoid;
  }
}
</style>
</head>
<body>
{% if model.is_demo %}
<div class="band fake" id="fake-models" role="alert">
FAKE MODELS {{ dash }} these numbers describe scripted responses, not a real provider
</div>
{% endif %}
<main>
<section class="banner {{ verdict_class }}" id="verdict">
  <p class="word">{{ model.verdict_word }}</p>
  <p class="reason">{{ model.verdict_reason }}</p>
  <p class="meta">
    Exit code a CI system would have received: <strong>{{ model.exit_code }}</strong>
    {{ dash }} decided by {{ model.decided_by or 'no recorded rule' }}
    {{ dash }} generated {{ generated }}
  </p>
</section>

<nav>
  <ol>
    <li><a href="#compared">What was compared</a></li>
    <li><a href="#judges">Per-judge results</a></li>
    <li><a href="#latency">Latency (descriptive only)</a></li>
    <li><a href="#flips">Flips</a></li>
    <li><a href="#gains">Gains</a></li>
    <li><a href="#unstable">Unstable items</a></li>
    <li><a href="#appendix">Methodology appendix</a></li>
    <li><a href="#provenance">Provenance</a></li>
  </ol>
</nav>

{% if not model.completeness.complete %}
<div class="note" id="completeness">
  <strong>This report is partial.</strong> Counts are observed against expected
  everywhere below; nothing is imputed, pro-rated or extrapolated.
  <ul>
    {% for sentence in model.completeness.missing %}
    <li>{{ sentence }}</li>
    {% endfor %}
    <li>last event in the log: {{ model.completeness.last_event or 'none' }}
        at {{ model.completeness.last_ts or 'an unrecorded time' }}</li>
  </ul>
</div>
{% endif %}

<h2 id="compared">What was compared</h2>
<dl class="facts">
  <dt>baseline model</dt><dd>{{ model.baseline.model_id or dash }}
      <span class="secondary">adapter {{ model.baseline.adapter or dash }}</span></dd>
  <dt>candidate model</dt><dd>{{ model.candidate.model_id or dash }}
      <span class="secondary">adapter {{ model.candidate.adapter or dash }}</span></dd>
  <dt>golden set</dt><dd><code>{{ model.goldenset.path or dash }}</code>
      <span class="hash">{{ model.hashes.goldenset or dash }}</span>
      {% if model.goldenset.available %}
      {{ dash }} {{ model.goldenset.size }} items,
      {{ model.goldenset.with_reference }} with a reference,
      {{ model.goldenset.untagged }} untagged
      {% else %}
      {{ dash }} <em>not available: {{ model.goldenset.reason }}</em>
      {% endif %}</dd>
  <dt>tag distribution</dt><dd>
      {% if model.goldenset.tags %}
        {% for tag, count in model.goldenset.tags.items() %}
        <span class="tag">{{ tag }}: {{ count }}</span>
        {% endfor %}
      {% else %}{{ dash }}{% endif %}</dd>
  <dt>judges hash</dt><dd><span class="hash">{{ model.hashes.judges or dash }}</span></dd>
  <dt>config</dt><dd><code>{{ config_path or dash }}</code>
      <span class="hash">{{ model.hashes.config or dash }}</span></dd>
  <dt>n per item</dt><dd>{{ model.n_per_item or dash }}</dd>
  <dt>parts per run</dt><dd>{{ baseline_parts }}; {{ candidate_parts }}</dd>
  <dt>completions observed / expected</dt>
      <dd>baseline {{ model.baseline.observed }}; candidate {{ model.candidate.observed }}</dd>
  <dt>failed completions</dt>
      <dd>baseline {{ model.baseline.failures }}; candidate {{ model.candidate.failures }}</dd>
  <dt>run artifacts</dt><dd><code>{{ model.baseline.artifact_path or dash }}</code><br>
      <code>{{ model.candidate.artifact_path or dash }}</code></dd>
</dl>

<h3>Thresholds in force</h3>
<table>
  <thead><tr><th>threshold</th><th>value</th><th>source</th></tr></thead>
  <tbody>
  {% for name, value in model.thresholds.items() %}
    <tr><td>{{ name }}</td><td class="num">{{ value }}</td>
        <td>{{ model.threshold_sources.get(name, unrecorded) }}</td></tr>
  {% endfor %}
  {% if not model.thresholds %}
    <tr><td colspan="3">no thresholds recorded in the evidence</td></tr>
  {% endif %}
  </tbody>
</table>
<p class="secondary">
  Every threshold above is echoed from the evidence record that produced the
  verdict, so a loosened gate cannot be hidden. Which of CLI flag, config file or
  built-in default set any individual number is not carried in the evidence
  payload; where it says {{ unrecorded }}, that is a gap in the record and not a
  claim about the default.
</p>

<h2 id="judges">Per-judge results</h2>
<p class="secondary">
  Item counts are given in three states and never as an item-level rate: an item
  passes at 80% or more of its draws, fails at 20% or less, and is unstable in
  between. Ten items each passing 3 of 5 draws are neither ten passing items nor
  ten failing ones.
</p>
{% for judge in model.judges %}
<h3 id="judge-{{ loop.index }}">{{ judge.name }}</h3>
<p class="secondary">model <code>{{ judge.model_id or dash }}</code>
   {{ dash }} rubric <span class="hash">{{ judge.rubric_hash or dash }}</span></p>
<table>
  <thead><tr><th></th><th>baseline</th><th>candidate</th></tr></thead>
  <tbody>
    <tr><td>passed / observed completions</td>
        <td class="num">{{ judge.baseline.observed }}</td>
        <td class="num">{{ judge.candidate.observed }}</td></tr>
    <tr><td>pass rate</td>
        <td class="num">{{ judge.baseline.rate | pct }}</td>
        <td class="num">{{ judge.candidate.rate | pct }}</td></tr>
    <tr><td>Wilson interval, two-sided (for printing)</td>
        <td class="num">{{ judge.baseline.interval | interval }}</td>
        <td class="num">{{ judge.candidate.interval | interval }}</td></tr>
    <tr><td>Wilson lower bound, one-sided (the number the gate used)</td>
        <td class="num">{{ judge.baseline.lower_bound | num }}</td>
        <td class="num">{{ judge.candidate.lower_bound | num }}</td></tr>
    <tr><td>items passing / failing / unstable</td>
        <td class="num">{{ judge.items_baseline | counts }}</td>
        <td class="num">{{ judge.items_candidate | counts }}</td></tr>
    <tr><td>imputed (failed completions scored at the floor)</td>
        <td class="num">{{ judge.imputed_baseline }}</td>
        <td class="num">{{ judge.imputed_candidate }}</td></tr>
    <tr><td>judge parse failures (excluded from the rate)</td>
        <td class="num">{{ judge.parse_failures_baseline }}</td>
        <td class="num">{{ judge.parse_failures_candidate }}</td></tr>
    <tr><td>Mann-Whitney p-value (alpha {{ judge.alpha | num3 }}
        {%- if judge.holm_threshold is not none %},
        Holm threshold {{ judge.holm_threshold | num }}{% endif %})</td>
        <td class="num" colspan="2">{{ judge.p_value | num6 }}</td></tr>
    <tr><td>test that actually ran</td>
        <td colspan="2"><code>{{ judge.test_ran }}</code></td></tr>
    <tr><td>regressed / floor cleared / underpowered</td>
        <td colspan="2">{{ judge.regressed | flag }} / {{ judge.floor_cleared | flag }} /
            {{ judge.underpowered | flag }}</td></tr>
    <tr><td>powered for the configured effect</td>
        <td colspan="2">{{ judge.mw_powered | flag }}{% if judge.power.get('n_required') %}
            ({{ judge.power.get('n_observed') }} observed per side, roughly
            {{ judge.power.get('n_required') }} required){% endif %}</td></tr>
    {% if judge.runs_needed is not none %}
    <tr><td>runs rigor says would clear the floor</td>
        <td colspan="2" class="num">{{ judge.runs_needed }}</td></tr>
    {% endif %}
    {% if judge.note %}
    <tr><td>note</td><td colspan="2">{{ judge.note }}</td></tr>
    {% endif %}
  </tbody>
</table>
{% endfor %}
{% if not model.judges %}
<p class="note">No judge rows are recorded in this evidence log, so nothing here
measures quality.</p>
{% endif %}

<h2 id="latency">Latency</h2>
<p class="secondary"><strong>Descriptive only. Latency is never a gate</strong>
{{ dash }} a migration that is slower per call is a product decision, not a
quality regression.</p>
<table>
  <thead><tr><th></th><th>median (s)</th><th>p90 (s)</th></tr></thead>
  <tbody>
    <tr><td>baseline</td><td class="num">{{ model.baseline.latency_median | num3 }}</td>
        <td class="num">{{ model.baseline.latency_p90 | num3 }}</td></tr>
    <tr><td>candidate</td><td class="num">{{ model.candidate.latency_median | num3 }}</td>
        <td class="num">{{ model.candidate.latency_p90 | num3 }}</td></tr>
  </tbody>
</table>

{% if not model.goldenset.available %}
<div class="band mismatch" id="goldenset-mismatch">
  Item inputs are not shown: {{ model.goldenset.reason }}
</div>
{% endif %}

{% if model.detail.capped %}
<div class="note" id="detail-budget">
  <strong>The quoted model text in this report is bounded.</strong>
  {{ model.detail.sentence }}
  <ul>
    <li>rows are visited round-robin across flips, gains and unstable, in
        golden-set order within each, so no section crowds out another</li>
    <li>a row's quotations are embedded whole or not at all, and the first row that
        did not fit stopped embedding for the rest of the document</li>
    <li>every changed item is still listed, with its id, tags, judges and margins;
        no row was dropped and no count is affected</li>
  </ul>
</div>
{% else %}
<p class="secondary" id="detail-budget">{{ model.detail.sentence }}</p>
{% endif %}

<h2 id="flips">Flips {{ dash }} items that stopped working ({{ model.flips | length }})</h2>
{{ changes(model.flips) }}

<h2 id="gains">Gains {{ dash }} items that started working ({{ model.gains | length }})</h2>
<p class="secondary">
  Shown because their absence would make this report an argument rather than a
  measurement. <strong>An improvement here does not offset a regression above.</strong>
  The items that stopped working are the ones a user will hit tomorrow, and
  netting the two lists is how a bad migration ships.
</p>
{{ changes(model.gains) }}

<h2 id="unstable">Unstable items ({{ model.unstable | length }})</h2>
<p class="secondary">
  An item that neither passes at 80% of its draws nor fails at 20% of them, on one
  or both sides. Listed even when nothing moved: an item sitting at 3 of 5 under
  both models is the row whose verdict is a coin toss on both sides of the
  migration, and no rerun will agree with this one.
</p>
{{ changes(model.unstable) }}

<h2 id="appendix">Methodology appendix</h2>
{% for section in sections %}
<h3>{{ section.heading }}</h3>
{% for paragraph in section.body %}
<p>{{ paragraph }}</p>
{% endfor %}
{% endfor %}

{% if model.warnings %}
<h3>Warnings recorded during the comparison</h3>
<ul>
{% for warning in model.warnings %}
<li>{{ warning }}</li>
{% endfor %}
</ul>
{% endif %}

<footer id="provenance">
<h2>Provenance</h2>
<dl class="facts">
  <dt>model-migration-kit</dt><dd>{{ model.tool_version }}</dd>
  <dt>opik-rigor</dt><dd>{{ model.rigor_version }}</dd>
  <dt>evidence log</dt><dd><code>{{ model.evidence_path }}</code></dd>
  <dt>evidence hash</dt><dd><span class="hash">{{ model.hashes.evidence }}</span></dd>
  <dt>golden-set hash</dt><dd><span class="hash">{{ model.hashes.goldenset or dash }}</span></dd>
  <dt>judges hash</dt><dd><span class="hash">{{ model.hashes.judges or dash }}</span></dd>
  <dt>config hash</dt><dd><span class="hash">{{ model.hashes.config or dash }}</span></dd>
  <dt>command recorded</dt>
      <dd><code>{{ model.command or 'not recorded in the evidence' }}</code></dd>
  {% if model.goldenset.overridden %}
  <dt>golden-set override</dt>
      <dd>read from <code>{{ model.goldenset.path }}</code> rather than the recorded
          <code>{{ model.goldenset.recorded_path or dash }}</code></dd>
  {% endif %}
  {% if model.artifact_dir %}
  <dt>artifact override</dt>
      <dd>run and judged artifacts were read by basename from
          <code>{{ model.artifact_dir }}</code> rather than from the paths recorded
          above</dd>
  {% endif %}
  <dt>generated</dt><dd>{{ generated }}</dd>
</dl>
</footer>
</main>
</body>
</html>
"""

_CHANGES_MACRO = """
{% macro changes(rows) %}
{% if not rows %}
<p class="secondary">None.</p>
{% else %}
{% for row in rows %}
<details>
  <summary>{{ row.summary }}</summary>
  <div>
    {% if not row.detail_embedded %}
    <p class="truncated">Outputs not embedded: this document's budget for quoted
    model text ({{ max_report_chars }} characters) was reached before this row.</p>
    <p class="secondary">The row itself is not abridged {{ dash }} the item, its tags,
    the judges it changed under and the margin each of them recorded are above, and
    nothing was dropped from any count. What is missing is the quotations, and they
    are in the run artifacts named in the provenance block. Raise
    <code>[report] max_report_chars</code> to embed more.</p>
    <h4>Candidate-side judge reasons</h4>
    <dl class="facts">
    {% for name in row.judges %}
      <dt>{{ name }} {{ row.labels.get(name, '') }}</dt>
      <dd>not embedded</dd>
    {% endfor %}
    </dl>
    {% else %}
    {% if row.input is not none %}
    <h4>Input</h4>
    <pre class="output">{{ row.input }}</pre>
    {% else %}
    <p class="secondary">Input not shown: the golden set is unavailable or has changed.</p>
    {% endif %}
    <h4>Baseline outputs ({{ row.baseline_outputs | length }})</h4>
    {% for text in row.baseline_outputs %}
    <pre class="output">{{ text }}</pre>
    {% endfor %}
    {% if not row.baseline_outputs %}
    <p class="secondary">No baseline outputs available.</p>
    {% endif %}
    <h4>Candidate outputs ({{ row.candidate_outputs | length }})</h4>
    {% for text in row.candidate_outputs %}
    <pre class="output">{{ text }}</pre>
    {% endfor %}
    {% if not row.candidate_outputs %}
    <p class="secondary">No candidate outputs available.</p>
    {% endif %}
    <h4>Candidate-side judge reasons</h4>
    <dl class="facts">
    {% for name in row.judges %}
      <dt>{{ name }} {{ row.labels.get(name, '') }}</dt>
      <dd>{{ row.reasons.get(name, 'no reason recorded') }}</dd>
    {% endfor %}
    </dl>
    {% if row.truncated %}
    <p class="truncated">{{ ellipsis }} truncated at {{ max_output_chars }} characters</p>
    {% endif %}
    {% endif %}
  </div>
</details>
{% endfor %}
{% endif %}
{% endmacro %}
"""


def _environment() -> Environment:
    """jinja2, with the two defaults that matter turned off.

    ``autoescape`` is ``False`` by default in jinja2 3.1.6. Model outputs are
    arbitrary attacker-influenced text, and an output containing
    ``<img src="https://tracker/x.png">`` becomes a real network fetch in an
    unescaped template -- which is why escaping and the URL detector are both
    required, and neither substitutes for the other.

    ``StrictUndefined`` catches a different failure: a renamed model field would
    otherwise render an empty verdict banner rather than raising, and an empty
    banner is a document that says nothing while looking complete.
    """
    env = Environment(
        loader=DictLoader({_TEMPLATE_NAME: _CHANGES_MACRO + _TEMPLATE}),
        autoescape=select_autoescape(default_for_string=True, default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = lambda value: _pct(value)
    env.filters["num"] = lambda value: _num(_number(value), 4)
    env.filters["num3"] = lambda value: _num(_number(value), 3)
    env.filters["num6"] = lambda value: _num(_number(value), 6)
    env.filters["interval"] = _interval
    env.filters["flag"] = _flag
    env.filters["counts"] = _item_counts
    return env


def render_html_string(
    model: ReportModel, *, now: str | None = None, title: str | None = None
) -> str:
    """The whole report as one self-contained HTML document.

    Deterministic: with the same model and the same ``now`` the bytes are
    identical, so a test can render twice and diff, and a reviewer can re-render a
    stored evidence log and get the file they were sent.
    """
    generated = now or model.generated
    heading = title or _default_title(model)
    template = _environment().get_template(_TEMPLATE_NAME)
    return template.render(
        model=model,
        title=heading,
        generated=generated,
        verdict_class=_VERDICT_CLASS.get(model.verdict_word, "none"),
        sections=methodology_sections(model),
        dash=EM_DASH,
        ellipsis="…",
        unrecorded=THRESHOLD_SOURCE_UNRECORDED,
        config_path=model.config_path,
        baseline_parts=_parts_phrase(model.baseline, "baseline"),
        candidate_parts=_parts_phrase(model.candidate, "candidate"),
        max_output_chars=model.max_output_chars,
        max_report_chars=model.max_report_chars,
    )


def render_html(
    model: ReportModel,
    out: str | Path,
    *,
    now: str | None = None,
    title: str | None = None,
) -> Path:
    """Render and write, self-containment checked *before* anything is written.

    A template edit that adds a font link fails the render rather than shipping a
    file that only CI notices. The cost is one parse per report.

    Written with ``encoding="utf-8", newline="\\n"`` explicitly: on Windows
    ``Path.write_text`` defaults to the ANSI code page, which mangles or refuses
    non-ASCII model output, and CRLF would make the file's hash differ per
    platform -- the same reason ``.gitattributes`` forces LF.
    """
    target = Path(out)
    html = render_html_string(model, now=now, title=title)
    assert_self_contained(html, source=str(target))
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(html)
    return target


_VERDICT_CLASS = {
    Verdict.GO: "go",
    Verdict.NO_GO: "nogo",
    Verdict.REVIEW: "review",
    _NO_VERDICT: "none",
}


def _default_title(model: ReportModel) -> str:
    base = model.baseline.model_id or "baseline"
    cand = model.candidate.model_id or "candidate"
    head = f"{model.verdict_word} {EM_DASH} {base} to {cand} {EM_DASH} model-migration-kit"
    if model.is_demo:
        return f"FAKE MODELS {EM_DASH} {head}"
    return head
