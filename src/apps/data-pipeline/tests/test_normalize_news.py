"""normalize_news 스텝 테스트 — 벤더 이형 흡수 + 필수필드 게이트 + quality_log(ALPHA-131)."""

import json

from data_pipeline.lake import LocalStorage
from data_pipeline.steps import normalize_news


def _raw_key(source: str, market: str, run_id: str = "R1", date: str = "2026-07-01") -> str:
    return (
        f"raw/source={source}/dataset=stock_news/market={market}"
        f"/published_date={date}/run_id={run_id}/part-00000.ndjson"
    )


def _write_raw(storage, key: str, rows: list[dict]) -> None:
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    storage.put_bytes(key, body.encode("utf-8"))


def _fmp_row(**over) -> dict:
    # FMP 원본 + ingest provenance(our_ticker/market/article_id/fetched_at).
    row = {"title": "삼성전자 실적 개선", "url": "https://e.com/a", "site": "Reuters",
           "publishedDate": "2026-07-01 09:00:00", "text": "본문",
           "our_ticker": "AAPL", "market": "US", "article_id": "fmp-a",
           "fetched_at": "2026-07-01T00:00:00+00:00"}
    row.update(over)
    return row


def _bk_row(**over) -> dict:
    # BigKinds resultList 원본 + provenance. URL(PROVIDER_LINK_PAGE)은 일부러 뺀다 —
    # BigKinds 는 NEWS_ID 로 식별하고 URL 이 없을 수 있다(경고 대상, 탈락 아님).
    row = {"NEWS_ID": "01100101.20260701153000000", "TITLE": "SK하이닉스 신규 계약",
           "CONTENT": "BigKinds 원문", "PROVIDER": "테스트신문",
           "our_ticker": "000660", "market": "KR", "article_id": "bk-a",
           "fetched_at": "2026-07-01T00:00:00+00:00"}
    row.update(over)
    return row


def _quality_log(storage) -> dict:
    keys = storage.list_keys("operations_archive/data_quality_logs/")
    assert len(keys) == 1, keys
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def test_both_vendors_normalize_and_pass(tmp_path):
    # WHY: 정제의 존재 이유는 FMP(title/url/site/publishedDate)·BigKinds(TITLE/PROVIDER/NEWS_ID)
    #      이형을 하나의 표준 메타행으로 수렴시키는 것 — 둘 다 정상이면 통과로 집계돼야
    #      다운스트림이 동형으로 읽는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row()])

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert (log["records_read"], log["records_passed"], log["records_failed"]) == (2, 2, 0)


def test_bigkinds_maps_vendor_fields_and_date_from_news_id(tmp_path):
    # WHY: BigKinds 는 필드명(TITLE/PROVIDER)과 발행일 출처(NEWS_ID 임베드 타임스탬프)가 FMP 와
    #      다르다 — 정규화가 이를 흡수해 표준행을 만들고 게이트를 통과해야 이형이 하나로 읽힌다.
    row = normalize_news._normalize("bigkinds", _bk_row())
    assert row["title"] == "SK하이닉스 신규 계약"
    assert row["publisher"] == "테스트신문"
    assert row["published_at"][:10] == "2026-07-01"  # NEWS_ID .20260701… 에서 파생
    assert row["market"] == "KR"


def test_missing_title_excluded_from_passed(tmp_path):
    # WHY: 제목 없는 기사는 분석 최소 요건 미달 — passed 로 인증되지 않고 사유와 함께 남아야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(title="  ")])

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0 and log["records_failed"] == 1
    assert "missing_title" in log["failures"][0]["reasons"]


def test_missing_url_passes_as_warning_not_failure(tmp_path):
    # WHY: BigKinds 는 URL 없이 NEWS_ID 로 식별 가능 — URL 결측으로 벤더 전량을 탈락시키면 안
    #      된다(F1: mass false-fail 방지). 통과시키되 결측은 warnings 로 드러낸다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row()])  # PROVIDER_LINK_PAGE 없음

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 0
    assert log["records_warned"] == 1
    assert "missing_url" in log["warnings"][0]["reasons"]


def test_implausible_future_date_blocked(tmp_path):
    # WHY: 달력상 유효하지만 범위 밖인 미래 발행일(2099)은 파싱을 통과해 엉뚱한 파티션을
    #      만든다 — records_passed 로 인증되지 않게 게이트가 잡아야 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(publishedDate="2099-12-31 00:00:00")])

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert "implausible_published_at" in log["failures"][0]["reasons"]


def test_non_string_title_isolated_not_crash(tmp_path):
    # WHY: 비문자열 title(int·list)은 .strip()/normalize 에서 런 전체를 죽일 수 있다 — 타입가드로
    #      None 처리해 missing_title 로 깔끔히 분류하고 나머지 검증이 완료돼야 한다(crash-before-gate).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(our_ticker="OK"), _fmp_row(title=123, article_id="bad")])

    assert normalize_news.run(storage, "N1") == 0  # 크래시 없이 완료
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 1
    assert "missing_title" in log["failures"][0]["reasons"]


def test_non_object_row_isolated_not_crash(tmp_path):
    # WHY: 유효 JSON 이지만 객체가 아닌 행(null·배열)은 _normalize 의 record.get 에서 런 전체를
    #      죽인다 — 행 단위로 격리해 나머지 검증은 완료돼야 한다(격리≠은폐, Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    body = "null\n" + json.dumps(_fmp_row()) + "\n[]\n"
    storage.put_bytes(_raw_key("fmp", "US"), body.encode("utf-8"))

    assert normalize_news.run(storage, "N1") == 0  # 크래시 없이 완료
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 2
    assert all("non_object_row" in f["reasons"] for f in log["failures"])


def test_unparseable_json_isolated(tmp_path):
    # WHY: 깨진 JSON 한 줄이 검증 배치를 끊으면 안 된다 — 격리 후 나머지는 검증한다.
    storage = LocalStorage(tmp_path / "lake")
    body = "{broken\n" + json.dumps(_fmp_row()) + "\n"
    storage.put_bytes(_raw_key("fmp", "US"), body.encode("utf-8"))

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 1 and log["records_failed"] == 1
    assert "unparseable_json" in log["failures"][0]["reasons"]


def test_unsupported_vendor_reported_not_silently_passed(tmp_path):
    # WHY: 알 수 없는 뉴스 벤더는 필드맵이 없어 조용히 통과시키면 안 된다 — 사유로 드러낸다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("googlerss", "US"), [_fmp_row()])

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert log["records_passed"] == 0
    assert "unsupported_vendor" in log["failures"][0]["reasons"]


def test_input_run_id_scopes_validation(tmp_path):
    # WHY: 특정 수집 런만 재검증할 수 있어야(멱등·부분 재실행) 전량 재스캔 없이 운영한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row()])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [_fmp_row(article_id="x"), _fmp_row(article_id="y")])

    assert normalize_news.run(storage, "N1", input_run_id="R2") == 0
    log = _quality_log(storage)
    assert log["records_read"] == 2  # R1(1건) 제외, R2(2건)만
