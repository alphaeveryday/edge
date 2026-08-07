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

import logging
import math
from datetime import datetime, time, timedelta, timezone

from ..config import KisNavSource as KisNavSourceConfig
from ..ops.trading_calendar import is_trading_day
from .http import PoliteClient
from .kis_nav import KisNavSource

logger = logging.getLogger(__name__)

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


_STAMP_FORMAT = "%H%M%S"
_STAMP_LEN = 6  # bsop_hour = HHMMSS

# 괴리율 단위 가드 허용 오차(퍼센트 단위 비교). `dprt` 는 소수 2자리로 와서(실측) 반올림
# 만으로 최대 0.005 어긋나고, 값이 커지면 벤더 내부 정밀도 차이가 비례해 는다. 막으려는
# 것은 비율↔퍼센트 100배 드리프트라, 이 범위면 정상 반올림을 통과시키면서 그건 문다.
_UNIT_REL_TOL = 0.02
_UNIT_ABS_TOL = 0.01


def _time_stamp(row: dict) -> str | None:
    """행의 시각 라벨(HHMMSS 6자리), 형식을 벗어나면 None.

    **자리수를 여기서 막는다.** `strptime("%H%M%S")` 는 `"9300"` 을 09:30:00 으로 관대하게
    받아, 선행 0 이 잘린 라벨 한 줄이 사전순 max 를 탈취한다 — 정상 표본의 지연 10초가
    21660초로 찍히고, 그 수치는 창 폭(1800초)을 훌쩍 넘어 "REST 로는 장중 실시간 불가"
    라는 **설계 결론을 오염 한 줄로 뒤집는다**. 이 관측이 존재하는 이유가 그 판단 하나다.

    ⚠️ `or ""` 를 쓰지 않는다 — 그 관용구는 수치 0(자정)을 결측으로 접는다. 25줄 위
    `_is_blank` 도크스트링이 예고한 바로 그 함정이라, 같은 파일에서 다시 밟지 않는다.
    """
    value = row.get("bsop_hour")
    if value is None:
        return None
    stamp = str(value).strip()
    if len(stamp) != _STAMP_LEN or not stamp.isdigit():
        return None
    try:
        # 자리수만 보면 `"240000"`·`"153060"` 이 통과한다 — 실제 시각인지까지 **여기서**
        # 확정해, 호출부가 파싱 실패를 다시 다루지 않게 한다(판정이 두 곳으로 갈리면
        # 한쪽만 고쳐진다).
        datetime.strptime(stamp, _STAMP_FORMAT)
    except ValueError:
        return None
    return stamp


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

        **하한 09:00 은 파티션 라벨과도 맞물린다.** raw 파티션의 `ingest_date` 는 스텝이 런
        시작에 `datetime.now(timezone.utc)` 로 1회 스탬프한다 — **UTC** 다. 이 가드가 통과
        시키는 시각대(09:00~23:59 KST = 00:00~14:59 UTC 같은 날)에서는 UTC 날짜와 KST 날짜가
        일치해 라벨이 맞다. 하한을 09:00 보다 앞으로 내리면 KST 날짜와 UTC 날짜가 갈려
        **파티션이 전날로 붙는다** — 하한을 만질 땐 이 결합을 함께 봐라.

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

    def _note_rows(self, our_etf_id: str, kis_symbol: str, rows: list[dict]) -> None:
        """응답 집합에서 **설계를 가르는 두 가지**를 관측한다 (ALPHA-845).

        1. **벤더 지연** = 수신 벽시계 − 최신 `bsop_hour`. 이 API 는 "지금 기준 최근 30행"
           을 주는데, 그 최신 행이 지금 것인지 창 폭(간격×30)만큼 낡은 것인지 **한 번도
           재지 않았다**. `cls` 가 표본 간격을 지배한다는 관측(같은 라벨이 cls 마다 다른
           값)에서 신선도를 추론해 왔는데, 그 둘은 별개 축이다. 지연이 창 폭에 가까우면
           이 엔드포인트로는 장중 실시간이 성립하지 않고 웹소켓(`H0STNAV0`)이 유일한
           경로가 된다 — 1분 레인 편입 설계 전체가 여기 걸려 있다.

        2. **괴리율 단위 가드** — `dprt` 는 **퍼센트**다(실측 확정, `_PREMIUM_UNIT` 주석).
           `sql_surface.v_nav.premium` 은 **비율**이라 벤더가 단위를 바꾸면 canonical 이
           100배 틀린 값을 싣는다. 그래서 값을 나열하지 않고 **어긋날 때만 경고**한다 —
           나열은 드리프트가 나도 로그 모양이 같아 아무도 못 본다.

        ⚠️ **지연의 부호로 전일 오염을 판정하지 않는다.** 응답에 날짜 필드가 없어 이 콜
        하나로는 판정이 불가능하고, 부호는 **양방향으로 틀린다**: 라벨이 구간 끝이면 최신
        행이 정상적으로 미래라 음수가 오탐이고(`_extra_provenance` 가 라벨 규약이 일관되지
        않다고 적어둔 그 성질이다), 반대로 15:30 이후 실행에서는 전일 잔값이 **양수**로
        위장한다 — 하필 창 폭과 비슷한 값이라 "REST 불가"의 강한 증거처럼 읽힌다. 여기서는
        관측한 사실만 남기고 판정은 하지 않는다(Rule 12).

        ⚠️ **두 축은 서로의 실패로 죽지 않는다.** 시각 라벨이 깨져도 단위 가드는 돌고 그
        반대도 마찬가지다 — return 을 공유하면 라벨 한 줄 때문에 그 ETF 표본이 단위 판정에서
        통째로 빠진다.

        관측 실패도 관측이다 — 조용히 지나가면 "지연이 0" 과 "못 쟀다" 가 같아 보인다.
        """
        self._note_lag(our_etf_id, kis_symbol, rows, datetime.now(KST))
        self._note_premium_unit(our_etf_id, kis_symbol, rows)

    def _note_lag(
        self, our_etf_id: str, kis_symbol: str, rows: list[dict], now: datetime
    ) -> None:
        """최신 행 기준 벤더 지연. 못 재면 못 쟀다고 남긴다."""
        stamps = [s for s in (_time_stamp(r) for r in rows) if s is not None]
        if malformed := len(rows) - len(stamps):
            # 조용히 버리면 형식 이탈 한 줄이 최신 판정을 흔든 것을 아무도 모른다.
            logger.warning(
                "iNAV 시각 라벨 형식 이탈 %d/%d 행: %s(%s) — 최신 행 판정에서 제외했다",
                malformed, len(rows), kis_symbol, our_etf_id,
            )
        if not stamps:
            logger.warning(
                "iNAV 지연 관측 불가: %s(%s) 쓸 수 있는 시각 라벨 0건(행 %d)",
                kis_symbol, our_etf_id, len(rows),
            )
            return
        # 전부 HHMMSS 6자리로 걸러졌으므로 사전순 = 시각순이다(`_time_stamp` 가 보장).
        stamp = max(stamps)
        observed = datetime.combine(
            now.date(), datetime.strptime(stamp, _STAMP_FORMAT).time(), tzinfo=KST
        )
        lag_sec = (now - observed).total_seconds()
        logger.info(
            "iNAV 벤더 지연: %s(%s) 최신=%s 수신=%s 지연=%.0f초 "
            "창=%d초 간격=%d초 행=%d%s",
            kis_symbol, our_etf_id, stamp, now.strftime(_STAMP_FORMAT), lag_sec,
            self.window_sec, self.interval_sec, len(stamps),
            # 사실만 적는다 — 원인(전일 잔값/구간 끝 라벨)은 이 콜로 못 가른다(도크스트링).
            " (라벨이 수신시각보다 미래다)" if lag_sec < 0 else "",
        )

    def _note_premium_unit(
        self, our_etf_id: str, kis_symbol: str, rows: list[dict]
    ) -> None:
        """`dprt` 가 퍼센트 가설을 벗어나면 경고. 맞으면 조용하다.

        전 행을 본다 — 한 행만 보면 그 행이 마침 결측일 때 그 ETF 가 통째로 대조에서
        빠진다. 정상일 때 로그를 남기지 않는 것은 의도다: 확정된 사실을 ETF·폴링마다
        되풀이하면 진짜 신호가 자기 소음에 묻힌다(1분 레인에 붙으면 하루 수만 줄이다).
        """
        checked = mismatched = 0
        sample: tuple[object, float, float] | None = None
        for row in rows:
            try:
                nav = float(row["nav"])
                price = float(row["stck_prpr"])
                dprt = float(row["dprt"])
            except (KeyError, TypeError, ValueError):
                continue
            # ⚠️ float() 는 "nan"·"inf" 를 **예외 없이** 통과시킨다. 그대로 두면 오염
            # 표본이 그럴듯한 수치(-100.0000%)로 대조에 섞여 판정을 흔든다.
            if not all(map(math.isfinite, (nav, price, dprt))) or nav <= 0:
                continue
            checked += 1
            expected_pct = (price / nav - 1.0) * 100.0
            if not math.isclose(
                dprt, expected_pct, rel_tol=_UNIT_REL_TOL, abs_tol=_UNIT_ABS_TOL
            ):
                mismatched += 1
                if sample is None:
                    sample = (row.get("bsop_hour"), dprt, expected_pct)
        if not checked:
            logger.warning(
                "iNAV 괴리 단위 대조 불가: %s(%s) 쓸 수 있는 (nav, 가격, dprt) 삼중 0건(행 %d)",
                kis_symbol, our_etf_id, len(rows),
            )
            return
        if mismatched:
            logger.warning(
                "iNAV 괴리 단위 드리프트 의심: %s(%s) %d/%d 행 불일치 — "
                "bsop_hour=%s 에서 dprt=%s 인데 퍼센트 가설은 %.4f 다. "
                "canonical premium_pct 에 그대로 실으면 안 된다",
                kis_symbol, our_etf_id, mismatched, checked, *sample,
            )

    def _extra_provenance(self) -> dict[str, object]:
        """어느 간격으로 뽑은 표본인지 행에 새긴다.

        같은 `bsop_hour` 라벨이라도 간격이 다르면 값이 다르다(실측: 15:16:00 이 cls=60 과
        cls=30 에서 서로 다른 값 — KIS 가 라벨을 구간 끝/시작 중 무엇으로 쓰는지 일관되지
        않다). 이 필드가 없으면 후속 canonical 의 자연키가 간격을 바꾸는 순간 조용히 덮인다.
        """
        return {"interval_sec": self.interval_sec}
