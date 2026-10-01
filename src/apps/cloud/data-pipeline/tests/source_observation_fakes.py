"""원천 관측 테스트용 DART 응답·구성종목 스냅샷 생성기 (ALPHA-1130).

형태는 OpenDART 개발가이드(list·fnlttSinglAcntAll·stockTotqySttus)와 canonical `etf_holdings` 파일
스키마(`steps/normalize_etf`)를 따른다. 값은 예시다 — 공급자 실응답 원문이 아니다.
"""

from __future__ import annotations

import io
import json
import zipfile

from data_pipeline.sources import dart_fundamental

SAMSUNG = {"corp_code": "00126380", "stock_code": "005930", "corp_name": "삼성전자"}
HYNIX = {"corp_code": "00164779", "stock_code": "000660", "corp_name": "SK하이닉스"}

# (bsns_year, reprt_code) → (보고서명, 접수번호, 접수일)
FILINGS = {
    ("2025", "11014"): ("분기보고서 (2025.09)", "20251114000101", "20251114"),
    ("2025", "11011"): ("사업보고서 (2025.12)", "20260310000202", "20260310"),
    ("2026", "11013"): ("분기보고서 (2026.03)", "20260515000303", "20260515"),
    ("2026", "11012"): ("반기보고서 (2026.06)", "20260814000404", "20260814"),
}

# 손익(원·원/주): 보고서별 (3개월, 누적). 사업보고서는 (연간, None).
FLOWS = {
    ("2025", "11014"): {"revenue": (80_000, 240_000), "operating_income": (9_000, 27_000),
                        "eps_basic": (1_000, 3_000), "eps_diluted": (990, 2_970)},
    ("2025", "11011"): {"revenue": (330_000, None), "operating_income": (38_000, None),
                        "eps_basic": (4_100, None), "eps_diluted": (4_060, None)},
    ("2026", "11013"): {"revenue": (85_000, 85_000), "operating_income": (10_000, 10_000),
                        "eps_basic": (1_100, 1_100), "eps_diluted": (1_090, 1_090)},
    ("2026", "11012"): {"revenue": (90_000, 175_000), "operating_income": (11_000, 21_000),
                        "eps_basic": (1_200, 2_300), "eps_diluted": (1_190, 2_280)},
}
EQUITY = {("2025", "11014"): 3_000_000, ("2025", "11011"): 3_100_000,
          ("2026", "11013"): 3_150_000, ("2026", "11012"): 3_200_000}
ACCOUNTS = {"revenue": ("ifrs-full_Revenue", "매출액"), "operating_income": ("dart_OperatingIncomeLoss", "영업이익"),
            "eps_basic": ("ifrs-full_BasicEarningsLossPerShare", "기본주당이익"),
            "eps_diluted": ("ifrs-full_DilutedEarningsLossPerShare", "희석주당이익")}


def fmt(value) -> str:
    return "" if value is None else f"{value:,}"


def statement(corp, year, code, fs_div, *, flows=None, equity=None, currency="KRW", rcept_no=None) -> bytes:
    """fnlttSinglAcntAll 응답. 연결은 지배기업 소유주지분, 별도는 자본총계."""
    rcept_no = rcept_no or FILINGS[(year, code)][1]
    lines = []
    for metric, (quarter, cumulative) in (flows or FLOWS[(year, code)]).items():
        account_id, name = ACCOUNTS[metric]
        lines.append({"rcept_no": rcept_no, "reprt_code": code, "bsns_year": year, "corp_code": corp["corp_code"],
                      "sj_div": "IS", "sj_nm": "손익계산서", "account_id": account_id, "account_nm": name,
                      "thstrm_amount": fmt(quarter), "thstrm_add_amount": fmt(cumulative), "ord": "1",
                      "currency": currency})
    equity_id = "ifrs-full_EquityAttributableToOwnersOfParent" if fs_div == "CFS" else "ifrs-full_Equity"
    lines.append({"rcept_no": rcept_no, "reprt_code": code, "bsns_year": year, "corp_code": corp["corp_code"],
                  "sj_div": "BS", "sj_nm": "재무상태표", "account_id": equity_id, "account_nm": "자본",
                  "thstrm_amount": fmt(equity if equity is not None else EQUITY[(year, code)]), "ord": "30",
                  "currency": currency})
    return json.dumps({"status": "000", "message": "정상", "list": lines}, ensure_ascii=False).encode()


def shares(corp, year, code, *, issued=1_000, treasury=0, preferred=0) -> bytes:
    """주식총수 표(실응답 형태: 보통주·우선주·합계·비고, 없는 수는 '-'). `issued` 는 합계, 우선주는 그 안의 몫."""
    rcept_no = FILINGS[(year, code)][1]
    rows = [{"rcept_no": rcept_no, "corp_code": corp["corp_code"], "se": se, "istc_totqy": fmt(i) if i else "-",
             # 실응답: stlm_dt = 그 보고서의 결산 기준일(반기 06-30, 사업보고서 12-31)
             "tesstk_co": fmt(t) if t else "-", "stlm_dt": dart_fundamental._period_end(year, code)}
            for se, i, t in (("보통주", issued - preferred, treasury), ("우선주", preferred, 0),
                             ("합계", issued, treasury), ("비고", 0, 0))]
    return json.dumps({"status": "000", "message": "정상", "list": rows}, ensure_ascii=False).encode()


def filing_list(corp, filings=FILINGS) -> bytes:
    rows = [{"corp_code": corp["corp_code"], "corp_name": corp["corp_name"], "stock_code": corp["stock_code"],
             "report_nm": name, "rcept_no": no, "rcept_dt": dt} for name, no, dt in filings.values()]
    return json.dumps({"status": "000", "message": "정상", "page_no": 1, "total_page": 1, "list": rows},
                      ensure_ascii=False).encode()


NO_DATA = json.dumps({"status": "013", "message": "조회된 데이타가 없습니다."}, ensure_ascii=False).encode()


class DartFake:
    """DartFundamentalSource 와 같은 인터페이스. 응답은 (종류, corp, year, code, fs) → bytes."""

    def __init__(self, corps, responses, lists):
        from data_pipeline.sources import dart_fundamental as df

        self._df, self._corps, self.responses, self.lists, self.calls = df, corps, responses, lists, []
        self.fetched_at = "2026-08-20T00:00:00+00:00"     # 수신시각 대역 — 테스트가 바꿀 수 있다

    def corp_map(self):
        return {c["stock_code"]: {"corp_code": c["corp_code"], "corp_name": c["corp_name"]} for c in self._corps}

    def _result(self, kind, request, body):
        status, detail = self._df.classify(body)
        return self._df.DartResult(kind, request, status, detail, body, self.fetched_at)

    def filings(self, corp_code, start, end):
        """실제 API 처럼 접수일 창(bgn_de~end_de) 안의 행만 돌려준다 — 창 밖 사업보고서를 재계획하는 경로가 검증되게."""
        self.calls.append(("list", corp_code, start.isoformat(), end.isoformat()))
        body = json.loads(self.lists[corp_code])
        if isinstance(body, dict) and isinstance(body.get("list"), list):
            lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            def inside(r):     # 파손 행·접수일 없는 행은 그대로 통과시킨다(파손 처리 테스트가 그 행을 봐야 한다)
                return not isinstance(r, dict) or not str(r.get("rcept_dt") or "").isdigit() \
                    or lo <= str(r.get("rcept_dt")) <= hi
            body = {**body, "list": [r for r in body["list"] if inside(r)]}
        return [self._result("list", {"endpoint": "list.json", "corp_code": corp_code}, json.dumps(body, ensure_ascii=False).encode())]

    def statement(self, corp_code, year, code, fs_div):
        self.calls.append(("statement", corp_code, year, code, fs_div))
        return self._result("statement", {"endpoint": "fnlttSinglAcntAll.json", "corp_code": corp_code,
                                          "bsns_year": year, "reprt_code": code, "fs_div": fs_div},
                            self.responses.get(("statement", corp_code, year, code, fs_div), NO_DATA))

    def shares(self, corp_code, year, code):
        self.calls.append(("shares", corp_code, year, code))
        return self._result("shares", {"endpoint": "stockTotqySttus.json", "corp_code": corp_code,
                                       "bsns_year": year, "reprt_code": code},
                            self.responses.get(("shares", corp_code, year, code), NO_DATA))


def write_holdings(storage, as_of: str, tickers: list[str], etf_id: str = "091160") -> None:
    """canonical KR 구성종목 스냅샷 한 장(normalize_etf 파일 스키마)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_etf_holdings_partition
    from data_pipeline.steps.normalize_etf import _canonical_schema

    rows = [{"market": "KR", "etf_id": etf_id, "constituent_ticker": t, "constituent_isin": None,
             "constituent_name": t, "constituent_mic": "XKRX", "constituent_asset_type": "stock",
             "weight_pct": 100 / len(tickers), "shares": 1.0, "market_value": 1.0, "currency": "KRW",
             "as_of_date": as_of, "source_vendor": "krx", "fetched_at": f"{as_of}T09:00:00+00:00"}
            for t in tickers]
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=_canonical_schema()), buf)
    storage.put_bytes(f"{canonical_etf_holdings_partition('KR', as_of)}/part-00000.parquet", buf.getvalue())


# ── KIS 마스터 ZIP (공식 헤더의 고정폭 형식, cp949) ──

def master_line(code, isin, name, group, large, medium, small, tail):
    back = f"{group:<2}1{large}{medium}{small}".ljust(tail, "N")
    return f"{code:<9}{isin:<12}{name}  {back}"


def zipped(member: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(member, ("\n".join(lines) + "\n").encode("cp949"))
    return buf.getvalue()


def filler(tail, n=520, prefix="9"):
    return [master_line(f"{prefix}{i:05d}", f"KR7{prefix}{i:05d}000", f"종목{i}", "ST", "0013", "0000", "0000", tail)
            for i in range(n)]


KOSPI = zipped("kospi_code.mst", [
    master_line("005930", "KR7005930003", "삼성전자", "ST", "0013", "0027", "0000", 227),
    master_line("091160", "KR7091160002", "KODEX 반도체", "EF", "0000", "0000", "0000", 227),
    *filler(227, n=2330)])                      # 시장별 행수 하한(실측의 90%)을 넘는 크기
KOSDAQ = zipped("kosdaq_code.mst", [
    master_line("058470", "KR7058470006", "리노공업", "ST", "1028", "0000", "0000", 221),
    *filler(221, n=1650, prefix="8")])
NAMES = zipped("idxcode.mst", ["00013전기·전자", "00027제조", "11028전기·전자"])


class SectorClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append(url)
        response = next(v for k, v in self.routes.items() if url.endswith(k))
        if isinstance(response, Exception):
            raise response
        return response




SECTOR_ROUTES = {"kospi_code.mst.zip": KOSPI, "kosdaq_code.mst.zip": KOSDAQ, "idxcode.mst.zip": NAMES}
