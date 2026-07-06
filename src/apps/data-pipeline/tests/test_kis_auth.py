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
