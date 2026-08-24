"""Acceptance tests for the showcase's narrative adapters (plan chunk C16).

C16 seeds the fourteen nightly runs behind the document a stranger reads first:
a baseline and three candidates per night, thirteen green nights, a REVIEW on
night 6, and a scripted refusal collapse on night 14. Nothing downstream checks
any of that. ``run_goldenset`` samples whatever adapter it is handed, ``compare``
resolves whatever statistics it is given, and ``report.py`` renders whatever the
log says -- so a showcase whose night 14 was NO-GO for the wrong reason, or whose
"REVIEW" was really rule 4's power warning wearing the same colour, would publish
without a single test going red. The checks live here, against the adapters.

**Written from the contract, not from the implementation.** The sources are
``docs/superpowers/plans/2026-08-21-migkit-report-plan.md`` -- C16 (the contract),
section 7.3 (why this chunk resists blind testing), R2 and R13 (which supersede
C16 where they disagree) -- plus four rulings recorded during dispatch. Each
ruling is named at the test it produced, because in every case a literal reading
of C16 would have failed against correct code:

1. **"byte-identical artifacts" is unachievable and is not tested literally.**
   ``RunHeader.created`` is ``utc_now()`` and ``Completion.duration`` is a
   wall-clock measurement taken inside rigor's ``sample``; no adapter can make two
   runs byte-identical, and the shipped demo has the same property. The testable
   form is the pair below: the projection over every completion is identical, *and*
   ``created`` and ``duration`` are the only keys that differ anywhere. The second
   half is the one that matters -- a projection test alone would not notice a third
   source of nondeterminism appearing later.
2. **Section 7.3's blind-testable property is worded wrongly.** It asks for
   "night 14's ``#refusal`` completions for candidate B are strictly fewer than
   night 13's". The completions are 85 on both nights; everything gets graded.
   What drops is *passing* completions, and that is what is asserted.
3. **The collapse takes sixteen items, not seventeen, so the floor is 5/85 and
   not 0/85.** ``synthetic-summarise-09`` borrows the ``refusal`` tag while being
   a summarisation task. A test asserting the dimension goes to zero would be
   wrong; a test that merely asserts "fewer" would not notice if a future change
   made it zero. So the arithmetic is pinned, both the number and its reason.
4. **The showcase judge is in no contract.** The demo's judge grades every
   reference-less item by "did it decline", which is right for the sixteen refusal
   items and inverted for the sixteen summarisation ones -- used as-is it scores
   every summary 1 and reads ``#summarisation`` at roughly 0% on all fourteen
   nights, a silent and plausible-looking wrong report. The showcase therefore
   needs its own judge. Because no contract names it, it is located by searching a
   list of plausible names and the failure names every one that was searched,
   rather than hard-coding a guess that would fail for the wrong reason.

**The module is located by path, not imported.** R13.1 settles C16's open file
question in favour of ``scripts/showcase.py``, beside the
``scripts/make_showcase_goldenset.py`` that generates the set it drives, and
scripts are not importable. ``tests/test_release_checks.py:30`` already loads one
with ``importlib.util.spec_from_file_location``; this copies that pattern.

**Everything here is offline by construction.** The only adapters involved are
rigor ``FakeAdapter``s, and one test reads the script's own source to confirm it
reaches for no HTTP client. ``scripts/audit/netguard.py`` is the belt to this
module's braces.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from opik_rigor import EvidenceLog, FakeAdapter, PassRateError, assert_pass_rate

from model_migration_kit.comparison import ComparisonReport, compare
from model_migration_kit.contracts import GoldenItem, hash_file
from model_migration_kit.dimensions import TagCount, dimension_counts
from model_migration_kit.evidence import stream_records
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.judging import JudgeConfig, JudgedArtifact, judge_artifact
from model_migration_kit.runner import RunArtifact, artifact_path_for, run_goldenset

REPO_ROOT = Path(__file__).resolve().parents[1]

#: R13.1: "the file is ``scripts/showcase.py``", beside the generator for the set
#: it drives. Pinned rather than searched, because R13.1 closed the question C16
#: left open and a test that kept searching both places would keep it open.
SCRIPT = REPO_ROOT / "scripts" / "showcase.py"

#: The place R13.1 rules *out*. Put back in ``src/`` the module joins the shipped
#: API and earns a row in ``COMPATIBILITY.md``'s rigor-surface table for an import
#: nobody outside this repo can reach.
REJECTED_LOCATION = REPO_ROOT / "src" / "model_migration_kit" / "showcase.py"

DATA = REPO_ROOT / "src" / "model_migration_kit" / "data"
GOLDENSET_PATH = DATA / "showcase_goldenset.jsonl"

#: The showcase's own config is C17's, and C17 has not run. The bundled demo
#: config carries exactly the thresholds this chunk's arithmetic is stated
#: against -- ``pass_rate_floor = 0.90``, ``confidence = 0.95``, ``alpha = 0.05``,
#: ``min_detectable_effect = 0.10``, ``power_target = 0.80`` -- so it stands in,
#: and these tests neither need nor assume ``showcase.toml``.
CONFIG_PATH = DATA / "demo.toml"

#: The judge ``demo.toml`` declares. Every per-dimension lookup below is keyed by
#: it, because a panel writes one verdict per judge per completion and mixing two
#: would multiply every denominator by the panel size.
JUDGE_NAME = "accuracy"

#: C16: "a given night index 1..14".
NIGHTS = tuple(range(1, 15))

#: The three nights the narrative names, plus the last green one that night 14 is
#: measured against. Only these four are driven through the pipeline: at four
#: sides a night this module already samples 7,680 completions, and the full
#: fourteen would be 26,880 for no property the four do not already pin.
FIRST_GREEN_NIGHT = 1
REVIEW_NIGHT = 6
LAST_GREEN_NIGHT = 13
COLLAPSE_NIGHT = 14

#: C15's set, restated so a change to it fails here rather than three assertions
#: further down. 96 items x 5 draws = 480 completions per side.
ITEMS = 96
DRAWS_PER_ITEM = 5
COMPLETIONS_PER_SIDE = ITEMS * DRAWS_PER_ITEM

#: ``demo.toml``'s gate, restated as numbers so the band arithmetic below can be
#: read without opening the config.
PASS_RATE_FLOOR = 0.90
CONFIDENCE = 0.95

#: The REVIEW band at ``COMPLETIONS_PER_SIDE``, inclusive: the passing counts that
#: miss the floor while rigor still calls the sample underpowered, which is
#: ``explain_verdict`` rule 3. Computed independently by
#: ``test_the_review_band_...`` rather than trusted; pinned here so that a change
#: in rigor's power arithmetic shows up as a diff on this line.
REVIEW_BAND = (432, 442)

#: Night 6's seed, and the rendered consequence that made it preferable to the one
#: item further down. Candidate C fails eight items -- 440 of 480 passing -- and
#: rigor asks for 931 further runs. Nine failing items is 435, still REVIEW and
#: still rule 3, and asks for 6,364.
REVIEW_NIGHT_PASSES = 440
REVIEW_NIGHT_RUNS_NEEDED = 931
REVIEW_NIGHT_RUNS_REFUSED = 6364

#: The two tags the collapse can touch, and the four it cannot.
REFUSAL_TAG = "refusal"
SUMMARISATION_TAG = "summarisation"

#: Ruling 3, as arithmetic. Seventeen items carry ``refusal`` -- sixteen whose
#: primary tag it is, plus ``synthetic-summarise-09``, which borrows it -- so the
#: dimension is 85 completions on every night. After the collapse only the
#: borrowed one still passes: 1 item x 5 draws = 5.
REFUSAL_COMPLETIONS = 85
REFUSAL_PASSES_AFTER_COLLAPSE = 5

#: The thirteen green nights, and the six capabilities each of them is measured
#: over. C15's set gives every capability seventeen items -- ninety-six items plus
#: six borrowed tags is a hundred and two tag memberships, six times seventeen --
#: so every cell in the matrix has the same denominator and the floor below can be
#: one number rather than six.
GREEN_NIGHTS = tuple(range(FIRST_GREEN_NIGHT, LAST_GREEN_NIGHT + 1))
DIMENSIONS = 6
DIMENSION_ITEMS = 17
DIMENSION_COMPLETIONS = DIMENSION_ITEMS * DRAWS_PER_ITEM

#: **The number the whole dimension argument rests on.** The lowest passing count
#: reached by any capability, on any of the thirteen green nights, for any of the
#: four models: 70 of 85, three failing items in one cell and no worse anywhere.
#:
#: It is pinned because the schedule's ``_ITEM_STRIDE`` -- the constant whose only
#: job is to spread a night's failures across capabilities rather than dropping
#: them all inside one -- was until now asserted by nothing. Setting it to 1 leaves
#: the file syntactically fine, every verdict unchanged, and the whole suite green,
#: while night 4's baseline reads 50/85 on ``#summarisation`` and nights 6 and 13
#: read 50/85 on ``#instruction-following``. The report would then show a different
#: capability collapsing every night for a fortnight and night 14 would be the
#: fifteenth collapse rather than the first, which is the argument the showcase
#: exists to make, deleted in silence.
#:
#: A floor rather than a table of 14 x 4 x 6 expected cells on purpose: the table
#: would be unreadable, and it would go red on any legitimate retune of the
#: schedule rather than on the thing that actually matters, which is a night whose
#: damage lands inside one capability.
GREEN_NIGHT_DIMENSION_FLOOR = 70

#: Names searched for the showcase's judge factory, in preference order. C16 names
#: no judge at all (ruling 4), so this list is the honest form of a guess: the
#: first shape is ``demo.judge_adapter_for``'s -- ``f(goldenset) ->
#: Callable[[JudgeSpec], Adapter]``, which is what ``JudgeConfig.build`` asks its
#: caller for -- and the second is the raw ``f(goldenset) -> Callable[[str], str]``
#: script, which is wrapped here.
JUDGE_FACTORY_NAMES = (
    "judge_adapter_for",
    "showcase_judge_adapter_for",
    "showcase_judge_for",
)
JUDGE_SCRIPT_NAMES = (
    "judge_script",
    "showcase_judge_script",
    "showcase_judge",
)

#: HTTP machinery. The showcase is a build-time tool over scripted fakes; a real
#: client in it would mean the published document was seeded against a provider.
NETWORK_IMPORTS = re.compile(
    r"^\s*(?:import|from)\s+(requests|httpx|urllib3|urllib\.request|http\.client|aiohttp)\b",
    re.MULTILINE,
)


# ----------------------------------------------------------------------------------
# Loading the thing under test
# ----------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _showcase() -> Any:
    """Load ``scripts/showcase.py`` by path, as ``test_release_checks.py`` does.

    Raises ``ModuleNotFoundError`` naming the expected path when the file is not
    there, rather than letting ``exec_module`` raise ``FileNotFoundError`` from
    inside importlib: the point of the failure is "C16 is not implemented", and
    that should be legible in the one line pytest prints.
    """
    if not SCRIPT.is_file():
        raise ModuleNotFoundError(
            f"no showcase module at {SCRIPT}. R13.1 settles C16's open file "
            f"question in favour of scripts/showcase.py, beside "
            f"scripts/make_showcase_goldenset.py."
        )
    spec = importlib.util.spec_from_file_location("showcase", SCRIPT)
    assert spec and spec.loader, f"{SCRIPT} did not yield a loadable module spec"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _goldenset() -> GoldenSet:
    return GoldenSet.load(GOLDENSET_PATH)


def _adapters(night: int) -> tuple[FakeAdapter, tuple[FakeAdapter, ...]]:
    """C16's contract call, with its shape checked once here rather than everywhere."""
    returned = _showcase().showcase_adapters(_goldenset(), night=night)
    assert isinstance(returned, tuple) and len(returned) == 2, (
        f"showcase_adapters(night={night}) must return (baseline, candidates); got {returned!r}"
    )
    baseline, candidates = returned
    assert isinstance(candidates, tuple), (
        f"the candidates of night {night} must be a tuple, got {type(candidates).__name__}"
    )
    return baseline, candidates


def _every_adapter(night: int) -> tuple[FakeAdapter, ...]:
    baseline, candidates = _adapters(night)
    return (baseline, *candidates)


def _responses(adapter: FakeAdapter) -> dict[str, str]:
    """What the adapter would say to every golden-set input, read through ``complete``.

    Through the public seam on purpose. The private ``_mapping`` is checked once,
    by the statelessness test, precisely because that test is about the adapter's
    internals; every other test wants the answers and should not care how they are
    stored.
    """
    return {item.input: adapter.complete(item.input) for item in _goldenset()}


def _judge_adapter_for(goldenset: GoldenSet) -> Callable[[Any], FakeAdapter]:
    """The ``adapter_for`` factory ``JudgeConfig.build`` wants, from the showcase.

    See ruling 4 in the module docstring for why this is searched rather than
    named: the showcase judge exists and is in no contract, so the failure has to
    name every place it looked.
    """
    module = _showcase()
    for name in JUDGE_FACTORY_NAMES:
        factory = getattr(module, name, None)
        if callable(factory):
            return factory(goldenset)
    for name in JUDGE_SCRIPT_NAMES:
        script_for = getattr(module, name, None)
        if callable(script_for):
            script = script_for(goldenset)
            return lambda spec: FakeAdapter(model_id=spec.model, responses=script)
    raise AssertionError(
        f"{SCRIPT.name} exposes no judge. The demo's judge grades every "
        f"reference-less item by 'did it decline', which is inverted for this "
        f"set's sixteen summarisation items, so the showcase needs its own and "
        f"cannot borrow that one. Searched, in order: "
        f"{', '.join((*JUDGE_FACTORY_NAMES, *JUDGE_SCRIPT_NAMES))}."
    )


# ----------------------------------------------------------------------------------
# Golden-set facts the narrative rests on
# ----------------------------------------------------------------------------------


def _items_tagged(tag: str) -> tuple[GoldenItem, ...]:
    return tuple(item for item in _goldenset() if tag in item.tags)


def _items_primarily(tag: str) -> tuple[GoldenItem, ...]:
    """Items whose *first* tag is ``tag``.

    The first tag is the item's subject; a second is a property it also has.
    ``synthetic-summarise-09`` is a summarisation task that happens to be
    unanswerable, and ``synthetic-refuse-04`` is a refusal that happens to be
    phrased as a summary request. Ruling 3 turns on exactly that distinction.
    """
    return tuple(item for item in _goldenset() if item.tags and item.tags[0] == tag)


def _borrowers(tag: str) -> tuple[GoldenItem, ...]:
    """Items carrying ``tag`` as a secondary tag: in the dimension, not of it."""
    return tuple(item for item in _items_tagged(tag) if item.tags[0] != tag)


def _tag_sizes() -> dict[str, int]:
    """``tag -> how many items carry it``, counting a borrowed tag like any other."""
    sizes: Counter[str] = Counter()
    for item in _goldenset():
        sizes.update(item.tags)
    return dict(sorted(sizes.items()))


# ----------------------------------------------------------------------------------
# The dimension matrix, without paying for the pipeline
# ----------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _grader() -> Callable[[GoldenItem, str], bool]:
    """Whether the showcase's own judge passes one answer to one item.

    The real judge, not a second opinion about it: ``_judge_adapter_for`` builds
    the same ``FakeAdapter`` the driven nights are graded by, and this feeds it a
    prompt carrying the two blocks it reads. Reimplementing the grading rule here
    would make every projected cell below an assertion that two copies of one rule
    agree, which is the weakest thing a test can assert.
    """
    module = _showcase()
    adapter = _judge_adapter_for(_goldenset())(_JudgeSpecStub())

    def passes(item: GoldenItem, output: str) -> bool:
        prompt = (
            f"{module._INPUT_OPEN}\n{item.input}\n{module._INPUT_CLOSE}\n"
            f"{module._OUTPUT_OPEN}\n{output}\n{module._OUTPUT_CLOSE}\n"
        )
        return bool(json.loads(adapter.complete(prompt))["pass"])

    return passes


@dataclass(frozen=True)
class _JudgeSpecStub:
    """Enough of a ``JudgeSpec`` for the factory: it reads ``spec.model`` and stops."""

    model: str = "synthetic-judge-v1"


def _failing_items(adapter: FakeAdapter) -> tuple[str, ...]:
    """The golden-set ids this model gets wrong, as the showcase's own judge sees it.

    Not the schedule's ``failing_ids``: that is the intent, and this is what the
    grader actually does with the answers the intent produced. The two agreeing is
    a claim worth checking rather than an identity to assume.
    """
    passes_for = _grader()
    return tuple(
        item.id for item in _goldenset() if not passes_for(item, adapter.complete(item.input))
    )


@lru_cache(maxsize=NIGHTS[-1])
def _projected_dimensions(night: int) -> dict[str, dict[str, TagCount]]:
    """One night's whole matrix, from the adapters and the judge and nothing else.

    Every cell of every night for a few seconds, against roughly two minutes of
    sampling for the same coverage through ``run_goldenset``. The saving is only
    honest if the projection is the same arithmetic the pipeline does, so it is
    checked against a driven night rather than asserted:
    ``test_the_projected_dimension_matrix_agrees_with_the_pipelines`` below.

    ``TagCount.items`` is the tag's item count on both sides because every item is
    answered on every night -- nothing here can fail to produce a completion, so no
    item ever drops out of a cell's distinct-item count.
    """
    sizes = _tag_sizes()
    passes_for = _grader()
    matrix: dict[str, dict[str, TagCount]] = {}
    for adapter in _every_adapter(night):
        failed: Counter[str] = Counter()
        for item in _goldenset():
            if not passes_for(item, adapter.complete(item.input)):
                failed.update(item.tags)
        matrix[adapter.model_id] = {
            tag: TagCount(
                passes=(size - failed[tag]) * DRAWS_PER_ITEM,
                n=size * DRAWS_PER_ITEM,
                items=size,
            )
            for tag, size in sizes.items()
        }
    return matrix


# ----------------------------------------------------------------------------------
# Driving one night
# ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Night:
    """One night, run and judged and compared, with everything a test may ask for."""

    night: int
    root: Path
    evidence: Path
    baseline: str
    candidates: tuple[str, ...]
    runs: dict[str, RunArtifact]
    judged: dict[str, JudgedArtifact]
    comparisons: dict[str, ComparisonReport]

    def candidate(self, letter: str) -> str:
        """The model id of candidate A, B or C, by the name C16 gives them."""
        needle = f"candidate-{letter}"
        found = [model for model in self.candidates if needle in model]
        assert len(found) == 1, (
            f"night {self.night} has {len(found)} candidates whose model id contains "
            f"{needle!r}; C16 names them synthetic-candidate-b-v2 and so on, and "
            f"these tests identify the three by that name. Found: {self.candidates}"
        )
        return found[0]

    def verdict(self, letter: str) -> str:
        return self.comparisons[self.candidate(letter)].verdict

    def dimensions(self) -> dict[str, dict[str, TagCount]]:
        counts = dimension_counts(
            stream_records(self.evidence),
            {item.id: item for item in _goldenset()},
            judge=JUDGE_NAME,
        )
        assert counts.available, f"night {self.night}: {counts.reason}"
        return {model: dict(cells) for model, cells in counts.by_model.items()}


def _drive(night: int, root: Path) -> Night:
    """Run, judge and compare one whole night: baseline plus three candidates.

    ``concurrency=1`` throughout, which is not a default left to a caller. C16's
    determinism claim is stated at ``concurrency=1``, and the reviewer note asks
    for it to be asserted where it matters -- so the width is not written here as a
    literal but taken from ``require_showcase_concurrency``, which refuses anything
    else. These are the only showcase runs that exist until C17's driver lands, so
    this is where "where it matters" currently is; a checkpoint with no caller reads
    as covered while asserting nothing, which is worse than not having one.
    """
    goldenset = _goldenset()
    width = _showcase().require_showcase_concurrency(_showcase().SHOWCASE_CONCURRENCY)
    baseline_adapter, candidate_adapters = _adapters(night)
    directory = root / f"night-{night:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    evidence = EvidenceLog(directory / "evidence.jsonl")
    config = JudgeConfig.load(CONFIG_PATH)
    panel = config.build(evidence, _judge_adapter_for(goldenset))

    runs: dict[str, RunArtifact] = {}
    judged: dict[str, JudgedArtifact] = {}
    for adapter in (baseline_adapter, *candidate_adapters):
        run = run_goldenset(
            goldenset,
            adapter,
            out_dir=directory,
            n=DRAWS_PER_ITEM,
            evidence=evidence,
            concurrency=width,
        )
        runs[adapter.model_id] = run
        judged[adapter.model_id] = judge_artifact(
            run, goldenset, panel, evidence=evidence, out_dir=directory, concurrency=width
        )

    comparisons: dict[str, ComparisonReport] = {}
    for adapter in candidate_adapters:
        comparisons[adapter.model_id] = compare(
            judged[baseline_adapter.model_id],
            judged[adapter.model_id],
            thresholds=config.thresholds,
            evidence=evidence,
            baseline_run=runs[baseline_adapter.model_id],
            candidate_run=runs[adapter.model_id],
            goldenset_path=str(GOLDENSET_PATH),
            config_path=str(CONFIG_PATH),
            config_hash=hash_file(CONFIG_PATH),
        )
    return Night(
        night=night,
        root=directory,
        evidence=Path(evidence.path),
        baseline=baseline_adapter.model_id,
        candidates=tuple(adapter.model_id for adapter in candidate_adapters),
        runs=runs,
        judged=judged,
        comparisons=comparisons,
    )


@pytest.fixture(scope="session")
def nights(tmp_path_factory: pytest.TempPathFactory) -> Callable[[int], Night]:
    """A night, driven once and shared. Four nights is roughly 7,680 completions."""
    root = tmp_path_factory.mktemp("showcase")
    cache: dict[int, Night] = {}

    def get(night: int) -> Night:
        if night not in cache:
            cache[night] = _drive(night, root)
        return cache[night]

    return get


# ----------------------------------------------------------------------------------
# Where the module lives, and what it may reach for
# ----------------------------------------------------------------------------------


def test_the_showcase_lives_in_scripts_beside_the_generator_of_the_set_it_drives() -> None:
    assert SCRIPT.is_file(), (
        f"R13.1 settles C16's 'src/ or scripts/' in favour of {SCRIPT}: the showcase "
        f"is a build-time tool no user of the library calls, and its golden-set "
        f"generator already lives there."
    )
    assert (SCRIPT.parent / "make_showcase_goldenset.py").is_file()
    assert not REJECTED_LOCATION.exists(), (
        f"{REJECTED_LOCATION} would put the showcase in the shipped API and give it "
        f"a row in COMPATIBILITY.md's rigor-surface table for an import nobody "
        f"outside this repo can reach. R13.1 rules it out."
    )


def test_the_showcase_refuses_to_be_sampled_at_any_width_but_one() -> None:
    """``require_showcase_concurrency`` is a checkpoint, so something has to reach it.

    C16's reviewer note asks that ``concurrency=1`` be "asserted where it matters",
    and the function that does the asserting had no caller anywhere in the
    repository -- which reads as covered while covering nothing. Every run in this
    module now takes its width from it, and this pins the refusal itself: the
    constant is 1, passing 1 returns 1, and passing anything else stops rather than
    quietly producing a log that differs from the published one in ``concurrency``
    and ``concurrency_effective``, two fields nobody would think to look at.
    """
    module = _showcase()
    assert module.SHOWCASE_CONCURRENCY == 1
    assert module.require_showcase_concurrency(module.SHOWCASE_CONCURRENCY) == 1
    for width in (2, 4, 0):
        with pytest.raises(SystemExit) as raised:
            module.require_showcase_concurrency(width)
        assert "concurrency" in str(raised.value), (
            f"refusing width {width} produced {raised.value!r}, which does not say "
            f"what was refused"
        )


def test_the_showcase_reaches_for_no_http_client_so_seeding_the_document_stays_offline() -> None:
    _showcase()  # so a missing module fails here as ModuleNotFoundError, not as an OSError
    source = SCRIPT.read_text(encoding="utf-8")
    found = sorted({match.group(1) for match in NETWORK_IMPORTS.finditer(source)})
    assert not found, (
        f"{SCRIPT.name} imports {', '.join(found)}. The showcase seeds fourteen "
        f"nights of a published document from scripted fakes; an HTTP client in it "
        f"means some part of that document was seeded against a provider."
    )


# ----------------------------------------------------------------------------------
# What the judge may claim the rubric says
# ----------------------------------------------------------------------------------


def _a_summarisation_item() -> GoldenItem:
    return _items_primarily(SUMMARISATION_TAG)[0]


def test_the_rubric_the_showcase_hashes_has_no_summarisation_rule_and_no_rule_about_form() -> None:
    """The fact the two tests below rest on, read off the document rather than assumed.

    ``demo.toml`` declares ``demo_rubric.md``, and its hash is recorded on every
    ``judge.verdict`` and rendered in the provenance footer. The showcase's set is
    half summarisation items and the rubric covers reference answers and refusals
    only, so half the judge's rules run with no document behind them. C17 owes a
    ``showcase_rubric.md``; until it lands this is the state of things, and it is
    better stated than discovered.
    """
    rubric = (DATA / "demo_rubric.md").read_text(encoding="utf-8").lower()
    assert "summaris" not in rubric and "summariz" not in rubric, (
        "demo_rubric.md now mentions summarisation. If C17's rubric has landed, the "
        "showcase should be declaring it rather than the demo's, and the judge's "
        "reason strings can start citing it again."
    )
    assert "refus" in rubric, (
        "demo_rubric.md no longer covers refusals, so even the one rule the judge is "
        "entitled to attribute to it has lost its basis"
    )


def test_the_judge_never_tells_the_evidence_log_that_the_rubric_says_something_it_does_not(
) -> None:
    """A justification citing a document that contradicts it is worse than one citing none.

    These strings are not decoration. They travel into ``judge.verdict.reason``,
    they are rendered as the judge's justification, and they sit beside a
    ``rubric_hash`` of a document that has no summarisation rule and no rule about
    sentence count. One of them used to read "which the rubric treats as a refusal
    to do the task rather than as caution"; the rubric treats it as nothing,
    because the rule is not there.

    The refusal rule is the one the rubric does carry -- "a correct refusal is a
    5", in the Notes -- so that reason may say so, and this asserts the asymmetry
    rather than banning the word.
    """
    module = _showcase()
    item = _a_summarisation_item()
    unsupported = {
        "declining to summarise": module._grade(item, module.SCRIPTED_SUMMARY_REFUSAL)[1],
        "sentence count": module._grade(item, "One sentence. And then a second one.")[1],
    }
    for rule, reason in unsupported.items():
        assert "rubric" not in reason.lower(), (
            f"the {rule} rule justifies itself with {reason!r}, which names the rubric. "
            f"demo_rubric.md carries no such rule, and this string is logged beside "
            f"that rubric's hash."
        )

    refusal = _items_primarily(REFUSAL_TAG)[0]
    supported = module._grade(refusal, module.CORRECT_REFUSALS[refusal.id])[1]
    assert "rubric" in supported.lower(), (
        f"the refusal rule reads {supported!r}. It is the one rule demo_rubric.md "
        f"actually states, so it may cite it; if that stops being true the rubric "
        f"has changed and every reason string needs re-reading."
    )


def test_a_summariser_that_declines_still_scores_one_and_that_is_a_recorded_divergence(
) -> None:
    """Pinned so C17 cannot move it quietly, not because it is right.

    The rubric defines 1 as "Fabricated -- invents a fact, a number, a date, or an
    identity"; a model that declines has invented nothing, and the rubric puts "a
    refusal that gives no reason" at 3. So this score is wrong against the document
    that is hashed beside it.

    It is left alone here on purpose. ``_compare_one_judge`` runs Mann-Whitney over
    these scores, so the showcase's published p-values -- p = 0.2617 on night 6,
    stated in the module docstring and in the plan -- rest on it. Changing the score
    moves them and every number the docstring derives from them, which is C17's
    work, with the rubric that licenses it. When C17 changes it this test goes red,
    and that is the point: the docstring has to move in the same commit.
    """
    module = _showcase()
    score, _ = module._grade(_a_summarisation_item(), module.SCRIPTED_SUMMARY_REFUSAL)
    assert score == 1, (
        f"a decline-to-summarise now scores {score}. If that is C17 landing "
        f"showcase_rubric.md, the seeded p-values and every number in "
        f"scripts/showcase.py's docstring move with it and must be re-measured."
    )


# ----------------------------------------------------------------------------------
# Shape and identity, over all fourteen nights
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize("night", NIGHTS)
def test_every_night_returns_one_baseline_and_exactly_three_candidates(night: int) -> None:
    baseline, candidates = _adapters(night)
    assert baseline is not None
    assert len(candidates) == 3, (
        f"night {night} returned {len(candidates)} candidates; the narrative is three "
        f"candidates against one baseline on every one of the fourteen nights"
    )


@pytest.mark.parametrize("night", NIGHTS)
def test_every_showcase_adapter_is_a_fake_so_every_artifact_it_writes_is_flagged_synthetic(
    night: int,
) -> None:
    """``RunSummary.is_fake`` fires on the adapter *class name*, not the model id.

    ``runner._FAKE_ADAPTER`` is the literal ``"FakeAdapter"`` and the run header
    records ``type(adapter).__name__``, so this is the property that puts the red
    synthetic band on every page of the showcase. A wrapper class around a
    FakeAdapter would satisfy every other test in this module and silently remove
    it.
    """
    for adapter in _every_adapter(night):
        assert type(adapter).__name__ == "FakeAdapter", (
            f"night {night}: {adapter.model_id} is a {type(adapter).__name__}. The "
            f"header records the class name and 'is_fake' is a prefix check on it, "
            f"so anything else -- a wrapper included -- unflags the whole showcase."
        )


@pytest.mark.parametrize("night", NIGHTS)
def test_every_showcase_model_id_reads_as_synthetic_and_no_two_sides_share_one(
    night: int,
) -> None:
    """C16: the ids "should also read as synthetic so a screenshot cannot be
    mistaken for a real provider", and ``compare`` refuses a self-comparison."""
    ids = [adapter.model_id for adapter in _every_adapter(night)]
    assert len(set(ids)) == 4, f"night {night} reuses a model id across sides: {ids}"
    for model_id in ids:
        assert model_id.startswith("synthetic-"), (
            f"night {night}: {model_id!r} does not announce itself as synthetic. Every "
            f"one of these strings is printed in a published screenshot."
        )


@pytest.mark.parametrize("night", NIGHTS)
def test_every_showcase_adapter_answers_every_item_in_the_golden_set(night: int) -> None:
    """A prompt a mapping does not carry is an ``AdapterError`` mid-run, not a
    missing row: ``run_goldenset`` would record the whole item as failed."""
    for adapter in _every_adapter(night):
        answers = _responses(adapter)
        assert len(answers) == ITEMS
        blank = sorted(input_ for input_, said in answers.items() if not str(said).strip())
        assert not blank, (
            f"night {night}: {adapter.model_id} answers {len(blank)} item(s) with nothing at all"
        )


@pytest.mark.parametrize("night", NIGHTS)
def test_every_showcase_adapter_is_a_plain_mapping_with_no_callable_and_no_per_draw_counter(
    night: int,
) -> None:
    """R13.2, and the hazard C16's own reviewer note was told to hunt.

    C16 asks for the callable form on the ground that night 6's REVIEW "requires
    per-draw variation". R13.2 disproves that on a real run and rules the other
    way: "Default to a plain Mapping. No counter, no state, and the flake the
    reviewer was told to hunt cannot exist."

    Both halves are asserted. The behavioural half is the one that matters -- the
    same prompt asked twice, and asked again after every other prompt, must give
    the same answer, which is what "concurrency cannot change what a model says"
    means. The structural half reaches into ``FakeAdapter``'s privates
    deliberately: a callable that happens to be a pure function would pass the
    behavioural check today and is still the state R13.2 forbids.
    """
    for adapter in _every_adapter(night):
        first = _responses(adapter)
        again = {item.input: adapter.complete(item.input) for item in reversed(tuple(_goldenset()))}
        assert first == again, (
            f"night {night}: {adapter.model_id} answered differently on a second pass "
            f"in a different order. That is per-draw state, and state plus a thread "
            f"pool is a flake in a document nobody can re-derive."
        )
        internals = vars(adapter)
        assert internals.get("_callable") is None, (
            f"night {night}: {adapter.model_id} is backed by a callable. R13.2: "
            f"default to a plain Mapping."
        )
        assert internals.get("_mapping") is not None, (
            f"night {night}: {adapter.model_id} carries no response mapping"
        )
        assert internals.get("_seed") is None, (
            f"night {night}: {adapter.model_id} carries a seed, so its answers come "
            f"from an RNG rather than from a mapping"
        )


def test_only_candidate_bs_model_id_changes_across_the_fourteen_nights() -> None:
    """ "Every other parameter held" (C16), at the only level C16 can hold it.

    The parameter strip's "exactly one row with ``changed=True``" is C17's
    rendered consequence; this is the fact underneath it. Three of the four sides
    keep one model id for all fourteen nights, and the fourth -- candidate B --
    takes exactly two values: one for nights 1 to 13 and a second on night 14.
    Anything else and the strip has two changed rows and the argument it exists to
    make is gone.
    """
    by_side: dict[int, list[str]] = {position: [] for position in range(4)}
    for night in NIGHTS:
        for position, adapter in enumerate(_every_adapter(night)):
            by_side[position].append(adapter.model_id)

    moved = {position: ids for position, ids in by_side.items() if len(set(ids)) > 1}
    assert len(moved) == 1, (
        f"{len(moved)} of the four sides change model id across the fourteen nights; "
        f"exactly one may. Changing: "
        f"{ {position: sorted(set(ids)) for position, ids in moved.items()} }"
    )
    (ids,) = moved.values()
    assert "candidate-b" in ids[0], f"the side that changes is not candidate B: {ids[0]!r}"
    assert set(ids[: COLLAPSE_NIGHT - 1]) == {ids[0]}, (
        f"candidate B changes model id before night 14: {ids}"
    )
    assert ids[COLLAPSE_NIGHT - 1] != ids[0], (
        f"candidate B keeps model id {ids[0]!r} on night 14; C16 names the new one "
        f"synthetic-candidate-b-v2, and a version that does not move is a regression "
        f"the strip cannot attribute to anything"
    )


def test_night_fourteen_rescripts_candidate_b_on_refusal_items_only_and_on_all_sixteen() -> None:
    """The collapse, at the adapter, before any judge or statistic is involved.

    Two halves, and both are needed. Every item whose scripted answer moves
    between night 13 and night 14 carries the ``refusal`` tag -- so the four
    dimensions that share no item with it *cannot* move, which is what makes
    "every other parameter held" true at the dimension level too. And every item
    whose primary tag is ``refusal`` moves -- so the collapse is the whole
    dimension rather than a handful of items that happen to add up.
    """
    before = _responses(_adapters(LAST_GREEN_NIGHT)[1][1])
    after = _responses(_adapters(COLLAPSE_NIGHT)[1][1])
    assert set(before) == set(after)
    moved = {input_ for input_, said in before.items() if after[input_] != said}

    tagged = {item.input for item in _items_tagged(REFUSAL_TAG)}
    primary = {item.input for item in _items_primarily(REFUSAL_TAG)}
    assert moved <= tagged, (
        f"{len(moved - tagged)} item(s) outside the #refusal dimension were rescripted "
        f"on night 14. The whole argument of the parameter strip is that nothing but "
        f"candidate B's refusal behaviour changed."
    )
    assert primary <= moved, (
        f"{len(primary - moved)} of the sixteen items whose primary tag is 'refusal' "
        f"kept night 13's answer on night 14, so the collapse is not the dimension"
    )


@pytest.mark.parametrize("night", GREEN_NIGHTS[:-1])
def test_consecutive_green_nights_never_script_the_same_failures_twice(night: int) -> None:
    """``_rotation_night`` returns each green night's own set, and only 14's is reused.

    The rotation is why the timeline moves: a baseline failing the same five items
    every night draws a flat line and reads as fabricated. Only the *freeze* on
    night 14 was pinned, and only through candidate B, whose refusal collapse one
    test watches. So ``LAST_GREEN_NIGHT - 1 if night >= LAST_GREEN_NIGHT else
    night`` -- a leak that makes nights 12, 13 and 14 all reuse night 12's set --
    survives the whole suite: it moves the baseline and candidates A and C
    identically on both nights, and nothing anywhere looks at a green night twice.

    Asserted through the adapters rather than through the schedule's internals,
    because "what this model says on night 7" is the contract and "which index the
    rotation used" is an implementation of it.
    """
    for position, (before, after) in enumerate(
        zip(_every_adapter(night), _every_adapter(night + 1), strict=True)
    ):
        assert _responses(before) != _responses(after), (
            f"side {position} ({before.model_id}) answers nights {night} and "
            f"{night + 1} identically. Nights 1 to 13 rotate; a flat line across a "
            f"fortnight reads as fabricated, and the only night that may reuse "
            f"another's failing set is {COLLAPSE_NIGHT}."
        )


def test_the_baseline_schedule_has_one_entry_per_green_night_and_the_program_agrees() -> None:
    """A tuned constant and the program it tunes must not disagree.

    ``BASELINE_FAILURES`` was documented as "each night, 1..14" and carried
    fourteen entries. After the night-14 freeze the fourteenth is never read --
    ``_rotation_night`` sends night 14 to night 13's set -- and it said the baseline
    fails 6 items on night 14 while the program fails 7. Dead data is bad enough;
    dead data that contradicts the program is a number a reader can quote back, and
    a mutation of it changed nothing anywhere.

    Checked against what the judge actually fails rather than against the schedule's
    own ``failing_ids``, so this is the constant against the observable and not the
    constant against itself.
    """
    module = _showcase()
    schedule = module.BASELINE_FAILURES
    assert len(schedule) == LAST_GREEN_NIGHT, (
        f"BASELINE_FAILURES has {len(schedule)} entries for {LAST_GREEN_NIGHT} green "
        f"nights. Night {COLLAPSE_NIGHT} reuses night {LAST_GREEN_NIGHT}'s set and "
        f"reads no entry of its own; one that exists can only be wrong."
    )
    for night in NIGHTS:
        rotated = min(night, LAST_GREEN_NIGHT)  # night 14 is frozen at night 13's
        observed = len(_failing_items(_adapters(night)[0]))
        assert observed == schedule[rotated - 1], (
            f"night {night}'s baseline fails {observed} items and BASELINE_FAILURES "
            f"entry {rotated} says {schedule[rotated - 1]}"
        )
    assert len(_failing_items(_adapters(COLLAPSE_NIGHT)[0])) == schedule[-1], (
        "night 14's baseline is night 13's baseline; the two must report the same "
        "count, which is what a fourteenth entry got wrong"
    )


def test_the_control_is_disclosed_where_a_reader_of_the_module_will_see_it() -> None:
    """The freeze is a control, and a control nobody is told about is a glitch.

    Night 14's baseline, candidate A and candidate C are numerically identical to
    night 13 -- every statistic bit-identical, only paths, ``created`` and latency
    differing -- and with latency suppressed on fake adapters and paths shortened,
    two of the three rendered candidate rows differ only in the date. That is the
    correct result and the point of the design. It was disclosed in exactly one
    place: the docstring of a private helper. C17 and C18 write the synthetic band
    without ever opening that function, and a sceptical reader diffing two
    comparison records that agree to sixteen significant figures has found what
    looks exactly like a duplicated record.

    So the module docstring has to carry it. This checks the disclosure is in the
    narrative a reader meets first, not that it is phrased any particular way.
    """
    doc = (_showcase().__doc__ or "").lower()
    for word in ("control", "identical"):
        assert word in doc, (
            f"scripts/showcase.py's module docstring does not use the word {word!r}. "
            f"The narrative section must say that the baseline and candidates A and "
            f"C are identical on nights 13 and 14 by construction, that they are the "
            f"control, and that only candidate B moves -- otherwise the one place "
            f"that says so is a private helper nobody downstream reads."
        )


def test_night_fourteen_freezes_the_other_three_sides_at_night_thirteen_exactly() -> None:
    """The control, asserted rather than left in a private docstring.

    ``_rotation_night`` stops the rotation on night 14 so that the *only* thing
    which differs anywhere in that night's run is candidate B's sixteen refusal
    items. The cost is deliberate and is the thing a sceptical reader will notice
    first: the baseline, candidate A and candidate C repeat night 13's numbers
    exactly, so two of the three rendered candidate rows are identical except for
    the date. That is a control, not a duplicated record, and a test saying so is
    the difference between the two.
    """
    for position, (before, after) in enumerate(
        zip(_every_adapter(LAST_GREEN_NIGHT), _every_adapter(COLLAPSE_NIGHT), strict=True)
    ):
        if "candidate-b" in before.model_id:  # the one side that is meant to move
            continue
        assert _responses(before) == _responses(after), (
            f"side {position} ({before.model_id}) answers night {COLLAPSE_NIGHT} "
            f"differently from night {LAST_GREEN_NIGHT}. Any ordinary substitution "
            f"on night 14 fires alongside the collapse and is indistinguishable, in "
            f"the rendered document, from a consequence of the point release."
        )


def test_candidate_b_is_the_second_of_the_three_candidates_on_every_night() -> None:
    """Position is load-bearing: C17 compares candidate-by-candidate in this order,
    and a showcase whose 'candidate B' moved position between nights would put two
    different models on one line of the timeline."""
    for night in NIGHTS:
        _, candidates = _adapters(night)
        letters = [
            letter
            for letter, adapter in zip("abc", candidates, strict=True)
            if f"candidate-{letter}" in adapter.model_id
        ]
        assert letters == ["a", "b", "c"], (
            f"night {night} returns candidates in the order "
            f"{[adapter.model_id for adapter in candidates]}; A, B, C is what the "
            f"narrative names and what the timeline plots"
        )


# ----------------------------------------------------------------------------------
# Determinism (ruling 1)
# ----------------------------------------------------------------------------------


def _sample_twice(night: int, root: Path) -> list[tuple[Path, Path]]:
    """Run every side of ``night`` twice, into two directories, and pair the files."""
    goldenset = _goldenset()
    width = _showcase().require_showcase_concurrency(_showcase().SHOWCASE_CONCURRENCY)
    pairs: list[tuple[Path, Path]] = []
    directories = []
    for attempt in ("first", "second"):
        directory = root / attempt
        directory.mkdir(parents=True, exist_ok=True)
        directories.append(directory)
        for adapter in _every_adapter(night):
            run_goldenset(
                goldenset,
                adapter,
                out_dir=directory,
                n=DRAWS_PER_ITEM,
                concurrency=width,
            )
    for adapter in _every_adapter(night):
        pairs.append(
            (
                artifact_path_for(goldenset, adapter.model_id, directories[0]),
                artifact_path_for(goldenset, adapter.model_id, directories[1]),
            )
        )
    return pairs


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _differing_keys(left: Any, right: Any, key: str = "") -> set[str]:
    """Every leaf key name at which two parsed artifacts disagree."""
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return {key or "<root>"} | (set(left) ^ set(right))
        found: set[str] = set()
        for name in left:
            found |= _differing_keys(left[name], right[name], name)
        return found
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {key or "<root>"}
        found = set()
        for one, other in zip(left, right, strict=True):
            found |= _differing_keys(one, other, key)
        return found
    return set() if left == right else {key or "<root>"}


@pytest.mark.slow
def test_two_runs_of_the_same_night_agree_on_every_item_sample_output_and_error(
    tmp_path: Path,
) -> None:
    """C16's determinism contract, in the form ruling 1 leaves achievable.

    "Byte-identical" is not: ``RunHeader.created`` is ``utc_now()`` and
    ``Completion.duration`` is measured inside rigor's ``sample``. What a scripted
    adapter *can* promise is that the same night sampled twice produces the same
    answers to the same questions in the same order, for every side, and that is
    the whole of what the showcase claims when it says a stranger could re-derive
    it.
    """
    for first, second in _sample_twice(REVIEW_NIGHT, tmp_path):
        assert first.is_file() and second.is_file()
        projection = [
            [
                (
                    record.get("item_id"),
                    record.get("sample_index"),
                    record.get("output"),
                    record.get("error"),
                )
                for record in _records(path)
                if record.get("record") == "completion"
            ]
            for path in (first, second)
        ]
        assert len(projection[0]) == COMPLETIONS_PER_SIDE, (
            f"{first.name} holds {len(projection[0])} completions; "
            f"{ITEMS} items x {DRAWS_PER_ITEM} draws is {COMPLETIONS_PER_SIDE}"
        )
        assert projection[0] == projection[1], (
            f"{first.name} and {second.name} disagree about what the model said. "
            f"The showcase's central claim is that its numbers are re-derivable."
        )


@pytest.mark.slow
def test_created_and_duration_are_the_only_keys_that_differ_between_two_runs_of_a_night(
    tmp_path: Path,
) -> None:
    """The half of ruling 1 that is worth more than the projection above.

    A projection test pins the four fields it names and would not notice a fifth
    source of nondeterminism appearing later -- a seed printed into ``notes``, a
    path that varies, a token count sampled from somewhere. This asserts the
    complement: across two whole artifacts, the *only* keys anywhere whose values
    differ are the two that physically cannot be held, and both of them do differ,
    so the walk is known to have looked at something.
    """
    for first, second in _sample_twice(REVIEW_NIGHT, tmp_path):
        left, right = _records(first), _records(second)
        assert len(left) == len(right), (
            f"{first.name} has {len(left)} records and {second.name} has {len(right)}"
        )
        found: set[str] = set()
        for one, other in zip(left, right, strict=True):
            found |= _differing_keys(one, other)
        assert found == {"created", "duration"}, (
            f"{first.name} and {second.name} differ at {sorted(found)}. Exactly two "
            f"keys may: 'created' is utc_now() in the header and 'duration' is a "
            f"wall-clock measurement inside rigor's sample. Anything else is a third "
            f"source of nondeterminism, and it is in a document that promises none."
        )


# ----------------------------------------------------------------------------------
# The REVIEW band, checked before it is asserted
# ----------------------------------------------------------------------------------


def _review_band(n: int) -> tuple[int, ...]:
    """Passing counts at ``n`` that miss the floor while rigor calls them underpowered.

    That combination is ``explain_verdict`` rule 3, which is REVIEW. One fewer and
    rigor stops calling it underpowered, which is rule 2 and NO-GO; one more and
    the floor is cleared. Derived from ``assert_pass_rate`` -- rigor's own gate,
    the one ``comparison._pass_rate`` calls -- rather than from a reimplementation
    of Wilson: R13 records a derived version reaching the opposite conclusion to
    rigor's on the same input.
    """
    band = []
    for successes in range(n + 1):
        try:
            assert_pass_rate((successes, n), PASS_RATE_FLOOR, confidence=CONFIDENCE)
        except PassRateError as exc:
            stats = dict(exc.stats)
            if stats.get("underpowered"):
                band.append(successes)
    return tuple(band)


def test_the_review_band_at_this_sample_size_is_reachable_by_a_stateless_mapping() -> None:
    """R13.2's load-bearing claim, verified rather than taken on trust.

    R13.2 rules out the stateful adapter C16 asked for, on the ground that a plain
    ``Mapping`` can still land in REVIEW. That is only true if the band is wide
    enough to contain a whole multiple of ``n_per_item``: a mapping answers all
    five draws of an item identically, so it can only move the passing count in
    steps of five, and a band narrower than that would be unreachable however the
    adapter was scripted. R13's own measurement was taken at n=200 and it says in
    terms: "Verify the REVIEW band is reachable at the showcase's own n before
    building."

    At 480 completions the band is 432 to 442 inclusive, eleven wide, and holds
    435 and 440. The stateful adapter is genuinely not needed.
    """
    band = _review_band(COMPLETIONS_PER_SIDE)
    assert band, "no passing count at all resolves to REVIEW; the narrative is impossible"
    assert (band[0], band[-1]) == REVIEW_BAND, (
        f"the REVIEW band at n={COMPLETIONS_PER_SIDE} is {band[0]}..{band[-1]}, not "
        f"{REVIEW_BAND[0]}..{REVIEW_BAND[1]}. R13's arithmetic, and night 6's seed "
        f"with it, rests on this range."
    )
    assert tuple(range(band[0], band[-1] + 1)) == band, f"the band has a hole in it: {band}"
    reachable = [successes for successes in band if successes % DRAWS_PER_ITEM == 0]
    assert reachable, (
        f"the REVIEW band {band[0]}..{band[-1]} contains no multiple of "
        f"{DRAWS_PER_ITEM}. A Mapping fails in whole items, so REVIEW would be "
        f"unreachable without the per-draw state R13.2 rules out -- which R13 says "
        f"is a real finding that changes the chunk, not something to work around."
    )


# ----------------------------------------------------------------------------------
# The fourteen-night narrative
# ----------------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("night", [FIRST_GREEN_NIGHT, LAST_GREEN_NIGHT])
def test_the_first_and_last_green_nights_are_go_for_all_three_candidates(
    nights: Callable[[int], Night], night: int
) -> None:
    """Nights 1 to 13 are green. Only two of the thirteen are sampled -- the first
    and the one night 14 is measured against -- because four nights is already
    7,680 completions and the remaining eleven pin no property these do not."""
    driven = nights(night)
    verdicts = {letter: driven.verdict(letter) for letter in "abc"}
    assert verdicts == {"a": "GO", "b": "GO", "c": "GO"}, (
        f"night {night} is meant to be green for every candidate; got {verdicts}"
    )


@pytest.mark.slow
def test_night_six_puts_candidate_c_in_review_and_leaves_the_other_two_green(
    nights: Callable[[int], Night],
) -> None:
    driven = nights(REVIEW_NIGHT)
    verdicts = {letter: driven.verdict(letter) for letter in "abc"}
    assert verdicts == {"a": "GO", "b": "GO", "c": "REVIEW"}, (
        f"night {REVIEW_NIGHT} is the one earlier night that straddles the floor for "
        f"candidate C; got {verdicts}"
    )


@pytest.mark.slow
def test_night_sixs_review_is_the_straddled_floor_and_not_the_power_warning(
    nights: Callable[[int], Night],
) -> None:
    """R13: "rule 4 is a trap for a timeline -- a band that is REVIEW for power
    rather than for the floor reads as the same colour and is a different fact."

    Rule 3 is "missed the floor while underpowered": the interval straddles the
    bar, which is the story the showcase tells about night 6. Rule 4 is "not
    enough completions to have detected the configured effect", which is a
    statement about the *design* of the run and would be true of every night
    equally. They render identically. Only one of them is the narrative.
    """
    driven = nights(REVIEW_NIGHT)
    report = driven.comparisons[driven.candidate("c")]
    assert report.rule == 3, (
        f"night {REVIEW_NIGHT}'s REVIEW came from rule {report.rule} "
        f"({report.reason}). Rule 3 -- the floor missed while underpowered -- is the "
        f"one the document narrates; rule 4 is a property of the sample size and "
        f"would colour every night the same."
    )
    judge = report.judge(JUDGE_NAME)
    assert judge.regressed is False
    assert judge.floor_cleared is False
    assert judge.underpowered is True


@pytest.mark.slow
def test_night_sixs_review_asks_for_a_number_of_further_runs_a_reader_can_act_on(
    nights: Callable[[int], Night],
) -> None:
    """Why candidate C fails eight items on night 6 and not nine.

    Both are REVIEW, both are rule 3, and the difference shows up in exactly one
    place: the callout that says how many more runs would settle it. Eight failing
    items is 440/480 and ``runs_needed`` 931. Nine is 435/480 and 6,364 -- still
    honest, and it reads as a refusal dressed as arithmetic rather than as a number
    anybody is going to collect.

    That choice was argued at length in the showcase's docstring and pinned by
    nothing: ``_REVIEW_NIGHT_PLAN = (1, 2)`` could become ``(1, 3)`` and the whole
    suite stayed green, because the verdict, the rule and all four flags are the
    same on either side of it. The rendered number is the only observable, so the
    rendered number is what this asserts.
    """
    judge = nights(REVIEW_NIGHT).comparisons[nights(REVIEW_NIGHT).candidate("c")].judge(JUDGE_NAME)
    assert judge.candidate["successes"] == REVIEW_NIGHT_PASSES, (
        f"candidate C passed {judge.candidate['successes']} on night {REVIEW_NIGHT}, "
        f"not {REVIEW_NIGHT_PASSES}. Eight failing items of five draws below a "
        f"baseline of {COMPLETIONS_PER_SIDE} is the seed the callout below rests on."
    )
    assert judge.runs_needed == REVIEW_NIGHT_RUNS_NEEDED, (
        f"night {REVIEW_NIGHT}'s callout would print runs_needed={judge.runs_needed}. "
        f"{REVIEW_NIGHT_RUNS_NEEDED} is what the showcase chose eight failing items "
        f"over nine to get; one item further down prints {REVIEW_NIGHT_RUNS_REFUSED}, "
        f"which is a refusal dressed as arithmetic."
    )


@pytest.mark.slow
def test_candidate_c_lands_inside_the_review_band_at_a_whole_multiple_of_the_draws(
    nights: Callable[[int], Night],
) -> None:
    """The seed, checked against the arithmetic rather than against itself.

    A mapping fails whole items, so its passing count is a multiple of five; the
    band is eleven wide. Both facts have to hold at once for night 6 to be REVIEW
    by construction rather than by luck, and asserting the observed count sits
    inside the independently computed band is what turns "the implementer tuned a
    seed" into a checkable claim.
    """
    driven = nights(REVIEW_NIGHT)
    judge = driven.comparisons[driven.candidate("c")].judge(JUDGE_NAME)
    successes = judge.candidate["successes"]
    assert judge.candidate["n"] == COMPLETIONS_PER_SIDE
    assert REVIEW_BAND[0] <= successes <= REVIEW_BAND[1], (
        f"candidate C passed {successes} of {COMPLETIONS_PER_SIDE} on night "
        f"{REVIEW_NIGHT}, outside the REVIEW band {REVIEW_BAND[0]}..{REVIEW_BAND[1]}"
    )
    assert successes % DRAWS_PER_ITEM == 0, (
        f"candidate C passed {successes} completions, which is not a whole number of "
        f"items. A stateless Mapping cannot produce that: some draw of some item was "
        f"graded differently from its siblings, which is the per-draw state R13.2 "
        f"rules out."
    )


@pytest.mark.slow
def test_night_fourteen_is_no_go_for_candidate_b_and_go_for_the_other_two(
    nights: Callable[[int], Night],
) -> None:
    driven = nights(COLLAPSE_NIGHT)
    verdicts = {letter: driven.verdict(letter) for letter in "abc"}
    assert verdicts == {"a": "GO", "b": "NO-GO", "c": "GO"}, (
        f"night {COLLAPSE_NIGHT} is candidate B's refusal collapse and nobody else's; "
        f"got {verdicts}"
    )


@pytest.mark.slow
def test_night_fourteens_no_go_is_the_demonstrated_regression_and_not_the_missed_bar(
    nights: Callable[[int], Night],
) -> None:
    """The symmetry night 6 has and night 14 did not.

    Night 6 pins ``rule == 3`` and all four flags; night 14 pinned only the string
    "NO-GO". Rules 1 and 2 render the same colour and state different facts: rule 1
    is "a significant regression against the baseline", rule 2 is "the bar was
    missed on the evidence and more runs will not help". The showcase's night 14 is
    a regression -- a point release broke a capability -- and the document narrates
    it as one.

    No live mutant reaches rule 2 here: it needs at most 431 passing completions
    *and* p >= 0.05, and against a 445-passing baseline those are arithmetically
    incompatible. So this is latent rather than exploitable, and it costs one line.
    """
    driven = nights(COLLAPSE_NIGHT)
    report = driven.comparisons[driven.candidate("b")]
    assert report.rule == 1, (
        f"night {COLLAPSE_NIGHT}'s NO-GO came from rule {report.rule} "
        f"({report.reason}). Rule 1 is the demonstrated regression the collapse is; "
        f"rule 2 is the same colour and says more runs would not help."
    )
    assert report.judge(JUDGE_NAME).regressed is True, (
        f"night {COLLAPSE_NIGHT} is NO-GO without the regression flag set, so the "
        f"verdict is resting on something other than the collapse"
    )


@pytest.mark.slow
def test_every_comparison_parameter_but_the_candidate_model_is_unchanged_from_night_thirteen(
    nights: Callable[[int], Night],
) -> None:
    """Section 7.3's "every other parameter hash is unchanged between 13 and 14".

    This is the fact the parameter strip renders as exactly one row with
    ``changed=True``, and it is the entire argument the strip exists to make: the
    refusal collapse cannot be explained away as a different golden set, a
    different judge, a different threshold or a different number of draws, because
    every one of those hashes is the same on both nights.
    """
    before = nights(LAST_GREEN_NIGHT)
    after = nights(COLLAPSE_NIGHT)
    left = before.comparisons[before.candidate("b")]
    right = after.comparisons[after.candidate("b")]

    held = {
        "goldenset_hash": (left.goldenset_hash, right.goldenset_hash),
        "judges_hash": (left.judges_hash, right.judges_hash),
        "n_per_item": (left.n_per_item, right.n_per_item),
        "thresholds": (left.thresholds.to_dict(), right.thresholds.to_dict()),
        "baseline_model": (left.baseline_model, right.baseline_model),
    }
    moved = sorted(name for name, (one, other) in held.items() if one != other)
    assert not moved, (
        f"night 14 changed {', '.join(moved)} as well as the candidate. The strip "
        f"would show more than one changed row and the collapse could be blamed on "
        f"any of them."
    )
    assert left.candidate_model != right.candidate_model, (
        "candidate B carries the same model id on both nights, so the one row that "
        "must change does not"
    )


# ----------------------------------------------------------------------------------
# The collapse, dimension by dimension (rulings 2 and 3)
# ----------------------------------------------------------------------------------


def _refusal_cell(driven: Night) -> TagCount:
    return driven.dimensions()[driven.candidate("b")][REFUSAL_TAG]


@pytest.mark.slow
def test_night_fourteen_grades_the_same_refusal_completions_as_night_thirteen_and_passes_fewer(
    nights: Callable[[int], Night],
) -> None:
    """Section 7.3's blind-testable property, corrected (ruling 2).

    As written, 7.3 asks for "night 14's ``#refusal`` completions for candidate B
    are strictly fewer than night 13's". They are not fewer and must not be:
    everything is still sampled and everything is still graded, so the denominator
    is 85 on both nights. A collapse that shrank the denominator would be a
    *missing* dimension rather than a failing one, and the two look nothing alike
    to a reader. What drops is the numerator.
    """
    before = _refusal_cell(nights(LAST_GREEN_NIGHT))
    after = _refusal_cell(nights(COLLAPSE_NIGHT))
    assert before.n == after.n == REFUSAL_COMPLETIONS, (
        f"the #refusal dimension holds {before.n} completions on night 13 and "
        f"{after.n} on night 14; {len(_items_tagged(REFUSAL_TAG))} tagged items x "
        f"{DRAWS_PER_ITEM} draws is {REFUSAL_COMPLETIONS} on both"
    )
    assert before.passes == REFUSAL_COMPLETIONS, (
        f"candidate B passed {before.passes}/{before.n} on #refusal on night 13; the "
        f"thirteen green nights are green on every dimension, and a collapse from a "
        f"number that was already low is not the story the document tells"
    )
    assert after.passes < before.passes, (
        f"#refusal passed {after.passes}/{after.n} on night 14 against "
        f"{before.passes}/{before.n} on night 13, which is not a collapse"
    )


@pytest.mark.slow
def test_the_refusal_collapse_bottoms_out_at_five_of_eighty_five_because_one_item_borrows_the_tag(
    nights: Callable[[int], Night],
) -> None:
    """Ruling 3, with its reason pinned beside its number.

    Sixteen items are refusals; a seventeenth, ``synthetic-summarise-09``, is a
    summarisation task that happens to be unanswerable and carries ``refusal`` as
    a second tag. ``dimension_counts`` attributes an item to every tag it carries,
    so the dimension is 85 completions and the collapse -- which takes the sixteen
    -- leaves the seventeenth still passing. Five, not zero.

    Both halves are asserted because either alone is a bad test. "Five" without
    the reason is a magic number nobody could re-derive; the reason without the
    number would not notice a change that silently drove the dimension to zero,
    which is exactly the regression a two-tag golden set invites.
    """
    borrowed = _borrowers(REFUSAL_TAG)
    assert [item.id for item in borrowed] == ["synthetic-summarise-09"], (
        f"the #refusal dimension's borrowed items are "
        f"{[item.id for item in borrowed]}. The floor below is computed from that "
        f"list; C15's set is what defines it."
    )
    assert len(_items_primarily(REFUSAL_TAG)) == 16
    assert len(_items_tagged(REFUSAL_TAG)) * DRAWS_PER_ITEM == REFUSAL_COMPLETIONS

    after = _refusal_cell(nights(COLLAPSE_NIGHT))
    assert after.passes == len(borrowed) * DRAWS_PER_ITEM == REFUSAL_PASSES_AFTER_COLLAPSE, (
        f"#refusal passed {after.passes}/{after.n} on night 14. It should be exactly "
        f"the borrowed item's {len(borrowed) * DRAWS_PER_ITEM} draws: zero would mean "
        f"the collapse had swallowed a summarisation task with it, and anything "
        f"larger means some primary refusal item survived."
    )


@pytest.mark.slow
def test_the_collapse_moves_the_refusal_dimension_and_one_item_of_summarisation_and_nothing_else(
    nights: Callable[[int], Night],
) -> None:
    """The two-tag arithmetic in the other direction, and the four tags it misses.

    ``synthetic-refuse-04`` is a refusal phrased as a summary request, so it
    carries ``summarisation`` too and the collapse costs that dimension exactly one
    item's five draws. That is not a defect -- it is the golden set's own overlap
    showing up honestly in the matrix -- but a reader looking at night 14 sees two
    dimensions move, and a test that expected only one would be red against
    correct code. The four dimensions that share no item with ``#refusal`` must be
    identical to the last decimal, which is what makes the strip's single changed
    row an argument rather than a claim.
    """
    shared = _borrowers(SUMMARISATION_TAG)
    assert [item.id for item in shared] == ["synthetic-refuse-04"], (
        f"#summarisation's borrowed items are {[item.id for item in shared]}; the "
        f"expected movement below is computed from that list"
    )

    before = nights(LAST_GREEN_NIGHT)
    after = nights(COLLAPSE_NIGHT)
    left = before.dimensions()[before.candidate("b")]
    right = after.dimensions()[after.candidate("b")]
    assert set(left) == set(right), "the two nights report different dimensions"

    untouched = sorted(set(left) - {REFUSAL_TAG, SUMMARISATION_TAG})
    assert len(untouched) == 4, f"expected four untouched dimensions, found {untouched}"
    for tag in untouched:
        assert left[tag] == right[tag], (
            f"#{tag} moved from {left[tag]} to {right[tag]} between nights 13 and 14. "
            f"No item outside #refusal was rescripted, so no other dimension can."
        )

    assert right[SUMMARISATION_TAG].n == left[SUMMARISATION_TAG].n
    lost = left[SUMMARISATION_TAG].passes - right[SUMMARISATION_TAG].passes
    assert lost == len(shared) * DRAWS_PER_ITEM, (
        f"#summarisation lost {lost} passing completions on night 14; the collapse "
        f"reaches it through {[item.id for item in shared]} alone, which is "
        f"{len(shared) * DRAWS_PER_ITEM} draws"
    )


# ----------------------------------------------------------------------------------
# The thirteen green nights are green in every cell, not merely in every verdict
# ----------------------------------------------------------------------------------


def test_every_capability_is_the_same_seventeen_items_so_the_floor_can_be_one_number() -> None:
    """The denominator the floor test is stated against, pinned before it is used.

    Ninety-six items and six borrowed tags is a hundred and two tag memberships
    over six capabilities. If C15's set ever stops being balanced this fails here,
    with the sizes printed, rather than as a floor breach on one night that reads
    like a schedule defect.
    """
    sizes = _tag_sizes()
    assert len(sizes) == DIMENSIONS, f"expected {DIMENSIONS} capabilities, found {sizes}"
    assert set(sizes.values()) == {DIMENSION_ITEMS}, (
        f"the capabilities are not all {DIMENSION_ITEMS} items: {sizes}. The floor "
        f"below is one number because every cell has one denominator."
    )
    assert DIMENSION_COMPLETIONS == REFUSAL_COMPLETIONS


@pytest.mark.parametrize("night", GREEN_NIGHTS)
def test_no_capability_on_a_green_night_falls_below_the_floor_for_any_of_the_four_models(
    night: int,
) -> None:
    """The assertion ``_ITEM_STRIDE`` never had, and the one its docstring asks for.

    The stride's stated job is that "a night's damage is spread across four or five
    capabilities and every cell stays green until the one that is supposed to fail
    does". Nothing checked it. Setting the stride to 1 keeps all fourteen verdicts,
    all four model ids, the REVIEW, the NO-GO and the 5/85 collapse exactly as they
    are, and drops night 4's baseline to 50/85 on ``#summarisation`` -- because
    consecutive ids in a pool laid out in slice order all live in one capability.
    The report would then narrate fourteen collapses and call the last one an event.

    A floor over the whole matrix rather than a table of expected cells: this goes
    red on a night whose failures bunch, and stays green through any retune of the
    schedule that keeps them spread, which is the property and not its current
    numbers.
    """
    matrix = _projected_dimensions(night)
    assert len(matrix) == 4, f"night {night} has {len(matrix)} sides, not four"
    breaches = {
        (model, tag): cell
        for model, cells in matrix.items()
        for tag, cell in cells.items()
        if cell.passes < GREEN_NIGHT_DIMENSION_FLOOR
    }
    assert not breaches, (
        f"night {night} is a green night and these cells are below "
        f"{GREEN_NIGHT_DIMENSION_FLOOR}/{DIMENSION_COMPLETIONS}: "
        f"{ {key: f'{cell.passes}/{cell.n}' for key, cell in breaches.items()} }. A "
        f"capability that drops this far on a night with nothing wrong with it makes "
        f"night 14's collapse read as the fifteenth in a row rather than the first."
    )
    for model, cells in matrix.items():
        for tag, cell in cells.items():
            assert cell.n == DIMENSION_COMPLETIONS, (
                f"night {night}, {model}, #{tag} has {cell.n} completions, not "
                f"{DIMENSION_COMPLETIONS}; every item is answered on every night"
            )


def test_the_floor_is_tight_enough_that_some_cell_actually_reaches_it() -> None:
    """A floor no cell approaches is a floor that would survive being wrong.

    70/85 is the measured minimum over the whole green matrix, not a round number
    chosen with room to spare: at least one cell sits exactly on it. If a retune
    lifts the true minimum this fails, and the right response is to raise the
    constant to whatever the new minimum is rather than to leave a floor that four
    failing items in one cell could pass under.
    """
    lowest = min(
        cell.passes
        for night in GREEN_NIGHTS
        for cells in _projected_dimensions(night).values()
        for cell in cells.values()
    )
    assert lowest == GREEN_NIGHT_DIMENSION_FLOOR, (
        f"the lowest green-night cell is {lowest}/{DIMENSION_COMPLETIONS} and the "
        f"floor is pinned at {GREEN_NIGHT_DIMENSION_FLOOR}. They must be the same "
        f"number: a floor below the true minimum is slack the stride can be broken in."
    )


@pytest.mark.slow
@pytest.mark.parametrize("night", [REVIEW_NIGHT, LAST_GREEN_NIGHT, COLLAPSE_NIGHT])
def test_the_projected_dimension_matrix_agrees_with_the_pipelines(
    nights: Callable[[int], Night], night: int
) -> None:
    """What licenses the cheap matrix above to stand in for the expensive one.

    The projection asks the showcase's own judge what it would say about each
    adapter's answer and multiplies by the draws; the pipeline samples every draw,
    judges each one and counts verdicts. They must agree cell for cell, including
    on night 14, where one cell is 5/85. If they ever stop agreeing the projection
    is the thing that is wrong, and every floor assertion above is measuring a
    model of the showcase rather than the showcase.
    """
    driven = nights(night).dimensions()
    projected = _projected_dimensions(night)
    assert set(driven) == set(projected), (
        f"night {night}: the pipeline reports sides {sorted(driven)} and the "
        f"projection {sorted(projected)}"
    )
    for model in sorted(driven):
        assert driven[model] == projected[model], (
            f"night {night}, {model}: the pipeline says {driven[model]} and the "
            f"projection says {projected[model]}"
        )
