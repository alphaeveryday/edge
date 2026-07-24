"""ETF 구성종목 fact 게이트 (ALPHA-342/343 / S005 ETF 정제).

정규화된 ETF 구성종목 fact 행 하나가 **canonical 에 넣을 최소 요건**을 갖췄는지 검사한다.
canonical 정체성((market,etf_id,constituent_ticker) 행키)·시간축(as_of_date 파티션)을 만들 수
없는 행은 막고(blocking), 값 이상(비중 범위밖·주식수/평가금액 음수)은 통과시키되 사유로
드러낸다(non-blocking 경고) — 잘못된 fact 가 조용히 canonical 로 흘러 후속 분석을 오염시키지
않게 하는 게이트다(공급계약·사업부문 게이트와 동형, AGENTS Rule 12).

blocking/non-blocking 경계:
  - **blocking**: 이게 없으면 fact 를 식별(market·etf_id·구성종목)·시간축(as_of_date)에
    놓을 수 없어 분석 자체가 불가.
  - **non-blocking**(경고): 비중·주식수·평가금액은 **참고 필드**다. KRX 해외기초 ETF 는 비중·
    평가금액을 대시(-)로 줘 raw 에 값이 없고(정규화가 null 로 정리), 값이 있어도 범위 이상은
    실재 구성종목이 무효란 뜻이 아니라 품질 신호일 뿐이다 — 통과시키되 로깅한다. 비중 완성도·
    파생(주식수×가격/NAV)은 다운스트림(analysis-engine) 소관이다. 결측(null) 자체는 KRX 해외
    기초의 구조적 특성이라 경고를 내지 않는다(해외 ETF 한 종목당 수백 행을 경고로 채우지 않음).

⚠️ 각도 H(coerce-to-passing 방지): 정규화가 참고 수치를 finite-or-null 로 정리하므로 여기선
inf/nan 이 오지 않는다(가격 정제가 math.isfinite 로 원천 차단하는 것과 동형). 게이트는 남은
범위 이상만 경고로 표면화한다 — 조용히 통과시키지 않는다(Rule 12).
"""

from __future__ import annotations

# canonical 진입을 막는 필수 사유. 나머지(weight_out_of_range·negative_shares·
# negative_market_value)는 경고로 로깅만 한다.
BLOCKING_REASONS_ETF = frozenset(
    {"missing_market", "unsupported_market", "missing_etf_id", "missing_constituent",
     "missing_as_of_date", "bad_as_of_date"}
)

# 정규화가 지원하는 시장(통화 계약을 만들 수 있는 집합). 그 밖은 unsupported_market.
_SUPPORTED_MARKETS = frozenset({"US", "KR"})

# as_of_date 하한 — 이보다 과거는 ETF 파이프라인 대상이 아닌 오염된 날짜로 본다.
MIN_AS_OF_DATE = "2000-01-01"

# 단일 구성종목 비중 상한(%). 한 종목이 ETF 의 100%를 넘을 수 없다 — 초과는 파싱/단위 이상.
_WEIGHT_MAX_PCT = 100.0


def _blank(value: object) -> bool:
    """사실상 빈 값인가 — None·비문자열·공백만 문자열(설정 NonBlankStr 관례와 동형)."""
    return not (isinstance(value, str) and value.strip())


def _is_number(value: object) -> bool:
    """참고 수치가 실수치인가(bool 제외). 정규화가 finite-or-null 로 정리해 여기선 finite 만 온다."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_etf_holding(row: dict, *, max_as_of_date: str) -> list[str]:
    """정규화된 ETF 구성종목 fact 행의 정체성·시간축·값 검사. 위반 사유 코드 리스트(정상=[]).

    max_as_of_date: 허용 as_of_date 상한('YYYY-MM-DD'). 파싱은 되지만 범위 밖인 미래 날짜가
      passed 로 인증되는 걸 막는다(공급계약·사업부문 게이트의 max_report_date 와 동형).

    입력 계약: weight_pct·shares·market_value 는 정규화가 finite-or-null 로 정리한 값이다
      (dash·결측·비수치·비유한 → None). 그래서 여기선 non-finite 를 재검하지 않고 범위만 본다.

    사유(전부 수집, 결정적 순서):
      - missing_market / unsupported_market : market 결측/미지원(US·KR 밖) (blocking)
      - missing_etf_id            : etf_id(행키) 결측/공백 (blocking)
      - missing_constituent       : constituent_ticker(행키) 결측/공백 (blocking)
      - missing_as_of_date        : as_of_date 결측/정규화 실패 (blocking)
      - bad_as_of_date            : as_of_date [MIN, max] 밖(far-future/past) (blocking)
      - weight_out_of_range        : 비중 present 이고 ≤0 또는 >100 (경고 — 파싱/단위 이상)
      - negative_shares            : 주식수 present 이고 <0 (경고)
      - negative_market_value      : 평가금액 present 이고 <0 (경고)
    """
    reasons: list[str] = []

    market = row.get("market")
    if _blank(market):
        reasons.append("missing_market")
    elif market not in _SUPPORTED_MARKETS:
        # 비어있진 않지만 정규화가 지원하지 않는 market — 통화 계약을 만들 수 없어 불완전(Rule 12).
        reasons.append("unsupported_market")

    if _blank(row.get("etf_id")):
        reasons.append("missing_etf_id")
    if _blank(row.get("constituent_ticker")):
        reasons.append("missing_constituent")

    as_of = row.get("as_of_date")
    if _blank(as_of):
        # updatedAt/trd_dd 결측·비날짜 정규화 실패 — 시간축 파티션을 못 만들어 canonical 불가.
        reasons.append("missing_as_of_date")
    elif not (MIN_AS_OF_DATE <= as_of[:10] <= max_as_of_date):
        # 달력유효-쓰레기 날짜('20991231' 등)가 records_passed 로 인증돼 엉뚱한 파티션을 만드는
        # 걸 막는다. ISO 문자열이라 사전순 비교가 곧 날짜 비교(사업부문 게이트와 동형).
        reasons.append("bad_as_of_date")

    weight = row.get("weight_pct")
    if _is_number(weight) and (weight <= 0 or weight > _WEIGHT_MAX_PCT):
        # 경고: 한 종목 비중이 0 이하·100% 초과면 파싱/단위 이상 신호로 표면화한다
        # (coerce-to-passing 방지 — 조용히 통과시키지 않는다, Rule 12).
        reasons.append("weight_out_of_range")

    shares = row.get("shares")
    if _is_number(shares) and shares < 0:
        reasons.append("negative_shares")

    market_value = row.get("market_value")
    if _is_number(market_value) and market_value < 0:
        reasons.append("negative_market_value")

    return reasons
