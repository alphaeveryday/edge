"""공시 공급계약 fact 게이트 (ALPHA-345 / S005 공시 정제).

파싱·조인된 공급계약 fact 행 하나가 **canonical 에 넣을 최소 요건**을 갖췄는지 검사한다.
canonical 정체성(rcept_no 행키)·시간축(report_date 파티션)을 만들 수 없는 행은 막고
(blocking), 값 이상(비율 범위밖·금액 비양수·계약상대방 유보 등)은 통과시키되 사유로
드러낸다(non-blocking 경고) — 분석에 쓸 수 없는 fact 가 조용히 canonical 로 흘러 후속
분석을 오염시키지 않게 하는 게이트다(AGENTS Rule 12).

blocking/non-blocking 경계는 뉴스 게이트(quality/news)와 동형이다:
  - **blocking**: 이게 없으면 fact 를 식별(rcept_no)·시간축(report_date)에 놓을 수 없어
    분석 자체가 불가. + 본문에서 계약을 하나도 못 뽑은 빈 파싱(empty_parse)도 canonical
    가치가 없어 막는다.
  - **non-blocking**(경고): 값 이상은 실제 공시가 존재하고(정체성·시간축 유효) 품질 신호일
    뿐 fact 무효는 아니다 — 로깅만 하고 행은 통과시킨다(계약상대방 유보는 정상 관행,
    비율 범위밖·금액 비양수는 파싱 이상 신호로 표면화. graph weight nulling 은 다운스트림
    소관). 이상치에 하드 게이트를 걸어 실재 공시를 통째로 탈락시키지 않는다.

⚠️ 각도 H(coerce-to-passing 방지): malformed 본문이 사유 없이 통과하지 않게, 파싱이 값을
못 만든 결측과 범위밖 값을 각각 사유로 수집한다(첫 실패에서 멈추지 않음 — Rule 12).
"""

from __future__ import annotations

# canonical 진입을 막는 필수 사유. 나머지(withheld_counterparty·ratio_out_of_range·
# amount_non_positive·missing_amount_and_ratio)는 경고로 로깅만 한다.
BLOCKING_REASONS_DISCLOSURE = frozenset(
    {"missing_rcept_no", "missing_report_date", "bad_report_date", "empty_parse"}
)

# report_date 하한 — 이보다 과거는 공시 파이프라인 대상이 아닌 오염된 날짜로 본다.
MIN_REPORT_DATE = "2000-01-01"

# 매출액대비 비율 상한 — 이를 넘는 %는 파싱 이상(단위 오인·표 오매칭) 신호로 표면화한다.
_RATIO_MAX_PCT = 150.0


def _blank(value: object) -> bool:
    """사실상 빈 값인가 — None·비문자열·공백만 문자열(설정 NonBlankStr 관례와 동형)."""
    return not (isinstance(value, str) and value.strip())


def validate_supply_fact(row: dict, *, max_report_date: str) -> list[str]:
    """조인된 공급계약 fact 행의 정체성·시간축·값 검사. 위반 사유 코드 리스트(정상=[]).

    max_report_date: 허용 report_date 상한('YYYY-MM-DD', 보통 검증 실행일 + 며칠). 파싱은
      되지만 범위 밖인 미래 날짜(달력상 유효하나 쓰레기)가 passed 로 인증되는 걸 막는다.

    사유(전부 수집, 결정적 순서):
      - missing_rcept_no          : rcept_no(행키) 결측/공백 (blocking)
      - missing_report_date       : report_date 결측/공백 (blocking)
      - bad_report_date           : report_date [MIN, max] 밖(far-future/past) (blocking)
      - empty_parse               : 본문에서 계약을 하나도 못 뽑음(핵심필드 전무) (blocking)
      - withheld_counterparty     : 계약상대방 유보(비밀유지·공시유보) (경고 — 정상 관행)
      - missing_amount_and_ratio  : 계약금액·매출액대비 둘 다 결측 (경고)
      - ratio_out_of_range        : 매출액대비 pct ≤0 또는 >150 (경고 — 파싱 이상 표면화)
      - amount_non_positive       : 계약금액 ≤0 (경고 — 파싱 이상 표면화)
    """
    reasons: list[str] = []

    if _blank(row.get("rcept_no")):
        reasons.append("missing_rcept_no")

    report_date = row.get("report_date")
    if _blank(report_date):
        # rcept_dt 결측/비날짜 정규화 실패 — 시간축 파티션을 못 만들어 canonical 불가.
        reasons.append("missing_report_date")
    elif not (MIN_REPORT_DATE <= report_date[:10] <= max_report_date):
        # 달력유효-쓰레기 날짜('20991231' 등)가 records_passed 로 인증돼 엉뚱한 파티션을
        # 만드는 걸 막는다. ISO 문자열이라 사전순 비교가 곧 날짜 비교.
        reasons.append("bad_report_date")

    # 본문에서 계약을 하나도 못 뽑은 빈 파싱 — 추출된 내용(계약상대방 원문·체결계약명·금액·
    # 비율·계약기간)이 전부 없으면 canonical 가치가 없다(테이블 없음·라벨 전무 등 malformed).
    # ⚠️ counterparty_withheld 로 판정하지 않는다 — 파서는 테이블이 아예 없어 상대방을 '못
    # 뽑은' 경우에도 withheld=True 로 표시하므로(nullish→withheld), 그걸 present 로 보면 빈
    # 본문이 통과한다. 실제 유보 공시는 counterparty_raw(유보 문구)·object 등이 남아 empty 가
    # 아니다 — 그래서 '가린 것'과 '못 뽑은 것'을 추출 내용의 유무로 가른다(각도 H).
    if (
        _blank(row.get("counterparty_raw"))
        and _blank(row.get("object"))
        and row.get("amount_krw") is None
        and row.get("ratio_pct") is None
        and _blank(row.get("contract_start"))
        and _blank(row.get("contract_end"))
    ):
        reasons.append("empty_parse")

    if row.get("counterparty_withheld"):
        # 경고: 경영상 비밀유지·공시유보로 상대방을 가린 정상 공시 — 탈락 아님.
        reasons.append("withheld_counterparty")

    if row.get("amount_krw") is None and row.get("ratio_pct") is None:
        # 경고: 규모 지표를 하나도 못 뽑음 — 식별·시간축은 있으나 분석 가치 손실을 드러낸다.
        reasons.append("missing_amount_and_ratio")

    ratio_pct = row.get("ratio_pct")
    if isinstance(ratio_pct, (int, float)) and not isinstance(ratio_pct, bool):
        if ratio_pct <= 0 or ratio_pct > _RATIO_MAX_PCT:
            # 경고: 0 이하·150% 초과는 단위 오인·표 오매칭 등 파싱 이상 신호로 표면화한다
            # (coerce-to-passing 방지 — 조용히 통과시키지 않는다, Rule 12).
            reasons.append("ratio_out_of_range")

    amount_krw = row.get("amount_krw")
    if isinstance(amount_krw, int) and not isinstance(amount_krw, bool) and amount_krw <= 0:
        # 경고: 계약금액 ≤0 은 파싱 이상(부호·단위) 신호로 표면화한다.
        reasons.append("amount_non_positive")

    return reasons
