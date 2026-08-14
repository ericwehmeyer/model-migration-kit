"""Acceptance tests for ``model_migration_kit.goldenset``.

Every expected value here comes from the session-1 contract brief and its
amendment 1, from hand derivation, or from a tool outside ``model_migration_kit`` --
never from running the code under test. The sha256 constants below were derived
with ``hashlib``/``json`` called directly, and the content-hash oracle in
``content_hash`` below is an independent re-implementation of the rule amendment
A states, not a call into the package.

Amendment 1 section A split the identity in two: ``hash`` is the *content* hash
over the canonical JSON of the parsed items sorted by id, and gates
comparability; ``file_hash`` is the old raw-bytes-with-CRLF-normalised value and
is provenance only.
"""

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from model_migration_kit.errors import GoldenSetError
from model_migration_kit.goldenset import GoldenSet


def content_hash(items: list[dict]) -> str:
    """Independent oracle for ``GoldenSet.hash``, per amendment 1 section A.

    Stdlib only. ``items`` are hand-written dicts in ``GoldenItem.to_dict()``
    shape -- ``id`` and ``input`` always, the optional keys present only when the
    item carries them. Sorted by id, canonical-JSON encoded, joined with a single
    newline and no trailing one, then sha256 with CRLF->LF normalisation (a no-op
    on canonical JSON, applied anyway because the rule says so).
    """
    blobs = [
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        for item in sorted(items, key=lambda i: i["id"])
    ]
    return hashlib.sha256(b"\n".join(blobs).replace(b"\r\n", b"\n")).hexdigest()


def file_hash(raw: bytes) -> str:
    """Independent oracle for ``GoldenSet.file_hash``: sha256 of CRLF-normalised bytes."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


# One-item file content, used by the hashing tests.
ONE_LINE = b'{"id":"a","input":"x"}'

#: Content hash of the single item ``{"id": "a", "input": "x"}``. Derived with
#: hashlib/json directly from the amendment's rule, not read out of the package.
CONTENT_HASH_ONE = "a0cce966940a34408bf00877694d404f8ffc017254044878debd200720c8617b"

#: Content hash of the two items ``a``/``x`` and ``b``/``y``, same derivation.
CONTENT_HASH_TWO = "f9b1bea253a1d10241e5bf15f1d7a84c7d29c872d55b4efacb48939a7c6e5a7b"

#: ``file_hash`` of ``ONE_LINE + b"\n"``. This is the original brief's value,
#: from PowerShell ``Get-FileHash``, re-derived with ``hashlib.sha256``. Under
#: amendment A it is the provenance hash, no longer ``hash``.
FILE_HASH_LF = "1bcdca7f33c2173558c98000ffc3fb22a6b4cdaa2d1f9f9ff8de13edc6fff2ec"

#: sha256 of ``ONE_LINE + b"\r\n"`` *without* newline normalisation. From the
#: original brief. Neither hash may ever equal this.
CRLF_RAW_HASH = "be5e48eae0480dacd08f51dcbb78a8d2d4e704726e9c21f2b9edb17a6c8f9693"

#: ``file_hash`` of ``b"\xef\xbb\xbf" + ONE_LINE + b"\n"``, derived with hashlib.
FILE_HASH_BOM = "aedc86b001f898cc34a26e624e7816c4074e9841c3af1f6d52f44ad623528683"

#: The exact three-line set the brief hand-derives ``stats()`` for.
STATS_SET = (
    b'{"id":"i1","input":"a","tags":["math"],"reference":"1"}\n'
    b'{"id":"i2","input":"b","tags":["math","hard"]}\n'
    b'{"id":"i3","input":"c"}\n'
)

#: The same three items as hand-written ``to_dict()`` payloads, for the oracle.
STATS_ITEMS = [
    {"id": "i1", "input": "a", "reference": "1", "tags": ["math"]},
    {"id": "i2", "input": "b", "tags": ["math", "hard"]},
    {"id": "i3", "input": "c"},
]


def write(tmp_path: Path, content: bytes, name: str = "golden.jsonl") -> Path:
    """Write exact bytes to a temp file. ``write_bytes`` does no line translation."""
    target = tmp_path / name
    target.write_bytes(content)
    return target


class TestContentHashOracle:
    """Anchors the local oracle against digests derived offline, before it is used."""

    def test_the_oracle_reproduces_the_hand_derived_digests(self):
        assert content_hash([{"id": "a", "input": "x"}]) == CONTENT_HASH_ONE
        assert content_hash(
            [{"id": "a", "input": "x"}, {"id": "b", "input": "y"}]
        ) == CONTENT_HASH_TWO
        assert file_hash(ONE_LINE + b"\n") == FILE_HASH_LF
        assert file_hash(b"\xef\xbb\xbf" + ONE_LINE + b"\n") == FILE_HASH_BOM

    def test_the_oracle_sorts_by_id_rather_than_preserving_order(self):
        forwards = [{"id": "a", "input": "x"}, {"id": "b", "input": "y"}]
        backwards = [{"id": "b", "input": "y"}, {"id": "a", "input": "x"}]
        assert content_hash(forwards) == content_hash(backwards) == CONTENT_HASH_TWO


class TestHashing:
    """Pins amendment A: ``hash`` is content identity, ``file_hash`` is provenance."""

    def test_a_one_item_set_matches_both_hand_derived_digests(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, ONE_LINE + b"\n"))
        assert loaded.hash == CONTENT_HASH_ONE
        assert loaded.file_hash == FILE_HASH_LF
        # The two answer different questions and must not have collapsed into one.
        assert loaded.hash != loaded.file_hash

    def test_a_multi_item_set_matches_the_oracle(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, STATS_SET))
        assert loaded.hash == content_hash(STATS_ITEMS)
        assert loaded.file_hash == file_hash(STATS_SET)

    def test_a_trailing_newline_changes_only_the_file_hash(self, tmp_path):
        with_nl = GoldenSet.load(write(tmp_path, ONE_LINE + b"\n", "a.jsonl"))
        without_nl = GoldenSet.load(write(tmp_path, ONE_LINE, "b.jsonl"))
        assert with_nl.hash == without_nl.hash == CONTENT_HASH_ONE
        assert with_nl.file_hash != without_nl.file_hash
        assert with_nl.file_hash == FILE_HASH_LF
        assert without_nl.file_hash == file_hash(ONE_LINE)

    def test_key_order_within_a_line_does_not_change_the_content_hash(self, tmp_path):
        reordered = b'{"input":"x","id":"a"}\n'
        loaded = GoldenSet.load(write(tmp_path, reordered))
        assert loaded.hash == CONTENT_HASH_ONE
        assert loaded.file_hash == file_hash(reordered)
        assert loaded.file_hash != FILE_HASH_LF

    def test_incidental_whitespace_does_not_change_the_content_hash(self, tmp_path):
        spaced = b'{"id": "a",   "input": "x"}\n'
        loaded = GoldenSet.load(write(tmp_path, spaced))
        assert loaded.hash == CONTENT_HASH_ONE
        assert loaded.file_hash != FILE_HASH_LF

    def test_item_order_changes_only_the_file_hash(self, tmp_path):
        forwards = b'{"id":"a","input":"x"}\n{"id":"b","input":"y"}\n'
        backwards = b'{"id":"b","input":"y"}\n{"id":"a","input":"x"}\n'
        first = GoldenSet.load(write(tmp_path, forwards, "f.jsonl"))
        second = GoldenSet.load(write(tmp_path, backwards, "b.jsonl"))
        assert first.hash == second.hash == CONTENT_HASH_TWO
        assert first.file_hash != second.file_hash
        # File order still drives iteration; only the hash is order-blind.
        assert first.ids == ("a", "b")
        assert second.ids == ("b", "a")

    def test_a_bom_changes_only_the_file_hash(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, b"\xef\xbb\xbf" + ONE_LINE + b"\n"))
        assert loaded.hash == CONTENT_HASH_ONE
        assert loaded.file_hash == FILE_HASH_BOM
        assert loaded.file_hash != FILE_HASH_LF

    def test_crlf_changes_neither_hash(self, tmp_path):
        crlf = ONE_LINE + b"\r\n"
        # The raw bytes really are different -- proved with hashlib, not the loader.
        assert hashlib.sha256(crlf).hexdigest() == CRLF_RAW_HASH
        loaded = GoldenSet.load(write(tmp_path, crlf))
        assert loaded.hash == CONTENT_HASH_ONE
        assert loaded.file_hash == FILE_HASH_LF
        assert CRLF_RAW_HASH not in (loaded.hash, loaded.file_hash)

    def test_both_hashes_are_stable_across_repeated_loads(self, tmp_path):
        path = write(tmp_path, STATS_SET)
        first, second = GoldenSet.load(path), GoldenSet.load(path)
        assert first.hash == second.hash == content_hash(STATS_ITEMS)
        assert first.file_hash == second.file_hash == file_hash(STATS_SET)

    def test_parse_from_bytes_and_from_str_agree_with_load(self, tmp_path):
        path = write(tmp_path, ONE_LINE + b"\n")
        from_bytes = GoldenSet.parse(ONE_LINE + b"\n")
        from_str = GoldenSet.parse('{"id":"a","input":"x"}\n')
        loaded = GoldenSet.load(path)
        assert from_bytes.hash == from_str.hash == loaded.hash == CONTENT_HASH_ONE
        assert from_bytes.file_hash == from_str.file_hash == loaded.file_hash == FILE_HASH_LF


class TestContentDifferencesChangeTheHash:
    """A change to any actual item content must move ``hash`` -- that is what it gates."""

    BASE_LINE = b'{"id":"a","input":"x","reference":"r","tags":["t"],"metadata":{"k":"v"}}'
    BASE_ITEM = {"id": "a", "input": "x", "reference": "r", "tags": ["t"], "metadata": {"k": "v"}}

    VARIANTS = {
        "id": (
            b'{"id":"z","input":"x","reference":"r","tags":["t"],"metadata":{"k":"v"}}',
            {"id": "z", "input": "x", "reference": "r", "tags": ["t"], "metadata": {"k": "v"}},
        ),
        "input": (
            b'{"id":"a","input":"CHANGED","reference":"r","tags":["t"],"metadata":{"k":"v"}}',
            {"id": "a", "input": "CHANGED", "reference": "r", "tags": ["t"],
             "metadata": {"k": "v"}},
        ),
        "reference": (
            b'{"id":"a","input":"x","reference":"CHANGED","tags":["t"],"metadata":{"k":"v"}}',
            {"id": "a", "input": "x", "reference": "CHANGED", "tags": ["t"],
             "metadata": {"k": "v"}},
        ),
        "tag": (
            b'{"id":"a","input":"x","reference":"r","tags":["CHANGED"],"metadata":{"k":"v"}}',
            {"id": "a", "input": "x", "reference": "r", "tags": ["CHANGED"],
             "metadata": {"k": "v"}},
        ),
        "metadata": (
            b'{"id":"a","input":"x","reference":"r","tags":["t"],"metadata":{"k":"CHANGED"}}',
            {"id": "a", "input": "x", "reference": "r", "tags": ["t"],
             "metadata": {"k": "CHANGED"}},
        ),
    }

    @pytest.mark.parametrize("field", sorted(VARIANTS))
    def test_changing_one_field_changes_the_content_hash(self, tmp_path, field):
        line, item = self.VARIANTS[field]
        base = GoldenSet.load(write(tmp_path, self.BASE_LINE + b"\n", "base.jsonl"))
        changed = GoldenSet.load(write(tmp_path, line + b"\n", "changed.jsonl"))
        assert base.hash == content_hash([self.BASE_ITEM])
        assert changed.hash == content_hash([item])
        assert base.hash != changed.hash

    def test_all_five_variants_are_mutually_distinct(self, tmp_path):
        digests = {content_hash([self.BASE_ITEM])}
        for line, item in self.VARIANTS.values():
            loaded = GoldenSet.load(write(tmp_path, line + b"\n", "v.jsonl"))
            assert loaded.hash == content_hash([item])
            digests.add(loaded.hash)
        assert len(digests) == 6


class TestShapeAndAccessors:
    """Pins the frozen dataclass surface: fields, ids, get, len, file-order iteration."""

    def test_fields_are_items_hash_path_and_file_hash(self, tmp_path):
        # Amendment 1 section A: field order is (items, hash, path, file_hash).
        path = write(tmp_path, ONE_LINE + b"\n")
        loaded = GoldenSet.load(path)
        names = [f.name for f in dataclasses.fields(loaded)]
        assert names == ["items", "hash", "path", "file_hash"]
        assert loaded.path == str(path)

    def test_file_hash_defaults_to_empty(self):
        # Constructed directly, not loaded: the default is part of the field contract.
        assert GoldenSet(items=(), hash="h", path="p").file_hash == ""

    def test_the_dataclass_is_frozen(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, ONE_LINE + b"\n"))
        with pytest.raises(dataclasses.FrozenInstanceError):
            loaded.hash = "nope"

    def test_ids_len_and_iteration_follow_file_order(self, tmp_path):
        # Deliberately not alphabetical, so a sorted implementation would fail.
        content = (
            b'{"id":"c","input":"1"}\n'
            b'{"id":"a","input":"2"}\n'
            b'{"id":"b","input":"3"}\n'
        )
        loaded = GoldenSet.load(write(tmp_path, content))
        assert loaded.ids == ("c", "a", "b")
        assert len(loaded) == 3
        assert [item.id for item in loaded] == ["c", "a", "b"]
        assert loaded.items == tuple(loaded)

    def test_get_returns_the_named_item(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, STATS_SET))
        assert loaded.get("i2").input == "b"

    def test_get_of_an_absent_id_raises(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, STATS_SET))
        with pytest.raises(GoldenSetError, match="no item with id 'zz'"):
            loaded.get("zz")

    def test_parse_defaults_its_source_to_memory(self):
        assert GoldenSet.parse(ONE_LINE).path == "<memory>"

    def test_parse_records_the_source_it_was_given(self):
        assert GoldenSet.parse(ONE_LINE, source="from-artifact").path == "from-artifact"


class TestStats:
    """Pins the hand-derived ``stats()`` expectation from the brief, key set included."""

    def test_stats_matches_the_hand_derived_expectation(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, STATS_SET))
        assert loaded.ids == ("i1", "i2", "i3")
        assert loaded.stats() == {
            "size": 3,
            "with_reference": 1,
            "untagged": 1,
            "tags": {"hard": 1, "math": 2},
        }

    def test_stats_has_exactly_the_contract_keys(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, STATS_SET))
        assert set(loaded.stats()) == {"size", "with_reference", "untagged", "tags"}

    def test_the_tag_distribution_is_sorted_by_key(self, tmp_path):
        # Insertion order would be zebra, apple, mango; sorted order is not that.
        content = (
            b'{"id":"i1","input":"a","tags":["zebra","apple"]}\n'
            b'{"id":"i2","input":"b","tags":["mango"]}\n'
        )
        stats = GoldenSet.load(write(tmp_path, content)).stats()
        assert list(stats["tags"]) == ["apple", "mango", "zebra"]


class TestValidationCase1DuplicateId:
    """Duplicate ids are an error naming the id and the line that defined it first."""

    def test_duplicate_id_names_the_id_and_the_earlier_line(self, tmp_path):
        content = (
            b'{"id":"a","input":"x"}\n'
            b'{"id":"b","input":"y"}\n'
            b'{"id":"a","input":"z"}\n'
        )
        path = write(tmp_path, content)
        with pytest.raises(GoldenSetError, match="duplicate id 'a'") as excinfo:
            GoldenSet.load(path)
        message = str(excinfo.value)
        assert "line 3:" in message
        assert "already defined on line 1" in message


class TestValidationCases2And3RequiredText:
    """``id`` and ``input`` must both be present, string, and non-blank."""

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            (b'{"input":"x"}', "missing required key 'id'"),
            (b'{"id":1,"input":"x"}', "'id' must be a string, got int"),
            (b'{"id":null,"input":"x"}', "'id' must be a string, got NoneType"),
            (b'{"id":"","input":"x"}', "'id' is empty"),
            (b'{"id":"   ","input":"x"}', "'id' is empty"),
        ],
    )
    def test_bad_id_raises(self, tmp_path, line, fragment):
        with pytest.raises(GoldenSetError, match=fragment):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            (b'{"id":"a"}', "missing required key 'input'"),
            (b'{"id":"a","input":7}', "'input' must be a string, got int"),
            (b'{"id":"a","input":["x"]}', "'input' must be a string, got list"),
            (b'{"id":"a","input":""}', "'input' is empty"),
            # \t here is the two-character JSON escape, which decodes to a tab.
            (b'{"id":"a","input":"\\t "}', "'input' is empty"),
        ],
    )
    def test_bad_input_raises(self, tmp_path, line, fragment):
        with pytest.raises(GoldenSetError, match=fragment):
            GoldenSet.load(write(tmp_path, line + b"\n"))


class TestValidationCases4And5JsonShape:
    """A line must be valid JSON, and that JSON must be an object."""

    @pytest.mark.parametrize("line", [b"{", b'{"id": }', b"not json at all", b"{'id':'a'}"])
    def test_malformed_json_raises(self, tmp_path, line):
        with pytest.raises(GoldenSetError, match="not valid JSON"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    @pytest.mark.parametrize(
        ("line", "typename"),
        [(b"[1,2]", "list"), (b'"a"', "str"), (b"3", "int"), (b"true", "bool")],
    )
    def test_valid_json_that_is_not_an_object_raises(self, tmp_path, line, typename):
        with pytest.raises(GoldenSetError, match=f"expected a JSON object, got {typename}"):
            GoldenSet.load(write(tmp_path, line + b"\n"))


class TestValidationCase6UnknownKeys:
    """Unknown top-level keys are rejected, listing the offenders and the allowed set."""

    @pytest.mark.parametrize(
        ("line", "offender"),
        [
            (b'{"id":"a","input":"x","tag":"math"}', "'tag'"),
            (b'{"id":"a","Input":"x","input":"x"}', "'Input'"),
        ],
    )
    def test_an_unknown_key_raises_and_names_it(self, tmp_path, line, offender):
        with pytest.raises(GoldenSetError, match=r"unknown key\(s\) " + offender) as excinfo:
            GoldenSet.load(write(tmp_path, line + b"\n"))
        message = str(excinfo.value)
        assert "Allowed:" in message
        for allowed in ("id", "input", "reference", "tags", "metadata"):
            assert allowed in message

    def test_several_unknown_keys_are_all_listed(self, tmp_path):
        line = b'{"id":"a","input":"x","zeta":1,"alpha":2}'
        with pytest.raises(GoldenSetError, match=r"unknown key\(s\)") as excinfo:
            GoldenSet.load(write(tmp_path, line + b"\n"))
        assert "'alpha'" in str(excinfo.value)
        assert "'zeta'" in str(excinfo.value)


class TestValidationCase7Reference:
    """``reference`` may be absent or null, but never a non-string or a blank string."""

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            (b'{"id":"a","input":"x","reference":5}', "must be a string or null, got int"),
            (b'{"id":"a","input":"x","reference":[]}', "must be a string or null, got list"),
            (b'{"id":"a","input":"x","reference":{}}', "must be a string or null, got dict"),
            (b'{"id":"a","input":"x","reference":""}', "'reference' is empty"),
            (b'{"id":"a","input":"x","reference":"  "}', "'reference' is empty"),
        ],
    )
    def test_bad_reference_raises(self, tmp_path, line, fragment):
        with pytest.raises(GoldenSetError, match=fragment):
            GoldenSet.load(write(tmp_path, line + b"\n"))


class TestValidationCase8Tags:
    """``tags`` is a list of distinct non-blank strings -- a bare string is not iterable here."""

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            # A bare string is iterable in Python; treating it as a tag list would
            # silently produce one tag per character, so it must be rejected.
            (b'{"id":"a","input":"x","tags":"math"}', "must be a list of strings, got str"),
            (b'{"id":"a","input":"x","tags":{"a":1}}', "must be a list of strings, got dict"),
            (b'{"id":"a","input":"x","tags":3}', "must be a list of strings, got int"),
        ],
    )
    def test_tags_that_are_not_a_list_raise(self, tmp_path, line, fragment):
        with pytest.raises(GoldenSetError, match=fragment):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_a_non_string_element_raises(self, tmp_path):
        line = b'{"id":"a","input":"x","tags":["math",3]}'
        with pytest.raises(GoldenSetError, match="every tag must be a string, got int"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    @pytest.mark.parametrize("tags", [b'["math",""]', b'["  "]'])
    def test_an_empty_element_raises(self, tmp_path, tags):
        line = b'{"id":"a","input":"x","tags":' + tags + b"}"
        with pytest.raises(GoldenSetError, match="tags cannot be empty strings"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_a_tag_repeated_within_one_item_raises(self, tmp_path):
        line = b'{"id":"a","input":"x","tags":["math","math"]}'
        with pytest.raises(GoldenSetError, match="duplicate tag 'math'"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_the_same_tag_on_two_items_is_fine(self, tmp_path):
        content = (
            b'{"id":"a","input":"x","tags":["math"]}\n'
            b'{"id":"b","input":"y","tags":["math"]}\n'
        )
        assert GoldenSet.load(write(tmp_path, content)).stats()["tags"] == {"math": 2}


class TestTagsAreStrippedBeforeTheDuplicateCheck:
    """Amendment 1 section B: whitespace around a tag is not a distinguishing feature."""

    def test_a_tag_differing_only_in_trailing_space_is_a_duplicate(self, tmp_path):
        line = b'{"id":"a","input":"x","tags":["math","math "]}'
        with pytest.raises(GoldenSetError, match="duplicate tag 'math'"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_a_tag_differing_only_in_leading_space_is_a_duplicate(self, tmp_path):
        line = b'{"id":"a","input":"x","tags":[" math","math"]}'
        with pytest.raises(GoldenSetError, match="duplicate tag 'math'"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_a_padded_tag_is_stored_stripped(self, tmp_path):
        line = b'{"id":"a","input":"x","tags":[" math "]}'
        assert GoldenSet.load(write(tmp_path, line + b"\n")).get("a").tags == ("math",)

    def test_padded_and_bare_tags_count_as_one_slice(self, tmp_path):
        content = (
            b'{"id":"a","input":"x","tags":[" math "]}\n'
            b'{"id":"b","input":"y","tags":["math"]}\n'
        )
        # One slice of two, not two slices of one -- the double-count this check exists for.
        assert GoldenSet.load(write(tmp_path, content)).stats()["tags"] == {"math": 2}

    def test_padding_does_not_change_the_content_hash(self, tmp_path):
        # Follows from B plus A: the stripped tag is what to_dict() serialises.
        padded = GoldenSet.load(
            write(tmp_path, b'{"id":"a","input":"x","tags":[" math "]}\n', "p.jsonl")
        )
        bare = GoldenSet.load(
            write(tmp_path, b'{"id":"a","input":"x","tags":["math"]}\n', "b.jsonl")
        )
        expected = content_hash([{"id": "a", "input": "x", "tags": ["math"]}])
        assert padded.hash == bare.hash == expected


class TestValidationCase9Metadata:
    """``metadata`` must be a JSON object when present."""

    @pytest.mark.parametrize(
        ("line", "typename"),
        [
            (b'{"id":"a","input":"x","metadata":[1]}', "list"),
            (b'{"id":"a","input":"x","metadata":"x"}', "str"),
            (b'{"id":"a","input":"x","metadata":3}', "int"),
        ],
    )
    def test_metadata_that_is_not_an_object_raises(self, tmp_path, line, typename):
        with pytest.raises(GoldenSetError, match=f"'metadata' must be an object, got {typename}"):
            GoldenSet.load(write(tmp_path, line + b"\n"))

    def test_json_object_keys_are_always_strings(self, tmp_path):
        # Amendment 1 section C removed the non-string-key guard as unreachable:
        # RFC 8259 object member names are always strings, so a key written as a
        # number arrives as the string "1". This pins what json.loads yields; it
        # deliberately asserts nothing about a guard, because there is none.
        line = b'{"id":"a","input":"x","metadata":{"1":"one"}}'
        item = GoldenSet.load(write(tmp_path, line + b"\n")).get("a")
        assert item.metadata == {"1": "one"}
        assert all(isinstance(key, str) for key in item.metadata)


class TestValidationCase10EmptySet:
    """A set with no items is an error, not an empty comparison."""

    @pytest.mark.parametrize("content", [b"", b"\n\n\n", b"   \n\t\n  \n", b"\r\n\r\n"])
    def test_a_set_with_no_items_raises(self, tmp_path, content):
        with pytest.raises(GoldenSetError, match="contains no items"):
            GoldenSet.load(write(tmp_path, content))

    def test_parse_of_empty_input_raises(self):
        with pytest.raises(GoldenSetError, match="contains no items"):
            GoldenSet.parse(b"")


class TestValidationCase11UnreadablePath:
    """An absent or unreadable path is a GoldenSetError, not an OSError."""

    def test_a_missing_file_raises(self, tmp_path):
        missing = tmp_path / "not-here.jsonl"
        with pytest.raises(GoldenSetError, match="cannot read golden set") as excinfo:
            GoldenSet.load(missing)
        assert str(missing) in str(excinfo.value)

    def test_a_directory_raises(self, tmp_path):
        # Reading a directory is IsADirectoryError on POSIX and PermissionError on
        # Windows; both are OSError, so this holds on either platform.
        with pytest.raises(GoldenSetError, match="cannot read golden set"):
            GoldenSet.load(tmp_path)


class TestValidationCase12Encoding:
    """Bytes that are not valid UTF-8 are rejected by name, not mojibake'd."""

    def test_invalid_utf8_raises(self, tmp_path):
        # 0xff is never a legal byte in UTF-8.
        with pytest.raises(GoldenSetError, match="is not valid UTF-8"):
            GoldenSet.load(write(tmp_path, b'{"id":"a","input":"\xff"}\n'))

    def test_latin1_encoded_text_raises(self, tmp_path):
        # "café" in latin-1 is a lone 0xe9, which is a truncated UTF-8 sequence.
        content = '{"id":"a","input":"café"}\n'.encode("latin-1")
        with pytest.raises(GoldenSetError, match="is not valid UTF-8"):
            GoldenSet.load(write(tmp_path, content))


class TestLineNumbering:
    """Reported line numbers are 1-based and count every physical line, blanks included."""

    def test_blank_lines_are_counted_in_the_reported_number(self, tmp_path):
        content = (
            b"\n"                              # line 1, blank
            b'{"id":"a","input":"x"}\n'        # line 2
            b"   \n"                           # line 3, whitespace only
            b"{oops\n"                         # line 4, the offender
        )
        path = write(tmp_path, content)
        with pytest.raises(GoldenSetError, match="line 4: not valid JSON") as excinfo:
            GoldenSet.load(path)
        assert str(path) in str(excinfo.value)

    def test_crlf_line_endings_do_not_shift_the_number(self, tmp_path):
        content = b'{"id":"a","input":"x"}\r\n\r\n{"id":"b"}\r\n'  # offender on line 3
        with pytest.raises(GoldenSetError, match="line 3: missing required key 'input'"):
            GoldenSet.load(write(tmp_path, content))

    def test_the_message_names_the_source_given_to_parse(self):
        data = b'{"id":"a","input":"x"}\n[1,2]\n'
        with pytest.raises(GoldenSetError, match="my-set.jsonl line 2: expected a JSON object"):
            GoldenSet.parse(data, source="my-set.jsonl")

    def test_the_message_names_the_default_memory_source(self):
        with pytest.raises(GoldenSetError, match="<memory> line 1: not valid JSON"):
            GoldenSet.parse(b"{\n")


class TestLeniencies:
    """The deliberate leniencies: they must load cleanly, not raise."""

    def test_blank_and_whitespace_lines_anywhere_are_skipped(self, tmp_path):
        content = (
            b"\n"
            b'{"id":"a","input":"x"}\n'
            b"   \n"
            b"\t\n"
            b'{"id":"b","input":"y"}\n'
            b"\n"
        )
        loaded = GoldenSet.load(write(tmp_path, content))
        assert loaded.ids == ("a", "b")
        assert len(loaded) == 2

    def test_a_bom_is_stripped_before_parsing(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, b"\xef\xbb\xbf" + ONE_LINE + b"\n"))
        assert loaded.ids == ("a",)
        assert loaded.get("a").id == "a"
        assert "\ufeff" not in loaded.get("a").id

    def test_an_absent_reference_is_none(self, tmp_path):
        loaded = GoldenSet.load(write(tmp_path, ONE_LINE + b"\n"))
        assert loaded.get("a").reference is None

    def test_an_explicit_null_reference_is_none(self, tmp_path):
        line = b'{"id":"a","input":"x","reference":null}'
        assert GoldenSet.load(write(tmp_path, line + b"\n")).get("a").reference is None

    @pytest.mark.parametrize(
        "line",
        [
            b'{"id":"a","input":"x"}',
            b'{"id":"a","input":"x","tags":[]}',
            b'{"id":"a","input":"x","tags":null}',
        ],
    )
    def test_missing_or_empty_tags_become_an_empty_tuple(self, tmp_path, line):
        item = GoldenSet.load(write(tmp_path, line + b"\n")).get("a")
        assert item.tags == ()

    def test_absent_metadata_becomes_an_empty_dict(self, tmp_path):
        assert GoldenSet.load(write(tmp_path, ONE_LINE + b"\n")).get("a").metadata == {}

    def test_metadata_round_trips(self, tmp_path):
        line = b'{"id":"a","input":"x","metadata":{"source":"prod","n":3}}'
        item = GoldenSet.load(write(tmp_path, line + b"\n")).get("a")
        assert item.metadata == {"source": "prod", "n": 3}

    def test_non_ascii_content_round_trips(self, tmp_path):
        content = (
            '{"id":"café","input":"Où est la gare ?","reference":"à droite"}\n'
            '{"id":"cjk","input":"日本語のテスト","tags":["翻訳"]}\n'
        ).encode()  # .encode() is utf-8 by default; ruff UP012 rejects the explicit arg
        loaded = GoldenSet.load(write(tmp_path, content))
        assert loaded.ids == ("café", "cjk")
        assert loaded.get("café").input == "Où est la gare ?"
        assert loaded.get("café").reference == "à droite"
        assert loaded.get("cjk").input == "日本語のテスト"
        assert loaded.get("cjk").tags == ("翻訳",)

    def test_a_last_line_without_a_newline_still_parses(self, tmp_path):
        content = b'{"id":"a","input":"x"}\n{"id":"b","input":"y"}'
        loaded = GoldenSet.load(write(tmp_path, content))
        assert loaded.ids == ("a", "b")
        assert loaded.get("b").input == "y"
