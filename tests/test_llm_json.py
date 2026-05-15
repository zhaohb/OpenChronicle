"""Tests for LLM JSON response parsing."""

from __future__ import annotations

from openchronicle.writer.llm import parse_json_object


def test_parse_plain_object() -> None:
    assert parse_json_object('{"entries": ["a"]}') == {"entries": ["a"]}


def test_parse_json_code_fence() -> None:
    raw = '```json\n{"entries": ["a"]}\n```'
    assert parse_json_object(raw) == {"entries": ["a"]}


def test_parse_fence_without_language_tag() -> None:
    raw = '```\n{"summary": "ok", "sub_tasks": []}\n```'
    assert parse_json_object(raw) == {"summary": "ok", "sub_tasks": []}


def test_parse_leading_prose() -> None:
    raw = 'Here is the result:\n{"summary": "ok", "sub_tasks": ["x"]}'
    assert parse_json_object(raw) == {"summary": "ok", "sub_tasks": ["x"]}


def test_parse_empty_returns_none() -> None:
    assert parse_json_object("") is None
    assert parse_json_object("   ") is None


def test_parse_invalid_returns_none() -> None:
    assert parse_json_object("not json") is None
    assert parse_json_object("```json\nnot valid\n```") is None


def test_parse_array_top_level_returns_none() -> None:
    assert parse_json_object("[1, 2]") is None
