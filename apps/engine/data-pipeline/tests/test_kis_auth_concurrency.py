"""KIS 토큰 동시 만료·갱신(ALPHA-1087) — 분 워커 동시 요청에서 토큰 발급이 몰리지 않아야 한다.

발급은 분당 1회 제한(403 EGW00133)이 걸린 별도 축이라, 같은 프로세스의 두 스레드가 같은 만료를 보고
각자 폐기·재발급하면 두 번째가 1분을 기다리고 window 가 늦는다. 여기서 고정하는 계약:
실패는 그 요청이 **쓴 토큰**과 묶이고, 지금 토큰과 같을 때만 폐기되며, 갱신은 한 스레드만 하고,
갱신 실패는 그 갱신을 기다리던 스레드에게만 같은 실패로 전달된다(허용 저장소 장애는 캐시하지 않는다).
보장 범위는 **프로세스 안**이다 — 컨테이너 사이 중복 발급은 공유 캐시·403 대기가 맡는다(test_kis_auth).
"""

import json
import threading
import time

import pytest

from data_pipeline.failures import SafeFailureError
from data_pipeline.sources import kis_auth
from data_pipeline.sources.call_budget import CallBudgetUnavailable
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_minute import KisMinuteClient


class IssuingClient:
    """토큰 발급(POST)은 t1, t2… 를 순서대로 내고 횟수를 센다. GET 은 script 로 응답한다."""

    def __init__(self, issue_delay=0.0, fail=None, get=None):
        self.issued = 0
        self.issue_delay, self.fail, self.get = issue_delay, fail, get
        self.lock = threading.Lock()

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if method == "POST":
            time.sleep(self.issue_delay)
            with self.lock:
                if self.fail:
                    raise self.fail
                self.issued += 1
                return json.dumps({"access_token": f"t{self.issued}", "expires_in": 86400})
        return self.get(headers)

    @staticmethod
    def _sleep(s):
        pass


@pytest.fixture(autouse=True)
def no_shared_cache(monkeypatch):
    monkeypatch.delenv("KIS_TOKEN_CACHE_PARAM", raising=False)


def auth(client):
    return kis_auth.KisAuth("k", "s", client)


def test_two_requests_failing_with_the_same_expired_token_refresh_once():
    c = IssuingClient(issue_delay=0.05)
    a = auth(c)
    assert a.token() == "t1"
    got, bar = [], threading.Barrier(2)

    def worker():
        bar.wait()
        a.invalidate("t1")
        got.append(a.token())
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert got == ["t2", "t2"] and c.issued == 2


def test_late_failure_of_the_old_token_does_not_drop_the_new_one():
    c = IssuingClient()
    a = auth(c)
    a.token()
    a.invalidate("t1"); assert a.token() == "t2"      # 첫 요청이 갱신
    a.invalidate("t1")                                  # 늦게 도착한 옛 토큰 실패
    assert a.token() == "t2" and c.issued == 2


def test_request_arriving_during_refresh_waits_and_reuses():
    c = IssuingClient(issue_delay=0.1)
    a = auth(c)
    out = []
    ts = [threading.Thread(target=lambda: out.append(a.token())) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out == ["t1"] * 3 and c.issued == 1


@pytest.mark.parametrize("failure", [StopFetch("HTTP 401", status=401, body="invalid appkey"),
                                     SafeFailureError("NETWORK_RETRY_EXHAUSTED")])
def test_refresh_failure_is_shared_with_waiters_then_retried(failure):
    """발급이 실패하는 동안 기다리던 스레드는 같은 실패를 받는다(발급을 줄줄이 다시 두드리지 않는다).
    그 뒤에 온 호출은 다시 시도한다 — 시간 냉각으로 막으면 후속 unit 전체가 즉시 실패해 window 가 결손으로 확정된다."""
    c = IssuingClient(issue_delay=0.1, fail=failure)
    a = auth(c)
    attempts = {"n": 0}
    real = a._resolve

    def counting():
        attempts["n"] += 1
        return real()
    a._resolve = counting
    errs = []

    def worker():
        try:
            a.token()
        except Exception as e:  # noqa: BLE001
            errs.append(type(e))
    ts = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert errs == [type(failure)] * 3 and attempts["n"] == 1
    c.fail = None
    assert a.token() == "t1" and attempts["n"] == 2   # 실패 뒤의 호출은 곧바로 재시도한다


def test_budget_outage_during_issue_is_not_cached():
    c = IssuingClient(fail=CallBudgetUnavailable("OperationalError"))
    a = auth(c)
    with pytest.raises(CallBudgetUnavailable):
        a.token()
    c.fail = None
    assert a.token() == "t1"                            # 저장소가 복구되면 바로 발급된다


def test_minute_client_threads_share_one_refresh_on_expiry():
    """실제 분봉 어댑터 경로: 두 스레드가 같은 만료 토큰(t1)으로 실패 → 발급은 한 번 더(t2)만."""
    expired = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
    ok = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok", "output2": []}

    def get(headers):
        time.sleep(0.02)
        return json.dumps(expired if headers["authorization"] == "Bearer t1" else ok)
    c = IssuingClient(issue_delay=0.05, get=get)
    client = KisMinuteClient("k", "s", c)
    client.auth.token()
    bar = threading.Barrier(2)
    errs = []

    def worker(sym):
        bar.wait()
        try:
            client._rows(sym, "103000")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=worker, args=(s,)) for s in ("000001", "000002")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs and c.issued == 2
