import pytest

from app.analysis.json_repair import is_truncated, parse_json_response


class TestParseJsonResponse:
    def test_valid_json(self):
        result = parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_code_fences(self):
        text = '```json\n{"key": "value"}\n```'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_with_bare_code_fences(self):
        text = '```\n{"key": "value"}\n```'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_trailing_comma_in_object(self):
        text = '{"key": "value",}'
        result = parse_json_response(text)
        assert result == {"key": "value"}

    def test_trailing_comma_in_array(self):
        text = '{"items": ["a", "b",]}'
        result = parse_json_response(text)
        assert result == {"items": ["a", "b"]}

    def test_code_fences_and_trailing_comma(self):
        text = '```json\n{"items": ["a",]}\n```'
        result = parse_json_response(text)
        assert result == {"items": ["a"]}

    def test_totally_invalid_returns_none(self):
        result = parse_json_response("This is not JSON at all")
        assert result is None

    def test_empty_string(self):
        result = parse_json_response("")
        assert result is None


class TestIsTruncated:
    def test_complete_json(self):
        assert is_truncated('{"key": "value"}') is False

    def test_truncated_json(self):
        assert is_truncated('{"key": "val') is True

    def test_complete_array(self):
        assert is_truncated("[1, 2, 3]") is False

    def test_empty_string(self):
        assert is_truncated("") is False
