"""Minimal OpenAI-compatible chat client (stdlib only).

Shared by the text generators (daily interpretation, "오늘의 한 줄"). Returns
``(text, model)`` on success and ``(None, label)`` on any failure, so callers
fall back to a deterministic template -- one ticker's LLM hiccup never aborts the
9-ticker batch.

Env: ``LLM_API_KEY``/``OPENAI_API_KEY`` (else Secrets Manager via ``aws.openai_key``),
``LLM_MODEL`` (default ``gpt-4o-mini``), ``LLM_BASE_URL``.
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_MODEL = "gpt-4o-mini"


def api_key() -> str | None:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    try:
        from .aws import openai_key
        return openai_key()
    except Exception:
        return None


def chat(messages: list[dict], *, max_tokens: int = 600, model: str | None = None) -> tuple[str | None, str]:
    """POST chat/completions. ``(text, model)`` on success; ``(None, label)`` on failure."""
    model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    key = api_key()
    if not key:
        return None, "no-key"
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = json.dumps({"model": model, "messages": messages,
                       "max_completion_tokens": max_tokens}).encode()  # gpt-5 family: no custom temperature
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = (json.loads(r.read().decode())["choices"][0]["message"]["content"] or "").strip()
        return (txt or None), model
    except Exception as exc:  # caller falls back to a template
        return None, f"{model}(error:{type(exc).__name__})"
