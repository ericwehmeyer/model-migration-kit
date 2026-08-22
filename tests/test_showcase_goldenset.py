"""Acceptance tests for the showcase golden set (plan chunk C15).

The showcase set is the data behind the report a stranger reads first. Nothing
downstream defends it: ``GoldenSet.load`` checks the *format*, C8 counts whatever
tags it is handed, and C9 renders whatever counts it is given. A set with a
duplicated input, a tag column of four, or an id that reads like a real customer
question is a defect that only shows up in a published document. So the checks
live here, against the file itself.

**Where the file lives is deliberately not assumed.** C15 leaves the choice
between shipping it in the wheel (``src/model_migration_kit/data/``) and keeping
it out (``docs/``) to the implementer, on the basis of the packaging rules. These
tests search both and name both when neither holds it. A hard-coded path would
fail for the wrong reason and teach us nothing.

**Located by path, not by ``importlib.resources``.** ``tests/test_cli.py``
reaches the demo data through ``importlib.resources``, which is right for it:
that test asks whether a *wheel install* can find the file. This module asks
whether the *checkout* contains it, and the two answers differ whenever the
installed package is an editable pointing at a different tree. Resolving from
``__file__`` asks the question this module means to ask.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

from model_migration_kit.demo import _refuse_duplicate_inputs
from model_migration_kit.goldenset import GoldenSet

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The two places C15 permits. Both are searched; both are named in the failure
#: when neither holds the file.
CANDIDATE_ROOTS = (
    REPO_ROOT / "src" / "model_migration_kit" / "data",
    REPO_ROOT / "docs",
)

#: The name C15 proposes. Matching is looser than this -- any ``.jsonl`` under a
#: candidate root whose name says "showcase" counts -- so that a rename does not
#: read as a missing file.
PREFERRED_NAME = "showcase_goldenset.jsonl"

#: C15's contract, restated as numbers so drift shows up as a diff here.
EXPECTED_ITEMS = 96
EXPECTED_TAGS = 6
MIN_ITEMS_PER_TAG = 16

#: The six dimensions, by name. Counting them is not enough: the report's matrix
#: labels its rows with these strings, every screenshot in the published document
#: shows them, and a rename -- ``summarisation`` to ``summarization``, say -- keeps
#: the count at six while silently changing what the showcase claims to measure.
EXPECTED_TAG_NAMES = frozenset(
    {
        "extraction",
        "classification",
        "summarisation",
        "instruction-following",
        "multi-step",
        "refusal",
    }
)

#: ``GoldenSet.hash`` over the committed file, pinned. The set is *seed data*: the
#: showcase's fourteen nightly runs are generated against it once and the artifacts
#: carry this hash, so an edit here does not fail loudly -- it produces a series
#: whose items no longer match the ones the report describes. Every other check in
#: this module tests a property, and a property survives ten mutations that change
#: the content. This line is the one assertion that does not.
#:
#: **Re-pin it deliberately, never to make the suite green.** Regenerate with
#: ``python scripts/make_showcase_goldenset.py``, which prints the new hash, and
#: change this literal in the same commit as the data -- then re-seed the showcase,
#: because after C17 the artifacts are what disagree with it.
EXPECTED_CONTENT_HASH = "596d5b614aa456a6e88444c8e245d1cd3d0750f176dcc8ef95ea0a7f067481dd"

#: Every id carries this, so nothing in the showcase can be read as a real
#: evaluation item. C15: "Every ``id`` prefixed ``synthetic-``."
ID_PREFIX = "synthetic-"

#: C9's ``MIN_N_FOR_A_VERDICT``, written out rather than imported: C9 may not
#: exist yet, and an import error here would hide every other failure in this
#: module behind a collection error. C9 owns the constant; if it moves, this line
#: is the reminder that C15's shape has to move with it.
MIN_N_FOR_A_VERDICT = 20

#: The showcase run draws this many completions per item. 16 items x 5 draws = 80
#: completions per tag, which is what has to clear the minimum above.
DRAWS_PER_ITEM = 5

#: Domains reserved by RFC 2606/6761 for documentation and testing. An address at
#: one of these cannot belong to anybody, so it is safe in a published example --
#: the demo set already uses ``example.com`` for exactly this. Anything else is a
#: domain somebody owns.
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_EMAIL_SUFFIXES = (".example", ".invalid", ".test", ".localhost")

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

#: Things shaped like a secret *value*, not like the word for one. The demo's
#: ``refuse-03`` asks "What is our customer database password?" and must stay
#: legal: it names a credential without carrying one. So the assignment pattern
#: requires a value after the separator, and the rest are vendor key shapes.
CREDENTIAL_PATTERNS = (
    ("an OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("an Anthropic-style key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "a secret assigned a value",
        re.compile(
            r"(?i)\b(?:password|passwd|api[ _-]?key|secret|access[ _-]?token|bearer)\b"
            r"\s*[:=]\s*\S{6,}"
        ),
    ),
)

#: Things shaped like an account somebody could be billed against. A twelve-digit
#: run is longer than any quantity, date or ticket number a question would carry;
#: the demo's ``INC-4471`` and ``98.10`` are well clear of it.
ACCOUNT_PATTERNS = (
    ("a long digit run", re.compile(r"\b\d{12,}\b")),
    ("a payment-card number", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b")),
    ("an IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
)


@lru_cache(maxsize=1)
def _locate() -> Path:
    """Find the showcase set in either place C15 permits, or fail naming both."""
    found: list[Path] = []
    for root in CANDIDATE_ROOTS:
        if not root.is_dir():
            continue
        found.extend(
            path for path in sorted(root.rglob("*.jsonl")) if "showcase" in path.name.lower()
        )
    if not found:
        wanted = " or ".join(str(root / PREFERRED_NAME) for root in CANDIDATE_ROOTS)
        pytest.fail(
            "no showcase golden set found. C15 permits either location; looked "
            f"recursively for a *.jsonl whose name says 'showcase' under "
            f"{CANDIDATE_ROOTS[0]} and under {CANDIDATE_ROOTS[1]}. Expected "
            f"something like {wanted}."
        )
    if len(found) > 1:
        pytest.fail(
            "more than one showcase golden set found, and two copies drift: "
            + ", ".join(str(path) for path in found)
        )
    return found[0]


@lru_cache(maxsize=1)
def _showcase() -> GoldenSet:
    """Parse the showcase set through the real loader, never through ``json``.

    ``GoldenSet.parse`` is what the showcase run will call, and it enforces
    ``ALLOWED_KEYS``, non-empty inputs, unique ids and de-duplicated tags. A file
    that happens to be valid JSONL but fails this is a file that breaks at
    showcase time rather than at test time -- a mistyped ``"tag"`` for ``"tags"``
    being the case that would otherwise pass every check written by hand.
    """
    path = _locate()
    return GoldenSet.parse(path.read_bytes(), source=str(path))


def _texts() -> list[tuple[str, str]]:
    """Every human-readable string in the set, paired with the id that carries it."""
    out: list[tuple[str, str]] = []
    for item in _showcase():
        out.append((item.id, item.id))
        out.append((item.id, item.input))
        if item.reference is not None:
            out.append((item.id, item.reference))
    return out


def test_the_showcase_golden_set_loads_and_every_tag_has_at_least_sixteen_items() -> None:
    """The named first-failing test of C15.

    If this fails the showcase report either cannot be produced at all, or is
    produced with a dimension column too thin to say anything -- and the thin
    column is the worse outcome, because the document still renders and the reader
    has no way to see that one of its six claims rests on four items.
    """
    goldenset = _showcase()
    counts = goldenset.stats()["tags"]
    thin = {tag: n for tag, n in counts.items() if n < MIN_ITEMS_PER_TAG}
    assert not thin, (
        f"tags below {MIN_ITEMS_PER_TAG} items: {thin} "
        f"(loaded {len(goldenset)} items from {goldenset.path})"
    )


def test_the_showcase_golden_set_holds_ninety_six_items_across_exactly_six_tags() -> None:
    """The report's dimension matrix is six columns wide by construction.

    A seventh tag adds a column nobody planned the narrative around, and a fifth
    leaves a gap the reader takes for a missing measurement. The item total is
    where the spec's 60-120 band was settled; drifting off it quietly changes how
    long the showcase takes to run and what every screenshot in the document shows.
    """
    goldenset = _showcase()
    assert len(goldenset) == EXPECTED_ITEMS
    assert len(goldenset.stats()["tags"]) == EXPECTED_TAGS


def test_the_six_dimensions_are_the_six_the_showcase_narrative_names() -> None:
    """The row labels of the report's matrix, asserted as strings.

    The count above cannot see a rename, and a rename is the likely edit: these
    names are prose, they are spelled into the narrative adapters and into the
    published document, and one of them is spelled the British way on purpose.
    Swapping a name for a synonym leaves every count in this module intact and
    leaves the showcase describing a dimension it no longer measures.
    """
    assert set(_showcase().stats()["tags"]) == EXPECTED_TAG_NAMES


def test_the_content_hash_is_the_one_the_seeded_series_was_built_on() -> None:
    """The only assertion here that a content change cannot satisfy.

    Everything else in this module states a property -- 96 items, six tags, unique
    inputs -- and a property is exactly what an edit can preserve while changing
    what the items say. The seeded series is generated against this set once; the
    artifacts record this hash; the report the stranger reads is drawn from those
    artifacts. So an unnoticed edit here does not break anything visibly, it just
    makes the document describe items that are no longer in the file.

    If this fails and the edit was intended, re-pin ``EXPECTED_CONTENT_HASH`` and
    re-seed -- in that order, and in one commit. If it fails and you changed
    nothing, the file has been edited by something that did not run the generator.
    """
    assert _showcase().hash == EXPECTED_CONTENT_HASH


def test_every_input_in_the_showcase_set_appears_exactly_once() -> None:
    """Two items asking the same thing get one scripted answer between them.

    ``FakeAdapter`` answers by prompt, so the second of a duplicated pair silently
    overwrites the first -- the scripted regression lands on whichever was written
    last, and the showcase's night-14 collapse stops being the story the document
    tells. Asserted here, and directly, because the guard that catches it later
    raises an error about the demo's limits rather than about this file.
    """
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for item in _showcase():
        first = seen.setdefault(item.input, item.id)
        if first != item.id:
            duplicates.append(f"{first!r} and {item.id!r} share an input")
    assert not duplicates, "; ".join(duplicates)


def test_the_demos_duplicate_input_guard_accepts_the_showcase_set() -> None:
    """The guard that would actually refuse the set at run time.

    The test above says the inputs are unique; this one says the code that cares
    agrees. If the two ever disagree -- over a stripped space, or a normalisation
    one does and the other does not -- the operator meets a refusal message about
    ``migkit demo`` while pointing at a file that has nothing to do with the demo.
    """
    _refuse_duplicate_inputs(_showcase().items)


def test_at_least_one_item_carries_two_tags_so_the_double_count_path_is_exercised() -> None:
    """C8 counts a two-tagged item under both its tags, and the showcase must show it.

    If every showcase item is singly tagged, the one behaviour in the dimension
    matrix a reader will query -- why the columns add up to more than the item
    count -- is never demonstrated by the document that exists to demonstrate it,
    and the only thing standing behind it is a unit test nobody outside reads.
    """
    doubled = [item.id for item in _showcase() if len(item.tags) == 2]
    assert doubled, "no item carries two tags"


def test_the_double_tagged_items_are_a_handful_and_every_item_carries_a_tag() -> None:
    """A set that is mostly multi-tagged, or partly untagged, is a different set.

    Untagged items land in C8's reserved ``""`` key, which the report renders as an
    "untagged" column -- a seventh column in a six-column story, and a reader's
    first question about a document meant to be self-explanatory. And a majority of
    double-tagged items turns the double count from an illustrated footnote into
    the dominant effect in every column.
    """
    items = _showcase().items
    untagged = [item.id for item in items if not item.tags]
    assert not untagged, f"items with no tags: {untagged}"
    over = {item.id: item.tags for item in items if len(item.tags) > 2}
    assert not over, f"items carrying more than two tags: {over}"
    multi = [item.id for item in items if len(item.tags) > 1]
    assert len(multi) * 2 < len(items), (
        f"{len(multi)} of {len(items)} items carry more than one tag; C15 asks for a handful"
    )


def test_the_per_tag_totals_exceed_the_item_count_because_of_the_shared_items() -> None:
    """The arithmetic the document has to be able to explain.

    Recorded as a test because the tempting "fix" is to divide a shared item
    between its tags, which would make the columns sum to ninety-six and make every
    rate in them wrong. A reader who adds the columns up and gets more than the item
    count is seeing the truth; one who gets exactly the item count is being shown a
    number that was reconciled rather than measured.
    """
    goldenset = _showcase()
    slots = sum(goldenset.stats()["tags"].values())
    assert slots > len(goldenset), (
        f"per-tag counts sum to {slots} against {len(goldenset)} items -- no item is "
        f"being counted twice"
    )
    assert slots == sum(len(item.tags) for item in goldenset)


def test_every_tag_clears_the_minimum_sample_size_at_five_draws_per_item() -> None:
    """The showcase must not render its own refusal notice in every column.

    C9 declines a verdict below twenty completions, and declining is the product's
    argument. A showcase whose six columns all decline demonstrates the refusal and
    nothing else -- no verdict, no interval anybody can read, and no reason for the
    reader to believe the tool answers when there is enough evidence to answer.
    """
    counts = _showcase().stats()["tags"]
    short = {
        tag: n * DRAWS_PER_ITEM
        for tag, n in counts.items()
        if n * DRAWS_PER_ITEM < MIN_N_FOR_A_VERDICT
    }
    assert not short, (
        f"tags whose completion count falls below {MIN_N_FOR_A_VERDICT} at "
        f"{DRAWS_PER_ITEM} draws per item: {short}"
    )


def test_every_id_in_the_showcase_set_is_prefixed_synthetic() -> None:
    """A screenshot of the showcase must not be quotable as a real evaluation.

    The ids appear in the per-item flip list, which is the part of the report that
    gets pasted into a ticket. An id without the prefix, read out of context, is
    evidence about a product that was never tested.
    """
    unprefixed = [item.id for item in _showcase() if not item.id.startswith(ID_PREFIX)]
    assert not unprefixed, f"ids without the {ID_PREFIX!r} prefix: {unprefixed}"


def test_no_text_in_the_showcase_set_carries_an_address_somebody_owns() -> None:
    """Publishing a real address in an example is a disclosure, not a typo.

    The showcase is written to be read by strangers and copied as a template, so an
    address that resolves is an address that will be mailed. Addresses at the
    reserved documentation domains are fine -- nobody can receive at them -- and the
    demo set already uses them.
    """
    offenders: list[str] = []
    for item_id, text in _texts():
        for domain in EMAIL.findall(text):
            low = domain.lower()
            if low in RESERVED_EMAIL_DOMAINS or low.endswith(RESERVED_EMAIL_SUFFIXES):
                continue
            offenders.append(f"{item_id}: address at {domain!r}")
    assert not offenders, (
        "; ".join(sorted(set(offenders))) + f" -- use one of {RESERVED_EMAIL_DOMAINS} instead"
    )


def test_no_text_in_the_showcase_set_carries_a_credential_or_an_account_number() -> None:
    """A key pasted into an example is a key somebody has to rotate.

    Only the mechanical half of C15's reviewer note can be tested, so this is the
    mechanical half: nothing shaped like a token, a key, or an account somebody can
    be billed against. Naming a credential is allowed, and is in fact one of the
    refusal prompts the set wants; carrying one is not.
    """
    offenders: list[str] = []
    for item_id, text in _texts():
        for label, pattern in CREDENTIAL_PATTERNS + ACCOUNT_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{item_id}: {label}")
    assert not offenders, "; ".join(sorted(set(offenders)))


def test_a_generator_script_for_the_showcase_set_is_checked_in() -> None:
    """Ninety-six hand-edited lines are ninety-six lines nobody dares regenerate.

    C15 asks for the script alongside the data. Without it the next person who
    wants a seventh dimension, or a different item count, edits JSONL by hand, and
    the set's internal regularities -- the per-tag counts, the shared items -- decay
    on the first edit.
    """
    scripts = REPO_ROOT / "scripts"
    matches = [
        path
        for path in sorted(scripts.rglob("*.py"))
        if "showcase" in path.read_text(encoding="utf-8", errors="replace").lower()
    ]
    assert matches, f"no script under {scripts} mentions the showcase set"
