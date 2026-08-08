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
from .http import PoliteClient, StopFetch
from .kis_nav import KisNavSource

logger = logging.getLogger(__name__)

TR_ID_NAV_MINUTE = "FHPST02440100"
PATH_NAV_MINUTE = "/uapi/etfetn/v1/quotations/nav-comparison-time-trend"
# 일별(kis_nav.MARKET_DIV="J")과 갈린다 — 실측으로 확정한 값이다. 바꾸면 전건 rt_cd=2.
MARKET_DIV_INAV = "E"
# FID_HOUR_CLS_CODE(초). 1콜=30행이라 조회 창 = 이 값 × 30.
# ⚠️ **초 단위 임의값이 아니다.** 벤더 문서의 어휘는 `60:1분, 180:3분, …, 7200:120분` —
# 즉 **분 단위**이고 하한이 60 이다. 초기 실측에서 1·5·10·15·30 이 "수용됐다"는 관측이
# 있었지만 수용은 정의된 동작이 아니다(어휘 밖 값에 벤더가 무엇을 하는지 미정의).
DEFAULT_INTERVAL_SEC = 60
MIN_INTERVAL_SEC = 60  # 벤더 문서 어휘의 하한(=1분)
# 응답 행 수. **문서화된 계약**이다 — "실전계좌의 경우, 한 번의 호출에 최근 30건까지".
# ⚠️ 단서가 "실전계좌"라 모의계좌는 다를 수 있다.
ROWS_PER_CALL = 30
# 행 식별에 필요한 필드 — 시각 축(bsop_hour)과 값(nav). 없으면 저장해도 못 쓴다.
REQUIRED_ROW_FIELDS = ("bsop_hour", "nav")

KST = timezone(timedelta(hours=9))
# 정규장 개장 시각(KST). 이 전에는 **오늘 iNAV 가 아직 존재하지 않는다** — 그때 부르면 KIS 가
# 빈 응답이 아니라 직전 거래일 값을 주고, 응답에 날짜가 없어 그게 오늘 것으로 라벨된다.
MARKET_OPEN = time(9, 0)
# 정규장 마감. **수집을 막는 경계가 아니다**(마감 후에 오는 건 오늘 종가 구간이라 라벨이
# 맞다 — `skip_reason`) — 지연 수치를 읽는 쪽이 장중과 마감후를 구분하게 하는 표시일 뿐이다.
MARKET_CLOSE = time(15, 30)


_STAMP_FORMAT = "%H%M%S"
_STAMP_LEN = 6  # bsop_hour = HHMMSS
# 개장 시각의 라벨 표기 — 라벨끼리는 6자리 고정이라 문자열 비교가 곧 시각 비교다.
_OPEN_STAMP = MARKET_OPEN.strftime(_STAMP_FORMAT)
# 괴리 대조가 요구하는 삼중. `REQUIRED_ROW_FIELDS`(행 식별)와 **다른 축**이다 — 이 셋이
# 빠져도 행은 저장된다(bronze 무변형). 다만 빠진 게 무엇인지는 남겨야 가드가 조용히
# 사라진 것을 안다.
_UNIT_FIELDS = ("nav", "stck_prpr", "dprt")

# 괴리율 단위 가드 허용 오차(퍼센트 단위 비교).
#
# ⚠️ **abs_tol 이 곧 사각지대 폭이다.** 비율↔퍼센트 드리프트의 잔차는 `0.99 × |진짜 괴리|`
# 라, 괴리가 `abs_tol/0.99` 보다 작은 구간은 100배 드리프트가 통째로 통과한다. 조이고 싶은
# 값이지만 **못 조인다** — 상한이 `dprt` 의 2자리 표기가 반올림인지 절사인지에 걸려 있는데
# 그게 미실측이다. 반올림이면 0.005, **절사면 0.00999** 다. 실측 표본(069500, 0.114115 →
# "0.11")은 두 가설이 같은 값을 내 구분하지 못한다. 절사인데 0.006 으로 조이면 정상 표본의
# 40%가 드리프트로 잡혀(실측 스윕) 진짜 신호가 자기 소음에 묻힌다 — 소음이 가드를 끄게
# 만들면 결국 관대해지는 쪽으로 착지한다. 그래서 **넓은 쪽(절사 상한)에 맞춘다.**
# ⏭ 3째 자리가 5 미만·이상인 표본 두 개면 갈린다. 갈린 뒤에 조여라.
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

    검사는 **두 겹뿐**이다. `None` 분기·`isdigit()`·`strip()` 을 두지 않는 이유가 각각
    있다: 결측은 `str(None)`="None"(4자)이라 길이에서 걸리고, 비숫자는 `strptime` 이 이미
    거부하며(`isdigit()` 은 `"²"` 를 통과시켜 오히려 더 약하다), 공백 패딩은 **걸러내는
    편이 옳다** — 벤더 포맷이 변한 신호라 조용히 흡수하면 그 변화를 못 본다.
    """
    stamp = str(row.get("bsop_hour"))
    if len(stamp) != _STAMP_LEN:
        return None
    try:
        # 자리수만 보면 `"240000"`·`"153060"` 이 통과한다 — 실제 시각인지까지 **여기서**
        # 확정해, 호출부가 파싱 실패를 다시 다루지 않게 한다(판정이 두 곳으로 갈리면
        # 한쪽만 고쳐진다).
        datetime.strptime(stamp, _STAMP_FORMAT)
    except ValueError:
        return None
    return stamp


def _stamp_secs(stamps: list[str]) -> list[int]:
    """라벨을 자정 기준 초로. 전부 `_time_stamp` 를 통과한 값이라 파싱은 실패하지 않는다."""
    return [
        t.hour * 3600 + t.minute * 60 + t.second
        for t in (datetime.strptime(s, _STAMP_FORMAT).time() for s in stamps)
    ]


def _median_gap_sec(stamps: list[str]) -> int | None:
    """연속 라벨 간격의 중앙값(초). 잴 수 없으면 None.

    **실측 격자**다 — `interval_sec` 은 우리가 보낸 요청값이라 벤더가 그걸 무시해도
    코드는 모른다. 중앙값을 쓰는 이유는 결손 한 칸이 평균을 끌기 때문이다.
    """
    secs = sorted(set(_stamp_secs(stamps)))
    gaps = sorted(b - a for a, b in zip(secs, secs[1:]))
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


def _stamp_span_sec(stamps: list[str]) -> int:
    """라벨 집합이 덮는 시간 폭(초). 한 창의 행인지 판정하는 축이다.

    응답 창은 계약상 `간격 × 30` 이라(모듈 도크스트링 2번), 이 폭이 창을 넘으면 그 행들은
    한 창에서 온 게 아니다 — 전일 잔값이 섞였다는 뜻이다. 날짜 필드가 없어 직접 확인은
    불가능하지만, **범위는 확인 가능하다**.
    """
    if not stamps:
        return 0
    secs = _stamp_secs(stamps)
    return max(secs) - min(secs)


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
        if interval_sec < MIN_INTERVAL_SEC:
            # 어휘 밖 값에 KIS 가 무엇을 돌려주는지 **정의돼 있지 않다**. 수용되더라도
            # 그 응답의 의미를 우리가 단언할 수 없어, 확인 안 된 값을 흘려보내지 않는다.
            raise ValueError(
                f"interval_sec 은 {MIN_INTERVAL_SEC} 이상이어야 한다(벤더 어휘 "
                f"60:1분·180:3분·…·7200:120분): {interval_sec}"
            )
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

    def _note_rows(
        self, our_etf_id: str, kis_symbol: str, rows: list[dict], received_count: int
    ) -> None:
        """응답 집합에서 **설계를 가르는 셋**을 관측한다 (ALPHA-845).

        0. **응답 형상** — 행 수와 표본 간격이 우리가 믿는 계약대로인가(`_note_shape`).
           `rows` 는 `_row_defect` 를 통과한 것뿐이라 행 수 대조는 **`received_count`**
           (벤더가 실제로 준 행 수)로 한다. 필터 후 수로 재면 우리가 버린 행을 벤더의
           계약 위반으로 고발하고, 같은 로그가 "창 폭 계산이 틀렸다"까지 주장한다 —
           둘 다 거짓이고, 이 조각의 산출물은 로그뿐이라 읽는 쪽이 그대로 믿는다.

        1. **벤더 지연** = 수신 벽시계 − 최신 `bsop_hour`. 이 API 는 "지금 기준 최근 30행"
           을 주는데, 그 최신 행이 지금 것인지 창 폭(간격×30)만큼 낡은 것인지 **한 번도
           재지 않았다**. `cls` 가 표본 간격을 지배한다는 관측(같은 라벨이 cls 마다 다른
           값)에서 신선도를 추론해 왔는데, 그 둘은 별개 축이다. 지연이 창 폭에 가까우면
           이 엔드포인트로는 장중 실시간이 성립하지 않고 웹소켓(`H0STNAV0`)이 유일한
           경로가 된다 — 1분 레인 편입 설계 전체가 여기 걸려 있다.

        2. **괴리율 단위 가드** — `dprt` 는 **퍼센트**다(실측 069500·2026-07-25:
           `stck_prpr/nav − 1` = 0.00114115, ×100 = 0.11411 → 반올림 0.11 = `dprt`.
           비율 가설이면 0.00 이라 안 맞는다. 교차 근거는 `nav_vrss_prpr` 121.24 =
           `stck_prpr − nav` 다).
           `sql_surface.v_nav.premium` 은 **비율**이라 벤더가 단위를 바꾸면 canonical 이
           100배 틀린 값을 싣는다. 그래서 값을 나열하지 않고 **어긋날 때만 경고**한다 —
           나열은 드리프트가 나도 로그 모양이 같아 아무도 못 본다.

        ⚠️ **지연의 부호로 전일 오염을 판정하지 않는다.** 응답에 날짜 필드가 없어 이 콜
        하나로는 판정이 불가능하고, 부호는 **양방향으로 틀린다**: 라벨이 구간 끝이면 최신
        행이 정상적으로 미래라 음수가 오탐이고(`_extra_provenance` 가 라벨 규약이 일관되지
        않다고 적어둔 그 성질이다), 반대로 15:30 이후 실행에서는 전일 잔값이 **양수**로
        위장한다 — 하필 창 폭과 비슷한 값이라 "REST 불가"의 강한 증거처럼 읽힌다. 여기서는
        관측한 사실만 남기고 판정은 하지 않는다(Rule 12).

        ⚠️ **세 축은 서로의 실패로 죽지 않는다.** 시각 라벨이 깨져도 단위 가드는 돌고 그
        반대도 마찬가지다 — return 을 공유하면 라벨 한 줄 때문에 그 ETF 표본이 단위 판정에서
        통째로 빠진다. 형상 축을 지연 축 **안**에 두면 같은 함정이 다시 열린다: 라벨이 전건
        깨진 응답은 벤더가 스키마를 바꿨을 가능성이 가장 큰 경우인데, 하필 그때 행 수
        대조가 안 돌아 **가장 의심스러운 응답이 형상 검사를 통째로 건너뛴다**.

        관측 실패도 관측이다 — 조용히 지나가면 "지연이 0" 과 "못 쟀다" 가 같아 보인다.
        """
        # 순차 호출만으로는 `return` 공유만 푼다 — 앞 축이 **던지면** 뒤 축이 통째로
        # 안 돈다. 그때 남는 건 부모의 ERROR 한 줄인데, 단위 가드는 정상일 때 조용하므로
        # "경고 없음"과 구분이 안 된다. 도크스트링의 독립 계약을 구조가 지게 한다.
        now = datetime.now(KST)
        for axis, run in (
            ("응답 형상",
             lambda: self._note_shape(our_etf_id, kis_symbol, rows, received_count)),
            ("지연", lambda: self._note_lag(our_etf_id, kis_symbol, rows, now)),
            ("괴리 단위", lambda: self._note_premium_unit(our_etf_id, kis_symbol, rows)),
        ):
            try:
                run()
            except StopFetch:
                raise  # 소스 전역 신호는 축 격리도 뚫는다(부모와 같은 계약)
            except Exception:
                logger.exception(
                    "iNAV %s 관측 실패 — 이 폴링에서 %s(%s) 의 그 축은 없다",
                    axis, kis_symbol, our_etf_id,
                )

    def _note_shape(
        self, our_etf_id: str, kis_symbol: str, rows: list[dict], received_count: int
    ) -> None:
        """응답이 **우리가 믿는 형상**대로 왔는가 — 행 수와 표본 간격.

        ⚠️ 행 수는 **벤더가 준 수**(`received_count`)로 잰다. `rows` 는 `_row_defect` 가
        거르고 남은 것이라, 그걸로 재면 우리가 버린 행을 벤더의 계약 위반으로 고발한다 —
        30행 중 2행에 `nav` 가 비어 왔을 뿐인데 "28행(계약 30) · 창 1800초가 실제와
        어긋난다"가 찍히고, 둘 다 거짓이다(벤더는 30행을 줬고 창 폭도 맞다). 버려진 행은
        `_note_failure` 가 따로 남기므로 여기서 겹쳐 셀 이유도 없다.

        둘 다 `window_sec` 의 입력이고, `window_sec` 은 판정 분모(지연÷창)이자 혼재 임계다.
        그래서 벤더가 어느 쪽을 바꿔도 **두 축이 같은 방향(관대)으로 함께 틀린다** — 20행이
        오면 실창은 1200초인데 1800 으로 찍혀 판정이 1.5배 관대해지고, 같은 배수로 혼재
        탐지도 죽는다. `ROWS_PER_CALL`·`interval_sec` 은 지금까지 **응답과 한 번도 대조되지
        않았다**(`interval_sec` 은 우리가 보낸 요청값이지 측정값이 아니다).

        ⚠️ 간격이 어긋나면 `_extra_provenance` 가 raw 행에 **거짓 `interval_sec` 을 각인**
        한다. 그 필드는 자연키의 일부라(같은 라벨이라도 간격이 다르면 값이 다르다), 없는
        것보다 틀린 값이 나쁘다.
        """
        if received_count != ROWS_PER_CALL:
            logger.warning(
                "iNAV 응답 행 수가 계약과 다르다: %s(%s) %d행(계약 %d) — "
                "창 %d초는 계약 행수로 계산한 값이라 실제와 어긋난다",
                kis_symbol, our_etf_id, received_count, ROWS_PER_CALL, self.window_sec,
            )
        # 간격은 라벨이 있어야 잰다 — 없으면 `_median_gap_sec` 이 None 을 주고 조용히
        # 넘어간다(라벨 결손 자체는 `_note_lag` 가 고발한다). 행 수 대조는 그와 **무관하게**
        # 이미 위에서 끝났다: 라벨이 전건 깨진 응답이야말로 벤더가 형상을 바꿨을 가능성이
        # 가장 큰 경우라, 그때 행 수를 안 재면 가장 의심스러운 응답이 검사를 건너뛴다.
        stamps = [s for s in (_time_stamp(r) for r in rows) if s is not None]
        if (observed := _median_gap_sec(stamps)) is not None and observed != self.interval_sec:
            logger.warning(
                "iNAV 표본 간격이 요청과 다르다: %s(%s) 요청 %d초 / 실측 %d초 — "
                "벤더가 FID_HOUR_CLS_CODE 를 무시한다. raw 의 interval_sec 이 거짓이 된다",
                kis_symbol, our_etf_id, self.interval_sec, observed,
            )

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
        # ⚠️ 사전순 = 시각순은 **하루 안에서만** 참이다(`_time_stamp` 가 보장하는 범위).
        # 응답 창은 계약상 `간격 × 30` 이라 **라벨 범위가 창 폭을 넘으면 한 창의 행이 아니다**.
        # ⚠️ 이 가드가 잡는 것은 **혼재뿐이다.** 전일 데이터가 *통째로* 반복되면(이 API 의
        # 실측 성질) 범위는 정상 창 안이라 구조적으로 못 문다 — 그쪽은 아래 지연 크기가
        # 유일한 단서고, 마감 후에는 그 단서도 정상값과 겹친다.
        span_sec = _stamp_span_sec(stamps)
        if span_sec > self.window_sec:
            logger.warning(
                "iNAV 라벨 범위가 창 폭을 넘는다: %s(%s) %s~%s(%d초) > 창 %d초 — "
                "한 창의 행이 아니다. 최신 판정이 오염될 수 있다",
                kis_symbol, our_etf_id, min(stamps), max(stamps), span_sec,
                self.window_sec,
            )
        if pre_open := [s for s in stamps if s < _OPEN_STAMP]:
            # **미실측 항목이라 오류로 단정하지 않는다.** 창이 뒤로 뻗으면(09:10 실행의
            # 창은 08:40 까지) KIS 가 장 시작 전 시각을 무엇으로 채우는지 확인된 바 없다 —
            # 그게 이 조각이 재려는 것 중 하나다. 최신 행만 찍으면 이 사실이 안 드러난다.
            logger.info(
                "iNAV 개장 전 라벨 %d/%d건: %s(%s) 최소=%s — 창이 개장 이전으로 뻗었다"
                "(전일 값인지 미실측)",
                len(pre_open), len(stamps), kis_symbol, our_etf_id, min(pre_open),
            )
        stamp = max(stamps)
        observed = datetime.combine(
            now.date(), datetime.strptime(stamp, _STAMP_FORMAT).time(), tzinfo=KST
        )
        lag_sec = (now - observed).total_seconds()
        # 마감 후 폴링은 지연이 창 폭의 십수 배로 나오는 게 **정상**이다 — 마커가 없으면
        # 하루치를 모아 보는 쪽에서 그 값이 "REST 불가"의 증거처럼 섞인다. 개장 전을 마감
        # 후와 한 라벨로 접으면 안 된다: 의미가 정반대다(전일 값 확정 vs 오늘 값 정상).
        if now.time() < MARKET_OPEN:
            session = "개장전"
        elif now.time() < MARKET_CLOSE:
            session = "장중"
        else:
            session = "마감후"
        # ⚠️ **장중에 창 폭을 넘는 지연은 그 자체로 이상이다.** 원인은 못 가르지만(전일
        # 통째 반복 / 벤더 시각축 이탈 / 진짜 지연) 셋 다 이 조각의 결론을 좌우한다.
        # INFO 로 두면 WARN 만 보는 쪽에서 **가장 나쁜 경우가 통째로 사라진다** — 부분
        # 혼재는 위 span 가드가 WARN 을 내는데 전량 오염이 조용하면 거짓 안심이 된다.
        anomalous = session == "장중" and abs(lag_sec) > self.window_sec
        logger.log(
            logging.WARNING if anomalous else logging.INFO,
            "iNAV 벤더 지연: %s(%s) 최신=%s 수신=%s 지연=%.0f초 "
            "창=%d초 간격=%d초 행=%d 구간=%s%s",
            kis_symbol, our_etf_id, stamp, now.strftime(_STAMP_FORMAT), lag_sec,
            self.window_sec, self.interval_sec, len(stamps), session,
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
        checked = mismatched = missing = nonfinite = 0
        absent: set[str] = set()
        sample: tuple[object, float, float] | None = None
        for row in rows:
            try:
                nav = float(row["nav"])
                price = float(row["stck_prpr"])
                dprt = float(row["dprt"])
            except (KeyError, TypeError, ValueError):
                # 결측·비수치와 아래 비유한·비양수를 **가른다** — 한 문장으로 뭉개면
                # "필드가 안 왔다"(벤더 계약 변화)와 "값이 이상하다"(데이터 오염)가
                # 같아 보이고 처방이 정반대다. **어느 필드인지도 남긴다**: 벤더가
                # `dprt` 를 개명하면 이 가드가 통째로 사라지는데 런은 초록이다.
                missing += 1
                absent.update(f for f in _UNIT_FIELDS if _is_blank(row.get(f)))
                continue
            # ⚠️ float() 는 "nan"·"inf" 를 **예외 없이** 통과시킨다. 그대로 두면 오염
            # 표본이 그럴듯한 수치(-100.0000%)로 대조에 섞여 판정을 흔든다.
            # `price <= 0` 도 같이 뺀다 — 안 빼면 `(0/nav − 1)×100 = −100.0000%` 가
            # 대조에 섞여, 바로 위 주석이 막으려는 그 값이 다른 문으로 들어온다.
            # 정상 표본에 붙는 거짓 드리프트 경고는 결국 가드를 끄게 만들고, 그러면
            # 관대한 쪽으로 착지한다(`_UNIT_ABS_TOL` 주석과 같은 논리).
            if not all(map(math.isfinite, (nav, price, dprt))) or nav <= 0 or price <= 0:
                nonfinite += 1
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
                "iNAV 괴리 단위 대조 불가: %s(%s) 쓸 수 있는 삼중 0건"
                "(행 %d · 결측·비수치 %d · 비유한·비양수 %d · 결측 필드 %s)",
                kis_symbol, our_etf_id, len(rows), missing, nonfinite,
                ",".join(sorted(absent)) or "-",
            )
            return
        if unusable := missing + nonfinite:
            # **부분 실패도 관측이다.** 30행 중 29행이 못 쓰는데 남은 1행이 맞으면 지금까진
            # 아무 로그도 안 남았다 — 표본이 1/30 인데 판정은 "정상"으로 보인다. 시각 축은
            # 같은 상황을 "형식 이탈 29/30" 으로 드러내는데 이쪽만 새고 있었다.
            logger.warning(
                "iNAV 괴리 단위 표본 부족: %s(%s) %d/%d 행만 대조"
                "(결측·비수치 %d · 비유한·비양수 %d)",
                kis_symbol, our_etf_id, checked, len(rows), missing, nonfinite,
            )
        if mismatched:
            logger.warning(
                "iNAV 괴리 단위 드리프트 의심: %s(%s) %d/%d 행 불일치 — "
                "bsop_hour=%s 에서 dprt=%s 인데 퍼센트 가설은 %.4f 다. "
                "**이 종목 표본에서만** 관측된 것이다 — 전 종목 판정은 한 실행의 "
                "관측 대상 수와 함께 봐야 한다",
                kis_symbol, our_etf_id, mismatched, checked, *sample,
            )

    def _extra_provenance(self) -> dict[str, object]:
        """어느 간격으로 뽑은 표본인지 행에 새긴다.

        같은 `bsop_hour` 라벨이라도 간격이 다르면 값이 다르다(실측: 15:16:00 이 cls=60 과
        cls=30 에서 서로 다른 값 — KIS 가 라벨을 구간 끝/시작 중 무엇으로 쓰는지 일관되지
        않다). 이 필드가 없으면 후속 canonical 의 자연키가 간격을 바꾸는 순간 조용히 덮인다.
        """
        return {"interval_sec": self.interval_sec}
