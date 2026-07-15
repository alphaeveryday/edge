"""tag_news 스텝 테스트 — canonical→feature 배선 + 재태깅 방지 + 비용 상한 (ALPHA-365).

실 LLM 은 부르지 않는다 — complete_fn 을 주입받는 설계라 가짜로 돈다. 각 테스트는 '왜 그
동작이 중요한지'를 검사한다: 재태깅 방지는 돈과 PIT 재현이 걸린 계약이고, 언어 게이트는
한국어 전용 프롬프트가 영어 기사에 조용히 씌워지는 걸 막는다.
"""

import json

from data_pipeline.lake import (
    LocalStorage,
    canonical_news_articles_partition,
    feature_news_assertions_partition,
)
from data_pipeline.steps import tag_news
from data_pipeline.tagging.extract import TAGGER_VERSION
from data_pipeline.tagging.ontology import ontology_version

_CANONICAL_COLUMNS = ("article_id", "published_at", "title", "lead_text", "language")


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
    row = {"article_id": article_id, "published_at": "2026-07-01T09:00:00+00:00",
           "title": "삼성전자, SK하이닉스와 공급계약 체결", "lead_text": "리드", "language": "ko"}
    row.update(over)
    return row


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
    """비용 상한 — 상한에 걸린 기사는 버려지는 게 아니라 다음 런에서 이어서 태깅돼야 한다."""
    storage = LocalStorage(tmp_path / "lake")

    _write_canonical(storage, "ko", "2026-07-01", [_article("a1"), _article("a2"), _article("a3")])

    first: list = []
    assert tag_news.run(storage, "R1", complete_fn=_fake_complete(first), limit=2) == 0
    assert len(first) == 2
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 2

    second: list = []
    assert tag_news.run(storage, "R2", complete_fn=_fake_complete(second)) == 0
    assert len(second) == 1  # 남은 1건만 — 이미 태깅된 2건은 안 부른다
    assert len(_read_feature(storage, "ko", "2026-07-01")) == 3


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
    assert log["tagger_version"] == TAGGER_VERSION
    assert log["ontology_version"] == ontology_version()
