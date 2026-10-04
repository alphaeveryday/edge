"""OpenDART 정기보고서 → EPS·BPS·매출액·영업이익 (ALPHA-1130). 계약 정본은 설계 §10.

엔드포인트(OpenDART 개발가이드, 키 = `dart_financial.source.api_key` 공유):
- `list.json?corp_code=&bgn_de=&end_de=&pblntf_ty=A` — 정기공시 목록. `rcept_dt`(접수일)의 유일한 출처다.
- `fnlttSinglAcntAll.json?corp_code=&bsns_year=&reprt_code=&fs_div={CFS|OFS}` — 단일회사 **전체** 재무제표.
  기존 `dart_financial` 의 `fnlttSinglAcnt`(주요계정)에는 주당이익·지배기업 소유주지분이 없어 쓰지 않는다.
- `stockTotqySttus.json?corp_code=&bsns_year=&reprt_code=` — 주식의 총수(BPS 분모).

**값의 의미(문서 기준, 실응답 미확인 — 2026-09-30):**
- 금액 단위는 원(`currency`=KRW 인 행만 받는다), 주당이익은 원/주. 천원·백만원 환산을 하지 않는다.
- 분·반기 보고서의 손익 `thstrm_amount` 는 해당 3개월, `thstrm_add_amount` 는 사업연도 개시~기말 누적이다.
  1분기는 둘이 같다. 사업보고서 `thstrm_amount` 는 연간이다. **4분기 3개월 값은 공시되지 않는다** —
  FY − 9개월 누적으로 유도하고(2026-09-30 결정) 유도임을 행에 남긴다. EPS 의 유도는 가중평균 주식수가
  달라 근사다(매출·영업이익은 정확히 성립).
- EPS 는 기본주당이익(`ifrs-full_BasicEarningsLossPerShare`)·희석(`…Diluted…`)의 표준계정 줄만 쓴다.
  같은 계정이 여러 줄이면 이름에 '우선주'가 없는 줄 하나만 보통주로 받고, 그래도 여럿이면 거부한다.
- BPS 는 공시값이 없어 계산한다: 지배기업 소유주지분(연결) 또는 자본총계(별도) ÷ (발행주식총수 − 자기주식수),
  주식수는 `stockTotqySttus` 의 '합계'(보통주+우선주) 행. 기준시점은 보고기간 말이다. 소수 6자리 반올림.
- 연결(CFS)·별도(OFS)를 둘 다 받아 fs_basis 로 가른다. 한 회사 안에서 섞지 않는 선택 규칙은 DB 조회 함수가 진다.
- 12월 결산만 받는다. 보고서명의 기말 월이 3·6·9·12 가 아니면 그 회사를 거부로 드러낸다(비12월 결산 미지원).

**정정 이력:** 재무 API 는 (회사, 연도, 보고서) 의 **최신 제출본**만 준다. 정정 전 원값은 받을 수 없으므로
복원하지 않는다 — 판본의 가시시각은 그 판본을 낸 접수번호의 접수일로 정해져, 정정본 값이 정정 전 시점에
보이는 일이 없다(원본~정정 사이 구간은 "그 시점에 우리가 가진 판본 없음"이 된다).
"""

from __future__ import annotations

import dataclasses
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..failures import SafeFailureError
from .dart_financial import STATUS_MESSAGES, STOP_STATUS_CODES, DartFinancialSource
from .http import StopFetch

KST = timezone(timedelta(hours=9))
REPORT_CODES = {"Q1": "11013", "Q2": "11012", "Q3": "11014", "FY": "11011"}
_PERIOD_BY_CODE = {v: k for k, v in REPORT_CODES.items()}
_PERIOD_END = {"11013": (3, 31), "11012": (6, 30), "11014": (9, 30), "11011": (12, 31)}
RCEPT_NO = re.compile(r"[0-9]{14}")
_TICKER = re.compile(r"[0-9A-Z]{6}")
_REPORT_NAME = re.compile(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)")
_FLOW_ACCOUNTS = {
    "revenue": "ifrs-full_Revenue",
    "operating_income": "dart_OperatingIncomeLoss",
    "eps_basic": "ifrs-full_BasicEarningsLossPerShare",
    "eps_diluted": "ifrs-full_DilutedEarningsLossPerShare",
}
_EQUITY_ACCOUNT = {"CFS": "ifrs-full_EquityAttributableToOwnersOfParent", "OFS": "ifrs-full_Equity"}
_UNITS = {"revenue": "KRW", "operating_income": "KRW", "eps_basic": "KRW_per_share",
          "eps_diluted": "KRW_per_share", "bps": "KRW_per_share", "bps_total_shares": "KRW_per_share"}
# ── 응답 무결성과 거부 사유 분류(ALPHA-1169) ──────────────────────────────────────────────────────────
# 순서: ① 응답 무결성(response_damage) → ② 지표 해석·계산(extract·derive_q4) → ③ 거부 사유 분류(reject_class).
# ①을 통과하지 못한 보고서의 거부는 사유가 무엇이든 처리 오류다 — "결손처럼 보이는 파손"이 결손으로 세어지지 않는다.
_STATEMENT_KINDS = frozenset({"BS", "IS", "CIS", "CF", "SCE"})    # 실응답에서 확인한 재무제표 종류
_FOREIGN_CURRENCIES = frozenset({"USD"})                           # 실응답에서 확인한 원화 아닌 통화
_SHARE_NOTE = "비고"                                                # 주식총수 표의 설명 행 — 수 칸에 글이 온다
_SHARE_CLASSES = ("합계", "보통주", "우선주")
# 정책 차단: 값을 만들 수 있어도 팀 결정으로 만들지 않는다.
POLICY_REASONS = frozenset({"bps_blocked_preferred_shares", "non_krw_currency"})
# 확인된 원천 부재: 온전한 응답에 그 값·줄이 없다(비슷한 줄도 없다).
# (금액 칸이 비지 않았는데 숫자가 아닌 것은 무결성 검사가 먼저 잡으므로, missing_value 는 빈 칸뿐이다.)
SOURCE_ABSENT_REASONS = frozenset({"account_not_found", "missing_value", "q4_derivation_input_missing",
                                   "bps_input_missing", "bps_share_class_row_absent"})
# 지원하지 않는 표기·계정: 원천에는 있어 보이는데 이 파서가 읽지 않는다. 원천 부재도 정책 차단도 아니다.
UNSUPPORTED_REASONS = frozenset({"account_unsupported", "bps_share_class_label_unsupported"})
GAP_CLASSES = frozenset({"policy", "source_absent"})               # 실행 실패로 세지 않는 분류(기록은 남긴다)
_REASON_CLASSES = (("policy", POLICY_REASONS), ("source_absent", SOURCE_ABSENT_REASONS),
                   ("unsupported", UNSUPPORTED_REASONS))
# 표준 계정 줄은 없지만 같은 항목으로 보이는 줄이 있는가 — (계정 id 에 든 줄기, 계정명 첫머리). 있으면 "원천에 없음"이
# 아니라 "지원하지 않는 계정"이다. 그 줄의 값을 대신 쓰지는 않는다(의미 확인 없이 치환·합산하지 않는다).
_SIMILAR_LINES = {
    "revenue": (("Revenue",), ("매출액", "영업수익", "수익(매출액)", "이자수익")),
    "operating_income": (("OperatingIncome", "ProfitLossFromOperatingActivities"), ("영업이익", "영업손익", "영업손실")),
    "eps_basic": (("BasicEarningsLossPerShare",), ("기본주당",)),
    "eps_diluted": (("DilutedEarningsLossPerShare",), ("희석주당",)),
    "bps": (("Equity",), ("자본총계", "지배기업")),               # 재무상태표의 자본 줄(다른 id·회사 정의 id 포함)
}


def _blank(value) -> bool:
    """금액 칸이 비었는가(None·빈 문자열·`-`). 비지 않았는데 `_amount` 가 None 이면 숫자 파손이다."""
    return value is None or str(value).strip() in ("", "-")       # 쉼표만 있는 칸은 빈 칸이 아니라 깨진 숫자다


def response_damage(lines: list[dict], shares: dict | None, period_end: str) -> list[str]:
    """한 보고서 응답(재무제표 줄 + 주식총수 표)의 무결성 위반 사유. 온전하면 빈 목록.

    지표를 해석하기 **전에** 본다 — 식별 칸이 깨진 줄은 계정 선택에서 조용히 빠져 "계정 없음"으로, 깨진 통화는
    "원화 아님"으로, 깨진 종류 행은 "행 없음"으로 읽히기 때문이다. 검사 범위는 지표 해석이 기대는 칸뿐이다.
    """
    problems: set[str] = set()
    used = set(_FLOW_ACCOUNTS.values()) | set(_EQUITY_ACCOUNT.values())
    for line in lines:
        # 칸의 타입부터 본다 — 배열·객체가 들어온 칸을 집합에 물으면 이 검사 자체가 죽어 다른 회사 정제까지 멈춘다.
        kind, account, currency = line.get("sj_div"), line.get("account_id"), line.get("currency")
        if not (isinstance(kind, str) and kind in _STATEMENT_KINDS and isinstance(account, str) and account.strip()):
            problems.add("bad_account_line")
        if not (isinstance(currency, str) and (currency == "KRW" or currency in _FOREIGN_CURRENCIES)):
            problems.add("currency_unrecognized")
        if isinstance(account, str) and account in used and any(
                not _blank(line.get(field)) and _amount(line.get(field)) is None
                for field in ("thstrm_amount", "thstrm_add_amount")):
            problems.add("amount_unreadable")
    for row in (shares or {}).get("list", []):
        if not isinstance(row, dict):
            continue                        # 파손 행은 상위 정제가 malformed_list_row 로 이미 거부한다
        kind, receipt = row.get("se"), row.get("rcept_no")
        if not (isinstance(receipt, str) and RCEPT_NO.fullmatch(receipt)):
            problems.add("bad_rcept_no")        # 숫자로 온 접수번호는 문자열 검사를 통과해 뒤에서 비교가 깨진다
        if not (isinstance(kind, str) and kind.strip()):
            problems.add("bad_share_row")
        elif _share_class(row) != _SHARE_NOTE and any(
                _share_count(row, field) is None for field in ("istc_totqy", "tesstk_co")):
            problems.add("share_count_unreadable")
    table = share_table_problem(shares, period_end)
    if table:
        problems.add(table)
    return sorted(problems)


def reject_class(reject: dict) -> str:
    """거부 한 건의 분류 — policy·source_absent·unsupported·error.

    무결성 위반 보고서의 거부(`response_damaged`), 사유 없는 거부, 분류 어휘에 없는 사유는 error 다. 사유가 여럿이면
    가장 무거운 쪽을 따른다. 유도 거부(`cause_reasons` — 4분기 유도의 3분기 쪽 사유)는 원인의 분류를 물려받는다.
    """
    reasons = [*(reject.get("reasons") or []), *(reject.get("cause_reasons") or [])]
    if reject.get("response_damaged") or not reasons:
        return "error"
    found = {next((name for name, group in _REASON_CLASSES if reason in group), "error") for reason in reasons}
    return next(name for name in ("error", "unsupported", "source_absent", "policy") if name in found)


def _similar_line(lines: list[dict], metric: str) -> bool:
    stems, heads = _SIMILAR_LINES[metric]
    return any(ln.get("sj_div") in (("BS",) if metric == "bps" else ("IS", "CIS")) and (
        any(stem in str(ln.get("account_id")) for stem in stems)
        or str(ln.get("account_nm") or "").replace(" ", "").startswith(heads)) for ln in lines)


BPS_FORMULA = ("bps = equity / (istc_totqy - tesstk_co); equity = {account}(BS, 기말); "
               "주식수 = stockTotqySttus se=보통주(우선주 없는 회사만); 소수 6자리 ROUND_HALF_UP")
BPS_TOTAL_FORMULA = ("bps_total_shares = equity / (istc_totqy - tesstk_co); equity = {account}(BS, 기말); "
                     "주식수 = stockTotqySttus se=합계(보통주+우선주, 통상 관행); 소수 6자리 ROUND_HALF_UP")
Q4_FORMULA = "Q4 = FY(사업보고서 thstrm_amount) - 9M(3분기보고서 thstrm_add_amount)"


@dataclass(frozen=True)
class DartResult:
    """DART 요청 하나의 결과. body 는 HTTP 200 일 때만 있고, request 에는 인증키가 없다."""

    kind: str                  # list · statement · shares
    request: dict
    status: str                # ok · empty · error
    detail: str | None
    body: bytes | None
    fetched_at: str
    stop: bool = False         # 키·IP·한도·점검 — 이 응답은 남기고 남은 호출을 멈춘다


def classify(body: bytes) -> tuple[str, str | None]:
    """DART JSON 상태 → ok·empty·error. 013(조회 데이터 없음)만 정상 0건이다."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "error", "unparseable_json"
    status = str(data.get("status")) if isinstance(data, dict) else None
    if status == "000" and isinstance(data.get("list"), list):
        return ("ok", None) if data["list"] else ("empty", "000_empty")
    if status == "013":
        return "empty", "013"
    return "error", f"dart_{status}"


class DartFundamentalSource:
    """corp_code 매핑은 기존 `DartFinancialSource` 의 corpCode.xml 로더를 그대로 쓴다."""

    def __init__(self, config, client):
        self._corp = DartFinancialSource(config, client)
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.client = client

    @property
    def enabled(self) -> bool:
        """설정 플래그와 인증키가 모두 있는가(기존 재무 소스와 같은 판정)."""
        return self._corp.enabled

    def corp_map(self) -> dict[str, dict[str, str]]:
        """종목코드 → {corp_code, corp_name}. corpCode.xml 은 실행당 한 번 받는다."""
        return self._corp._load_corp_map()

    def _get(self, kind: str, path: str, params: dict) -> DartResult:
        public = {"endpoint": path, **params}
        url = f"{self.base_url}/{path}?" + urllib.parse.urlencode({"crtfc_key": self.api_key or "", **params})
        try:
            body = self.client.request("GET", url, headers={"Accept": "application/json"}, decode=False)
        except StopFetch as exc:
            # 4xx·429 는 키·한도·차단 — 한 회사 문제가 아니라 남은 호출도 같은 답이다(본문 상태코드 STOP 과 같은 취급).
            return DartResult(kind, public, "error", f"http_{exc.status}", None, _now(), stop=True)
        except SafeFailureError as exc:
            return DartResult(kind, public, "error", str(exc), None, _now())
        # 수신시각은 응답을 다 받은 뒤다 — 요청 전에 찍으면 재시도·지연만큼 받기 전부터 보이게 된다.
        fetched_at = _now()
        status, detail = classify(body)
        # 키·IP·한도·점검은 한 회사 문제가 아니다 — 응답은 남기고 호출자가 남은 호출을 멈춘다.
        stop = status == "error" and bool(detail) and detail.removeprefix("dart_") in STOP_STATUS_CODES
        return DartResult(kind, public, status, detail, body, fetched_at, stop)

    def filings(self, corp_code: str, start: date, end: date) -> list[DartResult]:
        """정기공시 목록(모든 페이지). 정정 전 원본도 포함한다(접수번호→접수일 대조용)."""
        pages, page = [], 1
        while True:
            result = self._get("list", "list.json", {
                "corp_code": corp_code, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "A", "page_no": str(page), "page_count": "100"})
            if result.status != "ok":
                pages.append(result)
                return pages
            try:
                total = json.loads(result.body.decode("utf-8"))["total_page"]
            except (TypeError, ValueError, AttributeError, KeyError):
                total = None
            if not isinstance(total, int) or isinstance(total, bool) or total < page:
                # 뒤 페이지가 있는지 모른다(없음·문자열·소수·0·현재 쪽보다 작음) — 받은 본문은 남기되
                # 완전한 목록이라 하지 않는다.
                pages.append(dataclasses.replace(result, status="error", detail="bad_total_page"))
                return pages
            pages.append(result)
            if page >= total:
                return pages
            page += 1

    def statement(self, corp_code: str, bsns_year: str, reprt_code: str, fs_div: str) -> DartResult:
        """한 보고서·한 기준(CFS·OFS)의 전체 재무제표."""
        return self._get("statement", "fnlttSinglAcntAll.json", {
            "corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code, "fs_div": fs_div})

    def shares(self, corp_code: str, bsns_year: str, reprt_code: str) -> DartResult:
        """한 보고서의 주식의 총수 현황(BPS 분모)."""
        return self._get("shares", "stockTotqySttus.json", {
            "corp_code": corp_code, "bsns_year": bsns_year, "reprt_code": reprt_code})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_of(report_nm: str) -> tuple[str, str, int] | None:
    """보고서명 → (bsns_year, reprt_code, 기말 월). 정정 접두('[기재정정]')는 무시한다.
    '분기보고서 (YYYY.03)'=1분기, '(YYYY.09)'=3분기 — 월이 3·9 가 아니면 비12월 결산이다."""
    if not isinstance(report_nm, str):
        return None                         # 숫자·배열 등 파손 보고서명 — 호출자가 그 행만 거부한다
    match = _REPORT_NAME.search(report_nm)
    if not match:
        return None
    kind, year, month = match.group(1), match.group(2), int(match.group(3))
    code = {"사업": "11011", "반기": "11012"}.get(kind) or ("11013" if month == 3 else "11014")
    return year, code, month


def plan_reports(list_rows: list[dict], window_from: date, window_to: date) -> tuple[set, list[dict]]:
    """접수일이 창 안인 정기보고서 → 수집할 (bsns_year, reprt_code). 사업보고서면 같은 해 3분기도 붙인다
    (Q4 = FY − 9M 유도의 입력). 비12월 결산은 거부로 돌려준다."""
    targets = set()
    rows = [row for row in list_rows if isinstance(row, dict)]
    # 파손 행을 조용히 빼면 파손 목록이 "그 기간 정기보고서 없음"처럼 보인다 — 거부로 남긴다.
    rejects = [{"reasons": ["malformed_list_row"]} for row in list_rows if not isinstance(row, dict)]
    fy_years = {p[0] for row in rows if (p := report_of(row.get("report_nm"))) and p[1] == "11011"}
    # 결산월은 회사 단위로 사업보고서가 정한다 — 분기보고서의 월만 보면 6월 결산 회사의 9월 분기(=1분기)가 3분기로
    # 통과한다. 목록(소급 400일 — 사업보고서 한 번은 반드시 들어오는 폭)에 12월 사업보고서만 있어야 12월 결산이다.
    annual_months = {p[2] for row in rows if (p := report_of(row.get("report_nm"))) and p[1] == "11011"}
    if annual_months != {12}:
        reason = "non_december_fiscal_year" if annual_months else "fiscal_calendar_unconfirmed"
        rejects.extend({"corp_code": row.get("corp_code"), "report_nm": row.get("report_nm"), "reasons": [reason]}
                       for row in rows if report_of(row.get("report_nm")) is not None)
        return targets, rejects
    for row in rows:
        if not isinstance(row.get("report_nm"), str):
            rejects.append({"corp_code": row.get("corp_code"), "reasons": ["bad_report_nm"]})
            continue
        parsed = report_of(row.get("report_nm"))
        if parsed is None:
            continue
        year, code, month = parsed
        if month != _PERIOD_END[code][0]:
            rejects.append({"corp_code": row.get("corp_code"), "report_nm": row.get("report_nm"),
                            "reasons": ["non_december_fiscal_year"]})
            continue
        rcept_dt = row.get("rcept_dt")
        if not (isinstance(rcept_dt, str) and re.fullmatch(r"[0-9]{8}", rcept_dt)):
            # 접수일이 문자열 8자리가 아닌 행(숫자·배열 등 파손)은 그 행만 거부한다 — 비교에서 터지면 회사 전체가 빠진다.
            rejects.append({"corp_code": row.get("corp_code"), "report_nm": row.get("report_nm"),
                            "reasons": ["bad_rcept_dt"]})
            continue
        if window_from.strftime("%Y%m%d") <= rcept_dt <= window_to.strftime("%Y%m%d"):
            targets.add((year, code))
            # Q4 = FY − 9M 은 두 보고서가 한 실행에 있어야 다시 유도된다 — 어느 쪽이 새로(정정 포함) 접수되든
            # 짝을 함께 받는다. 사업보고서가 아직 없는 해의 3분기는 짝이 없다.
            if code == "11011":
                targets.add((year, "11014"))
            if code == "11014" and year in fy_years:
                targets.add((year, "11011"))
    return targets, rejects


def _amount(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in ("", "-"):
        return None          # 빈 칸은 0 이 아니다 — 결측으로 둔다
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _pick_line(lines: list[dict], account_id: str, statements: tuple[str, ...]) -> tuple[dict | None, str | None]:
    """계정 하나의 줄. 손익은 IS 우선·없으면 CIS. '우선주' 줄은 후보가 아니다 — 남은 한 줄만 인정한다.

    우선주 줄만 있으면 거부한다(한 줄뿐이라는 이유로 우선주 EPS 를 보통주 자리에 넣지 않는다). 구분 안 되는 줄이
    둘 이상이면 모호하다.
    """
    for sj in statements:
        found = [ln for ln in lines if ln.get("sj_div") == sj and ln.get("account_id") == account_id]
        candidates = [ln for ln in found if "우선주" not in (ln.get("account_nm") or "")]
        if len(candidates) == 1:
            return candidates[0], None
        if candidates:
            return None, "ambiguous_account_line"
        if found:
            return None, "preferred_share_line_only"
    return None, "account_not_found"


def _period_end(year: str, code: str) -> str:
    month, day = _PERIOD_END[code]
    return date(int(year), month, day).isoformat()


def extract(corp: dict, year: str, code: str, fs_div: str, statement: dict, shares: dict | None,
            ) -> tuple[list[dict], list[dict]]:
    """한 보고서·한 기준의 전체 재무제표 → 지표 행(공시 원값·BPS). 입력 근거를 행마다 남긴다."""
    rows, rejects = [], []
    # 접수번호·종목코드 형식이 틀린 판본은 DB CHECK 에서 그 실행의 적재 전체를 롤백시킨다 — 여기서 거른다.
    lines = [ln for ln in statement["body_json"]["list"] if isinstance(ln, dict)]
    if not _TICKER.fullmatch(str(corp.get("stock_code"))):
        return [], [{"corp_code": corp.get("corp_code"), "reprt_code": code, "reasons": ["bad_instrument_code"]}]
    bad = [ln for ln in lines if not RCEPT_NO.fullmatch(str(ln.get("rcept_no")))]
    if bad:
        rejects.append({"corp_code": corp.get("corp_code"), "reprt_code": code, "fs_basis": fs_div,
                        "reasons": ["bad_rcept_no"], "lines": len(bad)})
        lines = [ln for ln in lines if ln not in bad]
    base = {"corp_code": corp["corp_code"], "instrument_code": corp["stock_code"], "fiscal_year": int(year),
            "fs_basis": fs_div, "period_end": _period_end(year, code)}
    period = _PERIOD_BY_CODE[code]
    for metric, account_id in _FLOW_ACCOUNTS.items():
        line, problem = _pick_line(lines, account_id, ("IS", "CIS"))
        if line is None:
            if problem == "account_not_found" and _similar_line(lines, metric):
                problem = "account_unsupported"
            rejects.append({**base, "metric": metric, "reprt_code": code, "reasons": [problem]})
            continue
        if line.get("currency") != "KRW":
            rejects.append({**base, "metric": metric, "reprt_code": code, "reasons": ["non_krw_currency"]})
            continue
        if code == "11011":
            fields = [("CUMULATIVE", "FY", "thstrm_amount")]
        else:
            # 1분기는 3개월 = 누적이다. 누적 칸이 비었으면 1분기에 한해 3개월 값으로 대신한다.
            cumulative = ("thstrm_add_amount" if code != "11013" or line.get("thstrm_add_amount")
                          else "thstrm_amount")
            fields = [("QUARTER", period, "thstrm_amount"), ("CUMULATIVE", period, cumulative)]
        for period_kind, fiscal_period, field in fields:
            value = _amount(line.get(field))
            if value is None:
                rejects.append({**base, "metric": metric, "reprt_code": code, "field": field,
                                "reasons": ["missing_value"]})
                continue
            rows.append({**base, "fiscal_period": fiscal_period, "metric": metric, "period_kind": period_kind,
                         "derivation": "REPORTED", "value": str(value), "unit": _UNITS[metric], "formula": None,
                         "rcept_no": line["rcept_no"],
                         "inputs": [{"rcept_no": line["rcept_no"], "reprt_code": code, "sj_div": line.get("sj_div"),
                                     "account_id": account_id, "account_nm": line.get("account_nm"),
                                     "field": field, "value": str(value)}]})
    rows.extend(_bps(base, period if code != "11011" else "Q4", code, fs_div, lines, shares, rejects))
    return rows, rejects


# 주식 종류 표기 가운데 뜻이 같은 것(공백·줄바꿈을 없앤 뒤 정확히 일치할 때만) — 실응답 2026 반기 375곳에서 확인한 표기다.
# ① 같은 말에 '식'이 붙은 것 ② 표기 안에 보통주/우선주가 적힌 의결권 수식. 포함 검색으로 잡지 않는다 — `우선주 등`·
# `전환우선주`·`1우선주` 가 함께 걸린다. `종류주식`·`의결권 있는 주식`(보통주/우선주 표시 없음)·`기타주식` 은 같은 뜻인지
# 판단이 필요해 넣지 않는다(ALPHA-1170).
_SHARE_CLASS_ALIASES = {
    "보통주식": "보통주", "우선주식": "우선주",
    "의결권있는주식(보통주)": "보통주", "의결권없는주식(우선주)": "우선주",
    "보통주(의결권있는주식)": "보통주", "우선주(의결권없는주식)": "우선주",
    "의결권있는보통주": "보통주", "의결권없는우선주": "우선주",
    "의결권이있는주식(보통주)": "보통주", "의결권이없는주식(우선주)": "우선주",
}


def _share_class(row: dict) -> str:
    label = "".join(str(row.get("se") or "").split())
    return _SHARE_CLASS_ALIASES.get(label, label)


def _share_row(shares: dict | None, se: str) -> dict | None:
    """주식 종류 한 행. 같은 종류 행이 둘 이상이고 수가 서로 다르면 어느 쪽도 고르지 않는다(첫 행 선택은 순서 운이다)."""
    rows = [r for r in (shares or {}).get("list", []) if isinstance(r, dict) and _share_class(r) == se]
    if not rows:
        return None
    keys = ("istc_totqy", "tesstk_co", "rcept_no", "stlm_dt")
    if any(tuple(str(r.get(k)) for k in keys) != tuple(str(rows[0].get(k)) for k in keys) for r in rows[1:]):
        return {"se": se, "conflict": True, "rcept_no": rows[0].get("rcept_no")}
    return rows[0]


def _share_count(row: dict | None, field: str) -> Decimal | None:
    """주식총수 표의 수. `-` 는 0 이다(자기주식 없음·우선주 없음) — 금액 칸의 `-`(결측)와 다르다."""
    if row is None:
        return None
    value = row.get(field)
    text = ("" if value is None else str(value)).replace(",", "").strip()   # 숫자 0 은 결측이 아니다
    if text == "-":
        return Decimal(0)
    count = _amount(text)
    # 음수·소수 주식수는 파손 응답 — 주식은 정수 단위다. 정수 표기로 정규화한다("100.0"·"1E+2" 가 inputs 에 그대로
    # 남으면 DB 조회의 우선주 판정(정수 문자열 정규식)이 정책 차단을 파손으로 읽는다).
    if count is None or count < 0 or count != count.to_integral_value():
        return None
    return Decimal(int(count))


def _may_hold_shares(row: dict) -> bool:
    """발행수·자기주식 칸이 빈 칸·`-`·0 이 아니다 — 읽히는 수든 파손이든 주식이 있을 수 있는 행이다."""
    return any(row.get(field) is not None and str(row.get(field)).strip() != "" and _share_count(row, field) != 0
               for field in ("istc_totqy", "tesstk_co"))


def share_table_problem(shares: dict | None, period_end: str) -> str | None:
    """주식총수 응답이 한 표로서 유효하지 않은 이유(없으면 None). 응답이 아예 없으면 None(부재는 여기서 판정하지 않는다).

    분모 응답의 유효성은 자본 계정 추출과 독립이다 — 정제가 판본의 분모 상태(shares=error)를 여기로 정하고, `_bps` 도
    같은 규칙으로 값을 막는다(두 곳이 다른 규칙을 들면 "확정된 부재"와 "파손"이 갈린다).
    """
    if shares is None:
        return None
    rows = {se: _share_row(shares, se) for se in ("합계", "보통주", "우선주")}
    present = [row for row in rows.values() if row is not None]
    if not present:
        return None
    if any(row.get("conflict") for row in present):
        return "share_rows_inconsistent"
    if any(not RCEPT_NO.fullmatch(str(row.get("rcept_no"))) for row in present):
        return "bad_rcept_no"
    if len({(str(row.get("rcept_no")), str(row.get("stlm_dt"))) for row in present}) > 1 or \
            any(str(row.get("stlm_dt")) != period_end for row in present):
        return "share_rows_inconsistent"
    counts = {(se, field): _share_count(row, field) for se, row in rows.items() if row is not None
              for field in ("istc_totqy", "tesstk_co")}
    if rows["합계"] is not None and (counts[("합계", "istc_totqy")] is None or counts[("합계", "tesstk_co")] is None):
        return "share_count_unreadable"           # 합계 행의 수가 숫자가 아니거나 음수·소수 — 어느 분모도 없다
    # 종류별 행의 수 파손은 보통주 BPS 만 막고 통상 BPS 는 남긴다(`_bps`) — 표 전체의 무효는 아니다.
    issued = {se: counts[(se, "istc_totqy")] for se in rows if rows[se] is not None}
    treasury = {se: counts[(se, "tesstk_co")] for se in rows if rows[se] is not None}
    if any(issued[se] is not None and treasury[se] is not None and treasury[se] > issued[se] for se in issued):
        return "share_rows_inconsistent"
    if all(se in issued for se in ("합계", "보통주", "우선주")):
        # 발행수·자기주식 합계는 각각 따로 본다 — 한 열의 파손이 다른 열의 확인된 모순을 가리지 않게.
        for column in (issued, treasury):
            if None not in column.values() and column["보통주"] + column["우선주"] != column["합계"]:
                return "share_rows_inconsistent"
    return None


def _share_label_problem(shares: dict | None) -> str | None:
    """읽어야 할 종류 행(합계·보통주·우선주)이 없는데 다른 이름의 종류 행이 있으면 지원하지 않는 표기다.

    자본 줄이 없거나 원화가 아니어서 BPS 를 어차피 못 만드는 경우에도 따로 본다 — 앞선 결손 사유가 표기 문제를 가리지 않게.
    """
    rows = [r for r in (shares or {}).get("list", []) if isinstance(r, dict)]
    unknown = [r for r in rows if _share_class(r) not in (*_SHARE_CLASSES, _SHARE_NOTE)]
    missing = any(_share_row(shares, kind) is None for kind in _SHARE_CLASSES)
    return "bps_share_class_label_unsupported" if unknown and missing else None


def _bps(base, fiscal_period, code, fs_div, lines, shares, rejects) -> list[dict]:
    """BPS 두 지표 (실응답 2026-09-30 확인 — 삼성전자 우선주 802,371,203주, 자기주식은 보통주에만).

    - `bps`(보통주 1주 기준, v2 밸류 계약 `가격도 같은 주식단위`): 지배기업 소유주지분 ÷ (보통주 발행 − 보통주 자기주식).
      **우선주가 있으면 만들지 않는다** — 지분을 주식 종류별로 나눌 근거가 공시에 없어 보통주 순수 BPS 를 계산할 수 없다.
      그 회사는 `bps_blocked_preferred_shares` 로 거부 기록에 남는다(조용히 통상 BPS 로 대체하지 않는다).
    - `bps_total_shares`(통상 관행): 지배기업 소유주지분 ÷ (보통주+우선주 발행 합계 − 자기주식 합계). 항상 만든다.
      소비 쪽이 우선주 있는 회사에 이 값을 쓸지는 팀 결정(설계 §10.9)이다.
    기준시점은 주식총수 표의 `stlm_dt`(보고기간 말)이고 inputs 에 남긴다.
    """
    label = [reason] if (reason := _share_label_problem(shares)) else []
    line, problem = _pick_line(lines, _EQUITY_ACCOUNT[fs_div], ("BS",))
    if line is None:
        # 연결 재무제표에 지배지분 줄이 없고 자본총계 줄만 있으면 "자본이 없다"가 아니다 — 자본총계를 대신 쓰지는 않는다.
        if problem == "account_not_found" and _similar_line(lines, "bps"):
            problem = "account_unsupported"
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": [problem, *label]})
        return []
    if line.get("currency") != "KRW":
        # 원이 아닌 자본을 원/주로 적으면 단위가 조용히 틀린다(손익 줄과 같은 거부).
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": ["non_krw_currency", *label]})
        return []
    problem = share_table_problem(shares, base["period_end"])
    if problem:
        # 표 자체가 유효하지 않다(접수번호 형식·기준일≠보고기간 말·종류별 합 불일치·숫자 파손) — 어느 분모도 만들지 않는다.
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": [problem]})
        return []
    total, common, preferred = _share_row(shares, "합계"), _share_row(shares, "보통주"), _share_row(shares, "우선주")
    equity = _amount(line.get("thstrm_amount"))
    issued_total, treasury_total = _share_count(total, "istc_totqy"), _share_count(total, "tesstk_co")
    issued_common, treasury_common = _share_count(common, "istc_totqy"), _share_count(common, "tesstk_co")
    # 우선주 행이 없으면 "우선주 없음"이 아니라 "모름"이다(실응답은 없을 때도 `-` 행을 준다) — 보통주 BPS 를 막는 쪽으로.
    issued_preferred, treasury_preferred = _share_count(preferred, "istc_totqy"), _share_count(preferred, "tesstk_co")
    if equity is None or None in (issued_total, treasury_total) or issued_total - treasury_total <= 0:
        # 읽어야 할 행이 없는데 다른 이름의 종류 행이 있으면(label), 자본 칸이 비었더라도 표기 문제를 가리지 않는다.
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": label or ["bps_input_missing"]})
        return []
    # 보통주 BPS 를 만들 수 있는지의 판정을 한 번 내리고 통상 BPS 의 근거 줄에 남긴다 — DB 조회(bps_note)가 이 판정을
    # 그대로 읽는다(우선주 수만 보고 다시 추론하면 파손을 정책으로 읽는다). 우선주가 있어도 종류별 수를 하나라도
    # 못 읽었으면 파손이 먼저다 — 재수집 대상이 정책 대기로 보이면 안 된다.
    # 읽지 않는 종류 행(`종류주식` 등)에 주식이 있을 수 있으면 "모름"이다 — 그 주식이 우선주·합계에 들었는지 표만으로는
    # 알 수 없다(실응답 1,467표 가운데 보통주 BPS 가 나오는 표에는 그런 행이 없다, ALPHA-1170). `비고` 는 종류가 아니라
    # 설명 행이라 뺀다(실응답은 그 행의 수 칸에 `주1)` 같은 주석을 적는다).
    unread = any(isinstance(r, dict) and _share_class(r) not in (*_SHARE_CLASSES, _SHARE_NOTE)
                 and _may_hold_shares(r) for r in shares.get("list", []))
    if unread or None in (issued_common, treasury_common, issued_preferred, treasury_preferred):
        common_bps = "bps_share_rows_unreadable"
    elif issued_preferred > 0:
        common_bps = "bps_blocked_preferred_shares"        # 정책 차단(§10.9 팀 결정)
    elif issued_common - treasury_common <= 0:
        common_bps = "bps_input_missing"
    else:
        common_bps = "computed"
    equity_input = {"rcept_no": line["rcept_no"], "reprt_code": code, "sj_div": "BS",
                    "account_id": _EQUITY_ACCOUNT[fs_div], "account_nm": line.get("account_nm"),
                    "field": "thstrm_amount", "value": str(equity)}
    shares_input = {"rcept_no": total.get("rcept_no"), "reprt_code": code, "se": "합계",
                    "istc_totqy": str(issued_total), "tesstk_co": str(treasury_total),
                    "preferred_istc_totqy": str(issued_preferred) if issued_preferred is not None else "unknown",
                    "stlm_dt": total.get("stlm_dt"), "common_bps": common_bps}
    common_fields = {**base, "fiscal_period": fiscal_period, "period_kind": "POINT", "unit": "KRW_per_share",
                     "derivation": "EQUITY_OVER_SHARES", "rcept_no": max(line["rcept_no"], total["rcept_no"])}
    rows = [{**common_fields, "metric": "bps_total_shares",
             "value": str((equity / (issued_total - treasury_total)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
             "formula": BPS_TOTAL_FORMULA.format(account=_EQUITY_ACCOUNT[fs_div]),
             "inputs": [equity_input, shares_input]}]
    if common_bps != "computed":
        # 판정(common_bps)은 그대로 두고(DB 조회의 bps_note 가 읽는다) 거부 기록의 사유만 가른다. 있는 행의 수 파손은
        # 무결성 검사가 먼저 잡으므로, 여기서 종류 수를 못 읽는 것은 그 행이 없어서다 — 다른 이름의 종류 행이 있으면
        # 읽지 못하는 표기이고(지원하지 않음), 없으면 원천이 종류를 나눠 주지 않은 것이다. 어느 쪽도 우선주 0 으로 보지 않는다.
        reason = common_bps
        if common_bps == "bps_share_rows_unreadable" and unread:
            reason = "bps_share_class_label_unsupported"      # 읽는 행이 다 있어도 읽지 않는 종류 행에 주식이 있을 수 있다
        elif common_bps == "bps_share_rows_unreadable" and (common is None or preferred is None):
            reason = label[0] if label else "bps_share_class_row_absent"
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": [reason],
                        "preferred_istc_totqy": str(issued_preferred)})
        return rows
    rows.append({**common_fields, "metric": "bps",
                 "value": str((equity / (issued_common - treasury_common)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                 "formula": BPS_FORMULA.format(account=_EQUITY_ACCOUNT[fs_div]),
                 "inputs": [equity_input, {**shares_input, "se": "보통주", "istc_totqy": str(issued_common),
                                           "tesstk_co": str(treasury_common)}]})
    return rows


def derive_q4(fy_rows: list[dict], q3_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """같은 회사·연도·기준의 FY 누적과 9개월 누적 → Q4 3개월(유도). 입력이 없으면 만들지 않는다."""
    nine = {(r["metric"], r["fs_basis"]): r for r in q3_rows
            if r["period_kind"] == "CUMULATIVE" and r["fiscal_period"] == "Q3"}
    rows, rejects = [], []
    for fy in fy_rows:
        if fy["fiscal_period"] != "FY":
            continue
        q3 = nine.get((fy["metric"], fy["fs_basis"]))
        if q3 is None:
            rejects.append({"corp_code": fy["corp_code"], "fiscal_year": fy["fiscal_year"], "metric": fy["metric"],
                            "fs_basis": fy["fs_basis"], "reasons": ["q4_derivation_input_missing"]})
            continue
        value = Decimal(fy["value"]) - Decimal(q3["value"])
        rows.append({**fy, "fiscal_period": "Q4", "period_kind": "QUARTER", "derivation": "FY_MINUS_9M",
                     "value": str(value), "formula": Q4_FORMULA,
                     "rcept_no": max(fy["rcept_no"], q3["rcept_no"]),
                     "inputs": [*fy["inputs"], *q3["inputs"]]})
    return rows, rejects
