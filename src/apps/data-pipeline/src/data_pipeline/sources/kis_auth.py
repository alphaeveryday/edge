"""KIS(한국투자) OAuth 토큰 발급 — run 당 1회 발급·메모리 캐시.

KIS 는 잦은 재발급을 분당 한도로 차단하므로, 종목마다 발급하지 않고 run 시작 시 한 번
받아 그 run 내내 재사용한다(가격 어댑터가 fetch 시작에서 `token()` 을 한 번 호출).
토큰은 디스크·레이크에 남기지 않는다(메모리만) — 앱키/시크릿은 env 로만 주입한다.

도메인은 env(prod|vps)로 갈린다. 경로·tr_id 는 호출 어댑터가 고정한다(여긴 인증만).
"""

from __future__ import annotations

import json
import logging

from .http import PoliteClient, StopFetch

# env → REST 도메인. 과거 분봉·실전 시세는 prod 권장, 모의는 vps.
logger = logging.getLogger(__name__)

DOMAINS = {
    "prod": "https://openapi.koreainvestment.com:9443",
    "vps": "https://openapivts.koreainvestment.com:29443",
}
TOKEN_PATH = "/oauth2/tokenP"

# KIS 는 앱키당 **분당 1회**만 토큰을 발급하고, 초과분은 HTTP 403(EGW00133 "접근토큰 발급
# 잠시 후 다시 시도하세요(1분당 1회)")으로 거절한다(2026-07-20 라이브 실측). 발급된 토큰은
# 24시간(expires_in=86400) 유효하므로 이건 영구 오류가 아니라 **대기하면 풀리는 유량 제한**이다.
#
# 이게 실제로 문제가 되는 지점: SFN raw 페이즈의 CollectKisPrice·CollectKisNav 는 같은 앱키를
# 쓰는 별개 Parallel 브랜치라 거의 동시에 발급을 시도한다 — 재시도가 없으면 매 런에서 한쪽이
# 죽어 파이프라인이 상시 partial 이 된다(ALPHA-458). 사람이 1분 안에 수동 실행한 경우도 같다.
TOKEN_RATE_LIMIT_STATUS = 403
TOKEN_RATE_LIMIT_WAIT_SEC = 61  # "1분당 1회" + 시계 오차 여유


def domain_for(env: str) -> str:
    """env(prod|vps) → REST 도메인. 알 수 없는 env 는 fail-loud(조용한 기본값 금지)."""
    try:
        return DOMAINS[env]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 KIS env: {env!r} (prod|vps)") from exc


class KisAuth:
    """앱키/시크릿 → 액세스 토큰. 최초 `token()` 호출에서 1회 발급 후 메모리 캐시."""

    def __init__(self, app_key: str, app_secret: str, client: PoliteClient, env: str = "prod"):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = domain_for(env)
        self.client = client
        self._token: str | None = None

    def token(self) -> str:
        """캐시된 토큰이 있으면 재사용, 없으면 1회 발급한다(run 당 1회 규약).

        403(분당 1회 제한)은 대기하면 풀리는 유량 제한이라 **한 번만** 기다렸다 재시도한다 —
        같은 앱키를 쓰는 다른 스텝이 방금 발급했을 때가 전형이다(SFN 병렬 브랜치). 그 밖의
        4xx(잘못된 키 등)는 기다려도 안 풀리므로 그대로 올린다. 재시도 후에도 403 이면
        포기한다(무한 대기 금지 — 실패는 스텝이 fail-loud 로 드러낸다).
        """
        if self._token is None:
            try:
                self._token = self._issue()
            except StopFetch as exc:
                if getattr(exc, "status", None) != TOKEN_RATE_LIMIT_STATUS:
                    raise
                logger.warning(
                    "KIS 토큰 발급이 분당 1회 제한에 걸렸다 — %d초 대기 후 1회 재시도"
                    "(같은 앱키를 쓰는 다른 스텝이 방금 발급했을 수 있다)",
                    TOKEN_RATE_LIMIT_WAIT_SEC,
                )
                self.client._sleep(TOKEN_RATE_LIMIT_WAIT_SEC)
                self._token = self._issue()
        return self._token

    def _issue(self) -> str:
        body = json.dumps(
            {
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            }
        ).encode("utf-8")
        # 키 오류(4xx)는 client 가 StopFetch 로 올린다 — 재시도로 두드리지 않는다.
        raw = self.client.request(
            "POST",
            self.base + TOKEN_PATH,
            headers={"content-type": "application/json"},
            data=body,
            decode=True,
        )
        data = json.loads(raw)
        access_token = data.get("access_token")
        if not access_token:
            # 200 인데 토큰이 없으면(예 잘못된 grant) 조용히 넘기지 않고 fail-loud.
            detail = data.get("error_description") or data.get("msg1") or data
            raise RuntimeError(f"KIS 토큰 발급 실패: {detail}")
        return access_token
