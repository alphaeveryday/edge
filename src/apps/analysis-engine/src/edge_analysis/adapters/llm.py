"""DeepSeek client and the analysis step.

``analyze`` builds the prompt, calls the client, and validates the response;
an invalid response fails loudly rather than persisting an empty explanation.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date
from typing import Any, Protocol

from ..config import PipelineError
from ..domain.models import Decomposition, Explanation, KodexEvent, PriceTrigger
from ..domain.packet import build_packet

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


class AnalysisClient(Protocol):
    """Minimal contract the analysis step needs from an LLM client."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


class DeepSeekClient:
    """OpenAI-compatible DeepSeek chat client returning a parsed JSON object."""

    def __init__(self, api_key: str, model: str, timeout: int = 180) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
                "max_tokens": 8000,
            }
        ).encode("utf-8")
        last: Exception | None = None
        for _ in range(3):
            try:
                req = urllib.request.Request(
                    DEEPSEEK_URL,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = json.load(resp)
                return json.loads(payload["choices"][0]["message"]["content"])
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
                last = exc
        raise PipelineError(f"DeepSeek call failed after retries: {last}")


def analyze(
    client: AnalysisClient,
    *,
    etf_ticker: str,
    trade_date: date,
    decomp: Decomposition,
    gate: PriceTrigger,
    route_code: str,
    events: list[KodexEvent],
) -> Explanation:
    """Run the analysis LLM and return a validated Explanation."""
    system, packet = build_packet(
        etf_ticker=etf_ticker, trade_date=trade_date, decomp=decomp,
        gate=gate, route_code=route_code, events=events,
    )
    explanation = Explanation(client.complete_json(system, packet))
    if not explanation.is_valid:
        raise PipelineError("analysis response missing required fields")
    return explanation
