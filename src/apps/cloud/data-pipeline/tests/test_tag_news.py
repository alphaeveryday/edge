"""tag_news 스텝 테스트 — canonical→feature 배선 + 재태깅 방지 + 비용 상한 (ALPHA-365).

실 LLM 은 부르지 않는다 — complete_fn 을 주입받는 설계라 가짜로 돈다. 각 테스트는 '왜 그
동작이 중요한지'를 검사한다: 재태깅 방지는 돈과 PIT 재현이 걸린 계약이고, 언어 게이트는
한국어 전용 프롬프트가 영어 기사에 조용히 씌워지는 걸 막는다.
"""

import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_run_manifest_key,
    canonical_news_articles_partition,
    feature_news_assertions_minute_key,
    feature_news_assertions_minute_prefix,
    feature_news_assertions_partition,
    feature_run_manifest_key,
    quality_log_prefix,
)
from data_pipeline.steps import tag_news
from data_pipeline.tagging.extract import TAGGER_VERSION
from data_pipeline.tagging.ontology import ontology_version

_CANONICAL_COLUMNS = ("article_id", "published_at", "title", "lead_text", "language", "mentions")


def _write_canonical(storage, language: str, published_date: str, rows: list[dict]) -> None:
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _CANONICAL_COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _CANONICAL_COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    prefix = canonical_news_articles_partition(language, published_date)
    storage.put_bytes(f"{prefix}/part-00000.parquet", buf.getvalue())


def _read_feature(storage, language: str, published_date: str) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    prefix = feature_news_assertions_partition(language, published_date)
    rows = []
    for key in storage.list_keys(prefix + "/"):
        if key.endswith(".parquet"):
            rows.extend(pq.read_table(io.BytesIO(storage.get_bytes(key))).to_pylist())
    return rows


def _article(article_id: str = "a1", **over) -> dict:
    # mentions 기본값이 있어야 태깅된다 — mentions 게이트(ALPHA-416)가 무언급 기사를 거른다.
    row = {"article_id": article_id, "published_at": "2026-07-01T09:00:00+00:00",
           "title": "삼성전자, SK하이닉스와 공급계약 체결", "lead_text": "리드", "language": "ko",
           "mentions": json.dumps([{"market": "KR", "ticker": "005930"}])}
    row.update(over)
    return row


def _write_manifest(storage, run_id: str, partitions: list[dict]) -> None:
    storage.put_bytes(canonical_run_manifest_key("news_articles", run_id), json.dumps({
        "run_id": run_id, "producer": "normalize_news", "canonical_written": True,
        "canonical_partitions": partitions,
    }).encode())


def _manifest_partition(date: str, article_ids: list[str]) -> dict:
    return {"language": "ko", "published_date": date,
            "key": f"{canonical_news_articles_partition('ko', date)}/part-00000.parquet",
            "article_ids": article_ids}


def test_manifest_scope_reads_only_current_logical_ids(tmp_path):
    """WHY(ALPHA-1032): 병합 parquet 전체를 처리하면 직접 key여도 과거 ID를 다시 읽는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article("a-old"), _article("a-new")])
    _write_manifest(storage, "N1", [_manifest_partition("2026-07-01", ["a-new"])])
    list_calls: list[str] = []
    original_list_keys = storage.list_keys
    storage.list_keys = lambda prefix: list_calls.append(prefix) or original_list_keys(prefix)

    calls: list = []
    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete(calls)) == 0

    assert len(calls) == 1
    normal_list_calls = list(list_calls)
    assert [r["article_id"] for r in _read_feature(storage, "ko", "2026-07-01")] == ["a-new"]
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_written"] is True
    assert manifest["feature_partitions"][0]["article_ids"] == ["a-new"]
    assert canonical_news_articles_partition("ko", "") not in list_calls
    assert f"{feature_news_assertions_partition('ko', '2026-07-01')}/" not in normal_list_calls


def test_missing_input_manifest_fails_without_full_scan(tmp_path):
    """WHY(ALPHA-1032): 계보 결손을 canonical 전체 스캔으로 넓히면 정상 비용 회귀가 숨는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    calls: list = []

    assert tag_news.run(storage, "R1", input_run_id="missing",
                        complete_fn=_fake_complete(calls)) == 1
    assert calls == []
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_written"] is False


def test_manifest_missing_article_id_fails_loud(tmp_path):
    """WHY(ALPHA-1032): 직접 key가 있어도 ID 결손이면 손상 manifest이지 빈 성공이 아니다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    _write_manifest(storage, "N1", [_manifest_partition("2026-07-01", ["a1", "missing"])])

    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete([])) == 1
    assert _read_feature(storage, "ko", "2026-07-01") == []


def test_manifest_rejects_article_id_repeated_across_partitions(tmp_path):
    """WHY(ALPHA-1032): 논리 기사 하나를 날짜별로 중복 태깅하면 비용·1기사 1행 계약이 깨진다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_manifest(storage, "N1", [
        _manifest_partition("2026-07-01", ["a1"]),
        _manifest_partition("2026-07-02", ["a1"]),
    ])
    calls: list = []

    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete(calls)) == 1
    assert calls == []


def test_same_run_retry_keeps_ids_written_before_later_partition_failure(tmp_path):
    """WHY(ALPHA-1032): 재시도가 앞선 부분 성공을 skip해도 feature 계보는 유실되면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    _write_canonical(storage, "ko", "2026-07-02", [
        _article("a2", published_at="2026-07-02T09:00:00+09:00")])
    _write_manifest(storage, "N1", [
        _manifest_partition("2026-07-01", ["a1"]),
        _manifest_partition("2026-07-02", ["a2"]),
    ])
    original_put = storage.put_bytes

    def fail_second_feature(key, data):
        if "feature/news/assertions" in key and "published_date=2026-07-02" in key:
            raise OSError("의도된 두 번째 파티션 실패")
        return original_put(key, data)

    storage.put_bytes = fail_second_feature
    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete([])) == 1
    storage.put_bytes = original_put

    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete([])) == 0
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_written"] is True
    assert [p["article_ids"] for p in manifest["feature_partitions"]] == [["a1"], ["a2"]]


def test_same_run_retry_recovers_id_when_manifest_checkpoint_put_failed(tmp_path):
    """WHY(ALPHA-1032): feature만 저장된 장애 뒤 재시도가 current 행을 skip해도 계보는 남아야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    _write_manifest(storage, "N1", [_manifest_partition("2026-07-01", ["a1"])])
    original_put = storage.put_bytes
    manifest_key = feature_run_manifest_key("news_assertions", "R1")
    manifest_puts = 0

    def fail_checkpoint(key, data):
        nonlocal manifest_puts
        if key == manifest_key:
            manifest_puts += 1
            if manifest_puts == 2:
                raise OSError("의도된 checkpoint 실패")
        return original_put(key, data)

    storage.put_bytes = fail_checkpoint
    first_calls: list = []
    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete(first_calls)) == 1
    storage.put_bytes = original_put

    second_calls: list = []
    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete(second_calls)) == 0
    manifest = json.loads(storage.get_bytes(manifest_key))
    assert len(first_calls) == 1
    assert second_calls == []
    assert manifest["feature_written"] is True
    assert manifest["feature_partitions"][0]["article_ids"] == ["a1"]


def test_overlapping_manifest_retries_current_llm_error(tmp_path):
    """WHY(ALPHA-1032): 다음 전일·당일 겹침 수집이 같은 ID를 다시 내면 llm_error를 재판정한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    partition = _manifest_partition("2026-07-01", ["a1"])
    _write_manifest(storage, "N1", [partition])

    def boom(_system, _user):
        raise RuntimeError("일시 실패")

    assert tag_news.run(storage, "R1", input_run_id="N1", complete_fn=boom) == 0
    _write_manifest(storage, "N2", [partition])
    calls: list = []
    assert tag_news.run(storage, "R2", input_run_id="N2",
                        complete_fn=_fake_complete(calls)) == 0
    assert len(calls) == 1
    assert _read_feature(storage, "ko", "2026-07-01")[0]["status"] == "ok"


def test_empty_manifest_absorbs_current_intraday_mirror(tmp_path):
    """WHY(ALPHA-1032): canonical이 아직 없는 장중 판정도 다음 LoadAssertions에 넘겨야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_manifest(storage, "N1", [])
    current_date = tag_news.datetime.now(tag_news._KST).date().isoformat()
    article = _article("a-minute", published_at=f"{current_date}T08:00:00+09:00")
    mirror_key = _write_mirror(storage, article, tagged_at=f"{current_date}T08:01:00+09:00")

    assert tag_news.run(storage, "R1", input_run_id="N1",
                        complete_fn=_fake_complete([])) == 0

    assert storage.list_keys(mirror_key) == []
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_partitions"][0]["article_ids"] == ["a-minute"]


def _fake_complete(calls: list):
    """사건 1건을 내는 가짜 LLM. 호출을 기록해 '몇 번 불렀나'를 검사할 수 있게 한다."""
    def complete(system: str, user: str) -> str:
        calls.append(user)
        return json.dumps({
            "doc_class": "EVENT",
            "events": [{
                "event_type_code": "COMPANY.CONTRACT.SIGNING",
                "predicate_code": "SIGN",
                "arguments": [{"role_code": "SUPPLIER", "text": "삼성전자"}],
                "confidence": 0.9,
            }],
        }, ensure_ascii=False)
    return complete


def test_tags_canonical_news_into_feature_zone(tmp_path):
    """배선의 본체 — canonical 기사가 실제로 태깅돼 feature 존에 남는다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    calls: list = []

    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(calls)) == 0

    rows = _read_feature(storage, "ko", "2026-07-01")
    assert len(rows) == 1
    assert rows[0]["article_id"] == "a1"
    assert rows[0]["status"] == "ok"
    assert rows[0]["doc_class"] == "EVENT"
    assertions = json.loads(rows[0]["assertions"])
    assert len(assertions) == 1
    assert assertions[0]["event_type_code"] == "COMPANY.CONTRACT.SIGNING"
    # entity_id 는 여기서 안 채운다 — 해소는 로더(ALPHA-190) 소관이고 text 가 그 입력이다.
    assert assertions[0]["arguments"][0]["entity_id"] is None
    assert assertions[0]["arguments"][0]["text"] == "삼성전자"
    assert len(calls) == 1


def test_already_tagged_article_is_not_retagged(tmp_path):
    """재실행이 LLM 을 다시 부르면 돈이 나가고 값이 흔들려 PIT 재현이 깨진다 — 부르면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    first: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(first)) == 0
    assert len(first) == 1

    second: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(second)) == 0
    assert second == []  # 한 번도 안 불렀다
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 1
    # 원장 봉투(ALPHA-181): 재실행은 아무것도 **재판정하지 않았다** — 건너뛴 기사를 산출로
    # 세면 옛 실패(llm_unparseable 같은 비재시도 상태)가 실패 카운터 없이 산출로 뒤집힌다.
    # 산출·유실은 같은 스코프에서 와야 한다. 0건 → UNKNOWN 이 정직한 결과다.
    keys = [k for k in storage.list_keys("operations_archive/") if "run_id=R2/" in k]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["ops"] == {"records_out": 0, "failed_records": 0}


def test_tagger_version_change_forces_retag(tmp_path):
    """버전이 다르면 '다른 태거의 판정'이라 새로 만들어야 한다 — 안 그러면 옛 판정이 굳는다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete([])) == 0

    # 옛 버전으로 태깅된 행을 흉내 — 현재 태거가 그걸 갱신해야 한다.
    rows = _read_feature(storage, "ko", "2026-07-01")
    stale = dict(rows[0], tagger_version="tagging-v0")
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pa.schema([(c, pa.string()) for c in tag_news._FEATURE_COLUMNS])
    table = pa.Table.from_pylist([{c: stale.get(c) for c in tag_news._FEATURE_COLUMNS}], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    prefix = feature_news_assertions_partition("ko", "2026-07-01")
    storage.put_bytes(f"{prefix}/part-00000.parquet", buf.getvalue())

    calls: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(calls)) == 0
    assert len(calls) == 1  # 재태깅됨
    assert _read_feature(storage, "ko", "2026-07-01")[0]["tagger_version"] == TAGGER_VERSION


def test_english_articles_are_not_tagged(tmp_path):
    """프롬프트가 한국어 전용이다 — 영어 기사에 씌우면 품질이 조용히 무너진다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "en", "2026-07-01", [_article("e1", language="en", title="Samsung signs deal")])
    calls: list = []

    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(calls)) == 0
    assert calls == []
    assert _read_feature(storage, "en", "2026-07-01") == []


def test_limit_caps_llm_calls_and_leaves_rest_for_next_run(tmp_path):
    """WHY(ALPHA-1032): 상한 잔여를 완료 처리하면 run별 manifest 범위에서 영구 누락된다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1"), _article("a2"), _article("a3")])

    first: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(first), limit=2) == 1
    assert len(first) == 2
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 2
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_written"] is False

    second: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(second)) == 0
    assert len(second) == 1  # 남은 1건만 — 이미 태깅된 2건은 안 부른다
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 3
    manifest = json.loads(storage.get_bytes(feature_run_manifest_key("news_assertions", "R1")))
    assert manifest["feature_written"] is True
    assert manifest["feature_partitions"][0]["article_ids"] == ["a1", "a2", "a3"]


def test_date_window_prunes_partitions(tmp_path):
    """창은 비용 통제다 — 창 밖 파티션에 LLM 을 쓰면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    _write_canonical(storage, "ko", "2026-07-05", [_article("a2")])

    calls: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(calls),
                        from_date="2026-07-04", to_date="2026-07-06") == 0
    assert len(calls) == 1
    assert _read_feature(storage, "ko", "2026-07-01") == []
    assert len(_read_feature(storage, "ko", "2026-07-05")) == 1


def test_llm_failure_is_isolated_and_recorded_not_silent(tmp_path):
    """한 기사의 LLM 실패가 배치를 죽여도 안 되고, 성공으로 위장돼도 안 된다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1"), _article("a2")])

    def flaky(system: str, user: str) -> str:
        if "a1" in user or "SK하이닉스" in user:
            raise RuntimeError("boom")
        raise RuntimeError("boom")

    assert tag_news.run(storage, "R1", complete_fn=flaky) == 0
    rows = _read_feature(storage, "ko", "2026-07-01")
    # 실패해도 행은 남는다 — status 로 '무슨 일이 있었는지'가 드러나야 재시도 대상을 안다.
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"llm_error"}
    assert all(json.loads(r["assertions"]) == [] for r in rows)


def test_transient_llm_failure_is_retried_next_run(tmp_path):
    """llm_error 는 '이 기사는 이렇다'는 판정이 아니라 '물어보지도 못했다'는 뜻이다.

    이걸 태깅 완료로 캐시하면 네트워크가 한 번 끊긴 기사가 **영구히** 태깅되지 않는다 —
    재태깅 방지가 오히려 커버리지에 구멍을 낸다. 다음 런이 반드시 다시 물어봐야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])

    def boom(system: str, user: str) -> str:
        raise RuntimeError("일시적 네트워크 실패")

    assert tag_news.run(storage, "R1", complete_fn=boom) == 0
    assert _read_feature(storage, "ko", "2026-07-01")[0]["status"] == "llm_error"

    calls: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(calls)) == 0
    assert len(calls) == 1, "일시 실패한 기사를 재시도하지 않았다"
    assert _read_feature(storage, "ko", "2026-07-01")[0]["status"] == "ok"


def test_corrected_article_text_forces_retag(tmp_path):
    """normalize_news 는 같은 URL 재적재에서 최신 fetched_at 의 title·lead 를 대표로 삼아
    **정정을 반영한다**(그게 그 스텝의 의도된 기능이다). 태깅이 버전만 보고 건너뛰면 정정된
    제목인데 옛 텍스트 기반 assertion 이 남아, 정제가 반영한 정정이 태깅에서 조용히 사라진다.
    """
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1", title="삼성전자, 공급계약 체결")])
    first: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(first)) == 0
    assert len(first) == 1

    # 같은 article_id 인데 제목이 정정됐다(normalize_news 가 그렇게 덮어쓴 상황).
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1", title="[정정] 삼성전자, 공급계약 해지")])
    second: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(second)) == 0
    assert len(second) == 1, "텍스트가 정정됐는데 재태깅하지 않았다"
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert len(rows) == 1
    assert rows[0]["title"] == "[정정] 삼성전자, 공급계약 해지"


def test_recollection_without_text_change_does_not_call_llm(tmp_path):
    """지문은 **내용**에만 걸린다 — 재수집으로 fetched_at 만 갱신되고 텍스트가 같으면 같은 답이
    나올 게 뻔한데 LLM 을 다시 부르는 건 돈만 태운다.
    """
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete([])) == 0

    # 같은 텍스트로 canonical 을 다시 썼다(재수집·재정제).
    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])
    calls: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(calls)) == 0
    assert calls == [], "텍스트가 같은데 재태깅했다 — 비용만 든다"


def test_model_level_rejection_is_not_retried_every_run(tmp_path):
    """반면 모델이 답을 했는데 계약을 어긴 건(unparseable) 매 런 재호출하면 돈만 태운다.

    temperature=0 이라 같은 입력엔 같은 답이 온다 — 프롬프트·모델이 바뀌면 tagger_version 이
    올라가 그때 재태깅된다. 그게 재시도의 올바른 트리거다.
    """
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1")])

    def garbage(system: str, user: str) -> str:
        return "JSON 아님"

    assert tag_news.run(storage, "R1", complete_fn=garbage) == 0
    assert _read_feature(storage, "ko", "2026-07-01")[0]["status"] == "llm_unparseable"

    calls: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(calls)) == 0
    assert calls == [], "모델 수준 거절을 매 런 재호출하면 비용만 든다"


def test_articles_without_mentions_are_not_tagged(tmp_path):
    """LLM 비용 게이트(ALPHA-416) — 유니버스 종목이 안 잡힌 기사는 다운스트림(in_universe)이
    어차피 버리므로 기사당 1 LLM 콜을 태우지 않는다. 전체 경제 뉴스 수집 전환 후 기사가
    배수로 늘어도 태깅 비용이 '유니버스 관련 기사' 수준으로 유지되는 근거다. 건너뛴 수는
    조용히 사라지지 않고 로그로 드러난다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [
        _article("a1"),                            # mentions 있음 → 태깅
        _article("a2", mentions="[]"),             # 빈 mentions → 스킵
        _article("a3", mentions=None),             # 결측(구 canonical) → 스킵
        _article("a4", mentions="broken json"),    # 오염 → 스킵(태깅 안 함)
    ])
    calls: list = []

    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(calls)) == 0

    assert len(calls) == 1  # LLM 은 mentions 있는 a1 한 건만
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert [r["article_id"] for r in rows] == ["a1"]

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["articles_skipped_no_mention"] == 3


def test_quality_log_records_what_happened(tmp_path):
    """조용한 0건 금지 — 이 런이 몇 건 읽고 몇 건 태깅했는지가 로그에 남아야 한다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete([])) == 0

    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["articles_read"] == 1
    assert log["articles_tagged"] == 1
    assert log["articles_skipped_already_tagged"] == 0
    assert log["status_counts"] == {"ok": 1}
    # 원장 봉투(ALPHA-181): 산출은 **기사 단위**(ok + 이미 태깅됨)다. rows_written 은 파티션
    # 재작성 행수라 멱등 재실행이면 0 이고(다 돼 있는데 0건으로 보인다) 한 건만 바뀌어도 그
    # 파티션의 과거 행까지 이 런의 산출로 부풀린다. 한도 백로그도 유실이 아니다.
    assert log["ops"] == {"records_out": 1, "failed_records": 0}
    assert log["tagger_version"] == TAGGER_VERSION
    assert log["ontology_version"] == ontology_version()


def test_concurrent_tagging_preserves_all_rows_and_call_count(tmp_path):
    """WHY: LLM 콜을 병렬화(ALPHA-519)해도 결과는 순차와 같아야 한다 — merged 병합을 워커에서
    하면 동시 갱신이 서로를 덮어 행이 유실된다. 병합을 취합 후 메인스레드에 두는 설계를 잠근다:
    20건을 concurrency=8 로 태깅해도 20행 전부 남고 LLM 콜 수가 정확히 20이어야 한다.
    """
    storage = LocalStorage(tmp_path / "lake")
    articles = [_article(f"a{i}") for i in range(20)]
    _write_canonical(storage, "ko", "2026-07-01", articles)

    calls: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(calls), concurrency=8) == 0

    assert len(calls) == 20  # 병렬이어도 콜 수는 대상 수와 정확히 같다(중복·누락 없음)
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert {r["article_id"] for r in rows} == {f"a{i}" for i in range(20)}  # 20행 전부(유실 없음)
    assert {r["status"] for r in rows} == {"ok"}


def test_limit_respected_under_concurrency(tmp_path):
    """WHY: limit 은 선택 단계(순차)에서 확정 tagged + 이번에 고른 수로 판정한다 — 병렬 실행이
    이 상한을 흘리면 비용 가드가 깨진다. 20건·limit=5·concurrency=8 이면 정확히 5건만 태깅되고
    나머지 15는 동일 run 재시도로 남아야 한다(순차판과 동치).
    """
    storage = LocalStorage(tmp_path / "lake")
    articles = [_article(f"a{i}") for i in range(20)]
    _write_canonical(storage, "ko", "2026-07-01", articles)

    first: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(first), limit=5, concurrency=8) == 1
    assert len(first) == 5
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 5

    second: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(second), concurrency=8) == 0
    assert len(second) == 15  # 남은 15건만 — 이미 태깅된 5건은 안 부른다
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 20


def test_calls_run_concurrently_not_serialized(tmp_path):
    """WHY: 병렬화가 실제로 동시 실행돼야 의미가 있다 — ThreadPool 을 순차 루프로 되돌리거나
    workers=1 로 만드는 회귀는 런타임만 되돌리고 결과는 같아 값 검사로는 안 잡힌다(Rule 9).
    n 스레드가 barrier 에 동시 도달해야만 풀리게 해, 순차면 barrier 타임아웃(BrokenBarrier)→
    extract 가 llm_error 로 격리 → status 로 회귀가 드러나게 한다.
    """
    import threading

    storage = LocalStorage(tmp_path / "lake")
    n = 8
    _write_canonical(storage, "ko", "2026-07-01", [_article(f"a{i}") for i in range(n)])
    barrier = threading.Barrier(n, timeout=5)

    def gated(system: str, user: str) -> str:
        barrier.wait()  # n 개가 동시에 도달해야 풀린다 — 순차 실행이면 첫 콜에서 타임아웃
        return json.dumps({"doc_class": "EVENT", "events": [{
            "event_type_code": "COMPANY.CONTRACT.SIGNING", "predicate_code": "SIGN",
            "arguments": [{"role_code": "SUPPLIER", "text": "삼성전자"}], "confidence": 0.9}]},
            ensure_ascii=False)

    assert tag_news.run(storage, "R1", complete_fn=gated, concurrency=n) == 0
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert len(rows) == n
    assert {r["status"] for r in rows} == {"ok"}  # 순차 회귀면 barrier 타임아웃→llm_error


def test_parallel_results_map_to_correct_article(tmp_path):
    """WHY: pool.map 결과를 to_tag 순서에 zip 하므로 매핑이 어긋나면 기사별 결과가 뒤바뀐다.
    응답이 모두 같으면 오매핑을 못 잡는다 — 기사마다 고유 응답(제목 마커를 되읽어 supplier 로)을
    주고, 각 행의 assertion 이 자기 기사(제목 마커 == supplier 텍스트)에서 나왔는지 확인한다.
    """
    import re

    storage = LocalStorage(tmp_path / "lake")
    n = 12
    # 제목에 고유 마커 — 프롬프트(user)의 '제목:' 에 실리므로 fake 가 되읽어 응답에 넣는다.
    _write_canonical(storage, "ko", "2026-07-01",
                     [_article(f"a{i}", title=f"공급계약 MARK{i} 체결") for i in range(n)])

    def echoing(system: str, user: str) -> str:
        m = re.search(r"MARK\d+", user)
        return json.dumps({"doc_class": "EVENT", "events": [{
            "event_type_code": "COMPANY.CONTRACT.SIGNING", "predicate_code": "SIGN",
            "arguments": [{"role_code": "SUPPLIER", "text": m.group(0) if m else "NONE"}],
            "confidence": 0.9}]}, ensure_ascii=False)

    assert tag_news.run(storage, "R1", complete_fn=echoing, concurrency=8) == 0
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert len(rows) == n
    for r in rows:
        title_marker = re.search(r"MARK\d+", r["title"]).group(0)
        supplier = json.loads(r["assertions"])[0]["arguments"][0]["text"]
        assert supplier == title_marker, f"{r['article_id']}: 제목 {title_marker} 인데 supplier {supplier} — 오매핑"


# ── 장중 미러 흡수 (ALPHA-900) ───────────────────────────
# 배치와 1분 레인이 서로의 LLM 장부를 못 봐서 같은 기사를 두 번 유료로 태우던 것을,
# 1분 레인이 같은 파티션의 `minute/` 구역에 남기는 기사당 미러로 잇는다. 여기서 고정하는
# 건 셋이다 — ①미러가 실제로 skip 을 걸어 돈을 안 쓰는가 ②흡수 뒤 지워져 파티션이
# 안 부푸는가 ③**어느 판정이 이기는가**(정렬 순서에 맡기면 정정 기사에서 거꾸로 진다).

def _minute_result(**over) -> dict:
    result = {"doc_class": "EVENT", "status": "ok", "assertions": [], "reasons": [],
              "ontology_version": ontology_version(), "tagger_version": TAGGER_VERSION}
    result.update(over)
    return result


def _write_mirror(storage, article: dict, *, tagged_at: str, language: str = "ko") -> str:
    fingerprint, data = tag_news.mirror_row_bytes(article, _minute_result(), tagged_at)
    key = feature_news_assertions_minute_key(
        language, article["published_at"][:10], article["article_id"], fingerprint)
    storage.put_bytes(key, data)
    return key


def _part_key(language: str, published_date: str) -> str:
    return f"{feature_news_assertions_partition(language, published_date)}/part-00000.parquet"


def test_minute_mirror_skips_the_second_paid_call(tmp_path):
    # 이 배선의 존재 이유다. 미러가 있는데도 LLM 을 부르면 이중 과금이 그대로다 —
    # 그리고 그 실패는 조용하다(원장은 양쪽 다 초록이다).
    storage = LocalStorage(tmp_path)
    article = _article()
    _write_canonical(storage, "ko", "2026-07-01", [article])
    mirror_key = _write_mirror(storage, article, tagged_at="2026-07-01T10:00:00+00:00")

    calls: list = []
    assert tag_news.run(storage, "run-1", complete_fn=_fake_complete(calls)) == 0

    assert calls == []                                   # 한 번도 안 불렀다
    assert storage.list_keys(mirror_key) == []           # 흡수하고 지웠다
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert [r["article_id"] for r in rows] == ["a1"]     # part 파일에 남았다


def test_absorbed_mirror_does_not_pile_up_across_runs(tmp_path):
    # 안 지우면 파티션마다 미러가 무한히 쌓이고 `_read_feature` 가 매 런 그걸 전부 GET
    # 한다 — 이 스텝이 canonical 을 풀스캔하므로 비용이 날짜 수만큼 곱해진다.
    storage = LocalStorage(tmp_path)
    article = _article()
    _write_canonical(storage, "ko", "2026-07-01", [article])
    _write_mirror(storage, article, tagged_at="2026-07-01T10:00:00+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))

    prefix = feature_news_assertions_partition("ko", "2026-07-01")
    assert storage.list_keys(prefix + "/") == [_part_key("ko", "2026-07-01")]


def test_mirror_alone_triggers_compaction_without_new_tagging(tmp_path):
    # 태깅할 게 없는 파티션(전건 skip)에서도 미러는 흡수돼야 한다. `changed` 만 보고
    # 건너뛰면 그런 파티션의 미러가 영영 안 지워져 위 누적이 그대로 난다.
    storage = LocalStorage(tmp_path)
    article = _article()
    _write_canonical(storage, "ko", "2026-07-01", [article])
    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))   # part 파일 확정
    mirror_key = _write_mirror(storage, article, tagged_at="2026-07-02T10:00:00+00:00")

    calls: list = []
    tag_news.run(storage, "run-2", complete_fn=_fake_complete(calls))

    assert calls == []
    assert storage.list_keys(mirror_key) == []


def test_newest_judgement_wins_not_the_key_order(tmp_path):
    # ⭐ 정정 기사의 함정. 배치가 옛 본문으로 내린 판정은 `part-00000.parquet` 에 있고
    #    장중이 정정 본문으로 내린 판정은 `minute/…` 에 있는데, 키 정렬로는 part 파일이
    #    **뒤**라 나중에 읽혀 이긴다. 그러면 지문이 canonical(정정본)과 어긋나 재태깅이
    #    그대로 나고, 미러를 둔 목적이 통째로 사라진다.
    storage = LocalStorage(tmp_path)
    corrected = _article(title="삼성전자, SK하이닉스와 공급계약 해지")
    _write_canonical(storage, "ko", "2026-07-01", [corrected])
    # 배치의 옛 판정: 정정 전 제목에 대한 지문 + 더 이른 시각
    stale = tag_news._feature_row(
        _article(), _minute_result(), "2026-07-01T00:10:00+00:00",
        tag_news._input_fingerprint(_article()))
    storage.put_bytes(_part_key("ko", "2026-07-01"), tag_news._write_parquet_rows([stale]))
    # 장중의 최신 판정: 정정본 지문 + 더 늦은 시각
    _write_mirror(storage, corrected, tagged_at="2026-07-01T10:00:00+00:00")

    calls: list = []
    tag_news.run(storage, "run-1", complete_fn=_fake_complete(calls))

    assert calls == []
    rows = _read_feature(storage, "ko", "2026-07-01")
    assert [r["input_fingerprint"] for r in rows] == [tag_news._input_fingerprint(corrected)]


def test_absorption_count_is_visible(tmp_path):
    # 조용한 0 금지(Rule 12). 이 값이 0 인데 장중 레인이 돌았다면 미러가 안 떨어진
    # 것이고, 그때는 skip 도 안 걸려 이중 과금이 그대로다 — 둘을 나란히 봐야 판별된다.
    storage = LocalStorage(tmp_path)
    article = _article()
    _write_canonical(storage, "ko", "2026-07-01", [article])
    _write_mirror(storage, article, tagged_at="2026-07-01T10:00:00+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))

    log = json.loads(storage.get_bytes(
        storage.list_keys(quality_log_prefix(tag_news.DATASET))[0]))
    assert log["minute_mirrors_absorbed"] == 1
    assert log["articles_skipped_already_tagged"] == 1


def test_correction_arriving_mid_absorption_survives(tmp_path):
    """⭐ 압축 창의 경합. 배치가 미러를 읽은 **뒤** 장중이 같은 기사의 정정 판정을 쓰면,
    배치의 삭제가 그걸 지우면 안 된다 — 배치는 자기가 읽은 것을 지운다고 믿지만, 키가
    `article_id` 뿐이면 실제로 지워지는 건 **아무도 안 읽은 최신 판정**이다. 지문이 키에
    들어가 두 판정이 다른 객체가 되므로 살아남는다.
    """
    storage = _WriteOnReadStorage(tmp_path)
    old = _article()
    corrected = _article(title="삼성전자, SK하이닉스와 공급계약 해지")
    _write_canonical(storage, "ko", "2026-07-01", [old])
    old_key = _write_mirror(storage, old, tagged_at="2026-07-01T10:00:00+00:00")
    # 배치가 old_key 를 읽는 순간 장중이 정정 판정을 쓴다
    storage.on_read = lambda: _write_mirror(
        storage, corrected, tagged_at="2026-07-01T10:00:05+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))

    remaining = storage.list_keys(
        feature_news_assertions_minute_prefix("ko", "2026-07-01"))
    assert old_key not in remaining          # 읽고 병합한 것은 지웠다
    assert len(remaining) == 1               # 정정 판정은 살아남았다
    assert tag_news._read_parquet_rows(storage.get_bytes(remaining[0]))[0][
        "input_fingerprint"] == tag_news._input_fingerprint(corrected)


class _WriteOnReadStorage(LocalStorage):
    """미러를 처음 읽는 순간 콜백을 한 번 부른다 — read→delete 사이의 창을 재현한다."""

    on_read = None

    def get_bytes(self, key):
        data = super().get_bytes(key)
        if self.on_read is not None and "/minute/" in key:
            callback, self.on_read = self.on_read, None
            callback()
        return data


def test_full_scan_compacts_mirrors_outside_any_tagging_window(tmp_path):
    """창을 안 준 런은 canonical 파티션이 없는 날짜의 미러도 흡수한다 (ALPHA-900).

    backfill Consumer 가 오래된 기사를 뒤늦게 추출하면 그 조각이 남는데, 아무도 안
    지우면 `load_assertions` 가 feature 날짜를 풀스캔하며 **매 런 다시 GET 한다**.
    """
    storage = LocalStorage(tmp_path)
    old = _article(published_at="2026-06-01T09:00:00+09:00")
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    mirror_key = _write_mirror(storage, old, tagged_at="2026-06-01T10:00:00+00:00")

    calls: list = []
    tag_news.run(storage, "run-1", complete_fn=_fake_complete(calls))

    assert storage.list_keys(mirror_key) == []
    assert [r["article_id"] for r in _read_feature(storage, "ko", "2026-06-01")] == ["a1"]
    assert len(calls) == 1          # canonical 이 있는 날짜만 태웠다


def test_windowed_run_never_tags_outside_its_window(tmp_path):
    """⭐ 압축이 **태깅 범위를 넓히면 안 된다**. 창 밖 파티션에 미러가 하나 있다는 이유로
    그 날짜가 루프에 들어오면, 거기 미태깅 canonical 기사들이 전부 LLM 대상이 되어
    전역 `limit` 을 먼저 소진한다 — 창이 지키기로 한 비용 범위가 그대로 무너진다.
    같은 이유로 명시 범위 백필이 자기 범위 밖 part 파일을 쓰게 되어서도 안 된다.
    """
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    # 창 밖 날짜: canonical 기사도 있고 미러도 있다
    outside = _article("a-old", published_at="2026-06-01T09:00:00+09:00")
    _write_canonical(storage, "ko", "2026-06-01", [outside])
    _write_mirror(storage, outside, tagged_at="2026-06-01T10:00:00+00:00")
    part_before = storage.list_keys(_part_key("ko", "2026-06-01"))

    calls: list = []
    tag_news.run(storage, "run-1", complete_fn=_fake_complete(calls),
                 from_date="2026-07-01", to_date="2026-07-01")

    assert len(calls) == 1                                   # 창 안 기사 하나만
    assert storage.list_keys(_part_key("ko", "2026-06-01")) == part_before  # 안 썼다


def test_no_mention_article_mirror_is_not_absorbed(tmp_path):
    """⭐ 두 레인의 **대상 집합**이 갈리면 안 된다 (ALPHA-900/416).

    배치는 mentions 없는 기사를 일부러 태깅 대상에서 뺀다(유니버스 무관 — 다운스트림이
    어차피 버린다). 1분 레인에는 아직 그 게이트가 없어서(ALPHA-690) 그런 기사의 미러가
    온다. 그대로 흡수하면 `load_assertions` 는 mentions 를 안 보므로 **배치가 일부러 뺀
    기사**의 assertion 이 DB 에 착지한다.
    """
    storage = LocalStorage(tmp_path)
    no_mention = _article("a-무관", mentions="[]")
    _write_canonical(storage, "ko", "2026-07-01", [_article(), no_mention])
    _write_mirror(storage, no_mention, tagged_at="2026-07-01T10:00:00+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))

    ids = [r["article_id"] for r in _read_feature(storage, "ko", "2026-07-01")]
    assert ids == ["a1"]                    # 무관 기사는 feature 집합 밖이다
    log = json.loads(storage.get_bytes(storage.list_keys(quality_log_prefix(tag_news.DATASET))[0]))
    assert log["minute_mirrors_dropped_no_mention"] == 1   # 조용히 사라지지 않는다


def test_previously_tagged_article_that_lost_mentions_survives_rewrite(tmp_path):
    """⭐ 정정으로 mentions 를 잃은 기사의 옛 유료 판정을 되쓰기가 지우면 안 된다 (ALPHA-982).

    normalize_news 는 같은 URL 재적재에서 최신 본문으로 정정을 반영하는데, 정정 제목에서
    종목명이 빠지면 합성 mentions 가 빈다. 그때 파티션에 미러가 하나라도 있으면 되쓰기가
    돌고, 옛 코드는 part 에 실려 있던 유료 판정까지 mentions 배제식에 쓸어 담아 레이크에서
    영구 소실시켰다(버킷 버저닝 없음) — mentions 가 돌아오면 `_is_current` 가 행을 못 찾아
    다시 유료 호출하는 이중 과금이다. 배제는 **미러로만 온 행**에 한해야 한다.
    """
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    calls: list = []
    tag_news.run(storage, "run-1", complete_fn=_fake_complete(calls))
    assert len(calls) == 1                       # a1 의 판정이 part 에 실렸다

    # 정정으로 a1 이 mentions 를 잃고, 다른 기사의 미러가 있어 되쓰기가 도는 파티션.
    # a1 자신의 더 최신 미러도 온다(게이트 없는 1분 레인, ALPHA-690) — 병합 승자는 미러지만
    # 게이트 배제 기사이므로 착지해야 하는 것은 part 에 실려 있던 판정이다.
    _write_canonical(storage, "ko", "2026-07-01", [_article(mentions="[]")])
    _write_mirror(storage, _article("a-장중만"), tagged_at="2026-07-01T10:00:00+00:00")
    _write_mirror(storage, _article(), tagged_at="2099-01-01T00:00:00+00:00")

    tag_news.run(storage, "run-2", complete_fn=_fake_complete([]))

    rows = {r["article_id"]: r for r in _read_feature(storage, "ko", "2026-07-01")}
    assert sorted(rows) == ["a-장중만", "a1"]    # 옛 유료 판정이 살아남는다
    # 살아남은 것은 part 판정이지 미러 승자가 아니다 — 승자를 실으면 mentions 게이트를
    # 1분 레인 판정이 우회해 두 레인의 대상 집합이 다시 갈린다(ALPHA-900/416).
    assert rows["a1"]["tagged_at"] != "2099-01-01T00:00:00+00:00"
    log_key = [k for k in storage.list_keys(quality_log_prefix(tag_news.DATASET)) if "run-2" in k][0]
    log = json.loads(storage.get_bytes(log_key))
    assert log["minute_mirrors_dropped_no_mention"] == 0   # part 행은 이 축에 안 섞인다


def test_intraday_only_article_mirror_is_kept(tmp_path):
    """반례 — canonical 에 없는 기사(장중만 본 기사)의 미러는 **버리면 안 된다**.

    mentions 를 판정할 근거가 아예 없는 것이지 '무관하다'가 아니다. 버리면 이 PR 이
    노리는 두 번째 값(장중 판정이 `load_assertions` 로 착지)이 통째로 사라진다.
    """
    storage = LocalStorage(tmp_path)
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    intraday_only = _article("a-장중만")
    _write_mirror(storage, intraday_only, tagged_at="2026-07-01T10:00:00+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]))

    ids = sorted(r["article_id"] for r in _read_feature(storage, "ko", "2026-07-01"))
    assert ids == ["a-장중만", "a1"]


def test_windowed_run_absorbs_in_window_mirror_without_canonical(tmp_path):
    """⭐ canonical 이 아직 없는 날짜의 미러도 **창 안이면** 흡수한다 (ALPHA-900).

    기사 정본은 PG 이고 canonical 은 다음 `normalize_news` 에 온다 — 장중만 본 기사의
    발행일이 아직 canonical 에 없을 수 있다. `_partition_dates` 는 canonical 만 열거하니
    그런 날짜는 루프에 안 들어오고, 그러면 미러가 영영 흡수되지 않는다. 소비자는 흡수 전
    미러를 안 읽으므로(ALPHA-900) **그 장중 판정이 DB 에 영영 안 실린다** — 이 티켓이
    노리는 값 하나가 통째로 사라진다.

    명시 기간 복구에서도 미러 날짜를 빼면 canonical보다 먼저 온 장중 판정을 회수하지 못한다.
    """
    storage = LocalStorage(tmp_path)
    # 창 안이지만 canonical 파티션이 없는 날짜 — 장중만 본 기사다
    intraday_only = _article("a-장중만", published_at="2026-07-02T09:00:00+09:00")
    _write_canonical(storage, "ko", "2026-07-01", [_article()])
    mirror_key = _write_mirror(storage, intraday_only, tagged_at="2026-07-02T10:00:00+00:00")

    tag_news.run(storage, "run-1", complete_fn=_fake_complete([]),
                 from_date="2026-07-01", to_date="2026-07-03")

    assert storage.list_keys(mirror_key) == []
    assert [r["article_id"] for r in _read_feature(storage, "ko", "2026-07-02")] == ["a-장중만"]
