"""assemble_events 스텝 테스트 — 엔진 추출 체인 이식 (ALPHA-412).

실 DB·실 LLM 없이 돈다 — 가짜 complete_fn 이 분류를, 가짜 커넥션이 SQL 을 기록한다.

각 테스트가 지키는 WHY: 결정적 ID 파생이 엔진 산식에서 어긋나면 이행기(엔진이 아직
자체 조립을 하는 동안) 같은 이벤트가 두 계보로 갈리고, 자연키 브리지가 깨지면 로더
선적재 행과 FK 가 끊기며, 이미 정규화된 기사에 LLM 을 다시 태우면 비용이 이중이다.
"""

import io
import json

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, canonical_news_articles_partition
from data_pipeline.steps import assemble_events
from data_pipeline.steps.assemble_events import _stable_id

_COLUMNS = ("article_id", "source_vendor", "market", "title", "url", "normalized_url",
            "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions",
            "fetched_at")


def _write_news(storage, language: str, date: str, rows: list[dict]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    table = pa.Table.from_pylist([{c: r.get(c) for c in _COLUMNS} for r in rows], schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    storage.put_bytes(
        f"{canonical_news_articles_partition(language, date)}/part-00000.parquet", buf.getvalue())


def _article(article_id: str, ticker: str = "005930", **over) -> dict:
    row = {"article_id": article_id, "source_vendor": "bigkinds", "title": "삼성전자 실적 발표",
           "published_at": "2026-07-15T09:00:00+09:00", "publisher": "매일경제",
           "mentions": json.dumps([{"market": "KR", "ticker": ticker}])}
    row.update(over)
    return row


def _classified(article_id: str, ticker: str = "005930") -> str:
    return json.dumps({"items": [{
        "id": article_id, "is_event": True,
        "event_type_code": "COMPANY.EARNINGS.RESULT_RELEASE", "predicate_code": "REPORT",
        "primary_ticker": ticker, "lifecycle_stage": "", "confidence": 0.9,
    }]})


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list = []

    def execute(self, sql, params=None):
        conn = self._conn
        flat = " ".join(sql.split())
        conn.log.append((flat, params))
        upper = flat.upper()
        if upper.startswith("SELECT TICKER, INSTRUMENT_ID"):
            self._rows = list(conn.instruments)
        elif upper.startswith("SELECT D.SOURCE_DOCUMENT_ID FROM DOCUMENT D"):
            wanted = set(params[0])
            self._rows = [(a,) for a in conn.assembled_articles if a in wanted]
        elif upper.startswith("SELECT SOURCE_DOCUMENT_ID, DOCUMENT_ID"):
            # 자연키 해소 — 로더 선적재 행이 있으면 그 ID, 없으면 방금 넣은 후보 해시
            # (조회된 source_code 기준 — 벤더별 자연키).
            source_code, wanted = params[0], set(params[1])
            self._rows = [(a, conn.doc_overrides.get(a, _stable_id("doc", source_code, a)))
                          for a in wanted]
        elif upper.startswith("SELECT DOCUMENT_ID, EVENT_TYPE_CODE"):
            self._rows = list(conn.assertion_rows)
        elif upper.startswith("SELECT THREAD_ID, COUNT"):
            self._rows = [(t, n) for t, n in conn.prior_thread_counts.items()]

    def executemany(self, sql, rows):
        self._conn.batches.append((" ".join(sql.split()), list(rows)))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, instruments=None, assembled_articles=(), doc_overrides=None,
                 assertion_rows=None, prior_thread_counts=None):
        self.log: list = []
        self.batches: list = []
        self.instruments = instruments or [("005930", "inst_SAMSUNG"), ("000660", "inst_HYNIX"),
                                           ("999999", "inst_OTHER")]
        self.assembled_articles = set(assembled_articles)
        self.doc_overrides = doc_overrides or {}
        self.assertion_rows = assertion_rows
        self.prior_thread_counts = prior_thread_counts or {}

    def cursor(self):
        cur = _FakeCursor(self)
        return cur


def _setup(monkeypatch, conn):
    from contextlib import contextmanager

    @contextmanager
    def _c(config):
        yield conn

    monkeypatch.setattr(assemble_events, "connect", _c)


def _batch(conn, table: str) -> list:
    out = []
    for sql, rows in conn.batches:
        if sql.upper().startswith(f"INSERT INTO {table.upper()} "):
            out.extend(rows)
    return out


def _db() -> DbConfig:
    return DbConfig(password="x")


def _log(storage) -> dict:
    keys = [k for k in storage.list_keys("operations_archive/") if k.endswith("log.json")]
    assert len(keys) == 1
    return json.loads(storage.get_bytes(keys[0]).decode("utf-8"))


def _assertion_rows_for(article_id: str):
    doc_id = _stable_id("doc", "bigkinds", article_id)
    asrt_id = _stable_id("asrt", doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT")
    return [(doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT", asrt_id)]


def test_event_lineage_matches_engine_derivation(tmp_path, monkeypatch):
    """분류 1건 → document/assertion/source_event/argument/evidence/thread 전체 계보가
    엔진과 같은 결정적 ID 산식으로 서야 이행기 멱등 수렴이 성립한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)
    calls = []

    def complete_fn(system, user):
        calls.append(json.loads(user))
        return _classified("a1")

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT")
    evt_id = _stable_id("evt", asrt_id, "inst_SAMSUNG")
    [se] = _batch(conn, "source_event")
    assert se[0] == evt_id and se[1] == "NEWS" and se[3] == "2026-07-15"
    [arg] = _batch(conn, "event_argument")
    assert (arg[0], arg[2]) == (evt_id, "inst_SAMSUNG")
    [ev] = _batch(conn, "event_evidence")
    assert ev[1] == evt_id and ev[3] == "TITLE"
    # threading: 첫 이벤트는 FIRST_IN_THREAD, thread_id 도 엔진 산식.
    [link] = _batch(conn, "event_thread_link")
    thread_id = _stable_id("thr", "COMPANY.EARNINGS.RESULT_RELEASE||inst_SAMSUNG")
    assert (link[1], link[3]) == (thread_id, "FIRST_IN_THREAD")
    # 분류 입력의 tickers 는 entity_index 교집합이어야 한다(엔진 규칙).
    assert calls[0]["items"][0]["tickers"] == ["005930"]
    log = _log(storage)
    assert log["events_created"] == 1 and log["kodex_threaded"] == 1


def test_already_assembled_articles_skip_llm(tmp_path, monkeypatch):
    """이미 **조립된**(document_entity 자국) 기사는 LLM 을 다시 태우지 않는다 — 증분·비용
    통제. 판정 축은 document 존재가 아니다: LoadDocuments 가 선행하는 SFN 에선 그 기준이면
    todo 가 항상 비어 이벤트가 영영 안 생긴다(Codex #137 P1). complete_fn 이 불리면 즉시 깨진다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assembled_articles=["a1"])
    _setup(monkeypatch, conn)

    def complete_fn(system, user):
        raise AssertionError("이미 정규화된 기사에 LLM 이 호출됐다")

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert _log(storage)["already_normalized"] == 1
    assert _batch(conn, "source_event") == []


def test_lineage_lands_on_loader_written_document(tmp_path, monkeypatch):
    """load-documents 가 먼저 적재한 기사(ULID) — 계보는 그 행의 ID 에서 파생돼야
    한다(자연키 브리지, ALPHA-409 와 동일 계약)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    loader_doc = "doc_01LOADERULID"
    loader_asrt = "asrt_01LOADERASRT"
    conn = _FakeConn(
        doc_overrides={"a1": loader_doc},
        assertion_rows=[(loader_doc, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT", loader_asrt)],
    )
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    [asrt_arg] = _batch(conn, "assertion_argument")
    assert asrt_arg[0] == loader_asrt
    [se] = _batch(conn, "source_event")
    assert se[0] == _stable_id("evt", loader_asrt, "inst_SAMSUNG")


def test_fmp_article_documents_keep_their_vendor(tmp_path, monkeypatch):
    """en/fmp 기사가 분류돼도 document 자연키는 (fmp, article_id) — bigkinds 로 하드코딩하면
    LoadDocuments 가 fmp 로 적재한 행과 어긋난 중복 document 가 생긴다(Codex #137 P1)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "en", "2026-07-15", [
        _article("a-en", source_vendor="fmp", title="Samsung earnings")])
    doc_id = _stable_id("doc", "fmp", "a-en")
    asrt_id = _stable_id("asrt", doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT")
    conn = _FakeConn(assertion_rows=[(doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT", asrt_id)])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a-en"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [doc] = _batch(conn, "document")
    assert (doc[2], doc[3]) == ("fmp", "a-en")
    assert doc[5] == "en"  # language_code 도 파티션 축을 따른다(ko 하드코딩 금지)
    resolutions = [p for sql, p in conn.log
                   if sql.upper().startswith("SELECT SOURCE_DOCUMENT_ID, DOCUMENT_ID")]
    assert resolutions == [("fmp", ["a-en"])]


def test_thread_timestamps_stay_monotonic_on_conflict(tmp_path, monkeypatch):
    """백필이 기존 스레드보다 오래된 이벤트를 넣어도 opened_at/last_state_at 이 역행하면
    안 된다(ck_event_thread_time 위반 → 백필 전체 롤백) — LEAST/GREATEST 병합이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [thread_sql] = [sql for sql, _rows in conn.batches
                    if sql.upper().startswith("INSERT INTO EVENT_THREAD ")]
    assert "LEAST(event_thread.opened_at" in thread_sql
    assert "GREATEST(event_thread.last_state_at" in thread_sql


def test_non_kodex_event_is_created_but_not_threaded(tmp_path, monkeypatch):
    """유니버스(entity_index)엔 있지만 KODEX 구성종목이 아닌 이벤트 — 계보는 서되
    threading 은 안 탄다(엔진 select_kodex_events 규칙)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", ticker="999999")])
    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT")
    conn = _FakeConn(assertion_rows=[(doc_id, "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT", asrt_id)])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1", ticker="999999"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert len(_batch(conn, "source_event")) == 1
    assert _batch(conn, "event_thread_link") == []
    log = _log(storage)
    assert log["events_created"] == 1 and log["kodex_threaded"] == 0


def test_ticker_outside_article_mentions_is_rejected(tmp_path, monkeypatch):
    """모델이 기사 tickers 밖의(그러나 유니버스엔 있는) 종목을 반환하면 거부돼야 한다 —
    통과시키면 엉뚱한 회사에 이벤트·스레드가 선다(Codex #137 P1, 프롬프트 규칙의 코드 강제)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", ticker="005930")])
    conn = _FakeConn()
    _setup(monkeypatch, conn)

    # 기사 mentions 는 005930 뿐인데 모델이 000660(유니버스 내 타사)을 반환.
    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1", ticker="000660"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert _batch(conn, "source_event") == []
    assert _log(storage)["events_created"] == 0


def test_llm_failure_is_recorded_not_a_silent_traceback(tmp_path, monkeypatch):
    """분류 LLM 이 3회 재시도 후에도 죽으면 비0 종료 + 로그 — 조용한 0건 성공 금지."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    _setup(monkeypatch, conn)

    def complete_fn(system, user):
        raise RuntimeError("LLM down")

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 1
    log = _log(storage)
    assert log["exit_code"] == 1
    assert log["failures"][0]["reasons"] == ["assemble_error"]
    assert log["events_created"] == 0
