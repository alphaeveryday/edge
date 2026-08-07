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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .candle import Candle, build_candle, to_decimal
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, domain_for

logger = logging.getLogger(__name__)

TR_ID_MINUTE = "FHKST03010200"
PATH_MINUTE = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TR_ID_HISTORICAL = "FHKST03010230"
PATH_HISTORICAL = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
MARKET_DIV = "J"  # J: KRX 주식(일봉 kis_price 와 같은 축)
RATE_MSG_CD = "EGW00201"  # "초당 거래건수 초과" — HTTP 429 아님(200 본문)
MAX_RATE_RETRY = 5
# 유량 소진이 **연속 몇 종목**이면 소스 전역으로 승격하나 — 종목별 재시도(백오프 합
# ~7초)만으로는 지속 유량제한 시 400종 × 7초 ≈ 47분이 60초 창·claim lease 를 다
# 태운다(window 폭주). 연속 소진은 그 종목의 문제가 아니라 앱키 전역의 상태다 —
# 소스 전역 실패로 window 를 통째로 격리해 kernel 재시도(백오프)에 맡긴다.
RATE_STREAK_LIMIT = 5
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

    # TR·경로는 하위 클래스(소급 조회)가 갈아끼운다. 재시도·토큰·실패 축은 공유한다 —
    # 두 벌이 되면 한쪽만 고쳐지고, 그 드리프트는 늘 원장이 관대해지는 쪽으로 난다.
    tr_id = TR_ID_MINUTE
    path = PATH_MINUTE

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
        # 유량 소진(예산까지 EGW00201)이 연속된 종목 수 — RATE_STREAK_LIMIT 승격 판정용.
        self._rate_streak = 0

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
            "tr_id": self.tr_id,
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
        return self.base + self.path + "?" + urllib.parse.urlencode(params)

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
                self._rate_streak = 0  # 성공 = 유량 회복 — 승격 판정을 리셋한다
                return output2
            detail = f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            if _token_expired(f"{data.get('msg_cd')} {data.get('msg1')}") and not reissued:
                logger.warning("KIS 분봉 토큰 만료 — 캐시 폐기 후 1회 재발급: %s", detail)
                self.auth.invalidate()
                reissued = True
                continue
            if data.get("msg_cd") == RATE_MSG_CD:
                if attempt < MAX_RATE_RETRY - 1:
                    self.retry_count += 1
                    self.client._sleep(0.7 * (attempt + 1))
                    continue
                # 재시도 예산까지 유량 소진 — 연속되면 종목이 아니라 앱키 전역의 상태다.
                # 종목별 missing 으로만 접으면 백오프 합(~7초)이 전 종목에 곱해져 window
                # 폭주가 된다(RATE_STREAK_LIMIT 주석의 산술).
                self._rate_streak += 1
                if self._rate_streak >= RATE_STREAK_LIMIT:
                    raise KisSourceError(
                        f"KIS 유량 소진 연속 {self._rate_streak}종목 — 소스 전역 승격: {detail}")
                raise KisUnitError(f"KIS 분봉 {symbol} 유량 소진: {detail}")
            # 종목 단위 오류(없는 종목·일시 거절)는 재시도로 풀릴 수 있다 —
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


# ── 소급(과거 거래일) 분봉 ────────────────────────────────────────────────

# 페이징 시작·종료 라벨. 정규장 window 는 09:00–15:30 이고 라벨은 구간의 **끝**이라
# 09:01–15:30 이 계획 대상이다. 09:00 봉은 계획 밖이지만 09:01 의 직전가 씨앗이라 받는다.
DAY_LAST_HHMMSS = "153000"
DAY_FIRST_HHMMSS = "090000"
# 페이징 예산. 하루 391분 ÷ 120 = 4콜이면 끝난다 — 그 두 배를 넘겼다면 응답 형상이
# 우리가 아는 것이 아니다. 잘린 하루를 조용히 커밋하느니 그 종목을 실패로 남긴다.
MAX_DAY_PAGES = 8


def fill_no_trade_minutes(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
    """관측한 봉 **사이**의 빈 분을 직전 종가 flat·거래량 0 으로 채운다.

    🔴 소급 TR 은 무거래 분 행을 **주지 않는다**(2026-08-08 실측: 08-03 전 종목
    `cntg_vol=0` 행 0건, 저유동 439870 은 390분 중 176행뿐). 당일 TR 은 같은 분을
    `cntg_vol=0`·OHLC flat 으로 주므로, 채우지 않으면 같은 시장 사실이 벤더 경로에 따라
    `no_trade`(성공) 와 `missing`(실패) 으로 갈린다 — 그 window 는 영원히 INCOMPLETE 이고
    한산한 종목은 canonical 에서 하루의 대부분이 사라진다(`price_collect` 4분류 참조).

    ⚠️ **사이만 채운다.** 첫 관측 앞은 그 종목의 가격을 아직 모르고(직전가가 없다),
    마지막 관측 뒤는 그날 더 거래됐는지 모른다 — 둘 다 `missing` 으로 남는 게 사실에
    가깝다. 채우면 관측하지 않은 구간을 관측한 것처럼 만든다.
    """
    ordered = sorted(candles, key=lambda candle: candle.window_end)
    filled: list[Candle] = []
    for candle in ordered:
        if filled:
            previous = filled[-1]
            gap = int(
                (candle.window_end - previous.window_end).total_seconds()
            ) // INTERVAL_SECONDS
            for step in range(1, gap):
                close = previous.close
                filled.append(build_candle(
                    previous.symbol,
                    window_end=previous.window_end + timedelta(
                        seconds=INTERVAL_SECONDS * step),
                    span_seconds=INTERVAL_SECONDS,
                    values={"open": close, "high": close, "low": close, "close": close},
                    volume=Decimal(0),
                ))
        filled.append(candle)
    return tuple(filled)


class KisHistoricalMinuteClient(KisMinuteClient):
    """지난 거래일 분봉 — `FHKST03010230`. 하루를 한 번 받아 window 별로 나눠 준다.

    당일 클라이언트와 갈리는 것은 셋뿐이고, 셋 다 벤더 형상 차이라 여기서 흡수한다:

    1. **TR·날짜 축** — `FID_INPUT_DATE_1` 로 과거일을 지정한다. 당일 TR 에는 이 축이
       아예 없어서 과거 날짜를 물으면 오늘 봉이 오늘 라벨로 돌아온다(그 window 의
       `window_end` 와 안 맞아 전건 missing 이 된다) — 그래서 "과거는 못 받는다"가 아니라
       **다른 TR 을 써야 한다**가 맞다.
    2. **한 콜 120봉 + 하루 캐시** — window 마다 부르면 390 × 362 = 141k 콜이지만,
       하루를 4콜로 받아 캐시하면 362종 1.4k 콜이다. 앱키 유량은 전역이라 이 차이가
       그대로 다른 KIS 스텝의 예산이 된다.
    3. **거래일 경계** — 응답이 그 날짜에서 멈추지 않고 직전 거래일로 계속 내려간다
       (실측). `stck_bsop_date` 로 자르고, 경계를 넘은 응답이 곧 "그 날은 다 받았다"는
       페이징 종료 신호다.

    `session_date` 는 생성 시 고정한다 — 이 클라이언트는 하루 백필 전용이고, 날짜가
    호출마다 흔들리면 캐시가 곧 오염이 된다.
    """

    tr_id = TR_ID_HISTORICAL
    path = PATH_HISTORICAL

    def __init__(self, app_key: str, app_secret: str, client: PoliteClient,
                 *, session_date: date, env: str = "prod"):
        super().__init__(app_key, app_secret, client, env)
        self.session_date = session_date
        self._ymd = session_date.strftime("%Y%m%d")
        self._days: dict[str, dict[datetime, Candle]] = {}

    def _url(self, symbol: str, hour: str) -> str:
        params = {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": self._ymd,
            "FID_PW_DATA_INCU_YN": "Y",
            # 허봉(체결 없는 가상 틱) 제외 — 무거래 분은 우리가 직전가 flat 으로 채운다
            "FID_FAKE_TICK_INCU_YN": "N",
        }
        return self.base + self.path + "?" + urllib.parse.urlencode(params)

    def candles(self, symbol: str, *, window_end: datetime) -> tuple[Candle, ...]:
        """그 window 의 봉(0개 또는 1개). 하루치는 처음 한 번만 받는다."""
        asked = window_end.astimezone(KST).date()
        if asked != self.session_date:
            # 캐시가 하루에 묶여 있다 — 다른 날짜를 물으면 조용히 남의 날 봉을 준다
            raise ValueError(
                f"{symbol} 소급 조회 날짜 불일치: 클라이언트 {self.session_date}, "
                f"요청 {asked}"
            )
        day = self._days.get(symbol)
        if day is None:
            day = {candle.window_end: candle
                   for candle in fill_no_trade_minutes(self._fetch_day(symbol))}
            self._days[symbol] = day
        candle = day.get(window_end)
        # 빈 결과는 정상이다 — 첫 체결 전이면 그 분은 collector 가 missing 으로 센다
        return () if candle is None else (candle,)

    def _fetch_day(self, symbol: str) -> tuple[Candle, ...]:
        """그 거래일 전체 봉(무거래 분 제외 — 채우는 건 호출부)."""
        hour = DAY_LAST_HHMMSS
        rows: dict[str, dict] = {}
        earliest_seen: str | None = None
        for _ in range(MAX_DAY_PAGES):
            page = self._rows(symbol, hour)
            same_day = [r for r in page if r.get("stck_bsop_date") == self._ymd]
            for row in same_day:
                rows[row["stck_cntg_hour"]] = row
            if len(same_day) < len(page) or not same_day:
                # 경계를 넘었거나(직전 거래일이 섞였다) 그 창에 봉이 없다 — 하루가 끝났다
                break
            earliest = min(r["stck_cntg_hour"] for r in same_day)
            if earliest_seen is not None and earliest >= earliest_seen:
                # 진전이 없다 — 벤더가 같은 창을 반복하면 예산까지 헛돈다
                break
            earliest_seen = earliest
            if earliest <= DAY_FIRST_HHMMSS:
                break
            hour = (datetime.strptime(earliest, "%H%M%S")
                    - timedelta(minutes=1)).strftime("%H%M%S")
        else:
            # 예산 소진 = 하루가 안 끝났다. 잘린 하루를 커밋하면 그 결손이 4분류에서
            # no_trade 로 위장된다(합성이 사이를 메우므로) — 그 종목을 실패로 남긴다.
            raise KisUnitError(
                f"KIS 소급 분봉 {symbol} {self._ymd}: 페이지 예산({MAX_DAY_PAGES}) 소진"
            )
        return tuple(parse_minute_row(row, symbol) for row in rows.values())
