"""KrxAuth 테스트 — 2단계 로그인·CD001 게이트·JSESSIONID 추출·run 당 1회 캐시.

로그인은 보안 경로라(자격증명 평문 POST) 조용한 실패가 없어야 한다 — CD001 아닌 응답은
fail-loud, 성공은 승격 JSESSIONID 를 정확히 돌려주는지 검증한다. urllib+cookiejar 는
가짜 opener/jar 로 대체해 실제 네트워크 없이 분기만 확인한다.
"""

import json
import traceback
import urllib.error
import urllib.request

import pytest

from data_pipeline.failures import SafeFailureError
from data_pipeline.sources import krx_auth
from data_pipeline.sources.krx_auth import KrxAuth


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeOpener:
    """login.jsp GET(빈 본문) → MDCCOMS001D1.cmd POST(로그인 JSON) 순으로 응답한다."""

    def __init__(self, login_json: dict):
        self._login_json = login_json
        self.opened = []

    def open(self, req, timeout=None):
        self.opened.append(req.full_url)
        if req.data is None:  # 1콜: login.jsp GET
            return _Resp(b"")
        return _Resp(json.dumps(self._login_json).encode("utf-8"))  # 2콜: 로그인 POST


class _FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


def _patch(monkeypatch, login_json, cookies):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _FakeOpener(login_json))
    monkeypatch.setattr(krx_auth, "CookieJar", lambda: list(cookies))


def test_login_success_returns_promoted_jsessionid(monkeypatch):
    # WHY: CD001(정상) 로그인 후 cookiejar 의 승격 JSESSIONID 를 그대로 세션으로 돌려줘야
    #      getJsonData 가 게이트를 통과한다.
    _patch(monkeypatch, {"_error_code": "CD001"}, [_FakeCookie("JSESSIONID", "PROMOTED1")])
    auth = KrxAuth("id", "pw")
    assert auth.session() == "PROMOTED1"


def test_session_caches_login(monkeypatch):
    # WHY: 로그인은 run 당 1회 — 두 번째 session()이 재로그인하지 않고 캐시를 준다
    #      (ETF마다 로그인하면 계정 잠금·CD011 위험). build_opener 호출 횟수로 검증한다.
    logins = 0

    def _build(*a):
        nonlocal logins
        logins += 1
        return _FakeOpener({"_error_code": "CD001"})

    monkeypatch.setattr(urllib.request, "build_opener", _build)
    monkeypatch.setattr(krx_auth, "CookieJar", lambda: [_FakeCookie("JSESSIONID", "X")])
    auth = KrxAuth("id", "pw")
    auth.session()
    auth.session()
    assert logins == 1  # 두 번째 session()은 캐시 — 재로그인 없음


def test_duplicate_login_fails_loud(monkeypatch):
    # WHY: CD011(중복 로그인)은 계정 파이프라인 전용 위반 — 조용한 기본값 없이 fail-loud.
    _patch(monkeypatch, {"_error_code": "CD011", "_error_msg": "중복 로그인"},
           [_FakeCookie("JSESSIONID", "X")])
    with pytest.raises(RuntimeError, match="CD011"):
        KrxAuth("id", "pw").session()


def test_live_password_change_response_drops_account_fields(monkeypatch):
    # WHY(ALPHA-1064): 실응답은 _error_message와 함께 MBR_NO를 보낸다. 예외 문자열을 그대로
    # collection_log에 쓰는 경로에서 계정 식별자가 장기 보존됐으므로 허용 코드만 남겨야 한다.
    sensitive = "account-identifier-must-not-leak"
    _patch(monkeypatch, {
        "previousMemberYn": False,
        "MDC_MBR_TP_CD": "P",
        "MBR_NO": sensitive,
        "_error_code": "CD010",
        "_error_message": "패스워드 변경 필요",
    }, [_FakeCookie("JSESSIONID", "X")])

    with pytest.raises(SafeFailureError) as caught:
        KrxAuth("id", "pw").session()

    assert caught.value.detail() == {
        "category": "AUTHENTICATION",
        "code": "KRX_CD010",
        "summary": "KRX 패스워드 변경 필요",
    }
    assert sensitive not in str(caught.value)


def test_login_network_failure_is_classified_without_exception_detail(monkeypatch):
    # WHY(ALPHA-1064): 인증 거부와 일시 네트워크 장애는 운영 대응이 다르다. URL/프록시가
    # 들어갈 수 있는 URLError 원문은 버리고 재시도 소진 분류만 전달한다.
    secret = "proxy-password=sensitive"

    class FailingOpener:
        def open(self, req, timeout=None):
            raise urllib.error.URLError(secret)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: FailingOpener())
    with pytest.raises(SafeFailureError) as caught:
        KrxAuth("id", "pw").session()

    assert caught.value.detail()["category"] == "TRANSIENT"
    assert secret not in str(caught.value)
    assert secret not in "".join(traceback.format_exception(caught.value))


def test_login_http_forbidden_is_auth_without_response_detail(monkeypatch):
    # WHY(ALPHA-1064): HTTPError는 URLError의 하위 타입이다. 403을 일시 장애로 잡으면
    # 운영자가 자격증명 문제에 무의미한 재시도를 하므로 인증 실패로 분리해야 한다.
    secret_url = "https://user:token@data.krx.example/login"

    class FailingOpener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(secret_url, 403, "secret response", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: FailingOpener())
    with pytest.raises(SafeFailureError) as caught:
        KrxAuth("id", "pw").session()

    assert caught.value.detail() == {
        "category": "AUTHENTICATION",
        "code": "HTTP_FORBIDDEN",
        "summary": "공급자 접근 거부",
    }
    assert secret_url not in str(caught.value)
    assert secret_url not in "".join(traceback.format_exception(caught.value))


def test_success_without_cookie_fails_loud(monkeypatch):
    # WHY: CD001 인데 JSESSIONID 쿠키가 없으면(스키마 드리프트) 조용한 성공으로 위장하지 않는다.
    _patch(monkeypatch, {"_error_code": "CD001"}, [])
    with pytest.raises(RuntimeError, match="JSESSIONID"):
        KrxAuth("id", "pw").session()
