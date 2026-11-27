#!/usr/bin/env bash
# 분석 스냅샷 정리 — 매일 04:30. AWS 에서는 EventBridge 스케줄이 db-maintenance 태스크로 돌리던 일이다
# (infra/terraform/envs/dev/db-maintenance.tf). 규칙은 data_pipeline/ops/snapshot_retention.py 에 있다.
#
# 다른 스텝과 다른 점:
# - 진입점이 data_pipeline.run 이 아니라 `python -m data_pipeline.ops.snapshot_retention` 이다.
# - DB 접속은 env 파일의 PG* 값(tool_runs 를 지울 수 있는 계정)을 쓴다.
# - 지우는 동안 DB 디스크 여유를 본다. PostgreSQL 데이터 디렉터리가 있는 디스크를 컨테이너에 읽기 전용으로 붙이고
#   DB_DISK_PATH 로 알려 준다. 이 값이 없으면 지우지 않고 끝난다(exit 2).
. "$(dirname "$0")/../lib/batch.sh"

begin_lane snapshot-retention "${1:-}"
DISK_PATH="$(need DB_DISK_PATH)" || exit 1

STEP_DOCKER_OPTS="-e MODE=run -v $DISK_PATH:$DISK_PATH:ro --entrypoint python"
run_step retention -m data_pipeline.ops.snapshot_retention

end_lane
