"""ingest_raw_financial 스텝 테스트 — 공시 정체성 키로 존재검사→신규만 적재(멱등 폴링).

핵심 계약: 매일 폴링해도 저장은 공시당 1회. 신규 분기·정정은 새 키, 재폴링은 skip.
각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9).
"""

import json

from data_pipeline.config import FinancialSource, load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.fmp_financial import FmpFinancialSource
from data_pipeline.steps import ingest_raw_financial

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[financial.source]
base_url = "https://fmp.example/stable"

[targets]
symbols = ["NVDA"]
"""

_MAP = {"NVDA": "NVDA"}


class FakeClient:
    """responses: {(fmp_symbol, endpoint, period): [rows]}. 미지정 대상은 빈 배열."""

    def __init__(self, responses):
        self.responses = responses

    def get(self, url: str, *, accept: str = "application/json") -> str:
        endpoint = url.split("?")[0].rsplit("/", 1)[1]
        symbol = url.split("symbol=")[1].split("&")[0]
        period = url.split("period=")[1].split("&")[0]
        return json.dumps(self.responses.get((symbol, endpoint, period), []))


def _row(date: str, filing: str, period: str = "FY", **vals) -> dict:
    return {"date": date, "fillingDate": filing, "period": period, **vals}


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _source(settings, responses, api_key="k", symbol_map=None):
    config = FinancialSource(
        base_url=settings.financial.source.base_url, api_key=api_key,
        symbol_map=_MAP if symbol_map is None else symbol_map,
    )
    return FmpFinancialSource(config, FakeClient(responses))


def _run(tmp_path, responses, storage=None, api_key="k", run_id="20260704T000000Z"):
    settings = _settings(tmp_path)
    storage = storage or LocalStorage(tmp_path / "lake")
    source = _source(settings, responses, api_key=api_key)
    code = ingest_raw_financial.run(settings, storage, source, run_id)
    return code, storage


def _log(storage, run_id):
    key = next(k for k in storage.list_keys("operations_archive") if f"run_id={run_id}" in k)
    return json.loads(storage.get_bytes(key))


def test_saves_new_filings_under_identity_keys(tmp_path):
    # WHY: S035 — 신규 공시가 공시 정체성 키(종목·문서·주기·회계기간·공시일)로 저장되고,
    #      실행 결과가 collection_log 로 남아야 운영에서 수집을 확인한다.
    responses = {
        ("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26", netIncome=100)],
        ("NVDA", "balance-sheet-statement", "annual"): [_row("2025-01-31", "2025-02-26", totalAssets=900)],
    }
    code, storage = _run(tmp_path, responses, run_id="r1")

    assert code == 0
    keys = storage.list_keys("raw")
    assert len(keys) == 2
    assert all(k.startswith("raw/source=fmp/dataset=financial_statements/statement_type=") for k in keys)
    assert all(
        "/period=annual/fiscal_period_end=2025-01-31/filing_date=2025-02-26/data.json" in k
        for k in keys
    )
    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["records_saved"] == 2
    assert log["records_skipped_existing"] == 0


def test_daily_repoll_skips_existing_no_duplicate(tmp_path):
    # WHY: 핵심 계약 — 매일 폴링하면 FMP 가 같은 분기를 반복 반환한다. 공시 정체성 키
    #      존재검사로 이미 있는 공시는 skip 해 중복 저장이 0이어야 한다(요청 매일, 저장 공시당 1회).
    responses = {("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26")]}
    storage = LocalStorage(tmp_path / "lake")
    code1, _ = _run(tmp_path, responses, storage=storage, run_id="day1")
    keys_after_1 = storage.list_keys("raw")
    code2, _ = _run(tmp_path, responses, storage=storage, run_id="day2")
    keys_after_2 = storage.list_keys("raw")

    assert code1 == 0 and code2 == 0
    assert keys_after_1 == keys_after_2  # 둘째 폴링이 새 raw 를 만들지 않음(멱등)
    assert len(keys_after_2) == 1
    day2 = _log(storage, "day2")
    assert day2["records_saved"] == 0
    assert day2["records_skipped_existing"] == 1


def test_restatement_and_new_period_add_new_keys(tmp_path):
    # WHY: 정정 공시(같은 기간·다른 filing_date)와 새 분기는 새 정체성이라 새 키로 적재돼
    #      원본과 함께 보존된다(덮어쓰지 않음 = point-in-time 이력, 룩어헤드 방지).
    storage = LocalStorage(tmp_path / "lake")
    first = {("NVDA", "income-statement", "quarter"): [_row("2025-03-31", "2025-04-30", period="Q1")]}
    _run(tmp_path, first, storage=storage, run_id="r1")

    second = {("NVDA", "income-statement", "quarter"): [
        _row("2025-03-31", "2025-05-15", period="Q1"),   # 정정(filing_date 다름)
        _row("2025-06-30", "2025-07-30", period="Q2"),   # 새 분기
        _row("2025-03-31", "2025-04-30", period="Q1"),   # 원본 재등장 → skip
    ]}
    code, _ = _run(tmp_path, second, storage=storage, run_id="r2")

    assert code == 0
    keys = storage.list_keys("raw")
    assert len(keys) == 3  # 원본 Q1 + 정정 Q1 + Q2 모두 보존
    filings = sorted(k.split("filing_date=")[1].split("/")[0] for k in keys)
    assert filings == ["2025-04-30", "2025-05-15", "2025-07-30"]
    r2 = _log(storage, "r2")
    assert r2["records_saved"] == 2           # 정정 + 새 분기
    assert r2["records_skipped_existing"] == 1  # 원본 재등장


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입은 실패가 아니라 명시적 skip — 조용히 아무것도 안 하고 성공처럼 보이면
    #      안 되고, skip 사실이 로그로 남아야 한다(Rule 12).
    code, storage = _run(tmp_path, {}, api_key=None, run_id="r1")

    assert code == 0
    assert storage.list_keys("raw") == []
    assert _log(storage, "r1")["status"] == "skipped"


def test_all_empty_responses_marks_error(tmp_path):
    # WHY: 재무제표는 매 실행이 '최근 N기'를 재요청한다 — 매핑 대상이 있는데 전 엔드포인트가
    #      200 [] 를 주면 정상 '데이터 없음'이 아니라 엔드포인트 변경·커버리지 상실 같은
    #      이상이다. success(0건)로 위장하면 스케줄러가 데이터 유실을 못 본다(Rule 12).
    #      (가격은 주말 공백이 정상이라 이 가드가 없지만, 재무는 빈 응답이 항상 비정상.)
    code, storage = _run(tmp_path, {}, run_id="r1")  # 활성·NVDA 매핑됨·전 응답 빈 배열

    assert code == 1
    assert storage.list_keys("raw") == []
    log = _log(storage, "r1")
    assert log["status"] == "error"
    assert log["records_fetched"] == 0


class _AllFailingClient:
    def get(self, url, *, accept="application/json"):
        raise RuntimeError("GET 재시도 소진")


def test_all_targets_failing_marks_run_error(tmp_path):
    # WHY: 대상 격리로 남은 대상은 계속 시도하되, 전부 실패해 0건이면 status=success 로
    #      남기면 안 된다(조용한 성공 금지 — fail loud).
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = FinancialSource(base_url=settings.financial.source.base_url, api_key="k", symbol_map=_MAP)
    source = FmpFinancialSource(config, _AllFailingClient())
    code = ingest_raw_financial.run(settings, storage, source, "r1")

    assert code == 1
    assert storage.list_keys("raw") == []
    log = _log(storage, "r1")
    assert log["status"] == "error"
    assert log["records_failed_targets"] == 6  # 3문서 × 2주기 전부


class _EndpointFailingClient(FakeClient):
    """지정한 엔드포인트만 실패(재시도 소진), 나머지는 정상."""

    def __init__(self, responses, failing_endpoint):
        super().__init__(responses)
        self.failing_endpoint = failing_endpoint

    def get(self, url, *, accept="application/json"):
        endpoint = url.split("?")[0].rsplit("/", 1)[1]
        if endpoint == self.failing_endpoint:
            raise RuntimeError("boom")
        return super().get(url, accept=accept)


def test_partial_failure_marks_run_partial(tmp_path):
    # WHY: 일부 대상만 실패하면 저장분은 있으나 온전치 않다 — partial 로 드러내고 실패
    #      대상을 로그에 남겨 운영이 손실을 인지하게 한다.
    responses = {("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26")]}
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = FinancialSource(base_url=settings.financial.source.base_url, api_key="k", symbol_map=_MAP)
    source = FmpFinancialSource(config, _EndpointFailingClient(responses, "cash-flow-statement"))
    code = ingest_raw_financial.run(settings, storage, source, "r1")

    assert code == 0  # 부분 성공은 비정상 종료가 아님
    log = _log(storage, "r1")
    assert log["status"] == "partial"
    assert log["records_saved"] == 1
    assert log["records_failed_targets"] == 2  # cash-flow annual + quarter


def test_raw_write_failure_still_writes_collection_log(tmp_path):
    # WHY: "결과는 항상 collection_log" 계약 — raw put_bytes 가 실패(IAM·네트워크)해도
    #      런 흔적이 사라지면 안 된다. status=error·exit 1 로 남고 로그는 남아야 한다.
    class RawFailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if key.startswith("raw/"):
                raise OSError("S3 raw write denied")
            super().put_bytes(key, data)  # operations_archive 로그는 정상

    responses = {("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26")]}
    code, storage = _run(tmp_path, responses, storage=RawFailingStorage(tmp_path / "lake"), run_id="r1")

    assert code == 1
    log = _log(storage, "r1")
    assert log["status"] == "error"
    assert "denied" in log["error"]


def test_enabled_but_no_mapped_targets_marks_skipped(tmp_path):
    # WHY: 활성 소스(키 주입됨)인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑)
    #      수집이 사실상 불가능하다 — success(0건)로 위장하지 않고 skip 으로 드러낸다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = FinancialSource(base_url=settings.financial.source.base_url, api_key="k")  # 심볼맵 0
    source = FmpFinancialSource(config, FakeClient({}))
    code = ingest_raw_financial.run(settings, storage, source, "r1")

    assert code == 0  # 잘못된 설정이지만 크래시는 아님 — 로그로 드러냄
    assert storage.list_keys("raw") == []
    log = _log(storage, "r1")
    assert log["status"] == "skipped"
    assert log["reason"] == "no mapped targets"
