"""load_documents 스텝 테스트 — canonical 뉴스 → document (ALPHA-374).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·파라미터를 기록해 '무엇을 어떻게 넣는가'를 검사한다.
(레포의 다른 테스트도 전부 순수라 CI 에 Postgres 가 없다. 실 RDS e2e 는 수동 검증.)

각 테스트는 **왜 그 동작이 중요한지**를 검사한다: 멱등이 깨지면 재실행이 document_id 를 바꿔
그 문서를 참조할 assertion FK(ALPHA-376)가 전부 끊기고, 자연키 결손 행을 넣으면 같은 기사가
매 런 새 행으로 쌓인다.
"""

import io
import json

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_news_articles_partition
from data_pipeline.steps import load_documents

_COLUMNS = ("article_id", "source_vendor", "market", "title", "url", "normalized_url",
            "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions",
            "fetched_at")


def _write_canonical(storage, language: str, date: str, rows: list[dict],
                     part: str = "part-00000") -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_news_articles_partition(language, date)}/{part}.parquet", buf.getvalue())


def _article(article_id: str, **over) -> dict:
    row = {"article_id": article_id, "source_vendor": "bigkinds", "market": "KR",
           "title": "삼성전자 신규 수주", "url": f"https://news.example/{article_id}",
           "published_at": "2026-07-15T09:00:00+09:00", "publisher": "매일경제",
           "fetched_at": "2026-07-15T01:00:00+00:00"}
    row.update(over)
    return row


class _FakeCursor:
    """ON CONFLICT DO NOTHING 시맨틱 흉내 — 자연키(existing)가 이미 있으면 rowcount 0."""

    def __init__(self, log: list, existing: list[tuple]):
        self._log, self._existing = log, existing
        self.rowcount = 1

    def execute(self, sql, params=None):
        self._log.append((" ".join(sql.split()), params))
        if sql.lstrip().upper().startswith("INSERT INTO DOCUMENT"):
            self.rowcount = 0 if (params[1], params[2]) in self._existing else 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, existing: list[tuple] | None = None):
        self.log: list = []
        self._existing = existing or []

    def cursor(self):
        return _FakeCursor(self.log, self._existing)


def _fake_connect(conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    return _c


def _db() -> DbConfig:
    return DbConfig(password="x")


def _inserts(conn) -> list:
    return [p for sql, p in conn.log if sql.upper().startswith("INSERT INTO DOCUMENT ")]


def test_new_article_becomes_a_news_document_row(tmp_path, monkeypatch):
    """document 는 assertion FK 의 뿌리다 — 자연키(source_code, source_document_id)와
    시간 축(published_at·available_at)이 canonical 그대로 실려야 다운스트림이 문서를 찾는다.
    """
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0

    [(doc_id, source_code, source_document_id, title, language, published_at,
      available_at, source_uri)] = _inserts(conn)
    assert doc_id.startswith("doc_")  # ADR-0027 접두사+ULID
    assert (source_code, source_document_id) == ("bigkinds", "a1")
    assert language == "ko"
    assert published_at == "2026-07-15T09:00:00+09:00"
    assert available_at == "2026-07-15T01:00:00+00:00"  # fetched_at = 우리가 얻은 시각
    assert source_uri == "https://news.example/a1"
    assert title == "삼성전자 신규 수주"


def test_existing_article_is_not_recreated(tmp_path, monkeypatch):
    """멱등 — 재실행이 기존 행을 덮거나 새 document_id 로 갈아치우면 그 문서를 참조할
    assertion FK 가 전부 끊긴다(ALPHA-376). ON CONFLICT DO NOTHING(rowcount 0)이면 created
    가 아니라 already 로 세어져야 로그가 거짓말하지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(existing=[("bigkinds", "a1")])
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["created"] == 0
    assert log["already_present"] == 1
    assert log["created_rows_sample"] == []


def test_missing_identity_is_skipped_not_inserted(tmp_path, monkeypatch):
    """자연키(source_vendor·article_id) 결손 행을 넣으면 멱등의 근거가 사라져 같은 기사가
    매 런 새 행으로 쌓이거나 NOT NULL 위반으로 런 전체가 롤백된다 — 세고 뺀다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1"),
        _article("", source_vendor="bigkinds"),      # article_id 결손
        _article("a3", source_vendor=None),           # source_vendor 결손
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    assert [p[2] for p in _inserts(conn)] == ["a1"], "자연키 결손 행이 적재됐다"


def test_available_at_falls_back_to_published_at_or_skips(tmp_path, monkeypatch):
    """available_at 은 NOT NULL — fetched_at 결손이면 published_at 이 대신하고, 둘 다 없으면
    시간 축이 없는 문서라 적재 불가(넣으면 즉시 제약 위반으로 런 전체 롤백)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1", fetched_at=None),                          # → published_at 폴백
        _article("a2", fetched_at=None, published_at=None),       # → skip
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    [(_, _, article_id, _, _, _, available_at, _)] = _inserts(conn)
    assert article_id == "a1"
    assert available_at == "2026-07-15T09:00:00+09:00"
    # 결손이 로그에 세어져야 운영이 믿을 수 있다(Rule 12) — skip 은 침묵이 아니다.
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["skipped_no_available_at"] == 1


def test_window_prunes_partitions(tmp_path, monkeypatch):
    """--from/--to 는 published_date 파티션 프루닝이다 — 창 밖 파티션을 읽으면 백필/증분
    분리가 안 되고 스캔 비용이 창에 비례하지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-14", [_article("a-old")])
    _write_canonical(storage, "ko", "2026-07-15", [_article("a-in")])
    _write_canonical(storage, "ko", "2026-07-16", [_article("a-future")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db(),
                              from_date="2026-07-15", to_date="2026-07-15") == 0
    assert [p[2] for p in _inserts(conn)] == ["a-in"]


def test_same_article_in_two_partitions_is_created_once(tmp_path, monkeypatch):
    """같은 (source_vendor, article_id)가 두 파티션에 오면(같은 URL 재게시) 자연키가 하나라
    문서도 하나여야 한다 — 두 번 넣으면 uq_document_source 위반으로 런 전체가 롤백된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-14", [_article("a1")])
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    assert len(_inserts(conn)) == 1


def test_both_languages_are_loaded(tmp_path, monkeypatch):
    """en 문서도 assertion 이 생기는 순간 FK 대상이다 — ko 만 실으면 영어 뉴스 assertion
    적재(후속)가 열릴 때 FK 뿌리가 없다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a-ko")])
    _write_canonical(storage, "en", "2026-07-15",
                     [_article("a-en", source_vendor="fmp", title="Samsung wins order")])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    by_article = {p[2]: p[4] for p in _inserts(conn)}  # article_id → language_code
    assert by_article == {"a-ko": "ko", "a-en": "en"}


def test_run_log_records_what_happened(tmp_path, monkeypatch):
    """조용한 0건 금지 — 몇 건 읽고 몇 건 걸렀고 몇 건 만들었는지가 남아야 한다(Rule 12)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [
        _article("a1"),
        _article("", source_vendor="bigkinds"),
    ])
    conn = _FakeConn()
    monkeypatch.setattr(load_documents, "connect", _fake_connect(conn))

    assert load_documents.run(storage, "R1", db=_db()) == 0
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["articles_read"] == 2
    assert log["skipped_missing_identity"] == 1
    assert log["created"] == 1
    assert log["created_rows_sample"][0]["source_document_id"] == "a1"


def test_db_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """DB 가 터지면 트레이스백으로 죽는 게 아니라 **비0 종료 + 로그**로 드러나야 한다.
    롤백된 런의 created 를 로그가 만들었다고 주장하면 다음 사람이 DB 에 있다고 믿는다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage, "ko", "2026-07-15", [_article("a1")])

    from contextlib import contextmanager

    @contextmanager
    def _boom(config):
        raise RuntimeError("DB 연결 끊김")
        yield  # pragma: no cover

    monkeypatch.setattr(load_documents, "connect", _boom)

    assert load_documents.run(storage, "R1", db=_db()) == 1, "실패가 성공으로 위장됐다"
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1, "실패했는데 로그가 안 남았다"
    log = json.loads(storage.get_bytes(keys[0]).decode("utf-8"))
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["load_error"]
    assert log["created"] == 0, "롤백됐는데 만들었다고 로그가 주장한다"
    assert log["created_rows_sample"] == []
