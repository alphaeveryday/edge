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


def _canonical_rows(storage, published_date: str, language: str | None = None) -> list[dict]:
    # language=None 이면 그 날짜의 모든 언어 파티션(ko·en)을 합쳐 읽는다 — 단일벤더 테스트는 자기
    # 언어만 있으니 무관하고, 언어 배치 자체를 검증하는 테스트는 language= 로 한 파티션만 읽는다.
    from data_pipeline.lake import canonical_news_articles_partition

    languages = [language] if language else ["ko", "en"]
    rows: list[dict] = []
    for lang in languages:
        prefix = canonical_news_articles_partition(lang, published_date)
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
    assert row["published_at"] == "2026-07-01T15:30:00+09:00"  # NEWS_ID 벽시계는 KST
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
def test_passing_rows_split_by_language_partition(tmp_path):
    # WHY: ALPHA-352 — canonical 은 language(벤더 고정: bigkinds=ko·fmp=en)→published_date 로 갈린다.
    #      다운스트림 언어모델이 언어별로 프루닝/분기하도록. FMP(en)·BigKinds(ko) 는 같은 날짜라도
    #      **서로 다른 언어 파티션**에 적재되고 source_vendor 는 여전히 컬럼(provenance)이다.
    storage = LocalStorage(tmp_path / "lake")
    fmp, bk = _fmp_row(), _bk_row()
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bk])

    assert normalize_news.run(storage, "N1") == 0
    en = _canonical_rows(storage, "2026-07-01", language="en")
    ko = _canonical_rows(storage, "2026-07-01", language="ko")
    assert [r["article_id"] for r in en] == [_aid(fmp)] and en[0]["source_vendor"] == "fmp"
    assert [r["article_id"] for r in ko] == [_aid(bk)] and ko[0]["source_vendor"] == "bigkinds"
    log = _quality_log(storage)
    assert log["canonical_written"] is True
    assert log["canonical_rows_written"] == 2 and log["canonical_partitions_written"] == 2  # 언어별 2 파티션


def test_same_url_stays_separate_across_language_partitions(tmp_path):
    # WHY: ALPHA-352 트레이드오프 — 정체성(원문 URL 해시)이 같아도 언어 파티션이 다르면 병합
    #      경로가 파티션 단위라 통합되지 않는다. 같은 원문 URL 의 FMP(en)·BigKinds(ko) 는 각 언어
    #      파티션에 **같은 article_id 로 공존**한다(실무상 드묾 — FMP 영문·BigKinds 국문). 교차언어
    #      dedup 은 다운스트림(news_dedup_cluster) 소관으로 넘긴다 — 언어분리가 우선 계약이므로.
    storage = LocalStorage(tmp_path / "lake")
    fmp = _fmp_row(url="https://press.com/a", publishedDate="2026-07-01 09:00:00",
                   our_ticker="AAPL", fetched_at="2026-07-02T00:00:00+00:00")
    del fmp["article_id"]  # url 에서 파생되게
    bk = _bk_row(PROVIDER_LINK_PAGE="https://press.com/a", our_ticker="000660",
                 fetched_at="2026-07-01T00:00:00+00:00")  # 같은 원문 URL
    del bk["article_id"]
    _write_raw(storage, _raw_key("fmp", "US"), [fmp])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [bk])

    assert normalize_news.run(storage, "N1") == 0
    en = _canonical_rows(storage, "2026-07-01", language="en")
    ko = _canonical_rows(storage, "2026-07-01", language="ko")
    assert [r["article_id"] for r in en] == [_aid(fmp)]  # 같은 URL 이지만 언어별로 갈려
    assert [r["article_id"] for r in ko] == [_aid(bk)]   # 각 파티션에 1행씩 공존(병합 안 됨)
    assert _aid(fmp) == _aid(bk)  # 정체성 자체는 같다(원문 URL 해시) — 갈린 건 파티션뿐
    assert json.loads(en[0]["mentions"]) == [{"market": "US", "ticker": "AAPL"}]  # 각자 자기 mention
    assert json.loads(ko[0]["mentions"]) == [{"market": "KR", "ticker": "000660"}]


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


def test_scoped_run_writes_canonical_without_losing_other_runs(tmp_path):
    # WHY: SFN 이 --input-run-id 로 도는 경로다(ALPHA-389) — 스코프가 canonical 을 안 쓰면
    #      파이프라인이 아무것도 적재하지 못한다. 그리고 스코프가 **기존 행을 날리지 않아야**
    #      한다: _write_canonical 은 파티션의 기존 parquet 을 전부 읽어 병합하지 덮어쓰지
    #      않는다. 이게 깨지면 매 런이 직전 런의 기사를 지워 canonical 이 하루치만 남는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US", run_id="R1"), [_fmp_row(url="https://x.test/a")])
    _write_raw(storage, _raw_key("fmp", "US", run_id="R2"), [_fmp_row(url="https://x.test/b")])

    assert normalize_news.run(storage, "N1", input_run_id="R1") == 0
    log = _quality_log(storage)
    assert log["canonical_written"] is True and log["canonical_rows_written"] == 1
    assert log["records_read"] == 1  # R2 는 스코프 밖 — 읽지도 않는다

    # R2 만 스코프 재실행 — R1 의 기사가 남은 채 R2 가 더해져야 한다.
    assert normalize_news.run(storage, "N2", input_run_id="R2") == 0
    urls = sorted(r["url"] for r in _canonical_rows(storage, "2026-07-01"))
    assert urls == ["https://x.test/a", "https://x.test/b"]


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


def test_language_derived_from_vendor_not_market(tmp_path):
    # WHY: ALPHA-352 언어 파티션은 **벤더 고정**이다 — market 이 아니다. FMP 는 KR 기업 ADR 의
    #      영어 기사를 market=KR 로 낼 수 있는데(005930→SSNLF), 그래도 언어는 en 이어야 한다.
    #      market 을 언어 프록시로 쓰면 이 행이 ko 로 잘못 분류된다 → 벤더가 SSOT.
    storage = LocalStorage(tmp_path / "lake")
    kr_adr = _fmp_row(our_ticker="005930", market="KR", url="https://e.com/adr")  # FMP·market=KR
    _write_raw(storage, _raw_key("fmp", "KR"), [kr_adr])

    assert normalize_news.run(storage, "N1") == 0
    assert _canonical_rows(storage, "2026-07-01", language="ko") == []  # market=KR 이어도 ko 아님
    en = _canonical_rows(storage, "2026-07-01", language="en")
    assert [r["article_id"] for r in en] == [_aid(kr_adr)] and en[0]["market"] == "KR"  # en 파티션·market 컬럼은 KR


def test_lead_text_carries_vendor_body_snippet(tmp_path):
    # WHY: 태깅은 제목만으론 역할(공급자·고객사·금액)을 못 뽑는다. 리드는 이미 raw 에
    # 있는데(BigKinds CONTENT·FMP text) canonical 이 안 실어 나르면 다운스트림이 본문 크롤을
    # 기다려야 한다 — 있는 걸 버리지 않는 게 이 컬럼의 존재 이유다.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("fmp", "US"), [_fmp_row(text="FMP 리드 문장")])
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row(CONTENT="BigKinds 리드 스니펫")])
    assert normalize_news.run(storage, "RUN1") == 0

    leads = {r["title"]: r["lead_text"] for r in _canonical_rows(storage, "2026-07-01")}
    assert leads["삼성전자 실적 개선"] == "FMP 리드 문장"
    assert leads["SK하이닉스 신규 계약"] == "BigKinds 리드 스니펫"


def test_lead_text_absent_is_none_not_gate_failure(tmp_path):
    # WHY: 리드는 선택 정보다 — 없다고 기사를 canonical 에서 떨어뜨리면 제목만으로도 가능한
    # 태깅까지 잃는다. 결측은 NULL 로 남기고 통과시킨다.
    storage = LocalStorage(tmp_path / "lake")
    row = _fmp_row()
    del row["text"]
    _write_raw(storage, _raw_key("fmp", "US"), [row])
    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert r["lead_text"] is None


# ── 종목명 탐지 매핑 (ALPHA-416) ────────────────────────────


def _write_holdings(storage, as_of_date: str, names: list[tuple[str, str]]) -> None:
    """canonical ETF holdings 스냅샷 픽스처 — 탐지 인덱스의 이름 출처."""
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_etf_holdings_partition

    schema = pa.schema([("constituent_name", pa.string()), ("constituent_ticker", pa.string())])
    table = pa.Table.from_pylist(
        [{"constituent_name": n, "constituent_ticker": t} for n, t in names], schema=schema
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    prefix = canonical_etf_holdings_partition("KR", as_of_date)
    storage.put_bytes(f"{prefix}/part-00000.parquet", buf.getvalue())


def test_bigkinds_mentions_detected_from_holdings_names(tmp_path):
    # WHY: 전체 경제 뉴스 수집(카테고리 주도)에선 our_ticker provenance 가 없다 — 종목 매핑은
    #      정규화가 holdings 종목명 탐지로 합성해야 다운스트림 in_universe 필터가 살아남는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-30", [("한미반도체", "042700"), ("삼성전자", "005930")])
    row = _bk_row(TITLE="한미반도체, HBM 장비 수주", our_ticker=None)
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert json.loads(r["mentions"]) == [{"market": "KR", "ticker": "042700"}]
    log = _quality_log(storage)
    assert log["mention_index_as_of_date"] == "2026-06-30"
    assert log["mention_index_names"] == 2
    assert log["detected_name_counts"] == {"한미반도체": 1}


def test_detection_unions_with_our_ticker_provenance(tmp_path):
    # WHY: 이행기엔 구 raw(our_ticker 있음)와 신 raw 가 섞인다 — 탐지가 provenance 를 교체하면
    #      구 raw 의 종목↔기사 링크가 유실되므로 union 이어야 한다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-30", [("삼성전자", "005930")])
    row = _bk_row(TITLE="삼성전자 공급계약", CONTENT="리드")  # our_ticker=000660 유지
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert sorted(m["ticker"] for m in json.loads(r["mentions"])) == ["000660", "005930"]


def test_detection_uses_latest_holdings_snapshot(tmp_path):
    # WHY: 유니버스는 스냅샷마다 바뀐다 — 옛 스냅샷으로 탐지하면 편입 종목이 안 잡힌다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-01", [("옛날종목", "111111")])
    _write_holdings(storage, "2026-06-30", [("한미반도체", "042700")])
    row = _bk_row(TITLE="한미반도체 실적, 옛날종목 매각", our_ticker=None)
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert json.loads(r["mentions"]) == [{"market": "KR", "ticker": "042700"}]


def test_no_holdings_snapshot_keeps_provenance_path(tmp_path):
    # WHY: 신규 레이크(holdings 미적재)에서 정규화가 죽거나 mentions 가 사라지면 안 된다 —
    #      탐지는 no-op, our_ticker 경로는 그대로.
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row()])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert json.loads(r["mentions"]) == [{"market": "KR", "ticker": "000660"}]
    log = _quality_log(storage)
    assert log["mention_index_as_of_date"] is None
    assert log["detected_name_counts"] == {}


def test_fmp_path_is_not_touched_by_detection(tmp_path):
    # WHY: FMP 는 ingest 병합 mentions[] 가 SSOT — 영문 기사에 한글 이름 탐지를 섞으면
    #      벤더 분기 계약이 흐려진다. 한글 이름이 우연히 제목에 있어도 탐지하지 않는다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-30", [("삼성전자", "005930")])
    row = _fmp_row(title="삼성전자 Samsung beats estimates", mentions=[{"market": "US", "ticker": "AAPL"}])
    _write_raw(storage, _raw_key("fmp", "US"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert json.loads(r["mentions"]) == [{"market": "US", "ticker": "AAPL"}]
    assert _quality_log(storage)["detected_name_counts"] == {}


def test_same_name_different_tickers_is_excluded_not_last_row_wins(tmp_path):
    # WHY: 이름을 키로 덮어쓰면 parquet 나열 순서가 승자를 정한다 — 같은 기사가 런마다 다른
    #      ticker 로 매핑되고, 틀린 ticker 가 canonical 에 들어가면 다운스트림엔 되돌릴 근거가
    #      없다. 동명이는 어느 쪽도 고르지 않고(entity_resolution 과 같은 판단), 제외 사실을
    #      quality log 로 드러내 탐지 누락이 조용히 묻히지 않게 한다(Rule 12).
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-30",
                    [("대상", "001680"), ("대상", "999999"), ("한미반도체", "042700")])
    row = _bk_row(TITLE="대상, 한미반도체와 공급계약", our_ticker=None)
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    # 동명이 '대상'은 어느 ticker 도 아니다 — 단일 ticker 인 '한미반도체'만 남는다
    assert json.loads(r["mentions"]) == [{"market": "KR", "ticker": "042700"}]
    log = _quality_log(storage)
    assert log["mention_index_ambiguous_names"] == ["대상"]
    assert log["mention_index_names"] == 1
    assert log["detected_name_counts"] == {"한미반도체": 1}


def test_duplicate_rows_and_ticker_whitespace_stay_in_the_index(tmp_path):
    # WHY: 동명이 제외의 반대편 — **정상 중복까지 모호로 몰면** 탐지가 조용히 무너진다.
    #      한 종목은 여러 parquet·여러 ETF 에 같은 이름으로 반복 등장하고(set 이라 1건),
    #      벤더에 따라 ticker 에 공백이 섞인다(strip 이라 같은 값). 이 둘을 안 접으면
    #      멀쩡한 이름이 ambiguous 로 빠져 '탐지 0건'이 정상 로그처럼 보인다.
    storage = LocalStorage(tmp_path / "lake")
    _write_holdings(storage, "2026-06-30", [
        ("한미반도체", "042700"), ("한미반도체", "042700"),      # 중복 행 — 1건으로 접힌다
        ("삼성전자", " 005930 "), ("삼성전자", "005930"),        # ticker 공백 이형 — 같은 값
    ])
    row = _bk_row(TITLE="한미반도체·삼성전자 동반 상승", our_ticker=None)
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert sorted(m["ticker"] for m in json.loads(r["mentions"])) == ["005930", "042700"]
    log = _quality_log(storage)
    assert log["mention_index_ambiguous_names"] == []
    assert log["mention_index_names"] == 2


def test_unicode_form_variants_are_one_name_not_two(tmp_path):
    # WHY: NFC/NFD 는 눈에 같고 파이썬엔 다른 문자열이다. 정규화를 안 하면 같은 이름의 두 표기가
    #      각각 '단일 ticker'로 인덱스에 들어가 동명이 판정을 통째로 우회한다 — ALPHA-448 이
    #      막으려던 오매핑이 유니코드 형태 차이로 되살아나고, quality log 는 깨끗해 보인다.
    #      저장소 관례(normalize_disclosure·parse_dart_*)대로 NFKC 후 매칭한다.
    import unicodedata

    storage = LocalStorage(tmp_path / "lake")
    nfc, nfd = "대상", unicodedata.normalize("NFD", "대상")
    assert nfc != nfd  # 전제: 두 표기는 실제로 다른 문자열이다
    _write_holdings(storage, "2026-06-30", [(nfc, "001680"), (nfd, "999999")])
    row = _bk_row(TITLE=f"{nfc}, 공급계약 체결", our_ticker=None)
    _write_raw(storage, _raw_key("bigkinds", "KR"), [row])

    assert normalize_news.run(storage, "RUN1") == 0

    [r] = _canonical_rows(storage, "2026-07-01")
    assert json.loads(r["mentions"]) == []  # 한 이름의 두 ticker — 어느 쪽도 고르지 않는다
    log = _quality_log(storage)
    assert log["mention_index_ambiguous_names"] == ["대상"]
    assert log["mention_index_names"] == 0


def test_holdings_index_load_failure_is_fail_loud_but_still_normalizes(tmp_path, monkeypatch):
    # WHY: 전체 수집 전환 후엔 인덱스가 mentions 의 유일한 공급원 — 로드가 터졌는데 exit 0 이면
    #      'mentions 전량 소실'이 정상 완료로 오독된다(Rule 12). 단 정규화 자체는 계속돼
    #      canonical 은 쓰인다(구 our_ticker 경로 생존).
    storage = LocalStorage(tmp_path / "lake")
    _write_raw(storage, _raw_key("bigkinds", "KR"), [_bk_row()])
    monkeypatch.setattr(normalize_news, "_holdings_name_index",
                        lambda s: (_ for _ in ()).throw(OSError("s3 read failed")))

    assert normalize_news.run(storage, "RUN1") == 1

    [r] = _canonical_rows(storage, "2026-07-01")  # canonical 은 그래도 쓰였다
    assert json.loads(r["mentions"]) == [{"market": "KR", "ticker": "000660"}]
    log = _quality_log(storage)
    assert "s3 read failed" in log["mention_index_error"]
