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
   센티넬 2 이고, 응답이 두 날짜에 걸치면 센티넬도 두 쌍이라 104행이다. 위치가 아니라
   라벨로 걸러야 한다.
5. **필드명이 다르다** — `bstp_nmix_prpr/oprc/hgpr/lwpr`(주식은 `stck_*`). 그래서
   `kis_minute.parse_minute_row` 를 쓸 수 없다.
6. 🔴 **`cntg_vol` 은 "거래가 있었나"의 답이 아니다.** 지수는 자기가 체결되지 않고
   구성종목이 체결된다 — 그래서 거래량이 0 인 분에도 지수가 움직인다. 실측(45종 ×
   100봉 = 4,500봉, 2026-08-09): 거래량>0 이 3,806 · **거래량 0 인데 OHLC 가 움직인
   것이 175(3.9%)** · 거래량 0 & flat 이 519. 저유동 업종(1007 종이·목재)에서는 그게
   상시다.

   ⚠️ **그래서 `price_collect` 의 4분류를 그대로 쓰면 안 된다.** 그쪽은 `volume==0`
   인데 flat 이 아닌 봉을 `invalid` 로 접고(`collect_units`, "우리가 아는 형상이
   아니다"), `status_of` 는 invalid 하나로 window 전체를 INVALID 로 만든다 — 이
   dataset 은 **매 window 가 INVALID** 가 되어 단 한 칸도 확정되지 않는다. 주식에서
   그 규칙이 옳은 이유(체결이 없으면 가격이 못 움직인다)가 지수에는 성립하지 않는다.
   소비단은 iNAV 와 같이 **`no_trade` 축 없이** 가야 한다(`inav_collect` 는 스냅샷이라
   그 축이 없고, 여기는 축이 거짓이라 없다 — 결론이 같다).

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

import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

from .candle import Candle, build_candle, to_decimal
from .kis_minute import KisMinuteClient, KisUnitError

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
# `stck_bsop_date` 자리수(YYYYMMDD). 파서와 창 필터가 **같은 값**을 봐야 한다 — 갈리면
# 한쪽만 통과하는 형상이 생기고, 그 틈으로 새는 것은 늘 조용한 쪽이다.
_YMD_LEN = 8
# `stck_cntg_hour` 자리수(HHMMSS). 같은 이유로 필요하다 — 위 상수와 **함께** 봐야
# `strptime` 의 연접 파싱이 한쪽 자리를 훔쳐가지 못한다.
_HHMMSS_LEN = 6

KST = timezone(timedelta(hours=9))

_INDEX_FIELDS = (("open", "bstp_nmix_oprc"), ("high", "bstp_nmix_hgpr"),
                 ("low", "bstp_nmix_lwpr"), ("close", "bstp_nmix_prpr"))


def parse_index_row(raw: dict, unit_id: str, *, interval_sec: int) -> Candle:
    """`output2` 행 하나 → `Candle`. 필드가 빠지거나 형이 다르면 즉시 raise 한다.

    ⚠️ **`unit_id` 는 우리 축(KRX 업종코드)이다** — 질의에 쓴 KIS 코드가 아니다.
    `Candle.symbol` 이 그대로 `price_collect.record_of` 의 `unit_id` 가 돼 canonical 에
    실리는데, 거기 벤더 코드가 앉으면 일봉 `sector_index`(KRX 코드 축)와 **어떤 조인에도
    안 걸린다**. 번역은 `KisSectorIndexClient.candles` 가 지고 여기로는 우리 축만 온다
    (`inav_collect.record_of` 가 `unit_id` 를 따로 받는 것과 같은 이유).

    ⚠️ **`stck_cntg_hour` 는 구간의 시작이다**(모듈 도크스트링 3번) — 주식과 반대라
    `window_end = 라벨 + interval` 이다. `kis_minute.parse_minute_row` 가 같은 이름의
    필드를 끝으로 읽으므로, 두 파서를 한 벌로 합치면 한쪽이 반드시 1분 밀린다.

    응답에 시간대 표기가 없어 **KST 로 고정**한다(KRX 로컬 지수 전용 TR).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"{unit_id} 지수 분봉 행이 객체가 아니다: {type(raw).__name__}")
    day, label = raw.get("stck_bsop_date"), raw.get("stck_cntg_hour")
    if not isinstance(day, str) or not isinstance(label, str):
        raise ValueError(f"{unit_id} 지수 분봉 행에 날짜·시각이 없다: {raw!r}")
    # 자리수를 **양쪽 다** 못박는다 — `strptime("%Y%m%d%H%M%S")` 는 연접 문자열 하나를
    # 보므로, 한쪽이 짧으면 다른 쪽 자리를 잘라 먹고도 파싱에 성공한다. 실측:
    #   "20260807" + "1030"  → 10:03:00   (4자리 라벨이 조용히 다른 분이 된다)
    #   "20260807" + "30000" → 03:00:00   (선행 0 이 잘린 라벨 — `kis_inav._time_stamp`
    #                                      가 `"9300"` 으로 적어둔 그 함정과 같다)
    # 5자리는 선행 0 을 되붙이면 **복구도 가능해 보인다**("93000"→09:30:00). 그래도
    # 거부한다: 4자리와 구분할 근거가 없고, 추정해서 맞히면 틀렸을 때 신호가 안 남는다.
    # 🔴 이건 **값이 조용히 틀리는** 부류다: 결과의 `second` 가 0 이라 아래 격자 가드도
    # 안 걸리고, 그 봉은 멀쩡한 다른 window 에 앉아 VALID 로 확정된다. 한쪽만 막으면
    # 나머지 문으로 그대로 들어온다(날짜만 막았다가 Codex 리뷰에서 잡혔다).
    # 공백 패딩과 비-ASCII 숫자도 여기서 막는다 — `strptime` 이 둘 다 관대하게 받아
    # 값은 **맞게** 읽히고, 그래서 벤더의 포맷 변경이 조용히 흡수된다. 두 술어가 각각
    # 다른 문을 막는다. 하나만 두면 나머지가 열린다(실측):
    #   · `isdecimal()` → 공백 패딩. `%Y`·`%H`·`%d` 에 `" \d"` 대안이 있어
    #     `" 93000"`→09:30:00, `"202608 3"`→08-03 이 통과한다. **공백은 ASCII 라
    #     `isascii()` 로는 못 막는다.**
    #   · `isascii()` → 비-ASCII 숫자. `\d` 가 유니코드 Nd 라 `"1٠3000"`→10:30:00,
    #     `"٢٠٢٦0803"`→2026 이 통과한다. 이쪽은 `isdecimal()` 이 True 라 못 막는다.
    # 날짜와 라벨을 **연접해서** 본다 — 한쪽만 보면 다른 쪽 문이 그대로 열린다.
    stamp = day + label
    if (len(day) != _YMD_LEN or len(label) != _HHMMSS_LEN
            or not stamp.isascii() or not stamp.isdecimal()):
        # ⚠️ "자리수"라고 쓰지 않는다 — `" 93000"` 은 6자다. 운영자가 로그에서 자리수를
        # 세어 보고 "가드가 오작동했다"로 읽으면 진짜 원인(포맷 변경)을 놓친다.
        raise ValueError(
            f"{unit_id} 지수 분봉 날짜·시각 형상이 아니다: {day!r} {label!r}")
    try:
        start = datetime.strptime(day + label, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError as error:
        raise ValueError(f"{unit_id} 지수 분봉 시각 형식 오류: {day!r} {label!r}") from error
    if start.second:
        # 분 격자를 벗어난 라벨은 그대로 두면 어느 window 에도 안 맞아 **전건 missing** 이
        # 되는데, 원장에는 "벤더가 안 줬다"로 보여 원인이 형상 변화임을 가린다.
        raise ValueError(f"{unit_id} 지수 분봉 라벨이 분 격자 밖이다: {start.isoformat()}")
    values = {name: to_decimal(raw.get(key), key, unit_id) for name, key in _INDEX_FIELDS}
    # ⚠️ 벤더가 준 값을 그대로 싣는다(bronze 무변형). 다만 **이 값은 "거래가 있었나"의
    # 답이 아니다** — 모듈 도크스트링 6번의 실측을 보고, `Candle.traded` 를 그 뜻으로
    # 읽는 소비자를 붙이지 마라. 0 인 분에도 지수는 움직인다.
    volume = to_decimal(raw.get("cntg_vol"), "cntg_vol", unit_id)
    return build_candle(unit_id, window_end=start + timedelta(seconds=interval_sec),
                        span_seconds=interval_sec, values=values, volume=volume)


class KisSectorIndexClient(KisMinuteClient):
    """업종지수 분봉 조회 — 지수당 1콜(100분치). 새 HTTP·재시도 코드는 없다.

    부모와 갈리는 것은 `_url`(질의 파라미터)·`candles`(창을 못 고른다), 그리고
    **코드 번역**이다.

    ⚠️ **번역이 이 클래스 안에서 끝난다.** 부모(가격 레인)는 `unit_id` 가 곧 벤더
    질의 심볼이라 이 축이 없는데, 지수는 둘이 다르다. 경계를 밖으로 미루면 두 사고 중
    하나가 반드시 난다 — collector 가 KRX 코드를 그대로 `FID_INPUT_ISCD` 에 실어
    **남의 지수를 조용히 받거나**(모듈 도크스트링 1번), 번역은 하되 `Candle.symbol` 에
    KIS 코드가 남아 canonical 의 `unit_id` 가 벤더 코드가 된다(일봉 `sector_index` 와
    조인 불가). 들어오는 것도 나가는 것도 **KRX 업종코드**이고, KIS 코드는 URL 안에서만
    산다.
    """

    tr_id = TR_ID_INDEX_MINUTE
    path = PATH_INDEX_MINUTE

    def __init__(self, app_key: str, app_secret: str, client,
                 index_map: dict[str, str], *, env: str = "prod",
                 interval_sec: int = LANE_INTERVAL_SEC):
        super().__init__(app_key, app_secret, client, env)
        if not index_map:
            # 빈 맵으로 기동하면 전 unit 이 "맵에 없다"로 떨어져 매 window 가 죽는다.
            # 설정 결손을 수집 실패로 위장하지 않는다(Rule 12).
            raise SystemExit(
                "업종지수 index_map 이 비었다 — [minute_sector_index.index_map] 을 주입해라")
        # 우리 축(KRX 업종코드) → KIS 질의 코드. 사본을 든다: 호출자가 나중에 고쳐도
        # 이 클라이언트가 도는 중에 질의 대상이 바뀌면 안 된다.
        self.index_map = dict(index_map)
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

    def candles(self, unit_id: str, *, window_end: datetime) -> tuple[Candle, ...]:
        """그 거래일의 봉 전부(최근 100봉 중). 창은 **못 고른다**.

        `unit_id` 는 **KRX 업종코드**다(클래스 도크스트링) — 여기서 KIS 코드로 번역해
        질의하고, 돌려주는 `Candle.symbol` 은 다시 KRX 업종코드다.

        부모는 `window_end` 를 벤더에 보내 창을 고정하는데(400종을 도는 사이 최신 봉이
        넘어가는 것을 막는 장치다) 이 TR 에는 그 수단이 없다. 대신 페이지가 100분치라
        같은 window 를 100분 안에 다시 물어도 그 봉이 아직 페이지에 있다 — 창 고정이
        하던 일을 페이지 폭이 대신한다.

        **남의 날의 `값` 하자는 오늘을 죽이지 않는다**(`KisHistoricalMinuteClient._fetch_day`
        와 같은 축). 페이지는 벽시계 100분이 아니라 **최근 100봉**이라 장 초반에는 직전
        거래일 꼬리까지 뻗는다(09:30 이면 오늘 30봉 + 전 거래일 마지막 70봉). 어차피
        버릴 그 행 하나의 OHLC 정합 하자로 오늘 수집이 통째로
        INVALID 가 되면 안 된다. 단 **형상 위반은 날짜와 무관하게 실패시킨다** — 행이
        dict 가 아니거나 거래일이 문자열이 아니면 그 행이 어느 날 것인지조차 모르고,
        그건 벤더가 스키마를 바꿨다는 신호다(형제가 같은 조건을 raise 로 낸다).

        ⚠️ **봉투 수준 이상은 `KisUnitError` 로 되감는다.** 부모 `_rows` 는 `rt_cd=0`
        인데 `output2` 가 list 가 아닌 경우를 맨 `ValueError` 로 내는데, 그건 잘린 본문·
        프록시 오류 페이지 같은 **전송 사고**다. 그대로 새면 collector 가 `INVALID` 로
        접고 `status_of` 가 **window 전체**를 INVALID 로 만드는데, INVALID 는 재청구
        대상이 아니고 이 소스는 소급이 불가라 그 1분이 45종 전체에 대해 영구히 없어진다
        (`inav_collect._rows_for` 가 같은 이유로 봉투를 재시도 축에 두었다).
        """
        kis_code = self.index_map.get(unit_id)
        if kis_code is None:
            # 유니버스와 매핑이 갈렸다 — **재시도로 안 풀린다**(매 window 반복). 재시도
            # 축(missing)으로 접으면 벤더가 안 준 것처럼 보여 원인이 설정임을 가린다
            # (`inav_collect._rows_for` 가 같은 조건을 같은 축으로 낸다).
            raise ValueError(
                f"{unit_id} 는 index_map 에 없다 — 유니버스와 수집 매핑이 갈렸다")
        asked = (window_end - timedelta(seconds=self.interval_sec)).astimezone(KST)
        ymd = asked.strftime("%Y%m%d")
        try:
            rows = self._rows(kis_code, str(self.interval_sec))
        except json.JSONDecodeError:
            # ⚠️ 토큰 발급 응답의 JSON 손상은 여기로 오면 안 된다. `_call` 이
            # `self._headers()` 를 try 안에서 평가하고 그 경로가 `KisAuth.token()` →
            # `json.loads` 라, 아래 `except ValueError` 가 **인증 축 사고까지** 삼킨다
            # (`JSONDecodeError` 는 `ValueError` 서브클래스다). 삼키면 메시지가 엉뚱한
            # 엔드포인트를 가리키고, 45 unit 이 매 window 토큰 발급을 다시 두드린다.
            # 부모와 같은 축(ValueError)으로 그대로 올린다.
            raise
        except ValueError as error:
            raise KisUnitError(
                f"KIS 지수 분봉 {unit_id}({kis_code}) 응답 봉투 이상: {error}") from error
        bars: dict[datetime, Candle] = {}
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError(
                    f"{unit_id} 지수 분봉 행이 객체가 아니다: {type(raw).__name__}")
            label = raw.get("stck_cntg_hour")
            if label in SENTINEL_LABELS:
                # 계약이다(날짜마다 한 쌍) — 조용히 버린다. 아래 형식 이탈과 **가르는**
                # 이유는 도크스트링 4번에 있다: 아는 비-봉을 모르는 이탈과 한 통에 담으면
                # 벤더의 라벨 규약 변경이 정상 소음에 묻힌다.
                continue
            trade_date = raw.get("stck_bsop_date")
            if not isinstance(trade_date, str) or len(trade_date) != _YMD_LEN:
                # ⚠️ 형상 위반을 "남의 날"로 흘리면 **조용히 하루를 잃는다**: 전 행이
                # `continue` 로 떨어져 `bars` 가 비고, 그건 예외도 ERROR 로그도 없이
                # 45종 전건 missing 인 INCOMPLETE window 로 굳는다 — 원장에는 "벤더가
                # 안 줬다"로 보여 원인이 스키마 변화임을 가린다. 이 파일의 `start.second`
                # 가드와 같은 논거이고, 형제(`_fetch_day`)도 같은 조건을 raise 로 낸다.
                #
                # ⚠️ **날짜 자리수는 여기서 본다.** 아래 비교는 8자리 `ymd` 와의 정확
                # 일치라 짧은 날짜는 구조적으로 "남의 날"이 되어 `continue` 로 빠진다 —
                # 파서의 자리수 가드 중 **날짜 쪽**은 그래서 이 경로에서 영영 안 닿는다
                # (형이 맞는 형상 위반이 조용한 문으로 샌다).
                # ⚠️ 라벨 쪽은 다르다 — 그 날짜 행은 여기를 통과해 파서로 가고 거기서
                # 걸린다. 파서 가드를 죽은 코드로 오해하지 마라.
                raise ValueError(
                    f"{unit_id} 지수 분봉 행의 거래일 형상이 아니다: {trade_date!r}")
            if trade_date != ymd:
                continue
            candle = parse_index_row(raw, unit_id, interval_sec=self.interval_sec)
            previous = bars.get(candle.window_end)
            if previous is not None and previous != candle:
                # 같은 분에 **다른 값**이 오면 어느 쪽이 참인지 우리가 고를 수 없다.
                # dict 로 접으면 벤더 행 순서가 값을 정한다(소급 경로가 같은 조건을
                # 유일성 위반으로 내는 것과 축을 맞춘다). 값이 같은 재등장은 통과시킨다.
                raise ValueError(
                    f"{unit_id} 가 window {candle.window_end.isoformat()} 에 "
                    f"지수 봉 2건을 줬다 — 유일성 위반")
            bars[candle.window_end] = candle
        if not bars:
            # 빈 결과는 정상일 수 있다(개장 직후 첫 봉 전) — 실패로 올리지 않는다.
            # 다만 조용하지는 않게 남긴다: **틀린 지수코드도 여기로 온다**(KIS 가
            # `rt_cd=0` 에 빈 `output2` 를 준다, 도크스트링 1번). 그 둘은 지속 여부로만
            # 갈리므로 판정하지 않고 사실만 적는다(Rule 12).
            logger.info("지수 %s(%s) 의 %s 봉이 페이지에 0건이다(응답 %d행)",
                        unit_id, kis_code, ymd, len(rows))
        return tuple(bars.values())
