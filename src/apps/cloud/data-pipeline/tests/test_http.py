"""PoliteClient 테스트 — request() 코어 일반화 + get() 하위호환 (네트워크 없이 urlopen 대체).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). KR 벤더가 붙으면서
운반 계층이 POST·커스텀 헤더·바이너리 응답을 받아야 하되, 재시도·StopFetch 백본과 기존
get() 계약은 그대로여야 한다 — 이 회귀를 코드로 잠근다.
"""

import io
import socket
import threading
import time
import traceback
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest

from data_pipeline.failures import SafeFailureError
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


def test_4xx_body_is_available_to_adapter_but_not_exception_text(monkeypatch):
    # WHY(ALPHA-1064): 어댑터는 벤더 코드를 판정하려고 body가 필요하지만 str(exc)를 S3에 쓰는
    # 수집기는 토큰·요청 전문까지 복사하면 안 된다.
    secret = "account=fake-identifier&token=sensitive"

    def handler(req):
        raise urllib.error.HTTPError(
            req.full_url, 403, "forbidden", {}, io.BytesIO(secret.encode())
        )

    with pytest.raises(StopFetch) as caught:
        _client(monkeypatch, handler).request("GET", "https://x.example/y")

    assert caught.value.body == secret
    assert secret not in str(caught.value)


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
    # WHY: 재시도를 다 써도 실패하면 조용히 빈 결과가 아니라 안전한 일시 장애로 드러내되,
    #      원본 네트워크 예외의 URL·자격증명은 traceback에도 이어 붙이지 않는다.
    secret = "proxy-password=sensitive"

    def handler(req):
        raise urllib.error.URLError(secret)

    with pytest.raises(SafeFailureError) as caught:
        _client(monkeypatch, handler).request("GET", "https://x.example/y")
    assert caught.value.detail()["category"] == "TRANSIENT"
    assert secret not in "".join(traceback.format_exception(caught.value))


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


# ── 응답 없이 끊긴 연결(RemoteDisconnected) 재시도 — ALPHA-1153 ─────────────────────────────


def _scripted_client(monkeypatch, script, interval=0):
    """가상 시계 PoliteClient — script 의 (걸린 가상 시간, 응답 bytes 또는 예외)를 순서대로 낸다."""
    clock = _FakeClock()
    sends: list[float] = []

    def handler(req, timeout=None):
        sends.append(clock.monotonic())
        took, outcome = script.pop(0)
        clock.advance(took)
        if isinstance(outcome, Exception):
            raise outcome
        return _Resp(outcome)

    monkeypatch.setattr("urllib.request.urlopen", handler)
    monkeypatch.setattr("data_pipeline.sources.http.time", clock)
    client = PoliteClient(min_interval=interval)
    client._sleep = clock.advance
    return client, clock, sends


def _dropped():
    import http.client

    return http.client.RemoteDisconnected("Remote end closed connection without response")


def test_dropped_connection_is_retried_within_the_call(monkeypatch):
    # WHY: urllib 은 응답 수신 단계의 끊김을 URLError 로 감싸지 않는다. 그래서 재시도를 통째로 빠져나가
    #      호출자를 죽였다 — 분 가격은 종목 하나의 끊김이 window 전체 실패였다(ALPHA-1153).
    client, _, sends = _scripted_client(monkeypatch, [(0.1, _dropped()), (0.1, b"[]")])

    assert client.request("GET", "https://x.example/y") == "[]"
    assert len(sends) == 2


def test_body_cut_mid_read_is_the_same_event(monkeypatch):
    # WHY: 본문을 읽다 끊긴 것(IncompleteRead)도 같은 사건이다 — 헤더 전에 끊겼느냐 뒤에 끊겼느냐로
    #      재시도 여부가 갈리면 같은 원인이 두 가지 결과가 된다.
    import http.client

    client, _, sends = _scripted_client(
        monkeypatch, [(0.1, http.client.IncompleteRead(b"par")), (0.1, b"[]")])

    assert client.request("GET", "https://x.example/y") == "[]"
    assert len(sends) == 2


def test_dropped_connection_is_retried_only_once_per_call(monkeypatch):
    # WHY: 계속 끊기는 것은 드문 끊김이 아니라 장애다. 호출마다 여러 번 두드리면 어댑터의 재시도 루프·
    #      window 재청구와 곱해져 발신이 는다 — 한 번만 다시 보낸다. 그 뒤엔 **그 예외를 그대로** 올린다:
    #      안전 실패로 바꾸면 분 가격이 종목 결손으로 접어 window 를 커밋하고, 커밋된 window 는 자동
    #      재청구가 없어 결손이 영구화된다. 예외여야 window 가 실패해 lease 뒤 다시 수집된다.
    import http.client

    client, _, sends = _scripted_client(monkeypatch, [(0.1, _dropped())] * 4)

    with pytest.raises(http.client.RemoteDisconnected):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 2  # 일반 재시도 예산(4회)을 끊김이 다 쓰지 않는다


def test_late_drop_is_not_retried(monkeypatch):
    # WHY: 재시도에는 전체 기한이 있다 — 오래 기다린 끝에 끊긴 호출을 다시 보내면 한 호출이
    #      기한의 두 배를 쓴다(호출자의 window·lease 예산을 먹는다).
    from data_pipeline.sources.http import DISCONNECT_RETRY_DEADLINE_SEC

    client, _, sends = _scripted_client(
        monkeypatch, [(DISCONNECT_RETRY_DEADLINE_SEC + 0.5, _dropped()), (0.1, b"[]")])

    with pytest.raises(ConnectionError):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 1


def test_drop_retries_are_capped_across_calls(monkeypatch):
    # WHY: 호출당 한도만으로는 장애 때 "모든 호출이 한 번씩 더" 가 된다(발신 2배). 클라이언트 전체
    #      예산이 그 상한이다 — 예산을 다 쓰면 재시도 없이 실패하고, 구간이 지나면 다시 허용한다.
    #      ⚠️ 고정 구간 카운터다: 이 테스트는 "한 구간 안에서 5회"만 고정한다. 임의의 60초에 대한
    #      보장이 아니다(구간 경계에 걸치면 최대 10회).
    from data_pipeline.sources.http import (
        DISCONNECT_RETRY_BUDGET,
        DISCONNECT_RETRY_BUDGET_WINDOW_SEC,
    )

    script = [(0.1, _dropped()), (0.1, b"[]")] * DISCONNECT_RETRY_BUDGET + [(0.1, _dropped())]
    client, clock, sends = _scripted_client(monkeypatch, script)

    for _ in range(DISCONNECT_RETRY_BUDGET):
        assert client.request("GET", "https://x.example/y") == "[]"
    with pytest.raises(ConnectionError):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 2 * DISCONNECT_RETRY_BUDGET + 1  # 예산 밖 호출은 한 번만 나갔다

    clock.advance(DISCONNECT_RETRY_BUDGET_WINDOW_SEC)
    script += [(0.1, _dropped()), (0.1, b"[]")]
    assert client.request("GET", "https://x.example/y") == "[]"


def test_drop_retry_shares_the_existing_attempt_budget(monkeypatch):
    # WHY: 끊김 재시도는 새 루프가 아니라 기존 재시도 루프의 한 칸이다. 5xx 와 섞여도 호출당 발신
    #      상한(4)이 늘면 안 된다 — 중첩되면 재시도가 곱해진다.
    def http_503():
        return urllib.error.HTTPError("https://x.example/y", 503, "unavailable", {}, io.BytesIO(b""))

    client, _, sends = _scripted_client(
        monkeypatch, [(0.1, http_503()), (0.1, _dropped()), (0.1, http_503()), (0.1, http_503()),
                      (0.1, b"[]")])

    with pytest.raises(SafeFailureError):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 4


def test_drop_is_not_retried_when_the_backoff_would_cross_the_deadline(monkeypatch):
    # WHY: 기한은 **다시 보내는 시각**에 대한 것이다. 기한 직전에 끊긴 호출은 백오프(1초)를 기다리면
    #      기한을 넘긴다 — 끊긴 시점만 보면 통과해, 넘길 줄 알면서 기다렸다 보내게 된다.
    from data_pipeline.sources.http import DISCONNECT_RETRY_DEADLINE_SEC

    client, clock, sends = _scripted_client(
        monkeypatch, [(DISCONNECT_RETRY_DEADLINE_SEC - 0.5, _dropped()), (0.1, b"[]")])

    with pytest.raises(ConnectionError):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 1
    assert clock.now == pytest.approx(DISCONNECT_RETRY_DEADLINE_SEC - 0.5)  # 백오프도 기다리지 않았다


def test_drop_is_not_resent_after_a_send_wait_that_outlasts_the_deadline(monkeypatch):
    # WHY: 끊긴 시점엔 기한 안이어도 발신 간격(또는 공유 허용) 대기가 길면 실제 재발신은 기한 밖이다.
    #      발신 직전에 다시 보지 않으면 "기한 안에서만 다시 보낸다"가 대기 시간만큼 거짓이 된다.
    from data_pipeline.sources.http import DISCONNECT_RETRY_DEADLINE_SEC

    client, _, sends = _scripted_client(
        monkeypatch, [(0.1, _dropped()), (0.1, b"[]")], interval=DISCONNECT_RETRY_DEADLINE_SEC * 2)

    with pytest.raises(ConnectionError):
        client.request("GET", "https://x.example/y")
    assert len(sends) == 1


def test_drop_on_the_last_attempt_does_not_spend_the_budget(monkeypatch):
    # WHY: 마지막 칸에서 끊기면 다시 보낼 수 없다. 그때도 예산을 깎으면 보내지도 않은 재시도가 예산을
    #      비워, 뒤따르는 평범한 일회성 끊김을 복구하지 못한다.
    from data_pipeline.sources.http import DISCONNECT_RETRY_BUDGET

    def http_503():
        return urllib.error.HTTPError("https://x.example/y", 503, "unavailable", {}, io.BytesIO(b""))

    exhausted = [(0.0, http_503()), (0.0, http_503()), (0.0, http_503()), (0.0, _dropped())]
    script = exhausted * DISCONNECT_RETRY_BUDGET + [(0.1, _dropped()), (0.1, b"[]")]
    client, _, _ = _scripted_client(monkeypatch, script)

    for _ in range(DISCONNECT_RETRY_BUDGET):
        with pytest.raises(ConnectionError):
            client.request("GET", "https://x.example/y")

    # 같은 예산 구간 안(백오프 7초 × 5회 = 35초)의 일회성 끊김이 여전히 복구된다
    assert client.request("GET", "https://x.example/y") == "[]"



def test_drop_retry_waits_for_the_send_interval_like_any_other_send(monkeypatch):
    # WHY: 재시도가 발신 간격 제어를 건너뛰면 끊김이 몰릴 때 벤더 한도 위로 발신이 나간다(KIS 는 초당
    #      한도). 재발신도 직전 발신에서 간격만큼 떨어져야 한다 — 백오프(1초)보다 간격이 길면 간격이 이긴다.
    interval = 3.0
    client, _, sends = _scripted_client(
        monkeypatch, [(0.1, _dropped()), (0.1, b"[]")], interval=interval)

    assert client.request("GET", "https://x.example/y") == "[]"
    assert sends[1] - sends[0] == pytest.approx(interval)


def test_drop_retry_takes_a_new_shared_send_permit(monkeypatch):
    # WHY: 공유 호출 허용이 켜진 경로에서는 발신마다 허용 한 건이다. 재발신이 첫 허용을 재사용하면
    #      합산 한도 계산에서 발신 하나가 빠진다.
    class Pacer:
        calls = 0

        def pace(self, cost=1):
            self.calls += 1

    client, _, sends = _scripted_client(monkeypatch, [(0.1, _dropped()), (0.1, b"[]")])
    client.pacer = Pacer()

    assert client.request("GET", "https://x.example/y") == "[]"
    assert (len(sends), client.pacer.calls) == (2, 2)


def test_first_call_is_not_delayed(monkeypatch):
    # WHY: 첫 요청까지 간격만큼 기다리면 모든 스텝이 매 런마다 공짜로 느려진다 — 슬롯이
    #      0 에서 시작하므로 첫 콜은 즉시 나가야 한다.
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(b"{}"))
    client = PoliteClient(min_interval=5.0)
    started = time.monotonic()
    client.get("https://x.example/y")
    assert time.monotonic() - started < 1.0


def test_stats_separate_send_wait_from_response_time(monkeypatch):
    # WHY(ALPHA-1124): 수집이 느려졌을 때 벤더 응답이 느린 것인지 발신 간격을 기다린 것인지는 합계
    #      시간만으로 가를 수 없다 — 둘이 다른 칸에 쌓여야 원인을 고를 수 있다.
    client, _, _ = _virtual_clock_client(monkeypatch, interval=1.0, rtt=0.25)

    for _ in range(3):
        client.get("https://x.example/y")

    # 첫 콜은 안 기다리고, 뒤 두 콜은 간격 1.0 에서 응답 0.25 를 뺀 0.75 씩 기다린다
    assert client.stats.drain() == {
        "attempts": 3, "rtt_ms": 750, "rtt_max_ms": 250, "pace_wait_ms": 1500,
    }
    # 읽으면 비운다 — 다음 구간 요약에 앞 구간이 섞이면 구간별 비교가 무의미해진다
    assert client.stats.drain() == {}


def test_stats_count_every_send_and_name_failures_without_their_text(monkeypatch):
    # WHY(ALPHA-1124): 재시도로 성공한 호출은 결과만 보면 정상 1건이다. 발신 수·재시도 대기·실패 종류가
    #      따로 남아야 호출이 늘어난 것을 볼 수 있다. 실패는 상태코드·클래스명으로만 남긴다 — 예외
    #      문자열에는 URL·프록시 자격증명이 들어갈 수 있다.
    secret = "proxy-password=sensitive"
    outcomes = [
        lambda req: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 503, secret, {}, io.BytesIO(b""))),
        lambda req: (_ for _ in ()).throw(urllib.error.URLError(TimeoutError(secret))),
        lambda req: _Resp(b"[]"),
    ]
    client = _client(monkeypatch, lambda req: outcomes.pop(0)(req))

    assert client.request("GET", "https://x.example/y?key=" + secret) == "[]"

    stats = client.stats.drain()
    assert {key: stats[key] for key in stats if not key.endswith("_ms")} == {
        "attempts": 3, "transport_retry": 2, "err_http_503": 1, "err_TimeoutError": 1,
    }
    assert stats["transport_backoff_ms"] == 3000  # 1초 + 2초 — 재시도 대기는 응답 소요와 다른 칸이다
    assert secret not in str(stats)


# ---------- 연결 재사용(keep_alive) — 대역이 아니라 루프백 서버로 실제 소켓을 본다 ----------


class _LoopbackServer:
    """루프백 HTTP/1.1 서버 — 받은 연결 수와 요청 수를 센다.

    `statuses` 를 차례로 답한다(다 쓰면 200). 연결 하나에서 `per_conn` 건을 답하면 헤더로 알리지 않고
    닫는다 — 쉬는 연결을 서버가 먼저 닫는 경우의 모양이다. `truncate` 면 본문을 선언한 길이보다 짧게
    보내고 닫는다.
    """

    def __init__(self, statuses=(), per_conn=None, truncate=False):
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.05)  # stop() 을 알아채도록 accept 를 짧게 끊는다
        self.url = f"http://127.0.0.1:{self._listener.getsockname()[1]}/y"
        self._statuses, self._per_conn, self._truncate = list(statuses), per_conn, truncate
        self.connections = self.requests = 0
        self.closed = threading.Event()  # 서버가 연결을 닫으면 켠다
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stopped.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            self.connections += 1
            # 연결마다 스레드 — 살아 있는 재사용 연결이 다음 연결의 응답을 막지 않게
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()
        self._listener.close()

    def _answer(self, conn):
        reader, served = conn.makefile("rb"), 0
        while self._per_conn is None or served < self._per_conn:
            if not reader.readline():
                break  # 클라이언트가 닫았다
            while reader.readline() not in (b"\r\n", b""):
                pass  # 헤더 끝까지
            self.requests += 1
            served += 1
            status = self._statuses.pop(0) if self._statuses else 200
            length = 9 if self._truncate else 2
            conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Length: {length}\r\n\r\n[]".encode())
            if self._truncate:
                break
        reader.close()
        conn.close()
        self.closed.set()

    def stop(self):
        self._stopped.set()
        self._thread.join(2)


@pytest.fixture
def loopback():
    """루프백 서버를 띄우는 팩토리 — 테스트가 끝나면 리스너와 스레드를 정리한다."""
    servers = []

    def start(**kwargs):
        servers.append(_LoopbackServer(**kwargs))
        return servers[-1]

    yield start
    for server in servers:
        server.stop()


def _live_client(**kwargs):
    client = PoliteClient(min_interval=0, **kwargs)
    client._sleep = lambda secs: None
    return client


def _counts(client):
    return {key: value for key, value in client.stats.drain().items() if not key.endswith("_ms")}


def test_keep_alive_sends_every_call_over_one_connection(loopback):
    # WHY(ALPHA-1153): 분 가격은 1분에 수백 종목을 한 호스트에 묻는다. 호출마다 TCP·TLS 를 새로 맺으면 그
    #      수립 시간이 호출 간격보다 길어져 한 창이 1분을 넘긴다 — 재사용을 켠 클라이언트는 연결 하나로
    #      보내야 한다. 끄면(기본) 종전대로 호출마다 새 연결이다 — 다른 벤더의 운반은 바뀌지 않는다.
    reused, fresh = loopback(), loopback()
    keep, plain = _live_client(keep_alive=True), _live_client()
    for server, client in ((reused, keep), (fresh, plain)):
        for _ in range(3):
            assert client.get(server.url) == "[]"

    assert (reused.connections, reused.requests) == (1, 3)
    assert (fresh.connections, fresh.requests) == (3, 3)
    # 새로 맺은 횟수가 계측에 남는다 — 운영에서 재사용이 되는지는 이 값으로만 볼 수 있다
    assert _counts(keep) == {"attempts": 3, "connects": 1}


def test_keep_alive_reconnects_before_sending_when_the_server_closed_the_idle_connection(loopback):
    # WHY: 서버는 쉬는 연결을 알리지 않고 닫는다(창과 창 사이 공백). 그 연결에 요청을 실으면 응답 없이
    #      끊긴 것으로 보여 끊김 재시도 예산(60초 5회)을 쓰고, 예산이 바닥나면 창이 실패한다. 닫힌 것을
    #      **보내기 전에** 알아채 새로 맺어야 한다 — 실패한 발신도 재시도도 없어야 한다.
    server = loopback(per_conn=1)
    client = _live_client(keep_alive=True)

    assert client.get(server.url) == "[]"
    assert server.closed.wait(5)
    assert client.get(server.url) == "[]"

    assert server.connections == 2
    assert _counts(client) == {"attempts": 2, "connects": 2}


def test_keep_alive_reports_http_errors_like_urlopen(loopback):
    # WHY: 재시도·중단 분기는 `urlopen` 의 예외 모양에 걸려 있다. 재사용 경로가 4xx 를 정상 응답으로
    #      돌려주면 키 오류가 빈 수집으로 커밋되고, 5xx 를 올리지 않으면 재시도가 사라진다.
    server = loopback(statuses=[503, 200, 403])
    client = _live_client(keep_alive=True)

    assert client.get(server.url) == "[]"  # 503 → 재시도 → 200
    with pytest.raises(StopFetch) as stop:
        client.get(server.url)

    assert (stop.value.status, stop.value.body) == (403, "[]")
    # 본문을 끝까지 읽은 오류 응답 뒤에도 같은 연결을 쓴다(connects 1)
    assert _counts(client) == {
        "attempts": 3, "connects": 1, "transport_retry": 1, "err_http_503": 1, "err_http_403": 1,
    }
    assert server.connections == 1


def test_keep_alive_stops_on_4xx_even_when_its_body_is_cut(loopback):
    # WHY: 4xx 는 키·권한 문제라 즉시 중단해야 하고, 그 판정은 상태코드다. 본문이 중간에 끊겼다고 끊김
    #      재시도로 빠지면 같은 키로 다시 두드리고, 소진되면 종목 결손으로 접혀 다음 종목까지 계속 묻는다.
    server = loopback(statuses=[403], truncate=True)
    client = _live_client(keep_alive=True)

    with pytest.raises(StopFetch) as stop:
        client.get(server.url)

    assert (stop.value.status, server.requests) == (403, 1)


def test_keep_alive_never_sends_https_over_a_plain_connection(loopback, monkeypatch):
    # WHY: 연결을 호스트로만 찾으면 같은 호스트의 http 연결에 https 요청이 실려 인증 헤더가 평문으로 나간다.
    import http.client

    server = loopback()
    client = _live_client(keep_alive=True)
    assert client.get(server.url) == "[]"

    tls_connections = []

    class _Tls(http.client.HTTPConnection):
        """TLS 없이 같은 루프백 서버로 잇는 대역 — https 요청이 어느 연결 종류를 골랐는지만 본다."""

        def __init__(self, host, **kwargs):
            tls_connections.append(host)
            super().__init__(host, **kwargs)

    monkeypatch.setattr("http.client.HTTPSConnection", _Tls)
    assert client.get(server.url.replace("http://", "https://")) == "[]"

    assert len(tls_connections) == 1
    assert server.connections == 2


def test_keep_alive_treats_a_failed_send_as_a_network_failure():
    # WHY: `urlopen` 은 발신 단계 실패(연결 거부·DNS)를 URLError 로 감싸고, 그건 재시도 뒤 고정 코드로
    #      끝난다. 원형 그대로 올리면 '응답 없이 끊긴 연결' 분기로 빠져 재시도가 1회로 줄고 예외 원문이
    #      호출자에게 나간다.
    unused = socket.socket()
    unused.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{unused.getsockname()[1]}/y"
    unused.close()  # listen 한 적 없는 포트 — 연결이 거부된다
    client = _live_client(keep_alive=True)

    with pytest.raises(SafeFailureError):
        client.get(url)

    assert _counts(client) == {
        "attempts": 4, "connects": 4, "transport_retry": 3, "err_ConnectionRefusedError": 4,
    }


def test_keep_alive_leaves_requests_with_a_body_on_the_old_path(monkeypatch):
    # WHY: 본문 있는 요청은 `urlopen` 이 붙이는 헤더(Content-Type 기본값)까지 벤더가 보는 모양의 일부다.
    #      재사용 경로는 그걸 옮기지 않았으므로, 켜도 이 요청들은 종전 경로로 가야 한다.
    client = _client(monkeypatch, lambda req: _Resp(b"ok"))
    client.keep_alive = True

    assert client.request("POST", "https://x.example/y", data=b"a=1") == "ok"
