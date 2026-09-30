"""KIS(한국투자) 국내주식 분봉 소스 어댑터 (ALPHA-735 — 1분 가격 레인 벤더 교체).

TR 이 둘이고 **계약이 갈린다** — 클래스가 둘인 이유가 그것이다:

- `KisMinuteClient` — 당일 `inquire-time-itemchartprice` `FHKST03010200`. 상주 레인용.
- `KisHistoricalMinuteClient` — 지난 거래일 `inquire-time-dailychartprice`
  `FHKST03010230`(ALPHA-846). 날짜 축이 있고, **무거래 분 행을 주지 않으며**, 응답이
  거래일 경계를 넘어 내려간다. 아래 "무거래 분" 항은 **당일 TR 한정**이다.

당일 형상은 ALPHA-644 스파이크(2026-08-03 실전 도메인 프로브)가 확정한 것이다:

- 한 콜 = **30분치**, `FID_INPUT_HOUR_1`(HHMMSS)이 응답의 가장 늦은 라벨이고 최신→과거 역순.
- 필드: `stck_bsop_date`·`stck_cntg_hour`·`stck_prpr`(종가)·`stck_oprc`·`stck_hgpr`·
  `stck_lwpr`·`cntg_vol`. 전부 문자열.
- `stck_cntg_hour` 는 **구간 시작 라벨** — `window_end = 라벨 + 1분`. 업종지수 TR
  (`kis_sector_index`)과 같은 축이다. 이 축을 뒤집으면 전 구간이 한 칸 밀린 채 조용히
  커밋된다(ALPHA-1127 — 08-04~09-29 실시간 수집분이 그렇게 커밋됐다).
- ⚠️ **당일 TR 응답은 세 층이다**(2026-09-30 장중 프로브, ALPHA-1127). 시각 T(분 M 진행
  중)에 라벨 R 로 물으면:
    · 라벨 R 행 = **지금 형성 중인 봉** — 라벨과 무관하게 요청한 시각이 그대로 찍힌다.
      12:05:30 에 1205 로 물으면 1205 행에, 1206 으로 물으면 1206 행에 같은 값이 온다.
      ALPHA-644 가 "끝 라벨"로 실측한 것이 이 행이다 — 형성 중 봉만 보면 그렇게 보인다.
      체결 전이면 **직전 확정 봉을 거래량째 복제**한다(저유동 종목 부풀림의 원인).
    · 라벨 M ~ R−1 행 = **0 자리표시**(아직 끝나지 않은 분). 진짜 무거래 봉과 모양이 같다.
    · 라벨 ≤ M−1 행 = **확정 봉**. 분이 끝난 뒤 ~6초 안에 확정되고 그 뒤 바뀌지 않았다.
  그래서 창 w(라벨 w) 를 안전하게 읽는 길은 하나다 — **w+1 분이 끝난 뒤**(T ≥ w+2:00)
  라벨 w+2 이상으로 물어 w 행을 읽는다. 그러면 w 는 확정 층에 있고 자리표시와 섞이지
  않는다. claim 시각은 `minute.models.scheduled_at_for`(WINDOW_SETTLE_SEC)가 맞춘다.
- **무거래 분도 행이 온다**(`cntg_vol=0`, OHLC 는 직전가 flat) — 토스와 같은 형태라
  4분류(received/no_trade/missing/invalid) 판정 로직을 벤더별로 나눌 필요가 없다.
- **종가 단일가**(15:30:00 체결)는 라벨 `153000` 봉에 온다 — 계획 창(09:00~15:29) 밖이라
  `fold_closing_auction` 이 15:29 창에 합친다. 안 합치면 공식 종가와 하루 거래량의 ~10%
  가 canonical 에서 빠진다(ALPHA-1128).
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
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .candle import Candle, build_candle, is_stamp, to_decimal
from .http import PoliteClient, StopFetch
from .kis_auth import KisAuth, token_expired, domain_for
from .call_budget import CallBudgetError

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
# 만료 코드·판정은 `kis_auth` 가 소유한다(iNAV 레인과 공유 — 그 모듈 주석).
# 봉 하나가 덮는 길이. 이 어댑터는 1분봉 전용이다(두 TR 다 분봉이다).
INTERVAL_SECONDS = 60
# `stck_bsop_date`(YYYYMMDD)·`stck_cntg_hour`(HHMMSS) 자리수. **둘을 함께** 못박아야
# 아래 연접 `strptime` 이 한쪽 자리를 훔쳐가지 못한다(kis_sector_index 와 같은 문).
_YMD_LEN = 8
_HHMMSS_LEN = 6
_NO_DATA_INT64_MIN = "-9223372036854775808"

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


class KisDayIncompleteError(KisUnitError):
    """소급 조회가 그 **하루를 끝내지 못했다** — 재시도해도 같은 응답이 온다.

    `KisUnitError` 와 분리하는 이유는 캐시 여부다: 전송·유량 실패는 다음 window 에서
    풀리므로 캐시하면 몇 초의 장애가 하루를 죽이는데, 이건 응답의 형상 자체라 재청구가
    같은 페이지 예산을 다시 태울 뿐이다(종목당 최대 8콜 × 390 window).
    """


def parse_minute_row(raw: dict, symbol: str) -> Candle | None:
    """`output2` 행 하나 → `Candle` 또는 벤더의 데이터 없음이면 `None`.

    ⚠️ `stck_cntg_hour` 는 **구간의 시작**이다(모듈 docstring 의 세 층) — window_end 는
    1분 뒤다. 응답에 시간대 표기가 없어 **KST 로 고정**한다(KRX 로컬 시장 전용 TR).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{symbol} 분봉 행이 객체가 아니다: {type(raw).__name__}")
    day, hour = raw.get("stck_bsop_date"), raw.get("stck_cntg_hour")
    if not isinstance(day, str) or not isinstance(hour, str):
        raise ValueError(f"{symbol} 분봉 행에 날짜·시각이 없다: {raw!r}")
    # 자리수를 **양쪽 다** 못박는다 — `strptime("%Y%m%d%H%M%S")` 는 연접 문자열 하나를
    # 보므로 한쪽이 짧으면 다른 쪽 자리를 잘라 먹고도 파싱에 성공한다(실측:
    # `"20260807"+"1030"` → 10:03:00). 결과의 `second` 가 0 이라 격자로도 못 거른다 —
    # 그 봉은 멀쩡한 다른 window 에 앉아 확정된다. **값이 조용히 틀리는** 부류다.
    # 5자리(`"93000"`)는 선행 0 이 잘린 것으로 **복구도 가능해 보인다**(→09:30:00).
    # 그래도 거부한다: 4자리와 구분할 근거가 우리에게 없고, 추정해서 맞히면 틀렸을 때
    # 아무 신호가 안 남는다. 라벨 규약이 실제로 바뀌면 실패로 드러나는 편이 낫다.
    # 공백 패딩과 비-ASCII 숫자도 여기서 막는다 — `strptime` 이 둘 다 관대하게 받아
    # 값은 **맞게** 읽히고, 그래서 벤더의 포맷 변경이 조용히 흡수된다. 두 술어가 각각
    # 다른 문을 막는다. 하나만 두면 나머지가 열린다(실측):
    #   · `isdecimal()` → 공백 패딩. `%d` 의 `" [1-9]"` 로 `"202608 3"`→08-03 이 통과한다
    #     (`%Y`·`%m` 에는 없다). **공백은 ASCII 라 `isascii()` 로는 못 막는다.**
    #     ⚠️ 라벨 쪽(`%H` 의 `" \d"`)은 **파이썬 버전에 달렸다** — 실측 3.12(런타임
    #     이미지·CI)에는 없고 3.14 에는 있다. 즉 `" 93000"` 은 지금은 `strptime` 이
    #     거부하고, 이미지를 올리는 날 조용히 09:30:00 이 된다. 그래서 미리 막는다.
    #   · `isascii()` → 비-ASCII 숫자. `\d` 가 유니코드 Nd 라 `"1٠3000"`→10:30:00,
    #     `"٢٠٢٦0803"`→2026 이 통과한다(`%Y` 는 이쪽으로 샌다). `isdecimal()` 은 True 다.
    #     이쪽은 3.12 에서도 산다.
    # 날짜와 라벨을 **연접해서** 본다 — 한쪽만 보면 다른 쪽 문이 그대로 열린다.
    if not is_stamp(day, _YMD_LEN) or not is_stamp(hour, _HHMMSS_LEN):
        # ⚠️ "자리수"라고 쓰지 않는다 — `" 93000"` 은 6자다. 운영자가 로그에서 자리수를
        # 세어 보고 "가드가 오작동했다"로 읽으면 진짜 원인(포맷 변경)을 놓친다.
        raise ValueError(f"{symbol} 분봉 날짜·시각 형상이 아니다: {day!r} {hour!r}")
    try:
        start = datetime.strptime(day + hour, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise ValueError(f"{symbol} 분봉 시각 형식 오류: {day!r} {hour!r}") from error
    # ⛔ 분 격자 가드는 **여기 두지 마라**(`_fetch_day` 에 있다). 형제
    # `kis_sector_index.parse_index_row` 가 파서에 둬서 모양을 맞춘답시고 옮겨봤다가
    # 되돌린 자리다. 가르는 것은 어느 파일이냐가 아니라 **window 키와 충돌할 수 있느냐**다:
    #   · 짧은 라벨은 `second == 0` 으로 파싱**될 수 있다**("1030"→10:03:00) → 계획
    #     window 키와 정확히 일치할 수 있다 → `select_window_candle` 이 그걸 뽑아 틀린
    #     값이 VALID 로 확정된다. 위 자리수 가드가 막는 것이 그것이라 파서에 있어야 한다.
    #   · 격자 밖 봉(`second != 0`)은 분 정렬된 어떤 키와도 못 만난다 → 당일 경로에선
    #     `select_window_candle` 이 그냥 안 뽑는다. 여기서 raise 해봐야 **남의 행** 하나로
    #     그 window 를 INVALID 로 만들 뿐이다(30봉 페이지라 ~30 window 를 그렇게 만든다).
    #   · 소급은 다르다 — `fill_no_trade_minutes` 가 봉에 합성을 **앵커**하므로 격자 밖
    #     봉 뒤가 통째로 밀린다. 손실 폭은 그 봉이 어디 있느냐에 달렸다: 뒤에 실봉이
    #     빽빽하면 몇 개고, 그게 유일한 관측이면 계획 390개 **전부**다(실측 적중 0).
    #     거기서만 가드가 일한다 — `_fetch_day` 안에 있다.
    # 지수 어댑터도 페이지 전체를 돌려주므로 노출은 같다 — 그쪽 격자 가드도 같은 값을
    # 치르고 있다. 여기서 따라 할 이유가 아니다.
    raw_volume = raw.get("cntg_vol")
    if raw_volume == _NO_DATA_INT64_MIN:
        return None
    values = {name: to_decimal(raw.get(key), key, symbol) for name, key in _PRICE_FIELDS}
    volume = to_decimal(raw_volume, "cntg_vol", symbol)
    # currency 는 KIS 가 주지 않는다 — 지어내지 않고 None 으로 둔다(KRX 전용이라 KRW 지만,
    # 관측하지 않은 값을 artifact 에 싣지 않는다는 규약이 더 중요하다)
    return build_candle(symbol, window_end=start + timedelta(seconds=INTERVAL_SECONDS),
                        span_seconds=INTERVAL_SECONDS, values=values, volume=volume)


def is_closing_auction(candle: Candle) -> bool:
    """라벨 15:30(KST 벽시계) 봉인가 — 입력 tz 로 비교하면 UTC aware 봉이 마감을 못 만난다."""
    return candle.window_start.astimezone(KST).strftime("%H%M%S") == DAY_LAST_HHMMSS


def fold_closing_auction(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
    """라벨 15:30 봉(종가 단일가 체결)을 15:30 에 **끝나는** 봉(15:29 창)에 합친다.

    KRX 종가 단일가는 15:20~15:30 접수 뒤 15:30:00 에 한 번 체결되고, KIS 는 그것을
    시작 라벨 `153000` 봉으로 준다. 계획 창은 15:29 까지라 그대로 두면 공식 종가와 그
    체결(005930 09-29: 1,907,055주 = 하루의 12%)이 canonical 에서 사라진다. 391번째 창을
    만들지 않고 15:29 창에 접는 이유: 소비자(5분 롤업의 15:25 버킷·갭 계산의 전일 종가)가
    "마지막 창의 종가 = 공식 종가"를 전제하고, 창 수를 바꾸면 계획기·롤업·소비자가 다 같이
    바뀌기 때문이다. 접힌 봉: 시가·고가·저가는 둘을 아우르고, 종가는 단일가, 거래량은 합.

    당일·소급 두 경로가 같은 함수를 쓴다 — 갈리면 재수집분과 실시간분의 마지막 창이 다르다.
    15:30 봉만 있고 15:29 창이 없으면(첫 페이지가 잘린 소급 응답 등) 15:30 봉을 그 창으로
    옮긴다 — 버리면 그 종목의 종가가 없다.
    """
    auction = [c for c in candles if is_closing_auction(c)]
    if not auction:
        return candles
    rest = [c for c in candles if not is_closing_auction(c)]
    folded = []
    for auc in auction:
        end = auc.window_start
        regular = next((c for c in rest if c.symbol == auc.symbol and c.window_end == end), None)
        if regular is None:
            values = {"open": auc.open, "high": auc.high, "low": auc.low, "close": auc.close}
        else:
            rest.remove(regular)
            values = {"open": regular.open, "high": max(regular.high, auc.high),
                      "low": min(regular.low, auc.low), "close": auc.close}
        folded.append(build_candle(auc.symbol, window_end=end, span_seconds=INTERVAL_SECONDS,
                                   values=values, volume=(regular.volume if regular else 0) + auc.volume,
                                   currency=auc.currency))
    return tuple(rest + folded)


_token_expired = token_expired  # 이 모듈의 기존 호출부 이름을 유지한다


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
        # 동시 요청에서 두 카운터 갱신을 보호한다(분 워커 fetch_concurrency>1). 승격 판정의 '연속'은
        # 완료 순서 기준이 된다 — 동시성 N 이면 진행 중 요청 N 개만큼 순서가 섞일 수 있다.
        self._counter_lock = threading.Lock()

    def candles(self, symbol: str, *, window_end: datetime) -> tuple[Candle, ...]:
        """`window_end` 에 끝나는 창을 포함한 30분치 봉(최신→과거).

        ⚠️ **요청 라벨은 `window_end`(= 창 시작 + 1분) 다** — 창 시작 라벨보다 뒤에 둬야
        그 창이 "형성 중 봉" 층(요청 라벨 행)에 앉지 않는다(모듈 docstring 의 세 층).
        확정 층에 있으려면 묻는 **시각**도 w+1 분이 끝난 뒤여야 하는데, 그건 이 함수가
        아니라 `scheduled_at_for` 가 지킨다 — 여기서 자면 lease 검증이 깨진다.
        ⚠️ 요청 라벨을 **창 기준으로 고정**한다(최신 기준 금지). 최신으로 부르면 400종을
        도는 사이 최신 봉이 다음 분으로 넘어가 뒤쪽 종목이 통째로 missing 이 되고, 과거
        window 재시도도 영영 복구되지 않는다.
        """
        hour = window_end.astimezone(KST).strftime("%H%M%S")
        rows = self._rows(symbol, hour)
        parsed = tuple(candle for row in rows
                       if (candle := parse_minute_row(row, symbol)) is not None)
        if hour != DAY_LAST_HHMMSS:
            # 접기는 **정규장 마감 창**(15:30 에 끝남)을 물을 때만이다. 시간외 세션(720창,
            # 08-02 결정으로 현재 비활성)은 15:30~15:31 창이 따로 있어 단일가 봉이 제 창에
            # 앉는다 — 거기까지 접으면 그 창은 missing, 15:29 창은 중복이다. ⚠️ 시간외를
            # 다시 켜면 같은 세션의 15:29 창(이 분기 밖)이 접혀 5분 롤업이 단일가를 두 번
            # 센다 — 그때는 계획기가 15:30 창을 빼거나 접기를 세션 종류로 갈라야 한다.
            return parsed
        if not any(is_closing_auction(c) and c.traded for c in parsed):
            # 단일가 봉(라벨 15:30)이 응답에 없거나 **거래량 0** 이다. 09-30 프로브: 체결값은
            # 15:30:03~32(랜덤엔드)에 이 행에 잠깐 실렸다가 15:31:00 에 0 자리표시로 리셋되고
            # 그 뒤 한동안(≥6분) 0 으로 남는다 — 당일 TR 은 무거래 분도 행을 주므로 부재도
            # 0 도 "아직 확정 전"이다(정규장 유니버스에서 단일가 체결 0 인 종목은 09-11
            # 실측 0/461). 그대로 돌려주면 15:29 flat 봉이(또는 0 봉이 접혀) 그 창의 봉으로
            # 뽑혀 `no_trade` 로 **성공** 확정된다 — 종가 = 단일가 전 가격, ALPHA-1128 의
            # 모양 그대로다. INCOMPLETE 가 아니라 재청구도 없다. 마감 창만 빼서 missing 으로
            # 낸다 — 틀린 종가보다 없는 종가가 낫다(Rule 12). 판정은 접기 전후 길이가 아니라
            # **체결 있는 행의 존재**다 — 15:29 행이 없으면 접기가 봉을 옮기기만 해 길이가
            # 안 변한다(Codex 지적).
            logger.warning("KIS 분봉 %s 마감 응답에 종가 단일가 체결(라벨 %s)이 아직 없다 — "
                           "마감 창을 missing 으로 낸다", symbol, DAY_LAST_HHMMSS)
            return tuple(c for c in parsed
                         if c.window_end != window_end and not is_closing_auction(c))
        return fold_closing_auction(parsed)

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token if token is not None else self.auth.token()}",
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
            data, used_token = self._call(url, symbol)
            if data.get("rt_cd") == "0":
                output2 = data.get("output2")
                # 빈 list 는 정상(그 창에 봉이 없다 — collector 가 missing 으로 센다).
                # 키 누락·비-list 는 rt_cd=0 인데도 이상이라 형상 위반으로 올린다.
                if not isinstance(output2, list):
                    raise ValueError(f"KIS rt_cd=0 인데 output2 이상: {type(output2).__name__}")
                with self._counter_lock:
                    self._rate_streak = 0  # 성공 = 유량 회복 — 승격 판정을 리셋한다
                return output2
            detail = f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
            if _token_expired(f"{data.get('msg_cd')} {data.get('msg1')}") and not reissued:
                logger.warning("KIS 분봉 토큰 만료 — 캐시 폐기 후 1회 재발급: %s", detail)
                self.auth.invalidate(used_token)   # 이 요청이 쓴 토큰일 때만 — 이미 갱신됐으면 새 토큰 재사용
                reissued = True
                continue
            if data.get("msg_cd") == RATE_MSG_CD:
                if attempt < MAX_RATE_RETRY - 1:
                    with self._counter_lock:
                        self.retry_count += 1
                    self.client._sleep(0.7 * (attempt + 1))
                    continue
                # 재시도 예산까지 유량 소진 — 연속되면 종목이 아니라 앱키 전역의 상태다.
                # 종목별 missing 으로만 접으면 백오프 합(~7초)이 전 종목에 곱해져 window
                # 폭주가 된다(RATE_STREAK_LIMIT 주석의 산술).
                with self._counter_lock:
                    self._rate_streak += 1
                    streak = self._rate_streak
                if streak >= RATE_STREAK_LIMIT:
                    raise KisSourceError(
                        f"KIS 유량 소진 연속 {streak}종목 — 소스 전역 승격: {detail}")
                raise KisUnitError(f"KIS 분봉 {symbol} 유량 소진: {detail}")
            # 종목 단위 오류(없는 종목·일시 거절)는 재시도로 풀릴 수 있다 —
            # 원장이 그 window 를 다시 claim 하는 것으로 재시도된다.
            raise KisUnitError(f"KIS 분봉 {symbol}: {detail}")
        # 마지막 attempt 의 실패는 위에서 raise 되므로 여기 오는 길은 하나다 —
        # **마지막 칸에서 토큰 만료 재발급을 쓴 경우**(continue 로 루프가 끝난다).
        raise KisUnitError(f"KIS 분봉 {symbol}: 재시도 예산({MAX_RATE_RETRY}) 소진")

    def _call(self, url: str, symbol: str) -> tuple[dict, str | None]:
        """1회 요청 → (응답 dict, 이 요청이 쓴 토큰). 소스 전역/종목 단위/형상 위반을 여기서 가른다.

        토큰 조회는 종전처럼 try **안**에 둔다(인증 축 사고의 분류가 바뀌지 않게 — kis_sector_index 주석).
        쓴 토큰을 돌려주는 이유: 만료 실패를 **그 토큰**과 묶어야 동시 요청에서 이미 갱신된 새 토큰을
        지우지 않는다(`KisAuth.invalidate`).
        """
        used_token = None
        try:
            used_token = self.auth.token()
            body = self.client.request("GET", url, headers=self._headers(used_token), decode=True)
        except CallBudgetError:
            raise   # 공유 허용 장애는 원형 그대로 — window 시도 실패로 전파(종목 결손으로 접지 않는다)
        except StopFetch as exc:
            if _token_expired(getattr(exc, "body", "")):
                # 4xx 로 오는 만료 — 캐시를 버리고 올린다. 호출부(_rows)가 다음 attempt 에
                # 새 토큰으로 재발급된 헤더를 만든다.
                logger.warning("KIS 분봉 4xx 토큰 만료 — 캐시 폐기: %s", exc)
                if used_token is not None:
                    self.auth.invalidate(used_token)
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
        return data, used_token


# ── 소급(과거 거래일) 분봉 ────────────────────────────────────────────────

# 페이징 시작·종료 라벨. 정규장 window 는 09:00–15:30 이고 라벨은 구간의 **시작**이라
# 09:00–15:29 가 계획 대상이다. 15:30 봉(종가 단일가)은 계획 밖이지만 15:29 창에 접는다
# (`fold_closing_auction`). 09:00 봉은 개장 단일가 — 첫 창의 실체다.
DAY_LAST_HHMMSS = "153000"
DAY_FIRST_HHMMSS = "090000"
# 페이징 예산. 하루 391분 ÷ 120 = 4콜이면 끝난다 — 그 두 배를 넘겼다면 응답 형상이
# 우리가 아는 것이 아니다. 잘린 하루를 조용히 커밋하느니 그 종목을 실패로 남긴다.
MAX_DAY_PAGES = 8


def fill_no_trade_minutes(
    candles: tuple[Candle, ...], *, until: datetime | None = None
) -> tuple[Candle, ...]:
    """첫 관측 **이후**의 빈 분을 직전 종가 flat·거래량 0 으로 채운다.

    🔴 소급 TR 은 무거래 분 행을 **주지 않는다**(2026-08-08 실측: 08-03 전 종목
    `cntg_vol=0` 행 0건, 저유동 439870 은 390분 중 176행뿐). 당일 TR 은 같은 분을
    `cntg_vol=0`·OHLC flat 으로 주므로, 채우지 않으면 같은 시장 사실이 벤더 경로에 따라
    `no_trade`(성공) 와 `missing`(실패) 으로 갈린다 — 그 window 는 영원히 INCOMPLETE 이고
    한산한 종목은 canonical 에서 하루의 대부분이 사라진다(`price_collect` 4분류 참조).

    ⚠️ **첫 관측 앞은 안 채운다** — 그 종목의 그날 가격을 아직 모른다(직전가가 없다).
    거기서 `missing` 은 사실에 가장 가깝고, 채우면 없는 값을 지어내는 것이다.

    ⚠️ 반대로 **꼬리(`until`)는 채운다**. 마감된 거래일에서는 "마지막 관측 위에 봉이
    없다"가 관측이다 — 페이징이 하루의 끝(15:30)부터 내려오므로 첫 페이지가 그걸 증명한다.
    내부 갭과 인식론적으로 같은 자리라 다르게 다룰 이유가 없다. 실측 규모: 08-03 362종
    중 **3종**만 15:30 전에 끝나고(15:15·15:17·15:19) 합계 39분이다. 안 채우면 그 39
    window 가 362종 중 한 종목 때문에 INCOMPLETE 로 확정되는데, 재청구 대상도 아니라
    (`claim_due_window` 는 DUE·만료 CLAIMED 만 본다) 그대로 굳는다.
    """
    ordered = sorted(candles, key=lambda candle: candle.window_end)
    filled: list[Candle] = []
    for candle in ordered:
        if filled:
            _extend_flat(filled, upto=candle.window_end)
        filled.append(candle)
    if filled and until is not None:
        # half-open 이 아니라 **포함**이다 — 15:30 은 그날 마지막 window 의 끝이다
        _extend_flat(filled, upto=until + timedelta(seconds=INTERVAL_SECONDS))
    return tuple(filled)


def _extend_flat(filled: list[Candle], *, upto: datetime) -> None:
    """`filled` 의 마지막 봉 뒤부터 `upto` **직전**까지 직전 종가 flat 으로 잇는다."""
    previous = filled[-1]
    steps = int((upto - previous.window_end).total_seconds()) // INTERVAL_SECONDS
    close = previous.close
    for step in range(1, steps):
        filled.append(build_candle(
            previous.symbol,
            window_end=previous.window_end + timedelta(seconds=INTERVAL_SECONDS * step),
            span_seconds=INTERVAL_SECONDS,
            values={"open": close, "high": close, "low": close, "close": close},
            volume=Decimal(0),
        ))


class KisHistoricalMinuteClient(KisMinuteClient):
    """지난 거래일 분봉 — `FHKST03010230`. 하루를 한 번 받아 window 별로 나눠 준다.

    당일 클라이언트와 갈리는 축은 다섯이고, 전부 벤더 형상 차이라 여기서 흡수한다
    (아래 셋 + 세션 날짜 고정·범위 밖 window 거부, 그리고 무거래 분 합성은
    `fill_no_trade_minutes`):

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
        self._day_last_window_end = datetime.strptime(
            self._ymd + DAY_LAST_HHMMSS, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        self._days: dict[str, dict[datetime, Candle]] = {}
        # 결정적 실패의 (클래스, 메시지) — 객체를 들고 있으면 재raise 마다 traceback 이
        # 자란다. 전송·유량 실패는 여기 안 들어온다(재시도로 풀리는 축이라 캐시 금지).
        self._failures: dict[str, tuple[type[Exception], str]] = {}

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
        asked = window_end.astimezone(KST)
        if asked.date() != self.session_date:
            # 캐시가 하루에 묶여 있다 — 다른 날짜를 물으면 조용히 남의 날 봉을 준다
            raise ValueError(
                f"{symbol} 소급 조회 날짜 불일치: 클라이언트 {self.session_date}, "
                f"요청 {asked.date()}"
            )
        if not DAY_FIRST_HHMMSS < asked.strftime("%H%M%S") <= DAY_LAST_HHMMSS:
            # 페이징이 09:00–15:30 고정이라 시간외 window 는 **구조적으로** 안 나온다.
            # 빈 결과로 돌려주면 그 window 가 missing 으로 접혀 "벤더가 안 줬다"로 보이고,
            # 세션이 수렴하지 않아 그날 EOD 파생이 통째로 안 돈다. 조회 범위와 세션 계획
            # 범위가 갈린 것은 그 종목 하나의 문제가 아니라 이 백필 전체의 배선 오류다.
            raise KisSourceError(
                f"소급 조회 범위 밖 window: {window_end.isoformat()} — 이 클라이언트는 "
                f"{DAY_FIRST_HHMMSS}~{DAY_LAST_HHMMSS} 만 받는다(시간외 세션 미지원)"
            )
        failure = self._failures.get(symbol)
        if failure is not None:
            # ⚠️ 저장한 예외 **객체**를 다시 raise 하면 `__traceback__` 이 raise 마다
            # 2프레임씩 자란다 — `_candle_for` 가 `ValueError` 를 `logger.exception` 으로
            # 찍으므로 390 window 면 종목 하나가 로그 15만 줄을 쓴다. 새로 만들어 던진다.
            raise failure[0](failure[1])
        day = self._days.get(symbol)
        if day is None:
            # ⚠️ **결정적 실패만 캐시한다.** 같은 응답이 같은 실패를 내는 축(형상 위반·
            # 유일성 위반·하루 미완)은 재청구해도 산출이 없는데, 안 막으면 못 받는 종목
            # 하나가 390 window × 최대 8페이지 = 3,121콜을 앱키 전역 예산에서 태운다.
            #
            # 🔴 전송·유량 실패(`KisUnitError`)는 **캐시하지 않는다**. EGW00201 소진·
            # 5xx·JSON 손상·토큰 만료는 전부 재시도로 풀리는 축인데, 캐시하면 15:40
            # 배치와 겹친 몇 초가 그 종목의 **하루 전체**를 죽인다. 그리고 그건 되돌릴
            # 길이 없다 — INCOMPLETE 로 커밋된 window 는 `claim_due_window` 의 재청구
            # 대상(DUE·만료 CLAIMED)이 아니고, 재계획도 `DO NOTHING` 이라 재기동해도
            # 구멍이 그대로다.
            #
            # 소스 전역 실패(`KisSourceError`)도 캐시하지 않는다 — 종목의 사실이 아니라
            # 설정·유량의 사실이고, 이미 window 를 통째로 세운다.
            try:
                # ⚠️ 순서: 무거래 복원 **뒤에** 접는다. 소급 TR 은 무거래인 15:29 행을 주지
                # 않으므로 먼저 접으면 단일가 봉 하나가 마감 창이 되어 시가·고저가가 단일가로
                # 굳는다 — 실시간(벤더가 15:29 flat 행을 줌)과 다른 봉이 된다(Codex 지적).
                day = {candle.window_end: candle for candle in fold_closing_auction(
                    fill_no_trade_minutes(self._fetch_day(symbol),
                                          until=self._day_last_window_end))}
            except (KisDayIncompleteError, ValueError) as error:
                self._failures[symbol] = (type(error), str(error))
                raise
            self._days[symbol] = day
        candle = day.get(window_end)
        # 빈 결과는 정상이다 — 남는 경우는 **첫 체결 전**뿐이다(꼬리는 15:30 까지
        # 채우고, 그 사이는 합성이 메운다). collector 가 missing 으로 센다.
        return () if candle is None else (candle,)

    def _fetch_day(self, symbol: str) -> tuple[Candle, ...]:
        """그 거래일 전체 봉(무거래 분 제외 — 채우는 건 호출부).

        ⚠️ 하루를 **끝냈다는 증거 없이는 돌려주지 않는다.** 증거는 둘뿐이다 —
        경계를 넘은 페이지(직전 거래일이 섞여 왔다)이거나 09:00 에 닿았거나. 그 밖의
        이유로 페이징이 멈추면(빈 응답·같은 창 반복) 잘린 하루가 되는데, 합성이 사이를
        메우므로 그 결손은 4분류에서 `no_trade` 로 **위장돼** window 가 VALID 로 확정된다.
        그래서 관대한 조기 종료를 두지 않고 예산 소진까지 가서 실패로 낸다.
        """
        hour = DAY_LAST_HHMMSS
        collected: dict[datetime, Candle] = {}
        for _ in range(MAX_DAY_PAGES):
            try:
                page = self._rows(symbol, hour)
            except ValueError as error:
                # 🔴 **봉투 수준** 형상 위반(`output2 이상`·`응답이 객체가 아님`)은 잘린
                # 본문·프록시 오류 페이지 같은 **전송 사고**에 가깝다 — 깨진 JSON 이
                # `KisUnitError` 로 가는 것과 같은 축인데 이 둘만 ValueError 로 남아
                # 있다. 그대로 두면 하루 캐시가 결정적 실패로 오인해(행 형상 위반과
                # 구분이 안 된다) 몇 초의 전송 사고가 그 종목의 하루 전체를 죽인다.
                raise KisUnitError(
                    f"KIS 소급 분봉 {symbol} 응답 봉투 이상: {error}") from error
            # 남의 날 행은 **파싱하지 않는다** — 어차피 버릴 07-31 행 하나의 정합 하자로
            # 08-03 수집이 INVALID 가 되면 안 된다. 대신 dict 여부는 여기서 못박는다:
            # 원시 값을 그냥 만지면(`raw.get`) 비-dict 가 `AttributeError` 로 새는데,
            # collector 는 `ValueError` 만 unit INVALID 로 접어서 종목 하나의 손상 행이
            # window 를 통째로 죽이고 재청구가 같은 예외를 결정적으로 반복한다.
            same_day = []
            for raw in page:
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"{symbol} 소급 분봉 행이 객체가 아니다: {type(raw).__name__}")
                trade_date = raw.get("stck_bsop_date")
                if not is_stamp(trade_date, _YMD_LEN):
                    # ⚠️ 형상 위반을 "남의 날"로 흘리면 **조용히 하루를 잃는다**: 그 행이
                    # 빠져 `len(same_day) < len(page)` 가 성립하고, 그건 경계를 넘었다는
                    # 신호라 페이징이 끝나며, 빈 하루가 **성공으로** 캐시된다. 예외도
                    # ERROR 로그도 없이 362종 전건 missing 인 window 390개가 커밋된다.
                    #
                    # ⚠️ **날짜 형상은 여기서 본다.** 아래 비교는 8자리 `_ymd` 와의
                    # 정확 일치라 형상이 어긋난 날짜는 **구조적으로 "남의 날"**이 되어
                    # `continue` 로 빠진다 — 파서의 가드 중 날짜 쪽은 그래서 이 경로에서
                    # 영영 안 닿는다. 자리수만이 아니라 `is_stamp` **전체**를 여기서
                    # 봐야 한다: `"202608 3"`·`"٢٠٢٦0803"` 은 8자라 자리수만 보면
                    # 통과하고, 그대로 남의 날이 되어 하루가 조용히 절단된다(Codex P2).
                    # ⚠️ 라벨 쪽은 다르다 — 오늘 날짜 행은 여기를 통과해 파서로 가고
                    # 거기서 걸린다. 파서 가드를 죽은 코드로 오해하지 마라.
                    # 형제 `kis_sector_index.candles` 와 같은 조건이다.
                    raise ValueError(
                        f"{symbol} 소급 분봉 행의 거래일 형상이 아니다: {trade_date!r}")
                if trade_date != self._ymd:
                    continue
                candle = parse_minute_row(raw, symbol)
                if candle is None:
                    raise KisDayIncompleteError(
                        f"KIS 소급 분봉 {symbol} {trade_date}: 벤더 데이터 없음")
                if candle.window_end.second:
                    # 분 격자를 벗어난 봉 하나가 그 뒤 합성 전부를 같은 오프셋으로 밀어
                    # 계획된 window 키와 어긋나게 만든다 — 예외도 로그도 없이 그 종목의
                    # 하루가 통째로 missing 이 된다(실측: 09:03:30 봉 하나가 유일한
                    # 관측이면 계획 390개 전부, 앞에 09:01 실봉이 있으면 388개). 벤더가 그렇게 준 적은 없지만 가드가 없었다.
                    # ⚠️ **소급 전용이다.** 파서로 올리면 당일 경로가 남의 행 하나에
                    # 전건 INVALID 로 죽는다 — 논거는 `parse_minute_row` 안에 적어 뒀다.
                    raise ValueError(
                        f"{symbol} 소급 분봉 봉 시각이 분 격자 밖이다: "
                        f"{candle.window_end.isoformat()}")
                same_day.append(candle)
            for candle in same_day:
                previous = collected.get(candle.window_end)
                if previous is not None and previous != candle:
                    # 같은 분에 **다른 값**이 오면 어느 쪽이 참인지 우리가 고를 수 없다.
                    # dict 로 접으면 벤더 행 순서가 값을 정한다(당일 경로는 이 경우를
                    # `select_window_candle` 이 INVALID 로 낸다 — 축을 맞춘다).
                    # 값이 같은 재등장은 페이지 겹침일 뿐이라 통과시킨다 — 그건 데이터
                    # 충돌이 아니고, 막으면 겹쳐 주는 벤더에서 전 종목이 실패한다.
                    raise ValueError(
                        f"{symbol} 가 window {candle.window_end.isoformat()} 에 "
                        f"다른 봉 2건을 줬다 — 유일성 위반")
                collected[candle.window_end] = candle
            if len(same_day) < len(page):
                # 경계를 넘었다(직전 거래일이 섞여 왔다) = 그 날은 다 받았다.
                # ⚠️ 빈 응답은 여기 안 걸린다(0 < 0 은 거짓) — 그게 맞다. 빈 응답은
                # "하루가 끝났다"가 아니라 **아무 정보도 못 얻었다**이고, 같은 창을
                # 예산까지 다시 물은 뒤 실패로 나가야 한다.
                break
            if same_day:
                # 라벨(=창 시작) 기준 — window_end 로 보면 09:00 봉이 "090100" 이라 09:00
                # 도달을 못 보고 한 페이지를 더 물은 뒤 경계 arm 으로만 끝난다
                earliest = min(c.window_start for c in same_day).strftime("%H%M%S")
                if earliest <= DAY_FIRST_HHMMSS:
                    break
                hour = (datetime.strptime(earliest, "%H%M%S")
                        - timedelta(minutes=1)).strftime("%H%M%S")
        else:
            raise KisDayIncompleteError(
                f"KIS 소급 분봉 {symbol} {self._ymd}: 페이지 예산({MAX_DAY_PAGES}) 소진 "
                "— 하루를 끝냈다는 증거가 없다"
            )
        return tuple(collected.values())
