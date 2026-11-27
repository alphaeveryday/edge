#!/usr/bin/env bash
# 분석 스냅샷 정리 — 매일 04:30. AWS 에서는 EventBridge 스케줄이 db-maintenance 태스크로 돌리던 일이다
# (infra/terraform/envs/dev/db-maintenance.tf). 규칙은 data_pipeline/ops/snapshot_retention.py 에 있다.
#
# 다른 스텝과 다른 점:
# - 진입점이 data_pipeline.run 이 아니라 `python -m data_pipeline.ops.snapshot_retention` 이다.
# - DB 접속은 retention.env 의 PG* 값(tool_runs 를 지울 수 있는 계정)을 쓴다. 이 파일은 이 스텝에만 넘긴다.
# - 지우는 동안 DB 디스크 여유를 본다. PostgreSQL 데이터 디렉터리가 있는 디스크를 컨테이너에 읽기 전용으로 붙이고
#   DB_DISK_PATH 로 알려 준다. 이 값이 없으면 시작하지 않는다.
# - 06:15 에 끊는다. 05:20 원천 관측 레인이 락을 06:20 까지만 기다리므로, 더 돌면 그날 원천 관측이 빠진다.
#   미뤄도 되는 정리가 수집을 밀어내지 않게 한다. 못 지운 행은 다음 날 정리가 지운다.
. "$(dirname "$0")/../lib/batch.sh"

begin_lane snapshot-retention "${1:-}"
RETENTION_ENV="$EDGE_CONF_DIR/retention.env"
[ -r "$RETENTION_ENV" ] || die "정리 env 파일을 읽을 수 없다: $RETENTION_ENV"
DISK_PATH="$(need DB_DISK_PATH "$RETENTION_ENV")" || exit 1

STEP_DEADLINE=06:15
STEP_DOCKER_OPTS="--env-file $RETENTION_ENV -e MODE=run -v $DISK_PATH:$DISK_PATH:ro --entrypoint python"
run_step retention -m data_pipeline.ops.snapshot_retention

end_lane
