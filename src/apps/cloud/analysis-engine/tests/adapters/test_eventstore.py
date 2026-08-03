"""Event Store 리포지토리의 트리거·이벤트 소비 테스트.

엔진은 파이프라인이 쓴 price_movement_trigger 를 소비한다. 트리거를 직접 insert 해
이중 writer(ADR-0005)를 되살리면 안 되고, observation/route 계보 id 는 로컬 재계산
후보가 아니라 **소비한** trigger_id 에서 파생돼야 한다 — 아니면 계보가 DB 에 없는
행을 가리킨다.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from edge_analysis.adapters.eventstore import EventStore
from edge_analysis.domain.models import (
    Decomposition,
    EventContext,
    Explanation,
    Measure,
    Member,
)
from edge_analysis.observability import stable_id


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None
        self.rowcount = -1

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if flat.startswith("SELECT price_movement_trigger_id"):
            self._row = self._conn.trigger_row
        elif flat.startswith("INSERT INTO explanation_result"):
            self._row = self._conn.result_insert_row  # RETURNING publication_status
        elif flat.startswith("INSERT INTO tenant_delivery"):
            self.rowcount = self._conn.fanout_rowcount

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, trigger_row=None, result_insert_row=("PUBLISHED",), fanout_rowcount=1):
        self.executed = []
        self.value_batches = []
        self.trigger_row = trigger_row
        self.result_insert_row = result_insert_row
        self.fanout_rowcount = fanout_rowcount
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True
        self.executed.append(("COMMIT", None))


_DECOMP = Decomposition(
    members=[Member("005930", "삼성전자", 0.6, 0.05, 0.03, 1)],
    proxy_ret=0.05, covered_weight=0.6, total_weight=1.0, coverage=0.6,
    top1=1.0, top3=1.0, advancing=1, total_priced=1, n_constituents=1,
)


def test_missing_trigger_row_means_normal_variation():
    conn = _FakeConn(trigger_row=None)

    assert EventStore(conn).fetch_price_trigger("inst_ETF", date(2026, 7, 16)) is None

    # 자연키 + 최신 detected_at (이행기 중복 행이 있을 수 있다).
    sql, params = conn.executed[0]
    assert "ORDER BY detected_at DESC" in sql
    assert params == ("inst_ETF", "2026-07-16")


def test_lineage_derives_from_the_consumed_trigger_id(monkeypatch):
    import psycopg2.extras
    monkeypatch.setattr(
        psycopg2.extras, "execute_values",
        lambda cur, sql, rows: cur._conn.value_batches.append(list(rows)),
    )
    conn = _FakeConn(trigger_row=("pmt_01PIPELINEULID", 0.05, "abs|0.0500|>=0.03", True, False))
    store = EventStore(conn)

    gate = store.fetch_price_trigger("inst_ETF", date(2026, 7, 16))
    assert gate.trigger_id == "pmt_01PIPELINEULID"
    assert gate.abs_gate is True

    ids = store.persist_observation_route(
        gate.trigger_id, _DECOMP, "CONCENTRATED", True, {"005930": "ent_X"})
    assert ids["obs_id"] == stable_id("cob", "pmt_01PIPELINEULID")
    assert ids["route_id"] == stable_id("rte", ids["obs_id"])

    trigger_inserts = [s for s, _ in conn.executed
                       if s.upper().startswith("INSERT INTO PRICE_MOVEMENT_TRIGGER")]
    assert trigger_inserts == []  # 이중 writer 는 되살아나면 안 된다
    obs_inserts = [p for s, p in conn.executed
                   if s.upper().startswith("INSERT INTO ETF_CONTRIBUTION_OBSERVATION")]
    assert obs_inserts[0][1] == "pmt_01PIPELINEULID"  # obs FK 가 소비한 행을 가리킨다


class _EventFetchCursor:
    """fetch_event_contexts 의 3쿼리를 FROM 절로 라우팅하는 가짜 커서."""

    def __init__(self, conn):
        self._conn = conn
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        # 사건 헤더 쿼리도 EXISTS 안에 event_argument 를 담으므로 source_event 를 먼저 본다.
        if " FROM source_event " in flat:
            self._rows = self._conn.head_rows
        elif " FROM event_argument " in flat:
            self._rows = self._conn.argument_rows
        elif " FROM event_measure " in flat:
            self._rows = self._conn.measure_rows

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _EventFetchConn:
    def __init__(self, head_rows, argument_rows=(), measure_rows=()):
        self.executed = []
        self.head_rows = list(head_rows)
        self.argument_rows = list(argument_rows)
        self.measure_rows = list(measure_rows)

    def cursor(self):
        return _EventFetchCursor(self)


def test_fetch_aggregates_all_arguments_into_one_event_context():
    """다중 아규먼트 사건은 EventContext 1개로 모여야 한다 — 구 DISTINCT ON 단일행 붕괴는
    사건당 참여자 1명만 남겨 나머지 역할을 소실했다. 대표 ticker 는 정렬상 첫 행이 아니라
    holdings 접지 참여자다."""
    heads = [("evt_1", "COMPANY.CAPITAL.DIVIDEND_DECISION", "2026-07-16T09:00:00+09:00",
              "DECLARE", "DECIDED", "thr_1", "FIRST_IN_THREAD", "삼성전자 배당 결정", "evd_1",
              "삼성전자가 주당 361원 배당을 결정했다고 16일 공시했다.")]
    args = [
        ("evt_1", "ACQUIRER", "object", "ent_priv", None, None),  # 비종목 참여자가 먼저 정렬된다
        ("evt_1", "ISSUER", "subject", "ent_samsung", "005930", 0.9),
    ]
    conn = _EventFetchConn(heads, args)

    [ctx] = EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"])

    assert (ctx.source_event_id, ctx.entity_id, ctx.ticker) == ("evt_1", "ent_samsung", "005930")
    assert [p.role_code for p in ctx.arguments] == ["ACQUIRER", "ISSUER"]  # 전원 보존
    assert ctx.arguments[0].ticker is None  # 비종목 entity 도 유지(LEFT JOIN)
    assert ctx.arguments[1].confidence == 0.9
    assert (ctx.predicate_code, ctx.lifecycle_stage) == ("DECLARE", "DECIDED")
    assert (ctx.thread_id, ctx.novelty_status) == ("thr_1", "FIRST_IN_THREAD")
    assert ctx.evidence_id == "evd_1"  # 근거 lineage 키(ALPHA-603)
    assert ctx.lead_text.startswith("삼성전자가 주당 361원")  # 스니펫이 문맥으로 함께 온다


def test_fetch_maps_measures_with_values_and_surface_fallback():
    """event_measure 행이 measure_ord 순으로 Measure 에 대응돼야 한다 — 값 미해석
    (UNRESOLVED)이면 value 없이 surface 만 남는다."""
    heads = [("evt_1", "NEWS", "2026-07-16T09:00:00+09:00", None, None, None, None, "제목",
              "evd_1", None)]
    args = [("evt_1", "ISSUER", "subject", "ent_s", "005930", None)]
    measures = [
        ("evt_1", "DIVIDEND_PER_SHARE", Decimal("361.00000000"), "KRW", "TOTAL", "PARSED",
         "주당 361원"),
        ("evt_1", "PAYOUT_TOTAL", None, None, "UNKNOWN", "UNRESOLVED", "약 2조원"),
    ]
    conn = _EventFetchConn(heads, args, measures)

    [ctx] = EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"])

    assert ctx.measures[0] == Measure(
        "DIVIDEND_PER_SHARE", Decimal("361.00000000"), "KRW", "TOTAL", "PARSED", "주당 361원")
    assert ctx.measures[1].value is None
    assert ctx.measures[1].surface == "약 2조원"
    assert (ctx.measures[1].basis, ctx.measures[1].value_source) == ("UNKNOWN", "UNRESOLVED")


def test_fetch_tolerates_null_ontology_columns_from_prebackfill_rows():
    """백필 전 구데이터(predicate/lifecycle/slot NULL·측정 0건)에서도 종전 필드가 그대로
    나와야 한다 — 신규 컬럼은 덧붙는 문맥이지 전제가 아니다."""
    heads = [("evt_1", "NEWS", "2026-07-16T09:00:00+09:00", None, None, None, None, None,
              None, None)]
    args = [("evt_1", "ISSUER", None, "ent_s", "005930", None)]
    conn = _EventFetchConn(heads, args)

    [ctx] = EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"])

    assert (ctx.entity_id, ctx.ticker) == ("ent_s", "005930")
    assert (ctx.novelty_status, ctx.title) == ("UNKNOWN", "")  # 종전 폴백 유지
    assert ctx.predicate_code is None and ctx.lifecycle_stage is None
    assert ctx.evidence_id is None  # TITLE evidence 없는 사건(LEFT JOIN)
    assert ctx.lead_text is None  # 스니펫 백필 전에도 동작한다
    assert ctx.arguments[0].slot is None
    assert ctx.measures == ()


def _settings():
    """persist_explanation 이 Settings 에서 읽는 건 trade_date 하나다(나머지는 연결·레이크용)."""
    return SimpleNamespace(trade_date=date(2026, 7, 16))


def _event(event_id: str, evidence_id: str | None) -> EventContext:
    return EventContext(
        source_event_id=event_id, event_type_code="NEWS",
        available_at="2026-07-16T09:00:00+09:00", entity_id="ent_s", ticker="005930",
        thread_id=None, novelty_status="UNKNOWN", title="제목", evidence_id=evidence_id,
    )


def test_persist_records_lineage_only_for_events_that_have_evidence(monkeypatch):
    """설명이 실제로 본 근거만 lineage 로 남아야 한다 — 근거 없는 사건까지 실으면 FK 가
    가리킬 실체가 없고, 반대로 통째로 빼면 콘솔 근거가 0건이 된다(ALPHA-603).

    픽스처에 evidence 있는 사건과 없는 사건을 **섞는다** — 전부 있는 픽스처는 필터가
    빠져도 초록으로 통과한다.
    """
    import psycopg2.extras
    monkeypatch.setattr(
        psycopg2.extras, "execute_values",
        lambda cur, sql, rows: cur._conn.value_batches.append((" ".join(sql.split()), list(rows))),
    )
    conn = _FakeConn()
    events = [_event("evt_1", "evd_1"), _event("evt_2", None), _event("evt_3", "evd_3")]

    ids = EventStore(conn).persist_explanation(
        _settings(), "inst_ETF", Explanation({"explain": "본문", "confidence": "HIGH"}),
        route_id="rte_1", bundle="dev-mvp-0", primary_thread_id=None, events=events,
    )

    [(sql, rows)] = conn.value_batches
    assert "INSERT INTO explanation_run_event_evidence" in sql
    assert rows == [
        (ids["run_id"], "evd_1", "PROMPT"),   # run_id 는 방금 만든 그 실행을 가리킨다
        (ids["run_id"], "evd_3", "PROMPT"),
    ]
    assert conn.committed


def test_persist_without_any_evidence_skips_the_lineage_insert(monkeypatch):
    """근거가 하나도 없으면 빈 INSERT 를 던지지 않는다 — VALUES 가 비면 문법 오류다."""
    import psycopg2.extras
    monkeypatch.setattr(
        psycopg2.extras, "execute_values",
        lambda cur, sql, rows: cur._conn.value_batches.append((sql, list(rows))),
    )
    conn = _FakeConn()

    EventStore(conn).persist_explanation(
        _settings(), "inst_ETF", Explanation({"explain": "본문"}),
        route_id="rte_1", bundle=None, primary_thread_id=None, events=[_event("evt_1", None)],
    )

    assert conn.value_batches == []


def test_persist_publishes_first_result_and_fans_out_atomically():
    """게시·발번은 한 트랜잭션이어야 한다(ALPHA-493) — 발번만 먼저 커밋되면 sync 소비자
    (BundleEntryStore)가 본체 조인에 실패해 fail-loud 로 죽는다. 락은 게시 게이트 판정
    앞에 있어야 동시 런의 같은 날 이중 게시를 막는다."""
    conn = _FakeConn()

    ids = EventStore(conn).persist_explanation(
        _settings(), "inst_ETF", Explanation({"explain": "본문"}),
        route_id="rte_1", bundle=None, primary_thread_id=None,
        events=[_event("evt_1", None)],
    )

    sqls = [s for s, _ in conn.executed]
    lock_idx = next(i for i, s in enumerate(sqls) if "pg_advisory_xact_lock" in s)
    result_idx = next(i for i, s in enumerate(sqls)
                      if s.startswith("INSERT INTO explanation_result"))
    fanout_idx = next(i for i, s in enumerate(sqls)
                      if s.startswith("INSERT INTO tenant_delivery"))
    assert lock_idx < result_idx < fanout_idx < sqls.index("COMMIT")
    assert sqls.count("COMMIT") == 1  # 중간 커밋이 생기면 원자성이 깨진다
    assert conn.executed[fanout_idx][1] == (ids["explanation_result_id"],)
    assert "'NEW'" in sqls[fanout_idx]  # INVALIDATION 발번은 후속(ALPHA-440), CORRECTION 은 폐지(ADR-0044)
    assert (ids["publication_status"], ids["fanout_tenants"]) == ("PUBLISHED", 1)


def test_rerun_on_published_grain_stays_draft_and_skips_fanout():
    """같은 날 재실행은 DRAFT 보존 + 발번 없음 — as_of 가 런마다 새로워 grain 유니크만으로는
    이중 NEW 발번을 못 막는다. 게이트가 PUBLISHED 만 보는 것도 계약이다(무효화 후 재발번
    허용 여부는 발번 정책 소관 — ADR-0044)."""
    conn = _FakeConn(result_insert_row=("DRAFT",))

    ids = EventStore(conn).persist_explanation(
        _settings(), "inst_ETF", Explanation({"explain": "본문"}),
        route_id="rte_1", bundle=None, primary_thread_id=None,
        events=[_event("evt_1", None)],
    )

    result_sql = next(s for s, _ in conn.executed
                      if s.startswith("INSERT INTO explanation_result"))
    assert "CASE WHEN EXISTS" in result_sql
    assert "publication_status = 'PUBLISHED'" in result_sql
    assert not any(s.startswith("INSERT INTO tenant_delivery") for s, _ in conn.executed)
    assert (ids["publication_status"], ids["fanout_tenants"]) == ("DRAFT", 0)
    assert conn.committed  # DRAFT 도 남긴다 — 게시만 안 할 뿐 결과는 보존


def test_duplicate_result_id_skips_fanout_lineage_and_logs_the_drop(capsys, monkeypatch):
    """같은 result_id 재실행(ON CONFLICT 무삽입)이면 발번도 lineage 도 하지 않는다 —
    tenant_delivery 에 explanation_result_id 유니크가 없어 발번 dedup 은 이 분기가
    전담하고, 이번 런의 근거를 기존 run 에 섞으면 저장된 설명이 안 본 근거가 연결된다.
    산출물은 버려지므로 조용히 지나가면 유실이 안 보인다(Rule 12) — 로그가 남아야 한다."""
    import psycopg2.extras
    monkeypatch.setattr(
        psycopg2.extras, "execute_values",
        lambda cur, sql, rows: cur._conn.value_batches.append((sql, list(rows))),
    )
    conn = _FakeConn(result_insert_row=None)

    ids = EventStore(conn).persist_explanation(
        _settings(), "inst_ETF", Explanation({"explain": "본문"}),
        route_id="rte_1", bundle=None, primary_thread_id=None,
        events=[_event("evt_1", "evd_1")],
    )

    assert not any(s.startswith("INSERT INTO tenant_delivery") for s, _ in conn.executed)
    assert conn.value_batches == []  # 기존 run 의 lineage 를 오염시키지 않는다
    assert (ids["publication_status"], ids["fanout_tenants"]) == (None, 0)
    out = capsys.readouterr().out
    assert "explanation_result.duplicate_skipped" in out
    assert "explanation_result.stored" not in out  # 무저장 런은 성공 건으로 집계 금지


def test_fetch_without_matching_events_skips_detail_queries():
    conn = _EventFetchConn([])

    assert EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"]) == []

    assert len(conn.executed) == 1  # 사건이 없으면 참여자·측정 쿼리를 던지지 않는다
    assert conn.executed[0][1] == ("TITLE", "2026-07-16", ["005930"])  # holdings 접지 필터


class _MinuteFakeCursor(_FakeCursor):
    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if flat.startswith("SELECT trigger_id, entity_id, window_start"):
            self._row = self._conn.minute_trigger_row


class _MinuteFakeConn(_FakeConn):
    def __init__(self, minute_trigger_row=None):
        super().__init__()
        self.minute_trigger_row = minute_trigger_row

    def cursor(self):
        return _MinuteFakeCursor(self)


def test_fetch_minute_price_trigger_maps_signed_return_and_kst_date():
    """분봉 트리거 소비(ALPHA-709) — change_rate(절대값)가 아니라 **부호 있는**
    close/open−1 을 observed_return 으로 재구성하고, trade_date 는 KST 날짜다
    (UTC 날짜로 접으면 자정 경계에서 하루가 밀린다)."""
    from datetime import datetime, timezone
    from decimal import Decimal as D
    # 2026-07-16 09:05 KST == 00:05 UTC — UTC 날짜와 KST 날짜가 같은 날이지만,
    # 15:10 KST 이전의 UTC 표현으로 저장돼 오면 date() 를 그냥 부르면 어긋난다
    window = datetime(2026, 7, 15, 23, 59, tzinfo=timezone.utc)  # KST 07-16 08:59
    conn = _MinuteFakeConn(minute_trigger_row=(
        "mpt_1", "091160", window, D("100"), D("94"),
        D("0.06"), D("0.05"), "intraday-open-v1",
    ))
    result = EventStore(conn).fetch_minute_price_trigger("mpt_1")
    assert result is not None
    trigger, ticker, trade_date = result
    assert ticker == "091160"
    assert trade_date == date(2026, 7, 16)  # KST 축
    assert trigger.abs_gate and not trigger.rel_gate
    assert abs(trigger.observed_return - (-0.06)) < 1e-9  # 하락 방향이 산다
    assert "intraday-open-v1" in trigger.reason


def test_fetch_minute_price_trigger_missing_row_is_none():
    assert EventStore(_MinuteFakeConn()).fetch_minute_price_trigger("mpt_x") is None
