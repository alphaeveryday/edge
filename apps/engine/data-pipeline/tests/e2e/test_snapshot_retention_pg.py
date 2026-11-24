"""분석 내부 스냅샷 보존 규칙을 실 PostgreSQL(Flyway 로 적용된 실제 스키마)에서 고정한다.

지우면 안 되는 행을 지우면 다음 분석이나 재실행이 이어받을 조사 상태·뉴스 기준선을 잃는다. 그러면 load_research 는
오류 없이 지난 감사 기록을 다시 모아 상태를 부풀리고, read_snapshot 은 기준선 없음으로 답한다. 지워야 할 행을 남기면
DB 가 분석마다 수 MB 씩 분다. 시험은 자기 ETF 코드로만 범위를 좁히고 끝나면 자기 행을 지운다.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

OLD = datetime.now(timezone.utc) - timedelta(days=10)
YOUNG = datetime.now(timezone.utc) - timedelta(days=2)


def _conn():
    import psycopg

    return psycopg.connect(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]), dbname="edge",
                           user="edge", password="edge", autocommit=True)


class Chain:
    def __init__(self, conn):
        self.conn, self.etf, self.rows, self.analyses = conn, "T" + uuid.uuid4().hex[:6], [], []
        for fn in ("record_research_state", "capture_news_snapshot", "list_events"):
            conn.execute("""INSERT INTO tool_definitions (tool_id, function_name, version, description)
                VALUES (%s, %s, 'v1', 'e2e') ON CONFLICT DO NOTHING""", (fn + ":v1", fn))

    def outlook(self, status, at, previous=None, source="database", published=True):
        aid = uuid.uuid4().hex
        self.conn.execute("""INSERT INTO outlook_analyses (analysis_id, etf_code, analysis_at, previous_analysis_id,
            status, published_at, data_source) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                          (aid, self.etf, at, previous, status, at if status == "completed" and published else None, source))
        self.analyses.append(("outlook", aid))
        return aid

    def movement(self, status, at, trading_date, published=True):
        aid = uuid.uuid4().hex
        self.conn.execute("""INSERT INTO movement_analyses (analysis_id, etf_code, analysis_at, status, published_at,
            trading_date, data_source) VALUES (%s, %s, %s, %s, %s, %s, 'database')""",
                          (aid, self.etf, at, status, at if status == "completed" and published else None, trading_date))
        self.analyses.append(("movement", aid))
        return aid

    def run(self, fn, analysis, started=OLD, kind="outlook"):
        rid = uuid.uuid4().hex
        self.conn.execute(f"""INSERT INTO tool_runs (tool_run_id, tool_id, {kind}_analysis_id, output, status,
            started_at, finished_at) VALUES (%s, %s, %s, '{{"result": "x"}}', 'completed', %s, %s)""",
                          (rid, fn + ":v1", analysis, started, started))
        self.rows.append(rid)
        return rid

    def left(self):
        return {r for (r,) in self.conn.execute("SELECT tool_run_id FROM tool_runs WHERE tool_run_id = ANY(%s)",
                                                (self.rows,))}

    def retain(self, mode="run"):
        from data_pipeline.ops.snapshot_retention import DiskGuard, run

        return run(self.conn, mode=mode, age="7 days", etf_codes=[self.etf],
                   guard=DiskGuard(lambda: None, pace_bytes=0, min_bytes=0))

    def clean(self):
        self.conn.execute("DELETE FROM outlook_conclusion_keywords WHERE analysis_id IN "
                          "(SELECT analysis_id FROM outlook_analyses WHERE etf_code = %s)", (self.etf,))
        self.conn.execute("DELETE FROM tool_runs WHERE tool_run_id = ANY(%s)", (self.rows,))
        for kind, aid in reversed(self.analyses):
            self.conn.execute(f"DELETE FROM {kind}_analyses WHERE analysis_id = %s", (aid,))


@pytest.fixture
def chain():
    with _conn() as conn:
        chain = Chain(conn)
        yield chain
        chain.clean()


def test_only_superseded_snapshots_are_deleted(chain):
    first = chain.outlook("completed", OLD - timedelta(days=3))
    second = chain.outlook("completed", OLD - timedelta(days=2), first)
    third = chain.outlook("completed", OLD - timedelta(days=1), second)
    latest = chain.outlook("completed", OLD, third)
    failed = chain.outlook("failed", OLD + timedelta(minutes=30), latest)
    young_parent = chain.outlook("failed", YOUNG, latest)

    gone = {chain.run("record_research_state", first), chain.run("capture_news_snapshot", first),
            chain.run("record_research_state", second), chain.run("capture_news_snapshot", failed)}
    kept = {
        chain.run("record_research_state", latest),       # 다음 분석이 이어받는 상태
        chain.run("capture_news_snapshot", latest),       # 다음 전망의 뉴스 기준선
        chain.run("record_research_state", third),        # 최신 건을 같은 분석 시각으로 다시 돌리면 이 행을 읽는다
        chain.run("capture_news_snapshot", young_parent, YOUNG),  # 7일 안
        chain.run("list_events", first),                  # 스냅샷이 아닌 도구 원문(근거가 될 수 있음)
    }
    cited = chain.run("capture_news_snapshot", second)
    chain.conn.execute("""INSERT INTO outlook_conclusion_keywords (row_id, analysis_id, kind, position, label, tool_run_ids)
        VALUES (%s, %s, 'support', 0, 'e2e', %s)""", (uuid.uuid4().hex, second, [cited]))
    kept.add(cited)

    assert chain.retain("plan")["candidates"] == len(gone)
    assert chain.left() == gone | kept  # plan 은 읽기만 한다
    assert chain.retain()["deleted"] == len(gone)
    assert chain.left() == kept


def test_predecessor_of_a_running_analysis_survives(chain):
    old = chain.outlook("completed", OLD - timedelta(days=3))
    newer = chain.outlook("completed", OLD - timedelta(days=2), old)
    newest = chain.outlook("completed", OLD - timedelta(days=1), newer)
    chain.outlook("running", OLD, old)  # 대시보드 재실행처럼 오래된 분석을 직전으로 고른 실행
    state = chain.run("record_research_state", old)
    chain.run("record_research_state", newer)
    chain.run("record_research_state", newest)
    chain.retain()
    assert state in chain.left()


def test_latest_snapshot_of_each_kind_survives_when_the_newest_analyses_lack_it(chain):
    # load_research 는 스냅샷 없는 부모를 건너 거슬러 올라간다 — 최신 completed 분석들에 상태 행이 없으면
    # 그 앞 분석의 상태 행이 다음 분석의 출발점이다.
    older = chain.outlook("completed", OLD - timedelta(days=2))
    middle = chain.outlook("completed", OLD - timedelta(days=1), older)
    chain.outlook("completed", OLD, middle)
    state = chain.run("record_research_state", older)
    chain.retain()
    assert state in chain.left()


def test_chains_are_separated_by_data_source_and_by_movement_trading_day(chain):
    synthetic = chain.outlook("completed", OLD - timedelta(days=5), source="synthetic")
    for days in (2, 1, 0):
        chain.outlook("completed", OLD - timedelta(days=days))
    day1, day2 = (OLD - timedelta(days=1)).date(), OLD.date()
    old_same_day = chain.movement("completed", OLD - timedelta(days=1, hours=3), day1)
    second_of_day1 = chain.movement("completed", OLD - timedelta(days=1, hours=2), day1)
    last_of_day1 = chain.movement("completed", OLD - timedelta(days=1), day1)
    last_of_day2 = chain.movement("completed", OLD, day2)
    kept = {chain.run("record_research_state", synthetic),
            chain.run("record_research_state", second_of_day1, kind="movement"),
            chain.run("record_research_state", last_of_day1, kind="movement"),
            chain.run("record_research_state", last_of_day2, kind="movement")}
    gone = chain.run("record_research_state", old_same_day, kind="movement")
    chain.retain()
    assert chain.left() == kept and gone not in chain.left()


def test_order_matches_the_engine_when_an_unpublished_completed_analysis_ties(chain):
    # 선정 항목이 없는 가격변동은 completed 이지만 published_at 이 NULL 이다. 엔진(service._previous)은
    # published_at DESC(NULL 먼저)로 고르므로 같은 시각이면 그 분석을 직전으로 삼는다.
    day = OLD.date()
    chain.movement("completed", OLD - timedelta(hours=3), day)
    tied = [chain.movement("completed", OLD, day), chain.movement("completed", OLD, day)]
    unpublished = chain.movement("completed", OLD, day, published=False)
    kept = chain.run("record_research_state", unpublished, kind="movement")
    for analysis in tied:
        chain.run("record_research_state", analysis, kind="movement")
    chain.retain()
    assert kept in chain.left()
