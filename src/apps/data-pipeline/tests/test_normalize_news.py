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
    # BigKinds resultList 원본 + provenance. 실제 BigKinds 는 PROVIDER_LINK_PAGE(원문 URL)를
    # 주지만(실측 확인), 이 픽스처는 URL 없는 폴백 경로(정체성=NEWS_ID, missing_url 경고)를
    # 테스트하려 일부러 뺀다 — URL 정체성 테스트는 test_same_original_url… 이 따로 커버.
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


def _canonical_rows(storage, published_date: str) -> list[dict]:
    from data_pipeline.lake import canonical_news_articles_partition

    prefix = canonical_news_articles_partition(published_date)
    rows: list[dict] = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(normalize_news._read_parquet_rows(storage.get_bytes(key)))
    return rows


def _aid(record: dict) -> str:
    # normalize 는 raw stamp 를 신뢰하지 않고 정체성을 재계산하므로(Codex P2), 테스트도 기대 id 를
    # 같은 SSOT 로 파생한다 — 원문 URL 해시 / (URL 없으면) NEWS_ID.
    from data_pipeline.parse import news_article_id

    return news_article_id(record)


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


def test_bigkinds_url_absent_falls_back_to_news_id_not_title():
    # WHY: URL(PROVIDER_LINK_PAGE) 없는 BigKinds 행은 정체성이 NEWS_ID 폴백이어야 한다 — 제목|날짜로
    #      가면 제목·발행일 같은 별개 기사가 같은 id 로 붕괴해 canonical 병합에서 유실된다. URL 이
    #      있으면 url_hash 가 1순위지만(test_same_original_url…), 없을 땐 NEWS_ID 가 붕괴를 막는다.
    from data_pipeline.parse import news_article_id

    base = _bk_row(TITLE="같은 제목", NEWS_ID="01100101.20260701153000001")
    other = _bk_row(TITLE="같은 제목", NEWS_ID="01100101.20260701153000002")  # 같은 날짜, 다른 기사
    del base["article_id"]
    del other["article_id"]
    id1 = normalize_news._normalize("bigkinds", base)["article_id"]
    id2 = normalize_news._normalize("bigkinds", other)["article_id"]
    assert id1 and id2 and id1 != id2  # URL 없어도 NEWS_ID 로 별개 기사 → 별개 id
    assert id1 == news_article_id(base)  # ingest 와 동일 SSOT 사용(드리프트 없음)


class _FailingStorage:
    """LocalStorage 위임 + 지정 키에서 예외 — fail-loud(비0 종료) 경로 검증용."""

    def __init__(self, inner, *, fail_get: str | None = None, fail_put: str | None = None):
        self.inner = inner
        self.fail_get = fail_get  # get_bytes 키가 이 문자열을 포함하면 예외
        self.fail_put = fail_put  # put_bytes 키가 이 문자열을 포함하면 예외

    def list_keys(self, prefix):
        return self.inner.list_keys(prefix)

    def get_bytes(self, key):
        if self.fail_get and self.fail_get in key:
            raise OSError("의도된 읽기 실패")
        return self.inner.get_bytes(key)

    def put_bytes(self, key, data):
        if self.fail_put and self.fail_put in key:
            raise OSError("의도된 쓰기 실패")
        return self.inner.put_bytes(key, data)


def test_raw_read_failure_is_fail_loud(tmp_path):
    # WHY: raw 읽기 실패를 조용히 넘기면(exit 0) 검증이 사실상 안 돌았는데 성공으로 위장된다
    #      (Rule 12). raw_read_error 를 quality_log 에 남기고 비0 으로 종료해야 스케줄러가 안다.
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key("fmp", "US"), [_fmp_row()])
    storage = _FailingStorage(inner, fail_get="/dataset=stock_news/")

    assert normalize_news.run(storage, "N1") == 1  # fail-loud
    log = _quality_log(storage)
    assert "raw_read_error" in log["failures"][0]["reasons"]


def test_quality_log_write_failure_is_fail_loud(tmp_path):
    # WHY: 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알려야
    #      감사 로그 유실이 조용한 성공으로 묻히지 않는다(Rule 12).
    inner = LocalStorage(tmp_path / "lake")
    _write_raw(inner, _raw_key("fmp", "US"), [_fmp_row()])
    storage = _FailingStorage(inner, fail_put="data_quality_logs")

    assert normalize_news.run(storage, "N1") == 1  # fail-loud


def test_blocking_row_not_double_counted_as_warning(tmp_path):
    # WHY: blocking(missing_title)+경고(missing_url) 사유를 동시에 가진 행은 failures 로만 가고
    #      records_warned 를 올리면 안 된다 — 통과 안 한 행이 경고로도 세어지면 카운트가 부풀려진다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row(TITLE="  ")])  # url 없음 + 제목 결측

    assert normalize_news.run(storage, "N1") == 0
    log = _quality_log(storage)
    assert (log["records_passed"], log["records_failed"], log["records_warned"]) == (0, 1, 0)
    reasons = log["failures"][0]["reasons"]
    assert "missing_title" in reasons and "missing_url" in reasons  # 경고 사유도 함께 기록


def test_input_run_id_scopes_validation(tmp_path):
    # WHY: 특정 수집 런만 재검증할 수 있어야(멱등·부분 재실행) 전량 재스캔 없이 운영한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row()])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [_fmp_row(article_id="x"), _fmp_row(article_id="y")])

    assert normalize_news.run(storage, "N1", input_run_id="R2") == 0
    log = _quality_log(storage)
    assert log["records_read"] == 2  # R1(1건) 제외, R2(2건)만


# ── canonical 멱등 병합 + 중복 신호 (ALPHA-132) ──────────
def test_passing_rows_written_to_unified_date_partition(tmp_path):
    # WHY: canonical 은 소스를 흡수한 통합 구조다 — FMP·BigKinds 가 **한 published_date 파티션**에
    #      섞여 article_id 키로 적재되고 source_vendor 는 컬럼(provenance)이어야 한다(벤더 사일로 아님).
    storage = LocalStorage(tmp_path / "lake")
    fmp, bk = _fmp_row(), _bk_row()
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bk])

    assert normalize_news.run(storage, "N1") == 0
    by_id = {r["article_id"]: r for r in _canonical_rows(storage, "2026-07-01")}  # 통합 파티션 하나
    assert set(by_id) == {_aid(fmp), _aid(bk)}  # 두 벤더가 한 파티션에(원문 URL 해시 / NEWS_ID)
    assert by_id[_aid(fmp)]["source_vendor"] == "fmp"  # 벤더는 컬럼
    assert by_id[_aid(bk)]["source_vendor"] == "bigkinds"
    log = _quality_log(storage)
    assert log["canonical_written"] is True
    assert log["canonical_rows_written"] == 2 and log["canonical_partitions_written"] == 1


def test_same_original_url_unifies_identity_across_vendors(tmp_path):
    # WHY: canonical 통합의 핵심 — 정체성이 원문 URL 해시라 같은 원문 URL 이면 FMP(url)든
    #      BigKinds(PROVIDER_LINK_PAGE)든 **같은 article_id → 한 행으로 병합**(소스 무관 dedup).
    #      승자(최신 fetched_at)의 스칼라 메타가 대표가 되고 패자 스칼라는 버려지되, mentions 는
    #      양쪽 union — 종목별 권위 market 은 행 market 이 아니라 self-describing 한 mention 에 있다.
    storage = LocalStorage(tmp_path / "lake")
    fmp = _fmp_row(url="https://press.com/a", publishedDate="2026-07-01 09:00:00",
                   our_ticker="AAPL", fetched_at="2026-07-02T00:00:00+00:00")  # 최신 → 승자
    del fmp["article_id"]  # url 에서 파생되게
    bk = _bk_row(PROVIDER_LINK_PAGE="https://press.com/a", our_ticker="000660",
                 fetched_at="2026-07-01T00:00:00+00:00")  # 같은 원문 URL, 옛 수집
    del bk["article_id"]
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bk])

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert len(rows) == 1  # 같은 원문 URL → 같은 정체성 → 통합 병합
    row = rows[0]
    assert row["source_vendor"] == "fmp"  # 승자(최신) 프로비넌스가 행 대표
    assert sorted((m["market"], m["ticker"]) for m in json.loads(row["mentions"])) == \
        [("KR", "000660"), ("US", "AAPL")]  # 양쪽 종목 mention 보존(교차벤더 union)


def test_multi_mention_single_write_is_sorted_and_idempotent(tmp_path):
    # WHY: 단일 적재 경로도 병합 경로와 **같은 정렬 표현**을 써야 멱등 — mentions 를 raw(질의) 순서로
    #      쓰면 첫 런(단일 적재)과 재런(기존+신규 병합)의 바이트가 어긋난다(canonical 은 run_id
    #      없는 멱등 계약). 단일 적재도 dedup+정렬로 고정한다.
    storage = LocalStorage(tmp_path / "lake")
    fmp = _fmp_row(article_id="m",
                   mentions=[{"market": "US", "ticker": "MSFT"}, {"market": "US", "ticker": "AAPL"}])  # 역순
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])

    assert normalize_news.run(storage, "N1") == 0
    first = _canonical_rows(storage, "2026-07-01")
    assert json.loads(first[0]["mentions"]) == \
        [{"market": "US", "ticker": "AAPL"}, {"market": "US", "ticker": "MSFT"}]  # 단일 적재도 정렬됨
    assert normalize_news.run(storage, "N2") == 0
    assert _canonical_rows(storage, "2026-07-01") == first  # 단일→병합 재런 바이트 동일


def test_canonical_idempotent_across_runs(tmp_path):
    # WHY: canonical 은 run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가 같아야 한다 —
    #      두 번 돌려도 part-00000 하나, 내용 동일(멱등 재실행이 데이터를 늘리지 않게).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row()])

    assert normalize_news.run(storage, "N1") == 0
    first = _canonical_rows(storage, "2026-07-01")
    assert normalize_news.run(storage, "N2") == 0
    second = _canonical_rows(storage, "2026-07-01")
    assert first == second
    parts = [k for k in storage.list_keys("canonical/") if k.endswith(".parquet")]
    assert len(parts) == 1  # part 누적 없이 되쓰기


def test_same_article_latest_fetched_at_wins(tmp_path):
    # WHY: 같은 article_id 를 재적재(정정)하면 최신 수집분이 canonical 을 대표해야 한다 —
    #      오래된 스냅샷이 최신 정정을 덮지 않게(교차 런 병합 경로).
    storage = LocalStorage(tmp_path / "lake")
    old = _fmp_row(article_id="a", title="옛 제목", fetched_at="2026-07-01T00:00:00+00:00")
    new = _fmp_row(article_id="a", title="새 제목", fetched_at="2026-07-02T00:00:00+00:00")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [old])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [new])

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert len(rows) == 1 and rows[0]["title"] == "새 제목"  # 최신 fetched_at 승리


def test_failed_rows_excluded_from_canonical(tmp_path):
    # WHY: 게이트 탈락 행(제목 결측)은 canonical 에 들어가면 안 된다 — 분석 불가 뉴스 차단이
    #      이 정제의 핵심. 통과 행만 적재되고 탈락 행은 quality_log 에만 남는다(격리≠은폐).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"),
               [_fmp_row(title="살아남는 기사"), _fmp_row(title="  ")])  # 두 번째=제목 결측 → 탈락

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert [r["title"] for r in rows] == ["살아남는 기사"]  # 통과 행만, 탈락 행 제외


def test_duplicate_title_signal_logged_but_not_merged(tmp_path):
    # WHY: 다른 article_id·같은 정규화 제목(다른 URL)은 근접중복이다 — exact 병합하면 별개
    #      기사가 유실되므로 둘 다 보존하고 신호만 로깅한다(fuzzy 클러스터는 다운스트림 소관).
    storage = LocalStorage(tmp_path / "lake")
    a = _fmp_row(title="동일 헤드라인", url="https://e.com/a")
    b = _fmp_row(title="동일 헤드라인", url="https://e.com/b")  # 다른 원문 URL → 다른 id
    ida, idb = _aid(a), _aid(b)
    _write_raw(storage, _raw_key("fmp", "US"), [a, b])

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert sorted(r["article_id"] for r in rows) == sorted([ida, idb])  # 별개 기사 둘 다 보존
    sigs = _quality_log(storage)["duplicate_signals"]
    assert any(s["basis"] == "normalized_title" and set(s["article_ids"]) == {ida, idb} for s in sigs)


def test_scoped_run_does_not_write_canonical(tmp_path):
    # WHY: 스코프(--input-run-id) 실행은 재검증(quality_log)만 하고 canonical 은 안 쓴다 —
    #      canonical 은 전체 raw 를 보는 멱등 전체 런이 authoritative(부분 파티션 덮어쓰기 방지).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row()])

    assert normalize_news.run(storage, "N1", input_run_id="R1") == 0
    log = _quality_log(storage)
    assert log["canonical_written"] is False and log["canonical_rows_written"] == 0
    assert not any(k.endswith(".parquet") for k in storage.list_keys("canonical/"))


def test_mentions_preserved_fmp_merged_and_bigkinds_synthesized(tmp_path):
    # WHY: mentions[] 는 다운스트림 엔티티 링크 씨앗 — FMP 는 ingest 가 병합한 목록을,
    #      BigKinds 는 단일 our_ticker 를 합성해 보존해야 종목↔기사 연결이 유지된다.
    storage = LocalStorage(tmp_path / "lake")
    fmp = _fmp_row(mentions=[{"market": "US", "ticker": "AAPL"}, {"market": "US", "ticker": "MSFT"}])
    bk = _bk_row()
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bk])

    assert normalize_news.run(storage, "N1") == 0
    by_id = {r["article_id"]: r for r in _canonical_rows(storage, "2026-07-01")}
    assert json.loads(by_id[_aid(fmp)]["mentions"]) == [{"market": "US", "ticker": "AAPL"}, {"market": "US", "ticker": "MSFT"}]
    assert json.loads(by_id[_aid(bk)]["mentions"]) == [{"market": "KR", "ticker": "000660"}]  # our_ticker 합성


def test_full_run_merges_with_existing_partition_preserving_aged_out_raw(tmp_path):
    # WHY: canonical 은 raw 가 라이프사이클로 만료(Glacier/삭제)돼도 이전 적재분을 보존해야 한다 —
    #      전체 런이 기존 파티션을 읽어 새 article_id 를 '추가'(덮어쓰기 아님)해야 raw 가 사라진 옛
    #      기사가 유실되지 않는다. (_merge_partition 이 existing 을 떨어뜨리는 회귀를 격리해 잡는다 —
    #      전량 재스캔 테스트로는 new_rows 가 옛 기사도 재구성해 이 경로가 안 탄다.)
    import os

    storage = LocalStorage(tmp_path / "lake")
    a = _fmp_row(url="https://e.com/A")  # 원문 URL 로 정체성 파생(별개 기사)
    b = _fmp_row(url="https://e.com/B")
    ida, idb = _aid(a), _aid(b)
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [a])
    assert normalize_news.run(storage, "N1") == 0
    assert [r["article_id"] for r in _canonical_rows(storage, "2026-07-01")] == [ida]

    # R1 raw 를 만료(삭제) — LocalStorage 에 delete 가 없어 파일을 직접 제거해 시뮬레이션.
    os.remove(tmp_path / "lake" / _raw_key("fmp", "US", run_id="R1"))
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [b])
    assert normalize_news.run(storage, "N2") == 0

    ids = sorted(r["article_id"] for r in _canonical_rows(storage, "2026-07-01"))
    assert ids == sorted([ida, idb])  # 기존 A(raw 만료) 보존 + 신규 B 추가


def test_bigkinds_same_article_unions_mentions_across_tickers(tmp_path):
    # WHY: BigKinds 는 종목별 질의(preserve_all_rows)라 같은 기사(NEWS_ID)가 여러 추적 종목 질의에
    #      걸려 각기 단일 mention 으로 온다(ingest 가 mention 병합 안 함) — 병합이 최신 행으로 통째
    #      교체하면 한 종목의 mention 이 유실돼 종목↔기사 링크가 끊긴다. mentions 는 union 해야
    #      한다(Codex P2 회귀 방지). 멱등 재실행에도 union 이 안정적이어야 한다.
    storage = LocalStorage(tmp_path / "lake")
    a = _bk_row(article_id="X", our_ticker="373220")
    b = _bk_row(article_id="X", our_ticker="005380")  # 같은 기사, 다른 종목 질의
    _write_raw(storage, _raw_key("bigkinds", "KR"), [a, b])

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert len(rows) == 1  # 같은 article_id → 한 행
    assert sorted(m["ticker"] for m in json.loads(rows[0]["mentions"])) == ["005380", "373220"]

    # 멱등: 재실행해도 union 결과가 동일(중복 누적·순서 흔들림 없음).
    assert normalize_news.run(storage, "N2") == 0
    rows2 = _canonical_rows(storage, "2026-07-01")
    assert rows == rows2


def test_normalize_recomputes_identity_ignoring_stale_stamp(tmp_path):
    # WHY: canonical 정체성은 canonical 단계가 raw 내용에서 재계산해야 한다 — raw 에 (구 로직으로)
    #      stamp 된 옛 article_id 를 신뢰하면 정체성 로직 변경(NEWS_ID→원문 URL 우선)이 구 raw 에 안
    #      먹혀 같은 원문 URL 인 FMP·BigKinds 가 안 합쳐진다(Codex P2). stamp 무시·재계산을 고정.
    storage = LocalStorage(tmp_path / "lake")
    stale = _bk_row(PROVIDER_LINK_PAGE="https://press.com/x", article_id="STALE-NEWSID-BASED")
    _write_raw(storage, _raw_key("bigkinds", "KR"), [stale])

    assert normalize_news.run(storage, "N1") == 0
    rows = _canonical_rows(storage, "2026-07-01")
    assert [r["article_id"] for r in rows] == [_aid(stale)]  # 원문 URL 해시로 재계산
    assert rows[0]["article_id"] != "STALE-NEWSID-BASED"  # raw stamp 무시


def test_non_string_url_falls_back_to_news_id_not_crash(tmp_path):
    # WHY: 비문자열 PROVIDER_LINK_PAGE(예 123)가 normalize_url 의 .strip() 에서 크래시하면 ingest
    #      preserve_all_rows 수집 루프가 통째 중단된다(각도 H crash-before-gate). 비str URL 은 None
    #      으로 흘려 NEWS_ID 폴백으로 안전 식별해야 한다(Codex P2).
    storage = LocalStorage(tmp_path / "lake")
    bad = _bk_row(PROVIDER_LINK_PAGE=123)  # 비문자열 URL
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bad])

    assert normalize_news.run(storage, "N1") == 0  # 크래시 없이 완료
    rows = _canonical_rows(storage, "2026-07-01")
    assert len(rows) == 1 and rows[0]["article_id"] == _aid(bad)  # NEWS_ID 폴백
