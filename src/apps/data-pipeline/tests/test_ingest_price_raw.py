"""ingest_price_raw 스텝 테스트 — raw append(전부 보존, dedup 없음)·collection_log."""

import json

from data_pipeline.config import PriceSource, load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.fmp_price import FmpPriceSource
from data_pipeline.steps import ingest_price_raw

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[news.sources.fmp.symbol_map]
NVDA = "NVDA"
AAPL = "AAPL"
"005930" = "SSNLF"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[targets]
symbols = ["NVDA", "AAPL", "005930"]
"""

_MAP = {"NVDA": "NVDA", "AAPL": "AAPL", "005930": "SSNLF"}


class FakeClient:
    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = responses  # {fmp_symbol: [bars]}

    def get(self, url: str, *, accept: str = "application/json") -> str:
        symbol = url.split("symbol=")[1].split("&")[0]
        return json.dumps(self.responses.get(symbol, []))


def _bar(date: str, close: float = 10.0) -> dict:
    return {"date": date, "open": 9.0, "high": 11.0, "low": 8.5, "close": close, "volume": 100}


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _run(tmp_path, responses, api_key="k", run_id="20260703T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key=api_key)
    source = FmpPriceSource(config, FakeClient(responses), _MAP)
    code = ingest_price_raw.run(settings, storage, source, run_id)
    return code, storage


def test_saves_ingest_date_partition_and_log(tmp_path):
    # WHY: S004 AC1 — 수집분이 ingest_date 파티션 규약(market 별 1파일)대로 저장되고,
    #      실행 결과가 collection_log 로 남아야 운영에서 수집 여부를 확인할 수 있다.
    #      가격 raw 는 뉴스(published_date)와 달리 수집일(ingest_date)로 파티션한다.
    responses = {
        "NVDA": [_bar("2026-07-01"), _bar("2026-06-30")],  # US
        "SSNLF": [_bar("2026-07-01")],                      # KR
    }
    code, storage = _run(tmp_path, responses)

    assert code == 0
    keys = storage.list_keys("raw")
    # market 별 1파일, ingest_date 는 실행일(오늘) — 날짜 고정 대신 규약 구조로 검증.
    assert len(keys) == 2
    assert all(k.startswith("raw/source=fmp/dataset=price_daily/market=") for k in keys)
    assert all("/ingest_date=" in k and k.endswith("/part-00000.ndjson") for k in keys)
    us_key = next(k for k in keys if "market=US" in k)
    assert len(storage.get_bytes(us_key).decode("utf-8").strip().splitlines()) == 2  # 2 거래일

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "success"
    assert log["records_fetched"] == 3
    assert log["records_saved"] == 3


def test_raw_preserves_all_rows_including_repeated_trade_date(tmp_path):
    # WHY: raw 는 받은 행을 전부 보존한다(전부 append) — FMP 가 같은 거래일을 두 번
    #      줘도(이상치) 조용히 버리지 않는다. (market,ticker,trade_date) 정체성 판정·
    #      upsert 는 후속 canonical 소관이라 raw 단계에서 그 키로 dedup 하지 않는다.
    code, storage = _run(tmp_path, {"NVDA": [_bar("2026-07-01", 10.0), _bar("2026-07-01", 99.0)]})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 2  # 둘 다 보존(버리지 않음)
    assert {json.loads(line)["close"] for line in lines} == {10.0, 99.0}

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["records_saved"] == 2
    assert "records_skipped_duplicate" not in log  # dedup 개념 자체가 raw 에 없다


class _PartlyFailingClient(FakeClient):
    """지정한 심볼은 재시도 소진(RuntimeError), 나머지는 정상 응답."""

    def __init__(self, responses, failing):
        super().__init__(responses)
        self.failing = set(failing)

    def get(self, url, *, accept="application/json"):
        symbol = url.split("symbol=")[1].split("&")[0]
        if symbol in self.failing:
            raise RuntimeError("GET 재시도 소진")
        return super().get(url, accept=accept)


def _run_client(tmp_path, client, run_id="20260703T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k")
    source = FmpPriceSource(config, client, _MAP)
    return ingest_price_raw.run(settings, storage, source, run_id), storage


def test_all_symbols_failing_marks_run_error(tmp_path):
    # WHY: 심볼 격리로 남은 심볼은 계속 시도하되, 전 심볼이 실패해 0건 저장이면
    #      status=success 로 남기면 안 된다(조용한 성공 금지 — fail loud).
    code, storage = _run_client(tmp_path, _PartlyFailingClient({}, failing=["NVDA", "AAPL", "SSNLF"]))

    assert code == 1
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert log["records_failed_symbols"] == 3


def test_partial_failure_marks_run_partial(tmp_path):
    # WHY: 일부 심볼만 실패하면 저장분은 있으나 온전치 않다 — partial 로 드러내고
    #      실패 심볼을 로그에 남겨 운영이 손실을 인지하게 한다.
    client = _PartlyFailingClient({"NVDA": [_bar("2026-07-01")]}, failing=["AAPL", "SSNLF"])
    code, storage = _run_client(tmp_path, client)

    assert code == 0  # 부분 성공은 비정상 종료가 아님
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial"
    assert log["records_saved"] == 1
    assert log["records_failed_symbols"] == 2


def test_raw_write_failure_still_writes_collection_log(tmp_path):
    # WHY: "결과는 항상 collection_log" 계약 — raw put_bytes 가 실패(IAM·네트워크)해도
    #      런 흔적이 사라지면 안 된다. status=error·exit 1 로 남고 로그는 남아야 한다.
    class RawFailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            if key.startswith("raw/"):
                raise OSError("S3 raw write denied")
            super().put_bytes(key, data)  # operations_archive 로그는 정상

    settings = _settings(tmp_path)
    storage = RawFailingStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k")
    source = FmpPriceSource(config, FakeClient({"NVDA": [_bar("2026-07-01")]}), _MAP)
    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    [log_key] = storage.list_keys("operations_archive")
    log = json.loads(storage.get_bytes(log_key))
    assert log["status"] == "error"
    assert "denied" in log["error"]


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입(로컬 등)은 실패가 아니라 '명시적 skip' — 조용히 아무것도 안 하고
    #      성공처럼 보이면 안 되고, skip 사실이 로그로 남아야 한다(Rule 12).
    code, storage = _run(tmp_path, {}, api_key=None)

    assert code == 0
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"


def test_enabled_but_no_mapped_targets_marks_skipped(tmp_path):
    # WHY: 활성 소스(키 주입됨)인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑)
    #      수집이 사실상 불가능하다 — success(0건)로 위장하지 않고 skip 으로 드러낸다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k")  # 활성
    source = FmpPriceSource(config, FakeClient({}), symbol_map={})  # 매핑 0
    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 0  # 잘못된 설정이지만 크래시는 아님 — 로그로 드러냄
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"
    assert log["reason"] == "no mapped targets"


def test_dateless_bar_is_preserved(tmp_path):
    # WHY: raw 는 하나도 못 버린다 — date 가 없는 봉도(파티션은 ingest_date 라 무관)
    #      수집일 파티션에 그대로 보존돼야 한다(품질 판정은 후속 canonical 소관).
    code, storage = _run(tmp_path, {"NVDA": [{"open": 1.0, "close": 2.0}]})  # date 없음

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    assert "/ingest_date=" in raw_key  # 수집일로 파티션됨(비어 있지 않음)
    assert len(storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()) == 1


def test_unexpected_failure_still_writes_log(tmp_path):
    # WHY: '결과는 항상 collection_log' 계약 — fetch 자체가 죽는 예기치 못한 실패도
    #      로그 없이 죽으면 운영에서 런이 있었는지조차 알 수 없다.
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    config = PriceSource(base_url=settings.price.source.base_url, api_key="k")
    source = FmpPriceSource(config, FakeClient({}), _MAP)
    source.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))

    code = ingest_price_raw.run(settings, storage, source, "20260703T000000Z")

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert "boom" in log["error"]
