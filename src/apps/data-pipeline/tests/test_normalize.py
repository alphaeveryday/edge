"""normalize 스텝 테스트 — raw → canonical 병합(멱등)·본문 분리·품질 로그."""

import io
import json

import pyarrow.parquet as pq

from data_pipeline.lake import (
    LocalStorage,
    canonical_news_articles_key,
    canonical_news_bodies_key,
    raw_news_partition,
)
from data_pipeline.parse import make_article_id
from data_pipeline.steps import normalize


def _raw_item(url: str, *, title: str = "제목", published: str = "2026-07-01 09:00:00",
              site: str = "Reuters", text: str = "본문") -> dict:
    return {
        "title": title, "url": url, "publishedDate": published, "site": site,
        "text": text, "our_ticker": "NVDA", "market": "US",
        "fetched_at": "2026-07-02T00:00:00+00:00",
        "article_id": make_article_id(url, title, published),
    }


def _seed_raw(storage: LocalStorage, records: list[dict], run_id: str = "r1") -> None:
    key = f"{raw_news_partition('fmp', 'US', '2026-07-01', run_id)}/part-00000.ndjson"
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    storage.put_bytes(key, lines.encode("utf-8"))


def _read_parquet(storage: LocalStorage, key: str) -> list[dict]:
    return pq.read_table(io.BytesIO(storage.get_bytes(key))).to_pylist()


def test_normalize_writes_meta_and_body_datasets(tmp_path):
    # WHY: S003 AC1 — 제목·발행시각·언론사·URL 이 canonical 메타로 저장되고,
    #      본문은 별도 데이터셋(news_article_bodies)으로 분리돼야 한다(확정 결정 4).
    storage = LocalStorage(tmp_path)
    _seed_raw(storage, [_raw_item("https://e.com/a")])

    assert normalize.run(storage, run_id="n1") == 0

    metas = _read_parquet(storage, canonical_news_articles_key("2026-07-01", "fmp"))
    assert len(metas) == 1
    meta = metas[0]
    assert meta["title"] == "제목"
    assert meta["publisher"] == "Reuters"
    assert meta["url"] == "https://e.com/a"
    assert meta["published_at"] == "2026-07-01T09:00:00+00:00"
    assert "body" not in meta  # 본문은 메타에 없다

    bodies = _read_parquet(storage, canonical_news_bodies_key("2026-07-01", "fmp"))
    assert bodies[0]["body"] == "본문"
    assert bodies[0]["article_id"] == meta["article_id"]


def test_rerun_is_idempotent_merge_by_article_id(tmp_path):
    # WHY: canonical 은 run_id 없는 article_id 병합(확정 결정 3) — 같은 raw 를
    #      다시 돌려도 행이 늘지 않고, 갱신된 항목은 새 값으로 덮여야 한다.
    storage = LocalStorage(tmp_path)
    _seed_raw(storage, [_raw_item("https://e.com/a")], run_id="r1")
    normalize.run(storage, run_id="n1")

    # 같은 기사(같은 URL)의 제목이 바뀐 두 번째 수집 런
    _seed_raw(storage, [_raw_item("https://e.com/a", title="수정된 제목")], run_id="r2")
    normalize.run(storage, run_id="n2")

    metas = _read_parquet(storage, canonical_news_articles_key("2026-07-01", "fmp"))
    assert len(metas) == 1
    assert metas[0]["title"] == "수정된 제목"


def test_failed_records_dropped_and_logged_with_reasons(tmp_path):
    # WHY: S003 AC2 — 게이트 실패 항목은 canonical 에 못 들어가되, 사유가
    #      data_quality_logs 에 남아야 한다(조용한 유실 금지).
    storage = LocalStorage(tmp_path)
    bad = _raw_item("https://e.com/bad")
    bad["publishedDate"] = "미상"
    _seed_raw(storage, [_raw_item("https://e.com/good"), bad])

    normalize.run(storage, run_id="n1")

    metas = _read_parquet(storage, canonical_news_articles_key("2026-07-01", "fmp"))
    assert [m["url"] for m in metas] == ["https://e.com/good"]

    [log_key] = storage.list_keys("operations_archive/data_quality_logs")
    log = json.loads(storage.get_bytes(log_key))
    assert log["records_read"] == 2
    assert log["records_passed"] == 1
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["unparseable_published_at"]


def test_empty_body_makes_no_body_row(tmp_path):
    # WHY: 본문 없는 기사에 빈 body 행을 만들면 소비자가 '본문 있음'으로 오독한다.
    storage = LocalStorage(tmp_path)
    _seed_raw(storage, [_raw_item("https://e.com/nobody", text="")])

    normalize.run(storage, run_id="n1")

    assert storage.list_keys("canonical/news/news_article_bodies") == []
    metas = _read_parquet(storage, canonical_news_articles_key("2026-07-01", "fmp"))
    assert len(metas) == 1  # 메타는 저장된다


def test_input_run_id_filters_raw(tmp_path):
    # WHY: 스케줄 실행은 직전 수집 런만 처리한다 — 필터가 무시되면 매 실행이
    #      raw 전체를 다시 읽어 비용/시간이 커진다.
    storage = LocalStorage(tmp_path)
    _seed_raw(storage, [_raw_item("https://e.com/r1")], run_id="r1")
    _seed_raw(storage, [_raw_item("https://e.com/r2")], run_id="r2")

    normalize.run(storage, run_id="n1", input_run_id="r2")

    metas = _read_parquet(storage, canonical_news_articles_key("2026-07-01", "fmp"))
    assert [m["url"] for m in metas] == ["https://e.com/r2"]
