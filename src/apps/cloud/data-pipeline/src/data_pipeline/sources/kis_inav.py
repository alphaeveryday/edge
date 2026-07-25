"""KIS(한국투자) 국내 ETF iNAV 소스 어댑터 (ALPHA-555 — 장중 추정 NAV raw 수집).

API: ETF/ETN NAV비교추이(분) nav-comparison-time-trend, tr_id FHPST02440100.

일별 NAV(`kis_nav.py`, FHPST02440200)와 **다른 축**이다 — 저건 거래일 grain 의 종가 확정
NAV 고 이건 장중 시각 grain 의 추정 NAV 다. 기존 `etf_nav` 테이블(PK `(etf, trade_date)`)
에는 담을 축이 없어 dataset 을 분리한다.

라이브 실측(2026-07-25)이 확정한 계약 — 셋 다 일별 API 와 갈린다:

1. **`FID_COND_MRKT_DIV_CODE` 는 `"E"`** 다. 일별은 `"J"` 인데 여기에 `"J"` 를 주면
   `rt_cd=2 ERROR INVALID FID_COND_MRKT_DIV_CODE` 로 전건 튕긴다.
2. **응답이 항상 30행 고정**이다. 조회 창 = `FID_HOUR_CLS_CODE`(초) × 30 — cls=60 이면
   최근 30분치. 행 수로 창을 넓힐 수단은 없다.
3. **소급 조회가 안 된다.** `FID_INPUT_HOUR_1` 을 어떤 값으로 줘도 응답이 동일하고
   (파라미터 무시), `tr_cont`·ctx 도 없다 — 항상 "지금 기준 최근 30행"이다. 그래서 날짜창
   인자를 받지 않고 질의에도 날짜를 싣지 않는다(무시되는 파라미터를 실으면 소급이 되는 줄
   착각하게 된다). **놓친 구간은 영구 유실**이라, 갭 방어는 폴링 주기를 창보다 짧게 잡아
   겹치는 것뿐이다(스케줄 소관, ALPHA-556).

**응답에 날짜 필드가 없다** — `bsop_hour`(HHMMSS)만 온다(일별의 `stck_bsop_date` 에 해당
하는 게 없다). 거래일은 수집 시각으로 붙일 수밖에 없는데, KIS 는 휴장일에도 빈 응답이 아니라
**직전 거래일 데이터를 그대로 반복**한다(토요일 질의에 직전 금요일 데이터가 온 것이 실측
증거). 그래서 오늘 데이터가 없는 시점에 돌리면 옛 값에 오늘 날짜가 붙는 유령 as-of 가 된다
(ALPHA-387 의 as-of 오정렬과 같은 함정) — `skip_reason` 이 거래일·개장 이후만 통과시켜 막고
(ALPHA-557), 그래도 남는 오염은 부모가 붙이는 `fetched_at` 으로 교정한다.

`KisNavSource` 를 상속한다 — 토큰 발급·단축코드 파생·`EGW00201` 재시도·rt_cd 판정·ETF 단위
실패 격리가 전부 동일해서, 갈리는 건 엔드포인트와 질의 파라미터뿐이다. 부모가 그 둘을 훅
(`tr_id`·`path`·`_query_params`·`_row_defect`·`_extra_provenance`)으로 열어둬 오류 처리 코드는
한 벌만 산다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from ..config import KisNavSource as KisNavSourceConfig
from ..ops.trading_calendar import is_trading_day
from .http import PoliteClient
from .kis_nav import KisNavSource

TR_ID_NAV_MINUTE = "FHPST02440100"
PATH_NAV_MINUTE = "/uapi/etfetn/v1/quotations/nav-comparison-time-trend"
# 일별(kis_nav.MARKET_DIV="J")과 갈린다 — 실측으로 확정한 값이다. 바꾸면 전건 rt_cd=2.
MARKET_DIV_INAV = "E"
# FID_HOUR_CLS_CODE(초). 1콜=30행 고정이라 조회 창 = 이 값 × 30.
# 60(=1분, 창 30분)에서 시작한다 — 갱신 주기가 30초 이하인 것까지만 실측으로 확정됐고,
# 그보다 잘게 의미가 있는지는 장중 cls=1 측정이 정한다(ALPHA-556 열린 결정).
DEFAULT_INTERVAL_SEC = 60
ROWS_PER_CALL = 30  # 응답 고정 행 수(실측) — 창 = interval_sec × 이 값.
# 행 식별에 필요한 필드 — 시각 축(bsop_hour)과 값(nav). 없으면 저장해도 못 쓴다.
REQUIRED_ROW_FIELDS = ("bsop_hour", "nav")

KST = timezone(timedelta(hours=9))
# 정규장 개장 시각(KST). 이 전에는 **오늘 iNAV 가 아직 존재하지 않는다** — 그때 부르면 KIS 가
# 빈 응답이 아니라 직전 거래일 값을 주고, 응답에 날짜가 없어 그게 오늘 것으로 라벨된다.
MARKET_OPEN = time(9, 0)


def _is_blank(value: object) -> bool:
    """키가 없거나(None) 공백뿐인 문자열이면 결측.

    falsy 판정(`value or ""`)을 쓰면 **숫자 0 이 결측이 된다** — nav 가 0 으로 오거나 KIS 가
    수치 타입으로 바뀌면 멀쩡한 원본이 격리돼 사라진다. iNAV 는 소급 조회가 안 돼 그 유실이
    영구적이라(ALPHA-555), 결측 판정은 값이 아니라 **존재 여부**만 본다.
    """
    return value is None or (isinstance(value, str) and not value.strip())


class KisInavSource(KisNavSource):
    """장중 iNAV 어댑터. 날짜창 대신 간격(초)을 받는다 — 창은 간격 × 30 으로 정해진다."""

    tr_id = TR_ID_NAV_MINUTE
    path = PATH_NAV_MINUTE

    def __init__(
        self,
        config: KisNavSourceConfig,
        etf_map: dict[str, str],
        client: PoliteClient,
        interval_sec: int = DEFAULT_INTERVAL_SEC,
    ):
        # 일별 NAV 와 같은 KIS 자격증명·유니버스를 쓴다(kis_nav.source 재사용) — 같은 벤더의
        # 같은 계정이라 섹션을 쪼개면 앱키가 두 곳에서 갱신돼야 한다.
        super().__init__(config, etf_map, client)
        if interval_sec < 1:
            # KIS 가 0·음수에 무엇을 돌려주는지 모른다 — 확인 안 된 값을 흘려보내지 않는다.
            raise ValueError(f"interval_sec 은 1 이상이어야 한다: {interval_sec}")
        self.interval_sec = interval_sec

    @property
    def window_sec(self) -> int:
        """1콜이 덮는 시간 폭. 폴링 주기가 이보다 길면 갭이 나고, 갭은 소급이 안 된다."""
        return self.interval_sec * ROWS_PER_CALL

    @property
    def skip_reason(self) -> str | None:
        """지금 수집하면 안 되는 사유, 수집해도 되면 None (ALPHA-557).

        이 API 는 응답에 날짜를 주지 않아 거래일을 **수집 시각으로** 붙일 수밖에 없다.
        그런데 KIS 는 오늘 데이터가 없어도 빈 응답이 아니라 **직전 거래일 값을 그대로**
        준다(2026-07-25 토요일 실행이 7/24 데이터 930행을 적재한 것이 실측 증거). 두 성질이
        겹치면 옛 값이 오늘 날짜 파티션에 앉는 **유령 as-of** 가 된다(ALPHA-387 과 동형).

        그래서 "오늘 데이터가 존재하는 시점"에만 수집한다 — 거래일이고 개장 이후.
        장 마감 뒤(15:30~)는 막지 않는다: 그때 오는 건 오늘 종가 구간 값이라 라벨이 맞다.

        실패가 아니라 skip 이다(exit 0) — 스케줄러가 붙으면 휴장일마다 정상적으로 지나간다.
        거래일 판정은 `ops.trading_calendar.is_trading_day`(env `OPS_KR_HOLIDAYS`)를 그대로
        쓴다. Planner·KRX 어댑터와 같은 휴장일 집합이어야 한다 — 복제하면 갈라진다.
        """
        now = datetime.now(KST)
        if not is_trading_day(now.date()):
            return f"non-trading day (KST {now.date().isoformat()})"
        if now.time() < MARKET_OPEN:
            return f"before market open (KST {now.strftime('%H:%M')} < 09:00)"
        return None

    def _query_params(self, kis_symbol: str, d1: str, d2: str) -> dict[str, str]:
        """날짜(d1·d2)는 쓰지 않는다 — 이 API 가 시각·날짜 지정을 무시하기 때문이다(실측)."""
        return {
            "FID_COND_MRKT_DIV_CODE": MARKET_DIV_INAV,
            "FID_INPUT_ISCD": kis_symbol,
            "FID_HOUR_CLS_CODE": str(self.interval_sec),
        }

    def _row_defect(self, row: object) -> str | None:
        """형태 검사(부모) + iNAV 가 행 식별에 요구하는 필드.

        `bsop_hour` 없이는 시각 축을 못 만들고(자연키 결손), `nav` 없이는 담을 값이 없다.
        둘 중 하나라도 빠진 행을 저장하면 collection_log 는 success 인데 다운스트림은 그 행을
        못 쓴다 — 조용한 유실이라 여기서 ETF 단위 실패로 드러낸다(격리≠은폐, Rule 12).
        값 자체의 타당성(범위·형식)은 판정하지 않는다 — 그건 canonical 소관이다.
        """
        defect = super()._row_defect(row)
        if defect is not None or not isinstance(row, dict):
            return defect
        missing = [f for f in REQUIRED_ROW_FIELDS if _is_blank(row.get(f))]
        return f"필수 필드 결측: {', '.join(missing)}" if missing else None

    def _extra_provenance(self) -> dict[str, object]:
        """어느 간격으로 뽑은 표본인지 행에 새긴다.

        같은 `bsop_hour` 라벨이라도 간격이 다르면 값이 다르다(실측: 15:16:00 이 cls=60 과
        cls=30 에서 서로 다른 값 — KIS 가 라벨을 구간 끝/시작 중 무엇으로 쓰는지 일관되지
        않다). 이 필드가 없으면 후속 canonical 의 자연키가 간격을 바꾸는 순간 조용히 덮인다.
        """
        return {"interval_sec": self.interval_sec}
