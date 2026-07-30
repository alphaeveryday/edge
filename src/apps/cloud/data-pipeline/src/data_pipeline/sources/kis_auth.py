"""KIS(한국투자) OAuth 토큰 발급 — 컨테이너 간 공유 캐시(SSM) + run 당 1회 발급.

KIS 는 잦은 재발급을 분당 한도로 차단하므로, 종목마다 발급하지 않고 run 시작 시 한 번
받아 그 run 내내 재사용한다(가격 어댑터가 fetch 시작에서 `token()` 을 한 번 호출).
토큰은 디스크·레이크에 남기지 않는다(메모리 + SSM SecureString) — 앱키/시크릿은 env 로만
주입하고, **토큰 값은 로그·state 에 절대 남기지 않는다**(ADR-0009).

같은 앱키를 쓰는 SFN kis 브랜치 4개가 각자 발급하면 분당 1회 제한이 직렬 큐를 만들어
마지막 브랜치가 222초를 기다린다(2026-07-24 실런 실측, ALPHA-573). 토큰은 24시간 유효하므로
SSM SecureString 에 공유해 발급을 하루 1회로 수렴시킨다. 캐시 실패는 전부 발급 경로로
떨어뜨린다 — 최악이 현행 동작(성능만 손해)이고 데이터·가용성은 안 다친다.

도메인은 env(prod|vps)로 갈린다. 경로·tr_id 는 호출 어댑터가 고정한다(여긴 인증만).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import time

from ..ops.aws import ssm_client
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
# 현재 동시 발급자는 SFN raw 페이즈의 kis 브랜치 4개다(CollectKisPrice·CollectKisNav·
# CollectKisEtfProfile·CollectKisInvestor — 마지막은 ALPHA-482 로 추가). 여기에 직전 1분 내
# 발급이 겹칠 수 있어 한 칸 더 둔다.
# **kis 브랜치를 추가하면 이 값도 함께 올려라** — 안 올리면 새 브랜치가 상시 partial 이 된다
# (edge-review 지적). 무한 대기는 금지(막히면 런이 실패로 드러나야 한다, Rule 12).
# 아래 공유 캐시가 들어온 뒤로는 이 예산이 실제로 쓰이는 일이 드물다 — 403 을 맞은 쪽이
# 대기 대신 캐시를 다시 읽어 승자의 토큰을 가져가기 때문이다(캐시가 없는 로컬·폴백에서만 발화).
TOKEN_RATE_LIMIT_MAX_RETRY = 4

# ── 컨테이너 간 공유 캐시(SSM SecureString) ──
# 파라미터 이름은 terraform(data-pipeline 모듈)이 kis task-def env 로 주입한다. 없으면
# 캐시 없이 도는 게 정상이다 — 로컬 실행·단위테스트는 AWS 없이 돌아야 한다.
CACHE_PARAM_ENV = "KIS_TOKEN_CACHE_PARAM"
# 남은 유효시간이 이보다 짧으면 캐시를 안 쓴다. 수집 런 하나가 10분 안쪽이라, 런 도중
# 만료돼 전 종목이 401 을 두드리는 것보다 한 번 더 발급하는 편이 싸다.
CACHE_MIN_REMAINING_SEC = 900
# 상한 — KIS 토큰은 24시간(86400초) 유효하므로 그보다 먼 만료는 값이 손상됐다는 뜻이다.
# 상한이 없으면 손상된 큰 수 하나가 죽은 토큰을 영구히 유효로 만든다(edge-review 지적).
# 시계 오차 여유를 둔다 — 쓴 쪽과 읽는 쪽이 다른 컨테이너라 상한이 정확히 86400 이면 쓰는
# 쪽 시계가 몇 초만 앞서도 갓 발급한 정상 토큰이 미스가 된다(edge-review 지적).
CACHE_MAX_REMAINING_SEC = 86400 + 300
# 403 을 맞은 쪽이 승자의 SSM 쓰기를 기다리는 짧은 폴링. 403 은 '방금 누가 발급했다'는
# 신호지만, 그 승자가 응답을 받아 put_parameter 를 끝내기까지 1초 안팎의 틈이 있다. 이
# 틈에 한 번만 읽고 포기하면 61초 대기로 떨어져 없애려던 직렬 큐가 되살아난다.
CACHE_RACE_POLL_SEC = 2
CACHE_RACE_POLL_ATTEMPTS = 5


def _app_key_fingerprint(app_key: str) -> str:
    """앱키 지문 — 캐시가 **다른 앱키**로 받은 토큰인지 가른다(앱키 교체 시 오용 방지).

    앱키 자체를 SSM 에 쓰지 않으려고 해시 앞자리만 쓴다(토큰과 함께 평문 비교 대상이 되면
    시크릿 표면이 넓어진다 — ADR-0009).
    """
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12]


def _expires_at(data: dict) -> float | None:
    """발급 응답 → 만료 epoch. 만료를 못 읽으면 None — **그 토큰은 캐시하지 않는다.**

    날짜를 모르는 토큰을 기본값으로 캐시하면 만료된 걸 유효하다고 우기는 쪽으로 틀린다
    (전 종목 401). 못 읽으면 이번 런에서만 쓰고 버리는 게 안전한 방향이다.
    """
    try:
        return time.time() + float(data["expires_in"])
    except (KeyError, TypeError, ValueError, OverflowError):
        logger.warning("KIS 토큰 만료(expires_in)를 못 읽었다 — 이번 런에서만 쓰고 캐시하지 않는다")
        return None


class SsmTokenCache:
    """토큰을 SSM SecureString 에 공유한다. **모든 실패는 캐시 미스로 흡수한다.**

    폴백이 이 설계의 핵심이다 — SSM 이 없든 권한이 없든 값이 깨졌든, 최악이 '지금처럼 각자
    발급'이라 데이터 손상이나 런 실패로 번지지 않는다. 대신 판정은 **엄격한 쪽**으로 붙인다:
    조금이라도 미심쩍으면(지문 불일치·만료 임박·형식 이상) 미스로 떨어뜨린다. 관대한 실수는
    '만료·타인 토큰을 쓴다'로 나타나 전 종목 401 이 되는데, 엄격한 실수는 발급 한 번이다.
    """

    def __init__(self, param: str, client=None):
        self.param = param
        self._client = client
        # 직전 읽기가 '값이 없다'가 아니라 **읽기 자체가 실패**했는가(권한·SSM 장애·파라미터
        # 부재). 폴링이 이걸 안 보면 캐시가 죽은 환경에서 403 재시도마다 헛폴링을 반복해
        # 폴백 경로가 현행보다 느려진다 — "최악이 현행 동작"이라는 이 설계의 전제가 깨진다.
        self.read_failed = False

    @classmethod
    def from_env(cls) -> SsmTokenCache | None:
        param = os.environ.get(CACHE_PARAM_ENV)
        return cls(param) if param else None

    def _ssm(self):
        if self._client is None:
            self._client = ssm_client()
        return self._client

    def get(self, fingerprint: str) -> str | None:
        """유효한 공유 토큰 또는 None. 토큰 값은 로그에 남기지 않는다(남은 초만)."""
        try:
            raw = self._ssm().get_parameter(Name=self.param, WithDecryption=True)
            data = json.loads(raw["Parameter"]["Value"])
            token = data["token"]
            expires_at = float(data["expires_at"])
            same_key = data["app_key"] == fingerprint
        except Exception as exc:  # noqa: BLE001 — 캐시 실패는 발급 경로로 흡수한다
            # 파라미터 부재는 **정상 미스**다(아직 아무도 안 썼다 — 오늘 첫 런). 캐시가 죽은
            # 것으로 치면 승자 폴링이 첫 런에서 아예 안 도는데, 그때가 폴링이 제일 필요한
            # 순간이다. 클래스 이름으로 가른다 — botocore 예외를 import 하면 boto3 없는
            # 로컬·단위테스트에서 이 모듈을 못 읽는다(지연 import 관례).
            self.read_failed = type(exc).__name__ != "ParameterNotFound"
            logger.info("KIS 토큰 캐시 미스 — 발급 경로로 간다: %s", exc)
            return None
        self.read_failed = False
        if not same_key:
            logger.info("KIS 토큰 캐시 미스 — 캐시된 토큰이 다른 앱키의 것이다")
            return None
        # 타입·유한성을 여기서 막는다. float("NaN") 은 **모든 비교가 False** 라 아래 만료
        # 하한을 그냥 통과하고, "inf"·터무니없이 먼 미래는 만료된 토큰을 영원히 유효로 만든다.
        # 문자열이 아닌 token 은 `Bearer [1,2]` 같은 헤더로 나가 전 종목이 401 을 맞는다.
        # 손상된 값의 실패 방향은 반드시 '미스'여야 한다(edge-review 지적).
        # 공백이 섞인 토큰까지 막는 이유: CR/LF 가 든 값이 히트하면 호출부가 그대로
        # Authorization 헤더에 넣고, urllib 이 **토큰을 본문에 담은** ValueError 를 던진다.
        # 그 예외 문자열은 fetch_failures 를 타고 로그·collection_log 에 적힌다 — 캐시가
        # 토큰을 유출시키는 경로가 된다(ADR-0009, edge-review 지적).
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            logger.info("KIS 토큰 캐시 미스 — 캐시 값이 비었거나 문자열이 아니거나 공백을 포함한다")
            return None
        if not math.isfinite(expires_at):
            logger.info("KIS 토큰 캐시 미스 — 만료 시각이 유한한 값이 아니다")
            return None
        remaining = expires_at - time.time()
        if not (CACHE_MIN_REMAINING_SEC <= remaining <= CACHE_MAX_REMAINING_SEC):
            logger.info(
                "KIS 토큰 캐시 미스 — 남은 유효시간 %.0f초(허용 %d~%d초)",
                remaining, CACHE_MIN_REMAINING_SEC, CACHE_MAX_REMAINING_SEC,
            )
            return None
        logger.info("KIS 토큰 캐시 히트 — 발급을 건너뛴다(남은 유효시간 %.0f초)", remaining)
        return token

    def put(self, token: str, fingerprint: str, expires_at: float) -> None:
        """새로 발급한 토큰을 공유한다. 쓰기 실패는 경고만 — 이 런은 이미 토큰을 들고 있다."""
        try:
            self._ssm().put_parameter(
                Name=self.param,
                Value=json.dumps({"token": token, "app_key": fingerprint, "expires_at": expires_at}),
                Type="SecureString",
                Overwrite=True,
            )
        except Exception as exc:  # noqa: BLE001 — 다음 컨테이너가 발급하면 될 뿐이다
            logger.warning("KIS 토큰 캐시 쓰기 실패 — 다음 컨테이너가 다시 발급한다: %s", exc)


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
    """앱키/시크릿 → 액세스 토큰. 공유 캐시(SSM) → 없으면 1회 발급 후 메모리 캐시."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        client: PoliteClient,
        env: str = "prod",
        cache: SsmTokenCache | None = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = domain_for(env)
        self.client = client
        # 캐시 파라미터가 env 에 없으면 None — 로컬·테스트는 캐시 없이 현행대로 돈다.
        self.cache = cache if cache is not None else SsmTokenCache.from_env()
        self._token: str | None = None

    def token(self) -> str:
        """공유 캐시에 유효한 토큰이 있으면 그걸 쓰고, 없을 때만 발급한다(run 당 1회 규약).

        403(분당 1회 제한)은 대기하면 풀리는 유량 제한이다. 대기 **전에 캐시를 한 번 더 읽는다** —
        같은 앱키의 다른 컨테이너가 방금 발급해서 우리가 403 을 맞은 것이므로, 그 승자의 토큰이
        이미 캐시에 있으면 61초를 기다릴 이유가 없다(이게 222초 대기를 없애는 지점이다).
        그 밖의 4xx(잘못된 키 등)는 기다려도 안 풀리므로 그대로 올린다. 재시도 후에도 403 이면
        포기한다(무한 대기 금지 — 실패는 스텝이 fail-loud 로 드러낸다).
        """
        if self._token is None:
            self._token = self._resolve()
        return self._token

    def _cached(self, fingerprint: str) -> str | None:
        return self.cache.get(fingerprint) if self.cache else None

    def _await_winner(self, fingerprint: str) -> str | None:
        """403 직후 승자의 SSM 쓰기를 짧게 기다린다 — 없으면 None(1분 대기로 떨어진다).

        403 은 '같은 앱키로 방금 누가 발급했다'는 신호다. 그 승자가 응답을 받아 캐시에 쓰기까지
        1초 안팎이 걸리므로 한 번만 읽고 포기하면 61초 대기로 떨어져, 없애려던 직렬 큐가
        그대로 되살아난다(edge-review 지적).
        """
        for _ in range(CACHE_RACE_POLL_ATTEMPTS):
            cached = self._cached(fingerprint)
            if cached:
                return cached
            if self.cache.read_failed:
                # 캐시가 죽었다(권한·SSM 장애·파라미터 부재). 더 기다려도 아무도 안 쓴다 —
                # 여기서 헛폴링하면 폴백이 현행보다 느려진다(edge-review 지적).
                return None
            self.client._sleep(CACHE_RACE_POLL_SEC)
        # 마지막 잠 뒤에도 한 번 더 본다 — 안 그러면 폴링 창 끝에 들어온 쓰기를 놓치고
        # 그 다음이 61초 대기다(edge-review 지적).
        return self._cached(fingerprint)

    def _resolve(self) -> str:
        fingerprint = _app_key_fingerprint(self.app_key)
        cached = self._cached(fingerprint)
        if cached:
            return cached
        for attempt in range(TOKEN_RATE_LIMIT_MAX_RETRY + 1):
            try:
                token, expires_at = self._issue()
            except StopFetch as exc:
                # 유량 제한이 아닌 4xx(잘못된 키·권한)는 기다려도 안 풀린다 — 즉시 올린다.
                # 재시도를 다 쓴 뒤에도 막혀 있으면 그대로 올려 런을 실패로 드러낸다.
                if not _is_rate_limited(exc) or attempt == TOKEN_RATE_LIMIT_MAX_RETRY:
                    raise
                # 폴링은 **첫 403 에서만** 한다. 경합은 컨테이너들이 동시에 시작하는 그 순간에만
                # 있고, 그 뒤로도 매번 폴링하면 아무도 캐시를 못 쓰는 상황(승자가 쓰기에 실패)에서
                # 재시도마다 10초씩 얹혀 폴백이 현행보다 느려진다(edge-review 지적).
                if self.cache and attempt == 0:
                    cached = self._await_winner(fingerprint)
                    if cached:
                        logger.info("KIS 토큰 — 403 직후 공유 캐시에서 받았다(1분 대기 없음)")
                        return cached
                wait = TOKEN_RATE_LIMIT_WAIT_SEC + random.uniform(0, TOKEN_RATE_LIMIT_JITTER_SEC)
                logger.warning(
                    "KIS 토큰 발급이 분당 1회 제한에 걸렸다 — %.1f초 대기 후 재시도 "
                    "(%d/%d, 같은 앱키를 쓰는 다른 스텝이 방금 발급했을 수 있다)",
                    wait, attempt + 1, TOKEN_RATE_LIMIT_MAX_RETRY,
                )
                self.client._sleep(wait)
                # 깨어난 사이 다른 컨테이너가 발급해 캐시에 썼을 수 있다 — 발급을 또 두드리기
                # 전에 확인한다(발급 횟수를 하루 1회로 수렴시키는 쪽).
                cached = self._cached(fingerprint)
                if cached:
                    return cached
                continue
            if self.cache and expires_at is not None:
                self.cache.put(token, fingerprint, expires_at)
            return token
        raise AssertionError("도달 불가 — 마지막 시도는 성공하거나 raise 한다")

    def _issue(self) -> tuple[str, float | None]:
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
        return access_token, _expires_at(data)
