"""ETF NAV fact 게이트 (ALPHA-382 / S005 NAV 정제).

정규화된 NAV fact 행 하나가 **canonical 에 넣을 최소 요건**을 갖췄는지 검사한다. 구성종목
게이트(quality/etf.py)와 달리 참고 필드가 없다 — NAV 는 값 자체가 fact 라, 정체성(market·
etf_id)·시간축(trade_date)·값(nav) 넷이 전부 blocking 이다.

값 게이트의 근거는 마트 DDL 이다: `etf_nav_daily` 의
`ck_etf_nav_daily_nav CHECK (nav > 0 AND nav < 'Infinity')` 를 canonical 단계에서 미리
거른다 — 여기서 안 거르면 ALPHA-383 적재가 행 단위로 터지고, 그때는 어느 행이 왜 나쁜지가
DB 에러 문자열로만 남는다. 게이트로 잡으면 사유별 집계가 quality log 에 남는다(Rule 12).

⚠️ 각도 H(coerce-to-passing 방지): raw 의 `nav` 는 **문자열**이라 정규화가 finite-or-None
으로 정리한다(`_ref_number` 동형 — 콤마·대시·NaN/Inf·bool 차단). 그래서 여기 도달하는 nav 는
finite float 또는 None 뿐이고, 게이트는 None(변환 실패)과 0 이하만 판정한다. bool 을 막는
이유는 `float(True)=1.0` 이 양수라 게이트를 조용히 통과하기 때문이다.
"""

from __future__ import annotations

# NAV 는 참고 필드가 없다 — 전 사유가 blocking 이다(구성종목 게이트의 경고/차단 분리와 대비).
BLOCKING_REASONS_ETF_NAV = frozenset(
    {"missing_market", "unsupported_market", "missing_etf_id",
     "missing_trade_date", "bad_trade_date", "missing_nav", "non_positive_nav",
     "nav_out_of_range"}
)

# NAV 는 국내 ETF 만 대상이다(ADR-0024 MVP=국내 ETF, 수집도 KIS 단일 벤더·KR 고정).
# US 가 생기면 여기 넓힌다 — 지금 넓혀두면 잘못 라우팅된 행이 조용히 통과한다.
_SUPPORTED_MARKETS = frozenset({"KR"})

# NAV 상한(배타) — 마트 `etf_nav_daily.nav` 가 NUMERIC(24,8) 이라 정수부는 16자리까지다.
# CHECK(nav < 'Infinity') 는 통과하지만 자릿수를 넘는 값(1e308 등)은 적재에서 numeric overflow
# 로 터진다. 값 계약을 소유한 쪽에서 미리 거른다 — non_positive_nav 와 같은 근거다.
NAV_MAX_EXCLUSIVE = 10 ** 16

# trade_date 하한 — 이보다 과거는 NAV 파이프라인 대상이 아닌 오염된 날짜로 본다.
MIN_TRADE_DATE = "2000-01-01"


def _blank(value: object) -> bool:
    """사실상 빈 값인가 — None·비문자열·공백만 문자열(구성종목 게이트와 동형)."""
    return not (isinstance(value, str) and value.strip())


def validate_etf_nav(row: dict, *, max_trade_date: str) -> list[str]:
    """정규화된 NAV fact 행의 정체성·시간축·값 검사. 위반 사유 코드 리스트(정상=[]).

    max_trade_date: 허용 trade_date 상한('YYYY-MM-DD'). 파싱은 되지만 범위 밖인 미래 날짜가
      passed 로 인증되는 걸 막는다(구성종목 게이트의 max_as_of_date 와 동형).

    입력 계약: nav 는 정규화가 finite-or-None 으로 정리한 값이다(문자열·콤마·대시·비유한·
      bool → None). 그래서 여기선 non-finite 를 재검하지 않고 결측·부호만 본다.

    사유(전부 수집, 결정적 순서):
      - missing_market / unsupported_market : market 결측/미지원(KR 밖)
      - missing_etf_id     : etf_id(행키) 결측/공백
      - missing_trade_date : trade_date 결측/정규화 실패(비달력일·미패딩 포함)
      - bad_trade_date     : trade_date [MIN, max] 밖(far-future/past)
      - missing_nav        : nav 결측 또는 수치 변환 실패
      - non_positive_nav   : nav ≤ 0 (마트 CHECK 위반 — 순자산이 0 이하일 수 없다)
      - nav_out_of_range   : nav ≥ 10^16 (마트 NUMERIC(24,8) 정수부 초과 — 적재 overflow)
    """
    reasons: list[str] = []

    market = row.get("market")
    if _blank(market):
        reasons.append("missing_market")
    elif market not in _SUPPORTED_MARKETS:
        reasons.append("unsupported_market")

    if _blank(row.get("etf_id")):
        reasons.append("missing_etf_id")

    trade_date = row.get("trade_date")
    if _blank(trade_date):
        # stck_bsop_date 결측·비날짜 정규화 실패 — 시간축 파티션을 못 만들어 canonical 불가.
        reasons.append("missing_trade_date")
    elif not (MIN_TRADE_DATE <= trade_date[:10] <= max_trade_date):
        reasons.append("bad_trade_date")

    nav = row.get("nav")
    if nav is None:
        # 정규화가 못 읽은 값(문자열 파싱 실패·NaN/Inf·bool)은 전부 여기로 온다.
        reasons.append("missing_nav")
    elif nav <= 0:
        # 마트 CHECK(nav > 0) 를 canonical 에서 미리 강제 — 적재 시점에 터지지 않게.
        reasons.append("non_positive_nav")
    elif nav >= NAV_MAX_EXCLUSIVE:
        # finite 지만 마트 컬럼 폭을 넘는 값. CHECK 는 통과하고 INSERT 가 터지는 구간이라
        # 게이트가 안 보면 '통과로 인증된 행'이 적재에서만 실패한다(각도 H).
        reasons.append("nav_out_of_range")

    return reasons
