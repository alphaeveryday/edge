"""KIS(한국투자) KRX 업종지수 분봉 소스 어댑터 (ALPHA-887 — 구간 모드 섹터 정본).

API: 업종분봉조회 `inquire-time-indexchartprice`, tr_id `FHKUP03500200`.

`kis_minute.KisMinuteClient` 를 상속한다 — 토큰 재발급·`EGW00201` 재시도·유량 소진의
소스 전역 승격·종목 단위 격리가 전부 같아서, 갈리는 건 엔드포인트·질의 파라미터·행
형상뿐이다. 상주 레인이라 토큰 만료(24h)를 반드시 만나는데 그 처리가 부모에 있다.

주식 분봉(`FHKST03010200`)과 갈리는 축이 다섯이다. 전부 2026-08-08~09 라이브 실측이다:

1. 🔴 **KIS 지수코드는 KRX 업종코드가 아니다.** KIS 의 `U` 네임스페이스는 자체 조밀
   번호다(`0001`=코스피종합 · `1001`=코스닥 · `2001`=KOSPI200, 업종은 `0xxx`=KOSPI ·
   `1xxx`=KOSDAQ). KRX 코드를 그대로 넣으면 **`rt_cd=0` 에 그럴듯한 한글 업종명이 담긴
   남의 지수**가 온다 — 45종 중 43종이 정상 격자를 채워 맞는 것처럼 보인다. 매핑은
   `[minute_sector_index.index_map]` 이 진다(일봉 종가 99거래일 전건 대조로 확정).
2. **`FID_INPUT_HOUR_1` 이 시각이 아니라 간격(초)** 이다. 주식 당일 TR 은 이 자리에
   HHMMSS 로 창의 **끝**을 지정하는데, 여기서는 창을 고를 수단이 아예 없다 — 응답은
   항상 "지금 기준 최근 100봉"이다(`FID_INPUT_DATE_1`·`FID_INPUT_HOUR_2` 를 넣어도
   응답이 한 바이트도 안 바뀐다. `tr_cont` 도 비어 페이지네이션이 없다).
3. ⭐ **`stck_cntg_hour` 가 구간의 시작이다.** 주식 당일 TR(구간 **끝**)과 반대 축이다.
   1005 의 1분봉 5개를 5분봉에 합성해 대조했다 — 시작 가설 13건 연속 일치, 끝 가설
   13건 전건 불일치. 뒤집으면 전 구간이 정확히 1분 밀린 채 조용히 커밋된다.
4. **응답에 봉이 아닌 행이 섞인다** — `stck_cntg_hour` 가 `999999`·`888888` 인 두 줄이
   **날짜마다** 붙는다(OHLC 넷이 전부 그날 종가인 요약 행). 페이지는 102행 = 봉 100 +
   센티넬 2. 위치가 아니라 라벨로 걸러야 한다.
5. **필드명이 다르다** — `bstp_nmix_prpr/oprc/hgpr/lwpr`(주식은 `stck_*`). 그래서
   `kis_minute.parse_minute_row` 를 쓸 수 없다.

🔴 **소급 경로를 만들지 않는다.** 두 길이 다 막혀 있다: 이 TR 은 창 이동 수단이 없고,
소급 TR `FHKST03010230` 에 `div=U` 를 주면 `rt_cd=0` 인데 **일봉이 온다**(날짜당 3행,
시각 라벨 `090000`·`888888`·`999999`, `090000` 행의 OHLC 가 하루 전체 범위 —
20250801: o=4001.71 l=3856.00). 필드명이 분봉과 같아 파서가 그대로 먹으면 일봉이 분봉
파티션에 앉는다. **만들 자리가 없으면 그 오염 경로도 없다** — 이 파일에 날짜 축을
더하려는 다음 사람은 이 문단을 먼저 반증해라.

한 콜이 100분치라 마감 후 배치로는 09:00~13:50 이 영구 소실이다. 장중 폴링(1분 레인)이
유일한 경로인 이유가 그것이다.
"""

from __future__ import annotations

import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

from .candle import Candle, build_candle, to_decimal
from .kis_minute import KisMinuteClient

logger = logging.getLogger(__name__)

TR_ID_INDEX_MINUTE = "FHKUP03500200"
PATH_INDEX_MINUTE = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
# 주식(kis_minute.MARKET_DIV="J")과 갈린다 — 지수는 "U" 다. 실측으로 확정한 값이다.
MARKET_DIV_INDEX = "U"
# 기본 지수(0). **빈 문자열이 아니라 값이 있어야 한다** — 키가 아예 없으면
# `rt_cd=2 ERROR INPUT FIELD NOT FOUND [FID_ETC_CLS_CODE]` 로 전건 튕긴다.
ETC_CLS_CODE = "0"
# 이 키도 **필수**다(빠지면 같은 INPUT FIELD NOT FOUND). Y/N 응답이 동일해 값은 아무
# 의미가 없지만, 없으면 못 부른다 — 무의미와 불요는 다르다.
PW_DATA_INCU_YN = "Y"

# 봉이 아닌 행의 시각 라벨. 날짜마다 한 쌍씩 붙는다(요약 행 — OHLC 넷이 전부 그날 종가).
# **이름으로 안다**는 것이 중요하다: 파싱 실패로 흘리면 벤더가 진짜로 형상을 바꿨을 때
# (라벨 규약 변경) 그 신호가 이 상수 뒤에 숨는다. 아는 비-봉과 모르는 이탈을 가른다.
SENTINEL_LABELS = frozenset({"999999", "888888"})
# 이 레인의 window 길이(초). **벤더 어휘가 아니라 우리 격자다** — 라벨 간격이 이와 같아야
# 라벨이 window 격자에 얹힌다(`inav_collect._LANE_INTERVAL_SEC` 와 같은 축).
LANE_INTERVAL_SEC = 60

KST = timezone(timedelta(hours=9))

_INDEX_FIELDS = (("open", "bstp_nmix_oprc"), ("high", "bstp_nmix_hgpr"),
                 ("low", "bstp_nmix_lwpr"), ("close", "bstp_nmix_prpr"))


def parse_index_row(raw: dict, symbol: str, *, interval_sec: int) -> Candle:
    """`output2` 행 하나 → `Candle`. 필드가 빠지거나 형이 다르면 즉시 raise 한다.

    ⚠️ **`stck_cntg_hour` 는 구간의 시작이다**(모듈 도크스트링 3번) — 주식과 반대라
    `window_end = 라벨 + interval` 이다. `kis_minute.parse_minute_row` 가 같은 이름의
    필드를 끝으로 읽으므로, 두 파서를 한 벌로 합치면 한쪽이 반드시 1분 밀린다.

    응답에 시간대 표기가 없어 **KST 로 고정**한다(KRX 로컬 지수 전용 TR).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{symbol} 지수 분봉 행이 객체가 아니다: {type(raw).__name__}")
    day, label = raw.get("stck_bsop_date"), raw.get("stck_cntg_hour")
    if not isinstance(day, str) or not isinstance(label, str):
        raise ValueError(f"{symbol} 지수 분봉 행에 날짜·시각이 없다: {raw!r}")
    try:
        start = datetime.strptime(day + label, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise ValueError(f"{symbol} 지수 분봉 시각 형식 오류: {day!r} {label!r}") from error
    if start.second:
        # 분 격자를 벗어난 라벨은 그대로 두면 어느 window 에도 안 맞아 **전건 missing** 이
        # 되는데, 원장에는 "벤더가 안 줬다"로 보여 원인이 형상 변화임을 가린다.
        raise ValueError(f"{symbol} 지수 분봉 라벨이 분 격자 밖이다: {start.isoformat()}")
    values = {name: to_decimal(raw.get(key), key, symbol) for name, key in _INDEX_FIELDS}
    # 지수의 `cntg_vol` 은 그 구간의 체결 수량이다. 0 인 분이 정상이라 `no_trade` 축은
    # 주식과 같이 간다(`price_collect` 4분류를 벤더별로 나누지 않는다).
    volume = to_decimal(raw.get("cntg_vol"), "cntg_vol", symbol)
    return build_candle(symbol, window_end=start + timedelta(seconds=interval_sec),
                        span_seconds=interval_sec, values=values, volume=volume)


class KisSectorIndexClient(KisMinuteClient):
    """업종지수 분봉 조회 — 지수당 1콜(100분치). 새 HTTP·재시도 코드는 없다.

    부모와 갈리는 것은 `_url`(질의 파라미터)과 `candles`(창을 못 고른다) 둘뿐이다.
    """

    tr_id = TR_ID_INDEX_MINUTE
    path = PATH_INDEX_MINUTE

    def __init__(self, app_key: str, app_secret: str, client, *, env: str = "prod",
                 interval_sec: int = LANE_INTERVAL_SEC):
        super().__init__(app_key, app_secret, client, env)
        if interval_sec != LANE_INTERVAL_SEC:
            # **레인 상수**에 건다(`inav_collect.KisInavCollector` 와 같은 가드). 다른
            # 간격이면 라벨이 1분 격자에 아예 안 얹혀 전 지수가 매 window missing 인데,
            # 그 상태는 원장에 "벤더가 안 준다"로 보여 원인이 설정임을 가린다. 기동에서 막는다.
            raise SystemExit(
                f"1분 레인의 업종지수는 interval_sec={LANE_INTERVAL_SEC} 만 쓴다"
                f"(받은 값 {interval_sec}) — 다른 간격이면 라벨이 1분 격자에 안 맞아"
                " 전 지수가 매 window missing 이 된다"
            )
        self.interval_sec = interval_sec

    def _url(self, symbol: str, hour: str) -> str:
        """질의 URL. **`hour` 는 시각이 아니라 간격(초)** 이다 — 부모와 축이 다르다.

        인자 이름을 부모에서 물려받는 이유는 그 자리가 벤더의 같은 키
        (`FID_INPUT_HOUR_1`)이기 때문이다. 의미는 TR 이 정한다.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV_INDEX,
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": hour,
            "FID_ETC_CLS_CODE": ETC_CLS_CODE,
            "FID_PW_DATA_INCU_YN": PW_DATA_INCU_YN,
        }
        return self.base + self.path + "?" + urllib.parse.urlencode(params)

    def candles(self, symbol: str, *, window_end: datetime) -> tuple[Candle, ...]:
        """그 거래일의 봉 전부(최근 100분치 중). 창은 **못 고른다**.

        부모는 `window_end` 를 벤더에 보내 창을 고정하는데(400종을 도는 사이 최신 봉이
        넘어가는 것을 막는 장치다) 이 TR 에는 그 수단이 없다. 대신 페이지가 100분치라
        같은 window 를 100분 안에 다시 물어도 그 봉이 아직 페이지에 있다 — 창 고정이
        하던 일을 페이지 폭이 대신한다.

        **남의 날 행은 파싱하지 않는다**(`KisHistoricalMinuteClient._fetch_day` 와 같은
        축). 장 초반에는 페이지가 직전 거래일 꼬리까지 뻗는데(09:30 의 페이지는 07:50
        부터다), 어차피 버릴 그 행 하나의 정합 하자로 오늘 수집이 통째로 INVALID 가
        되면 안 된다.
        """
        asked = (window_end - timedelta(seconds=self.interval_sec)).astimezone(KST)
        ymd = asked.strftime("%Y%m%d")
        rows = self._rows(symbol, str(self.interval_sec))
        bars: dict[datetime, Candle] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError(
                    f"{symbol} 지수 분봉 행이 객체가 아니다: {type(raw).__name__}")
            label = raw.get("stck_cntg_hour")
            if label in SENTINEL_LABELS:
                # 계약이다(날짜마다 한 쌍) — 조용히 버린다. 아래 형식 이탈과 **가르는**
                # 이유는 도크스트링 4번에 있다: 아는 비-봉을 모르는 이탈과 한 통에 담으면
                # 벤더의 라벨 규약 변경이 정상 소음에 묻힌다.
                continue
            if raw.get("stck_bsop_date") != ymd:
                continue
            candle = parse_index_row(raw, symbol, interval_sec=self.interval_sec)
            previous = bars.get(candle.window_end)
            if previous is not None and previous != candle:
                # 같은 분에 **다른 값**이 오면 어느 쪽이 참인지 우리가 고를 수 없다.
                # dict 로 접으면 벤더 행 순서가 값을 정한다(소급 경로가 같은 조건을
                # 유일성 위반으로 내는 것과 축을 맞춘다). 값이 같은 재등장은 통과시킨다.
                raise ValueError(
                    f"{symbol} 가 window {candle.window_end.isoformat()} 에 "
                    f"지수 봉 2건을 줬다 — 유일성 위반")
            bars[candle.window_end] = candle
        if not bars:
            # 빈 결과는 정상일 수 있다(개장 직후 첫 봉 전) — 실패로 올리지 않는다.
            # 다만 조용하지는 않게 남긴다: **틀린 지수코드도 여기로 온다**(KIS 가
            # `rt_cd=0` 에 빈 `output2` 를 준다, 도크스트링 1번). 그 둘은 지속 여부로만
            # 갈리므로 판정하지 않고 사실만 적는다(Rule 12).
            logger.info("지수 %s 의 %s 봉이 페이지에 0건이다(응답 %d행)",
                        symbol, ymd, len(rows))
        return tuple(bars.values())
