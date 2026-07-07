"""ingest_raw_financial 스텝 테스트 — bronze 통일 raw append·collection_log.

핵심 계약: 재무 raw 도 ingest_date/run_id 파티션에 받은 행을 전부 보존한다.
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


def test_saves_ingest_date_partition_and_log(tmp_path):
    # WHY: S035 realign — 재무 raw 도 가격과 동형으로 market 별 ingest_date 파티션(수집일)에
    #      저장되고, 실행 결과가 collection_log 로 남아야 운영에서 수집을 확인한다. 여러 문서
    #      (income·balance)는 한 market 파일에 함께 append 되고 각 행에 statement_type 이 남는다.
    responses = {
        ("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26", netIncome=100)],
        ("NVDA", "balance-sheet-statement", "annual"): [_row("2025-01-31", "2025-02-26", totalAssets=900)],
    }
    code, storage = _run(tmp_path, responses, run_id="r1")

    assert code == 0
    [key] = storage.list_keys("raw")  # market 별 1파일(US)
    assert key.startswith("raw/source=fmp/dataset=financial_statements/market=US")
    assert "/ingest_date=" in key and key.endswith("/part-00000.ndjson")
    lines = storage.get_bytes(key).decode("utf-8").strip().splitlines()
    assert len(lines) == 2
    assert {json.loads(ln)["statement_type"] for ln in lines} == {"income_statement", "balance_sheet"}

    log = _log(storage, "r1")
    assert log["status"] == "success"
    assert log["records_saved"] == 2


def test_dart_source_uses_same_bronze_partition_and_log(tmp_path):
    # WHY: OpenDART 는 새 벤더지만 raw 저장 규약은 FMP 재무와 같은 bronze 통일 규약이다.
    #      step 이 source_name 으로 source=dart 파티션과 로그를 만들고 별도 경로를 조립하지
    #      않아야 후속 canonical 이 dataset=financial_statements 를 일관되게 읽는다.
    class DartLikeSource:
        source_name = "dart"
        enabled = True
        planned_symbols = 1
        fetch_failures: list[dict] = []

        def fetch(self, symbols):
            yield {
                "market": "KR",
                "our_ticker": "005930",
                "stock_code": "005930",
                "corp_code": "00126380",
                "account_nm": "자산총계",
            }

    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    code = ingest_raw_financial.run(settings, storage, DartLikeSource(), "r1")

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    assert raw_key.startswith("raw/source=dart/dataset=financial_statements/market=KR")
    [line] = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert json.loads(line)["corp_code"] == "00126380"
    log = _log(storage, "r1")
    assert log["source_vendor"] == "dart"
    assert log["records_saved"] == 1


def test_raw_preserves_all_rows_no_dedup(tmp_path):
    # WHY: bronze 는 받은 행을 전부 보존한다(append) — 매일 재폴링해 같은 공시(같은
    #      fiscal_period_end·filing_date)가 반복 와도 조용히 버리지 않는다. 중복 제거·정정
    #      (SCD)·point-in-time 판정은 후속 canonical MERGE 소관이라 raw 에서 dedup 하지 않는다.
    responses = {("NVDA", "income-statement", "annual"): [
        _row("2025-01-31", "2025-02-26", netIncome=100),
        _row("2025-01-31", "2025-02-26", netIncome=100),  # 같은 공시 중복 — 그대로 보존
    ]}
    code, storage = _run(tmp_path, responses, run_id="r1")

    assert code == 0
    [key] = storage.list_keys("raw")
    lines = storage.get_bytes(key).decode("utf-8").strip().splitlines()
    assert len(lines) == 2  # 둘 다 보존(dedup 안 함)
    log = _log(storage, "r1")
    assert log["records_saved"] == 2
    assert "records_skipped_existing" not in log  # dedup 개념 자체가 raw 에 없다


def test_repoll_new_run_id_writes_separate_partition(tmp_path):
    # WHY: 매일 재폴링은 새 run_id 로 별도 파티션 파일에 그대로 append 된다(스냅샷 보존) —
    #      raw 에서 합치거나 덮지 않는다. 같은 run_id 재실행만 같은 키를 덮는다(재현 실행).
    responses = {("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26")]}
    storage = LocalStorage(tmp_path / "lake")
    _run(tmp_path, responses, storage=storage, run_id="day1")
    _run(tmp_path, responses, storage=storage, run_id="day2")

    keys = storage.list_keys("raw")
    assert len(keys) == 2  # run_id 별 파일 2개(중복 스냅샷도 보존)
    assert {k.split("run_id=")[1].split("/")[0] for k in keys} == {"day1", "day2"}


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
