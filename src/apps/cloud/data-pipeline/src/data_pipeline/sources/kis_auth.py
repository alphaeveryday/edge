"""KIS(한국투자) OAuth 토큰 발급 — run 당 1회 발급·메모리 캐시.

KIS 는 잦은 재발급을 분당 한도로 차단하므로, 종목마다 발급하지 않고 run 시작 시 한 번
받아 그 run 내내 재사용한다(가격 어댑터가 fetch 시작에서 `token()` 을 한 번 호출).
토큰은 디스크·레이크에 남기지 않는다(메모리만) — 앱키/시크릿은 env 로만 주입한다.

도메인은 env(prod|vps)로 갈린다. 경로·tr_id 는 호출 어댑터가 고정한다(여긴 인증만).
"""

from __future__ import annotations

import json
import logging
import random

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
# 유량 제한은 **오류 코드로** 가른다 — 같은 403 이라도 잘못된 앱키·권한 문제는 기다려도
# 안 풀리므로 61초를 낭비하고 같은 실패를 반복하면 안 된다(edge-review 지적).
TOKEN_RATE_LIMIT_STATUS = 403
TOKEN_RATE_LIMIT_CODE = "EGW00133"
TOKEN_RATE_LIMIT_WAIT_SEC = 61  # "1분당 1회" + 시계 오차 여유
# 대기 뒤 **지터**를 더한다. 같은 앱키를 쓰는 두 브랜치가 동시에 403 을 맞으면(직전 1분 내
# 발급이 있었던 경우 — 빠른 수동 재실행·실행 겹침) 고정 간격으로는 둘이 같은 시각에 다시
# 깨어나 충돌을 그대로 재생산한다. 지터가 순서를 갈라 한쪽이 먼저 발급하게 한다.
TOKEN_RATE_LIMIT_JITTER_SEC = 20
# 재시도 횟수 — **동시에 토큰을 발급하는 브랜치 수보다 커야 한다.** 발급이 분당 1회라 N개
# 브랜치가 동시에 시작하면 최악의 경우 마지막 브랜치가 N-1 분을 기다려야 자기 차례가 온다.
# 현재 동시 발급자는 SFN raw 페이즈의 kis 브랜치 3개다(CollectKisPrice·CollectKisNav·
# CollectKisEtfProfile, ALPHA-462). 여기에 직전 1분 내 발급이 겹칠 수 있어 한 칸 더 둔다.
# **kis 브랜치를 추가하면 이 값도 함께 올려라** — 안 올리면 새 브랜치가 상시 partial 이 된다
# (edge-review 지적). 무한 대기는 금지(막히면 런이 실패로 드러나야 한다, Rule 12).
TOKEN_RATE_LIMIT_MAX_RETRY = 4


def domain_for(env: str) -> str:
    """env(prod|vps) → REST 도메인. 알 수 없는 env 는 fail-loud(조용한 기본값 금지)."""
    try:
        return DOMAINS[env]
    except KeyError as exc:
        raise ValueError(f"알 수 없는 KIS env: {env!r} (prod|vps)") from exc


def _is_rate_limited(exc: StopFetch) -> bool:
    """토큰 발급 4xx 가 '분당 1회' 유량 제한인가 — 상태코드 403 + 본문 오류코드 EGW00133.

    코드로 가리는 이유: 403 이라고 전부 대기 대상은 아니다(잘못된 앱키·권한도 4xx 다).
    벤더가 코드를 바꾸면 여기서 못 잡고 그 런은 실패로 드러난다 — 조용히 계속되는 것보다
    낫다(Rule 12). 실측 본문: {"error_code":"EGW00133","error_description":"접근토큰 발급
    잠시 후 다시 시도하세요(1분당 1회)"}.
    """
    return (
        getattr(exc, "status", None) == TOKEN_RATE_LIMIT_STATUS
        and TOKEN_RATE_LIMIT_CODE in getattr(exc, "body", "")
    )


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
            for attempt in range(TOKEN_RATE_LIMIT_MAX_RETRY + 1):
                try:
                    self._token = self._issue()
                    break
                except StopFetch as exc:
                    # 유량 제한이 아닌 4xx(잘못된 키·권한)는 기다려도 안 풀린다 — 즉시 올린다.
                    # 재시도를 다 쓴 뒤에도 막혀 있으면 그대로 올려 런을 실패로 드러낸다.
                    if not _is_rate_limited(exc) or attempt == TOKEN_RATE_LIMIT_MAX_RETRY:
                        raise
                    wait = TOKEN_RATE_LIMIT_WAIT_SEC + random.uniform(0, TOKEN_RATE_LIMIT_JITTER_SEC)
                    logger.warning(
                        "KIS 토큰 발급이 분당 1회 제한에 걸렸다 — %.1f초 대기 후 재시도 "
                        "(%d/%d, 같은 앱키를 쓰는 다른 스텝이 방금 발급했을 수 있다)",
                        wait, attempt + 1, TOKEN_RATE_LIMIT_MAX_RETRY,
                    )
                    self.client._sleep(wait)
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
