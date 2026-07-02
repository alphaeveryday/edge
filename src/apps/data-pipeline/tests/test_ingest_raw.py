"""ingest_raw 스텝 테스트 — raw append·중복 제거·collection_log (local 스토리지)."""

import json

from data_pipeline.config import NewsSource, load_settings
from data_pipeline.lake import LocalStorage
from data_pipeline.sources.fmp import FmpNewsSource
from data_pipeline.steps import ingest_raw

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

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


def test_same_article_across_symbols_saved_once(tmp_path):
    # WHY: S002 AC2 — 같은 기사가 두 심볼 질의에 걸려 와도 중복 저장되지 않아야 한다.
    same = _item("https://e.com/shared")
    code, storage = _run(tmp_path, {"NVDA": [same], "AAPL": [dict(same)]})

    assert code == 0
    [raw_key] = storage.list_keys("raw")
    lines = storage.get_bytes(raw_key).decode("utf-8").strip().splitlines()
    assert len(lines) == 1

    log = json.loads(storage.get_bytes(storage.list_keys("operations_archive")[0]))
    assert log["records_skipped_duplicate"] == 1


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
