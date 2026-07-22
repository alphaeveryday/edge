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


# 단일 identity_role(=required_roles[0]=ISSUER) 타입 — edge 의 단일 entity 추출로 identity 를
# 채울 수 있어 thread 가 선다. (EARNINGS.RESULT_RELEASE 는 identity=[ISSUER, REPORTING_PERIOD]
# 라 edge 가 REPORTING_PERIOD 를 못 채워 UNKNOWN 이 되므로, thread 형성 테스트엔 부적합.)
_ETYPE = "COMPANY.CAPITAL.DIVIDEND_DECISION"
_PRED = "DECLARE"
_IDENTITY_ROLE = "ISSUER"


def _thread_key(entity_id: str, event_type_code: str = _ETYPE, role: str = _IDENTITY_ROLE) -> str:
    """계약 thread_key(단일 identity 역할) — assemble_events._thread_key 와 같은 형식."""
    return f"event_type_id={event_type_code}||required:{role}={entity_id}"


def _article(article_id: str, ticker: str = "005930", **over) -> dict:
    row = {"article_id": article_id, "source_vendor": "bigkinds", "title": "삼성전자 배당 결정",
           "published_at": "2026-07-15T09:00:00+09:00", "publisher": "매일경제",
           "mentions": json.dumps([{"market": "KR", "ticker": ticker}])}
    row.update(over)
    return row


def _classified(article_id: str, ticker: str = "005930") -> str:
    return json.dumps({"items": [{
        "id": article_id, "is_event": True,
        "event_type_code": _ETYPE, "predicate_code": _PRED,
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
        elif upper.startswith("SELECT SE.SOURCE_EVENT_ID, SE.EVENT_TYPE_CODE, SE.AVAILABLE_AT, EA.ROLE_CODE"):
            # 미연결 이벤트 = 사전 존재분(conn.unthreaded_events) + 이번 run 이 방금 insert 한
            # source_event(link insert 는 아직이라 전부 미연결). 이벤트마다 event_argument 전
            # 역할 행을 (sid, type, available_at, role_code, entity_id) 로 편다(계약 thread_key
            # 가 identity 역할 전체 값을 필요로 함, ALPHA-457).
            ea_by_se: dict = {}
            se_meta: dict = {}
            for bsql, brows in conn.batches:
                u = bsql.upper()
                if u.startswith("INSERT INTO EVENT_ARGUMENT "):
                    for r in brows:
                        ea_by_se.setdefault(r[0], []).append((r[1], r[2]))  # (role_code, entity_id)
                elif u.startswith("INSERT INTO SOURCE_EVENT "):
                    for r in brows:
                        se_meta[r[0]] = (r[2], r[6])  # (event_type_code, available_at)
            out = list(conn.unthreaded_events)
            for sid, (etype, avail) in se_meta.items():
                for role_code, entity_id in ea_by_se.get(sid, []):
                    out.append((sid, etype, avail, role_code, entity_id))
            self._rows = out
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
                 assertion_rows=None, prior_thread_counts=None, unthreaded_events=()):
        self.log: list = []
        self.batches: list = []
        self.instruments = instruments or [("005930", "inst_SAMSUNG"), ("000660", "inst_HYNIX"),
                                           ("999999", "inst_OTHER")]
        self.assembled_articles = set(assembled_articles)
        self.doc_overrides = doc_overrides or {}
        self.assertion_rows = assertion_rows
        self.prior_thread_counts = prior_thread_counts or {}
        # 사전 존재하는 미연결 이벤트 행: (source_event_id, event_type_code, available_at,
        # role_code, entity_id) — 역할당 한 행(멀티역할이면 같은 sid 로 여러 행).
        self.unthreaded_events = list(unthreaded_events)

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
    asrt_id = _stable_id("asrt", doc_id, _ETYPE, _PRED)
    return [(doc_id, _ETYPE, _PRED, asrt_id)]


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
    asrt_id = _stable_id("asrt", doc_id, _ETYPE, _PRED)
    evt_id = _stable_id("evt", asrt_id, "inst_SAMSUNG")
    [se] = _batch(conn, "source_event")
    assert se[0] == evt_id and se[1] == "NEWS" and se[3] == "2026-07-15"
    [arg] = _batch(conn, "event_argument")
    assert (arg[0], arg[2]) == (evt_id, "inst_SAMSUNG")
    [ev] = _batch(conn, "event_evidence")
    assert ev[1] == evt_id and ev[3] == "TITLE"
    # threading: 첫 이벤트는 FIRST_IN_THREAD, thread_id 는 identity_roles 기반 계약 키.
    [link] = _batch(conn, "event_thread_link")
    thread_id = _stable_id("thr", _thread_key("inst_SAMSUNG"))
    assert (link[1], link[3]) == (thread_id, "FIRST_IN_THREAD")
    # 분류 입력의 tickers 는 entity_index 교집합이어야 한다(엔진 규칙).
    assert calls[0]["items"][0]["tickers"] == ["005930"]
    log = _log(storage)
    assert log["events_created"] == 1 and log["threaded"] == 1


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
        assertion_rows=[(loader_doc, _ETYPE, _PRED, loader_asrt)],
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
    asrt_id = _stable_id("asrt", doc_id, _ETYPE, _PRED)
    conn = _FakeConn(assertion_rows=[(doc_id, _ETYPE, _PRED, asrt_id)])
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


def test_in_universe_non_kodex_event_is_threaded(tmp_path, monkeypatch):
    """유니버스(entity_index=holdings 파생 마스터) 구성종목이면 과거 KODEX 9종이 아니어도
    threading 을 탄다(ALPHA-468). WHY: 다중 ETF 설명(ALPHA-465·467)은 KODEX 반도체뿐 아니라
    발화한 모든 ETF 구성종목을 설명하는데, threading 이 KODEX 한정이면 그 종목들이 계보·
    신규성(thread_id·novelty) 없이 설명에 들어가 검증이 반쪽이 된다. entity 해소가 이미
    유니버스 필터라, 그 위 KODEX 협소화는 제거돼야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", ticker="999999")])
    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, _ETYPE, _PRED)
    conn = _FakeConn(assertion_rows=[(doc_id, _ETYPE, _PRED, asrt_id)])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1", ticker="999999"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert len(_batch(conn, "source_event")) == 1
    # 999999 → inst_OTHER 도 계보가 선다(과거엔 event_thread_link == [] 였다).
    [link] = _batch(conn, "event_thread_link")
    thread_id = _stable_id("thr", _thread_key("inst_OTHER"))
    assert (link[1], link[3]) == (thread_id, "FIRST_IN_THREAD")
    log = _log(storage)
    assert log["events_created"] == 1 and log["threaded"] == 1


def test_rerun_threads_preexisting_unthreaded_event(tmp_path, monkeypatch):
    """배포 전 KODEX-only 로 조립돼 미연결로 남은 과거 이벤트가, 재실행 때 새 분류가 없어도
    threading 된다(ALPHA-468 self-heal, edge-review). WHY: 분류는 assembled_source_ids 로
    이미 조립된 기사를 건너뛰므로 created 가 비지만, threading 대상이 created 가 아니라 '그
    날짜의 미연결 전체'라 과거 미연결분이 채워진다. 안 그러면 그 이벤트는 영영 계보 없이
    남고 prior_count 가 못 세 같은 스레드 새 이벤트의 novelty 까지 오염된다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    stale = ("evt_stale", _ETYPE, "2026-07-15T09:00:00+09:00", _IDENTITY_ROLE, "inst_OTHER")
    # a1 은 이미 조립됨 → 재분류 안 함(created 비어야 self-heal 만 검증됨).
    conn = _FakeConn(assembled_articles=["a1"], unthreaded_events=[stale])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert _batch(conn, "source_event") == []  # 신규 조립 없음
    [link] = _batch(conn, "event_thread_link")
    thread_id = _stable_id("thr", _thread_key("inst_OTHER"))
    assert (link[0], link[1], link[3]) == ("evt_stale", thread_id, "FIRST_IN_THREAD")
    log = _log(storage)
    assert log["events_created"] == 0 and log["threaded"] == 1


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


def _multi_role_event(source_event_id: str, role_values: dict, available_at="2026-07-15T09:00:00+09:00"):
    return {"source_event_id": source_event_id, "event_type_code": "COMPANY.CONTRACT.SIGNING",
            "available_at": available_at, "role_values": role_values}


def test_contract_signing_splits_thread_by_customer(monkeypatch):
    """계약 불변식: CONTRACT.SIGNING identity=SUPPLIER·CUSTOMER·CONTRACT_OBJECT 라, 같은
    공급사라도 CUSTOMER 가 다르면 **다른 thread** 다. 엔티티 하나로 키를 만들던 옛 구현은
    이 둘을 한 스레드로 뭉갰다(신규 계약이 기존 계약의 FOLLOW_UP 으로 오독). WHY: novelty·
    prior_event_count 가 이 키 위에서 나오므로, 키가 CUSTOMER 를 안 보면 사건 계보가 틀린다.

    라이브 파이프라인에선 edge 가 CUSTOMER·CONTRACT_OBJECT 를 추출하지 않아 이 타입은 UNKNOWN
    이 되지만(아래 테스트), thread_key 로직 자체는 identity 가 채워졌을 때 올바로 갈라야 한다 —
    그래서 event_argument 를 손으로 심어 thread_events 를 직접 검증한다."""
    conn = _FakeConn()
    ev_a = _multi_role_event("evt_a", {"SUPPLIER": "inst_SUP", "CUSTOMER": "inst_CUST1",
                                       "CONTRACT_OBJECT": "concept_battery"})
    ev_b = _multi_role_event("evt_b", {"SUPPLIER": "inst_SUP", "CUSTOMER": "inst_CUST2",
                                       "CONTRACT_OBJECT": "concept_battery"})
    assemble_events.thread_events(conn, [ev_a, ev_b])

    links = {r[0]: r for r in _batch(conn, "event_thread_link")}
    assert links["evt_a"][1] != links["evt_b"][1]         # 다른 thread_id
    assert links["evt_a"][3] == "FIRST_IN_THREAD" and links["evt_b"][3] == "FIRST_IN_THREAD"
    # 같은 공급사·같은 계약대상, CUSTOMER 만 다름 → 키가 CUSTOMER 를 반영한다(역할 순서=identity_roles).
    key_a = ("event_type_id=COMPANY.CONTRACT.SIGNING||required:SUPPLIER=inst_SUP"
             "||required:CUSTOMER=inst_CUST1||required:CONTRACT_OBJECT=concept_battery")
    assert links["evt_a"][1] == _stable_id("thr", key_a)
    assert len(_batch(conn, "event_thread")) == 2


def test_missing_identity_role_emits_unknown(monkeypatch):
    """identity 역할을 못 채우면(edge 는 CONTRACT.SIGNING 의 CUSTOMER·CONTRACT_OBJECT 를
    추출하지 않는다) synthetic thread 를 만들지 않고 novelty=UNKNOWN·thread_id=NULL 로 남긴다
    (계약 불변식 5 missing_identity_policy=EMIT_UNKNOWN_LINK_ONLY). WHY: 없는 identity 로 억지
    스레드를 세우면 서로 다른 계약이 다시 한 스레드로 뭉개져 옛 버그가 재발한다."""
    conn = _FakeConn()
    ev = _multi_role_event("evt_x", {"SUPPLIER": "inst_SUP"})  # CUSTOMER·CONTRACT_OBJECT 결측
    assemble_events.thread_events(conn, [ev])

    [link] = _batch(conn, "event_thread_link")
    assert link[1] is None and link[3] == "UNKNOWN"          # thread_id NULL + UNKNOWN
    assert "CUSTOMER" in link[6] and "CONTRACT_OBJECT" in link[6]   # unknown_reason
    [snap] = _batch(conn, "thread_discovery_snapshot")
    assert snap[1] is None                                   # snapshot thread_id 도 NULL
    assert _batch(conn, "event_thread") == []               # 스레드 행 없음


def test_falsy_identity_value_is_treated_as_missing(monkeypatch):
    """identity 역할이 키로는 있지만 값이 None(또는 빈 문자열)이면 결측으로 봐 UNKNOWN 이어야
    한다(계약 _identity_scalar 규약, edge-review). WHY: 키 존재만 검사하면 `required:CUSTOMER=None`
    헛 스레드가 서서 CUSTOMER 가 실제로 다른 계약들이 다시 한 스레드로 뭉갠다 — 이 티켓이 고친
    바로 그 뭉갬이 값 검사 누락으로 재발한다."""
    conn = _FakeConn()
    ev = _multi_role_event("evt_n", {"SUPPLIER": "inst_SUP", "CUSTOMER": None,
                                     "CONTRACT_OBJECT": ""})
    assemble_events.thread_events(conn, [ev])

    [link] = _batch(conn, "event_thread_link")
    assert link[1] is None and link[3] == "UNKNOWN"
    assert "CUSTOMER" in link[6] and "CONTRACT_OBJECT" in link[6]
    assert _batch(conn, "event_thread") == []


def test_unthreaded_query_reevaluates_unknown_links(tmp_path, monkeypatch):
    """미연결 조회가 기존 UNKNOWN 링크도 대상에 포함해야 한다(계약상 UNKNOWN 은 재평가 가능,
    edge-review). WHY: `etl.source_event_id IS NULL` 만이면 UNKNOWN 이 영구 상태가 되어, identity
    가 나중에 채워져도(멀티역할 추출 도입 등) 전역 TRUNCATE 없이는 승격되지 않는다. UNKNOWN
    링크는 thread_id NULL 이라 prior_count 를 오염시키지 않아 재조회가 안전하다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)
    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=lambda s, u: _classified("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [fetch_sql] = [sql for sql, _p in conn.log
                   if sql.upper().startswith("SELECT SE.SOURCE_EVENT_ID, SE.EVENT_TYPE_CODE")]
    assert "novelty_status = 'UNKNOWN'" in fetch_sql
    assert "etl.source_event_id IS NULL OR" in fetch_sql


def test_run_logs_unknown_thread_for_unfillable_type(tmp_path, monkeypatch):
    """run 이 UNKNOWN 을 threaded 로 뭉치지 않고 unknown_thread 로 갈라 로그에 남긴다(Rule 12).
    EARNINGS.RESULT_RELEASE 는 identity=[ISSUER, REPORTING_PERIOD] 인데 edge 는 REPORTING_PERIOD
    를 추출하지 않아 라이브에서 UNKNOWN 이 된다 — 이런 흔한 타입이 로그에서 안 보이면 스레드
    커버리지 저하를 아무도 모른다."""
    etype, pred = "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT"
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, etype, pred)
    conn = _FakeConn(assertion_rows=[(doc_id, etype, pred, asrt_id)])
    _setup(monkeypatch, conn)

    def complete_fn(system, user):
        return json.dumps({"items": [{"id": "a1", "is_event": True, "event_type_code": etype,
                                      "predicate_code": pred, "primary_ticker": "005930",
                                      "lifecycle_stage": "", "confidence": 0.9}]})

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [link] = _batch(conn, "event_thread_link")
    assert link[1] is None and link[3] == "UNKNOWN" and "REPORTING_PERIOD" in link[6]
    log = _log(storage)
    assert log["events_created"] == 1 and log["threaded"] == 0 and log["unknown_thread"] == 1
