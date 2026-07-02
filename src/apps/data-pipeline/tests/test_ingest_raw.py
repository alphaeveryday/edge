"""ingest_raw 스텝 테스트 — raw append·중복 제거·collection_log (local 스토리지)."""

import json

from data_pipeline.config import NewsSource, load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.fmp import FmpNewsSource
from data_pipeline.steps import ingest_raw

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[news.sources.fmp.symbol_map]
NVDA = "NVDA"
AAPL = "AAPL"

[price.source]
base_url = "https://example.com/price"

[targets]
symbols = ["NVDA", "AAPL"]
"""


class FakeClient:
    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = responses

    def get(self, url: str, *, accept: str = "application/json") -> str:
        symbol = url.split("symbols=")[1].split("&")[0]
        return json.dumps(self.responses.get(symbol, []))


def _item(url: str, published: str = "2026-07-01 09:00:00") -> dict:
    return {"title": f"기사 {url}", "url": url, "publishedDate": published, "site": "s"}


def _settings(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return load_settings(path)


def _run(tmp_path, responses, api_key="k", run_id="20260701T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = FmpNewsSource(
        settings.news.sources["fmp"].model_copy(update={"api_key": api_key}),
        FakeClient(responses),
    )
    code = ingest_raw.run(settings, storage, source, run_id)
    return code, storage


def test_saves_partitioned_ndjson_and_log(tmp_path):
    # WHY: S002 AC1 — 수집분이 published_date 파티션 규약대로 저장되고,
    #      실행 결과가 collection_log 로 남아야 운영에서 수집 여부를 확인할 수 있다.
    responses = {
        "NVDA": [_item("https://e.com/a", "2026-07-01 09:00:00")],
        "AAPL": [_item("https://e.com/b", "2026-06-30 23:00:00")],
    }
    code, storage = _run(tmp_path, responses)

    assert code == 0
    keys = storage.list_keys("raw")
    assert keys == [
        "raw/source=fmp/dataset=stock_news/market=US/published_date=2026-06-30"
        "/run_id=20260701T000000Z/part-00000.ndjson",
        "raw/source=fmp/dataset=stock_news/market=US/published_date=2026-07-01"
        "/run_id=20260701T000000Z/part-00000.ndjson",
    ]

    # started_date 는 실행 시점(오늘) — 날짜 고정 대신 존재·내용으로 검증한다.
    log_keys = storage.list_keys("operations_archive")
    assert len(log_keys) == 1
    log = json.loads(storage.get_bytes(log_keys[0]))
    assert log["status"] == "success"
    assert log["records_fetched"] == 2
    assert log["records_saved"] == 2


def test_same_article_across_symbols_saved_once_with_merged_mentions(tmp_path):
    # WHY: S002 AC2 — 같은 기사가 두 심볼 질의에 걸려 와도 중복 저장되지 않아야 한다.
    #      단, 두 종목 mention 은 모두 보존돼야 한다 — 뒤 record 를 통째로 버리면
    #      다운스트림(ticker 매칭)이 그 기사가 AAPL 도 언급했음을 알 수 없다.
    same = _item("https://e.com/shared")
    code, storage = _run(tmp_path, {"NVDA": [same], "AAPL": [dict(same)]})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    tickers = {m["ticker"] for m in record["mentions"]}
    assert tickers == {"NVDA", "AAPL"}  # 두 종목 연결 모두 보존

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["records_skipped_duplicate"] == 1


class _PartlyFailingClient(FakeClient):
    """지정한 심볼은 재시도 소진(RuntimeError), 나머지는 정상 응답."""

    def __init__(self, responses, failing):
        super().__init__(responses)
        self.failing = set(failing)

    def get(self, url, *, accept="application/json"):
        symbol = url.split("symbols=")[1].split("&")[0]
        if symbol in self.failing:
            raise RuntimeError("GET 재시도 소진")
        return super().get(url, accept=accept)


def _run_client(tmp_path, client, run_id="20260701T000000Z"):
    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = FmpNewsSource(
        settings.news.sources["fmp"].model_copy(update={"api_key": "k"}), client
    )
    return ingest_raw.run(settings, storage, source, run_id), storage


def test_all_symbols_failing_marks_run_error(tmp_path):
    # WHY: 심볼 격리로 남은 심볼은 계속 시도하되, 전 심볼이 실패해 0건 저장이면
    #      status=success 로 남기면 안 된다(조용한 성공 금지 — fail loud).
    client = _PartlyFailingClient({}, failing=["NVDA", "AAPL"])
    code, storage = _run_client(tmp_path, client)

    assert code == 1
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert log["records_failed_symbols"] == 2
    assert {f["symbol"] for f in log["failed_symbols"]} == {"NVDA", "AAPL"}


def test_partial_failure_marks_run_partial(tmp_path):
    # WHY: 일부 심볼만 실패하면 저장분은 있으나 온전치 않다 — partial 로 드러내고
    #      실패 심볼을 로그에 남겨 운영이 손실을 인지하게 한다.
    client = _PartlyFailingClient({"NVDA": [_item("https://e.com/a")]}, failing=["AAPL"])
    code, storage = _run_client(tmp_path, client)

    assert code == 0  # 부분 성공은 비정상 종료가 아님
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "partial"
    assert log["records_saved"] == 1
    assert log["records_failed_symbols"] == 1


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
    source = FmpNewsSource(
        settings.news.sources["fmp"].model_copy(update={"api_key": "k"}),
        FakeClient({"NVDA": [_item("https://e.com/a")]}),
    )
    code = ingest_raw.run(settings, storage, source, "20260701T000000Z")

    assert code == 1
    [log_key] = storage.list_keys("operations_archive")
    log = json.loads(storage.get_bytes(log_key))
    assert log["status"] == "error"
    assert "denied" in log["error"]


def test_record_carries_article_id(tmp_path):
    # WHY: article_id 가 raw 항목에 실려 있어야 Step2 가 재계산 없이 병합 키로 쓴다.
    code, storage = _run(tmp_path, {"NVDA": [_item("https://e.com/a")]})
    [raw_key] = storage.list_keys("raw")
    record = json.loads(storage.get_bytes(raw_key).decode("utf-8").strip())
    assert len(record["article_id"]) == 64


def test_disabled_source_skips_with_log(tmp_path):
    # WHY: 키 미주입(로컬 등)은 실패가 아니라 '명시적 skip' — 조용히 아무것도 안 하고
    #      성공처럼 보이면 안 되고, skip 사실이 로그로 남아야 한다(Rule 12).
    code, storage = _run(tmp_path, {}, api_key=None)

    assert code == 0
    assert storage.list_keys("raw") == []
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "skipped"


def test_partition_date_fallbacks():
    # WHY: raw 는 하나도 못 버린다 — 발행시각 없으면 수집시각, 그마저 없으면 런 시작일.
    #      fetched_at 하드 서브스크립트가 한 레코드로 런 전체를 죽이면 안 된다.
    fb = "2026-07-03"
    assert ingest_raw._partition_date({"publishedDate": "2026-07-01 09:00:00"}, fb) == "2026-07-01"
    assert ingest_raw._partition_date({"fetched_at": "2026-07-02T00:00:00+00:00"}, fb) == "2026-07-02"
    assert ingest_raw._partition_date({}, fb) == "2026-07-03"  # 둘 다 없으면 런 시작일


def test_disabled_skip_survives_log_write_failure(tmp_path):
    # WHY: skip 경로의 로그 쓰기도 best-effort — 스토리지 장애로 skip 로그마저 못
    #      남겨도 크래시 대신 정상 종료해야 한다(다른 경로와 계약 일관).
    class FailingStorage(LocalStorage):
        def put_bytes(self, key, data):
            raise OSError("storage down")

    settings = _settings(tmp_path)
    storage = FailingStorage(tmp_path / "lake")
    source = FmpNewsSource(
        settings.news.sources["fmp"].model_copy(update={"api_key": None}), FakeClient({})
    )
    assert ingest_raw.run(settings, storage, source, "20260701T000000Z") == 0  # 크래시 없음


def test_unexpected_failure_still_writes_log(tmp_path):
    # WHY: '결과는 항상 collection_log' 계약 — 재시도 소진 같은 예기치 못한 실패도
    #      로그 없이 죽으면 운영에서 런이 있었는지조차 알 수 없다. 부분 수집분은
    #      저장되고 status=error 로 남아야 한다.
    ok_item = _item("https://e.com/ok")

    class ExplodingClient(FakeClient):
        def get(self, url, *, accept="application/json"):
            if "AAPL" in url:
                raise RuntimeError("GET 재시도 소진")
            return super().get(url, accept=accept)

    settings = _settings(tmp_path)
    storage = LocalStorage(tmp_path / "lake")
    source = FmpNewsSource(
        settings.news.sources["fmp"].model_copy(update={"api_key": "k"}),
        ExplodingClient({"NVDA": [ok_item]}),
    )
    # 어댑터의 심볼 격리를 우회해 fetch 자체가 죽는 경우를 검증한다.
    source.fetch = lambda symbols: (_ for _ in ()).throw(RuntimeError("boom"))

    code = ingest_raw.run(settings, storage, source, "20260701T000000Z")

    assert code == 1
    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["status"] == "error"
    assert "boom" in log["error"]


def test_missing_published_date_falls_back_to_fetched_date(tmp_path):
    # WHY: raw 는 전부 보존한다(품질 게이트는 Step2) — 발행시각이 없어도 버리지 않고
    #      수집일 파티션으로라도 남아야 한다.
    item = {"title": "no date", "url": "https://e.com/nodate"}
    code, storage = _run(tmp_path, {"NVDA": [item]})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    assert "published_date=" in raw_key  # 수집일로 파티션됨(비어 있지 않음)
