"""raw ndjson → `report_current` Iceberg MERGE.

경로는 셋이다.

    1. 대상 테이블 보장 (CREATE TABLE IF NOT EXISTS)
    2. **임시 외부 테이블**을 raw run 접두사에 걸고 (JsonSerDe)
    3. MERGE 로 밀어넣은 뒤 임시 테이블을 버린다

파이썬으로 ndjson 을 읽어 parquet 로 다시 쓰지 않는다 - 데이터가 로컬을 왕복할 이유가 없고,
왕복하면 "레이크에 있는 것"과 "파이썬이 본 것"이 갈릴 수 있다. 변환은 S3 안에서 SQL 로 끝난다.

임시 테이블 이름에 run_id 를 넣는다. 두 백필 run 의 적재가 겹쳐 돌아도 서로의 스테이징을
덮지 않는다 - 이름이 같으면 한쪽이 다른 쪽의 location 을 가리키게 되고, 그 사고는 조용하다.

**스테이징 중복 제거가 필수다.** 같은 정체(report_id, content_hash, available_at)가 원본에
두 번 있으면 Trino MERGE 가 "multiple rows matched" 로 죽는다. raw 는 무변형 append 라
재수집 시 중복이 생길 수 있으므로, 소스 쪽에서 row_number 로 하나만 남긴다.
"""

from __future__ import annotations

import logging
import re

from ..backfill.classification import ReportClass
from ..backfill.reports import KOREA_KR
from ..backfill.reports import SOURCE as KOREA_SOURCE
from ..lake import raw_report_partition
from .athena import Athena
from .tables import BUCKET, REPORT_CURRENT, Table, latest_view

logger = logging.getLogger(__name__)

# 백필이 쓴 레코드의 고정 필드. 분류축은 ReportClass 에서 유도한다 - 축을 추가하면 스테이징
# 스키마가 자동으로 따라온다(둘을 손으로 맞추면 언젠가 어긋나고, 어긋나면 조용히 NULL 이 된다).
_FIXED = ("report_id", "source_id", "published_at", "available_at",
          "title", "url", "source", "fetched_at")
_CLASS_FIELDS = tuple(ReportClass(kind="current", source_class="GOV").as_columns())
STAGING_FIELDS = _FIXED + tuple(f for f in _CLASS_FIELDS if f not in _FIXED)

_SAFE = re.compile(r"[^0-9a-z_]+")


def staging_name(run_id: str) -> str:
    """Glue 테이블 이름은 소문자·숫자·밑줄만 안전하다."""
    return "stg_" + _SAFE.sub("_", run_id.lower()).strip("_")


def staging_ddl(database: str, name: str, location: str) -> str:
    """raw ndjson 위의 외부 테이블. **전부 string 으로 읽는다.**

    raw 는 무변형이 원칙이라 날짜·시각이 문자열로 들어 있다. 여기서 타입을 강제하면 형식이
    조금 다른 행 하나가 파티션 전체를 실패시킨다. 형 변환은 MERGE 의 SELECT 에서 한다 -
    실패해도 어느 열이 문제인지 드러난다.
    """
    cols = ",\n  ".join(f"{f} string" for f in STAGING_FIELDS)
    return (f"CREATE EXTERNAL TABLE IF NOT EXISTS {database}.{name} (\n  {cols}\n)\n"
            f"ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'\n"
            f"LOCATION '{location}/'\n"
            f"TBLPROPERTIES ('has_encrypted_data'='false', "
            f"'ignore.malformed.json'='false')")


def merge_sql(database: str, staging: str, *, run_id: str, ingest_date: str,
              table: Table = REPORT_CURRENT) -> str:
    """MERGE 한 문장. 정체가 같으면 넣지 않고, 다르면 새 행이다(append-only).

    `available_at` 을 매칭 키에 넣는 것은 **파티션 가지치기** 때문이다. 없으면 MERGE 가
    대상 전체를 훑어 스캔 비용이 테이블 크기에 비례한다. 발표일이 정체의 일부인 것은
    보고서에서 자연스럽다 - 같은 문서가 다른 날 발표되는 일은 없다.
    """
    src = f"""SELECT
      report_id,
      to_hex(sha256(to_utf8(coalesce(title, '') || '|' || coalesce(url, '')))) AS content_hash,
      '' AS entity,
      geo,
      CAST(available_at AS date) AS available_at,
      CAST(from_iso8601_timestamp(fetched_at) AS timestamp) AS fetched_at,
      'POINT' AS period_key,
      'POINT' AS period_type,
      source, '{run_id}' AS src_run_id, '{ingest_date}' AS src_ingest_date,
      kind, report_type, section,
      source_class, reliability, credibility,
      unit, cadence, region, sector, domain, horizon,
      title, url, license,
      '' AS body_ref
    FROM {database}.{staging}
    WHERE report_id IS NOT NULL AND available_at IS NOT NULL"""
    cols = table.column_names()
    insert_cols = ", ".join(cols)
    insert_vals = ", ".join(f"s.{c}" for c in cols)
    return f"""MERGE INTO {database}.{table.name} t
USING (
  SELECT * FROM (
    SELECT *, row_number() OVER (
      PARTITION BY report_id, content_hash, available_at
      ORDER BY fetched_at) AS rn
    FROM ({src})
  ) WHERE rn = 1
) s
ON t.report_id = s.report_id
  AND t.content_hash = s.content_hash
  AND t.available_at = s.available_at
WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"""


def merge_report_current(ath: Athena, *, run_id: str, ingest_date: str,
                         database: str, bucket: str = BUCKET, prefix: str = "",
                         source: str = KOREA_SOURCE, geo: str = "",
                         keep_staging: bool = False) -> dict:
    """raw 한 run 을 canonical 로 밀어넣는다. 멱등이다 - 두 번 돌려도 행이 늘지 않는다."""
    geo = geo or KOREA_KR.geo
    part = raw_report_partition(source, geo, ingest_date, run_id)
    location = f"s3://{bucket}/{prefix.rstrip('/') + '/' if prefix else ''}{part}"
    stg = staging_name(run_id)
    tbl = REPORT_CURRENT

    ath.run(f"CREATE DATABASE IF NOT EXISTS {database}")
    ath.run(tbl.ddl(database, bucket=bucket, prefix=prefix))
    ath.run(f"DROP TABLE IF EXISTS {database}.{stg}")
    ath.run(staging_ddl(database, stg, location))
    staged = ath.scalar(f"SELECT count(*) FROM {database}.{stg}")
    before = ath.scalar(f"SELECT count(*) FROM {database}.{tbl.name}")
    ath.run(merge_sql(database, stg, run_id=run_id, ingest_date=ingest_date))
    after = ath.scalar(f"SELECT count(*) FROM {database}.{tbl.name}")
    ath.run(latest_view(tbl, database))
    if not keep_staging:
        ath.run(f"DROP TABLE IF EXISTS {database}.{stg}")

    out = {"table": f"{database}.{tbl.name}", "location": tbl.location(bucket, prefix),
           "staged": int(staged or 0), "before": int(before or 0),
           "after": int(after or 0),
           "inserted": int(after or 0) - int(before or 0),
           "src": location, "scanned_bytes": ath.scanned}
    logger.info("canonical MERGE %s", out)
    return out
