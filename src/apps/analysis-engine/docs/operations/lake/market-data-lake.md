---
doc_type: design
status: Accepted
owner: platform
created: 2026-07-10
updated: 2026-07-11
related:
  - ../../README.md
  - ../../baseline/analysis-engine-design.md
---

# Market-data lake

PostgreSQL `market` 스키마를 S3 Iceberg 테이블로 내보내고, Glue/Athena 에서 조회하는 현재 운영 가이드다. 구현은 `src/alphamale/lake/` 패키지와 `ops/lake/setup_infra.sh` 가 소유한다.

## Layout

```text
s3://<lake-bucket>/<region>/<table>/
Glue databases: market_data_us | market_data_kr | market_data_common
Athena workgroup: market_data
```

## Prerequisites

- `uv sync`
- AWS profile with S3 / Glue / Athena / Secrets Manager 권한
- `LAKE_BUCKET` and `RDS_SECRET` exported in the shell
- `session-manager-plugin`

## Commands

```bash
AWS_PROFILE=<your-profile> LAKE_BUCKET=<lake-bucket> RDS_SECRET=<secret-id> bash ops/lake/setup_infra.sh
uv run alphamale help lake
uv run alphamale lake extract --all --dry-run
uv run alphamale lake extract --table kr_ff5_factor_daily --replace
uv run alphamale lake verify
```

기존 Iceberg target 이 이미 있으면 기본 동작은 중단(fail closed)이다. 해당 table 을 drop/recreate 해도 되는 경우에만 `--replace` 를 명시한다. `--replace` 는 destructive 옵션이다.

## RDS tunnel example

RDS 가 private 이면 SSM port-forward 를 열고 `PGHOST=127.0.0.1`, `PGPORT=15432` 로 실행한다. 런타임에 `AWS_PROFILE`, `LAKE_BUCKET`, `RDS_SECRET` 를 명시적으로 주입해야 하며, Glue/Athena naming 기본값만 `src/alphamale/lake/settings.py` 가 소유한다.
