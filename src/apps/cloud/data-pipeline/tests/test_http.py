"""PoliteClient 테스트 — request() 코어 일반화 + get() 하위호환 (네트워크 없이 urlopen 대체).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). KR 벤더가 붙으면서
운반 계층이 POST·커스텀 헤더·바이너리 응답을 받아야 하되, 재시도·StopFetch 백본과 기존
get() 계약은 그대로여야 한다 — 이 회귀를 코드로 잠근다.
"""

import io
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

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


class _FakeClock:
    """가상 시계 — `monotonic()` 을 읽고 `advance()` 로만 흐른다.

    `_respect_interval` 은 시각 읽기·대기·슬롯 갱신을 **전부 락 안에서** 하므로, 이 시계로
    갈아끼우면 가상 시간축이 실제 스레드 인터리빙과 무관하게 결정적이 된다. 벽시계로 재면
    엄밀히 단언할 때 경합에 간헐 실패하고, 느슨하게 잡으면 2배 발신률 회귀가 통과한다 —
    가상 시계는 그 딜레마 자체를 없앤다(실제로 자지도 않아 즉시 끝난다).
    """

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        # 대기 지점에서 **실제로** 스레드를 넘긴다. 가상 시계만 두면 대기가 즉시 반환돼
        # 슬롯 읽기~쓰기 사이가 열리지 않아, 락을 빼도 경합이 재현되지 않는다(실측: 5/5 통과).
        # 올바른 구현은 이 양보가 락 안에서 일어나므로 가상 시간축은 그대로 결정적이다.
        time.sleep(0.001)


def _virtual_clock_client(monkeypatch, interval, rtt=0.0):
    """시간이 가상 시계로만 흐르는 PoliteClient. rtt 는 응답까지 걸리는 가상 시간."""
    clock = _FakeClock()
    sends: list[float] = []

    def handler(req):
        sends.append(clock.monotonic())
        clock.advance(rtt)
        return _Resp(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: handler(req))
    monkeypatch.setattr("data_pipeline.sources.http.time", clock)
    client = PoliteClient(min_interval=interval)
    client._sleep = clock.advance
    return client, clock, sends


def test_min_interval_caps_average_rate_across_threads(monkeypatch):
    # WHY: 어댑터를 팬아웃하면 워커 여럿이 한 클라이언트를 공유한다. 간격 처리가 스레드
    #      안전하지 않으면 워커들이 같은 시각을 읽고 뭉쳐 나가 유량이 워커 수만큼 샌다 —
    #      KIS 는 앱키당 초당 한도가 있고 그 예산을 네 스텝이 나눠 쓰므로(문서값 20/s) 그게
    #      곧 한도 초과다. 계약은 **평균 발신률**이다(EGW00201 이 초당 카운터라 그 축이 걸린다).
    #      인접 간격은 계약이 아니다 — 락이 urlopen 전에 풀려 원리적으로 보장할 수 없고,
    #      보장하려면 I/O 를 직렬화해야 해서 팬아웃이 무의미해진다.
    interval = 1.0
    calls = 20
    client, clock, _ = _virtual_clock_client(monkeypatch, interval)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: client.get("https://x.example/y"), range(calls)))

    # 20콜이 소비한 가상 시간은 정확히 19 간격이다 — 첫 콜은 안 기다리고 나머지는 각자
    # 자기 슬롯까지 기다린다. 톨러런스가 없으므로 양쪽으로 갈린다:
    #   - 슬롯 간격이 절반인 회귀 → 9.5 로 미달 (느슨한 하한이라 통과하던 구멍을 막는다)
    #   - 락이 없어 슬롯이 겹치는 회귀 → 갱신을 잃은 만큼 미달
    assert clock.now == pytest.approx(interval * (calls - 1))


def test_serial_sends_are_spaced_from_send_not_from_completion(monkeypatch):
    # WHY: 슬롯 기준이 '직전 완료'에서 '직전 발신'으로 옮겨졌다(start-to-start). 옛 기준은
    #      매 요청이 `RTT + interval` 을 쓰게 해 직렬 경로를 응답시간만큼 공짜로 느리게 했다 —
    #      KIS 실측 RTT 0.78s, interval 0.5s 에서 0.78 → 1.28 req/s 차이가 여기서 난다.
    #      응답에 시간이 걸려야 두 기준이 갈리므로 rtt 를 태워 재현한다.
    interval = 1.0
    client, _, sends = _virtual_clock_client(monkeypatch, interval, rtt=interval)

    for _ in range(5):
        client.get("https://x.example/y")

    gaps = [b - a for a, b in zip(sends, sends[1:])]
    assert gaps == pytest.approx([interval] * 4)  # 완료 기준이면 rtt + interval = 2.0


def test_first_call_is_not_delayed(monkeypatch):
    # WHY: 첫 요청까지 간격만큼 기다리면 모든 스텝이 매 런마다 공짜로 느려진다 — 슬롯이
    #      0 에서 시작하므로 첫 콜은 즉시 나가야 한다.
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(b"{}"))
    client = PoliteClient(min_interval=5.0)
    started = time.monotonic()
    client.get("https://x.example/y")
    assert time.monotonic() - started < 1.0
