"""Single entry point for all Claude calls, with a JSON-file cache and DEMO_MODE support.

Owner: shared. Built in prompt A4 (changed only in a "contracts: ..." commit).

Public interface:
    call_json(system, user, schema, model=None, cache_key=None, *, effort="low", max_tokens=4000,
              prompt_version="", cache_only=False) -> dict
    cache_drop(cache_key) -> bool
    ClaudeError, CacheMiss

Cache: one JSON file per key in ai/cache/ (env AI_CACHE_DIR overrides). The two private helpers
_cache_get / _cache_put are the only place that knows the storage, so the backend can be swapped
(for example to core.ledger.cache_get / cache_put) in one place.
DEMO_MODE=1 (or cache_only=True) never creates a client: a cache miss raises CacheMiss.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()  # never overrides variables that are already set

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "cache"


class ClaudeError(Exception):
    """API error, timeout, refusal, truncation, bad JSON or missing key."""


class CacheMiss(ClaudeError):
    """Cache-only call and no cached response."""


def _default_model() -> str:
    return os.environ.get("CLAUDE_RUNTIME_MODEL") or DEFAULT_MODEL


def _cache_dir() -> Path:
    return Path(os.environ.get("AI_CACHE_DIR") or DEFAULT_CACHE_DIR)


def _cache_path(cache_key: str) -> Path:
    return _cache_dir() / (hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24] + ".json")


def _cache_get(cache_key: str) -> dict | None:
    """Cached response for `cache_key`, or None."""
    path = _cache_path(cache_key)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
    except (OSError, ValueError):
        logger.warning("Unreadable cache file %s, ignored", path.name)
        return None
    if entry.get("cache_key") != cache_key or not isinstance(entry.get("response"), dict):
        return None
    return entry["response"]


def _cache_put(cache_key: str, model: str, prompt_version: str, response: dict) -> None:
    path = _cache_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"cache_key": cache_key, "model": model, "prompt_version": prompt_version,
             "created_at": datetime.now().isoformat(timespec="seconds"), "response": response}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)


def cache_drop(cache_key: str) -> bool:
    """Delete one cache entry (used when a cached answer failed verification). True if it existed."""
    path = _cache_path(cache_key)
    if not path.exists():
        return False
    path.unlink()
    logger.info("cache entry dropped (%s)", cache_key[:60])
    return True


def _create(**kwargs):
    """The only network call. Tests patch this function."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ClaudeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(timeout=30.0, max_retries=1).messages.create(**kwargs)


def _parse(response) -> dict:
    """First text block of the response as a JSON object. Raises ClaudeError otherwise."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        raise ClaudeError("model refused the request")
    if stop == "max_tokens":
        raise ClaudeError("response truncated (max_tokens)")
    text = next((b.text for b in (response.content or []) if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise ClaudeError("response has no text block")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ClaudeError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ClaudeError("response JSON is not an object")
    return data


def call_json(system: str, user: str, schema: dict, model: str | None = None,
              cache_key: str | None = None, *, effort: str = "low", max_tokens: int = 4000,
              prompt_version: str = "", cache_only: bool = False) -> dict:
    """One structured-output Claude call. Cache first; DEMO_MODE=1 or cache_only never calls the API."""
    model = model or _default_model()
    if cache_key:
        hit = _cache_get(cache_key)
        if hit is not None:
            logger.info("cache hit (%s)", cache_key[:60])
            return hit
    if cache_only or os.environ.get("DEMO_MODE") == "1":
        logger.info("cache miss in cache-only mode (%s)", (cache_key or "no key")[:60])
        raise CacheMiss(f"no cached response for {cache_key!r}")

    kwargs = {
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": effort},
    }
    started = time.perf_counter()
    try:
        response = _create(**kwargs)
    except ClaudeError:
        raise
    except anthropic.AnthropicError as exc:
        logger.warning("Claude call failed (%s): %s", model, type(exc).__name__)
        raise ClaudeError(f"{type(exc).__name__}: {exc}") from exc
    usage = getattr(response, "usage", None)
    logger.info("Claude call %s: in=%s out=%s tokens, %.1f s", model,
                getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"),
                time.perf_counter() - started)

    data = _parse(response)
    if cache_key:
        _cache_put(cache_key, model, prompt_version, data)
    return data


if __name__ == "__main__":
    _dir = _cache_dir()
    print("model:", _default_model())
    print("DEMO_MODE:", os.environ.get("DEMO_MODE", "(unset)"))
    print("cache folder:", _dir)
    print("cached files:", len(list(_dir.glob("*.json"))) if _dir.exists() else 0)
