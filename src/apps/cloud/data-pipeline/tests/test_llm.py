"""OpenAI 호환 LLM 어댑터(openai_compatible_complete_fn) 테스트 — 네트워크 없이 urlopen 대체.

여기서 잠그는 건 429(동시성 캡 초과) 유계 백오프 재시도다(ALPHA-517): 태깅 병렬화 시 순간
429 가 기사별 llm_error 로 굳지 않고 재시도로 흡수돼야 하며, 그 외 오류(키 4xx·5xx)는 재시도
없이 즉시 올라 fail-loud 해야 한다. sleep 은 no-op 으로 대체해 실제 대기는 없다.
"""

import io
import urllib.error

import pytest

from data_pipeline.tagging import llm

_OK_BODY = b'{"choices": [{"message": {"content": "{}"}}]}'


class _Resp(io.BytesIO):
    """urlopen 컨텍스트매니저 스텁 — with 블록에서 body 를 read() 한다."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://llm.example/v1/chat/completions", code, "err", {}, io.BytesIO(b""))


def _complete(monkeypatch, responses):
    """urlopen 을 responses 시퀀스(순서대로 소비, _Resp 는 반환·Exception 은 raise)로 대체한
    complete callable. 대기(time.sleep)는 no-op."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        item = responses[i]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    fn = llm.openai_compatible_complete_fn(api_key="k", base_url="https://llm.example/v1")
    return fn, calls


def test_429_retried_then_succeeds(monkeypatch):
    # WHY: 병렬화 시 동시성 캡(500) 순간 초과가 429 로 온다 — 재시도가 없으면 그 순간 429 가
    #      기사별 llm_error 로 굳어 태깅 커버리지가 캡 근처에서 조용히 떨어진다.
    fn, calls = _complete(monkeypatch, [_http_error(429), _http_error(429), _Resp(_OK_BODY)])
    assert fn("sys", "usr") == "{}"
    assert calls["n"] == 3  # 429 두 번 뒤 성공


def test_429_exhausted_raises(monkeypatch):
    # WHY: 캡이 계속 막혀 재시도를 소진하면 조용한 폴백이 아니라 예외로 올려 기사 단위 격리로
    #      드러나야 한다(Rule 12) — 소진 시도 수는 초기 1 + MAX_RETRIES_429.
    fn, calls = _complete(monkeypatch, [_http_error(429)] * (llm.MAX_RETRIES_429 + 1))
    with pytest.raises(urllib.error.HTTPError):
        fn("sys", "usr")
    assert calls["n"] == llm.MAX_RETRIES_429 + 1


def test_non_429_http_error_not_retried(monkeypatch):
    # WHY: 키 오류(4xx)·서버 오류(5xx)는 재시도해도 안 풀린다 — 429 만 유량이라 재시도하고
    #      나머지는 즉시 올려 헛대기 없이 기사 단위로 격리한다. 401 은 단 한 번만 호출돼야 한다.
    fn, calls = _complete(monkeypatch, [_http_error(401), _Resp(_OK_BODY)])
    with pytest.raises(urllib.error.HTTPError):
        fn("sys", "usr")
    assert calls["n"] == 1  # 재시도 없음
