from __future__ import annotations

from app.llm.client import _strip_thought


def test_strip_thought_whole_block():
    text = "before <|channel|>thought reasoning content channel|> after"
    result = _strip_thought(text)
    assert "reasoning content" not in result
    assert "before" in result
    assert "after" in result


def test_strip_thought_split_across_parts():
    part_one = "hello <|channel|>thou"
    part_two = "ght split content channel|> world"
    joined = part_one + part_two
    result = _strip_thought(joined)
    assert "split content" not in result
    assert "hello" in result
    assert "world" in result


def test_strip_thought_no_block():
    text = "hello world no thought here"
    assert _strip_thought(text) == text


def test_strip_thought_empty_thought():
    text = "a<|channel|>thoughtchannel|>b"
    result = _strip_thought(text)
    assert result == "ab"


def test_strip_thought_multiple_blocks():
    text = "a <|channel|>thought x channel|> b <|think|> y channel|> c"
    result = _strip_thought(text)
    assert "x" not in result
    assert "y" not in result
    assert result.count("a") == 1
    assert "b" in result
    assert "c" in result
