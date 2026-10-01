#!/usr/bin/env bash
# deploy-airflow 의 서비스 교체 완료 판정(ALPHA-1138). 사용: wait_rollout.sh <cluster> <service> <새 태스크 정의 ARN>
#
# services-stable 은 태스크 수가 맞으면 돌아오지만 ECS 는 그 뒤 수십 초 지나서야 rollout 을 COMPLETED 로 바꾼다
# (2026-10-01 실측: IN_PROGRESS 판정 21·26초 뒤 "deployment completed"). 그래서 한 번만 보지 않고 PRIMARY 를 다시 본다.
#   새 리비전·COMPLETED → 성공 / FAILED·다른 리비전(circuit breaker 롤백) → 즉시 실패 / 그 밖(IN_PROGRESS) → 제한 시간까지 재조회.
set -euo pipefail
cluster="$1" service="$2" want="$3"
timeout="${ROLLOUT_TIMEOUT:-600}" poll="${ROLLOUT_POLL:-15}"
deadline=$(( SECONDS + timeout ))
while :; do
  state="$(aws ecs describe-services --cluster "${cluster}" --services "${service}" \
    --query 'services[0].deployments[?status==`PRIMARY`].[taskDefinition,rolloutState]' --output text)"
  echo "PRIMARY: ${state}"
  read -r def rollout <<<"${state}" || true
  if [ "${def}" != "${want}" ] || [ "${rollout}" = FAILED ]; then
    echo "::error::배포가 새 리비전으로 끝나지 않았다(롤백됐을 수 있다): ${state}"; exit 1
  fi
  [ "${rollout}" = COMPLETED ] && exit 0
  [ "${SECONDS}" -lt "${deadline}" ] || { echo "::error::rollout ${timeout}초 안에 끝나지 않았다: ${state}"; exit 1; }
  sleep "${poll}"
done
