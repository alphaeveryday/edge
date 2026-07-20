"""KIS 인증 테스트 — 토큰 run 당 1회 발급·메모리 캐시, 실패 fail-loud (네트워크 없음)."""

import json

import pytest

from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_auth import KisAuth, domain_for


class FakeClient:
    """POST 토큰 요청을 세는 스텁. body 를 돌려주거나 raise_exc 를 던진다."""

    _sleep = staticmethod(lambda secs: None)

    def __init__(self, body: str = "", raise_exc: Exception | None = None):
        self.body = body
        self.raise_exc = raise_exc
        self.calls = 0

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls += 1
        if self.raise_exc:
            raise self.raise_exc
        return self.body


def _token_body(tok: str = "tok-123") -> str:
    return json.dumps({"access_token": tok, "access_token_token_expired": "2026-07-07 00:00:00"})


def test_token_issued_once_and_cached():
    # WHY: KIS 는 잦은 재발급을 분당 한도로 차단한다 — 여러 번 요청해도 실제 발급은 1회여야
    #      종목마다 토큰을 두드리지 않는다(run 당 1회 규약).
    client = FakeClient(body=_token_body("tok-abc"))
    auth = KisAuth("key", "secret", client, env="prod")

    assert auth.token() == "tok-abc"
    assert auth.token() == "tok-abc"  # 캐시 재사용
    assert client.calls == 1  # 발급은 단 한 번


def test_missing_access_token_fails_loud():
    # WHY: 200 인데 토큰이 없으면(잘못된 grant 등) 조용히 빈 토큰으로 넘기면 이후 전 종목이
    #      401 을 두드린다 — 발급 단계에서 명시적으로 실패해야 한다.
    client = FakeClient(body=json.dumps({"error_description": "invalid grant"}))
    with pytest.raises(RuntimeError):
        KisAuth("key", "secret", client, env="prod").token()


def test_key_error_propagates_as_stop_fetch():
    # WHY: 앱키 오류(4xx)는 client 가 StopFetch 로 올린다 — 재시도·격리 대상이 아니라
    #      즉시 중단이어야 무의미한 호출을 막는다. auth 가 이를 삼키지 않고 전파한다.
    client = FakeClient(raise_exc=StopFetch("HTTP 403"))
    with pytest.raises(StopFetch):
        KisAuth("key", "secret", client, env="prod").token()


def test_unknown_env_fails_loud():
    # WHY: 알 수 없는 env 를 조용히 prod 로 기본화하면 모의/실전을 잘못 친다 — fail loud.
    with pytest.raises(ValueError):
        domain_for("staging")


def test_domain_selects_by_env():
    # WHY: prod/vps 도메인이 뒤바뀌면 실전 키로 모의 서버(또는 반대)를 쳐 데이터가 오염된다.
    assert domain_for("prod").endswith(":9443")
    assert domain_for("vps").endswith(":29443")


def test_토큰_403_은_대기후_1회_재시도한다(monkeypatch):
    # WHY: SFN raw 페이즈의 CollectKisPrice·CollectKisNav 는 같은 앱키를 쓰는 별개 Parallel
    #      브랜치라 거의 동시에 토큰을 발급한다. KIS 는 앱키당 분당 1회만 발급하므로(403
    #      EGW00133, 2026-07-20 실측) 재시도가 없으면 매 런에서 한쪽이 죽어 파이프라인이
    #      상시 partial 이 된다(ALPHA-458). 토큰은 24h 유효라 기다리면 반드시 풀린다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth, TOKEN_RATE_LIMIT_WAIT_SEC

    slept = []

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, data=None, decode=True):
            self.calls += 1
            if self.calls == 1:
                raise StopFetch("HTTP 403: 수집 중단", status=403)
            return json.dumps({"access_token": "TOKEN", "expires_in": 86400})

        def _sleep(self, seconds):
            slept.append(seconds)

    client = _Client()
    auth = KisAuth("k", "s", client)

    assert auth.token() == "TOKEN"
    assert client.calls == 2                      # 대기 후 정확히 1회 재시도
    assert slept == [TOKEN_RATE_LIMIT_WAIT_SEC]   # 분당 제한이 풀릴 만큼 기다린다
    assert auth.token() == "TOKEN" and client.calls == 2  # 이후엔 캐시(run 당 1회 규약)


def test_403_이_아닌_4xx_는_기다리지_않고_올린다():
    # WHY: 잘못된 앱키(401 등)는 기다려도 안 풀린다 — 61초를 낭비하고 같은 실패를 반복하는 대신
    #      즉시 드러내야 한다(Rule 12). 유량 제한만 대기 대상이다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth

    class _Client:
        def request(self, *a, **k):
            raise StopFetch("HTTP 401: 수집 중단", status=401)

        def _sleep(self, seconds):
            raise AssertionError("401 에는 대기하면 안 된다")

    with pytest.raises(StopFetch):
        KisAuth("k", "s", _Client()).token()


def test_재시도_후에도_403_이면_포기한다():
    # WHY: 무한 대기 금지 — 두 번째도 막히면 그 런은 실패로 드러내고 스케줄러가 알게 한다.
    from data_pipeline.sources.http import StopFetch
    from data_pipeline.sources.kis_auth import KisAuth

    class _Client:
        def __init__(self):
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            raise StopFetch("HTTP 403: 수집 중단", status=403)

        def _sleep(self, seconds):
            pass

    client = _Client()
    with pytest.raises(StopFetch):
        KisAuth("k", "s", client).token()
    assert client.calls == 2
