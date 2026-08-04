"""super-admin-api 클라이언트 — 분봉 설명 자동 회수(ALPHA-746)의 집행 경로.

엔진이 explanation_result 를 직접 UPDATE 하지 않는 이유: 무효화는 WITHDRAWN 전이 +
tenant_delivery INVALIDATION 발번 + 감사 로그가 한 트랜잭션이어야 하고(ALPHA-440),
그 발화자는 super-admin-api 하나다 — 발화자가 둘이 되면 advisory lock 규약과 감사
원장이 갈린다. 인증은 세션 쿠키(login → JSESSIONID)다.

stdlib urllib 를 쓴다 — 이 패키지에 HTTP 라이브러리 의존성이 없고(DeepSeekClient 와
같은 결), 하루 수십 건 이하의 물량에 커넥션 풀은 과잉이다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.cookiejar import CookieJar


class SuperAdminUnavailableError(RuntimeError):
    """일시 실패(연결·5xx·예상 밖 상태) — 호출자가 큐 재배달로 올린다.

    invalidate 는 멱등이라(409 = 이미 무효) 재배달 재실행이 안전하다. 반쯤 회수하고
    성공으로 접으면 남은 설명이 노출된 채 조용히 남는다(Rule 12).
    """


class SuperAdminClient:
    """login 세션을 쥔 얇은 클라이언트 — 회수 한 배치 동안만 산다."""

    def __init__(self, base_url: str, email: str, password: str, *, timeout: int = 10) -> None:
        self._base = base_url.rstrip("/")
        self._email = email
        self._password = password
        self._timeout = timeout
        # 세션 쿠키 유지 — login 이 심는 쿠키로 이후 invalidate 가 인증된다(AdminAuthFilter).
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def _post(self, path: str, body: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            # 4xx/5xx 도 (status, body)로 돌린다 — 상태 분기는 호출부 소관이다.
            return error.code, error.read().decode("utf-8", "replace")
        except urllib.error.URLError as error:
            raise SuperAdminUnavailableError(f"super-admin 연결 실패: {error}") from error

    def login(self) -> None:
        """세션 확보. 실패는 전부 transient — 401(자격 오류)도 재배달로 올린다.

        자격 오류를 성공으로 접으면 회수가 조용히 유실되고, 주입이 고쳐지면 재배달이
        낫게 한다(반복되면 DLQ 가 드러낸다).
        """
        status, body = self._post(
            "/api/v1/auth/login", {"email": self._email, "password": self._password}
        )
        if status != 200:
            raise SuperAdminUnavailableError(f"login 실패 status={status}: {body[:200]}")

    def invalidate(self, run_id: str, reason: str) -> str:
        """무효화 1건 — 'invalidated' | 'already_withdrawn' | 'not_found'.

        상태 분기는 AnalysisService.invalidate 의 결과 스위치와 1:1 이다:
        409(ADMN4090 게시 상태 아님)=재호출 멱등 신호 — 정상 / 404(ADMN4041 런 없음)=
        대상 없음 — 호출자가 경고 로그 / 그 외는 transient(재배달).
        """
        status, body = self._post(f"/api/v1/analyses/{run_id}/invalidate", {"reason": reason})
        if status == 200:
            return "invalidated"
        if status == 409:
            return "already_withdrawn"
        if status == 404:
            return "not_found"
        raise SuperAdminUnavailableError(
            f"invalidate 실패 status={status} run={run_id}: {body[:200]}"
        )
