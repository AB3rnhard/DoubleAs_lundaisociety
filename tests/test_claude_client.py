"""Tests for ai.claude_client (no network: the private _create is patched). Owner: shared. Built in A4."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ai import claude_client as cc

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["a"],
          "properties": {"a": {"type": "string"}}}


def _response(text: str = '{"a": "x"}', stop: str = "end_turn", thinking: bool = False):
    content = []
    if thinking:
        content.append(SimpleNamespace(type="thinking", thinking="hmm"))
    content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5))


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("CLAUDE_RUNTIME_MODEL", "test-model")
    return tmp_path


@pytest.fixture
def calls(monkeypatch):
    seen: list[dict] = []

    def fake(**kwargs):
        seen.append(kwargs)
        return _response()

    monkeypatch.setattr(cc, "_create", fake)
    return seen


def test_cache_round_trip(tmp_path, calls):
    out = cc.call_json("sys", "usr", SCHEMA, cache_key="k1", prompt_version="v1")
    assert out == {"a": "x"} and len(calls) == 1
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(entry) == {"cache_key", "model", "prompt_version", "created_at", "response"}
    assert entry["cache_key"] == "k1" and entry["model"] == "test-model" and entry["prompt_version"] == "v1"
    assert files[0].name == cc._cache_path("k1").name


def test_cache_hit_does_not_call_create(calls):
    cc.call_json("sys", "usr", SCHEMA, cache_key="k1")
    assert cc.call_json("sys", "usr", SCHEMA, cache_key="k1") == {"a": "x"}
    assert len(calls) == 1


def test_demo_mode_miss_raises_and_never_calls_create(monkeypatch, calls):
    monkeypatch.setenv("DEMO_MODE", "1")
    with pytest.raises(cc.CacheMiss):
        cc.call_json("sys", "usr", SCHEMA, cache_key="missing")
    assert calls == []


def test_demo_mode_still_serves_cache(monkeypatch, calls):
    cc.call_json("sys", "usr", SCHEMA, cache_key="k1")
    monkeypatch.setenv("DEMO_MODE", "1")
    assert cc.call_json("sys", "usr", SCHEMA, cache_key="k1") == {"a": "x"}


def test_cache_only_miss_raises_and_never_calls_create(calls):
    with pytest.raises(cc.CacheMiss):
        cc.call_json("sys", "usr", SCHEMA, cache_key="missing", cache_only=True)
    assert calls == []


def test_cache_miss_is_a_claude_error():
    assert issubclass(cc.CacheMiss, cc.ClaudeError)


def test_thinking_block_before_text_parses(monkeypatch):
    monkeypatch.setattr(cc, "_create", lambda **kw: _response(thinking=True))
    assert cc.call_json("sys", "usr", SCHEMA) == {"a": "x"}


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_bad_stop_reason_raises(monkeypatch, stop):
    monkeypatch.setattr(cc, "_create", lambda **kw: _response(stop=stop))
    with pytest.raises(cc.ClaudeError):
        cc.call_json("sys", "usr", SCHEMA)


def test_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(cc, "_create", lambda **kw: _response(text="not json"))
    with pytest.raises(cc.ClaudeError):
        cc.call_json("sys", "usr", SCHEMA)


def test_no_text_block_raises(monkeypatch):
    monkeypatch.setattr(cc, "_create", lambda **kw: SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="x")], stop_reason="end_turn", usage=None))
    with pytest.raises(cc.ClaudeError):
        cc.call_json("sys", "usr", SCHEMA)


def test_request_kwargs(calls):
    cc.call_json("the system", "the user", SCHEMA, cache_key="k")
    kw = calls[0]
    assert kw["model"] == "test-model"
    assert kw["system"] == "the system"
    assert kw["messages"] == [{"role": "user", "content": "the user"}]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["format"]["schema"] == SCHEMA
    assert kw["output_config"]["effort"] == "low"
    assert kw["max_tokens"] == 4000
    assert not {"thinking", "temperature"} & set(kw)


def test_explicit_model_wins(calls):
    cc.call_json("s", "u", SCHEMA, model="other")
    assert calls[0]["model"] == "other"


def test_failing_call_writes_no_cache_file(tmp_path, monkeypatch):
    def boom(**kw):
        raise cc.ClaudeError("boom")

    monkeypatch.setattr(cc, "_create", boom)
    with pytest.raises(cc.ClaudeError):
        cc.call_json("s", "u", SCHEMA, cache_key="k")
    assert list(tmp_path.glob("*.json")) == []


def test_bad_json_writes_no_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_create", lambda **kw: _response(text="nope"))
    with pytest.raises(cc.ClaudeError):
        cc.call_json("s", "u", SCHEMA, cache_key="k")
    assert list(tmp_path.glob("*.json")) == []


def test_missing_api_key_is_claude_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(cc.ClaudeError):
        cc.call_json("s", "u", SCHEMA)


def test_cache_drop_removes_entry(tmp_path, calls):
    cc.call_json("sys", "usr", SCHEMA, cache_key="k1")
    assert cc.cache_drop("k1") is True
    assert list(tmp_path.glob("*.json")) == []
    assert cc.cache_drop("k1") is False
