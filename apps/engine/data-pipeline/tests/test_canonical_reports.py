"""canonical(Iceberg) 규약 검사 — **DDL·MERGE 를 망 없이 고정한다.**

Athena 를 타는 테스트는 느리고 돈이 든다. 그런데 정작 틀리기 쉬운 것은 SQL 의 모양이다 -
파티션 변환을 빼먹거나, MERGE 매칭 키가 어긋나거나, 스테이징 스키마가 백필이 쓴 필드와
갈리는 것. 그건 문자열 검사로 잡힌다. 실행은 실연결로 한 번 확인한다.
"""

from __future__ import annotations

from data_pipeline.backfill.classification import ReportClass
from data_pipeline.canonical.athena import Athena, AthenaError
from data_pipeline.canonical.reports import (
    STAGING_FIELDS,
    merge_sql,
    staging_ddl,
    staging_name,
)
from data_pipeline.canonical.tables import (
    DB_DRAFT,
    REPORT_CURRENT,
    as_of_sql,
    latest_view,
)


class FakeAthena:
    """start→poll→results 를 흉내내는 최소 클라이언트."""

    def __init__(self, state="SUCCEEDED", rows=None, reason=""):
        self.state, self.reason = state, reason
        self.rows = rows or [{"Data": [{"VarCharValue": "cnt"}]},
                             {"Data": [{"VarCharValue": "7"}]}]
        self.sql: list[str] = []

    def start_query_execution(self, **kw):
        self.sql.append(kw["QueryString"])
        return {"QueryExecutionId": f"q{len(self.sql)}"}

    def get_query_execution(self, QueryExecutionId):  # noqa: N803
        return {"QueryExecution": {"Status": {"State": self.state,
                                             "StateChangeReason": self.reason},
                                   "Statistics": {"DataScannedInBytes": 11}}}

    def get_query_results(self, **kw):
        return {"ResultSet": {"Rows": self.rows}}


def test_a_failed_query_raises_instead_of_looking_successful():
    """start_query_execution 은 비동기다 - 폴링을 빼먹으면 실패가 성공처럼 보인다.

    DDL 이 조용히 실패하면 "테이블이 없는데 있다고 믿는" 상태가 되고, 그 뒤 MERGE 가
    엉뚱한 사유로 죽어 원인을 찾는 데 시간이 든다.
    """
    ath = Athena(client=FakeAthena(state="FAILED", reason="이유"), poll=0)

    try:
        ath.run("SELECT 1")
    except AthenaError as exc:
        assert "FAILED" in str(exc) and "이유" in str(exc)
        assert "SELECT 1" in str(exc)      # 어느 SQL 이었는지 붙는다
    else:
        raise AssertionError("실패를 삼켰다")


def test_scanned_bytes_accumulate_because_athena_bills_by_scan():
    """파티션이 잘못 잡혀 전량 스캔하는 질의를 청구서로 알게 되면 늦다."""
    ath = Athena(client=FakeAthena(), poll=0)

    ath.run("SELECT 1")
    ath.run("SELECT 2")

    assert ath.scanned == 22


def test_the_table_is_partitioned_by_month_not_day():
    """일 파티션이면 하루 85건 → 85행 파일이 수천 개 생기고 메타데이터가 조회를 잡아먹는다."""
    ddl = REPORT_CURRENT.ddl(DB_DRAFT, prefix="draft")

    assert "PARTITIONED BY (month(available_at), geo)" in ddl
    assert "'table_type'='ICEBERG'" in ddl and "'format'='parquet'" in ddl
    assert "write_target_data_file_size_bytes" in ddl


def test_draft_isolation_is_a_separate_database_not_a_prefix():
    """**Iceberg 는 접두사 격리가 안 통한다** - location 이 카탈로그에 박힌다.

    raw 는 draft/ 접두사로 갈랐지만 canonical 은 DB 를 갈라야 한다. 같은 테이블에 접두사만
    다른 데이터를 넣을 수 없으므로, 접두사만 믿으면 draft 가 프로덕션 테이블에 섞인다.
    """
    ddl = REPORT_CURRENT.ddl(DB_DRAFT, prefix="draft")

    assert f"{DB_DRAFT}.report_current" in ddl
    assert "s3://edge-dev-pipeline-lake/draft/canonical/reports/report_current" in ddl
    assert REPORT_CURRENT.location(prefix="") == (
        "s3://edge-dev-pipeline-lake/canonical/reports/report_current")


def test_staging_schema_follows_the_classification_axes():
    """스테이징 필드를 손으로 나열하면 축을 추가할 때 어긋나고, 어긋나면 조용히 NULL 이 된다."""
    axes = ReportClass(kind="current", source_class="GOV").as_columns()

    for axis in axes:
        assert axis in STAGING_FIELDS, f"{axis} 축이 스테이징에서 빠졌다"
    assert "report_id" in STAGING_FIELDS and "fetched_at" in STAGING_FIELDS


def test_staging_reads_everything_as_string():
    """raw 는 무변형이라 형식이 조금 다른 행이 섞인다 - 타입을 강제하면 파티션째로 실패한다."""
    ddl = staging_ddl(DB_DRAFT, "stg_x", "s3://b/p")

    assert "JsonSerDe" in ddl and "LOCATION 's3://b/p/'" in ddl
    assert " date" not in ddl and " timestamp" not in ddl
    assert ddl.count(" string") == len(STAGING_FIELDS)


def test_staging_name_is_scoped_to_the_run():
    """두 run 의 적재가 겹쳐 돌 때 이름이 같으면 한쪽이 다른 쪽 location 을 가리킨다."""
    assert staging_name("backfill-reports-korea_kr-20260731") == (
        "stg_backfill_reports_korea_kr_20260731")
    assert staging_name("A-b.c/d") == "stg_a_b_c_d"


def test_merge_matches_on_identity_and_prunes_partitions():
    """정체는 (report_id, content_hash, available_at) 다.

    content_hash 가 매칭 키에 있어야 **내용이 바뀌면 새 행**이 된다(append-only).
    available_at 이 있어야 MERGE 가 대상 전체를 훑지 않는다 - 없으면 스캔 비용이 테이블
    크기에 비례해 늘고, 그 비용은 백필처럼 여러 번 돌릴 때 곱해진다.
    """
    sql = merge_sql(DB_DRAFT, "stg_x", run_id="r1", ingest_date="2026-07-31")

    assert "t.report_id = s.report_id" in sql
    assert "t.content_hash = s.content_hash" in sql
    assert "t.available_at = s.available_at" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql
    assert "WHEN MATCHED" not in sql, "갱신 경로가 생기면 옛값을 잃는 경로도 생긴다"


def test_merge_dedupes_the_source_or_trino_dies():
    """같은 정체가 원본에 두 번 있으면 Trino MERGE 가 multiple rows matched 로 죽는다.

    raw 는 무변형 append 라 재수집 시 중복이 생길 수 있다 - 소스에서 하나만 남겨야 한다.
    """
    sql = merge_sql(DB_DRAFT, "stg_x", run_id="r1", ingest_date="2026-07-31")

    assert "row_number() OVER" in sql and "rn = 1" in sql
    assert "PARTITION BY report_id, content_hash, available_at" in sql


def test_merge_records_which_raw_run_it_came_from():
    """계보가 없으면 재현이 불가능하다 - 어느 run 을 다시 돌려야 하는지 모른다."""
    sql = merge_sql(DB_DRAFT, "stg_x", run_id="r1", ingest_date="2026-07-31")

    assert "'r1' AS src_run_id" in sql and "'2026-07-31' AS src_ingest_date" in sql


def test_current_value_is_decided_by_the_query_not_the_storage():
    """저장이 판정하면 옛값을 지우는 경로가 필요해지고, 그 경로가 PIT 를 깬다.

    창 키는 **정체성에서 파생**한다(`identity` - `content_hash`). 뷰가 키를 손으로 다시
    쓰면 저장과 조회가 갈린다 - `report_current` 는 같은 공시가 다른 as-of 로 다시 들어오는
    것을 별 행으로 보므로 `available_at` 이 정체성에 있고, 그것을 창에서 빼면 옛 as-of
    상태가 조용히 접힌다.
    """
    view = latest_view(REPORT_CURRENT, DB_DRAFT)

    assert "CREATE OR REPLACE VIEW" in view and "report_current_latest" in view
    keys = ", ".join(k for k in REPORT_CURRENT.identity if k != "content_hash")
    assert f"PARTITION BY {keys} ORDER BY fetched_at DESC" in view
    assert "available_at" in keys, "as-of 를 창에서 빼면 정정 이력이 접힌다"


def test_as_of_closes_the_window_at_fetched_at():
    """사후에 들어온 정정본이 섞이면 조용히 미래를 본다 - 백테스트가 실제보다 좋아진다."""
    sql = as_of_sql(REPORT_CURRENT, DB_DRAFT, "2026-07-15 00:00:00")

    assert "fetched_at <= TIMESTAMP '2026-07-15 00:00:00'" in sql
    assert "ORDER BY fetched_at DESC" in sql
