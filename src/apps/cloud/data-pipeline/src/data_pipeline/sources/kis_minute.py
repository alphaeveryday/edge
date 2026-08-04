"""KIS(한국투자) 국내주식 당일 분봉 소스 어댑터 (ALPHA-735 — 1분 가격 레인 벤더 교체).

API: 주식당일분봉조회 `inquire-time-itemchartprice`, tr_id `FHKST03010200`.

형상은 ALPHA-644 스파이크(2026-08-03 실전 도메인 프로브)가 확정한 것이다:

- 한 콜 = **30분치**, `FID_INPUT_HOUR_1`(HHMMSS)이 창의 **끝**이고 응답은 최신→과거 역순.
- 필드: `stck_bsop_date`·`stck_cntg_hour`·`stck_prpr`(종가)·`stck_oprc`·`stck_hgpr`·
  `stck_lwpr`·`cntg_vol`. 전부 문자열.
- `stck_cntg_hour` 는 **구간 끝 라벨**(토스 `timestamp` 와 같은 규약) — `window_start =
  라벨 − 1분`. 이 축을 뒤집으면 전 구간이 한 칸 밀린 채 조용히 커밋된다.
- **무거래 분도 행이 온다**(`cntg_vol=0`, OHLC 는 직전가 flat) — 토스와 같은 형태라
  4분류(received/no_trade/missing/invalid) 판정 로직을 벤더별로 나눌 필요가 없다.
- **멀티종목 단일콜 불가**(구분자는 rt_cd=2, 12자리 연접은 조용히 잘림) → 종목당 1콜.

유량 실측 **14.8 req/s**(20콜 1.35s, 실패 0) — 363종 1 window 가 24.5초라 토스(72.6초)로는
못 맞추던 60초 창 안에 들어간다. 이게 이 교체의 이유다. 다만 이 유량은 **앱키 단위 전역**이라
15:40 배치의 kis 스텝과 나눠 쓴다 — 그래서 기본 간격은 실측 상한이 아니라 보수적으로 잡고
(`MinutePriceWorkerConfig.min_interval_sec`) env 로 조인다.

⚠️ 초당한도는 HTTP 429 가 아니라 **200 본문 `EGW00201`** 로 온다(kis_price 와 같다) — 운반
계층이 모르니 여기서 재시도한다.
⚠️ 상주 워커는 토큰(24h)보다 오래 산다. `KisAuth._token` 은 프로세스 수명 캐시라 만료를
스스로 못 본다 — 만료 신호를 만나면 캐시를 버리고 **한 번만** 재발급한다.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

from .candle import Candle, build_candle, to_decimal
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for

logger = logging.getLogger(__name__)

TR_ID_MINUTE = "FHKST03010200"
PATH_MINUTE = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
MARKET_DIV = "J"  # J: KRX 주식(일봉 kis_price 와 같은 축)
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문)
MAX_RATE_RETRY = 5
# 토큰 만료 오류 코드. KIS 는 만료를 4xx 본문(EGW00123 "기간이 만료된 token")이나 rt_cd!=0
# 응답으로 알린다 — 두 경로 다 같은 처방(캐시 폐기 후 1회 재발급)이라 코드로만 가른다.
# ⚠️ 만료 응답 형상은 **실측 대상이 아니었다**(24시간 상주 실증 전) — 코드가 안 맞으면
# 재발급이 안 걸려 그 window 들이 실패로 드러난다(조용한 성공은 아니다, Rule 12).
TOKEN_EXPIRED_CODES = ("EGW00121", "EGW00123")
# 봉 하나가 덮는 길이. 이 어댑터는 1분봉 전용이다(FHKST03010200 이 분봉 TR).
INTERVAL_SECONDS = 60

KST = timezone(timedelta(hours=9))

_PRICE_FIELDS = (("open", "stck_oprc"), ("high", "stck_hgpr"),
                 ("low", "stck_lwpr"), ("close", "stck_prpr"))


class KisSourceError(RuntimeError):
    """**소스 전역** 실패 — 자격증명·권한·유량처럼 그 계정의 모든 종목이 못 나가는 축.

    unit 단위 missing 으로 접으면 400종 전부가 missing 인 INCOMPLETE window 가 매분 쌓이는데
    고칠 것은 설정 하나다. 재시도 대상처럼 보이면 아무도 그걸 고치러 가지 않는다(Rule 12).
    """


class KisUnitError(RuntimeError):
    """그 **종목 하나**의 실패 — 재시도로 풀릴 수 있다(collector 가 missing 으로 분류)."""


def parse_minute_row(raw: dict, symbol: str) -> Candle:
    """`output2` 행 하나 → `Candle`. 필드가 빠지거나 형이 다르면 즉시 raise 한다.

    ⚠️ `stck_cntg_hour` 는 **구간의 끝**이다(실측) — window_start 는 1분 앞이다.
    응답에 시간대 표기가 없어 **KST 로 고정**한다(KRX 로컬 시장 전용 TR).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{symbol} 분봉 행이 객체가 아니다: {type(raw).__name__}")
    day, hour = raw.get("stck_bsop_date"), raw.get("stck_cntg_hour")
    if not isinstance(day, str) or not isinstance(hour, str):
        raise ValueError(f"{symbol} 분봉 행에 날짜·시각이 없다: {raw!r}")
    try:
        end = datetime.strptime(day + hour, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise ValueError(f"{symbol} 분봉 시각 형식 오류: {day!r} {hour!r}") from error
    values = {name: to_decimal(raw.get(key), key, symbol) for name, key in _PRICE_FIELDS}
    volume = to_decimal(raw.get("cntg_vol"), "cntg_vol", symbol)
    # currency 는 KIS 가 주지 않는다 — 지어내지 않고 None 으로 둔다(KRX 전용이라 KRW 지만,
    # 관측하지 않은 값을 artifact 에 싣지 않는다는 규약이 더 중요하다)
    return build_candle(symbol, window_end=end, span_seconds=INTERVAL_SECONDS,
                        values=values, volume=volume)


def _token_expired(text: str) -> bool:
    return any(code in text for code in TOKEN_EXPIRED_CODES)


class KisMinuteClient:
    """당일 분봉 조회 — 종목당 1콜(30분치). 간격·재시도는 `PoliteClient` 와 여기서 지킨다."""

    def __init__(self, app_key: str, app_secret: str, client: PoliteClient,
                 env: str = "prod"):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base = domain_for(env)
        self.client = client
        self.auth = KisAuth(app_key, app_secret, client, env)
        # 실제 재시도 수 — collector 가 window 결과의 retry_count 로 싣는다(0 고정이면
        # 유량 압력이 관측에서 통째로 사라진다)
        self.retry_count = 0

    def candles(self, symbol: str, *, window_end: datetime) -> tuple[Candle, ...]:
        """`window_end` 로 끝나는 30분치 봉(최신→과거).

        ⚠️ **요청 window 를 끝으로 고정한다.** 최신 기준으로 부르면 400종을 도는 사이
        최신 봉이 다음 분으로 넘어가 뒤쪽 종목이 통째로 missing 이 되고, 과거 window
        재시도도 영영 복구되지 않는다.
        """
        hour = window_end.astimezone(KST).strftime("%H%M%S")
        rows = self._rows(symbol, hour)
        return tuple(parse_minute_row(row, symbol) for row in rows)

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.auth.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": TR_ID_MINUTE,
            "custtype": "P",
        }

    def _url(self, symbol: str, hour: str) -> str:
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": hour,
            # 과거(장 시작 이후 전 구간) 데이터 포함. N 이면 창 앞부분이 빈다.
            "FID_PW_DATA_INCU_YN": "Y",
        }
        return self.base + PATH_MINUTE + "?" + urllib.parse.urlencode(params)

    def _rows(self, symbol: str, hour: str) -> list[dict]:
        """1콜의 `output2`. EGW00201 만 본문 기반 재시도하고, 토큰 만료는 1회 재발급한다."""
        url = self._url(symbol, hour)
        reissued = False
        for attempt in range(MAX_RATE_RETRY):
            data = self._call(url, symbol)
            if data.get("rt_cd") == "0":
                output2 = data.get("output2")
                # 빈 list 는 정상(그 창에 봉이 없다 — collector 가 missing 으로 센다).
                # 키 누락·비-list 는 rt_cd=0 인데도 이상이라 형상 위반으로 올린다.
                if not isinstance(output2, list):
                    raise ValueError(f"KIS rt_cd=0 인데 output2 이상: {type(output2).__name__}")
                return output2
            detail = f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            if _token_expired(f"{data.get('msg_cd')} {data.get('msg1')}") and not reissued:
                logger.warning("KIS 분봉 토큰 만료 — 캐시 폐기 후 1회 재발급: %s", detail)
                self.auth.invalidate()
                reissued = True
                continue
            if data.get("msg_cd") == RATE_MSG_CD and attempt < MAX_RATE_RETRY - 1:
                self.retry_count += 1
                self.client._sleep(0.7 * (attempt + 1))
                continue
            # 종목 단위 오류(없는 종목·일시 거절)와 유량 소진은 재시도로 풀릴 수 있다 —
            # 원장이 그 window 를 다시 claim 하는 것으로 재시도된다.
            raise KisUnitError(f"KIS 분봉 {symbol}: {detail}")
        # 마지막 attempt 의 실패는 위에서 raise 되므로 여기 오는 길은 하나다 —
        # **마지막 칸에서 토큰 만료 재발급을 쓴 경우**(continue 로 루프가 끝난다).
        raise KisUnitError(f"KIS 분봉 {symbol}: 재시도 예산({MAX_RATE_RETRY}) 소진")

    def _call(self, url: str, symbol: str) -> dict:
        """1회 요청 → 응답 dict. 소스 전역/종목 단위/형상 위반을 여기서 가른다."""
        try:
            body = self.client.request("GET", url, headers=self._headers(), decode=True)
        except StopFetch as exc:
            if _token_expired(getattr(exc, "body", "")):
                # 4xx 로 오는 만료 — 캐시를 버리고 올린다. 호출부(_rows)가 다음 attempt 에
                # 새 토큰으로 재발급된 헤더를 만든다.
                logger.warning("KIS 분봉 4xx 토큰 만료 — 캐시 폐기: %s", exc)
                self.auth.invalidate()
                raise KisUnitError(f"KIS 분봉 {symbol} 토큰 만료: {exc}") from exc
            # 그 밖의 4xx/429 는 키·권한·쿼터 — 종목을 바꿔도 안 풀린다.
            raise KisSourceError(f"KIS 분봉 소스 전역 실패: {exc}") from exc
        except RuntimeError as exc:
            # PoliteClient 의 5xx·네트워크 재시도 소진 — 그 종목만 격리한다(전송 실패
            # 하나가 앞서 모은 종목까지 버리고 window 를 죽이면 안 된다).
            raise KisUnitError(f"KIS 분봉 {symbol} 요청 실패: {exc}") from exc
        try:
            data = json.loads(body)
        except ValueError as exc:
            # 깨진 JSON 은 전송 사고에 가깝다 — 재시도로 풀리는 축(missing)으로 둔다.
            raise KisUnitError(f"KIS 분봉 {symbol} 응답 JSON 손상: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"KIS 응답이 객체가 아님: {type(data).__name__}")
        return data
