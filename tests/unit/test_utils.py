"""Unit tests for memory_tool.utils module."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memory_tool.utils import (
    ISO_FORMAT,
    utc_now,
    normalize_text,
    stem_token,
    normalize_tags_list,
    tags_to_json,
    tags_to_text,
    parse_tags_json,
    auto_tags_from_text,
    parse_ids,
    quote_fts_query,
    resolve_db_path,
    DEFAULT_PROFILE,
    PROFILE_CHOICES,
)


class TestUtcNow:
    """Test utc_now function."""

    def test_returns_iso_format(self):
        """Verify utc_now returns valid ISO format timestamp."""
        result = utc_now()
        # Verify format matches ISO_FORMAT
        datetime.strptime(result, ISO_FORMAT)

    def test_returns_utc_time(self):
        """Verify utc_now returns UTC time."""
        result = utc_now()
        now = datetime.now(timezone.utc)
        parsed = datetime.strptime(result, ISO_FORMAT).replace(tzinfo=timezone.utc)
        # Should be within 1 second
        diff = abs((now - parsed).total_seconds())
        assert diff < 1

    def test_returns_string(self):
        """Verify utc_now returns a string."""
        result = utc_now()
        assert isinstance(result, str)


class TestNormalizeText:
    """Test normalize_text function."""

    @pytest.mark.parametrize(
        "input_text,expected",
        [
            ("  hello   world  ", "hello world"),
            ("hello\nworld", "hello world"),
            ("hello\tworld", "hello world"),
            ("hello  \n  \t  world", "hello world"),
            ("", ""),
            ("   ", ""),
            ("single", "single"),
        ],
    )
    def test_normalizes_whitespace(self, input_text, expected):
        """Test various whitespace normalization cases."""
        assert normalize_text(input_text) == expected


class TestStemToken:
    """Test stem_token function."""

    @pytest.mark.parametrize(
        "input_word,expected",
        [
            ("running", "run"),
            ("tests", "test"),
            ("configuration", "configur"),
            ("files", "file"),
            ("test", "test"),  # Already stemmed
        ],
    )
    def test_stems_words(self, input_word, expected):
        """Test word stemming."""
        result = stem_token(input_word)
        assert result == expected


class TestNormalizeTagsList:
    """Test normalize_tags_list function."""

    def test_empty_input_returns_empty_list(self):
        """Test that None, empty string, and empty list return empty list."""
        assert normalize_tags_list(None) == []
        assert normalize_tags_list("") == []
        assert normalize_tags_list([]) == []

    def test_normalizes_string_tags(self):
        """Test comma-separated string tags."""
        result = normalize_tags_list("tag1, tag2, tag3")
        assert set(result) == {"tag1", "tag2", "tag3"}

    def test_normalizes_json_string(self):
        """Test JSON string tags."""
        result = normalize_tags_list('["tag1", "tag2"]')
        assert result == ["tag1", "tag2"]

    def test_normalizes_list_tags(self):
        """Test list input tags."""
        result = normalize_tags_list(["tag1", "tag2"])
        assert result == ["tag1", "tag2"]

    def test_removes_duplicates(self):
        """Test duplicate removal."""
        result = normalize_tags_list(["tag1", "tag1", "tag2"])
        assert result == ["tag1", "tag2"]

    def test_removes_blacklisted_words(self):
        """Test blacklisted words are removed."""
        result = normalize_tags_list("the, and, test, a")
        assert "the" not in result
        assert "and" not in result
        assert "a" not in result
        assert "test" in result

    def test_applies_stemming(self):
        """Test stemming is applied to tags."""
        result = normalize_tags_list("running, tests")
        assert "run" in result  # stemmed
        assert "test" in result  # stemmed

    def test_handles_whitespace(self):
        """Test whitespace handling in comma-separated tags."""
        result = normalize_tags_list("  tag1  ,  tag2  ")
        assert result == ["tag1", "tag2"]


class TestTagsConversion:
    """Test tags_to_json and tags_to_text functions."""

    def test_tags_to_json(self):
        """Test converting tags list to JSON."""
        tags = ["tag1", "tag2", "tag3"]
        result = tags_to_json(tags)
        assert json.loads(result) == tags

    def test_tags_to_text(self):
        """Test converting tags list to text."""
        tags = ["tag1", "tag2", "tag3"]
        result = tags_to_text(tags)
        assert result == "tag1 tag2 tag3"

    def test_parse_tags_json(self):
        """Test parsing tags JSON."""
        json_str = '["tag1", "tag2"]'
        result = parse_tags_json(json_str)
        assert result == ["tag1", "tag2"]

    def test_parse_tags_json_invalid(self):
        """Test parsing invalid JSON returns empty list."""
        result = parse_tags_json("invalid json")
        assert result == []


class TestAutoTagsFromText:
    """Test auto_tags_from_text function."""

    def test_extracts_meaningful_words(self):
        """Test extraction of meaningful words from text."""
        title = "Database Connection Error"
        summary = "Failed to connect to PostgreSQL database"
        result = auto_tags_from_text(title, summary)

        # Should extract meaningful words
        assert len(result) > 0
        assert len(result) <= 10  # Max 10 tags

    def test_filters_common_words(self):
        """Test that common words are filtered out."""
        title = "The and of a test"
        summary = "This is an example"
        result = auto_tags_from_text(title, summary)

        # Common words should be filtered
        assert "the" not in result
        assert "and" not in result
        assert "of" not in result
        assert "a" not in result


class TestParseIds:
    """Test parse_ids function."""

    def test_parses_single_id(self):
        """Test parsing single ID."""
        assert parse_ids("123") == [123]

    def test_parses_multiple_ids(self):
        """Test parsing multiple IDs."""
        assert parse_ids("1, 2, 3") == [1, 2, 3]

    def test_parses_range(self):
        """Test parsing ID range."""
        assert parse_ids("1-3") == [1, 2, 3]

    def test_parses_mixed(self):
        """Test parsing mixed IDs and ranges."""
        assert parse_ids("1, 3-5") == [1, 3, 4, 5]

    def test_removes_duplicates(self):
        """Test duplicate removal."""
        assert parse_ids("1, 1, 2") == [1, 2]

    def test_handles_whitespace(self):
        """Test whitespace handling."""
        assert parse_ids("  1  ,  2  ") == [1, 2]


class TestQuoteFtsQuery:
    """Test quote_fts_query function."""

    def test_adds_quotes_to_multiword(self):
        """Test adding quotes to multi-word query."""
        result = quote_fts_query("hello world")
        assert result == '"hello world"'

    def test_preserves_single_word(self):
        """Test single word is not quoted."""
        result = quote_fts_query("hello")
        assert result == "hello"

    def test_handles_empty_string(self):
        """Test empty string handling."""
        assert quote_fts_query("") == ""


class TestResolveDbPath:
    """Test resolve_db_path function."""

    def test_explicit_db_path(self):
        """Test explicit db path is used."""
        result = resolve_db_path("claude", "/custom/path.db")
        assert result == "/custom/path.db"

    def test_valid_profile(self):
        """Test valid profile returns path."""
        result = resolve_db_path("claude", None)
        assert ".claude_memory" in result
        assert result.endswith("memory.db")

    def test_invalid_profile_raises(self):
        """Test invalid profile raises ValueError."""
        with pytest.raises(ValueError, match="profile"):
            resolve_db_path("invalid", None)

    def test_default_profile(self):
        """Test default profile is claude."""
        result = resolve_db_path(DEFAULT_PROFILE, None)
        assert ".claude_memory" in result
