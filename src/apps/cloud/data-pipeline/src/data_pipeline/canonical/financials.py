"""raw 재무 ndjson → `statement_line` Iceberg MERGE.

보고서(`canonical/reports.py`)와 같은 3단(대상 보장 → 임시 외부 테이블 → MERGE)이지만
가운데에 **펴기(unpivot)** 가 들어간다. 원본 한 행에 금액이 6개 열로 흩어져 있어서다.

    thstrm_amount        → THSTRM    · POINT
    thstrm_add_amount    → THSTRM    · CUMULATIVE
    frmtrm_amount        → FRMTRM    · POINT
    frmtrm_q_amount      → FRMTRM    · QUARTER
    frmtrm_add_amount    → FRMTRM    · CUMULATIVE
    bfefrmtrm_amount     → BFEFRMTRM · POINT

빈 금액은 행을 만들지 않는다 - 없는 값과 0 을 구분해야 하고, NULL 행을 쌓으면 파일만 커진다.

**금액을 검사 없이 캐스팅하지 않는다.** `try_cast` 로 바꾸고 원문(`amount_text`)을 남겨,
파싱 실패가 `amount IS NULL AND amount_text <> ''` 로 드러나게 한다. 실패를 조용히 버리면
어느 계정이 빠졌는지 사후에 알 수 없다.
"""

from __future__ import annotations

import logging

from ..backfill.financial import DATASET, FOLDER, MARKET, SOURCE  # noqa: F401
from ..lake import raw_financial_partition
from .athena import Athena
from .reports import staging_name
from .spine import REPRT_PERIOD
from .tables import BUCKET, STATEMENT_LINE, Table

logger = logging.getLogger(__name__)

# 백필이 낸 32열. 손으로 나열하는 이유는 raw 가 **계약 경계**라서다 - 열이 사라지면
# 조용히 NULL 이 되는 대신 스테이징 스키마와 어긋나 드러나야 한다.
STAGING_FIELDS = (
    "rcept_no", "reprt_code", "bsns_year", "corp_code", "sj_div", "sj_nm",
    "account_id", "account_nm", "account_detail", "ord", "currency",
    "thstrm_nm", "thstrm_amount", "thstrm_add_amount",
    "frmtrm_nm", "frmtrm_amount", "frmtrm_q_nm", "frmtrm_q_amount",
    "frmtrm_add_amount", "bfefrmtrm_nm", "bfefrmtrm_amount",
    "fs_div", "fs_nm", "stock_code", "corp_name", "reprt_nm", "collect_status",
    "our_ticker", "market", "fetched_at", "backfill_source", "backfill_oid",
)

# (period_kind, amount_kind, 기간표기 열, 금액 열)
MEASURES = (
    ("THSTRM", "POINT", "thstrm_nm", "thstrm_amount"),
    ("THSTRM", "CUMULATIVE", "thstrm_nm", "thstrm_add_amount"),
    ("FRMTRM", "POINT", "frmtrm_nm", "frmtrm_amount"),
    ("FRMTRM", "QUARTER", "frmtrm_q_nm", "frmtrm_q_amount"),
    ("FRMTRM", "CUMULATIVE", "frmtrm_nm", "frmtrm_add_amount"),
    ("BFEFRMTRM", "POINT", "bfefrmtrm_nm", "bfefrmtrm_amount"),
)


def staging_ddl(database: str, name: str, location: str) -> str:
    """raw ndjson 위의 외부 테이블. 전부 string 으로 읽는다(형 변환은 MERGE 에서)."""
    cols = ",\n  ".join(f"{f} string" for f in STAGING_FIELDS)
    return (f"CREATE EXTERNAL TABLE IF NOT EXISTS {database}.{name} (\n  {cols}\n)\n"
            f"ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'\n"
            f"LOCATION '{location}/'\n"
            f"TBLPROPERTIES ('has_encrypted_data'='false', "
            f"'ignore.malformed.json'='false')")


def unpivot_sql(database: str, staging: str, *, run_id: str,
                ingest_date: str) -> str:
    """금액 6열을 행으로 펴는 SELECT.

    `VALUES` 로 (기간, 성격) 6쌍을 만들어 교차조인하고, 어느 원본 열을 볼지는 `CASE` 가
    고른다. 그래서 **스테이징을 한 번만 읽는다** - UNION ALL 6개면 6번 읽고, Athena 는
    스캔으로 과금한다.

    `UNNEST(ARRAY[ROW(...)])` 를 먼저 시도했다가 버렸다. Trino 문서상 ROW 배열은 컬럼으로
    펼쳐지지만 **Athena engine v3 는 한 컬럼으로 넘긴다**("Column alias list has 4 entries
    but 'm' has 1 columns available", 타입을 명시해도 같음 — 실측). VALUES 는 어디서나 돈다.
    """
    pairs = ", ".join(f"('{pk}', '{ak}')" for pk, ak, _, _ in MEASURES)
    period = "\n           ".join(
        f"WHEN reprt_code = '{code}' THEN bsns_year || '-{suffix}'"
        for code, (_, suffix) in REPRT_PERIOD.items())
    ptype = "\n           ".join(
        f"WHEN reprt_code = '{code}' THEN '{kind}'"
        for code, (kind, _) in REPRT_PERIOD.items())

    def case(idx: int) -> str:
        arms = "\n           ".join(
            f"WHEN m.period_kind = '{m[0]}' AND m.amount_kind = '{m[1]}' THEN {m[idx]}"
            for m in MEASURES)
        return f"CASE\n           {arms}\n         END"

    return f"""SELECT
      corp_code, corp_name,
      fs_div, fs_nm, sj_div, sj_nm,
      account_id, account_nm, account_detail,
      try_cast(ord AS integer) AS ord,
      m.period_kind, m.amount_kind,
      {case(2)} AS period_label,
      try_cast(replace({case(3)}, ',', '') AS decimal(38,6)) AS amount,
      {case(3)} AS amount_text,
      currency, bsns_year, reprt_code, reprt_nm, rcept_no,
      our_ticker AS entity,
      market AS geo,
      CAST(date_parse(substr(rcept_no, 1, 8), '%Y%m%d') AS date) AS available_at,
      CAST(from_iso8601_timestamp(fetched_at) AS timestamp) AS fetched_at,
      CASE
           {period}
         ELSE bsns_year || '-FY' END AS period_key,
      CASE
           {ptype}
         ELSE 'FY' END AS period_type,
      '{SOURCE}' AS source,
      '{run_id}' AS src_run_id,
      '{ingest_date}' AS src_ingest_date
    FROM {database}.{staging}
    CROSS JOIN (VALUES {pairs}) AS m(period_kind, amount_kind)
    WHERE rcept_no IS NOT NULL
      AND {case(3)} IS NOT NULL AND {case(3)} <> ''"""


def merge_sql(database: str, staging: str, *, run_id: str, ingest_date: str,
              table: Table = STATEMENT_LINE) -> str:
    """정체가 같으면 넣지 않는다. `bsns_year` 를 매칭 키에 넣어 파티션을 가지치기한다."""
    src = unpivot_sql(database, staging, run_id=run_id, ingest_date=ingest_date)
    cols = table.column_names()
    on = " AND ".join(f"t.{k} = s.{k}" for k in table.identity)
    return f"""MERGE INTO {database}.{table.name} t
USING (
  SELECT * FROM (
    SELECT *, row_number() OVER (
      PARTITION BY {', '.join(table.identity)} ORDER BY fetched_at) AS rn
    FROM ({src})
  ) WHERE rn = 1
) s
ON {on}
  AND t.bsns_year = s.bsns_year
  AND t.entity = s.entity
WHEN NOT MATCHED THEN INSERT ({', '.join(cols)})
  VALUES ({', '.join('s.' + c for c in cols)})"""


def merge_statement_line(ath: Athena, *, run_id: str, ingest_date: str,
                         database: str, bucket: str = BUCKET, prefix: str = "",
                         keep_staging: bool = False) -> dict:
    """raw 재무 한 run 을 canonical 로 밀어넣는다. 멱등이다."""
    part = raw_financial_partition(SOURCE, MARKET, ingest_date, run_id)
    location = f"s3://{bucket}/{prefix.rstrip('/') + '/' if prefix else ''}{part}"
    stg = staging_name(run_id + "-fin")
    tbl = STATEMENT_LINE

    ath.run(f"CREATE DATABASE IF NOT EXISTS {database}")
    ath.run(tbl.ddl(database, bucket=bucket, prefix=prefix))
    ath.run(f"DROP TABLE IF EXISTS {database}.{stg}")
    ath.run(staging_ddl(database, stg, location))
    staged = ath.scalar(f"SELECT count(*) FROM {database}.{stg}")
    before = ath.scalar(f"SELECT count(*) FROM {database}.{tbl.name}")
    ath.run(merge_sql(database, stg, run_id=run_id, ingest_date=ingest_date))
    after = ath.scalar(f"SELECT count(*) FROM {database}.{tbl.name}")
    unparsed = ath.scalar(f"SELECT count(*) FROM {database}.{tbl.name} "
                          f"WHERE amount IS NULL AND amount_text <> ''")
    if not keep_staging:
        ath.run(f"DROP TABLE IF EXISTS {database}.{stg}")

    out = {"table": f"{database}.{tbl.name}", "location": tbl.location(bucket, prefix),
           "staged_raw_rows": int(staged or 0), "before": int(before or 0),
           "after": int(after or 0), "inserted": int(after or 0) - int(before or 0),
           "unparsed_amounts": int(unparsed or 0),
           "src": location, "scanned_bytes": ath.scanned}
    logger.info("canonical MERGE %s", out)
    return out
