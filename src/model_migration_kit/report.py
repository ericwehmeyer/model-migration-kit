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

**A verdict is read as belonging to the comparison it follows.** The headline
verdict and :attr:`ReportModel.series` are two reductions of one log, and if they
disagree the document contradicts itself about which night a NO-GO belongs to.
They agree by construction rather than by coincidence: in
:meth:`ReportModel.from_evidence` a ``migkit.comparison`` record *clears* the
verdict slot as well as filling the comparison slot, so the headline can only
ever carry a verdict written after the comparison it is printed beside, and
:class:`~model_migration_kit.series.SeriesBuilder` gives every verdict to the most
recently opened point, so ``series[-1]`` is the headline run. Kept as two
independent last-wins variables -- which is what 0.1.1 does -- a verdict from an
earlier night fills the slot of a night that died before deciding, and a crashed
run renders as a clean GO with ``complete is True`` and exit 0, because
``cli.py`` derives the exit code from the verdict alone. Under this rule that run
renders with no verdict, exit 3, and a line in the completeness strip naming the
absent ``migkit.verdict`` record. ``compare`` writes the two records back to back
(``comparison.py:907-908``), so no *complete* log renders differently for any of
this; what changed is what a crashed one renders.

**What "by construction" covers, and what it does not.** One pass over one record
stream: both reductions see every record and select the same two -- the last
``migkit.comparison``, and the last ``migkit.verdict`` after it -- so they cannot
disagree about *which* run the document is about or *which* decision it took.
They read that verdict's two fields through different coercions, though.
:meth:`ReportModel.from_evidence` takes ``payload.get("verdict")`` raw, while
:func:`~model_migration_kit.series.run_point` puts the same key through ``str``.
On every log ``compare`` writes the value is already a string and the two are one
read, but a payload carrying a non-string ``verdict`` -- a hand-edited log, a
future or older writer -- puts the banner and ``series[-1]`` back into
disagreement, and an unhashable one makes :attr:`ReportModel.exit_code` raise
where the series would have rendered a blank row. Unifying the two reads changes
what a *complete* log renders, which C19 is forbidden to do, so it is a chunk of
its own. Recorded here so the paragraph above is read as the claim it is: about
*which record* the two halves agree on, not about how that record is decoded.

**Two processes appending to one evidence log is the shape no pairing rule reads
correctly, and this one does not detect it.** rigor's log interleaves whole
records rather than tearing them, and ``cli.DEFAULT_EVIDENCE`` makes one shared
path the default, so ``C_A C_B V_A V_B`` and ``C_A C_B V_B V_A`` are equally
producible and nothing in either record says which comparison a verdict came back
to. Detecting the shape and refusing was considered at C19's review and rejected,
for a reason worth writing down rather than reopening: the only observable
signature of the interleave -- two comparisons standing before either verdict --
is *also* the signature of the crashed night this chunk exists to render
correctly, so a detector keyed on it would refuse the one log C19 was written to
read, and turn a report into no report on the case that matters most. Nor could
it be right about which of the two orderings it saw. Detection needs a fact the
payload does not carry: a writer or run identity on each record, which is a
change to ``comparison.py``'s two ``evidence.append`` calls and to the
completeness strip that would disclose it. Until a record carries one, this
reduction declines to be wrong on the single-writer log the pipeline actually
produces, and says so here -- where somebody debugging a banner that disagrees
with the timeline beside it is already reading -- rather than only in the build
plan, which they are not.

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
whenever either side's adapter name starts with ``Fake`` -- or whenever any run
in :attr:`ReportModel.series` names one, because a log whose headline run is real
and whose earlier nights were scripted is still a document with scripted numbers
drawn in it. The consequence is the point: you cannot obtain a clean-looking
report from scripted models by avoiding ``migkit demo``. A flag-driven banner is
exactly the banner that goes missing from the screenshot someone pastes into a
deck.

**And there is a third state, because "was this scripted" has three answers.**
An adapter name that was never recorded makes the question unanswerable, and a
two-state design answers it *real* on the strength of nothing: blank both sides'
adapters in the payload, delete the run artifacts, and a fully scripted demo
renders clean, with a verdict and pass rates and no band. The log that does that
is byte-identical to a real run whose adapter was never written down, so no
reader can separate them -- which makes "the document does not know" the only
honest answer and :class:`Provenance` the place it is said. The scripted claim
still outranks the gap, and the ``<title>`` still carries only the scripted one:
a prefix on every legacy log teaches readers to skip the prefix.

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

import html
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

import opik_rigor
from jinja2 import DictLoader, Environment, StrictUndefined, select_autoescape
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .contracts import EVENT_COMPARISON, EVENT_VERDICT, Verdict, hash_file, utc_now
from .dimensions import (
    MIN_ITEMS_FOR_A_VERDICT,
    MIN_N_FOR_A_VERDICT,
    UNTAGGED,
    DimensionCell,
    DimensionCounts,
    DimensionTally,
    TagCount,
    dimension_cell,
)
from .errors import ArtifactError, GoldenSetError, ReportError
from .evidence import resolve_evidence, stream_records
from .goldenset import GoldenSet
from .judging import JudgedArtifact
from .runner import RunArtifact

# `_text` is imported rather than re-spelled, and that is the whole of R36.4's
# fix. This module used to coerce shared payload fields with `str(x or "")`
# while `series` read the *same JSON fields* through `_text`, which is
# `"" if value is None else str(value)`. The two agree on every value a log this
# tool writes carries and part company on exactly one class: falsy and not
# `None`. `0` arrived as `""` here and as `"0"` there, so one page printed a
# recorded value as an absence while another printed it as a value.
#
# R36.4 rules the *split* is the defect and not any one of its five sites --
# `baseline.model_id`, `candidate.model_id` and three fields of `judges[0]` --
# because fixing five call sites one at a time guarantees a sixth. Sharing the
# function, rather than copying its expression, is what makes a sixth
# impossible: there is no second coercion left to drift. The direction was
# ruled on R32.1's tie-break generalised -- **which reading preserves what the
# log recorded** -- and only `_text` does.
#
# It is private to `series` and imported anyway: this is one package, and a
# public alias would advertise a coercion nobody outside should be choosing
# between. `RunSummary.adapter` is deliberately *not* converted; see
# `_run_summary`.
from .series import (
    CandidateField,
    CandidateLineage,
    Multiplicity,
    ParameterChange,
    RunPoint,
    SeriesBuilder,
    SpotCheck,
    SpotCheckSubject,
    Trend,
    _text,
    candidate_field,
    correct_field,
    parameter_strip,
    parse_created,
    spot_check,
    trend,
)

__all__ = [
    "DEFAULT_MAX_REPORT_CHARS",
    "Completeness",
    "DetailBudget",
    "DimensionMatrix",
    "FlipRow",
    "JudgeRow",
    "MethodologySection",
    "PROVENANCE_RECORDED",
    "PROVENANCE_SCRIPTED",
    "PROVENANCE_UNRECORDED",
    "Provenance",
    "RateStat",
    "ReportModel",
    "RunSummary",
    "TIMELINE_PAD",
    "TagColumn",
    "Timeline",
    "UrlViolation",
    "assert_self_contained",
    "external_urls",
    "methodology_sections",
    "render_html",
    "render_html_string",
    "render_terminal",
    "timeline_svg",
    "interval_bar_svg",
    "INTERVAL_BAR_PAD",
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
#:
#: ``ping`` (a POST the browser sends when the link is followed), ``xlink:href``
#: (what SVG's ``<use>`` and ``<image>`` are still written with) and ``xml:base``
#: (re-points every relative URL beneath it) are named here rather than left to
#: ``_SCHEME_RE``. That rule no longer sees every attribute -- see
#: ``_NEVER_DEREFERENCED_RE`` -- and all three used to rest on it alone. A real
#: fetching attribute whose only guard is a rule someone is in the middle of
#: narrowing is the shape the next hole arrives in.
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
        "ping",
        "xlink:href",
        "xml:base",
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

#: The attribute names the two *name-agnostic* rules below -- the
#: protocol-relative check and ``_SCHEME_RE`` -- are not applied to. Those two
#: ask what a value looks like; every other rule in ``_attribute_reason`` asks
#: what the attribute *is*. Shape is the wrong question for a family the browser
#: never dereferences, and asking it anyway was not merely noisy: a recorded
#: verdict reading ``review: n was too small`` matches ``scheme:``, so one line
#: of evidence in a ``data-`` attribute made ``render_html`` refuse the entire
#: document. A control whose false positives are reachable from the untrusted
#: input it exists to defend against is a denial-of-render vector -- a bigger
#: hole than the one it closes.
#:
#: SAFETY -- read before widening anything. A ``data-`` value is inert here only
#: because nothing in the document can read it: ``<script>`` is banned outright
#: by ``FORBIDDEN_TAGS`` and inline handlers by ``_EVENT_HANDLER_RE``, and
#: ``assert_self_contained`` runs both over every rendered document. **Relax
#: either ban and this exemption becomes unsafe**: one line of script turns
#: ``data-verdict`` into a fetch and nothing here would say so. Whoever allows
#: script must delete this exemption in the same change.
#:
#: SAFETY, the second coupling -- CSS. Script is not the only thing that can read
#: an attribute back out. ``[data-icon] { background: url(attr(data-icon)) }``
#: would turn an exempt value into a request with no script anywhere, and what
#: stops it is *attr()-tainting*: css-values-5 makes a value produced by
#: ``attr()`` tainted as a whole, and "using an attr()-tainted value as or in a
#: ``<url>`` makes a declaration invalid at computed-value time". That is a rule
#: on the *type*, not a list of functions, so it covers ``src()``, ``image()``,
#: ``image-set()`` and laundering through ``var()`` or a custom property alike;
#: Chrome enforces it (WPT ``css/css-values/attr-security.html``, 30/30), and
#: Gecko and WebKit have not shipped advanced ``attr()`` at all.
#:
#: Note what kind of rule that is. The script ban above is enforced *here*, by
#: ``FORBIDDEN_TAGS``. Tainting is enforced by browsers, and this repository does
#: not control it. Before this chunk the shape rules caught
#: ``data-icon="https://..."`` on the way in, so tainting never had to hold; now
#: it does. If it is ever relaxed, re-read this exemption rather than assuming
#: the script ban still covers it.
#:
#: What tainting does *not* stop, and never did: an attribute value can still
#: *select* which static URL is fetched -- ``[data-verdict^="a"] { background:
#: url(...) }`` is the classic form, and the CSSWG closed the ``@container
#: style(attr(...))`` version as wontfix on the grounds that attribute selectors
#: already did it. That channel reads the attribute whether or not this scanner
#: flagged its value, so the exemption neither opens nor widens it, and the
#: static URL it needs is caught by ``_scan_css`` -- unless written as
#: ``image-set()``, which ``_URL_FN_RE`` does not match. That gap predates this
#: chunk and is the follow-up worth doing.
#:
#: These four families and no others. ``data-*`` is author data and ``aria-*``
#: is accessibility metadata -- neither has a member the platform retrieves --
#: and ``xmlns``/``xmlns:*`` carry namespace URIs, which are opaque identifiers
#: rather than locations; no browser has ever fetched one. Widening past this is
#: a new argument, not an extension of this one. The name-based rules still run
#: on exempt names, so an exempt attribute that is also in ``FETCHING_ATTRS`` --
#: as ``xmlns`` is not, but a future name might be -- is still a violation.
_NEVER_DEREFERENCED_RE = re.compile(r"^(?:data-.+|aria-.+|xmlns|xmlns:.+)$")

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
        # The two rules that judge the value's shape instead of the attribute's
        # meaning, and the only two the exemption turns off. See
        # _NEVER_DEREFERENCED_RE for the four families and for the script ban
        # this exemption's safety rests on.
        judge_by_shape = not _NEVER_DEREFERENCED_RE.match(name)
        if judge_by_shape and stripped.startswith("//"):
            return "protocol-relative URL; it fetches over whatever scheme the page was opened with"
        if judge_by_shape and _SCHEME_RE.match(value):
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


#: :attr:`Provenance.state` when a ``Fake*`` adapter produced any run the document
#: shows. The positive claim, and the only one the ``<title>`` carries.
PROVENANCE_SCRIPTED = "scripted"

#: :attr:`Provenance.state` when the evidence does not name the adapter that
#: produced a headline run, so the document can say neither "scripted" nor "real".
PROVENANCE_UNRECORDED = "unrecorded"

#: :attr:`Provenance.state` when every headline run names an adapter and none of
#: them is scripted. The only state that bands nothing.
PROVENANCE_RECORDED = "recorded"

#: The three states' band labels, ids and colours, keyed by state. A mapping
#: rather than three ``if`` chains in three renderers: the terminal, the HTML band
#: and any later surface read the same row, so a fourth state cannot be added to
#: one of them and forgotten in the others.
_PROVENANCE_BANDS: Mapping[str, tuple[str, str, str, str]] = {
    PROVENANCE_SCRIPTED: ("FAKE MODELS", "fake-models", "fake", "red"),
    PROVENANCE_UNRECORDED: (
        "PROVENANCE NOT RECORDED",
        "provenance-unrecorded",
        "unrecorded",
        "yellow",
    ),
}


@dataclass(frozen=True)
class Provenance:
    """Where this document's numbers came from, in the three states that exist.

    **Three states and not two, which is R29.2's ruling and the reason this class
    exists.** ``is_demo`` answers "was any run scripted" and answers it from
    adapter names; a log that records no adapter at all makes that question
    unanswerable, and a two-state design resolves the unanswerable case to
    *real* -- silently, on the strength of nothing. Blanking both sides' adapters
    in the payload and deleting the run artifacts turned a fully scripted demo
    into a clean report carrying a verdict and pass rates, which is exactly the
    document §5.3 says cannot be obtained. The two logs are byte-identical, so no
    amount of reading can tell them apart: the honest reading is that the
    document does not know, and the honest rendering says so.

    That is this package's own rule -- an absence must not render as a
    measurement -- applied to its most important disclosure.

    **The scripted state outranks the unrecorded one.** A ``Fake*`` adapter on
    either side is a positive finding and a missing adapter on the other is a
    gap; a document that has both has something definite to say and says it. The
    consequence is that :attr:`unrecorded` can be non-empty while
    :attr:`state` is :data:`PROVENANCE_SCRIPTED`, which is why the sentences
    below never describe an unnamed side as real.

    **Both findings are carried at both scopes.** R34.1: the scripted finding was
    built at the headline (:attr:`headline_scripted`) and across the series
    (:attr:`scripted_comparisons`), and the unrecorded finding was built at the
    headline only -- so the gap ``is_demo`` was widened to close was still open
    for the state invented to close it. :attr:`unrecorded_comparisons` closes it.
    The *reach of the band* is untouched (R34.3): the band still speaks for the
    headline comparison, and the series counts speak for the series, each
    sentence naming its own scope so no reader has to work out which is speaking.

    **The counts are in comparisons, and say so.** R29.4: a :class:`RunPoint`
    carries no run id and no artifact path, so two comparisons naming the same
    baseline run cannot be told from two comparisons naming two -- in the
    showcase shape a night is four runs and six adapter mentions. A run count
    off ``migkit.run_started`` would be exact where those records exist and
    absent on precisely the hand-assembled logs this disclosure protects. A
    coarser number that is right beats a precise one that is wrong.
    """

    #: One of the three ``PROVENANCE_*`` constants.
    state: str
    #: Whether the *headline* comparison's own sides name a ``Fake*`` adapter, as
    #: distinct from the document containing scripted runs anywhere. R29.1 turns
    #: on this: the two cases get different sentences, not one sentence with a
    #: variable in it.
    headline_scripted: bool
    #: Comparisons the document draws -- one per ``migkit.comparison`` record.
    comparisons: int
    #: Of those, how many name a ``Fake*`` adapter on at least one side.
    scripted_comparisons: int
    #: Headline sides whose adapter the evidence never recorded, ``"baseline"``
    #: before ``"candidate"``. Empty on every log this tool writes.
    unrecorded: tuple[str, ...]
    #: Of the comparisons, how many name no adapter on at least one side. R34.1:
    #: the exact mirror of :attr:`scripted_comparisons`, counted the same way and
    #: in the same unit, and it exists because the scripted finding was carried at
    #: both scopes while the unrecorded finding was built at headline scope only.
    #: ``is_demo`` reaches into the series deliberately -- a band that appears
    #: only when the *last* run was fake is a band you can remove by scripting the
    #: runs before it -- so the gap that the scripted finding was widened to close
    #: was still open for the state invented to close it.
    #:
    #: A side is unrecorded when its adapter string is blank once stripped, which
    #: is the rule :attr:`unrecorded` already applies to the headline's two sides.
    #: A comparison can be counted here *and* in
    #: :attr:`scripted_comparisons` -- a ``Fake*`` baseline beside a candidate
    #: that named nothing is both a finding and a gap -- so the two counts do not
    #: partition the series and nothing here subtracts them as if they did.
    unrecorded_comparisons: int
    #: Comparisons whose payload ``created`` names a different UTC calendar day
    #: from the evidence record carrying it. R29.3: detected, never assumed --
    #: unconditional prose would be false on ``migkit demo``, where the two are
    #: the same instant, and an asymmetry asserted where none was measured is
    #: this package's rule inverted inside the chunk about unsuppressible
    #: honesty.
    dated_apart: int

    @property
    def banded(self) -> bool:
        """Whether this document carries a band above its verdict banner."""
        return self.state in _PROVENANCE_BANDS

    @property
    def label(self) -> str:
        """The band's shouted half, or ``""`` when there is no band."""
        return _PROVENANCE_BANDS.get(self.state, ("", "", "", ""))[0]

    @property
    def anchor(self) -> str:
        """The band's ``id``, so a reviewer can link straight at it."""
        return _PROVENANCE_BANDS.get(self.state, ("", "", "", ""))[1]

    @property
    def css(self) -> str:
        """The band's modifier class."""
        return _PROVENANCE_BANDS.get(self.state, ("", "", "", ""))[2]

    @property
    def border(self) -> str:
        """The terminal panel's border colour. Never the only carrier of a fact."""
        return _PROVENANCE_BANDS.get(self.state, ("", "", "", ""))[3]

    @property
    def sentence(self) -> str:
        """The band's words, written once for the terminal and the HTML both.

        The same discipline as :attr:`DetailBudget.sentence`: two copies of a
        disclosure are two chances for one of them to go stale, and this one is
        the disclosure the whole module is built around.
        """
        if self.state == PROVENANCE_SCRIPTED:
            head = "these numbers describe scripted responses, not a real provider"
            counted = self._counted
            return f"{head}; {counted}" if counted else head
        if self.state == PROVENANCE_UNRECORDED:
            return (
                f"the evidence does not name the adapter that produced "
                f"{self._sides}, so this report cannot say whether these numbers "
                f"came from a real provider or from a script"
            )
        return ""

    @property
    def _counted(self) -> str:
        """The band's series-aware clause, or ``""`` when there is no series.

        ``0`` comparisons is a model built by hand rather than a document with no
        scripted runs in it, so it counts nothing rather than publishing a
        ``0 of 0`` that reads like a measurement.

        The one-comparison spellings are written out rather than reached with a
        pluralising helper, because English will not let the number and the verb
        be chosen independently: "all 1 comparison name a Fake adapter" is what a
        helper produces, and a document whose loudest sentence is ungrammatical
        is a document a reader trusts less than one that says nothing.

        The last branch -- the one that has to speak for comparisons it may not
        have been able to check -- is :attr:`_no_scripted_sentence`, which carries
        R34.2's argument beside its own wording.
        """
        if not self.comparisons:
            return ""
        if self.scripted_comparisons == self.comparisons:
            if self.comparisons == 1:
                return "the one comparison in this document names a Fake adapter"
            return (
                f"all {self.comparisons} comparisons in this document name a Fake "
                f"adapter"
            )
        if self.scripted_comparisons:
            verb = "names" if self.scripted_comparisons == 1 else "name"
            return (
                f"{self.scripted_comparisons} of the {self.comparisons} comparisons "
                f"in this document {verb} a Fake adapter"
            )
        return self._no_scripted_sentence

    @property
    def _no_scripted_sentence(self) -> str:
        """The clause for a band whose own series names no ``Fake*`` adapter. R34.2.

        What shipped counted every comparison into one denominator and said none
        of them names a Fake adapter. Every word of that is true when the payloads
        name no adapter at all, and it reads as *these N were checked and came
        back clean*: a comparison with empty adapter strings sat in the
        denominator exactly as if it had been examined and cleared. Not a false
        sentence but a **true one licensing a false inference**, which is R29.1's
        shape one scope up -- harder to find, and easier to defend in review.

        So the denominator is the comparisons that named an adapter **on both
        sides**, because a comparison naming one side and not the other was
        half-examined and the unnamed half is exactly where a ``Fake*`` would
        hide; and the ones that named none are counted in a clause of their own
        that disowns them. When *every* comparison is in that second group there
        is no denominator left, and this claims nothing rather than printing the
        ``0 of 0`` :attr:`_counted` already refuses for the empty series -- that
        refusal is the precedent here, not a separate rule.

        This runs only where :attr:`scripted_comparisons` is ``0``, and that is
        the one branch where the subtraction is sound: elsewhere a comparison can
        be scripted *and* unrecorded, so ``comparisons - unrecorded_comparisons``
        is not the complement of the scripted count and a numerator could exceed
        its own denominator. The branches above therefore keep the whole series in
        view; they make a positive claim about the comparisons that did name a
        Fake adapter, and no claim of cleanliness about the rest.

        Written out per number rather than reached through a pluralising helper,
        for the reason :attr:`_counted` gives: English will not let the number and
        the verb be chosen independently.
        """
        total = self.comparisons
        unnamed = self.unrecorded_comparisons
        named = total - unnamed
        band = "this band comes from the run artifacts the headline read"
        if not unnamed:
            if total == 1:
                return (
                    f"the one comparison in this document names no Fake adapter in "
                    f"its own payload, and {band}"
                )
            return (
                f"none of the {total} comparisons in this document name a Fake "
                f"adapter in their own payloads, and {band}"
            )
        if not named:
            if total == 1:
                return (
                    f"the one comparison in this document records no adapter on at "
                    f"least one side, so this document cannot say whether it was "
                    f"scripted, and {band}"
                )
            return (
                f"none of the {total} comparisons in this document record an "
                f"adapter on both sides, so this document cannot say whether any of "
                f"them was scripted, and {band}"
            )
        if named == 1:
            checked = (
                "the one comparison in this document that records an adapter on "
                "both sides names no Fake adapter in its own payload"
            )
        else:
            checked = (
                f"none of the {named} comparisons in this document that record an "
                f"adapter on both sides name a Fake adapter in their own payloads"
            )
        if unnamed == 1:
            gap = (
                "the other one records no adapter on at least one side, and this "
                "document cannot speak for it"
            )
        else:
            gap = (
                f"the other {unnamed} record no adapter on at least one side, and "
                f"this document cannot speak for them"
            )
        return f"{checked}, and {band}; {gap}"

    @property
    def _sides(self) -> str:
        """Which headline run has no adapter recorded, named rather than counted."""
        if len(self.unrecorded) == 1:
            return f"the {self.unrecorded[0]} run"
        return "either run"


@dataclass(frozen=True)
class MethodologySection:
    heading: str
    body: tuple[str, ...]  # paragraphs, already substituted with real numbers


#: What :attr:`ReportModel.dimensions` carries when nothing ever counted. Only
#: :meth:`ReportModel.from_evidence` counts, so this is the sentence a model built
#: by any other route hands whoever asks. It is a sentence rather than an empty
#: mapping for the reason
#: :class:`~model_migration_kit.dimensions.DimensionCounts` gives for the same
#: choice, and it is deliberately not one of that module's refusals: those say why
#: counting declined, and this says counting was never asked for.
_NO_DIMENSION_COUNTS = (
    "no dimension counts were taken for this report: ReportModel.from_evidence is "
    "the only thing that takes them."
)

#: What :attr:`ReportModel.trend` carries when nobody drew a line -- seven empty
#: fields, and **no caveat** (R30.4). :func:`~model_migration_kit.series.trend`
#: raises R21.5's assumed-lineage note whenever it is handed a lineage nobody
#: declared, so every line this module draws carries it; a ``ReportModel`` that
#: was never handed a series has assumed nothing, and putting the note here would
#: make a default say something was measured and doubted when nothing was
#: measured at all. That is this project's recurring defect (an absence rendering
#: as a measurement) reached from the one direction nobody checks, because the
#: sentence would be *true of every real report* and so would look right.
#:
#: A shared instance rather than a ``default_factory``: a :class:`Trend` is a
#: ``NamedTuple`` of tuples, so there is no per-instance state to protect and
#: nothing a caller could mutate through it. ``dimensions`` and ``detail`` need
#: the factory because they are dataclasses; this does not.
_NO_TREND = Trend(
    points=(),
    successions=(),
    excluded=(),
    undated=0,
    caveats=(),
    outside_lineage=(),
    absent_models=(),
)


@dataclass(frozen=True)
class TagColumn:
    """One model's side of the matrix: a cell per tag, in the matrix's tag order.

    **A sequence rather than a ``tag -> cell`` mapping, and that is the whole
    reason this class exists.** ``DimensionCell.items`` is an int and
    ``Mapping.items`` is a bound method, so a template writing ``column.items``
    where it meant ``cell.items`` would print ``<built-in method items>`` into the
    page and no test that renders a *populated* matrix would notice -- the two
    spellings are one keystroke apart and both "work".
    ``model_migration_kit.dimensions`` files that hazard against its own
    ``by_model``, where the mapping is what the counter returns and cannot be
    renamed away. Here it can: a cell already carries its own ``tag``, so the
    mapping was never carrying information the cells did not, and dropping it
    removes the attribute the slip needs in order to resolve to anything.

    ``cells`` is aligned with :attr:`DimensionMatrix.tags` position for position,
    so two columns can be read side by side by index. :meth:`cell` exists so that
    no caller has to rely on that alignment to answer "how did this model do on
    this tag".
    """

    model_id: str
    cells: tuple[DimensionCell, ...]

    def cell(self, tag: str) -> DimensionCell | None:
        """This column's cell for ``tag``, or ``None`` if the tag is not in it.

        ``None`` rather than a fabricated empty cell: a tag absent from the matrix
        was absent from the golden set, and a zero cell would say it was measured
        and produced nothing. Those are different findings. A tag the *set* holds
        and this model produced nothing for is already present here, as zeros.
        """
        for one in self.cells:
            if one.tag == tag:
                return one
        return None


@dataclass(frozen=True)
class DimensionMatrix:
    """How every tag in the golden set did, per model -- or why there is no table.

    A refusal is ``available=False`` and a ``reason`` a reader can act on, and it
    arrives carrying nothing: no tags, no baseline cells, no candidate columns.
    That mirrors the promise
    :class:`~model_migration_kit.dimensions.DimensionCounts` makes about
    ``by_model``, and it exists for the same reason -- every way this table can
    decline is global, so there is no subset of cells that happens to be sound,
    and half a matrix rendered as the matrix is the "missing data stated as zero"
    failure this codebase has shipped once already.

    **Six things can make it unavailable, and none of their sentences is written
    here.** The golden set can be missing, unreadable, unrecorded or changed --
    one reason, ``goldenset["reason"]``, in the words the completeness strip and
    the warnings list already use -- and the counting itself declines for five
    more of its own. Every one of them is quoted verbatim from where it was
    written. A second phrasing would be a second chance for one of them to go
    stale, which is the argument :attr:`DetailBudget.sentence` makes for writing a
    disclosure once.

    **:attr:`judge` is on the matrix because the counts underneath it are not.**
    ``DimensionCounts`` carries no judge name, so on its own it is a per-judge
    table with the judge erased -- and a panel writes one verdict per judge per
    completion, so which judge produced these numbers is not a footnote. It is the
    panel's first judge, which is what ``from_evidence`` selects.

    :attr:`min_n` and :attr:`min_items` are the floors every cell here was judged
    against. They travel with the matrix rather than being looked up by the
    renderer, because a document that refuses a cell has to be able to say what it
    refused against, and R9 gave it two floors to refuse against.
    """

    available: bool
    reason: str  # "" when available
    judge: str
    #: Alphabetical, with :data:`~model_migration_kit.dimensions.UNTAGGED`
    #: last. The contract said "golden-set tag order" and R27.3 corrected the
    #: phrase: the set's *file* order is not reachable from anything this module
    #: sees -- ``GoldenSet.stats()`` returns ``dict(sorted(...))`` and the counter
    #: keys its inner mapping through ``sorted(index.tags)`` -- so a regression to
    #: file order is unimplementable and alphabetical is the whole promise.
    #: Last rather than first, which is where its empty string would sort:
    #: it is the leftover bucket, and a table that opens with the leftovers reads
    #: as one whose first row is the most important.
    tags: tuple[str, ...]
    #: The side the comparison payload names as the baseline, which is where the
    #: side comes from: the counter keys by ``model_id`` and does not know which
    #: side is which. A baseline that was judged and produced nothing is a column
    #: of zeros, never a missing column -- the two are printed next to each other,
    #: and a vanishing column turns a comparison into a single reading with no
    #: sentence saying where the other one went.
    baseline: TagColumn
    #: Every other model the run named, the comparison's candidate first. Plural
    #: because ``by_model`` holds every model a ``migkit.judging_completed``
    #: named, and dropping one because the payload did not call it the candidate
    #: would be a column of real measurements silently discarded.
    candidates: tuple[TagColumn, ...]
    min_n: int
    min_items: int

    def column(self, model_id: str) -> TagColumn | None:
        """The column for ``model_id``, baseline or candidate, or ``None``.

        Here rather than in the caller because :attr:`baseline` and
        :attr:`candidates` are two fields holding one population, and a caller
        that knows only a model id should not have to know which of the two it
        landed in.
        """
        for one in (self.baseline, *self.candidates):
            if one.model_id == model_id:
                return one
        return None


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
    #: Comparison records whose payload ``created`` names a different UTC calendar
    #: day from the ``ts`` of the evidence record carrying it. Counted in
    #: :meth:`from_evidence` and nowhere else, because it is the one fact on this
    #: model that the series cannot supply: ``series._created`` returns the
    #: payload's ``created`` when it parses and **discards** the envelope ``ts``,
    #: keeping only a ``created_source`` label, so by the time a
    #: :class:`~model_migration_kit.series.RunPoint` exists the second clock is
    #: gone. R29.3 ruled the counting happen here rather than widening
    #: ``RunPoint``, which is outside C18's files.
    #:
    #: Zero on a model built by any other route, which is the same claim as "no
    #: comparison disagreed with its own record": both mean the document says
    #: nothing about dates, and the disclosure is written to appear only when the
    #: count is positive.
    dated_apart: int = 0
    #: One point per ``migkit.comparison`` record in the log, oldest first --
    #: every run the log holds, not only the one this report is about. A log of
    #: fourteen nightly runs used to render as one verdict with thirteen nights
    #: on disk and unread.
    #:
    #: ``series[-1]`` is the headline run, but every field above is still read
    #: from the records themselves rather than from a point. That is deliberate:
    #: the timeline can gain, lose or re-derive a field without the banner, the
    #: judge table, the flips or the provenance block moving with it.
    series: tuple[RunPoint, ...] = ()
    #: How each tag in the golden set did, per model, under the panel's first
    #: judge -- or the sentence saying why there is no such table. Never ``None``:
    #: a report that has no counts says so in the same place as one that has them.
    #:
    #: Counted from the *headline* run and not from the whole log, which is the
    #: opposite of :attr:`series` and is deliberate on both sides. The timeline is
    #: about the history; this breaks the banner's number down by tag, so it has to
    #: be about the banner's run. See
    #: :meth:`~model_migration_kit.dimensions.DimensionTally.add`.
    #:
    #: **This replaces the raw ``dimension_counts`` that used to sit here, and
    #: does not sit beside it.** A :class:`~model_migration_kit.dimensions.DimensionCell`
    #: carries ``tag``, ``passes``, ``n`` and ``items``, so the matrix subsumes
    #: every fact the raw counts held and nothing is lost by dropping them.
    #: Carrying both would put one set of facts on the model at two fidelities,
    #: which is two chances for them to disagree -- the same argument this field's
    #: refusals make for never re-wording a decline reason.
    #:
    #: Defaulted for the reason ``series`` is: every constructor of a
    #: ``ReportModel`` in this codebase and in anyone else's predates the field, and
    #: a required argument would break each one. The default is a refusal rather
    #: than an empty mapping -- ``{}`` is not a sentence anyone can print.
    dimensions: DimensionMatrix = field(
        default_factory=lambda: DimensionMatrix(
            available=False,
            reason=_NO_DIMENSION_COUNTS,
            judge="",
            tags=(),
            baseline=TagColumn(model_id="", cells=()),
            candidates=(),
            min_n=MIN_N_FOR_A_VERDICT,
            min_items=MIN_ITEMS_FOR_A_VERDICT,
        )
    )
    #: The one candidate table this log can render, or ``None`` -- see
    #: :func:`~model_migration_kit.series.candidate_field`, which is called on
    #: :attr:`series` and reads nothing else.
    #:
    #: **It is the field :func:`~model_migration_kit.series.correct_field`
    #: returned, not the one :func:`~model_migration_kit.series.candidate_field`
    #: did** (R30.2), and :attr:`multiplicity` is the second half of that one
    #: call. The correction appends one
    #: :class:`~model_migration_kit.series.Caveat` per candidate whose
    #: significance did not survive it, so keeping the uncorrected field here
    #: would compute those caveats and drop them -- R21's finding, reproduced
    #: inside the chunk written to fix R21. There is no second place that fact is
    #: recorded: :attr:`~model_migration_kit.series.Multiplicity.changed` is a
    #: tuple of model ids and not prose, and the sentence saying what the
    #: correction did to a *particular* candidate exists only on that candidate's
    #: caveat. Nothing else about the field moves -- a correction that changed
    #: nothing, and a correction that was refused, both return the field
    #: unchanged, and the rows, the exclusions and the key are the same objects
    #: either way.
    #:
    #: **``None`` is carried through, and is not an empty field.** The producer
    #: returns ``None`` when no comparability key holds two distinct candidate
    #: models, which is a different claim from "a field of no candidates": the
    #: first says this log cannot be tabled, the second says it was tabled and
    #: came out empty. A view model that substituted a default here would publish
    #: the second sentence on the evidence for the first, which is the failure
    #: C7's first-run marker, C4's exclusions, C5's superseded exclusion and
    #: C10's zero column each exist to prevent, one layer up. The renderer gates
    #: on ``is not None`` and says why there is no table; nothing here decides
    #: that for it.
    #:
    #: **The excluded-runs list is this field's own**
    #: :attr:`~model_migration_kit.series.CandidateField.excluded`, per R23.2, and
    #: there is deliberately no second top-level ``partition_comparable`` call.
    #: Two partitions would put the same facts on the model twice, computed
    #: against possibly different keys, and a disagreement between them could not
    #: be adjudicated from the model. One partition, one source -- so the list and
    #: the table it explains are guaranteed to be about the same set of runs.
    #:
    #: The consequence, flagged in R23.2 and accepted: when this is ``None`` the
    #: exclusion sentences computed along the way die with it, so the document
    #: cannot say *why* there is no table. That is the right trade -- a
    #: wrong-but-present list is worse than an absent one -- but it means the
    #: renderer's empty state must say that runs may have been excluded without
    #: being able to name them, rather than printing an empty list that reads as
    #: "nothing was excluded".
    #:
    #: Built with :func:`~model_migration_kit.series.candidate_field`'s own
    #: default window. Nothing in the evidence log, the thresholds or the config
    #: records a staleness window, so there is no recorded number to prefer; the
    #: field carries whatever window it was built with on
    #: :attr:`~model_migration_kit.series.CandidateField.stale_after_days`, so a
    #: renderer can name it instead of guessing.
    #:
    #: Defaulted for the reason :attr:`series` and :attr:`dimensions` are: every
    #: existing constructor of a ``ReportModel`` predates the field.
    candidates: CandidateField | None = None
    #: What a ``k``-prompt hand check of this run would probably have missed, or
    #: ``None`` -- see :func:`~model_migration_kit.series.spot_check`.
    #:
    #: **The counting judge's candidate-side items**, per R26.3. Both halves are
    #: rulings and neither is this module's to re-decide:
    #:
    #: * The judge is :attr:`judges`\ ``[0]``, reached through the same
    #:   ``counting_judge`` local :attr:`dimensions` is built with. One document
    #:   must not select its judge two different ways, and a spot check under one
    #:   judge printed beside a tag matrix counted under another would be two
    #:   numbers a reader has no way to reconcile. The panel is never summed:
    #:   two judges grading the same sixty completions are a hundred and twenty
    #:   records and sixty completions, and adding them would multiply ``N``.
    #: * The side is the candidate. The number exists to say what a cheaper
    #:   method would have missed, and the failures that argument is about are
    #:   the ones the decision turns on.
    #:
    #: The counts are :attr:`JudgeRow.items_candidate` on that row -- the same
    #: mapping the judge table prints through :func:`_item_counts` -- so the
    #: ``N`` and ``F`` in the sentence are the numbers rendered a few rows above
    #: it rather than a second reading of the same fact.
    #:
    #: **``None`` is carried through, and is not a zero.** The producer declines
    #: on three separate grounds, and each is a different sentence the renderer
    #: owes the reader: nothing failed, so there was nothing to miss; the check
    #: would read every item, which is a census and not a spot check; or there is
    #: no set to sample. A default here would print an absence as a measurement,
    #: which is the failure this document's whole design rule is against. The
    #: renderer gates on ``is not None``.
    #:
    #: **The subject is in the sentence, and a renderer must not caption around
    #: it.** :class:`~model_migration_kit.series.SpotCheckSubject` carries the
    #: judge and the side as facts, and the producer -- not this module and not a
    #: template -- turns them into words. A caption supplying a subject the
    #: sentence already states is two renderings of one fact, which is how they
    #: come to disagree.
    #:
    #: An **unnamed** judge needs no handling here: a blank name is reachable from
    #: real evidence and the producer says so in the sentence itself. A run with
    #: no judge row at all is different and is not a subject that can be named, so
    #: no spot check is attempted for it.
    #:
    #: Defaulted for :attr:`candidates`' reason.
    spot_check: SpotCheck | None = None
    #: This log's one candidate line, and everything it left out -- see
    #: :func:`~model_migration_kit.series.trend`, called on :attr:`series` and
    #: reading nothing else.
    #:
    #: **Never ``None``.** ``trend`` has no ``None`` return: a log it can draw no
    #: line from comes back as a :class:`~model_migration_kit.series.Trend` whose
    #: :attr:`~model_migration_kit.series.Trend.points` are empty and whose other
    #: six fields say why -- which runs were excluded, how many could not be
    #: dated, which ran on this baseline outside the succession, which declared
    #: ids never ran at all. So the absence has a shape here rather than needing
    #: an ``is None`` gate, and a renderer that has no line to draw still has
    #: something to print. That is the opposite of :attr:`candidates`, and the two
    #: differ because the producers do; neither shape is chosen here.
    #:
    #: **The lineage is always
    #: :meth:`~model_migration_kit.series.CandidateLineage.assumed_from`** (R30.1).
    #: Nothing outside ``series.py`` mentions a lineage: no config schema carries
    #: one, ``from_evidence`` reads no config, and R21.3 forbids it starting. So
    #: every report rendered today carries R21.5's caveat saying the succession
    #: was assumed from the log rather than declared. **That is correct and is not
    #: to be tuned down.** It is the true sentence about every log this project
    #: can currently read, and a caveat on every report becomes noise only once a
    #: declaration path exists and reports using it still carry it. Suppressing it
    #: here -- a flag, a default declaration, a filter on the way out -- would
    #: restore the silent default R21.5 rejected, and would do it in the wiring,
    #: which R21.5 names as the one shape of this defect nobody would find.
    #:
    #: **``baseline_model`` is :attr:`baseline`'s** ``model_id`` and not
    #: ``series[-1].baseline_model`` (R30.4). They are one fact and R23.2 allows
    #: it one source; the tie goes to the one that is always there, since
    #: :attr:`baseline` is read from the records while :attr:`series` can be
    #: empty, and choosing the other would need an empty-series special case
    #: purely to answer a question :attr:`baseline` already answers.
    trend: Trend = _NO_TREND
    #: Every tracked parameter as it stood across the line's last two runs -- see
    #: :func:`~model_migration_kit.series.parameter_strip`. Empty exactly when
    #: :attr:`trend` has no points.
    #:
    #: **Both points come from** :attr:`~model_migration_kit.series.Trend.points`
    #: **and never from** :attr:`series` (R30.3). ``trend``'s own docstring
    #: settles it: filtering the line by the field that moves "is what hid the
    #: change", and the strip "was always able to show the change and was
    #: prevented by its own caller". :attr:`series` is the whole log in the log's
    #: order, so feeding the strip from it would compare two runs that may be
    #: neither consecutive nor on the same line.
    #:
    #: Two consequences, both accepted rather than worked around:
    #:
    #: * ``points[-1]`` is the *line's* newest run, which is not always the
    #:   headline run. If the headline was excluded from the line the strip is
    #:   not about the banner -- and that is right: the strip belongs beside the
    #:   timeline, where :attr:`~model_migration_kit.series.Trend.excluded`,
    #:   :attr:`~model_migration_kit.series.Trend.outside_lineage` and
    #:   :attr:`~model_migration_kit.series.Trend.undated` already say who is
    #:   missing and why. A strip silently retargeted at the headline would
    #:   compare two runs the chart above it does not draw as consecutive.
    #: * **The strip is gated on the trend, not on itself.** An empty tuple here
    #:   means an empty line, and the reason is in :attr:`trend`. A renderer that
    #:   gates on this being non-empty publishes "no parameters tracked" over a
    #:   log that simply has no line yet. When there *is* a line the tuple is
    #:   never empty -- ``parameter_strip`` emits one row per tracked parameter
    #:   including the ones that held -- so empty here is unambiguous.
    #:
    #: A one-point line passes ``previous=None``, which the producer renders as
    #: :data:`~model_migration_kit.series.NO_PREVIOUS_RUN` in every ``before``
    #: cell: a word, not a blank, so a genuine first run cannot be read as a run
    #: that changed nothing.
    parameter_strip: tuple[ParameterChange, ...] = ()
    #: What correcting :attr:`candidates`' p-values across its candidates did, or
    #: why it was declined -- the second half of the one
    #: :func:`~model_migration_kit.series.correct_field` call that also produced
    #: :attr:`candidates`.
    #:
    #: **``None`` exactly when :attr:`candidates` is ``None``, and never
    #: otherwise** (R30.4). The two are one fact -- the multiplicity is *of* the
    #: field -- and ``correct_field`` takes a
    #: :class:`~model_migration_kit.series.CandidateField`, not an optional one.
    #: A refusal :class:`~model_migration_kit.series.Multiplicity` invented for
    #: the no-field case would be this module composing a producer's prose, which
    #: R26.4 refused for the spot check's sentence and R21.5 refused for the
    #: lineage caveat. The renderer already owes a sentence for
    #: ``candidates is None``; a second one saying "and so nothing was corrected"
    #: can only agree with it or contradict it, and the second is the outcome
    #: that ships.
    #:
    #: When it is present it is never a silence: ``correct_field`` records a
    #: refusal as a :class:`~model_migration_kit.series.Multiplicity` with
    #: ``applied=False`` and its own note, so "fewer than two testable
    #: candidates", "the members were tested at different levels" and "the
    #: correction ran and changed nothing" are three readable outcomes rather
    #: than one absent object.
    multiplicity: Multiplicity | None = None

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

        # One streaming pass, keeping three records and one flat point per
        # comparison, never the log. See
        # :func:`~model_migration_kit.evidence.stream_records`: the list-returning
        # read this replaced cost 5.0 to 5.8 times the log's own bytes, and the
        # evidence log is the largest artifact the pipeline writes.
        #
        # The series is accumulated *in this loop*, through
        # :class:`~model_migration_kit.series.SeriesBuilder` -- the same pairing
        # rule ``read_series`` drives. Calling ``read_series`` beside this loop
        # would be a second read of that same largest artifact, and a second
        # pairing rule would be a way for the timeline and the banner to disagree
        # about which verdict belongs to which run.
        #
        # It is accumulated *beside* the three assignments below rather than in
        # place of them: nothing after this loop reads a point, so no later work
        # on the timeline can move which record the banner came from.
        #
        # The per-tag counting is accumulated in this loop for a harder reason
        # than tidiness: it *cannot* be done anywhere else. A ``judge.verdict``
        # carries no item id, so a verdict joins to a golden-set item by its input
        # text -- and the golden set's path and hash live in the
        # ``migkit.comparison`` payload, which is written after judging and is
        # therefore one of the last records this loop sees. Reading the log again
        # to do the join is the one-pass rule this comment block opens with;
        # buffering the verdicts is the amplification ``evidence.py`` measured. So
        # :class:`~model_migration_kit.dimensions.DimensionTally` splits the work:
        # it files each verdict under a digest of its input on the way past, and
        # the join happens below, once the comparison record has named the set.
        comparison = None
        verdict_record = None
        last = None
        dated_apart = 0
        builder = SeriesBuilder()
        tally = DimensionTally()
        for record in _stream_records(path):
            last = record
            builder.add(record)
            tally.add(record)
            # Last one wins, and a comparison clears the verdict beside it, so
            # these are one reduction over the log and not two independent
            # last-wins variables. Independent, they let a verdict written on an
            # earlier night fill the slot of a night that died before deciding:
            # the banner reports the older decision, the timeline reports none,
            # and the document disagrees with itself. Every log written today
            # holds one comparison followed immediately by its verdict, so both a
            # slip to first-wins and a dropped reset here pass every test that
            # only ever looks at a one-run log.
            if record.event_type == EVENT_COMPARISON:
                comparison = record
                verdict_record = None
                # Counted here and not from the series: see
                # :attr:`ReportModel.dated_apart`. This is the loop's only
                # remaining reader of an envelope ``ts``, and it has to be, because
                # a ``RunPoint`` no longer carries one.
                dated_apart += _dated_apart(record.payload, str(record.ts or ""))
            elif record.event_type == EVENT_VERDICT:
                verdict_record = record
        series = builder.points()
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

        warnings: list[str] = _payload_warnings(payload.get("warnings", ()))
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
        # The panel's first judge, selected once. A panel writes one verdict per
        # judge per completion, so counting two would multiply every denominator
        # by the panel size -- and the matrix has to be able to say which judge it
        # counted under, which the counts underneath it cannot.
        #
        # The *row* is bound here and the name derived from it, rather than each
        # consumer writing ``judges[0]`` again, because the tag matrix and the
        # spot check must be about the same judge (R26.3): one document selecting
        # its judge in two places is one edit away from selecting two judges.
        counting = judges[0] if judges else None
        counting_judge = counting.name if counting is not None else ""
        counts = _close_the_tally(tally, gs_view, counting_judge)
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
        # Built here rather than beside ``counts`` because a cell echoes the run's
        # own gate, and the gate is in ``thresholds``. An absent confidence is
        # passed on as ``None`` rather than defaulted here: the cell discloses
        # rigor's fallback in its own note, and a default applied twice is a
        # printed interval whose level nothing on the page can be held to.
        dimensions = _dimension_matrix(
            counts,
            judge=counting_judge,
            baseline_id=baseline.model_id,
            candidate_id=candidate.model_id,
            confidence=_number(thresholds.get("confidence")),
            floor=_number(thresholds.get("pass_rate_floor")),
        )
        # Pure arithmetic over the series the loop above already built, so this
        # opens no file and re-reads nothing -- see :attr:`candidates`. ``None``
        # is passed straight through: there is no ``or CandidateField(...)`` here
        # and there must never be one, because the absence is the finding and a
        # default would publish it as a measurement.
        candidates = candidate_field(series)
        # R30.2: the corrected field replaces the uncorrected one rather than
        # sitting beside it. `correct_field` appends one caveat per candidate
        # whose significance did not survive the correction, and those caveats
        # live nowhere else -- keeping the field `candidate_field` returned would
        # compute them and throw them away, which is R21's finding rebuilt inside
        # the chunk written to fix R21. Rebinding rather than a second name for
        # the same reason C22a gives for one partition: two fields differing only
        # in their caveats is a disagreement nothing on the model can adjudicate.
        #
        # R30.4: `multiplicity` is `None` exactly here, where there is no field to
        # correct, and never anywhere else. No refusal `Multiplicity` is invented
        # for this case -- `correct_field` mints refusals for the families it can
        # see, and one minted here would be this module writing a producer's
        # prose. A correction of nothing is not a refused correction.
        multiplicity: Multiplicity | None = None
        if candidates is not None:
            candidates, multiplicity = correct_field(candidates)
        # R30.1: the lineage is assumed, on every report, because nothing in this
        # package reads a declared one and R21.3 forbids this method starting.
        # `trend` then raises R21.5's caveat on every line drawn from today, which
        # is the true sentence about every log this tool can read -- it is not a
        # placeholder and it is not to be suppressed here. R21.5 rules that the
        # words are the producer's; all this passes is the fact of how the
        # succession was come by, which only the caller knows.
        #
        # `baseline.model_id`, and since R36.4 that is no longer a choice
        # between two readings. R30.4 picked this source over
        # `series[-1].baseline_model` on the argument that it "survives an empty
        # series"; R32.1 corrected the ruling, because `from_evidence` raises
        # `ArtifactError` on a log with no comparison record, so an empty series
        # never reaches this line and the case being guarded does not exist.
        # What was left was a real difference in *coercion* on falsy ids, and
        # R36.4 closed that at the source: `RunSummary.model_id` and
        # `RunPoint.baseline_model` are now one JSON field read through one
        # function, so the two expressions are equal on every log. R32.1's
        # scheduled one-liner is subsumed and was deliberately not also made --
        # swapping the expression now would change nothing and would re-open the
        # question of which reader is authoritative.
        #
        # Assembly is `CandidateLineage.assumed_from`'s and deliberately not
        # rebuilt here -- it restricts the assumption to `trend`'s own selection,
        # and a copy of the loop here could not see that rule.
        line = trend(
            series,
            baseline_model=baseline.model_id,
            lineage=CandidateLineage.assumed_from(
                series, baseline_model=baseline.model_id
            ),
        )
        # R30.3: the strip's two points are the line's, never the log's. `series`
        # is every experiment in the file in the file's order, so `series[-2:]`
        # can be two runs that are neither consecutive nor on one line; the strip
        # exists to license an attribution, and two runs the chart does not draw
        # as consecutive license the wrong one. An empty line yields an empty
        # tuple rather than a strip of NO_PREVIOUS_RUN rows against a run that
        # was never selected.
        strip = (
            ()
            if not line.points
            else parameter_strip(
                line.points[-2] if len(line.points) > 1 else None, line.points[-1]
            )
        )
        # The counting judge's candidate-side items, R26.3, computed from the
        # rows already parsed above -- see :attr:`spot_check`. ``counting`` and
        # ``counting_judge`` are the same selection :attr:`dimensions` was built
        # from, taken above and not repeated here.
        #
        # Guarded on the row existing rather than on its name. An empty panel has
        # no side whose counts could be passed and no subject that could name
        # one; a row whose ``name`` is blank has both, and the producer states
        # that gap in its own sentence, so it is not this module's to screen.
        spot = (
            None
            if counting is None
            else spot_check(
                int(counting.items_candidate.get("passing", 0) or 0),
                int(counting.items_candidate.get("failing", 0) or 0),
                int(counting.items_candidate.get("unstable", 0) or 0),
                subject=SpotCheckSubject(judge=counting_judge, side="candidate"),
            )
        )
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
            dated_apart=dated_apart,
            series=series,
            dimensions=dimensions,
            candidates=candidates,
            spot_check=spot,
            trend=line,
            parameter_strip=strip,
            multiplicity=multiplicity,
        )

    # -- derived ------------------------------------------------------------ #

    @property
    def is_demo(self) -> bool:
        """True when a ``Fake*`` adapter produced any run this document shows.

        Derived from the artifacts and never from a flag, so a hand-wired
        ``FakeAdapter`` run cannot produce a clean-looking report.

        The first two terms are the headline run's own sides and are what this
        property has always meant. The third is the series: once the document
        prints a timeline, a scripted night drawn on it is scripted numbers in
        the report, and a band that appears only when the *last* run was fake is
        a band you can remove by scripting the runs before it. Each term stands
        alone -- an empty series, or one whose adapter strings were never
        recorded, leaves the first two exactly as they were, because ``""``
        starts with nothing.
        """
        return (
            self.baseline.is_fake
            or self.candidate.is_fake
            or any(
                point.adapter_baseline.startswith(_FAKE_PREFIX)
                or point.adapter_candidate.startswith(_FAKE_PREFIX)
                for point in self.series
            )
        )

    @property
    def provenance(self) -> Provenance:
        """What this document can say about where its numbers came from.

        :attr:`is_demo` is untouched and still means exactly what it meant: it is
        the *positive* claim, and everything keyed off it -- the ``<title>``
        prefix, the latency omission, the appendix -- keeps keying off it. This
        adds the state it cannot express, which is "the evidence does not say".

        The precedence is R29.2's: scripted outranks unrecorded, because a
        ``Fake*`` adapter is a finding and a missing adapter is a gap.

        **The unrecorded state is read off the headline sides only, and R34.3
        keeps it that way.** The band sits over the headline's numbers and a
        reader takes it as being about them; widening its reach would put a claim
        about last month's runs on top of this comparison's verdict, which is
        R29.1's defect chosen deliberately.
        ``test_a_series_of_real_runs_does_not_band_the_report`` was written by
        C3's reviewer around exactly the input "an earlier run whose adapter
        strings are empty, under a real headline", and it still passes: nothing
        below can turn :attr:`state` on from the series.

        **What the series does now carry is the count.**
        :attr:`Provenance.unrecorded_comparisons` is series-scoped because
        :attr:`scripted_comparisons` is, and R34.1 ruled the two must be mirrors.
        A count is not a band: it changes what the sentences may say they
        checked, not which document gets banded.
        """
        unrecorded = tuple(
            name
            for name, side in (("baseline", self.baseline), ("candidate", self.candidate))
            if not side.adapter.strip()
        )
        if self.is_demo:
            state = PROVENANCE_SCRIPTED
        elif unrecorded:
            state = PROVENANCE_UNRECORDED
        else:
            state = PROVENANCE_RECORDED
        return Provenance(
            state=state,
            headline_scripted=self.baseline.is_fake or self.candidate.is_fake,
            comparisons=len(self.series),
            scripted_comparisons=sum(
                1
                for point in self.series
                if point.adapter_baseline.startswith(_FAKE_PREFIX)
                or point.adapter_candidate.startswith(_FAKE_PREFIX)
            ),
            unrecorded=unrecorded,
            unrecorded_comparisons=sum(
                1
                for point in self.series
                if not point.adapter_baseline.strip()
                or not point.adapter_candidate.strip()
            ),
            dated_apart=self.dated_apart,
        )

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


def _dated_apart(payload: Any, ts: str) -> int:
    """``1`` when this comparison's two clocks name different UTC days, else ``0``.

    Two clocks, two claims. ``payload["created"]`` is written by the comparison
    and is the date the timeline plots; ``record.ts`` is written when the line was
    appended to the log. A seed generator that dates fourteen nights into one
    sitting patches the first and cannot patch the second, so the gap is the
    signature of a document whose history was manufactured -- and C17's contract
    requires that asymmetry disclosed rather than left for a careful reader to
    notice and distrust.

    **A whole UTC day apart, and not a millisecond**, which is R29.3's threshold
    and is what makes this a detector rather than a coin flip. A real ``compare``
    writes ``created`` and appends the record microseconds later; the two strings
    routinely differ and occasionally straddle a second, so any tighter rule
    would fire on ``migkit demo`` and turn the disclosure into noise. Normalising
    to UTC before taking the date is the other half: ``2026-08-24T23:30-05:00``
    and ``2026-08-25T04:30Z`` are the same instant written by two machines, and a
    detector that read the calendar day off the string would call them a gap.

    Unparseable or absent on either side counts as no gap. That is deliberately
    *not* the same as "the dates agree" -- it is "this record cannot be asked" --
    and the distinction survives because the disclosure this feeds appears only
    on a positive count and says nothing otherwise.
    """
    mapping = payload if isinstance(payload, Mapping) else {}
    created = parse_created(str(mapping.get("created") or ""))
    written = parse_created(str(ts or ""))
    if created is None or written is None:
        return 0
    left = created.astimezone(timezone.utc).date()
    right = written.astimezone(timezone.utc).date()
    return 1 if left != right else 0


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


def _close_the_tally(
    tally: DimensionTally, gs_view: Mapping[str, Any], judge: str
) -> DimensionCounts:
    """Close the tally against the golden set, or hand back the golden set's own words.

    **Named for what it does rather than for what it looked like.** This was
    ``_dimension_counts``, which reads as a wrapper around
    :func:`~model_migration_kit.dimensions.dimension_counts` and is not one: that
    function takes a stream and a golden set and runs both phases back to back,
    while this one takes a :class:`~model_migration_kit.dimensions.DimensionTally`
    whose first phase already ran on ``from_evidence``'s single pass and calls
    :meth:`~model_migration_kit.dimensions.DimensionTally.counts` on it. The two
    are not interchangeable and the old name said they were. ``_close_the_tally``
    is the second phase, which is the thing C10 will reach for by name.

    Nothing here opens a file and nothing here reads the log a second time: the
    tally already holds everything the log had to say, in a form keyed by distinct
    input rather than by record -- so repeated draws of one item collapse onto one
    entry and a set sampled fifty times costs what the same set sampled once costs.

    That is *not* "bounded by the golden set rather than by the log", which is what
    this sentence used to claim and which
    :class:`~model_migration_kit.dimensions.DimensionTally` had already corrected
    on its own copy in the same commit series. Distinct inputs are the golden set's
    size only while the inputs come from the golden set. On the single pass this
    function closes, the join has not happened yet, so an input that joins to
    nothing cannot be recognised and is filed like any other: a log full of them
    grows the tally linearly, with no bound but the log. The tally's own docstring
    carries the measured constant and the ceiling it implies.

    **A golden-set refusal is quoted, never re-worded.** ``gs_view["reason"]``
    already explains a missing, unreadable, unrecorded or changed golden set in the
    words the completeness strip and the warnings list use. A second phrasing here
    would be a second chance for one of them to go stale, which is the argument
    :attr:`DetailBudget.sentence` makes for writing a disclosure once. The
    counting's own refusals arrive the same way, from
    :attr:`~model_migration_kit.dimensions.DimensionCounts.reason`.

    ``judge`` is the panel's first judge, taken from the comparison payload. A
    panel writes one verdict per judge per completion, so counting two would
    multiply every denominator by the panel size. An empty name -- a comparison
    that recorded no judges at all -- reaches the counter and comes back as its
    "this judge wrote nothing" refusal, which is the true sentence.
    """
    if not gs_view["available"]:
        return DimensionCounts(
            available=False, reason=str(gs_view["reason"]), by_model={}
        )
    return tally.counts(gs_view["by_id"], judge=judge)


def _matrix_tags(by_model: Mapping[str, Mapping[str, TagCount]]) -> tuple[str, ...]:
    """The tag universe of the counts, alphabetical, with ``UNTAGGED`` last.

    Read off the columns rather than off ``goldenset["tags"]``, which looks like
    the same list and is not: that one is built from tag *counts* and so has no
    entry for the untagged bucket at all. The columns are what the cells will be
    built from, so they are what the header has to be built from.

    They cannot disagree about anything else. :meth:`ReportModel.from_evidence` is
    the only caller, and it takes the counts and ``goldenset["tags"]`` from the
    same ``view.update(...)`` of the same loaded set -- so the "different golden
    sets" hazard this docstring used to give as its second reason describes a
    state the code cannot reach, and is gone (R27.7).

    The key is :data:`~model_migration_kit.dimensions.UNTAGGED` rather than an
    inline ``""``. The sentinel is empty on purpose -- ``"untagged"`` is a legal
    tag, and a set that used it would collide with this bucket and read as a
    larger slice -- and typing the empty string here would put a second, silent
    copy of that decision in a second file.
    """
    seen: set[str] = set()
    for column in by_model.values():
        seen.update(column)
    ordered = sorted(one for one in seen if one != UNTAGGED)
    if UNTAGGED in seen:
        ordered.append(UNTAGGED)
    return tuple(ordered)


def _matrix_models(
    by_model: Mapping[str, Mapping[str, TagCount]], baseline_id: str, candidate_id: str
) -> tuple[str, ...]:
    """Every model the matrix shows, the payload's two sides first and in order.

    The two sides come from the comparison payload and never from position in
    ``by_model``: the counter keys by ``model_id`` and does not know which side is
    which, so a matrix that took the first key as the baseline would swap the
    columns on any log whose model ids happen to sort the other way.

    A side the payload names that the counter never saw is still listed, and comes
    back below as a column of zeros. The alternative is a comparison rendered as a
    single column with nothing on the page saying where the other one went.
    """
    ordered = [baseline_id]
    if candidate_id != baseline_id:
        ordered.append(candidate_id)
    ordered.extend(sorted(set(by_model) - set(ordered)))
    return tuple(ordered)


#: Appended to every cell of a side the comparison payload names and the counting
#: never saw. Three different situations render as a column of zeros -- a judged
#: side that produced nothing, a side no judging pass ever closed, and a tag no
#: model produced -- and the first two have different fixes: check the judge
#: configuration, versus check whether the run completed at all. The note is where
#: this document says such things, so the distinction goes in the note rather than
#: in a field nothing else on the page would carry (R27.5). The zeros themselves
#: stay, because dropping the column would leave a one-column "comparison" with
#: nothing saying where the other side went.
_NEVER_CLOSED = (
    "No judging pass closed for {model_id} in this log, so this side was never "
    "counted at all: these zeros are a missing judging pass rather than a "
    "measurement."
)


def _tag_column(
    model_id: str,
    counted: Mapping[str, TagCount],
    tags: Sequence[str],
    *,
    confidence: float | None,
    floor: float | None,
    min_n: int,
    min_items: int,
    closed: bool,
) -> TagColumn:
    """One model's cells, one per tag in ``tags``, in that order.

    A tag the model produced nothing for is a cell of zeros rather than an absent
    one, because the tag was in the golden set: "measured nothing here" is a
    finding, and ``dimension_cell`` renders it as one. Nothing is invented to fill
    it -- the zeros are the count, and ``item_counts`` is never consulted, because
    it is an aggregate and splitting it across tags by any rule at all would be
    the invention this whole module exists to refuse.

    ``closed`` is whether the counting saw this model at all -- a model with a
    ``migkit.judging_completed`` is present in ``by_model`` even when it graded
    nothing, and a model the payload names and no judging pass ever closed is not.
    Both render as zeros and they are not the same finding, so the second one says
    so in its note. See :data:`_NEVER_CLOSED`.

    The floors arrive from :func:`_dimension_matrix` rather than being named again
    here, so that the numbers :class:`DimensionMatrix` publishes as what it refused
    against and the numbers the cells actually refused against are one expression
    rather than three that agree today.
    """
    cells: list[DimensionCell] = []
    for tag in tags:
        one = counted.get(tag)
        passes, n, items = (0, 0, 0) if one is None else (one.passes, one.n, one.items)
        cell = dimension_cell(
            tag,
            passes,
            n,
            items,
            confidence=confidence,
            floor=floor,
            min_n=min_n,
            min_items=min_items,
        )
        if not closed:
            said = _NEVER_CLOSED.format(model_id=model_id)
            cell = replace(cell, note=f"{cell.note} {said}".strip())
        cells.append(cell)
    return TagColumn(model_id=model_id, cells=tuple(cells))


def _dimension_matrix(
    counts: DimensionCounts,
    *,
    judge: str,
    baseline_id: str,
    candidate_id: str,
    confidence: float | None,
    floor: float | None,
) -> DimensionMatrix:
    """Turn one judge's raw per-tag counts into the table the document prints.

    **A decline is passed through and never re-worded.** ``counts.reason`` already
    holds whichever of the six sentences applies -- the golden set's own, quoted
    by :func:`_close_the_tally`, or one of the five the counting writes for itself
    -- and every one of them names a different fix. Rephrasing here would be a
    third copy of a disclosure that already has two.

    **A decline also comes back carrying nothing**, which is not tidiness. Every
    way this table can decline is global rather than per-cell, so there is no
    subset of it that happens to be sound; emptiness is what stops a renderer
    being tempted by one. That is the promise
    :class:`~model_migration_kit.dimensions.DimensionCounts` makes about
    ``by_model``, kept one layer up.

    **The two floors are read once, here.** They are the numbers the matrix
    publishes as what it refused against *and* the numbers the cells are refused
    against, and until R27.7 they were three separate references to the module
    constants -- a design whose own docstring claimed it was one expression. One
    local, passed to both, is what makes that sentence true.
    """
    min_n, min_items = MIN_N_FOR_A_VERDICT, MIN_ITEMS_FOR_A_VERDICT
    if not counts.available:
        return DimensionMatrix(
            available=False,
            reason=counts.reason,
            judge=judge,
            tags=(),
            baseline=TagColumn(model_id="", cells=()),
            candidates=(),
            min_n=min_n,
            min_items=min_items,
        )
    tags = _matrix_tags(counts.by_model)
    columns = {
        model_id: _tag_column(
            model_id,
            counts.by_model.get(model_id, {}),
            tags,
            confidence=confidence,
            floor=floor,
            min_n=min_n,
            min_items=min_items,
            closed=model_id in counts.by_model,
        )
        for model_id in _matrix_models(counts.by_model, baseline_id, candidate_id)
    }
    return DimensionMatrix(
        available=True,
        reason="",
        judge=judge,
        tags=tags,
        baseline=columns[baseline_id],
        candidates=tuple(
            column for model_id, column in columns.items() if model_id != baseline_id
        ),
        min_n=min_n,
        min_items=min_items,
    )


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


#: What the page says when the payload carries a ``warnings`` value this report
#: cannot read. It states the gap and claims nothing on either side, in the same
#: register as the exclusions note: an absence must not render as a measurement,
#: and a silent warnings section is the measurement "nothing was flagged".
_WARNINGS_NOT_RECORDED = (
    "this comparison's own warnings are recorded as a value this report cannot "
    "read, so none of them were carried onto this page. Read this as not known, "
    'and never as "the comparison recorded no warnings".'
)


def _payload_warnings(value: Any) -> list[str]:
    """The comparison's own warnings, or a stated gap when they cannot be read.

    **A null ``warnings`` is not an empty ``warnings``.** ``[]`` is a writer that
    ran the comparison and recorded that it produced none; a value that is not a
    list of warnings at all is a writer that had somewhere to say so and said
    nothing readable. Only the first is a measurement, and the page's silence
    about warnings is read as exactly that measurement -- the warnings list is
    where "60 completions cannot detect a 10% drop" appears, so a reader who sees
    no warnings section concludes there was nothing to see. Coercing the second
    case into the first would print that conclusion off no evidence, which is this
    package's central rule inverted. The gap therefore goes into the warnings list
    itself, which is already where this reconstruction says what it could not read
    (see :func:`_load_artifact`).

    **An absent key is left as it was**, at ``()``: ``payload.get("warnings", ())``
    is a decision somebody made and wrote down, and every log this tool writes
    carries the key (``comparison.py`` emits ``list(self.warnings)``
    unconditionally), so an absent key is a foreign or pre-field log rather than
    the state this rules on. Re-deciding it is a separate question and is left
    visibly open rather than quietly assumed.

    **A bare string is one warning, never its letters.** ``str`` is iterable, so
    the obvious comprehension renders ``"careful"`` as seven single-character
    rows. :func:`model_migration_kit.series._warnings` ruled this and is tested on
    it; the two readers of the same payload field must not disagree about it.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(one) for one in value]
    return [_WARNINGS_NOT_RECORDED]


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
    # **Not** `_text`, and R36.4 says so explicitly. `RunSummary.adapter` also
    # disagrees with `RunPoint.adapter_baseline`/`adapter_candidate`, but for a
    # *second, unrelated* reason: the lines below prefer `run.header.adapter`
    # over the payload, and join a multi-adapter run into one comma-joined
    # cell. Converting the coercion here would fold two different disagreements
    # into one edit and leave the larger one still standing while looking
    # closed. Reported, left.
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
        # R36.4: `_text`, so this side's id is the same string `RunPoint` reads
        # off the same JSON field. It was `str(side.get("model_id", "") or "")`,
        # and on a falsy-but-recorded id the two readers disagreed -- which on
        # the baseline side left the run in *none* of `Trend`'s seven fields,
        # since `trend` selects on `point.baseline_model == baseline_model` and
        # `""` matches nothing. A run in the log and on no part of the page is
        # R24.1, and it was live here.
        model_id=_text(side.get("model_id")),
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
        # R36.4's other three shared sites. `RunPoint` reads `judges[0]`'s name,
        # model id and rubric hash through `_text`; these three read the same
        # keys of the same mapping, so they coerce through the same function.
        name=_text(raw.get("name")),
        model_id=_text(raw.get("model_id")),
        rubric_hash=_text(raw.get("rubric_hash")),
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


#: The scripted paragraph's closing, which is true in both of R29.1's cases and
#: is the sentence the whole disclosure exists to deliver.
_MACHINERY_IS_REAL = (
    "The only real thing in this document is the machinery: the sampling, the "
    "judging, the statistics and the decision rules are the production paths, "
    "exercised end to end. The quality difference they measure was written into "
    "the script."
)


def _scripted_paragraph(model: ReportModel, provenance: Provenance) -> str:
    """The appendix's opening paragraph, for a document with scripted runs in it.

    **Two openings, not one opening with a variable in it** -- R29.1, and the
    defect it rules on was live in the rendered document. The old paragraph was
    headline-scoped while ``is_demo`` is series-scoped, so a real headline over a
    scripted history printed, verbatim:

        At least one side of this comparison was produced by a Fake adapter
        (AnthropicAdapter for the baseline, OpenAICompatAdapter for the
        candidate).

    Both named adapters are real. That is worse than an absence rendering as a
    measurement: it is a disclosure disclosing the wrong thing, in the paragraph
    a sceptical reader opens first, and it named two real adapters *as the
    evidence for its own claim*. The second opening therefore names no adapter at
    all -- the evidence is in comparisons the sentence is not about, and there is
    nothing on this side of the document to point at.

    Neither opening describes an unnamed side as real. The scripted state
    outranks the unrecorded one, so a headline side whose adapter was never
    recorded can reach here, and "both sides are real" would be a claim the
    evidence does not support.
    """
    if provenance.headline_scripted:
        opening = (
            "These numbers describe scripted responses, not a real provider. At "
            f"least one side of this comparison was produced by a Fake adapter "
            f"({model.baseline.adapter or 'unknown'} for the baseline, "
            f"{model.candidate.adapter or 'unknown'} for the candidate)."
        )
    else:
        opening = (
            "This document draws scripted runs, and the comparison in front of "
            "you is not one of them: neither of its sides names a Fake adapter. "
            "No adapter is named here as the evidence, because the scripted runs "
            "are other comparisons on the timeline and naming this one's sides "
            "would name the wrong runs."
        )
    return " ".join(
        one
        for one in (
            opening,
            _counted_paragraph(provenance),
            _MACHINERY_IS_REAL,
            _dated_sentence(provenance),
        )
        if one
    )


def _counted_paragraph(provenance: Provenance) -> str:
    """How much of the document is scripted, counted in comparisons. R29.4.

    Comparisons and never runs: a :class:`~model_migration_kit.series.RunPoint`
    carries no run id, so two comparisons against one baseline run cannot be told
    from two comparisons against two, and a run count would render 84 for a
    document holding 56 runs. The reasoning is on :class:`Provenance`.

    Silent when the document draws no timeline at all, because ``0 of 0`` is a
    model built by hand rather than a measured absence of scripted runs.
    """
    if not provenance.comparisons:
        return ""
    total = provenance.comparisons
    scripted = provenance.scripted_comparisons
    if scripted == total:
        if total == 1:
            return (
                "The one comparison drawn in this document names a Fake adapter on "
                "at least one side."
            )
        return (
            f"All {total} comparisons drawn in this document name a Fake adapter on "
            f"at least one side."
        )
    if scripted:
        rest = total - scripted
        return (
            f"{scripted} of the {total} comparisons drawn in this document "
            f"{'names' if scripted == 1 else 'name'} a Fake adapter on at least one "
            f"side; {'the other one does not' if rest == 1 else f'the other {rest} do not'}."
        )
    if total == 1:
        return (
            "The one comparison drawn in this document names no Fake adapter in its "
            "own payload: this paragraph is here because the run artifacts the "
            "headline read do, which is a disagreement between the log and the "
            "files it points at."
        )
    return (
        f"None of the {total} comparisons drawn in this document name a Fake "
        f"adapter in their own payloads: this paragraph is here because the run "
        f"artifacts the headline read do, which is a disagreement between the log "
        f"and the files it points at."
    )


def _dated_sentence(provenance: Provenance) -> str:
    """C17's timestamp asymmetry, disclosed when it is there and not when it is not.

    R29.3. Unconditional prose was refused because it is false on ``migkit demo``,
    whose comparison ``created`` and whose record ``ts`` are the same instant -- an
    asymmetry asserted where none was measured, inside the chunk whose subject is
    unsuppressible honesty.
    """
    if not provenance.dated_apart:
        return ""
    if provenance.dated_apart == provenance.comparisons == 1:
        opening = (
            "The one comparison here records a created date on a different UTC day "
            "from the evidence record carrying it."
        )
    elif provenance.dated_apart == provenance.comparisons:
        opening = (
            f"All {provenance.comparisons} comparisons record a created date on a "
            f"different UTC day from the evidence record carrying each."
        )
    else:
        opening = (
            f"{provenance.dated_apart} of the {provenance.comparisons} comparisons "
            f"{'records' if provenance.dated_apart == 1 else 'record'} a created "
            f"date on a different UTC day from the evidence record carrying each."
        )
    return (
        f"{opening} Those are two clocks making two claims: created is written by "
        f"the comparison, and the record's own timestamp is written when the line "
        f"was appended to the log. A document seeded one night at a time has that "
        f"gap by construction -- the comparison dates are the seed's and the run "
        f"and judging records keep the real clock -- and it is disclosed here "
        f"rather than left for a reader to find and distrust."
    )


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
    # R29.2 decided two surfaces for the unrecorded state -- the band and the
    # terminal -- and this appendix is neither. The scripted paragraph stays
    # because §5.3 named it as one of the five places that say the models are
    # scripted; a third wording of the unrecorded gap would be a third thing to
    # keep in step for no ruling that asked for it.
    provenance = model.provenance
    if provenance.state == PROVENANCE_SCRIPTED:
        tested.append(_scripted_paragraph(model, provenance))
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


def _pp(value: Any, dash: str = EM_DASH) -> str:
    """A percentage-point delta, signed, at one decimal place.

    Rounded here and never on the model.
    :attr:`~model_migration_kit.series.Candidate.delta_pp` is deliberately
    unrounded so that a renderer wanting one decimal place can take one, while a
    model that had already rounded could not give back what it dropped. This is
    that edge, and it is also where the binary-float residue
    (``9.999999999999998`` where the arithmetic says ten) stops being visible.

    The sign is explicit because the number's whole subject is direction: an
    unsigned ``0.6`` beside an unsigned ``0.4`` reads as two magnitudes, and one of
    them may be a regression.

    ``None`` is the dash, on :func:`_pct`'s rule: a delta that could not be
    computed because a side recorded no rate is not a delta of zero.
    """
    number = _number(value)
    if number is None:
        return dash
    return f"{number:+.1f} pp"


def _days(value: Any, dash: str = EM_DASH) -> str:
    """A span in days at one decimal place, or the dash where none was measurable.

    ``None`` is the dash and is never ``0.0``. Three findings arrive at this filter
    and only one of them is a zero: a field whose spread could not be measured at
    all (fewer than two dated rows), a row whose own date was never recorded, and
    the newest row in a field, which really is zero days old. Printing the first
    two as ``0.0 days`` would state "measured in a single sitting" on the evidence
    for "we do not know when this was measured" -- the failure this document is
    built to refuse, in the smallest possible space.
    """
    number = _number(value)
    if number is None:
        return dash
    return f"{number:.1f} days"


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
    # Both provenance bands, from the one place their words are written. R29.2
    # item 3: the terminal and the HTML must say the same words, which they did
    # not while each renderer held its own copy of the scripted sentence. The
    # separator is an ASCII hyphen and not the HTML's em dash on purpose -- rich
    # substitutes box characters on a legacy Windows console and not arbitrary
    # text, and this line prints before anything else has had a chance to fail.
    provenance = model.provenance
    if provenance.banded:
        out.print(
            Panel(
                Text(f"{provenance.label} - {provenance.sentence}", style="bold"),
                border_style=provenance.border,
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
.band.unrecorded {
  background: #fbe9c8;
  color: #4a3400;
  border: 2px solid #a8760a;
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
.banner .bar {
  margin: 0.75rem 0 0 0;
}
.chart {
  margin: 0.75rem 0 0.25rem 0;
}
/* Both charts are drawn to a fixed viewBox and scaled down by the viewport, so
   a narrow window shrinks them rather than scrolling the page sideways. */
.banner .bar svg, .chart svg {
  max-width: 100%;
  height: auto;
}
.draws {
  color: #4a5058;
  font-size: 0.88rem;
  margin: 0.15rem 0 0.6rem 0;
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
/* A cell whose sample is too small to read as a judgement. Shaded and never
   coloured: DimensionCell.verdict_refused is the only field that decides whether
   the numbers may be read as a verdict, and a refused cell still shows its
   interval -- what it does not do is claim one. */
td.refused {
  background: #f6f7f9;
}
.cellnote {
  display: block;
  color: #46301b;
  font-size: 0.85rem;
  margin-top: 0.2rem;
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
{% if model.provenance.banded %}
<div class="band {{ model.provenance.css }}" id="{{ model.provenance.anchor }}" role="alert">
{{ model.provenance.label }} {{ dash }} {{ model.provenance.sentence }}
</div>
{% endif %}
<main>
<section class="banner {{ verdict_class }}" id="verdict">
  <p class="word">{{ model.verdict_word }}</p>
  <p class="reason">{{ model.verdict_reason }}</p>
  <div class="bar">{{ model | interval_bar | safe }}</div>
  <p class="meta">
    Exit code a CI system would have received: <strong>{{ model.exit_code }}</strong>
    {{ dash }} decided by {{ model.decided_by or 'no recorded rule' }}
    {{ dash }} generated {{ generated }}
  </p>
</section>

{#- Bound once and read by both the nav and the sections below, so that a link
    and the section it points at cannot come to disagree about whether the
    section exists. `excluded` renders in two disjoint states -- a named list,
    and the sentence for a log that cannot name its exclusions at all -- and
    spelling that condition twice is how a nav entry starts dangling. -#}
{% set candidate_field = model.candidates %}
{% set excluded_shown = candidate_field is none or candidate_field.excluded %}
{% set matrix = model.dimensions %}
{#- The one line, bound once for the same reason: the nav entry for the parameter
    strip and the strip itself are gated on `trend.points` (R33.1), and a gate
    spelled twice is a gate that comes apart. `parameter_strip` is deliberately
    *not* the gate -- when there is a line the tuple is never empty, so gating on
    it would publish "no parameters tracked" over a log that simply has no line. -#}
{% set line = model.trend %}
{% set on_line = line.points | length %}
{% set logged = model.series | length %}
<nav>
  <ol>
{% if candidate_field is not none %}
    <li><a href="#candidates">Candidates measured against one baseline</a></li>
{% endif %}
{% if excluded_shown %}
    <li><a href="#excluded">Runs outside the candidate table</a></li>
{% endif %}
    <li><a href="#dimensions">Results by dimension</a></li>
    <li><a href="#compared">What was compared</a></li>
{% if model.series %}
    <li><a href="#timeline">Run history</a></li>
{% endif %}
{% if line.points %}
    <li><a href="#parameters">Parameters across the line's last two runs</a></li>
{% endif %}
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

{#- The spot check. `id="counterfactual"` is the contract's own and is not to be
    improved on: a link that changes its target is a link somebody else's document
    has already got wrong.

    Gated on `is not None`, never defaulted: the producer declines on three
    separate grounds -- nothing failed, the check would read every item, or there
    is no set at all -- and a zero here would print an absence as a measurement.

    **The sentence is the producer's and is printed, not composed.** SpotCheck
    carries its judge and its side in the sentence itself, so nothing here
    captions around it to supply a subject it already states; two renderings of
    one fact is how they come to disagree. Nothing below quotes a number from the
    record either -- k, N, F and the probability are in the sentence. -#}
{% if model.spot_check is not none %}
<p class="note" id="counterfactual">
  <strong>What a hand check would have missed.</strong>
  {{ model.spot_check.sentence }}
  <span class="secondary">This is arithmetic about a check nobody ran, not a
  measurement this run took {{ dash }} it is the argument for having run the
  harness, and it is the one number here a sceptical reader should redo.</span>
</p>
{% endif %}

{% if candidate_field is not none %}
<h2 id="candidates">Candidates measured against one baseline</h2>
<p class="secondary">
  Every candidate model this evidence log measured under one comparability key:
  same golden set, same judges, same draws per item, same baseline model. Rows are
  ordered by model name and never by result {{ dash }} a table sorted by its own
  outcome invites the reading that position <em>is</em> the outcome, and this is a
  set of measurements taken under one key, not a ranking.
</p>
<dl class="facts">
  <dt>baseline model</dt>
      <dd><code>{{ candidate_field.key.baseline_model or dash }}</code></dd>
  <dt>baseline pass rate</dt>
      <dd>{{ candidate_field.baseline_pass_rate | pct }}
      <span class="secondary">{{ dash }} one reading, taken from the newest run in
      this field. Every delta below was computed against the baseline
      <em>its own</em> run measured, so adding this number back to a delta does not
      recover that row's pass rate: wherever the baseline moved between two nights
      the sum is a rate no run recorded.</span></dd>
  <dt>golden set hash</dt>
      <dd><span class="hash">{{ candidate_field.key.goldenset_hash or dash }}</span></dd>
  <dt>judges hash</dt>
      <dd><span class="hash">{{ candidate_field.key.judges_hash or dash }}</span></dd>
  <dt>n per item</dt><dd>{{ candidate_field.key.n_per_item or dash }}</dd>
  <dt>measured how far apart</dt>
      <dd>
      {% if candidate_field.spread_days is none %}
      not measurable {{ dash }} fewer than two rows carry a date, and one dated run
      is a single observation, which can no more say how far apart this field was
      taken than no observation can
      {% else %}
      {{ candidate_field.spread_days | days }} between the oldest and the newest
      row, against a window of {{ candidate_field.stale_after_days | days }}
      {% if candidate_field.spread_flagged %}
      {{ dash }} <strong>wider than the window</strong>: these rows may not have
      been measured close enough together to be read side by side
      {% endif %}
      {% endif %}
      </dd>
</dl>
<table>
  <thead><tr>
    <th>candidate</th><th>run</th><th>pass rate</th>
    <th>delta vs its own baseline</th><th>age in this field</th>
  </tr></thead>
  <tbody>
  {% for row in candidate_field.candidates %}
    <tr>
      <td><code>{{ row.model or dash }}</code></td>
      <td>{{ row.point.created or 'no recorded date' }}
          <span class="secondary">({{ row.point.created_source }} clock)</span></td>
      <td class="num">{{ row.point.pass_rate | pct }}</td>
      <td class="num">{{ row.delta_pp | pp }}</td>
      <td class="num">{{ row.stale_days | days }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% if candidate_field.caveats %}
<p class="secondary">Rows that are in the table above under protest:</p>
<ul class="secondary">
  {% for caveat in candidate_field.caveats %}
  <li>
    {% if caveat.point is none %}
    about this field as a whole:
    {% else %}
    <code>{{ caveat.point.candidate_model or 'unnamed candidate' }}</code>,
    {{ caveat.point.created or 'undated' }}:
    {% endif %}
    {{ caveat.reason }}
  </li>
  {% endfor %}
</ul>
{% endif %}
{% endif %}

{#- The multiplicity note, gated on `model.multiplicity` and deliberately not on
    the candidate table (R33.1). R30.4 makes the two `None` together, so the gates
    select the same documents today -- but a note gated on a *different* field is a
    note that can outlive its subject, and this one is only ever true of the table
    above it.

    `note` is the producer's sentence and carries the method, the family size, the
    alpha, the untested rows and what the correction changed. It is printed, never
    summarised: `Multiplicity` exists to make the correction *sayable*, and the
    failure it is shaped against is a report that states a guard was applied while
    showing nothing it did. `thresholds` is the one field the sentence does not
    carry, which is why it renders below rather than being left in the record. -#}
{% if model.multiplicity is not none %}
<div class="note" id="multiplicity">
  <strong>Correcting across the candidates in that table.</strong>
  {{ model.multiplicity.note }}
  {% if model.multiplicity.thresholds %}
  The threshold each candidate's p-value was held to, for display and diagnosis
  {{ dash }} significance is decided by the step-down that produced these numbers
  and never by reading a p-value against the one beside it:
  <ul>
    {% for name, threshold in model.multiplicity.thresholds.items() %}
    <li><code>{{ name }}</code> {{ dash }} {{ threshold | num }}</li>
    {% endfor %}
  </ul>
  {% endif %}
</div>
{% endif %}

{% if excluded_shown %}
<h2 id="excluded">Runs outside the candidate table</h2>
{% if candidate_field is none %}
<p class="note">
  <strong>There is no candidate table above, and this page cannot list what was
  left out of one.</strong> No group of runs in this log both shares a
  comparability key and names two distinct candidate models, so no field of
  candidates could be assembled {{ dash }} and the sentence explaining each
  omission is written by the same pass that assembles it, so those sentences do
  not exist either. Runs in this log may have been excluded from a comparison
  without this page being able to name them. Read this as <em>not known</em>, and
  never as "nothing was excluded".
</p>
{% else %}
<p class="secondary">
  Every point in this log the table above does not hold, each with the sentence
  saying why {{ dash }} runs under <em>other</em> comparability keys included, so
  that a table which quietly dropped a third of the log cannot look complete.
</p>
<ul>
  {% for one in candidate_field.excluded %}
  <li><code>{{ one.point.candidate_model or 'unnamed candidate' }}</code>
      {{ dash }} {{ one.point.created or 'no recorded date' }}
      {{ dash }} {{ one.reason }}</li>
  {% endfor %}
</ul>
{% endif %}
{% endif %}

<h2 id="dimensions">Results by dimension</h2>
{% if not matrix.available %}
<p class="note">
  <strong>There is no per-dimension table.</strong> {{ matrix.reason }}
</p>
{% elif not matrix.tags %}
<p class="note">
  <strong>There is no per-dimension table.</strong> The counting ran and came back
  with no tags to break this run down by, so there is nothing here to show. That
  is an empty tag universe in the golden set, not a set of dimensions that every
  model scored zero on.
</p>
{% else %}
{% set columns = [matrix.baseline] + (matrix.candidates | list) %}
<p class="secondary">
  How every tag in the golden set did, per model, under
  <strong>{{ matrix.judge }}</strong> {{ dash }} the panel's first judge, and the
  only judge these numbers come from; a panel writes one verdict per judge per
  completion and they are never summed. Counted from the run this report is about
  and not from the whole log: the run history below is the history, this breaks the
  banner's own number down by tag. A cell is shaded where the sample cannot
  support a verdict at all {{ dash }} the numbers are still printed, because
  declining to read a measurement as a judgement is not the same as not having
  one. The floors every cell here was judged against are {{ matrix.min_n }}
  graded completions and {{ matrix.min_items }} items.
</p>
<table>
  <thead><tr>
    <th>dimension</th>
    {% for column in columns %}
    {#- loop.index0 == 1 is matrix.candidates[0], which is the comparison's own
        candidate by construction (R27.8). Named by position rather than through a
        matrix.candidate accessor: a second way to name one side is a second thing
        that can disagree with the first. -#}
    <th><code>{{ column.model_id or dash }}</code>
        <span class="secondary">
        {% if loop.first %}baseline
        {% elif loop.index0 == 1 %}candidate
        {% else %}also judged in this run
        {% endif %}
        </span></th>
    {% endfor %}
  </tr></thead>
  <tbody>
  {% for tag in matrix.tags %}
    <tr>
      <th>{{ tag }}</th>
      {% for column in columns %}
      {% set cell = column.cell(tag) %}
      <td class="num{% if cell.verdict_refused %} refused{% endif %}">
        {{ cell.rate | pct }}
        <span class="secondary">{{ cell.interval | interval }}</span><br>
        <span class="secondary">{{ cell.passes }} of {{ cell.n }} graded
        completions, over {{ cell.items }} item(s)</span>
        {% if cell.note %}
        <span class="cellnote">{{ cell.note }}</span>
        {% endif %}
      </td>
      {% endfor %}
    </tr>
  {% endfor %}
  </tbody>
</table>
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
        <td>{{ model.threshold_sources.get(name, unrecorded) | source_label }}</td></tr>
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
  claim about the default. A source that is a file is named by its filename here;
  its full path is shown once, whole, and where it can be checked {{ dash }} under
  <em>config</em> in "What was compared", above.
</p>

{% if model.series %}
{% set timeline = model.series | timeline %}
<h2 id="timeline">Run history {{ dash }} {{ model.series | length }} comparison(s) in this log</h2>
<div class="chart">{{ timeline.svg | safe }}</div>
<p class="secondary">
  Every comparison this evidence log holds, oldest first, with the candidate's
  pass rate against the floor each run was actually held to. <strong>The axis is
  time, not run number</strong>, so a three-week gap between the run that was
  green and the run that was not is drawn as three weeks. Nothing is
  interpolated: no line joins the markers, because a line between two runs would
  assert a pass rate on the dates in between, and on those dates nothing ran. The
  banner above, and the bar inside it, report the <strong>last comparison this log
  records</strong> {{ dash }} which is the newest marker on this chart whenever the
  clock agrees with the file, and is not when it does not: the series is drawn in
  time order and the log is read in write order, and a run appended with an older
  timestamp sits to the left of the run the banner describes.
</p>
{% if timeline.runs_without_rate or timeline.runs_without_floor %}
<p class="secondary">Not everything could be drawn, and the gaps are counted
rather than hidden:</p>
<ul class="secondary">
  {% if timeline.runs_without_rate %}
  <li>{{ timeline.runs_without_rate }} run(s) recorded no pass rate, so they
      carry no marker</li>
  {% endif %}
  {% if timeline.runs_without_floor %}
  <li>{{ timeline.runs_without_floor }} run(s) recorded no floor, so the rule is
      broken where they sit {{ dash }} which is a gap in the record, not a floor
      of zero</li>
  {% endif %}
</ul>
{% endif %}
{#- The lineage block (R33.2). Below the chart and inside this section, because
    its whole job is the *difference* between the log the chart draws and the line
    the lineage names -- which is a paragraph about two sets, not a second chart.
    The chart above is still `model.series` and is deliberately not re-pointed at
    `Trend.points`: its heading says "in this log", and re-pointing it would
    silently drop every run the lineage does not name.

    It is rendered whenever there is a chart, and it is never a heading over
    nothing: `trend` raises R21.5's assumed-lineage caveat on every line this
    project can currently draw, and the paragraph below always states how much of
    the log the line holds. The alternative -- gating the block on the disclosures
    being non-empty -- drops exactly that caveat on the commonest document there
    is, which is the defect this chunk exists to fix.

    The count sentence says "N of M" rather than "the line is the whole log",
    because the two are not the same claim: a run measured against a *different*
    baseline is not selected, is not excluded, is in none of these fields, and a
    page claiming to be the whole log would be wrong about it. -#}
{#- `Caveat.point` is `RunPoint | None`; the note about the *line* is the one
    with no point. Split by asking rather than by indexing: rendering a claim
    about how the chart was assembled against whichever night happened to anchor
    it is an absence rendering as a measurement, from the rendering side. -#}
{% set line_notes = line.caveats | selectattr("point", "none") | list %}
{% set run_notes = line.caveats | rejectattr("point", "none") | list %}
<h3>The candidate line, and what it leaves out</h3>
<p class="secondary">
  The chart above draws every comparison this log holds. The candidate line
  {{ dash }} one lineage of candidate models against one baseline, which is what
  the parameter strip and the successions below are about {{ dash }} draws
  {{ on_line }} of those {{ logged }} comparison(s).
  {% if on_line == logged %}
  Every comparison in this log is on it, so nothing was left off the line.
  {% else %}
  What became of the rest is below, as far as this log can say: a run measured
  against a <em>different baseline</em> is not a run of this line at all, was
  never adjudicated as one, and is not listed here.
  {% endif %}
</p>
{% if line_notes %}
<p class="secondary">Notes on the line itself, rather than on any one run:</p>
<ul class="secondary">
  {% for caveat in line_notes %}
  <li>{{ caveat.reason }}</li>
  {% endfor %}
</ul>
{% endif %}
{% if run_notes %}
<p class="secondary">Runs that are on the line under protest:</p>
<ul class="secondary">
  {% for caveat in run_notes %}
  <li><code>{{ caveat.point.candidate_model or 'unnamed candidate' }}</code>
      {{ dash }} {{ caveat.point.created or 'no recorded date' }}
      {{ dash }} {{ caveat.reason }}</li>
  {% endfor %}
</ul>
{% endif %}
{% if line.excluded %}
<p class="secondary">Runs kept off the line, each with the sentence saying why
{{ dash }} the count without the reason is the list that is worse than none:</p>
<ul class="secondary">
  {% for one in line.excluded %}
  <li><code>{{ one.point.candidate_model or 'unnamed candidate' }}</code>
      {{ dash }} {{ one.point.created or 'no recorded date' }}
      {{ dash }} {{ one.reason }}</li>
  {% endfor %}
</ul>
{% endif %}
{% if line.undated %}
<p class="secondary">
  {{ line.undated }} otherwise-comparable run(s) recorded no date any axis can
  place them on, so they sit at no point on the chart and on no line. That is a
  gap in the record, not a run that happened at the beginning of time.
</p>
{% endif %}
{% if line.outside_lineage %}
<p class="secondary">
  Runs measured against <em>this</em> baseline whose candidate model the lineage
  does not name. They are not exclusions {{ dash }} an exclusion is a
  comparability verdict, and these were never adjudicated because they were never
  selected {{ dash }} but a page that did not list them would say the line was
  drawn in full when it was not:</p>
<ul class="secondary">
  {% for point in line.outside_lineage %}
  <li><code>{{ point.candidate_model or 'unnamed candidate' }}</code>
      {{ dash }} {{ point.created or 'no recorded date' }}</li>
  {% endfor %}
</ul>
{% endif %}
{% if line.absent_models %}
<p class="secondary">
  Candidate models the lineage names that have no run anywhere in this log. A
  model named and never heard of is likelier to be a typo in the declaration than
  a quiet night:</p>
<ul class="secondary">
  {% for name in line.absent_models %}
  <li><code>{{ name }}</code></li>
  {% endfor %}
</ul>
{% endif %}
{% if line.successions %}
<p class="secondary">
  Where the candidate model changed, inside this one line. The line is still one
  line {{ dash }} these ids are one lineage {{ dash }} and an unbroken line read
  without this list reads as one unbroken model:</p>
<ul class="secondary">
  {% for one in line.successions %}
  <li><code>{{ one.before or 'unnamed candidate' }}</code> gave way to
      <code>{{ one.after or 'unnamed candidate' }}</code> at
      {{ one.created or 'no recorded date' }}</li>
  {% endfor %}
</ul>
{% endif %}
{% endif %}

{#- The parameter strip, gated on `model.trend.points` and never on
    `len(model.series) >= 2` (R33.1) or on the strip being non-empty: the strip is
    fed from the *line*, so a four-run log with no line has two runs in `series`
    and an empty strip, and gating on either would put a heading over nothing.
    When there is a line the tuple is never empty -- one row per tracked
    parameter, the ones that held included -- so empty means no line, and the
    reason for that is in the block above. -#}
{% if line.points %}
{#- Display labels, which is the template's job: `ParameterChange.name` carries
    identifier-safe *keys*, deliberately, because a template deriving a class or
    an anchor from a label broke on the sixth row once already. The key is printed
    beside the label so a reader can join the row back to the record, and an
    unknown key falls back to itself rather than rendering blank. -#}
{% set parameter_labels = {
     'model_id': 'candidate model',
     'n_per_item': 'draws per item',
     'items': 'golden-set items',
     'judges': 'judge panel',
     'goldenset': 'golden set',
     'config': 'config',
   } %}
<h2 id="parameters">Parameters across the line's last two runs</h2>
<p class="secondary">
  Every parameter this report tracks, as it stood on the last two runs
  <em>of the line above</em> {{ dash }} which need not be the last two runs in the
  log, and need not include the run the banner describes. The list is printed
  whole every time, the parameters that held included: a strip showing only what
  moved cannot be told apart from a strip that was not looking.
  {% if on_line > 1 %}
  The two runs are {{ line.points[-2].created or 'a run with no recorded date' }}
  and {{ line.points[-1].created or 'a run with no recorded date' }}.
  {% else %}
  This line holds one run, at {{ line.points[-1].created or 'no recorded date' }},
  so there is nothing before it to compare against and every earlier value says
  so in words rather than sitting blank {{ dash }} a blank cell in a table of
  values reads as "same as above", and this one is not.
  {% endif %}
</p>
<table>
  <thead><tr>
    <th>parameter</th><th>before</th><th>after</th><th>changed</th>
  </tr></thead>
  <tbody>
  {% for row in model.parameter_strip %}
    <tr>
      <td>{{ parameter_labels.get(row.name, row.name) }}
          <span class="secondary"><code>{{ row.name }}</code></span></td>
      <td>{{ row.before }}</td>
      <td>{{ row.after }}</td>
      <td>{{ row.changed | flag }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}

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
{% if model.baseline.is_fake and model.candidate.is_fake %}
<p class="secondary">
  <strong>Not measured.</strong> Both sides of this comparison ran on scripted
  adapters, which return their answers without calling a provider, so every
  timing here would be a few microseconds of local dictionary lookup. The table
  is omitted rather than printed as zeros: a row that reads
  <code>0.000 / 0.000</code> is not a fast model, it is the absence of a
  measurement, and a reader should not have to work that out.
</p>
{% else %}
<table>
  <thead><tr><th></th><th>median (s)</th><th>p90 (s)</th></tr></thead>
  <tbody>
    <tr><td>baseline</td>
    {% if model.baseline.is_fake %}
        <td colspan="2">not measured {{ dash }} scripted adapter</td>
    {% else %}
        <td class="num">{{ model.baseline.latency_median | num3 }}</td>
        <td class="num">{{ model.baseline.latency_p90 | num3 }}</td>
    {% endif %}
        </tr>
    <tr><td>candidate</td>
    {% if model.candidate.is_fake %}
        <td colspan="2">not measured {{ dash }} scripted adapter</td>
    {% else %}
        <td class="num">{{ model.candidate.latency_median | num3 }}</td>
        <td class="num">{{ model.candidate.latency_p90 | num3 }}</td>
    {% endif %}
        </tr>
  </tbody>
</table>
{% endif %}

{% if not model.goldenset.available %}
<div class="band mismatch" id="goldenset-mismatch">
  Item inputs are not shown: {{ model.goldenset.reason }}
</div>
{% endif %}

{% set printed = model | printed_chars %}
{#
  The budget sentence counts what the run *produced*. Identical draws are printed
  once below, so what is on the page is smaller, and both numbers are given
  rather than letting one quietly replace the other: the sentence is a
  completeness claim, and a completeness claim that starts counting what survived
  the presentation layer certifies a smaller thing in the same words.
#}
{% if model.detail.capped %}
<div class="note" id="detail-budget">
  <strong>The quoted model text in this report is bounded.</strong>
  {{ model.detail.sentence }}
  {% if printed != model.detail.embedded %}
  The rows that do carry their outputs embedded
  {{ '{:,}'.format(model.detail.embedded) }} characters of model text, of which
  {{ '{:,}'.format(printed) }} are printed below: where every draw of a side came
  back byte-identical it is shown once and counted, rather than repeated. The
  first figure counts what the models produced, which is what completeness is
  about; the second counts what this page spends on it.
  {% endif %}
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
<p class="secondary" id="detail-budget">{{ model.detail.sentence }}
{% if printed != model.detail.embedded %}
  Of those, {{ '{:,}'.format(printed) }} characters are printed below: where every
  draw of a side came back byte-identical it is shown once and counted, rather
  than repeated. The figure above counts what the models produced, which is what
  completeness is about.
{% endif %}
</p>
{% endif %}

<h2 id="flips">Flips {{ dash }} items that stopped working ({{ model.flips | length }})</h2>
<p class="secondary">
  <strong>Open by default</strong>, because these are the finding. Everything
  else in this document is context for them.
</p>
{{ changes(model.flips, True) }}

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
{#
  ``opened`` is passed true only for flips. Gains stay closed on purpose: they
  are context, not the finding, and this document already argues that netting the
  two lists is how a bad migration ships. Opening them by default would give a
  reader's eye the same weight for both.
#}
{% macro changes(rows, opened=False) %}
{% if not rows %}
<p class="secondary">None.</p>
{% else %}
{% for row in rows %}
<details{% if opened %} open{% endif %}>
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
    {% set baseline_draws = row.baseline_outputs | draws %}
    <h4>Baseline outputs ({{ baseline_draws.total }})</h4>
    {% for text in baseline_draws.texts %}
    <pre class="output">{{ text }}</pre>
    {% endfor %}
    {% if baseline_draws.sentence %}
    <p class="draws">{{ baseline_draws.sentence }}</p>
    {% endif %}
    {% if not row.baseline_outputs %}
    <p class="secondary">No baseline outputs available.</p>
    {% endif %}
    {% set candidate_draws = row.candidate_outputs | draws %}
    <h4>Candidate outputs ({{ candidate_draws.total }})</h4>
    {% for text in candidate_draws.texts %}
    <pre class="output">{{ text }}</pre>
    {% endfor %}
    {% if candidate_draws.sentence %}
    <p class="draws">{{ candidate_draws.sentence }}</p>
    {% endif %}
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


class _Draws(NamedTuple):
    """One side's draws, grouped for printing rather than for counting.

    Presentation only. Nothing here reaches a statistic: the rate, the interval
    and every count in the document are read from the evidence payload, and this
    decides how many ``<pre>`` blocks a row spends on text the models produced.

    The distinction the type exists to make is *uniformity*. Five byte-identical
    draws and five different ones are the same five blocks today, so a reader
    cannot see whether the draws agreed -- and whether they agreed is the fact
    that says how much weight one draw carries. Repetition is not evidence.
    """

    #: What to print. One entry when every draw was byte-identical; otherwise
    #: every draw, in the order they were recorded.
    texts: tuple[str, ...]
    #: Draws the run produced. Never derived from ``texts``, which is the whole
    #: point: after a collapse the two differ, and the sentence below needs both.
    total: int
    #: Distinct texts among them.
    distinct: int

    @property
    def collapsed(self) -> bool:
        """True when this is printing fewer blocks than the run produced draws."""
        return len(self.texts) < self.total

    @property
    def sentence(self) -> str:
        """What the reader is owed about the draws, or ``""`` when nothing is.

        Three cases and three different facts. Every draw agreeing is stated
        *because* only one block is shown -- the count would otherwise be missing
        from the page entirely. Draws differing is stated because the number that
        differed is the finding, and today it is invisible: uniformity and
        variation render identically. A single draw gets no sentence, because
        "1 draw, 1 distinct" tells a reader nothing they cannot see.
        """
        if self.total < 2:
            return ""
        if self.distinct == 1:
            return f"all {self.total} draws identical"
        return f"{self.total} draws, {self.distinct} distinct"


def _draws(outputs: Sequence[str]) -> _Draws:
    """Group one side's outputs, collapsing only total agreement.

    Only the all-identical case collapses. Partial grouping -- three of one text
    and two of another shown as two blocks with counts -- was considered and
    rejected: it silently reorders the draws, and the order draws were recorded
    in is the only evidence a reader has about *when* a model changed its answer
    within a run. Total agreement has no order to lose.

    First-appearance order, not sorted, for the same reason.
    """
    seen: list[str] = []
    for text in outputs:
        if text not in seen:
            seen.append(text)
    if len(seen) == 1 and len(outputs) > 1:
        return _Draws((seen[0],), len(outputs), 1)
    return _Draws(tuple(outputs), len(outputs), len(seen))


def _banner_bar(model: ReportModel) -> str:
    """The headline run's pass rate, interval and floor, as one inline SVG.

    Read from ``series[-1]`` rather than from a judge row, and that is the point
    of the field rather than a shortcut. :class:`~model_migration_kit.series.RunPoint`
    carries ``floor`` together with ``floor_source``, so the bar draws *the number
    the run was held to* and can tell that apart from the number that was merely
    configured. A judge row carries neither, and a bar drawn from
    ``model.thresholds`` would show a rule the gate may not have applied.

    ``[-1]`` and not "the newest by clock", which is the same run on every log
    this pipeline writes and not on every log that exists.
    :func:`~model_migration_kit.series.read_series` sorts nothing -- file order is
    the series order, so that a log whose clock stepped back over a daylight-saving
    boundary is not silently reordered -- while :func:`timeline_svg` sorts by
    parsed ``created``, because its axis is time. So ``series[-1]`` is exactly the
    comparison ``from_evidence``'s own last-wins reduction kept for the banner,
    which is what makes the bar and the banner one reading; it is *not* guaranteed
    to be the rightmost marker on the chart. The prose beneath the chart says
    which of the two it is, because a document that lets a reader assume they are
    the same is a document that can show a GO beside a chart ending in red.

    An empty series is not an error and not a zero. Every model built by some
    route other than :meth:`ReportModel.from_evidence` has one, and
    :func:`interval_bar_svg` already draws each missing value as its own named
    picture -- a floor of ``None`` draws no line at all rather than a line at 0.0,
    because a rule that was never set, rendered as a floor of zero, makes the
    document claim a bar cleared a bar that does not exist.
    """
    point = model.series[-1] if model.series else None
    if point is None:
        return interval_bar_svg(rate=None, interval=None, floor=None, label="candidate")
    label = f"candidate {point.judge_name}".strip()
    return interval_bar_svg(
        rate=point.pass_rate,
        interval=point.interval,
        floor=point.floor,
        label=label,
    )


def _printed_chars(model: ReportModel) -> int:
    """Characters of quoted model text the document actually prints.

    Not what it embedded -- :attr:`DetailBudget.embedded` counts that, and the two
    differ by exactly the draws this document collapsed. Both numbers are printed,
    beside each other, because the budget sentence is a *completeness* claim and
    completeness is about what the run produced. Letting that sentence quietly
    start counting what was printed would shrink the thing it certifies while
    leaving the wording intact, which is the same failure as stating missing data
    as zero, in a new coat.
    """
    total = 0
    for section in (model.flips, model.gains, model.unstable):
        for row in section:
            if not row.detail_embedded:
                continue
            total += len(row.input or "")
            total += sum(len(text) for text in _draws(row.baseline_outputs).texts)
            total += sum(len(text) for text in _draws(row.candidate_outputs).texts)
            total += sum(len(reason) for reason in row.reasons.values())
    return total


def _source_label(value: object) -> str:
    """A threshold's source, shortened to a basename only when it is a full path.

    The thresholds table prints its source once per row, and on the demo run that
    is the same absolute path six times, each about 130 characters, in a document
    whose readable width is 60rem. The full path is already shown once, whole, and
    where it can be checked, under ``config`` in "What was compared" -- and there
    only. The provenance block carries the *evidence log's* path and the config
    *hash*, not the config path, so the prose beneath the table names the one
    place the whole path is: a shortening that sends a reviewer to a section the
    path is not in is a hiding.

    Shortened only when :func:`_is_absolute` says so, so that
    ``THRESHOLD_SOURCE_UNRECORDED`` and any other prose passes through untouched:
    a sentence containing a slash is not a path, and :func:`_basename` would cut
    it at the slash.

    **Not put in a ``title=``**, which is what C14a's contract asked for. A
    Windows path is a scheme by ``_SCHEME_RE``'s reading -- ``C:`` matches
    ``[a-zA-Z][a-zA-Z0-9+.-]*:`` -- and ``title`` is not exempt under
    ``_NEVER_DEREFERENCED_RE``, so the tooltip would make
    :func:`assert_self_contained` refuse the whole document, on Windows only.
    Measured, not reasoned: see
    ``test_a_windows_path_in_a_title_attribute_would_fail_the_self_containment_gate``.
    """
    text = "" if value is None else str(value)
    return _basename(text) if _is_absolute(text) else text


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
    # A delta and a span, formatted at the edge rather than on the model, and each
    # rendering its ``None`` as the dash rather than as a zero -- see _pp and _days.
    env.filters["pp"] = _pp
    env.filters["days"] = _days
    env.filters["flag"] = _flag
    env.filters["counts"] = _item_counts
    # The two hand-rolled SVGs, reached as filters so that each one's markup
    # enters the document at exactly one point that the template marks ``| safe``.
    # A helper called anywhere else, or a ``| safe`` on anything else, is caught by
    # test_the_document_marks_exactly_one_expression_safe_per_hand_rolled_svg_and_no_others,
    # which asserts the *set* of safe expressions by name: two safes in the wrong
    # places is the mutation a count would pass.
    env.filters["interval_bar"] = _banner_bar
    env.filters["timeline"] = lambda points: timeline_svg(tuple(points))
    env.filters["draws"] = _draws
    env.filters["printed_chars"] = _printed_chars
    env.filters["source_label"] = _source_label
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
    heading = _warned_title(model, title) if title else _default_title(model)
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


#: The ``<title>``'s half of the scripted-models warning. Session 3 §5.3 names
#: five places that say it and calls none of them a footnote; this is the first
#: of the five, and the only one that survives being pasted into a chat window as
#: a link preview or read out by a screen reader announcing the tab.
_FAKE_TITLE_PREFIX = "FAKE MODELS"


def _warned_title(model: ReportModel, head: str) -> str:
    """``head``, carrying the scripted-models warning whenever the report is one.

    Applied to a caller's ``title=`` and not only to :func:`_default_title`,
    because the two are the same surface and only one of them was covered. §5.3
    lists the ``<title>`` among the five places that say the models are scripted;
    ``render_html_string(model, title="Nightly quality report")`` removed it, and
    the removal is invisible in the body, which still bands. That is the exact
    failure the rule is written against -- the warning going missing from the
    thing someone pastes into a deck -- reached through an argument rather than
    through a flag, which is why the "no ``demo=``/``fake=`` keyword" test never
    saw it.

    The guard is a *prefix* check and deliberately not a substring one. A title
    that merely contains the words -- "what our FAKE MODELS review found" -- must
    still be prefixed, or the suppression vector is spelled out in the docstring
    of the function that closes it.
    """
    if not model.is_demo or head.upper().startswith(_FAKE_TITLE_PREFIX):
        return head
    return f"{_FAKE_TITLE_PREFIX} {EM_DASH} {head}"


def _default_title(model: ReportModel) -> str:
    base = model.baseline.model_id or "baseline"
    cand = model.candidate.model_id or "candidate"
    head = f"{model.verdict_word} {EM_DASH} {base} to {cand} {EM_DASH} model-migration-kit"
    return _warned_title(model, head)


# --------------------------------------------------------------------------- #
# the timeline -- one comparison per marker, on an axis that is time
# --------------------------------------------------------------------------- #

#: Blank margin, in user units, between the plotting area and every edge of the
#: viewBox. One number for all four sides, which is what makes the centre of the
#: plotting area the centre of the picture: the single-run case has to draw its one
#: marker at the horizontal centre, and under asymmetric padding "the centre" would
#: be two different numbers depending on which one you meant. Exported because the
#: alternative is a test that hard-codes 24.0 and fails the day the layout is
#: re-tuned, for no defect.
TIMELINE_PAD = 24.0

#: Side of the square one run is drawn as. A square rather than a circle so the
#: marker carries an attribute literally named ``x``: a ``<circle>`` has ``cx``,
#: and horizontal position is the one number this chart exists to be read for. It
#: is placed by its corner -- ``x`` is the mapped coordinate minus half a side --
#: so the run's position is ``x + width/2``, which is what every reader of a
#: ``<rect>`` already assumes. See :func:`_svg_marker` for why not a ``transform``.
_MARKER_SIDE = 7.0

#: Width given to a floor rule that would otherwise be zero-wide -- the one-run
#: series, whose single group begins and ends at the same marker. Drawing nothing
#: there would be indistinguishable from "no floor was recorded", which is a
#: different fact about the run and is counted separately.
_LONE_RULE_WIDTH = 16.0

#: The chart's styling, inlined into the chart. The report's stylesheet cannot be
#: relied on for this: an SVG ``<line>`` with no ``stroke`` is invisible rather
#: than black, so a timeline that inherited its colours from the page would render
#: as an empty rectangle everywhere the page is not -- including the one place this
#: project promises the document still works, a reviewer's offline machine. Every
#: rule is prefixed with the chart's own class because ``<style>`` inside inline
#: SVG is not scoped: it applies to the whole document that embeds it.
_TIMELINE_CSS = (
    ".migkit-timeline .floor{stroke:#6b6b6b;stroke-width:1.5;stroke-dasharray:4 3;fill:none}"
    ".migkit-timeline .whisker{stroke:#9aa0a6;stroke-width:1.5}"
    ".migkit-timeline rect{stroke:#ffffff;stroke-width:1}"
    ".migkit-timeline .go{fill:#1a7f37}"
    ".migkit-timeline .nogo{fill:#b3261e}"
    ".migkit-timeline .review{fill:#a76b00}"
    ".migkit-timeline .none{fill:#6b6b6b}"
    ".migkit-timeline text{font-size:11px;fill:#3c4043}"
)


class Timeline(NamedTuple):
    """The chart, and the two counts the sentence beneath it has to print.

    A tuple rather than a bare ``str`` because two of this chart's rules are about
    what it does *not* draw: a run whose floor was never recorded leaves a gap in
    the rule, and a run with no pass rate gets no marker at all. Both of those are
    invisible by construction, and an absence nobody counts is an absence nobody
    notices -- which is the failure this whole document is built against. Handing
    the caller the counts is what lets the page say "2 of 14 runs recorded no
    floor" beneath the picture instead of leaving a reader to wonder whether the
    rule is broken for a reason.

    Still a tuple, so ``svg, *_ = timeline_svg(points)`` reads the way the
    contract's original ``-> str`` signature implied it would.
    """

    #: The chart, as one ``<svg>`` element. Never ``""``: an empty series renders
    #: as a chart that says there is nothing to plot, because a blank space in a
    #: compliance document is read as a rendering bug rather than as a fact.
    svg: str
    #: Points whose ``floor`` is ``None``. Counted over every point handed in and
    #: not only the drawn ones: a run with no usable date and no floor is still a
    #: run whose floor is unknown.
    runs_without_floor: int
    #: Points whose ``pass_rate`` is ``None`` -- a run that produced no measurable
    #: rate, which a truncated run reaches routinely.
    runs_without_rate: int


def timeline_svg(
    points: Sequence[RunPoint],
    *,
    width: int = 900,
    height: int = 260,
) -> Timeline:
    """Draw a series of comparisons as one SVG, with time on the horizontal axis.

    **The axis is time, not run number.** Nightly runs are not evenly spaced: a
    series recorded under CI has weekends in it, and the three weeks between the
    run that was green and the run that was not is the most informative thing on
    the chart. Evenly spaced dots hide exactly that, and they hide it while looking
    correct, which is why spacing is computed from each point's parsed ``created``
    and never from its index.

    **Nothing is interpolated.** A marker is drawn where a run happened and nowhere
    else: no line joins the markers, because a line between two runs asserts a pass
    rate on every date in between, and on those dates nothing ran. The floor is
    drawn as a step -- horizontal while it held, vertical where it changed -- for
    the same reason and a sharper one: a ``<polyline>`` through the floor values
    draws a diagonal ramp between two different floors, and a floor that ramps is
    a floor no run was ever held to.

    Nothing undrawable is dropped in silence. A run with no pass rate gets no
    marker and is counted; a run with no recorded floor leaves a gap in the rule
    and is counted; a run whose ``created`` will not parse cannot be placed on a
    time axis at all, so it is left off and the picture says how many were.

    Args:
        points: The series, in any order -- they are sorted here by parsed
            timestamp, because a chart whose x-axis is time may not take its
            ordering from the order records happened to be appended in. The sort
            is stable, so runs recorded at one identical instant keep the order
            the log appended them in: when the clock cannot separate two runs the
            only ordering evidence left is the order they were written down.
        width: viewBox width in user units. The element is drawn to scale.
        height: viewBox height in user units.

    Returns:
        A :class:`Timeline`: the ``<svg>``, the number of points with no recorded
        floor, and the number with no pass rate.
    """
    runs_without_floor = sum(1 for point in points if point.floor is None)
    runs_without_rate = sum(1 for point in points if point.pass_rate is None)
    left, right = TIMELINE_PAD, float(width) - TIMELINE_PAD
    top, bottom = TIMELINE_PAD, float(height) - TIMELINE_PAD

    dated = [(parse_created(point.created), point) for point in points]
    placed = [(moment, point) for moment, point in dated if moment is not None]
    placed.sort(key=lambda pair: pair[0])
    undated = len(points) - len(placed)

    if not placed:
        # Also the all-undated case, which says the same thing and then says how
        # many. "Nothing was plotted" and "four runs carried a date this package
        # cannot read" are different facts, and the second one is a defect in the
        # log rather than an empty series; the docstring above promises the
        # picture says how many, and this is the branch that has to keep it.
        said = "No dated runs to plot"
        if undated:
            said += f": {undated} run(s) with no usable date"
        empty = _svg_text(float(width) / 2, float(height) / 2, said, "middle")
        return Timeline(
            _svg_frame(width, height, [_svg_name(said), empty]),
            runs_without_floor,
            runs_without_rate,
        )

    span = (placed[-1][0] - placed[0][0]).total_seconds()
    xs = _timeline_x(placed, left, right)
    body = [_svg_title(len(placed), span), f"<style>{_TIMELINE_CSS}</style>"]
    body.extend(_floor_marks([point for _, point in placed], xs, top, bottom))

    for index, (_, point) in enumerate(placed):
        if point.pass_rate is None:
            continue
        x = xs[index]
        if point.interval is not None:
            low, high = point.interval
            body.append(
                f'<line class="whisker" x1="{_svg_number(x)}" x2="{_svg_number(x)}"'
                f' y1="{_svg_number(_timeline_y(low, top, bottom))}"'
                f' y2="{_svg_number(_timeline_y(high, top, bottom))}"/>'
            )
        body.append(_svg_marker(x, _timeline_y(point.pass_rate, top, bottom), point))

    if undated:
        body.append(
            _svg_text(
                left,
                float(height) - 6.0,
                f"{undated} run(s) with no usable date, not plotted",
                "start",
            )
        )
    return Timeline(_svg_frame(width, height, body), runs_without_floor, runs_without_rate)


def _timeline_x(
    placed: Sequence[tuple[datetime, RunPoint]],
    left: float,
    right: float,
) -> list[float]:
    """One x per point, mapped linearly from the earliest instant to the latest.

    Two inputs are not a mapping at all and are handled first, because both divide
    by a zero span. A single run has no span to map and is drawn at the horizontal
    centre. Every run sharing one timestamp is not a pathological input either: a
    generator that pins ``utc_now`` to a constant -- which is how the showcase
    series is built -- produces a series identical to the microsecond, and so does
    any log where two comparisons landed inside the same one. Those are spread
    evenly across the axis the mapping would have used, and the chart's ``<title>``
    says why, so that even spacing is never read as a claim about elapsed time.
    """
    if len(placed) == 1:
        return [(left + right) / 2]
    span = (placed[-1][0] - placed[0][0]).total_seconds()
    if span <= 0:
        step = (right - left) / (len(placed) - 1)
        return [left + step * index for index in range(len(placed))]
    first = placed[0][0]
    return [
        left + (right - left) * ((moment - first).total_seconds() / span) for moment, _ in placed
    ]


def _timeline_y(rate: float, top: float, bottom: float) -> float:
    """A rate on a fixed 0-to-1 axis, never on one scaled to the data.

    An axis that rescaled itself to the observed range would draw a series moving
    from 0.94 to 0.95 as a cliff, and the reader of a change-control document is
    entitled to read height as rate without first reading the axis.
    """
    return bottom - min(max(rate, 0.0), 1.0) * (bottom - top)


def _floor_groups(points: Sequence[RunPoint]) -> list[tuple[int, int, float]]:
    """``(first index, last index, floor)`` per maximal run of one recorded floor.

    A point with no recorded floor ends the group it follows rather than joining
    it, so two 0.9 runs either side of an unrecorded one are two groups and not
    one -- the rule breaks over the gap instead of being drawn straight through the
    run nobody can say what it was held to.
    """
    groups: list[tuple[int, int, float]] = []
    for index, point in enumerate(points):
        floor = point.floor
        if floor is None:
            continue
        if groups and groups[-1][1] == index - 1 and groups[-1][2] == floor:
            groups[-1] = (groups[-1][0], index, floor)
        else:
            groups.append((index, index, floor))
    return groups


def _floor_marks(
    points: Sequence[RunPoint],
    xs: Sequence[float],
    top: float,
    bottom: float,
) -> list[str]:
    """The floor as horizontal rules and vertical steps, one ``<line>`` each.

    A group's rule reaches half-way to the neighbouring run on each side rather
    than stopping at its own outermost marker, and the vertical step is drawn at
    that same midpoint. The midpoint is the honest position: the evidence says the
    floor was one number on the day of one run and another on the day of the next,
    and says nothing whatever about the days in between. Reaching half-way is also
    what makes a group of one visible at all.

    Each horizontal rule carries ``data-rule``: the floor as recorded, never the
    ``y`` it was mapped to. A reader who wants the number back should not have to
    invert the projection to get it, and a pixel written into a ``data-`` value is
    a pixel a later chunk reads as a rate.
    """
    marks: list[str] = []
    groups = _floor_groups(points)
    last = len(xs) - 1
    for start, end, floor in groups:
        x1 = xs[start] if start == 0 else (xs[start - 1] + xs[start]) / 2
        x2 = xs[end] if end == last else (xs[end] + xs[end + 1]) / 2
        if x2 <= x1:
            centre = (x1 + x2) / 2
            x1, x2 = centre - _LONE_RULE_WIDTH / 2, centre + _LONE_RULE_WIDTH / 2
        y = _timeline_y(floor, top, bottom)
        marks.append(
            f'<line class="floor" x1="{_svg_number(x1)}" x2="{_svg_number(x2)}"'
            f' y1="{_svg_number(y)}" y2="{_svg_number(y)}" data-rule="{_svg_number(floor, 6)}"/>'
        )
    for position, before in enumerate(groups[:-1]):
        after = groups[position + 1]
        if before[1] + 1 != after[0]:
            continue  # a run with no recorded floor sits between them: no step.
        x = (xs[before[1]] + xs[after[0]]) / 2
        marks.append(
            f'<line class="floor" x1="{_svg_number(x)}" x2="{_svg_number(x)}"'
            f' y1="{_svg_number(_timeline_y(before[2], top, bottom))}"'
            f' y2="{_svg_number(_timeline_y(after[2], top, bottom))}"/>'
        )
    return marks


def _svg_marker(x: float, y: float, point: RunPoint) -> str:
    """One run, as a square carrying the four values the contract names.

    Neither ``class`` nor ``data-verdict`` is ever a word lifted straight out of
    the log. A class name built from recorded text is a class name an evidence log
    gets to choose, and this module treats everything that arrived from disk as
    attacker-influenced -- but the sharper reason is the one
    :func:`assert_self_contained` supplies: it judges *every* attribute value by
    :data:`_SCHEME_RE`, so a recorded verdict reading ``review: n was too small``
    matches ``scheme:`` and makes ``render_html`` refuse the whole document.
    Recorded text that can stop a report existing is recorded text controlling
    markup. So a verdict this report recognises travels verbatim and one it does
    not is rendered as no verdict rather than as itself -- the recorded word is
    printed in full, as escaped text, in the row this marker belongs to.

    ``data-rate`` and ``data-floor`` are empty exactly when the number is missing.
    An absent floor is the reason the rule beneath this run has a gap in it, and
    writing ``0`` there instead is the one thing this document may never do.

    **The square is placed by its corner, not centred by a ``transform``.** Both
    draw the same picture; they differ in what a reader of the markup has to do to
    recover the run's position. With the corner form it is ``x + width/2``, which
    is what every SVG reader already assumes of a ``<rect>``. With a translate it
    is ``x`` composed with a transform, and anything that reads ``x`` alone --
    a test, a later chunk, a person -- is wrong by half a side and has no way to
    see that it is. The contract asks for the position to be assertable from
    ``data-created`` and ``x``; the convention that needs no transform is the one
    that keeps that true.
    """
    half = _MARKER_SIDE / 2
    rate = "" if point.pass_rate is None else _svg_number(point.pass_rate, 6)
    floor = "" if point.floor is None else _svg_number(point.floor, 6)
    verdict = point.verdict if point.verdict in _VERDICT_CLASS else ""
    return (
        f'<rect class="{_VERDICT_CLASS.get(point.verdict, "none")}"'
        f' x="{_svg_number(x - half)}" y="{_svg_number(y - half)}"'
        f' width="{_svg_number(_MARKER_SIDE)}" height="{_svg_number(_MARKER_SIDE)}"'
        f' data-created="{_svg_attr(point.created)}" data-rate="{rate}"'
        f' data-verdict="{_svg_attr(verdict)}" data-floor="{floor}"/>'
    )


def _svg_name(text: str) -> str:
    """A ``<title>``, which is the accessible name of the element that holds it.

    Every branch of this chart emits one, the empty one included. An
    ``<svg role="img">`` carrying no title is an image with no name: it is
    announced as "image" and nothing else, so the reader who cannot see the
    picture is told strictly less than the picture says. In a document whose
    subject is what the evidence does and does not contain, that is the same
    defect as an uncounted absence.
    """
    return f"<title>{_svg_attr(text)}</title>"


def _svg_title(runs: int, span: float) -> str:
    """The chart's accessible name, and the disclosure the zero-span case owes."""
    text = f"Candidate pass rate over {runs} run(s); the horizontal axis is time."
    if runs > 1 and span <= 0:
        text += (
            f" All {runs} runs share a timestamp, so the markers are spaced evenly"
            " rather than by elapsed time."
        )
    return _svg_name(text)


def _svg_text(x: float, y: float, text: str, anchor: str) -> str:
    return (
        f'<text x="{_svg_number(x)}" y="{_svg_number(y)}" text-anchor="{anchor}">'
        f"{_svg_attr(text)}</text>"
    )


def _svg_frame(width: int, height: int, body: Sequence[str]) -> str:
    """The ``<svg>`` element itself, carrying no ``xmlns`` -- which is deliberate.

    A namespace declaration is the one attribute value in this chart that looks
    like a URL, and :func:`assert_self_contained` judges attribute values by
    :data:`_SCHEME_RE` rather than by attribute name. So
    ``xmlns="http://www.w3.org/2000/svg"`` is reported as a position that would
    fetch, and ``render_html`` -- which runs that assertion over its own output
    before writing anything -- refuses to render any report containing this chart.
    Verified rather than surmised: it is what the first render of a timeline did.

    Losing the declaration costs nothing where the chart is used. Inline SVG in an
    HTML document is put into the SVG namespace by the HTML parser itself, and the
    declaration matters only to somebody who saves the chart alone as a ``.svg``,
    which is not how this document is read. Teaching the detector about namespace
    URIs is the other fix, and it is not this chunk's to make: that detector is a
    security control, and widening it so that a chart renders is the shape of
    change that quietly widens it for something else.
    """
    joined = "\n".join(body)
    return (
        f'<svg class="migkit-timeline" role="img"'
        f' viewBox="0 0 {_svg_number(width)} {_svg_number(height)}"'
        f' width="{_svg_number(width)}" height="{_svg_number(height)}">{joined}</svg>'
    )


def _svg_number(value: float, digits: int = 3) -> str:
    """A coordinate, rounded and trimmed, so that 450.0 is written ``450``.

    Trimmed rather than left as ``450.000`` because these values are read by people
    as often as by parsers, and rounded rather than repr'd because a coordinate
    carrying seventeen significant figures of binary float is noise in a document
    somebody has to diff.
    """
    return f"{value:.{digits}f}".rstrip("0").rstrip(".") or "0"


def _svg_attr(value: object) -> str:
    """Attribute values and text content, escaped.

    Every string that reaches here came off disk -- ``created`` and ``verdict`` are
    recorded text, and this module's whole posture is that recorded text is
    attacker-influenced. An unescaped ``"`` in a ``data-`` value closes the
    attribute, and the rest of the record becomes markup.
    """
    return html.escape(str(value), quote=True)
