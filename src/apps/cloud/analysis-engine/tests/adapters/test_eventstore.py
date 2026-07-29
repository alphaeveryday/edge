"""Event Store 리포지토리의 트리거·이벤트 소비 테스트.

엔진은 파이프라인이 쓴 price_movement_trigger 를 소비한다. 트리거를 직접 insert 해
이중 writer(ADR-0005)를 되살리면 안 되고, observation/route 계보 id 는 로컬 재계산
후보가 아니라 **소비한** trigger_id 에서 파생돼야 한다 — 아니면 계보가 DB 에 없는
행을 가리킨다.
"""

from datetime import date
from decimal import Decimal

from edge_analysis.adapters.eventstore import EventStore
from edge_analysis.domain.models import Decomposition, Measure, Member
from edge_analysis.observability import stable_id


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if flat.startswith("SELECT price_movement_trigger_id"):
            self._row = self._conn.trigger_row

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, trigger_row=None):
        self.executed = []
        self.value_batches = []
        self.trigger_row = trigger_row
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


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
              "DECLARE", "DECIDED", "thr_1", "FIRST_IN_THREAD", "삼성전자 배당 결정",
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
    assert ctx.lead_text.startswith("삼성전자가 주당 361원")  # 스니펫이 문맥으로 함께 온다


def test_fetch_maps_measures_with_values_and_surface_fallback():
    """event_measure 행이 measure_ord 순으로 Measure 에 대응돼야 한다 — 값 미해석
    (UNRESOLVED)이면 value 없이 surface 만 남는다."""
    heads = [("evt_1", "NEWS", "2026-07-16T09:00:00+09:00", None, None, None, None, "제목", None)]
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
    heads = [("evt_1", "NEWS", "2026-07-16T09:00:00+09:00", None, None, None, None, None, None)]
    args = [("evt_1", "ISSUER", None, "ent_s", "005930", None)]
    conn = _EventFetchConn(heads, args)

    [ctx] = EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"])

    assert (ctx.entity_id, ctx.ticker) == ("ent_s", "005930")
    assert (ctx.novelty_status, ctx.title) == ("UNKNOWN", "")  # 종전 폴백 유지
    assert ctx.predicate_code is None and ctx.lifecycle_stage is None
    assert ctx.lead_text is None  # 스니펫 백필 전에도 동작한다
    assert ctx.arguments[0].slot is None
    assert ctx.measures == ()


def test_fetch_without_matching_events_skips_detail_queries():
    conn = _EventFetchConn([])

    assert EventStore(conn).fetch_event_contexts(date(2026, 7, 16), ["005930"]) == []

    assert len(conn.executed) == 1  # 사건이 없으면 참여자·측정 쿼리를 던지지 않는다
    assert conn.executed[0][1] == ("TITLE", "2026-07-16", ["005930"])  # holdings 접지 필터
