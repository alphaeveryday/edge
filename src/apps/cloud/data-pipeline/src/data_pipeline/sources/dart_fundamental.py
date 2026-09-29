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
_REPORT_NAME = re.compile(r"(사업|반기|분기)보고서\s*\((\d{4})\.(\d{2})\)")
_FLOW_ACCOUNTS = {
    "revenue": "ifrs-full_Revenue",
    "operating_income": "dart_OperatingIncomeLoss",
    "eps_basic": "ifrs-full_BasicEarningsLossPerShare",
    "eps_diluted": "ifrs-full_DilutedEarningsLossPerShare",
}
_EQUITY_ACCOUNT = {"CFS": "ifrs-full_EquityAttributableToOwnersOfParent", "OFS": "ifrs-full_Equity"}
_UNITS = {"revenue": "KRW", "operating_income": "KRW", "eps_basic": "KRW_per_share",
          "eps_diluted": "KRW_per_share", "bps": "KRW_per_share"}
BPS_FORMULA = ("bps = equity / (istc_totqy - tesstk_co); equity = {account}(BS, 기말); "
               "주식수 = stockTotqySttus se=합계(보통주+우선주); 소수 6자리 ROUND_HALF_UP")
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
        fetched_at = datetime.now(timezone.utc).isoformat()
        public = {"endpoint": path, **params}
        url = f"{self.base_url}/{path}?" + urllib.parse.urlencode({"crtfc_key": self.api_key or "", **params})
        try:
            body = self.client.request("GET", url, headers={"Accept": "application/json"}, decode=False)
        except StopFetch as exc:
            return DartResult(kind, public, "error", f"http_{exc.status}", None, fetched_at)
        except SafeFailureError as exc:
            return DartResult(kind, public, "error", str(exc), None, fetched_at)
        status, detail = classify(body)
        if status == "error" and detail and detail.removeprefix("dart_") in STOP_STATUS_CODES:
            # 키·IP·한도·점검은 한 회사 문제가 아니다 — 남은 호출을 두드리지 않는다.
            raise StopFetch(f"DART {detail} {STATUS_MESSAGES.get(detail.removeprefix('dart_'), '')}")
        return DartResult(kind, public, status, detail, body, fetched_at)

    def filings(self, corp_code: str, start: date, end: date) -> list[DartResult]:
        """정기공시 목록(모든 페이지). 정정 전 원본도 포함한다(접수번호→접수일 대조용)."""
        pages, page = [], 1
        while True:
            result = self._get("list", "list.json", {
                "corp_code": corp_code, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "A", "page_no": str(page), "page_count": "100"})
            pages.append(result)
            if result.status != "ok":
                return pages
            total = json.loads(result.body.decode("utf-8")).get("total_page") or 1
            if page >= int(total):
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


def report_of(report_nm: str) -> tuple[str, str, int] | None:
    """보고서명 → (bsns_year, reprt_code, 기말 월). 정정 접두('[기재정정]')는 무시한다.
    '분기보고서 (YYYY.03)'=1분기, '(YYYY.09)'=3분기 — 월이 3·9 가 아니면 비12월 결산이다."""
    match = _REPORT_NAME.search(report_nm or "")
    if not match:
        return None
    kind, year, month = match.group(1), match.group(2), int(match.group(3))
    code = {"사업": "11011", "반기": "11012"}.get(kind) or ("11013" if month == 3 else "11014")
    return year, code, month


def plan_reports(list_rows: list[dict], window_from: date, window_to: date) -> tuple[set, list[dict]]:
    """접수일이 창 안인 정기보고서 → 수집할 (bsns_year, reprt_code). 사업보고서면 같은 해 3분기도 붙인다
    (Q4 = FY − 9M 유도의 입력). 비12월 결산은 거부로 돌려준다."""
    targets, rejects = set(), []
    for row in list_rows:
        parsed = report_of(row.get("report_nm"))
        if parsed is None:
            continue
        year, code, month = parsed
        if month != _PERIOD_END[code][0]:
            rejects.append({"corp_code": row.get("corp_code"), "report_nm": row.get("report_nm"),
                            "reasons": ["non_december_fiscal_year"]})
            continue
        rcept_dt = row.get("rcept_dt") or ""
        if window_from.strftime("%Y%m%d") <= rcept_dt <= window_to.strftime("%Y%m%d"):
            targets.add((year, code))
            if code == "11011":
                targets.add((year, "11014"))
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
    """계정 하나의 줄. 손익은 IS 우선·없으면 CIS. 여러 줄이면 '우선주'가 없는 한 줄만 인정한다."""
    for sj in statements:
        candidates = [ln for ln in lines if ln.get("sj_div") == sj and ln.get("account_id") == account_id]
        if len(candidates) > 1:
            candidates = [ln for ln in candidates if "우선주" not in (ln.get("account_nm") or "")]
        if len(candidates) == 1:
            return candidates[0], None
        if candidates:
            return None, "ambiguous_account_line"
    return None, "account_not_found"


def _period_end(year: str, code: str) -> str:
    month, day = _PERIOD_END[code]
    return date(int(year), month, day).isoformat()


def extract(corp: dict, year: str, code: str, fs_div: str, statement: dict, shares: dict | None,
            ) -> tuple[list[dict], list[dict]]:
    """한 보고서·한 기준의 전체 재무제표 → 지표 행(공시 원값·BPS). 입력 근거를 행마다 남긴다."""
    lines = statement["body_json"]["list"]
    rows, rejects = [], []
    base = {"corp_code": corp["corp_code"], "instrument_code": corp["stock_code"], "fiscal_year": int(year),
            "fs_basis": fs_div, "period_end": _period_end(year, code)}
    period = _PERIOD_BY_CODE[code]
    for metric, account_id in _FLOW_ACCOUNTS.items():
        line, problem = _pick_line(lines, account_id, ("IS", "CIS"))
        if line is None:
            rejects.append({**base, "metric": metric, "reprt_code": code, "reasons": [problem]})
            continue
        if line.get("currency") not in (None, "KRW"):
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


def _bps(base, fiscal_period, code, fs_div, lines, shares, rejects) -> list[dict]:
    line, problem = _pick_line(lines, _EQUITY_ACCOUNT[fs_div], ("BS",))
    if line is None:
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": [problem]})
        return []
    equity = _amount(line.get("thstrm_amount"))
    total = next((r for r in (shares or {}).get("list", []) if (r.get("se") or "").strip() == "합계"), None)
    issued = _amount(total.get("istc_totqy")) if total else None
    treasury = _amount(total.get("tesstk_co")) if total else None
    if total is not None and treasury is None and (total.get("tesstk_co") or "").strip() == "-":
        treasury = Decimal(0)   # 주식 총수 표의 '-' 는 자기주식 없음이다(금액 칸의 '-' 와 다르다)
    if equity is None or issued is None or treasury is None or issued - treasury <= 0:
        rejects.append({**base, "metric": "bps", "reprt_code": code, "reasons": ["bps_input_missing"]})
        return []
    value = (equity / (issued - treasury)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    rcept_nos = sorted({line["rcept_no"], total.get("rcept_no") or line["rcept_no"]})
    return [{**base, "fiscal_period": fiscal_period, "metric": "bps", "period_kind": "POINT",
             "derivation": "EQUITY_OVER_SHARES", "value": str(value), "unit": "KRW_per_share",
             "formula": BPS_FORMULA.format(account=_EQUITY_ACCOUNT[fs_div]), "rcept_no": rcept_nos[-1],
             "inputs": [{"rcept_no": line["rcept_no"], "reprt_code": code, "sj_div": "BS",
                         "account_id": _EQUITY_ACCOUNT[fs_div], "account_nm": line.get("account_nm"),
                         "field": "thstrm_amount", "value": str(equity)},
                        {"rcept_no": total.get("rcept_no"), "reprt_code": code, "se": "합계",
                         "istc_totqy": str(issued), "tesstk_co": str(treasury)}]}]


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
