"""토스증권 Open API 분봉 소스 어댑터 (1분 파이프라인 — 계획 §9 토스 adapter).

**형상은 전부 실측이다**(2026-08-01, `.dev/toss-openapi-실측.md`). 계획 §19 가 형상 추측을
금지하므로 이 파일의 상수·파싱 규칙은 녹화 fixture(`tests/fixtures/toss/`)가 근거다.

```text
POST /oauth2/token   client_credentials(form) → {access_token, token_type, expires_in 86399}
GET  /api/v1/candles?symbol=&interval=1m&count=<1..200>[&before=<ISO8601>]
     → {"result": {"candles": [...newest-first...], "nextBefore": "<ISO8601>"}}
```

⚠️ **timestamp 는 구간의 끝이다(right-labeled).** 정규장 09:00~15:30 인데 첫 캔들이 `09:01`,
마지막이 `15:30` 인 것으로 확정했다. 원장 window 는 `[window_start, window_end)` 라서
**캔들 ts == window_end** 로 매핑한다 — 이걸 뒤집으면 전 구간이 한 칸 밀린 채 조용히
커밋된다(캔들 수는 그대로라 어떤 게이트도 안 걸린다).

⚠️ **거래가 없어도 캔들이 온다.** 저유동 종목 하루 390개 중 376개가 `volume: "0"` 인 flat
캔들이었다(OHLC 동일=직전가). 그래서 no_trade 는 "행이 없다"가 아니라 **행이 있고 거래량이
0** 이다. 행 자체가 없으면 missing 이다 — 이 구분이 원장 4분류의 근거다.

⚠️ **OHLCV 가 문자열**로 온다(`"258500"`). float 로 받으면 정밀도가 깨지므로 Decimal 로
읽는다. 응답은 **gzip** 이고 **에러 본문도 gzip** 이라 디코드하지 않으면 사유가 안 보인다.

레이트 리밋은 **초당 5회**(`X-RateLimit-*` 헤더, 초과 시 429 + `Retry-After: 1`).
RTT 는 p50 0.10s·p95 0.12s(VPC→인터넷 실측)라 **처리량은 RTT 가 아니라 이 간격에 묶인다** —
348종 1 window = 348콜 ÷ 5 = 약 70초다. 동시성을 올려도 한도가 같아 줄지 않는다(KIS 에서
같은 착각을 한 적이 있다). SLA 120초 안이지만 recovery 여유가 얇다는 뜻이라, 이 산식은
숫자가 바뀌면 다시 계산해야 한다.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.tossinvest.com"

# API 가 스스로 알려주는 어휘다(400 응답의 `data.allowedValues`) — 실측으로 확인했다.
SUPPORTED_INTERVALS = ("1m", "1d")
# 캔들 하나가 덮는 구간 길이 — timestamp 가 구간의 **끝**이라 window_start 를 여기서 뺀다
INTERVAL_SECONDS = {"1m": 60, "1d": 86_400}
# 400 응답의 `data.constraint` 가 준 값.
MAX_COUNT = 200
# 가격·거래량 크기 상한 — 소스가 지수표기 거대값을 줘도 artifact 에 싣지 않는다
MAX_MAGNITUDE = Decimal("1e15")
# 초당 5회(X-RateLimit-Limit). 간격으로 환산해 호출 전에 지킨다 — 429 를 맞고 나서
# 물러나는 것보다 애초에 안 넘는 게 싸다(429 는 그 콜이 통째로 버려진다).
RATE_LIMIT_PER_SECOND = 5
MIN_INTERVAL_SECONDS = 1.0 / RATE_LIMIT_PER_SECOND
# 토큰 만료 여유 — 만료 직전 발급분으로 요청하다 401 을 맞지 않게.
TOKEN_REFRESH_MARGIN_SECONDS = 60

_PRICE_FIELDS = (("open", "openPrice"), ("high", "highPrice"),
                 ("low", "lowPrice"), ("close", "closePrice"))


class TossApiError(RuntimeError):
    """토스 API 가 구조화된 오류를 준 경우. `code` 로 재시도 여부를 가른다."""

    def __init__(self, status: int, code: str, message: str, *, request_id: str = "",
                 retry_after: float | None = None):
        super().__init__(f"toss {status} {code}: {message} (requestId={request_id})")
        self.status = status
        self.code = code
        self.request_id = request_id
        # 벤더가 "언제 다시 오라"고 말했으면 그게 권위다(실측: 429 에 `Retry-After: 1`)
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """재시도로 풀리는가 — 한도 초과와 5xx 만이다.

        `stock-not-found`(404)·`invalid-request`(400)는 같은 요청을 다시 보내도 같은
        답이 온다. 그걸 재시도로 돌리면 한도만 먹고 window 가 늦어진다.
        """
        return self.status in (0, 429) or self.status >= 500   # 0 = 전송 실패


@dataclass(frozen=True)
class Candle:
    """분봉 하나 — 원장이 쓰는 축으로 정규화한 형태.

    `window_start` 는 토스 timestamp 에서 **1분을 뺀 값**이다(위 right-labeled 주석).
    """

    symbol: str
    window_start: datetime
    window_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: str | None

    @property
    def traded(self) -> bool:
        """거래가 있었나 — 거래량 0 은 정상 응답이고 no_trade 다(missing 이 아니다)."""
        return self.volume > 0


def _to_decimal(raw: object, field_name: str, symbol: str) -> Decimal:
    """문자열 숫자를 Decimal 로. float 를 거치지 않는다 — 정밀도가 깨진다."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError(f"{symbol} 캔들의 {field_name} 형이 예상 밖이다: {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as error:
        raise ValueError(f"{symbol} 캔들의 {field_name} 를 못 읽는다: {raw!r}") from error
    if not value.is_finite():
        raise ValueError(f"{symbol} 캔들의 {field_name} 가 유한하지 않다: {raw!r}")
    if abs(value) >= MAX_MAGNITUDE:
        # `1E+999` 같은 값도 Decimal 로는 유한하다 — 여기서 안 막으면 artifact 에 실린
        # 뒤 NUMERIC 저장이나 분석 단계에서야 터진다(그때는 어느 window 인지 못 찾는다)
        raise ValueError(f"{symbol} 캔들의 {field_name} 가 범위를 벗어났다: {raw!r}")
    return value


def parse_candle(raw: dict, symbol: str, *, interval: str = "1m") -> Candle:
    """응답 캔들 한 건 → `Candle`. 필드가 빠지거나 형이 다르면 즉시 raise 한다.

    조용히 기본값을 넣지 않는 이유(Rule 12): 가격이 0 이나 None 으로 접히면 그 window 가
    '정상 수집'으로 커밋되고, 나중에 아무도 그 자리를 다시 보지 않는다.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{symbol} 캔들이 객체가 아니다: {type(raw).__name__}")
    stamp = raw.get("timestamp")
    if not isinstance(stamp, str):
        raise ValueError(f"{symbol} 캔들에 timestamp 가 없다: {raw!r}")
    try:
        end = datetime.fromisoformat(stamp)
    except ValueError as error:
        raise ValueError(f"{symbol} 캔들 timestamp 형식 오류: {stamp!r}") from error
    if end.tzinfo is None:
        # 실측 응답은 항상 `+09:00` 오프셋을 달고 온다. naive 가 오면 우리가 시간대를
        # 골라야 하는데, 그 추측이 바로 한 칸 밀린 커밋을 만든다.
        raise ValueError(f"{symbol} 캔들 timestamp 에 오프셋이 없다: {stamp!r}")
    values = {
        name: _to_decimal(raw.get(key), key, symbol) for name, key in _PRICE_FIELDS
    }
    volume = _to_decimal(raw.get("volume"), "volume", symbol)
    if volume < 0:
        raise ValueError(f"{symbol} 캔들 거래량이 음수다: {raw!r}")
    if min(values.values()) <= 0:
        # OHLC **상호관계**만 보면 전부 -1 인 행이 통과한다(레포 공통 게이트의
        # non_positive_price 불변식과 같은 축)
        raise ValueError(f"{symbol} 캔들 가격이 양수가 아니다: {raw!r}")
    if not (values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]):
        # OHLC 정합은 소스가 깨질 수 있는 축이다 — 통과시키면 canonical 이 그대로 받는다
        raise ValueError(f"{symbol} 캔들 OHLC 정합 위반: {raw!r}")
    currency = raw.get("currency")
    if currency is not None and not isinstance(currency, str):
        raise ValueError(f"{symbol} 캔들 currency 형이 예상 밖이다: {currency!r}")
    span = INTERVAL_SECONDS.get(interval)
    if span is None:
        # 어휘 밖 interval 로 파싱하면 window 길이를 우리가 지어내게 된다
        raise ValueError(f"interval 은 {sorted(INTERVAL_SECONDS)} 만 된다: {interval!r}")
    return Candle(
        symbol=symbol,
        # ⚠️ ts 는 구간의 **끝**이다 — window_start 는 그 interval 만큼 앞이다
        # (1d 를 60초로 접으면 일봉 소비자의 날짜 창이 통째로 어긋난다)
        window_start=end.__class__.fromtimestamp(end.timestamp() - span, tz=end.tzinfo),
        window_end=end,
        volume=volume, currency=currency, **values,
    )


@dataclass
class TossOpenApiClient:
    """토큰 발급·캐시 + 분봉 조회. 호출 간격과 429 백오프를 여기서 지킨다.

    `sleep`/`monotonic`/`opener` 는 테스트가 실시간 대기 없이 간격 규칙을 검증하는
    이음매다(벽시계 단언을 가상 시계로 바꾸는 이 레포 관례와 같은 축).
    """

    client_id: str
    # repr 에서 뺀다 — client 를 예외 컨텍스트나 디버그 로그에 %r 로 남기면
    # 자격증명이 그대로 중앙 로그에 박힌다
    client_secret: str = field(repr=False)
    base_url: str = BASE_URL
    min_interval: float = MIN_INTERVAL_SECONDS
    max_retries: int = 3
    timeout: float = 10.0
    opener: object = None          # urlopen 대체(테스트 주입)
    sleep: object = time.sleep
    monotonic: object = time.monotonic
    _token: str = field(default="", repr=False)
    _token_expires_at: float = field(default=0.0, repr=False)
    # ⚠️ **None 이 "아직 호출 없음"이다.** 0.0 을 그 뜻으로 쓰면 monotonic 이 0 에서
    # 시작하는 순간 truthiness 검사가 매번 거짓이 돼 **간격이 통째로 안 걸린다**
    # (테스트가 실제로 잡았다 — falsy-zero).
    _last_call_at: float | None = field(default=None, repr=False)
    # 실제 재시도 횟수 — collector 가 window 결과의 retry_count 로 싣는다(0 고정이면
    # 429 압력이 관측에서 통째로 사라진다)
    retry_count: int = field(default=0, repr=False)

    # ── 운반 ──────────────────────────────────────────────────
    def _open(self, request):
        opener = self.opener or urllib.request.urlopen
        return opener(request, timeout=self.timeout)

    def _respect_interval(self) -> None:
        moment = self.monotonic()
        if self._last_call_at is not None:
            elapsed = moment - self._last_call_at
            if elapsed < self.min_interval:
                self.sleep(self.min_interval - elapsed)
                # 잠든 만큼 시계가 갔다 — 다시 읽어야 다음 간격이 자기 시작점을 갖는다
                moment = self.monotonic()
        self._last_call_at = moment

    def _request(self, path: str, *, token: str | None = None, form: dict | None = None):
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers)
        self._respect_interval()
        try:
            with self._open(request) as response:
                return _decode(response.read(), response.headers), dict(response.headers)
        except urllib.error.HTTPError as error:
            payload = _decode(error.read(), error.headers)
            detail = payload.get("error") if isinstance(payload, dict) else None
            retry_after = _retry_after(error.headers)
            if isinstance(detail, dict):
                raise TossApiError(
                    error.code, str(detail.get("code", "")), str(detail.get("message", "")),
                    request_id=str(detail.get("requestId", "")), retry_after=retry_after,
                ) from error
            # 구조화 오류가 아니면 코드만 들고 올린다 — 본문을 지어내지 않는다
            raise TossApiError(error.code, "unstructured", str(payload)[:200],
                               retry_after=retry_after) from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            # ⚠️ 전송 실패(연결 끊김·timeout·손상 gzip)도 **같은 타입으로** 올린다.
            # HTTPError 만 감싸면 이것들이 종목 단위 격리를 빠져나가, 200번째 종목의
            # timeout 하나가 앞서 모은 199종까지 버리고 window 를 통째로 죽인다.
            raise TossApiError(0, "transport", f"{type(error).__name__}: {error}") from error

    def _call(self, path: str, *, token: str | None = None, form: dict | None = None):
        """재시도는 **재시도로 풀리는 오류에만**(429·5xx·전송 실패).

        `max_retries` 는 **최초 호출 뒤 추가 시도 횟수**다(0 이면 재시도 없음).
        429 는 벤더가 준 `Retry-After` 를 따른다 — 우리 backoff(0.2s)가 그보다 짧으면
        아직 제한된 구간에서 다시 두드려 예산만 태우고 그 종목이 missing 이 된다.
        """
        for attempt in range(self.max_retries + 1):
            try:
                return self._request(path, token=token, form=form)
            except TossApiError as error:
                if not error.retryable or attempt == self.max_retries:
                    raise
                delay = max(error.retry_after or 0.0, self.min_interval * (2 ** attempt))
                logger.warning("토스 %s — 재시도 %d/%d(%.2fs 대기)",
                               error, attempt + 1, self.max_retries, delay)
                self.retry_count += 1
                self.sleep(delay)
        raise AssertionError("unreachable")

    # ── 토큰 ──────────────────────────────────────────────────
    def token(self) -> str:
        """24시간짜리 토큰을 프로세스 안에서 재사용한다(상주 Worker 전제).

        만료 여유를 두고 갱신한다 — 만료 직전 토큰으로 요청하면 그 window 가 401 로
        통째로 날아가는데, 그건 벤더 장애가 아니라 우리가 만든 실패다.
        """
        if self._token and self.monotonic() < self._token_expires_at:
            return self._token
        payload, _ = self._call("/oauth2/token", form={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        token = payload.get("access_token")
        expires_in = payload.get("expires_in")
        if not isinstance(token, str) or not token:
            raise ValueError("토스 토큰 응답에 access_token 이 없다")
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise ValueError(f"토스 토큰 만료 시간이 이상하다: {expires_in!r}")
        self._token = token
        self._token_expires_at = (
            self.monotonic() + max(0.0, float(expires_in) - TOKEN_REFRESH_MARGIN_SECONDS)
        )
        return token

    # ── 분봉 ──────────────────────────────────────────────────
    def candles(self, symbol: str, *, interval: str = "1m", count: int = 1,
                before: datetime | None = None,
                kind: str = "stock") -> tuple[Candle, ...]:
        """최신순 캔들. 없는 종목(404 `stock-not-found`)은 `TossApiError` 로 올린다."""
        if interval not in SUPPORTED_INTERVALS:
            # API 도 400 으로 거부하지만, 한도를 먹기 전에 우리가 먼저 막는다
            raise ValueError(f"interval 은 {SUPPORTED_INTERVALS} 만 된다: {interval!r}")
        if not 1 <= count <= MAX_COUNT:
            raise ValueError(f"count 는 1..{MAX_COUNT} 다: {count}")
        query = {"interval": interval, "count": count}
        if before is not None:
            # ⚠️ `before` 는 **inclusive** 다(실측: 다음 페이지 첫 캔들 = nextBefore 값).
            # 그래서 특정 window 를 집으려면 그 window_end 를 그대로 주면 된다.
            query["before"] = before.isoformat()
        if kind == "index":
            # 지수는 전용 경로다(실측 fixture) — 주식 경로로 부르면 404 다
            path = f"/api/v1/market-indicators/{urllib.parse.quote(symbol)}/candles?"
        else:
            path = "/api/v1/candles?"
            query["symbol"] = symbol
        try:
            payload, _ = self._call(path + urllib.parse.urlencode(query), token=self.token())
        except TossApiError as error:
            if error.status != 401:
                raise
            # 서버가 로컬 만료시각보다 먼저 토큰을 무효화했다. 캐시를 버리고 **한 번만**
            # 다시 발급한다 — 안 그러면 남은 유효기간 내내 전 종목이 401→missing 이다.
            logger.warning("토스 401 — 캐시 토큰 폐기 후 재발급")
            self._token, self._token_expires_at = "", 0.0
            payload, _ = self._call(path + urllib.parse.urlencode(query), token=self.token())
        if not isinstance(payload, dict):
            # 벤더가 배열·문자열을 주면 .get 이 AttributeError 를 내고 그건 호출자의
            # ValueError 격리를 빠져나간다 — 형상 오류로 정직하게 올린다
            raise ValueError(f"{symbol} 응답 최상위가 객체가 아니다: {str(payload)[:120]}")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("candles"), list):
            raise ValueError(f"{symbol} 분봉 응답 형상이 다르다: {str(payload)[:200]}")
        return tuple(parse_candle(row, symbol, interval=interval)
                     for row in result["candles"])


def _retry_after(headers) -> float | None:
    """`Retry-After` 초 단위. 형식이 다르면 None — 우리 backoff 가 대신한다."""
    raw = headers.get("Retry-After") if headers else None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _decode(raw: bytes, headers) -> dict:
    """gzip 해제 + JSON. **에러 본문도 gzip 이라** 이 경로를 공유해야 사유가 보인다."""
    if "gzip" in (headers.get("Content-Encoding") or ""):
        raw = gzip.decompress(raw)
    if not raw:
        return {}
    return json.loads(raw)
