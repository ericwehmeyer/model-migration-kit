"""Acceptance tests for :mod:`model_migration_kit.runner`.

Written against the session brief as amended by AMENDMENT 1. Every expectation
comes from the contract, from hand derivation, or from a tool outside
``model_migration_kit`` -- never from running the code under test. Two hashing rules are
re-implemented locally on purpose, so that the expected value and the observed
value have independent provenance:

* ``_content_hash`` implements amendment section A -- canonical JSON per item,
  items sorted by id, joined with a single newline, sha256 with CRLF->LF -- using
  stdlib ``json`` and ``hashlib`` only. This is what keys artifact filenames.
* ``_sha256_lf`` is the provenance (``file_hash``) rule: sha256 of the file's raw
  bytes with CRLF normalised to LF. One test cross-checks it against the literal
  the brief derived with PowerShell ``Get-FileHash``, so the helper the other
  expectations lean on is itself grounded outside this package.

Everything runs offline against ``opik_rigor.FakeAdapter`` or a few-line adapter
written here. No sleep exceeds 50ms and nothing draws from an unseeded RNG: the
one stochastic-looking component, the flaky adapter in the dogfooding test, fails
on an arithmetic subset of prompts and so has no RNG to seed.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from opik_rigor import EvidenceLog, FakeAdapter, PassRateError, assert_pass_rate, sample_of

from model_migration_kit.contracts import ARTIFACT_SCHEMA_VERSION
from model_migration_kit.errors import ArtifactError
from model_migration_kit.goldenset import GoldenSet
from model_migration_kit.runner import RunArtifact, artifact_path_for, run_goldenset

#: A one-item golden set used wherever a filename is asserted verbatim.
KNOWN_SET_BYTES = b'{"id":"a","input":"x"}\n'
#: The brief's PowerShell `Get-FileHash -Algorithm SHA256` value for those bytes.
#: Under amendment A this is the *provenance* hash and gates nothing.
KNOWN_SET_FILE_HASH = "1bcdca7f33c2173558c98000ffc3fb22a6b4cdaa2d1f9f9ff8de13edc6fff2ec"
#: The content hash of the same set: sha256 of b'{"id":"a","input":"x"}' (no
#: trailing newline, one item, so no join). Derived offline with the system
#: Python's hashlib, outside this repo's virtualenv and without model_migration_kit.
KNOWN_SET_HASH = "a0cce966940a34408bf00877694d404f8ffc017254044878debd200720c8617b"
KNOWN_SET_HASH_16 = "a0cce966940a3440"


# --------------------------------------------------------------------------- #
# Helpers: fixtures on disk, hand-written artifact records, tiny adapters.
# --------------------------------------------------------------------------- #


def _sha256_lf(data: bytes) -> str:
    """The provenance (``file_hash``) rule, with stdlib hashlib alone."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _content_hash(items) -> str:
    """Amendment section A's content-hash rule, with stdlib json/hashlib alone."""
    blobs = [
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        for item in sorted(items, key=lambda item: item["id"])
    ]
    return _sha256_lf(b"\n".join(blobs))


def _item_dicts(ids, *, prefix="q") -> list[dict]:
    return [{"id": item_id, "input": f"{prefix}-{item_id}"} for item_id in ids]


def _goldenset_bytes(ids, *, prefix="q") -> bytes:
    lines = [json.dumps(item) for item in _item_dicts(ids, prefix=prefix)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_goldenset(tmp_path, ids, *, name="set.jsonl", prefix="q") -> GoldenSet:
    path = tmp_path / name
    path.write_bytes(_goldenset_bytes(ids, prefix=prefix))
    return GoldenSet.load(path)


def _echo(prompt: str) -> str:
    return f"answer::{prompt}"


def _echo_adapter(model_id: str = "fake-echo-v1", **kwargs) -> FakeAdapter:
    return FakeAdapter(model_id=model_id, responses=_echo, **kwargs)


class _CountingAdapter:
    """Adapter proxy that records every prompt that actually reached a model."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.prompts: list[str] = []

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._inner.complete(prompt)


class _RealishAdapter:
    """Stands in for a provider adapter: anything whose class is not FakeAdapter."""

    def __init__(self, model_id: str = "m1") -> None:
        self.model_id = model_id
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return f"real::{prompt}"


class _NonStringAdapter:
    """An adapter that breaks the provider protocol by returning a non-string."""

    model_id = "protocol-breaker-v1"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str):
        self.prompts.append(prompt)
        return 42


class _NamelessAdapter:
    """An adapter carrying no usable model string."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "unattributable"


def _header_line(**overrides) -> str:
    """A header record as an *old* writer wrote it: no notes, so no item count."""
    record = {
        "record": "header",
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": "m1",
        "goldenset_hash": "a" * 64,
        "goldenset_path": "set.jsonl",
        "n_per_item": 2,
        "created": "2026-01-01T00:00:00.000000+00:00",
        "adapter": "FakeAdapter",
        "notes": {},
    }
    record.update(overrides)
    return json.dumps(record)


def _completion_line(item_id: str, sample_index: int, **overrides) -> str:
    record = {
        "record": "completion",
        "item_id": item_id,
        "sample_index": sample_index,
        "output": "ok",
        "duration": 0.0,
        "error": None,
        "error_type": None,
        "tokens_in": None,
        "tokens_out": None,
    }
    record.update(overrides)
    return json.dumps(record)


def _write_lines(path, lines) -> None:
    path.write_bytes(("".join(f"{line}\n" for line in lines)).encode("utf-8"))


def _physical_lines(path) -> list[bytes]:
    """Complete (newline-terminated) lines currently in the artifact."""
    data = path.read_bytes()
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
    return parts


def _drop_last_lines(path, count: int) -> None:
    kept = _physical_lines(path)[:-count]
    path.write_bytes(b"".join(line + b"\n" for line in kept))


def _tear_last_line(path, *, keep_bytes: int = 20) -> None:
    """Leave the final record as an unterminated fragment, as a kill mid-write does."""
    lines = _physical_lines(path)
    fragment = lines[-1][:keep_bytes]
    assert fragment and not fragment.endswith(b"\n")
    path.write_bytes(b"".join(line + b"\n" for line in lines[:-1]) + fragment)


def _pairs(artifact: RunArtifact) -> list[tuple[str, int]]:
    return [(c.item_id, c.sample_index) for c in artifact.completions]


def _payloads(log: EvidenceLog, event_type: str) -> list[dict]:
    return [record.payload for record in log.read() if record.event_type == event_type]


# --------------------------------------------------------------------------- #


class TestArtifactPath:
    """Pins that a filename is keyed by the model and the set's *content* hash."""

    @pytest.fixture
    def known_set(self, tmp_path) -> GoldenSet:
        path = tmp_path / "known.jsonl"
        path.write_bytes(KNOWN_SET_BYTES)
        return GoldenSet.load(path)

    def test_the_two_hashes_are_different_things(self, known_set):
        # Grounds both local helpers: the file hash against the brief's PowerShell
        # literal, the content hash against the offline stdlib derivation.
        assert _sha256_lf(KNOWN_SET_BYTES) == KNOWN_SET_FILE_HASH
        assert _content_hash([{"id": "a", "input": "x"}]) == KNOWN_SET_HASH
        assert known_set.hash == KNOWN_SET_HASH
        assert known_set.file_hash == KNOWN_SET_FILE_HASH

    def test_plain_model_id_keeps_its_characters(self, known_set, tmp_path):
        out = tmp_path / "runs"
        assert artifact_path_for(known_set, "gpt-4o-mini", out) == (
            out / f"gpt-4o-mini__{KNOWN_SET_HASH_16}.jsonl"
        )

    def test_slash_becomes_a_dash(self, known_set, tmp_path):
        assert artifact_path_for(known_set, "openai/gpt-4o", tmp_path) == (
            tmp_path / f"openai-gpt-4o__{KNOWN_SET_HASH_16}.jsonl"
        )

    def test_colon_and_at_become_dashes(self, known_set, tmp_path):
        assert artifact_path_for(known_set, "claude:sonnet@2025", tmp_path) == (
            tmp_path / f"claude-sonnet-2025__{KNOWN_SET_HASH_16}.jsonl"
        )

    def test_two_goldensets_land_in_two_files_for_one_model(self, known_set, tmp_path):
        other = _write_goldenset(tmp_path, ["a", "b"], name="other.jsonl")
        assert artifact_path_for(known_set, "m", tmp_path) != artifact_path_for(
            other, "m", tmp_path
        )

    def test_two_models_land_in_two_files_for_one_set(self, known_set, tmp_path):
        assert artifact_path_for(known_set, "model-a", tmp_path) != artifact_path_for(
            known_set, "model-b", tmp_path
        )


class TestSampling:
    """Pins behaviour 1, 2, 6 and 14: n draws per item, the prompt, and the shape."""

    def test_k_items_at_n_yields_k_times_n_completions(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=4
        )
        assert len(artifact.completions) == 12
        assert artifact.counts() == {"i1": 4, "i2": 4, "i3": 4}

    def test_sample_indices_are_exactly_range_n(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=4
        )
        for item_id in ("i1", "i2", "i3"):
            indices = [c.sample_index for c in artifact.completions_for(item_id)]
            assert sorted(indices) == [0, 1, 2, 3]

    def test_prompt_is_the_item_input(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        adapter = _CountingAdapter(_echo_adapter())
        run_goldenset(goldenset, adapter, artifact=tmp_path / "run.jsonl", n=2)
        assert sorted(adapter.prompts) == ["q-i1", "q-i1", "q-i2", "q-i2"]

    def test_prompt_builder_replaces_the_prompt(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        adapter = _CountingAdapter(_echo_adapter())
        run_goldenset(
            goldenset,
            adapter,
            artifact=tmp_path / "run.jsonl",
            n=1,
            prompt_builder=lambda item: f"<<{item.id}>>",
        )
        assert adapter.prompts == ["<<i1>>", "<<i2>>"]

    def test_successful_completion_carries_the_adapter_string(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2
        )
        for completion in artifact.completions:
            assert completion.output == "answer::q-i1"
            assert completion.error is None
            assert completion.error_type is None
            assert completion.ok is True
            assert completion.duration >= 0.0
        assert artifact.failures() == ()

    def test_stats_reports_the_run_in_full(self, tmp_path):
        ids = ["i1", "i2", "i3"]
        goldenset = _write_goldenset(tmp_path, ids)
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2
        )
        assert artifact.stats() == {
            "model_id": "fake-echo-v1",
            # Independently derived from the fixture items with stdlib json/hashlib.
            "goldenset_hash": _content_hash(_item_dicts(ids)),
            "n_per_item": 2,
            "adapters": ["FakeAdapter"],
            "items": 3,
            "items_expected": 3,
            "completions": 6,
            "failures": 0,
            "parts": 1,
        }
        assert artifact.is_complete(goldenset) is True

    def test_concurrency_changes_nothing_observable(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        serial = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "serial.jsonl", n=4
        )
        parallel = run_goldenset(
            goldenset,
            _echo_adapter(),
            artifact=tmp_path / "parallel.jsonl",
            n=4,
            concurrency=3,
        )
        assert parallel.counts() == serial.counts()
        assert parallel.parts == 1
        assert sorted(_pairs(parallel)) == sorted(_pairs(serial))
        assert len(set(_pairs(parallel))) == 12
        for item_id in ("i1", "i2", "i3"):
            assert sorted(c.sample_index for c in parallel.completions_for(item_id)) == [
                0,
                1,
                2,
                3,
            ]


class TestFailuresAreKept:
    """Pins behaviours 3, 4 and 5: no failure mode is allowed to vanish."""

    def test_raising_adapter_still_yields_n_completions(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        adapter = FakeAdapter(
            model_id="fake-broken-v1",
            responses=["never used"],
            fail_with=RuntimeError("boom"),
        )
        artifact = run_goldenset(goldenset, adapter, artifact=tmp_path / "run.jsonl", n=3)
        assert artifact.counts() == {"i1": 3, "i2": 3}
        assert len(artifact.failures()) == 6
        for completion in artifact.completions:
            assert completion.output is None
            assert completion.error == "boom"
            assert completion.error_type == "RuntimeError"
            assert completion.ok is False

    def test_timeout_is_recorded_as_a_failure_not_a_crash(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        adapter = _echo_adapter(latency=0.05)
        artifact = run_goldenset(
            goldenset, adapter, artifact=tmp_path / "run.jsonl", n=2, timeout=0.01
        )
        assert artifact.counts() == {"i1": 2}
        for completion in artifact.completions:
            assert completion.error_type == "SampleTimeout"
            assert completion.output is None
            assert completion.ok is False

    def test_non_string_output_is_a_protocol_failure(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        adapter = _NonStringAdapter()
        artifact = run_goldenset(goldenset, adapter, artifact=tmp_path / "run.jsonl", n=2)
        assert artifact.counts() == {"i1": 2}
        for completion in artifact.completions:
            assert completion.error_type == "AdapterProtocolError"
            assert completion.output is None
            assert completion.ok is False


class TestResume:
    """Pins behaviours 7, 8, 9 and amendment G: resuming duplicates no work, and
    re-running a finished run records nothing at all."""

    def _partial_run(self, tmp_path, path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        return goldenset

    def test_resume_after_whole_lines_are_lost(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        _drop_last_lines(path, 3)  # header + i1 x2 + i2 x1 survive

        artifact = run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}
        assert len(set(_pairs(artifact))) == 6
        assert artifact.parts == 2

    def test_resume_after_a_partial_line_is_lost(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        _drop_last_lines(path, 2)
        _tear_last_line(path)  # header + i1 x2 + i2 x1(torn) on disk

        artifact = run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}
        assert len(set(_pairs(artifact))) == 6
        assert artifact.parts == 2

    def test_completed_items_are_not_sampled_again(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        _drop_last_lines(path, 3)  # i1 is complete, i2 has one draw, i3 has none

        adapter = _CountingAdapter(_echo_adapter())
        artifact = run_goldenset(goldenset, adapter, artifact=path, n=2)
        assert adapter.prompts == ["q-i2", "q-i3", "q-i3"]
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}

    def test_load_drops_a_torn_final_line(self, tmp_path):
        path = tmp_path / "run.jsonl"
        self._partial_run(tmp_path, path)
        _tear_last_line(path)

        artifact = RunArtifact.load(path)
        assert len(artifact.completions) == 5
        assert artifact.parts == 1
        assert len(set(_pairs(artifact))) == 5

    def test_fresh_starts_over(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        artifact = run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2, fresh=True)
        assert artifact.parts == 1
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}
        assert len(set(_pairs(artifact))) == 6

    def test_a_partial_artifact_is_not_complete(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        _drop_last_lines(path, 3)
        assert RunArtifact.load(path).is_complete(goldenset) is False

    def test_re_running_a_complete_run_is_a_no_op(self, tmp_path):
        """Amendment G: a header is written only when there is work to do."""
        path = tmp_path / "run.jsonl"
        goldenset = self._partial_run(tmp_path, path)
        before = path.read_bytes()

        adapter = _CountingAdapter(_echo_adapter())
        artifact = run_goldenset(goldenset, adapter, artifact=path, n=2)
        assert adapter.prompts == []
        assert artifact.parts == 1
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}
        assert artifact.adapters == ("FakeAdapter",)
        assert path.read_bytes() == before


class TestCompleteness:
    """Pins amendment D: an artifact can be judged complete without the set."""

    def test_the_header_records_the_set_it_ran(self, tmp_path):
        ids = ["i1", "i2", "i3"]
        goldenset = _write_goldenset(tmp_path, ids)
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2
        )
        assert artifact.items_expected == 3
        assert artifact.header.notes["goldenset_items"] == 3
        # Provenance hash, derived here from the fixture bytes with stdlib hashlib.
        assert artifact.header.notes["goldenset_file_hash"] == _sha256_lf(_goldenset_bytes(ids))

    def test_a_complete_artifact_knows_it_without_the_set(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2
        )
        assert artifact.is_complete() is True

    def test_a_truncated_artifact_is_incomplete_without_the_set(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        _drop_last_lines(path, 3)  # i3 never ran; the artifact still loads cleanly
        assert RunArtifact.load(path).is_complete() is False

    def test_an_item_short_of_its_draws_is_incomplete_without_the_set(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        _drop_last_lines(path, 1)  # every item present, one draw short
        assert RunArtifact.load(path).is_complete() is False

    def test_an_old_header_has_no_recorded_item_count(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(path, [_header_line(), _completion_line("i1", 0)])
        assert RunArtifact.load(path).items_expected is None

    def test_an_old_header_cannot_be_judged_without_the_set(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(path, [_header_line(), _completion_line("i1", 0)])
        with pytest.raises(ArtifactError, match="does not record how many items"):
            RunArtifact.load(path).is_complete()


class TestAdapterIdentity:
    """Pins amendments E and K: every adapter that contributed is disclosed, and
    the one fake/real crossing that can be identified with certainty is refused."""

    def test_a_clean_run_lists_one_adapter(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        artifact = run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2
        )
        assert artifact.adapters == ("FakeAdapter",)
        assert artifact.stats()["adapters"] == ["FakeAdapter"]

    def test_adapters_are_listed_once_in_first_appearance_order(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)

        _drop_last_lines(path, 3)
        run_goldenset(goldenset, _CountingAdapter(_echo_adapter()), artifact=path, n=2)

        _drop_last_lines(path, 3)
        artifact = run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)

        assert artifact.parts == 3
        assert artifact.adapters == ("FakeAdapter", "_CountingAdapter")
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}

    def test_wrapping_an_adapter_in_a_proxy_is_allowed(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _RealishAdapter("m1"), artifact=path, n=2)
        _drop_last_lines(path, 3)

        artifact = run_goldenset(
            goldenset, _CountingAdapter(_RealishAdapter("m1")), artifact=path, n=2
        )
        assert artifact.adapters == ("_RealishAdapter", "_CountingAdapter")
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}

    def test_resuming_a_real_run_with_the_fake_is_refused(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _RealishAdapter("m1"), artifact=path, n=2)
        _drop_last_lines(path, 3)

        with pytest.raises(ArtifactError, match="scripted FakeAdapter"):
            run_goldenset(goldenset, _echo_adapter("m1"), artifact=path, n=2)

    def test_resuming_a_fake_run_with_a_real_one_is_allowed(self, tmp_path):
        path = tmp_path / "run.jsonl"
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        run_goldenset(goldenset, _echo_adapter("m1"), artifact=path, n=2)
        _drop_last_lines(path, 3)

        artifact = run_goldenset(goldenset, _RealishAdapter("m1"), artifact=path, n=2)
        assert artifact.adapters == ("FakeAdapter", "_RealishAdapter")
        assert artifact.counts() == {"i1": 2, "i2": 2, "i3": 2}


class TestResumeMismatch:
    """Pins behaviour 10 and amendment H: another run's artifact is never merged
    into, and `fresh=True` never deletes it either."""

    def test_a_different_model_is_refused(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        path = tmp_path / "run.jsonl"
        run_goldenset(goldenset, _echo_adapter("model-a"), artifact=path, n=2)
        with pytest.raises(ArtifactError, match="holds a run of"):
            run_goldenset(goldenset, _echo_adapter("model-b"), artifact=path, n=2)

    def test_a_different_goldenset_is_refused(self, tmp_path):
        first = _write_goldenset(tmp_path, ["i1"], name="first.jsonl")
        second = _write_goldenset(tmp_path, ["i1", "i2"], name="second.jsonl")
        path = tmp_path / "run.jsonl"
        run_goldenset(first, _echo_adapter(), artifact=path, n=2)
        with pytest.raises(ArtifactError, match="The set changed since"):
            run_goldenset(second, _echo_adapter(), artifact=path, n=2)

    def test_a_different_n_is_refused(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        path = tmp_path / "run.jsonl"
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        with pytest.raises(ArtifactError, match="Mixing sample sizes"):
            run_goldenset(goldenset, _echo_adapter(), artifact=path, n=3)

    def test_fresh_will_not_delete_another_models_artifact(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        path = tmp_path / "run.jsonl"
        run_goldenset(goldenset, _echo_adapter("model-a"), artifact=path, n=2)
        with pytest.raises(ArtifactError, match="holds a run of"):
            run_goldenset(goldenset, _echo_adapter("model-b"), artifact=path, n=2, fresh=True)

        survivor = RunArtifact.load(path)
        assert survivor.header.model_id == "model-a"
        assert survivor.counts() == {"i1": 2}

    def test_fresh_will_not_delete_another_goldensets_artifact(self, tmp_path):
        first = _write_goldenset(tmp_path, ["i1"], name="first.jsonl")
        second = _write_goldenset(tmp_path, ["i1", "i2"], name="second.jsonl")
        path = tmp_path / "run.jsonl"
        run_goldenset(first, _echo_adapter(), artifact=path, n=2)
        with pytest.raises(ArtifactError, match="The set changed since"):
            run_goldenset(second, _echo_adapter(), artifact=path, n=2, fresh=True)

        survivor = RunArtifact.load(path)
        assert survivor.header.goldenset_hash == _content_hash(_item_dicts(["i1"]))
        assert survivor.counts() == {"i1": 2}


class TestLoadRefusesCorruption:
    """Pins behaviour 11 and amendment J: only a torn final line is tolerated,
    and a zero-byte file is a reader's error but a writer's clean slate."""

    def test_absent_file(self, tmp_path):
        with pytest.raises(ArtifactError, match="no run artifact at"):
            RunArtifact.load(tmp_path / "missing.jsonl")

    def test_zero_byte_file(self, tmp_path):
        path = tmp_path / "run.jsonl"
        path.write_bytes(b"")
        with pytest.raises(ArtifactError, match="has no header record"):
            RunArtifact.load(path)

    def test_no_header_record(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(path, [_completion_line("i1", 0)])
        with pytest.raises(ArtifactError, match="has no header record"):
            RunArtifact.load(path)

    def test_unknown_record_type(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(path, [_header_line(), json.dumps({"record": "bogus"})])
        with pytest.raises(ArtifactError, match="unknown record type"):
            RunArtifact.load(path)

    def test_duplicate_item_and_sample_index(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(
            path,
            [_header_line(), _completion_line("i1", 0), _completion_line("i1", 0)],
        )
        with pytest.raises(ArtifactError, match="sample 0 twice"):
            RunArtifact.load(path)

    def test_future_schema_version(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(
            path,
            [
                _header_line(schema_version=ARTIFACT_SCHEMA_VERSION + 1),
                _completion_line("i1", 0),
            ],
        )
        with pytest.raises(ArtifactError, match="Refusing"):
            RunArtifact.load(path)

    def test_headers_disagreeing_on_identity(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(
            path,
            [
                _header_line(model_id="model-a"),
                _completion_line("i1", 0),
                _header_line(model_id="model-b"),
            ],
        )
        with pytest.raises(ArtifactError, match="disagree on model_id"):
            RunArtifact.load(path)

    def test_malformed_line_in_the_middle(self, tmp_path):
        path = tmp_path / "run.jsonl"
        _write_lines(path, [_header_line(), "{not json", _completion_line("i1", 0)])
        with pytest.raises(ArtifactError, match="malformed record at line 2"):
            RunArtifact.load(path)

    def test_a_zero_byte_artifact_is_startable(self, tmp_path):
        """Amendment J: the writer may replace what the reader must refuse."""
        path = tmp_path / "run.jsonl"
        path.write_bytes(b"")
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        artifact = run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        assert artifact.parts == 1
        assert artifact.counts() == {"i1": 2, "i2": 2}


class TestArgumentValidation:
    """Pins behaviour 12 and amendment I: bad arguments and an unattributable
    model string are refused before a model is ever called."""

    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"n": 0}, "n must be an integer"),
            ({"n": -1}, "n must be an integer"),
            ({"n": True}, "n must be an integer"),
            ({"n": 2.5}, "n must be an integer"),
            ({"concurrency": 0}, "concurrency must be an integer"),
            ({"timeout": 0}, "timeout must be"),
            ({"timeout": -1}, "timeout must be"),
        ],
        ids=["n0", "n-1", "nTrue", "n2.5", "concurrency0", "timeout0", "timeout-1"],
    )
    def test_rejected_before_sampling(self, tmp_path, kwargs, fragment):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        adapter = _CountingAdapter(_echo_adapter())
        out_dir = tmp_path / "runs"
        with pytest.raises(ValueError, match=fragment):
            run_goldenset(goldenset, adapter, out_dir=out_dir, **kwargs)
        assert adapter.prompts == []
        assert not out_dir.exists()

    @pytest.mark.parametrize("model_id", ["", "   "], ids=["empty", "whitespace"])
    def test_an_empty_model_id_is_rejected(self, tmp_path, model_id):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        adapter = _NamelessAdapter(model_id)
        out_dir = tmp_path / "runs"
        with pytest.raises(ValueError, match="model_id is empty"):
            run_goldenset(goldenset, adapter, out_dir=out_dir, n=2)
        assert adapter.prompts == []
        assert not out_dir.exists()


class TestEvidence:
    """Pins behaviour 13 and amendment F: the run narrates itself into rigor's
    evidence log, one record per completion and not merely per item."""

    def test_a_clean_run_writes_one_record_per_completion_and_per_item(self, tmp_path):
        ids = ["i1", "i2", "i3"]
        goldenset = _write_goldenset(tmp_path, ids)
        log = EvidenceLog(tmp_path / "evidence.jsonl")
        path = tmp_path / "run.jsonl"
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2, evidence=log)

        types = [record.event_type for record in log.read()]
        assert types.count("migkit.run_started") == 1
        assert types.count("migkit.completion") == 6
        assert types.count("migkit.item_completed") == 3
        assert types.count("migkit.run_completed") == 1
        assert types.count("sample.completed") == 3

        started = log.last("migkit.run_started").payload
        assert started["model_id"] == "fake-echo-v1"
        assert started["goldenset_hash"] == _content_hash(_item_dicts(ids))
        assert started["items"] == 3
        assert started["n_per_item"] == 2
        assert started["artifact"] == str(path)
        assert started["resumed_from"] == 0

    def test_every_completion_record_carries_the_model_and_no_output(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2"])
        log = EvidenceLog(tmp_path / "evidence.jsonl")
        run_goldenset(
            goldenset, _echo_adapter(), artifact=tmp_path / "run.jsonl", n=2, evidence=log
        )

        payloads = _payloads(log, "migkit.completion")
        assert len(payloads) == 4
        assert {(p["item_id"], p["sample_index"]) for p in payloads} == {
            ("i1", 0),
            ("i1", 1),
            ("i2", 0),
            ("i2", 1),
        }
        for payload in payloads:
            assert payload["model_id"] == "fake-echo-v1"
            assert payload["ok"] is True
            assert payload["error"] is None
            assert payload["error_type"] is None
            # rigor's Adapter protocol exposes no usage data, so these are always
            # None: a recorded roadmap item, pinned rather than worked around.
            assert payload["tokens_in"] is None
            assert payload["tokens_out"] is None
            assert payload["duration"] >= 0.0
            assert "output" not in payload

    def test_a_failed_completion_records_its_error(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1"])
        adapter = FakeAdapter(
            model_id="fake-broken-v1",
            responses=["never used"],
            fail_with=RuntimeError("boom"),
        )
        log = EvidenceLog(tmp_path / "evidence.jsonl")
        run_goldenset(goldenset, adapter, artifact=tmp_path / "run.jsonl", n=2, evidence=log)

        payloads = _payloads(log, "migkit.completion")
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["model_id"] == "fake-broken-v1"
            assert payload["ok"] is False
            assert payload["error"] == "boom"
            assert payload["error_type"] == "RuntimeError"

    def test_a_resumed_run_reports_what_already_existed(self, tmp_path):
        goldenset = _write_goldenset(tmp_path, ["i1", "i2", "i3"])
        path = tmp_path / "run.jsonl"
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2)
        _drop_last_lines(path, 3)  # 3 completions survive: i1 x2, i2 x1

        log = EvidenceLog(tmp_path / "evidence.jsonl")
        run_goldenset(goldenset, _echo_adapter(), artifact=path, n=2, evidence=log)

        assert log.last("migkit.run_started").payload["resumed_from"] == 3
        types = [record.event_type for record in log.read()]
        assert types.count("migkit.item_completed") == 2
        assert types.count("migkit.completion") == 3


class TestDogfooding:
    """Gates this suite's own flaky-adapter run with opik-rigor's pass-rate gate.

    The adapter fails on an arithmetic subset of prompts rather than at random, so
    the sample is exactly reproducible and there is no RNG to seed. Two of twenty
    items fail, giving 18/20; the Wilson one-sided 95% lower bound for that is
    0.73834, computed by hand from ``z = 1.6448536`` (stdlib
    ``statistics.NormalDist().inv_cdf(0.95)``) and
    ``(p + z^2/2n -/+ z*sqrt(p(1-p)/n + z^2/4n^2)) / (1 + z^2/n)``.
    """

    @staticmethod
    def _flaky(prompt: str) -> str:
        # prompts are "q-i00" .. "q-i19"; fail on i03 and i13.
        if int(prompt[-2:]) % 10 == 3:
            raise RuntimeError("scripted provider failure")
        return f"ok::{prompt}"

    @pytest.fixture
    def outcomes(self, tmp_path) -> list[bool]:
        ids = [f"i{index:02d}" for index in range(20)]
        goldenset = _write_goldenset(tmp_path, ids)
        adapter = FakeAdapter(model_id="fake-flaky-v1", responses=self._flaky)
        artifact = run_goldenset(goldenset, adapter, artifact=tmp_path / "run.jsonl", n=1)
        return [artifact.completions_for(item_id)[0].ok for item_id in ids]

    def test_the_scripted_failure_rate_is_the_one_we_scripted(self, outcomes):
        assert len(outcomes) == 20
        assert sum(outcomes) == 18

    def test_run_success_rate_clears_a_rigor_gate(self, outcomes):
        result = sample_of(outcomes, outcome=bool)
        assert result.n == 20
        assert result.successes == 18

        report = assert_pass_rate(result, 0.60, confidence=0.95, label="runner-success-rate")
        assert report["passed"] is True
        assert report["successes"] == 18
        assert report["n"] == 20
        assert 0.7380 < report["lower_bound"] < 0.7387

    def test_the_same_sample_fails_a_bar_it_cannot_defend(self, outcomes):
        result = sample_of(outcomes, outcome=bool)
        with pytest.raises(PassRateError):
            assert_pass_rate(result, 0.95, confidence=0.95, label="runner-success-rate")
