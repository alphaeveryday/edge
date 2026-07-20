"""PoliteClient 테스트 — request() 코어 일반화 + get() 하위호환 (네트워크 없이 urlopen 대체).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). KR 벤더가 붙으면서
운반 계층이 POST·커스텀 헤더·바이너리 응답을 받아야 하되, 재시도·StopFetch 백본과 기존
get() 계약은 그대로여야 한다 — 이 회귀를 코드로 잠근다.
"""

import io
import urllib.error

import pytest

from data_pipeline.sources.http import PoliteClient, StopFetch


class _Resp(io.BytesIO):
    """urlopen 컨텍스트매니저 스텁 — with 블록에서 body 를 read() 한다."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(monkeypatch, handler):
    """urlopen 을 handler(req)->_Resp 로 대체한 PoliteClient. 대기는 no-op."""
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: handler(req))
    client = PoliteClient(min_interval=0)
    client._sleep = lambda secs: None  # 실제 sleep 제거
    return client


def test_get_returns_decoded_string(monkeypatch):
    # WHY: 기존 어댑터(FMP)는 get()->str 계약에 의존한다 — 일반화 후에도 그대로 문자열.
    client = _client(monkeypatch, lambda req: _Resp(b'{"ok": 1}'))
    assert client.get("https://x.example/y") == '{"ok": 1}'


def test_get_sends_accept_header(monkeypatch):
    # WHY: get() 은 Accept 헤더를 실어 왔다 — 하위호환 래퍼가 이 헤더를 유지해야 한다.
    seen = {}

    def handler(req):
        seen["accept"] = req.get_header("Accept")
        return _Resp(b"[]")

    _client(monkeypatch, handler).get("https://x.example/y")
    assert seen["accept"] == "application/json"


def test_request_post_carries_headers_and_body(monkeypatch):
    # WHY: KIS 토큰·BigKinds search 는 POST + 커스텀 헤더 + JSON 본문이 필요하다 —
    #      운반 계층이 method/headers/data 를 그대로 실어야 한다.
    seen = {}

    def handler(req):
        seen["method"] = req.get_method()
        seen["ctype"] = req.get_header("Content-type")
        seen["data"] = req.data
        return _Resp(b'{"access_token": "t"}')

    client = _client(monkeypatch, handler)
    out = client.request(
        "POST", "https://x.example/tok", headers={"content-type": "application/json"}, data=b"{}"
    )
    assert out == '{"access_token": "t"}'
    assert seen == {"method": "POST", "ctype": "application/json", "data": b"{}"}


def test_request_can_return_raw_bytes(monkeypatch):
    # WHY: OpenDART corpCode.xml 은 ZIP(바이너리)로 온다 — decode=False 로 원본 bytes 를
    #      받아야 UTF-8 강제 디코드로 깨지지 않는다.
    client = _client(monkeypatch, lambda req: _Resp(b"PK\x03\x04rawzip"))
    out = client.request("GET", "https://x.example/z", decode=False)
    assert out == b"PK\x03\x04rawzip"


def test_request_stops_on_4xx(monkeypatch):
    # WHY: 4xx(키·요청 오류)는 재시도로 두드리지 않고 즉시 StopFetch — 쿼터·차단을 악화시키지 않게.
    def handler(req):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b""))

    with pytest.raises(StopFetch):
        _client(monkeypatch, handler).request("GET", "https://x.example/y")


def test_request_retries_5xx_then_succeeds(monkeypatch):
    # WHY: 일시적 5xx 는 백오프 재시도로 흡수해야 한 번의 서버 딸꾹질이 수집을 죽이지 않는다.
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, io.BytesIO(b""))
        return _Resp(b"[]")

    assert _client(monkeypatch, handler).request("GET", "https://x.example/y") == "[]"
    assert calls["n"] == 2  # 첫 5xx 후 재시도로 성공


def test_request_raises_after_retry_exhaustion(monkeypatch):
    # WHY: 재시도를 다 써도 실패하면 조용히 빈 결과가 아니라 RuntimeError 로 드러내야 한다.
    def handler(req):
        raise urllib.error.URLError("network down")

    with pytest.raises(RuntimeError):
        _client(monkeypatch, handler).request("GET", "https://x.example/y")
