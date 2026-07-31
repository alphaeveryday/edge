"""재무 canonical 규약 검사 — **펴기와 파싱 실패 보존을 문자열로 고정한다.**

여기서 지키는 것은 셋이다.

    펴기      금액 6열이 행으로 갈라진다. 안 펴면 다른 공시의 당기와 전기를 맞대는 사고가
              나고, 숫자가 나오므로 조용하다
    보존      파싱 실패를 버리지 않는다(`amount_text` 를 남긴다). 버리면 어느 계정이
              빠졌는지 사후에 알 수 없다
    정체      (rcept_no, fs_div, sj_div, ord, period_kind, amount_kind) 가 한 행을 가른다
"""

from __future__ import annotations

from data_pipeline.canonical.financials import (
    MEASURES,
    STAGING_FIELDS,
    merge_sql,
    staging_ddl,
    unpivot_sql,
)
from data_pipeline.canonical.tables import DB_DRAFT, STATEMENT_LINE, latest_view


def test_every_amount_column_becomes_its_own_row():
    """원본 한 행에 금액이 6열이다. 하나라도 빠지면 그 기간·성격이 통째로 사라진다."""
    got = {(pk, ak) for pk, ak, _, _ in MEASURES}

    assert got == {("THSTRM", "POINT"), ("THSTRM", "CUMULATIVE"),
                   ("FRMTRM", "POINT"), ("FRMTRM", "QUARTER"),
                   ("FRMTRM", "CUMULATIVE"), ("BFEFRMTRM", "POINT")}
    cols = {amt for _, _, _, amt in MEASURES}
    assert cols <= set(STAGING_FIELDS), "펴는 대상 열이 스테이징에 없다"
    labels = {lbl for _, _, lbl, _ in MEASURES}
    assert labels <= set(STAGING_FIELDS)


def test_unpivot_reads_staging_once():
    """UNION ALL 6개로 하면 스테이징을 6번 읽는다 - Athena 는 스캔으로 과금한다."""
    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert sql.count(f"FROM {DB_DRAFT}.stg_x") == 1
    assert "CROSS JOIN (VALUES " in sql
    assert sql.count("('THSTRM', 'POINT')") == 1


def test_values_join_not_unnest_of_rows():
    """`UNNEST(ARRAY[ROW(...)])` 는 Athena engine v3 에서 한 컬럼으로 넘어온다(실측).

    Trino 문서상으로는 펼쳐져야 하지만 실제로는 alias 개수 불일치로 죽었다 - 타입을
    명시해도 같았다. VALUES 교차조인 + CASE 는 어디서나 돈다.
    """
    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert "UNNEST" not in sql
    assert "AS m(period_kind, amount_kind)" in sql
    # 6쌍 전부가 CASE 팔로 들어간다 - 하나 빠지면 그 기간이 NULL 이 된다.
    for pk, ak, _, amt in MEASURES:
        assert f"WHEN m.period_kind = '{pk}' AND m.amount_kind = '{ak}' THEN {amt}" in sql


def test_empty_amounts_do_not_become_rows():
    """없는 값과 0 은 다르다. NULL 행을 쌓으면 파일만 커지고 집계가 흐려진다."""
    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert "IS NOT NULL AND" in sql and "<> ''" in sql
    assert "WHERE rcept_no IS NOT NULL" in sql


def test_amount_parsing_failures_are_kept_not_dropped():
    """`try_cast` + 원문 보존이라야 `amount IS NULL AND amount_text <> ''` 로 드러난다."""
    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert "try_cast(replace(" in sql and "AS decimal(38,6)) AS amount" in sql
    assert "AS amount_text" in sql          # 원문도 함께 실린다
    assert "amount_text" in STATEMENT_LINE.column_names()


def test_amount_is_decimal_because_per_share_figures_are_fractional():
    """bigint 로 잡았더니 `기본주당이익(손실) = 0.33` 이 파싱에 실패했다(실측).

    double 도 아니다 - 재무 수치를 부동소수로 담으면 합계가 미세하게 어긋나고 그 차이를
    사후에 설명할 수 없다. 조 단위(1e12)를 담으면서 소수도 되는 decimal 이어야 한다.
    """
    types = {c.name: c.type for c in STATEMENT_LINE.columns}

    assert types["amount"] == "decimal(38,6)"
    assert types["amount_text"] == "string"


def test_available_at_comes_from_the_receipt_number():
    """접수번호 앞 8자리가 접수일이다 - 재무에서 '언제 알 수 있었나'의 유일한 근거다."""
    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert "substr(rcept_no, 1, 8)" in sql and "AS available_at" in sql


def test_identity_includes_ord_because_accounts_repeat():
    """SCE 는 같은 account_id 가 축마다 반복된다. ord 가 없으면 행이 뭉개진다."""
    assert STATEMENT_LINE.identity == (
        "rcept_no", "fs_div", "sj_div", "ord", "period_kind", "amount_kind",
        "content_hash")

    sql = merge_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")
    for k in STATEMENT_LINE.identity:
        assert f"t.{k} = s.{k}" in sql
    assert "WHEN MATCHED" not in sql, "갱신 경로가 생기면 옛값을 잃는다"


def test_a_corrected_amount_is_a_new_row_not_a_skipped_merge():
    """정정공시로 **금액만** 바뀐 줄이 조용히 버려지면 canonical 이 옛값을 들고 있는다.

    WHY: 정체가 (접수번호·구분·순번·기간·성격) 뿐이면 정정본은 `WHEN NOT MATCHED` 에서
    걸러진다 - raw 는 새로 받았는데 canonical 은 갱신되지 않고, 그 불일치는 조회에서만
    드러난다. 금액 지문을 정체에 넣어 새 행으로 쌓고(append-only), "지금 값"은
    `latest_view` 가 fetched_at 으로 판정한다.
    """
    assert "content_hash" in STATEMENT_LINE.identity
    assert "content_hash" in STATEMENT_LINE.column_names()

    sql = unpivot_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")
    assert "AS content_hash" in sql and "sha256" in sql

    # 지금 값 판정에서는 지문을 뺀다 - 안 빼면 정정본과 원본이 둘 다 '지금 값'이 된다.
    keys = tuple(k for k in STATEMENT_LINE.identity if k != "content_hash")
    view = latest_view(STATEMENT_LINE, DB_DRAFT)
    assert f"PARTITION BY {', '.join(keys)} ORDER BY fetched_at DESC" in view


def test_merge_prunes_by_partition_keys():
    """파티션 키가 매칭에 없으면 MERGE 가 대상 전체를 훑고, 그 비용이 재실행마다 곱해진다."""
    sql = merge_sql(DB_DRAFT, "stg_x", run_id="r", ingest_date="d")

    assert "t.bsns_year = s.bsns_year" in sql
    assert "t.entity = s.entity" in sql
    assert "row_number() OVER" in sql and "rn = 1" in sql


def test_partitioned_by_year_and_bucketed_entity():
    """종목을 나열 파티션으로 두면 2,900개 × 연도로 폭발한다.

    `bucket` 은 **개수를 먼저** 받는다 - 순서를 바꿔 쓰면 Athena 가 DDL 단계에서
    "Cannot parse number from entity" 로 죽는다(실측으로 확인).
    """
    ddl = STATEMENT_LINE.ddl(DB_DRAFT, prefix="draft")

    assert "PARTITIONED BY (bsns_year, bucket(32, entity))" in ddl
    assert "canonical/financials/statement_line" in ddl
    assert "'table_type'='ICEBERG'" in ddl


def test_staging_covers_all_raw_columns():
    """raw 는 계약 경계다 - 열이 사라지면 조용히 NULL 이 되는 대신 어긋나 드러나야 한다."""
    ddl = staging_ddl(DB_DRAFT, "stg_x", "s3://b/p")

    assert len(STAGING_FIELDS) == 32
    assert ddl.count(" string") == 32
    for f in ("rcept_no", "our_ticker", "fs_div", "ord", "fetched_at",
              "backfill_oid"):
        assert f in STAGING_FIELDS
