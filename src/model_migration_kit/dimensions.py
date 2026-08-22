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

from dataclasses import dataclass

from opik_rigor import wilson_interval
from opik_rigor.distribution import DEFAULT_CONFIDENCE

# --- C9: the cell, the refusal, and the two floors ---------------------------
#
# ``DEFAULT_CONFIDENCE`` is imported from ``opik_rigor.distribution`` rather than
# from the package root because the root does not re-export it, and the root is
# where ``wilson_interval`` comes from two lines up. It is in
# ``distribution.__all__``, so it is rigor's public surface and invariant 1 holds
# -- but it is the first submodule import anywhere in ``src/``, and the cheaper
# fix lives upstream: rigor re-exporting the constant beside the function that
# defaults to it.
#
# Two floors, and a cell must clear both.
#
# ``MIN_N_FOR_A_VERDICT`` counts completions and is the older of the two. It does
# not do the job on its own. At ``n_per_item=5`` a tag carrying four items
# produces exactly twenty completions, ``20 < 20`` is ``False``, and the cell
# renders a verdict -- so the effective floor was four items. Four is the number
# in the spec's own refusal sentence, the showpiece example of a cell that must
# decline, which means the completions floor as written passed the one case it
# exists to fail.
#
# ``MIN_ITEMS_FOR_A_VERDICT`` counts distinct items and is the fix. Twenty
# completions drawn from four items are not twenty observations: they are four
# questions asked five times each, correlated by construction because every draw
# within an item shares a prompt, a reference and a rubric clause. A dimension
# verdict generalises over *questions*, so the sample size that matters here is
# nearer four than twenty. That is also why a larger completions floor is not the
# fix: at ``n_per_item=10`` the same four questions would clear a floor of forty
# just as easily.
#
# Ten items rather than some other number, and the reason is statable rather than
# aesthetic: below ten, a single item is worth more than a tenth of the
# dimension's verdict, and one badly written golden-set item should not be able to
# move a published claim by that much. It refuses the spec's four-item example and
# the showcase tag clears it at sixteen items and eighty completions.
#
# The floors are independent on purpose. Neither subsumes the other -- twelve
# items at one draw each clears the item floor and fails the completions floor --
# and collapsing them into one number loses whichever case the survivor cannot
# see.

MIN_N_FOR_A_VERDICT: int = 20
MIN_ITEMS_FOR_A_VERDICT: int = 10


@dataclass(frozen=True)
class DimensionCell:
    """One tag's row: what was measured, and whether it may be read as a verdict.

    ``rate``, ``interval`` and ``floor`` are the numbers a renderer draws.
    ``verdict_refused`` is the only field that decides whether it may draw them as
    a judgement, and it is settled by sample size alone -- never by how the
    interval happens to sit against ``floor``. A refused cell still shows its
    interval; what it does not do is colour it.

    ``needed`` and ``needed_unit`` travel as a pair and answer "what would make
    this cell answerable", in the unit that actually binds. ``needed_unit`` is
    ``""`` exactly when ``needed`` is ``None``, which is exactly when the cell is
    not refused.
    """

    tag: str
    passes: int
    n: int
    items: int
    rate: float | None
    interval: tuple[float, float] | None
    floor: float | None
    verdict_refused: bool
    needed: int | None
    needed_unit: str
    note: str


def dimension_cell(
    tag: str,
    passes: int,
    n: int,
    items: int,
    *,
    confidence: float | None,
    floor: float | None,
    min_n: int = MIN_N_FOR_A_VERDICT,
    min_items: int = MIN_ITEMS_FOR_A_VERDICT,
) -> DimensionCell:
    """One :class:`DimensionCell`, refusing the verdict when the sample cannot carry it.

    **Takes four plain integers rather than a counts object.** The counting and
    the cell are written against each other's contract and not against each
    other's types, so nothing here imports what the counter defines.

    **The refusal rule is sample size, and only sample size.** ``verdict_refused``
    is ``True`` when ``n < min_n`` or ``items < min_items``, however the interval
    sits against ``floor``. The tempting alternative -- "refuse when the interval
    is too wide to decide" -- is a different and worse rule, because a narrow
    interval can be produced by a tiny sample that happens to be unanimous: four
    passes out of four would answer, on four draws of one question. Declining is
    the differentiator this whole document is built on, and a rule a narrow
    interval can talk out of refusing does not decline.

    **When both floors bind the note names items**, which is not a style
    preference. It is the only one of the two a reader can act on. "You need more
    completions" sends someone to raise ``n_per_item``, and raising ``n_per_item``
    cannot fix an item shortfall -- it multiplies the same few questions. Advice
    that does not work is worse than no advice, so the item floor is named
    whenever it binds and the completions floor is named only when it binds alone.

    **``n == 0`` is a rendering state, not a computation.** ``wilson_interval(0,
    0)`` raises ``ValueError("a rate over zero runs is not a rate")``, which is
    correct of it and useless here: a tag that was in the golden set and produced
    nothing judged is a finding to display, not an exception to propagate. So the
    zero case calls nothing and every derived field is ``None``.

    **``floor`` is carried, not consulted.** It arrives from the run's gate for
    the renderer's benefit; nothing in this function compares an interval to it,
    which is the cheapest way to guarantee that no refused cell is quietly judged
    against it.

    ``note`` is documented as the refusal sentence and ``""`` otherwise, but a
    defaulted confidence has to be stated somewhere and this is the only field
    that can state it. So an unrefused cell whose confidence was defaulted does
    carry a note. Given the choice between an empty string and a printed interval
    whose confidence level the reader cannot know, the interval is the one that
    misleads.

    Args:
        confidence: ``None`` falls back to rigor's ``DEFAULT_CONFIDENCE``, and the
            fallback is recorded in ``note``. It is never silent.
        min_n: Completions floor. Overridable so a caller -- or a mutation test --
            can move it independently of ``min_items``.
        min_items: Distinct-items floor. Independent of ``min_n`` in both
            directions; moving one must not change what the other refuses.

    Raises:
        ValueError: ``passes > n``, a corrupt count that must not render.
            ``items > n``, which is impossible and means the caller mispaired two
            numbers. Any negative count, which nothing downstream would catch --
            ``items`` in particular is validated nowhere else.
    """
    if passes < 0 or n < 0 or items < 0:
        raise ValueError(
            f"counts for {tag!r} cannot be negative: passes={passes}, n={n}, items={items}"
        )
    if passes > n:
        raise ValueError(f"more passes than completions for {tag!r}: {passes} > {n}")
    if items > n:
        raise ValueError(f"more items than completions for {tag!r}: {items} > {n}")

    level = DEFAULT_CONFIDENCE if confidence is None else confidence

    rate: float | None = None
    interval: tuple[float, float] | None = None
    if n > 0:
        rate = passes / n
        interval = wilson_interval(passes, n, level)

    short_items = items < min_items
    short_n = n < min_n

    needed: int | None
    if short_items:
        needed, needed_unit = min_items - items, "items"
        refusal = f"{min_items} items needed for a verdict here; you have {items}."
    elif short_n:
        needed, needed_unit = min_n - n, "completions"
        refusal = f"{min_n} completions needed for a verdict here; you have {n}."
    else:
        needed, needed_unit, refusal = None, "", ""

    sentences: list[str] = []
    if n == 0:
        sentences.append(f"Nothing was measured for {tag}.")
    if refusal:
        sentences.append(refusal)
    if confidence is None and interval is not None:
        sentences.append(
            f"No confidence level was given, so rigor's default of {level:.0%} was used."
        )

    return DimensionCell(
        tag=tag,
        passes=passes,
        n=n,
        items=items,
        rate=rate,
        interval=interval,
        floor=floor,
        verdict_refused=short_items or short_n,
        needed=needed,
        needed_unit=needed_unit,
        note=" ".join(sentences),
    )
