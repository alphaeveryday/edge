"""클라이언트 견고성 — 결정론적 실패를 3번 반복하지 않는지.

temperature=0 에서 같은 요청은 같은 응답을 낳는다. 잘린 JSON 을 동일 예산으로
재시도하면 같은 실패가 3번 나고 셀이 죽는다. 계약: 형식 어긋남은 수리(salvage),
절단(finish_reason=length)은 예산 증액으로 **요청을 바꿔** 재시도한다.
"""
import io
import json

import pytest

from edge_analysis.adapters import llm as L
from edge_analysis.config import PipelineError


def test_salvage_strips_fences_and_prose():
    assert L._salvage('```json\n{"a": 1}\n```') == {"a": 1}
    assert L._salvage('설명하자면...\n{"a": {"b": 2}} 이상입니다.') == {"a": {"b": 2}}


def test_salvage_returns_none_when_unrepairable():
    assert L._salvage("{...잘린 채 끝") is None
    assert L._salvage("json 없음") is None
    assert L._salvage('[1, 2]') is None            # 객체가 아니면 계약 밖


def _resp(content: str, finish: str):
    body = json.dumps({"choices": [{"message": {"content": content},
                                    "finish_reason": finish}]}).encode()

    class R(io.BytesIO):
        def __enter__(self):  # noqa: D105
            return self

        def __exit__(self, *a):  # noqa: D105
            return False

    return R(body)


def test_truncation_raises_budget_on_retry(monkeypatch):
    sent: list[int] = []
    full = json.dumps({"done": True})

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data)["max_tokens"])
        # 첫 시도(8000)는 절단, 예산이 커진 뒤에야 온전한 JSON.
        return _resp('{"thought": "잘린', "length") if sent[-1] == 8000 \
            else _resp(full, "stop")

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    out = L.DeepSeekClient("k", "m").complete_json("s", "u")
    assert out == {"done": True}
    assert sent[0] == 8000 and sent[1] > 8000       # 예산이 실제로 커졌다


def test_same_failure_still_bounded(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _resp("영원히 산문", "stop")

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(PipelineError):
        L.DeepSeekClient("k", "m").complete_json("s", "u")


def test_default_response_timeout_keeps_a_failed_canary_bounded(monkeypatch):
    seen: list[int] = []

    def fake_urlopen(req, timeout=None):
        seen.append(timeout)
        raise L.urllib.error.URLError("slow upstream")

    monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(PipelineError):
        L.DeepSeekClient("k", "m").complete_json("s", "u")

    assert seen == [60, 60, 60]
