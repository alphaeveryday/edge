"""assemble_events 스텝 테스트 — v4 온톨로지 추출 체인 (ALPHA-412 이식 → ALPHA-545 v4).

실 DB·실 LLM 없이 돈다 — 가짜 complete_fn 이 2콜(게이트→타입별 추출)을, 가짜 커넥션이
SQL 을 기록한다. 가짜 LLM 은 user 페이로드에 event_type_code 가 있으면 추출 콜, 없으면
게이트 콜로 응답한다(실제 프롬프트 구성과 같은 구분 축).

각 테스트가 지키는 WHY: 결정적 ID 파생이 엔진 산식에서 어긋나면 이행기(엔진이 아직
자체 조립을 하는 동안) 같은 이벤트가 두 계보로 갈리고, 자연키 브리지가 깨지면 로더
선적재 행과 FK 가 끊기며, 이미 정규화된 기사에 LLM 을 다시 태우면 비용이 이중이다.
v4 추가분: 참여자 전원(다중역할)·수량(event_measure) 기록, KR 금액의 결정적 파싱,
stage 메뉴 통제(자유텍스트 오염 차단), novelty 세분(CORRECTION·DUPLICATE_REBROADCAST).
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

# 다중역할·stage·novelty 테스트용 타입 — identity=[SUPPLIER, CUSTOMER, CONTRACT_OBJECT],
# DEAL_LIFECYCLE(RUMORED<...<CLOSED; terminal CANCELLED), 수량 CONTRACT_VALUE(basis 필수).
_CONTRACT = "COMPANY.CONTRACT.SIGNING"
_CONTRACT_PRED = "SIGN"


def _thread_key(entity_id: str, event_type_code: str = _ETYPE, role: str = _IDENTITY_ROLE) -> str:
    """계약 thread_key(단일 identity 역할) — assemble_events._thread_key 와 같은 형식."""
    return f"event_type_id={event_type_code}||required:{role}={entity_id}"


_CONTRACT_KEY = ("event_type_id=COMPANY.CONTRACT.SIGNING||required:SUPPLIER=inst_SUP"
                 "||required:CUSTOMER=inst_CUST||required:CONTRACT_OBJECT=concept_hbm")


def _article(article_id: str, ticker: str = "005930", **over) -> dict:
    row = {"article_id": article_id, "source_vendor": "bigkinds", "title": "삼성전자 배당 결정",
           "published_at": "2026-07-15T09:00:00+09:00", "publisher": "매일경제",
           "mentions": json.dumps([{"market": "KR", "ticker": ticker}])}
    row.update(over)
    return row


# ── 가짜 2콜 LLM — user 페이로드의 event_type_code 유무로 게이트/추출을 가른다 ──
def _gate_item(article_id: str, ticker: str = "005930", etype: str = _ETYPE,
               doc_class: str = "EVENT", confidence: float = 0.9) -> dict:
    return {"id": article_id, "doc_class": doc_class, "event_type_code": etype,
            "primary_ticker": ticker, "confidence": confidence}


def _extract_item(article_id: str, predicate: str | None = _PRED, stage: str | None = None,
                  arguments=(), measures=(), confidence: str = "H") -> dict:
    return {"id": article_id, "predicate": predicate, "stage": stage,
            "arguments": list(arguments), "measures": list(measures),
            "confidence": confidence}


def _llm_fn(gate_items=(), extract_items=(), calls: list | None = None):
    """(system, user) → 응답. 항목별 게이트/추출 응답을 id 로 찾아 배치 모양대로 돌려준다."""
    gate_by_id = {i["id"]: i for i in gate_items}
    extract_by_id = {i["id"]: i for i in extract_items}

    def fn(system: str, user: str) -> str:
        payload = json.loads(user)
        if calls is not None:
            calls.append(payload)
        table = extract_by_id if "event_type_code" in payload else gate_by_id
        return json.dumps(
            {"items": [table[i["id"]] for i in payload["items"] if i["id"] in table]},
            ensure_ascii=False)

    return fn


def _default_llm(article_id: str, ticker: str = "005930", etype: str = _ETYPE,
                 pred: str = _PRED, **extract_over):
    """구 단일역할 경로와 동형의 최소 2콜 응답 — arguments/measures 없는 이벤트."""
    return _llm_fn([_gate_item(article_id, ticker, etype)],
                   [_extract_item(article_id, predicate=pred, **extract_over)])


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
            # (조회된 source_code 기준 — 벤더별 자연키). available_at 은 **문서 행 값**
            # (ALPHA-538: 비계가 지역 published 가 아니라 이 값을 실어야 순서 무관).
            source_code, wanted = params[0], set(params[1])
            self._rows = [(a, conn.doc_overrides.get(a, _stable_id("doc", source_code, a)),
                           conn.doc_available_at)
                          for a in wanted]
        elif upper.startswith("SELECT DOCUMENT_ID, EVENT_TYPE_CODE"):
            self._rows = list(conn.assertion_rows)
        elif upper.startswith("SELECT SE.SOURCE_EVENT_ID, SE.EVENT_TYPE_CODE, SE.AVAILABLE_AT,"
                              " SE.LIFECYCLE_STAGE, SE.PREDICATE_CODE, EA.ROLE_CODE"):
            # 미연결 이벤트 = 사전 존재분(conn.unthreaded_events) + 이번 run 이 방금 insert 한
            # source_event(link insert 는 아직이라 전부 미연결). 이벤트마다 event_argument 전
            # 역할 행을 (sid, type, available_at, stage, predicate, role_code, entity_id) 로
            # 편다(thread_key 는 identity 전 역할, novelty 는 stage·predicate 를 쓴다).
            ea_by_se: dict = {}
            se_meta: dict = {}
            for bsql, brows in conn.batches:
                u = bsql.upper()
                if u.startswith("INSERT INTO EVENT_ARGUMENT "):
                    for r in brows:
                        ea_by_se.setdefault(r[0], []).append((r[1], r[2]))  # (role_code, entity_id)
                elif u.startswith("INSERT INTO SOURCE_EVENT "):
                    for r in brows:
                        # (event_type_code, available_at, lifecycle_stage, predicate_code)
                        se_meta[r[0]] = (r[2], r[6], r[4], r[7])
            out = list(conn.unthreaded_events)
            for sid, (etype, avail, stage, predicate) in se_meta.items():
                for role_code, entity_id in ea_by_se.get(sid, []):
                    out.append((sid, etype, avail, stage, predicate, role_code, entity_id))
            self._rows = out
        elif upper.startswith("SELECT THREAD_ID, CURRENT_STAGE"):
            self._rows = [(t, s) for t, s in conn.thread_current_stages.items()]
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
                 assertion_rows=None, prior_thread_counts=None, unthreaded_events=(),
                 thread_current_stages=None):
        self.log: list = []
        self.batches: list = []
        self.instruments = instruments or [("005930", "inst_SAMSUNG"), ("000660", "inst_HYNIX"),
                                           ("999999", "inst_OTHER")]
        self.assembled_articles = set(assembled_articles)
        self.doc_overrides = doc_overrides or {}
        # document.available_at 대역 — load-documents 선적재분은 fetched 기반이라 기사
        # published(09:00+09)와 다를 수 있다. 구분되는 값으로 둬서 어느 시각이 실렸는지 잡는다.
        self.doc_available_at = "2026-07-15T08:30:00+09:00"
        self.assertion_rows = assertion_rows
        self.prior_thread_counts = prior_thread_counts or {}
        # 기존 event_thread.current_stage 대역 — novelty 의 stage 진행 판정 시드.
        self.thread_current_stages = thread_current_stages or {}
        # 사전 존재하는 미연결 이벤트 행: (source_event_id, event_type_code, available_at,
        # lifecycle_stage, predicate_code, role_code, entity_id) — 역할당 한 행.
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


def _assertion_rows_for(article_id: str, etype: str = _ETYPE, pred: str = _PRED):
    doc_id = _stable_id("doc", "bigkinds", article_id)
    asrt_id = _stable_id("asrt", doc_id, etype, pred)
    return [(doc_id, etype, pred, asrt_id)]


def test_event_lineage_matches_engine_derivation(tmp_path, monkeypatch):
    """분류 1건 → document/assertion/source_event/argument/evidence/thread 전체 계보가
    엔진과 같은 결정적 ID 산식으로 서야 이행기 멱등 수렴이 성립한다. v4 2콜 체인에서도
    게이트 콜 입력의 tickers 는 entity_index 교집합이어야 하고(엔진 규칙), 참여자 없는
    추출(구 단일역할 경로)은 primary 폴백 행 하나로 이어진다 — 회귀 무파손."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)
    calls = []
    complete_fn = _llm_fn([_gate_item("a1")], [_extract_item("a1")], calls=calls)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, _ETYPE, _PRED)
    evt_id = _stable_id("evt", asrt_id, "inst_SAMSUNG")
    [se] = _batch(conn, "source_event")
    assert se[0] == evt_id and se[1] == "NEWS" and se[3] == "2026-07-15"
    [arg] = _batch(conn, "event_argument")
    assert (arg[0], arg[2]) == (evt_id, "inst_SAMSUNG")
    # 참여자 없는 추출 → 구 단일역할과 동형의 폴백 행(신규 컬럼은 결측, kind 는 ticker
    # 해소 = 발행사 접지라 ISSUER).
    assert arg[4:] == (None, None, "ISSUER", None)
    [ev] = _batch(conn, "event_evidence")
    assert ev[1] == evt_id and ev[3] == "TITLE"
    # threading: 첫 이벤트는 FIRST_IN_THREAD, thread_id 는 identity_roles 기반 계약 키.
    [link] = _batch(conn, "event_thread_link")
    thread_id = _stable_id("thr", _thread_key("inst_SAMSUNG"))
    assert (link[1], link[3]) == (thread_id, "FIRST_IN_THREAD")
    # 게이트 콜(1번째 호출) 입력의 tickers 는 entity_index 교집합이어야 한다(엔진 규칙).
    assert calls[0]["items"][0]["tickers"] == ["005930"]
    # 2번째 호출은 타입별 추출 콜 — 게이트가 고른 타입이 페이로드에 박힌다.
    assert calls[1]["event_type_code"] == _ETYPE
    log = _log(storage)
    assert log["events_created"] == 1 and log["threaded"] == 1
    assert log["assembler_version"] == assemble_events.ASSEMBLER_VERSION


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


def test_non_event_doc_class_skips_extraction_and_assembly(tmp_path, monkeypatch):
    """게이트가 비이벤트(doc_class≠EVENT)로 판정하면 추출 콜 자체가 나가지 않고 조립도
    없다 — 2콜 체인의 게이트가 비용 관문이다(이식원 v3: 비이벤트는 명시 클래스, item 없음)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn()
    _setup(monkeypatch, conn)
    calls = []
    complete_fn = _llm_fn([_gate_item("a1", doc_class="MARKET_COMMENTARY")], [], calls=calls)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    assert _batch(conn, "source_event") == []
    assert all("event_type_code" not in c for c in calls)  # 추출 콜 0회
    assert _log(storage)["events_created"] == 0


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

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    [asrt_arg] = _batch(conn, "assertion_argument")
    assert asrt_arg[0] == loader_asrt
    [se] = _batch(conn, "source_event")
    assert se[0] == _stable_id("evt", loader_asrt, "inst_SAMSUNG")


def test_assertion_insert_is_a_scaffold_without_owned_columns(tmp_path, monkeypatch):
    """document_assertion 비계 계약(ALPHA-538) — 이 스텝은 공유 결정값(자연키 + **문서 행**
    available_at)만 싣는다. confidence·lifecycle_stage 를 실으면 load-assertions 와
    '먼저 쓴 쪽이 이기는' 순서 의존이 부활하고(소유자는 각각 tag-news 체인·event grain),
    available_at 을 지역 published 값으로 실으면 load-documents 선적재 문서(fetched 기반)
    에서 같은 순서 의존이 남는다(Codex #243 P2). v4 의 새 컬럼(predicate_code·
    confidence_level·completeness)은 event grain(source_event)에 실린다 — 소유권 계약 그대로."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    asrt_sql = next(s for s, _ in conn.batches
                    if s.upper().startswith("INSERT INTO DOCUMENT_ASSERTION"))
    assert "confidence" not in asrt_sql.lower() and "lifecycle_stage" not in asrt_sql.lower()
    doc_id = _stable_id("doc", "bigkinds", "a1")
    [row] = _batch(conn, "document_assertion")
    [se] = _batch(conn, "source_event")
    assert row[:4] == (_stable_id("asrt", doc_id, _ETYPE, _PRED), doc_id, _ETYPE, _PRED)
    # available_at = 문서 행 값(fetched 기반 대역) — 기사 published 로 만든 이벤트 시각과 다르다.
    assert len(row) == 5 and row[4] == conn.doc_available_at and row[4] != se[6]
    # v4 event grain: lifecycle_stage 는 검증된 값만(없으면 None), predicate·confidence_level·
    # completeness 가 source_event 에 실린다("H"→HIGH, ISSUER 는 primary 로 충족→complete).
    assert len(se) == 10 and se[4] is None
    assert se[7:] == (_PRED, "HIGH", "complete")


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

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a-en"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [doc] = _batch(conn, "document")
    assert (doc[2], doc[3]) == ("fmp", "a-en")
    assert doc[5] == "en"  # language_code 도 파티션 축을 따른다(ko 하드코딩 금지)
    resolutions = [p for sql, p in conn.log
                   if sql.upper().startswith("SELECT SOURCE_DOCUMENT_ID, DOCUMENT_ID")]
    assert resolutions == [("fmp", ["a-en"])]


def test_thread_timestamps_stay_monotonic_on_conflict(tmp_path, monkeypatch):
    """백필이 기존 스레드보다 오래된 이벤트를 넣어도 opened_at/last_state_at 이 역행하면
    안 된다(ck_event_thread_time 위반 → 백필 전체 롤백) — LEAST/GREATEST 병합이어야 하고,
    current_stage 는 COALESCE 병합(NULL 로 되돌리지 않음)이어야 한다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [thread_sql] = [sql for sql, _rows in conn.batches
                    if sql.upper().startswith("INSERT INTO EVENT_THREAD ")]
    assert "LEAST(event_thread.opened_at" in thread_sql
    assert "GREATEST(event_thread.last_state_at" in thread_sql
    assert "COALESCE(EXCLUDED.current_stage" in thread_sql


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
                               complete_fn=_default_llm("a1", ticker="999999"),
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
    # v4 조회 형상: (sid, etype, available_at, stage, predicate, role_code, entity_id) —
    # 구 행(v4 이전 적재분)은 stage·predicate 가 NULL 이다.
    stale = ("evt_stale", _ETYPE, "2026-07-15T09:00:00+09:00", None, None,
             _IDENTITY_ROLE, "inst_OTHER")
    # a1 은 이미 조립됨 → 재분류 안 함(created 비어야 self-heal 만 검증됨).
    conn = _FakeConn(assembled_articles=["a1"], unthreaded_events=[stale])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a1"),
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
                               complete_fn=_default_llm("a1", ticker="000660"),
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


# ── v4: 다중 참여자·수량 기록 ────────────────────────────────────────────────
def test_multi_company_arguments_recorded_with_slot_and_group(tmp_path, monkeypatch):
    """멀티기업 기사 — 해소된 참여자 **전원**이 event_argument 에 slot·mention·group_ord 와
    함께 실리고(다중역할), 미해소 참여자(CONTRACT_OBJECT 개념)는 entity_id NOT NULL PK 라
    스킵하되 **completeness 에는 반영**된다(이식원 UNRESOLVED 규약 — 보도가 역할을 말한
    사실과 해소 가능 여부는 다른 축). 수량(CONTRACT_VALUE)은 event_measure 로 간다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article(
        "a1", title="삼성전자, SK하이닉스와 1,883억원 HBM 공급계약",
        mentions=json.dumps([{"market": "KR", "ticker": "005930"},
                             {"market": "KR", "ticker": "000660"}]))])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [_extract_item(
            "a1", predicate=_CONTRACT_PRED,
            arguments=[
                {"role": "SUPPLIER", "slot": "subject", "mention": "삼성전자",
                 "ticker": "005930", "group": 0},
                {"role": "CUSTOMER", "slot": "object", "mention": "SK하이닉스",
                 "ticker": "000660", "group": 0},
                {"role": "CONTRACT_OBJECT", "slot": "qualifier", "mention": "HBM",
                 "ticker": "", "group": 0},
            ],
            measures=[{"role": "CONTRACT_VALUE", "surface": "1,883억원",
                       "basis": "TOTAL", "group": 0}])])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, _CONTRACT, _CONTRACT_PRED)
    evt_id = _stable_id("evt", asrt_id, "inst_SAMSUNG")
    args = {(r[1], r[2]): r for r in _batch(conn, "event_argument")}
    # 해소된 참여자 2명 전원 — primary 가 참여자로 실렸으니 폴백 행은 없다.
    assert set(args) == {("SUPPLIER", "inst_SAMSUNG"), ("CUSTOMER", "inst_HYNIX")}
    assert args[("SUPPLIER", "inst_SAMSUNG")][4:] == ("subject", "삼성전자", "ISSUER", 0)
    assert args[("CUSTOMER", "inst_HYNIX")][4:] == ("object", "SK하이닉스", "ISSUER", 0)
    # 수량 — KR 파서가 value/unit 을 계산(1,883억원 → 1883e8 KRW), 추출 순서가 measure_ord.
    [meas] = _batch(conn, "event_measure")
    assert meas == (evt_id, 0, "CONTRACT_VALUE", "1,883억원", 188_300_000_000.0, "KRW",
                    "TOTAL", "PARSED", "ok", 0, None)
    # completeness: required=SUPPLIER·CONTRACT_OBJECT — 미해소 CONTRACT_OBJECT 도 추출은
    # 됐으므로 complete. 미해소 수는 로그 카운터로 드러난다(Rule 12).
    [se] = _batch(conn, "source_event")
    assert (se[0], se[7], se[9]) == (evt_id, _CONTRACT_PRED, "complete")
    log = _log(storage)
    assert log["arguments_unresolved"] == 1


def test_measure_role_outside_quantity_menu_is_dropped(tmp_path, monkeypatch):
    """수량 메뉴 밖 역할을 measures 로 뱉은 환각(CONTRACT_OBJECT 는 참여자 역할이다)은 그
    항목만 버린다 — event_measure 에 실리지 않고 covered_roles 도 부풀리지 않아, required
    역할이 참여자 추출로 안 채워졌으면 completeness 가 partial 로 남는다(Codex #255 P2)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", title="삼성전자 HBM 공급계약")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [_extract_item(
            "a1", predicate=_CONTRACT_PRED,
            arguments=[{"role": "SUPPLIER", "slot": "subject", "mention": "삼성전자",
                           "ticker": "005930", "group": 0}],
            measures=[
                {"role": "CONTRACT_OBJECT", "surface": "HBM", "basis": "TOTAL", "group": 0},
                {"role": "CONTRACT_VALUE", "surface": "1,883억원", "basis": "TOTAL", "group": 0},
            ])])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    # 발명 역할은 버려지고 메뉴 안 CONTRACT_VALUE 만 실린다 — 남은 항목이 ord 0 을 받는다.
    [meas] = _batch(conn, "event_measure")
    assert (meas[1], meas[2]) == (0, "CONTRACT_VALUE")
    # covered_roles 미부풀림 — required 인 CONTRACT_OBJECT 가 참여자로 안 채워졌으니 partial.
    [se] = _batch(conn, "source_event")
    assert se[9] == "partial"


def test_kr_amount_parsing_and_basis_flow_into_event_measure(tmp_path, monkeypatch):
    """KR 금액 파싱 — 조/억 혼합은 결정적 파서가 NUMERIC 값을 계산하고(1조2,000억원 →
    1.2e12), 문법 밖 표기는 값을 지어내지 않고 UNRESOLVED 로 남는다. basis 는 모델 명시가
    메뉴 안이면 그 값(ANNUAL), 메뉴 밖이면 surface 의 결정적 판정(총→TOTAL)이다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1")],
        [_extract_item("a1", measures=[
            # 모델 basis 가 메뉴 밖("TTM") → surface 의 '총' 이 TOTAL 을 결정.
            {"role": "TOTAL_DIVIDEND_VALUE", "surface": "총 1조2,000억원",
             "basis": "TTM", "group": 0},
            # 모델이 ANNUAL 명시 → 그대로. 값은 주당 1,500원.
            {"role": "DIVIDEND_PER_SHARE", "surface": "주당 1,500원",
             "basis": "ANNUAL", "group": 0},
            # 숫자 없는 표기 → 값 없음(UNRESOLVED·no_number) — 추정 금지.
            {"role": "TOTAL_DIVIDEND_VALUE", "surface": "역대급 규모",
             "basis": "UNKNOWN", "group": 1},
        ])])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    measures = _batch(conn, "event_measure")
    assert [m[1] for m in measures] == [0, 1, 2]  # measure_ord = 추출 순서
    assert measures[0][3:10] == ("총 1조2,000억원", 1_200_000_000_000.0, "KRW", "TOTAL",
                                 "PARSED", "ok", 0)
    assert measures[1][3:10] == ("주당 1,500원", 1_500.0, "KRW", "ANNUAL", "PARSED", "ok", 0)
    assert measures[2][3:10] == ("역대급 규모", None, None, "UNKNOWN", "UNRESOLVED",
                                 "no_number", 1)


# ── v4: stage 메뉴 통제 ──────────────────────────────────────────────────────
def test_stage_outside_lifecycle_menu_becomes_null(tmp_path, monkeypatch):
    """stage 메뉴 밖 값(자유텍스트)은 NULL + 카운터 — 현행 DB 의 43종 오염이 다시 쌓이지
    않게 lifecycle 모델 어휘만 lifecycle_stage 에 실린다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn([_gate_item("a1", etype=_CONTRACT)],
                          [_extract_item("a1", predicate=_CONTRACT_PRED, stage="총력전")])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [se] = _batch(conn, "source_event")
    assert se[4] is None
    assert _log(storage)["stage_rejected"] == 1


def test_stage_inside_lifecycle_menu_is_kept(tmp_path, monkeypatch):
    """메뉴 안 stage(DEAL_LIFECYCLE 의 DEFINITIVE_SIGNED)는 그대로 실린다 — 통제는 오염
    차단이지 stage 삭제가 아니다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [_extract_item("a1", predicate=_CONTRACT_PRED, stage="DEFINITIVE_SIGNED")])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [se] = _batch(conn, "source_event")
    assert se[4] == "DEFINITIVE_SIGNED"
    assert _log(storage)["stage_rejected"] == 0


# ── threading / novelty ──────────────────────────────────────────────────────
def _multi_role_event(source_event_id: str, role_values: dict,
                      available_at="2026-07-15T09:00:00+09:00",
                      stage=None, predicate=None, etype=_CONTRACT):
    return {"source_event_id": source_event_id, "event_type_code": etype,
            "available_at": available_at, "role_values": role_values,
            "lifecycle_stage": stage, "predicate_code": predicate}


def test_contract_signing_splits_thread_by_customer(monkeypatch):
    """계약 불변식: CONTRACT.SIGNING identity=SUPPLIER·CUSTOMER·CONTRACT_OBJECT 라, 같은
    공급사라도 CUSTOMER 가 다르면 **다른 thread** 다. 엔티티 하나로 키를 만들던 옛 구현은
    이 둘을 한 스레드로 뭉갰다(신규 계약이 기존 계약의 FOLLOW_UP 으로 오독). WHY: novelty·
    prior_event_count 가 이 키 위에서 나오므로, 키가 CUSTOMER 를 안 보면 사건 계보가 틀린다.

    v4 다중역할 기록으로 CUSTOMER 가 event_argument 에 실리는 경로가 실제로 생겼다 —
    thread_key 로직은 identity 가 채워졌을 때 올바로 갈라야 한다."""
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
    """identity 역할을 못 채우면(개념 역할 미해소 등) synthetic thread 를 만들지 않고
    novelty=UNKNOWN·thread_id=NULL 로 남긴다(계약 불변식 5
    missing_identity_policy=EMIT_UNKNOWN_LINK_ONLY). WHY: 없는 identity 로 억지 스레드를
    세우면 서로 다른 계약이 다시 한 스레드로 뭉개져 옛 버그가 재발한다."""
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


def test_novelty_splits_follow_up_duplicate_and_correction(monkeypatch):
    """novelty 세분(ALPHA-545) — 같은 스레드 안에서: stage 순서축 **전진**만 FOLLOW_UP_STAGE,
    같은 단계 재보도는 DUPLICATE_REBROADCAST, 정정 마커(CANCELLED 의 CANCEL)는 CORRECTION.
    WHY: 구 구현은 prior>0 을 전부 FOLLOW_UP 으로 뭉쳐 재보도가 '진행'으로 오독됐다 —
    novelty 를 소비하는 설명(신규성 강조)이 재탕 기사를 새 소식으로 포장하게 된다.
    입력을 뒤섞어 available_at 정렬 위에서 판정됨도 함께 잠근다."""
    conn = _FakeConn()
    rv = {"SUPPLIER": "inst_SUP", "CUSTOMER": "inst_CUST", "CONTRACT_OBJECT": "concept_hbm"}
    e1 = _multi_role_event("evt_1", rv, "2026-07-15T09:00:00+09:00", stage="RUMORED")
    e2 = _multi_role_event("evt_2", rv, "2026-07-15T10:00:00+09:00", stage="DEFINITIVE_SIGNED")
    e3 = _multi_role_event("evt_3", rv, "2026-07-15T11:00:00+09:00", stage="DEFINITIVE_SIGNED")
    e4 = _multi_role_event("evt_4", rv, "2026-07-15T12:00:00+09:00", stage="CANCELLED")
    assemble_events.thread_events(conn, [e3, e1, e4, e2])  # 순서 뒤섞기 — 정렬이 판정 축

    links = {r[0]: r[3] for r in _batch(conn, "event_thread_link")}
    assert links == {"evt_1": "FIRST_IN_THREAD", "evt_2": "FOLLOW_UP_STAGE",
                     "evt_3": "DUPLICATE_REBROADCAST", "evt_4": "CORRECTION"}
    # 스레드 헤더의 current_stage 는 순서축 전진을 따라 마지막 단계(CANCELLED)까지 갱신된다.
    [thread] = _batch(conn, "event_thread")
    assert thread[3] == "CANCELLED"


def test_novelty_seeds_stage_and_prior_from_db(monkeypatch):
    """런을 넘어선 novelty — 기존 스레드의 current_stage(DB 시드)와 prior 링크 수를 기준으로
    같은 단계 재보도=DUPLICATE_REBROADCAST, 전진=FOLLOW_UP_STAGE, 정정 술어(REVISE)=CORRECTION.
    WHY: 배치 내부 상태만 보면 재실행·백필에서 같은 단계 재보도가 매번 FOLLOW_UP 으로 나와
    PIT 재현이 깨진다."""
    tid = _stable_id("thr", _CONTRACT_KEY)
    conn = _FakeConn(prior_thread_counts={tid: 2},
                     thread_current_stages={tid: "DEFINITIVE_SIGNED"})
    rv = {"SUPPLIER": "inst_SUP", "CUSTOMER": "inst_CUST", "CONTRACT_OBJECT": "concept_hbm"}
    dup = _multi_role_event("evt_dup", rv, "2026-07-16T09:00:00+09:00",
                            stage="DEFINITIVE_SIGNED")
    follow = _multi_role_event("evt_fol", rv, "2026-07-16T10:00:00+09:00", stage="EFFECTIVE")
    correct = _multi_role_event("evt_cor", rv, "2026-07-16T11:00:00+09:00", predicate="REVISE")
    assemble_events.thread_events(conn, [dup, follow, correct])

    links = {r[0]: r[3] for r in _batch(conn, "event_thread_link")}
    assert links == {"evt_dup": "DUPLICATE_REBROADCAST", "evt_fol": "FOLLOW_UP_STAGE",
                     "evt_cor": "CORRECTION"}


def test_repeat_without_stage_info_is_duplicate_rebroadcast(monkeypatch):
    """단계 정보가 전혀 없는 후속 보도(stage NULL, 라이프사이클 없는 타입 포함)는
    DUPLICATE_REBROADCAST 다 — '진행'의 증거 없이 FOLLOW_UP 을 남발하지 않는다."""
    conn = _FakeConn()
    rv = {"ISSUER": "inst_SAMSUNG"}
    e1 = _multi_role_event("evt_1", rv, "2026-07-15T09:00:00+09:00", etype=_ETYPE)
    e2 = _multi_role_event("evt_2", rv, "2026-07-15T10:00:00+09:00", etype=_ETYPE)
    assemble_events.thread_events(conn, [e1, e2])

    links = {r[0]: r[3] for r in _batch(conn, "event_thread_link")}
    assert links == {"evt_1": "FIRST_IN_THREAD", "evt_2": "DUPLICATE_REBROADCAST"}


def test_unthreaded_query_reevaluates_unknown_links(tmp_path, monkeypatch):
    """미연결 조회가 기존 UNKNOWN 링크도 대상에 포함해야 한다(계약상 UNKNOWN 은 재평가 가능,
    edge-review). WHY: `etl.source_event_id IS NULL` 만이면 UNKNOWN 이 영구 상태가 되어, identity
    가 나중에 채워져도(멀티역할 추출 도입 등) 전역 TRUNCATE 없이는 승격되지 않는다. UNKNOWN
    링크는 thread_id NULL 이라 prior_count 를 오염시키지 않아 재조회가 안전하다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1"))
    _setup(monkeypatch, conn)
    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=_default_llm("a1"),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [fetch_sql] = [sql for sql, _p in conn.log
                   if sql.upper().startswith("SELECT SE.SOURCE_EVENT_ID, SE.EVENT_TYPE_CODE")]
    assert "novelty_status = 'UNKNOWN'" in fetch_sql
    assert "etl.source_event_id IS NULL OR" in fetch_sql


def test_run_logs_unknown_thread_for_unfillable_type(tmp_path, monkeypatch):
    """run 이 UNKNOWN 을 threaded 로 뭉치지 않고 unknown_thread 로 갈라 로그에 남긴다(Rule 12).
    EARNINGS.RESULT_RELEASE 는 identity=[ISSUER, REPORTING_PERIOD] 인데 REPORTING_PERIOD 는
    entity 로 해소되지 않아 라이브에서 UNKNOWN 이 된다 — 이런 흔한 타입이 로그에서 안 보이면
    스레드 커버리지 저하를 아무도 모른다."""
    etype, pred = "COMPANY.EARNINGS.RESULT_RELEASE", "REPORT"
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1")])
    doc_id = _stable_id("doc", "bigkinds", "a1")
    asrt_id = _stable_id("asrt", doc_id, etype, pred)
    conn = _FakeConn(assertion_rows=[(doc_id, etype, pred, asrt_id)])
    _setup(monkeypatch, conn)

    assert assemble_events.run(storage, "R1", db=_db(),
                               complete_fn=_default_llm("a1", etype=etype, pred=pred),
                               from_date="2026-07-15", to_date="2026-07-15") == 0
    [link] = _batch(conn, "event_thread_link")
    assert link[1] is None and link[3] == "UNKNOWN" and "REPORTING_PERIOD" in link[6]
    log = _log(storage)
    assert log["events_created"] == 1 and log["threaded"] == 0 and log["unknown_thread"] == 1


# ── 분류 병렬화(ALPHA-520) — v4 2콜 체인에서도 게이트 배치는 동시 실행 ──────
def test_classify_batches_run_concurrently_not_serialized(tmp_path):
    """WHY: classify 배치 병렬화(ALPHA-520)가 실제 동시 실행돼야 의미가 있다 — ThreadPool 을
    순차로 되돌리는 회귀는 런타임만 되돌리고 결과는 같아 값 검사로 안 잡힌다(Rule 9). 게이트
    3배치가 barrier 에 동시 도달해야만 풀리게 해, 순차면 타임아웃→_complete_json RuntimeError
    로 드러낸다(추출 콜은 배치 수가 달라 barrier 미적용 — 게이트 동시성만 검증).
    """
    import threading

    from edge_ontology import load_ontology_view
    from data_pipeline.steps.assemble_events import CLASSIFY_BATCH, classify_titles

    view = load_ontology_view()
    entity_index = {"005930": "inst_SAMSUNG"}
    n_batches = 3
    rows = [{"article_id": f"a{i}", "title": f"제목{i}", "tickers": ["005930"]}
            for i in range(n_batches * CLASSIFY_BATCH)]
    barrier = threading.Barrier(n_batches, timeout=5)

    def gated(system, user):
        payload = json.loads(user)
        if "event_type_code" in payload:  # 추출 콜 — 검증 대상 아님, 즉답
            return json.dumps({"items": [_extract_item(it["id"]) for it in payload["items"]]})
        barrier.wait()  # 게이트 3배치가 동시에 도달해야 풀린다 — 순차면 타임아웃
        return json.dumps({"items": [_gate_item(it["id"]) for it in payload["items"]]})

    results = classify_titles(gated, rows, view, entity_index, concurrency=n_batches)
    assert len(results) == n_batches * CLASSIFY_BATCH  # 순차 회귀면 barrier 타임아웃→RuntimeError


def test_classify_merges_all_batches_with_correct_per_batch_tickers(tmp_path):
    """WHY: 배치별 결과를 취합 후 병합하므로 (1) 배치 하나라도 병합에서 누락되면 그 기사들이
    사라지고 (2) 클로저가 배치를 잘못 잡으면 allowed_by_id 가 어긋나 엉뚱한 티커가 걸러진다.
    티커를 배치 걸쳐 교차시키고 각 기사의 결과 엔티티가 자기 입력 티커와 맞는지로 둘 다 잠근다.
    2콜 체인에선 게이트→추출 병합까지 거친 최종 결과가 기사 전량이어야 한다.
    """
    from edge_ontology import load_ontology_view
    from data_pipeline.steps.assemble_events import classify_titles

    view = load_ontology_view()
    entity_index = {"005930": "inst_SAMSUNG", "000660": "inst_HYNIX"}
    n = 100
    tickers = ["005930" if i % 2 == 0 else "000660" for i in range(n)]
    rows = [{"article_id": f"a{i}", "title": f"제목{i}", "tickers": [tickers[i]]} for i in range(n)]

    def echoing(system, user):
        payload = json.loads(user)
        if "event_type_code" in payload:
            return json.dumps({"items": [_extract_item(it["id"]) for it in payload["items"]]})
        return json.dumps({"items": [
            _gate_item(it["id"], ticker=it["tickers"][0]) for it in payload["items"]]})

    results = classify_titles(echoing, rows, view, entity_index, concurrency=8)

    assert len(results) == n  # 배치 누락 없음
    for i in range(n):
        cls = results[f"a{i}"]
        assert cls["primary_ticker"] == tickers[i]  # 배치 격리: 자기 입력 티커로 해소
        assert cls["entity_id"] == ("inst_SAMSUNG" if tickers[i] == "005930" else "inst_HYNIX")


def test_malformed_nonscalar_labels_degrade_field_not_run(tmp_path, monkeypatch):
    """비스칼라 라벨({"predicate": []}·stage {}·slot []·basis []·confidence [])은 그 필드만
    결측/기본값 처리한다 — frozenset·dict 멤버십 TypeError 가 run() 밖으로 새면 기형 기사
    하나가 날짜 전체를 롤백시킨다(Codex #255 P2, Rule 12: 국소 실패)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", title="삼성전자 공급계약")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [{"id": "a1", "predicate": [], "stage": {}, "confidence": [],
          "arguments": [{"role": "SUPPLIER", "slot": [], "mention": "삼성전자",
                            "ticker": "005930", "group": 0}],
          "measures": [{"role": "CONTRACT_VALUE", "surface": "총 1,883억원",
                        "basis": [], "group": 0}]}])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    [se] = _batch(conn, "source_event")
    # predicate 는 타입 기본술어로, stage·confidence_level 은 NULL 로 강등 — 행은 산다.
    assert (se[4], se[7], se[8]) == (None, _CONTRACT_PRED, None)
    [arg] = _batch(conn, "event_argument")
    assert arg[4] is None  # slot [] → NULL
    [meas] = _batch(conn, "event_measure")
    assert meas[6] == "TOTAL"  # basis [] → surface 결정 판정("총")


def test_malformed_container_types_do_not_abort_run(tmp_path, monkeypatch):
    """arguments/measures 컨테이너가 비리스트(정수 등 truthy 스칼라)여도, items 에 비객체
    항목(null·스칼라)이 섞여도 그 항목만 결측 취급한다 — `or []`·`.get` 은 truthy 스칼라와
    null 을 못 걸러 TypeError/AttributeError 로 날짜 전체를 롤백시킨다(Codex #255 P2).
    이벤트는 폴백 anchor 로 살고 measure 는 0건이다."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", title="삼성전자 공급계약")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    inner = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [{"id": "a1", "predicate": _CONTRACT_PRED, "stage": None,
          "arguments": 1, "measures": 5, "confidence": "H"}])

    def complete_fn(system: str, user: str) -> str:
        out = json.loads(inner(system, user))
        out["items"] += [None, 7]  # 게이트·추출 응답 양쪽에 비객체 항목 주입
        return json.dumps(out, ensure_ascii=False)

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    [arg] = _batch(conn, "event_argument")  # 폴백 anchor 행만
    assert (arg[1], arg[2]) == ("SUPPLIER", "inst_SAMSUNG")
    assert _batch(conn, "event_measure") == []


def test_quantity_unit_family_mismatch_stays_unresolved(tmp_path, monkeypatch):
    """수량 역할의 unit_family 와 파서 단위 소속이 다르면(CONTRACT_VALUE+5%,
    CONTRACT_DURATION+%) 값을 지어내지 않고 UNRESOLVED + unit_mismatch 로 남긴다 — PCT 가
    KRW·일수 자리로 새면 임계·골드 대조가 오염된다. 소속이 맞으면 보존한다: USD 통화
    (환산·강등 금지), 년(DURATION_DAYS 소속 YEARS)(Codex #255 P2 일반화)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", title="삼성전자 공급계약")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [_extract_item("a1", predicate=_CONTRACT_PRED,
                       measures=[{"role": "CONTRACT_VALUE", "surface": "5%",
                                  "basis": "TOTAL", "group": 0},
                                 {"role": "CONTRACT_VALUE", "surface": "3억달러",
                                  "basis": "TOTAL", "group": 0},
                                 {"role": "CONTRACT_DURATION", "surface": "5%",
                                  "basis": "TOTAL", "group": 0},
                                 {"role": "CONTRACT_DURATION", "surface": "3년",
                                  "basis": "TOTAL", "group": 0}])])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    pct, usd, dur_pct, years = _batch(conn, "event_measure")
    assert (pct[4], pct[5], pct[7], pct[8]) == (None, None, "UNRESOLVED", "unit_mismatch")
    assert (usd[4], usd[5], usd[7]) == (300_000_000.0, "USD", "PARSED")
    assert (dur_pct[4], dur_pct[7], dur_pct[8]) == (None, "UNRESOLVED", "unit_mismatch")
    assert (years[4], years[5], years[7]) == (3.0, "YEARS", "PARSED")


def test_primary_under_other_role_keeps_identity_anchor(tmp_path, monkeypatch):
    """추출이 primary 를 다른 유효 역할(CUSTOMER)로만 묶으면 폴백 anchor 행(cls.role_code)을
    여전히 세운다 — 단일 identity 타입에서 anchor 가 사라지면 thread_key 입력이 없어
    이벤트가 UNKNOWN 으로 전락한다(Codex #255 P2)."""
    storage = LocalStorage(tmp_path / "lake")
    _write_news(storage, "ko", "2026-07-15", [_article("a1", title="삼성전자 공급계약")])
    conn = _FakeConn(assertion_rows=_assertion_rows_for("a1", _CONTRACT, _CONTRACT_PRED))
    _setup(monkeypatch, conn)
    complete_fn = _llm_fn(
        [_gate_item("a1", etype=_CONTRACT)],
        [_extract_item("a1", predicate=_CONTRACT_PRED,
                       arguments=[{"role": "CUSTOMER", "slot": "object",
                                      "mention": "삼성전자", "ticker": "005930", "group": 0}])])

    assert assemble_events.run(storage, "R1", db=_db(), complete_fn=complete_fn,
                               from_date="2026-07-15", to_date="2026-07-15") == 0

    args = {(a[1], a[2]): a for a in _batch(conn, "event_argument")}
    # CUSTOMER 로 실린 primary + identity 폴백(SUPPLIER=required_roles[0]) 둘 다 선다.
    assert set(args) == {("CUSTOMER", "inst_SAMSUNG"), ("SUPPLIER", "inst_SAMSUNG")}
    assert args[("SUPPLIER", "inst_SAMSUNG")][4:] == (None, None, "ISSUER", None)
