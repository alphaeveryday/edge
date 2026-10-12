#!/usr/bin/env bash
# deploy-airflow 의 서비스 교체 완료 판정(ALPHA-1138). 사용: wait_rollout.sh <cluster> <service> <update-service 가 돌려준 deployment id>
#
# services-stable 은 태스크 수가 맞으면 돌아오지만 ECS 는 그 뒤 수십 초 지나서야 rollout 을 COMPLETED 로 바꾼다
# (2026-10-01 실측: IN_PROGRESS 판정 21·26초 뒤 "deployment completed"). 그래서 한 번만 보지 않고 PRIMARY 를 다시 본다.
# 판정 대상은 update-service 가 돌려준 **이번 배포의 id** 다 — 같은 리비전의 다른 배포가 COMPLETED 여도 이번 성공이 아니다
# (배포 하나는 태스크 정의 하나에 묶이므로 id 가 맞으면 리비전도 맞다).
#   PRIMARY 가 이번 배포·COMPLETED → 성공
#   PRIMARY 가 다른 배포(circuit breaker 롤백·다른 배포가 덮음)이거나 FAILED → 즉시 실패
#   그 밖(IN_PROGRESS) → 제한 시간까지 재조회
set -euo pipefail
cluster="$1" service="$2" want_id="$3"
timeout="${ROLLOUT_TIMEOUT:-600}" poll="${ROLLOUT_POLL:-15}"
deadline=$(( SECONDS + timeout ))
while :; do
  state="$(aws ecs describe-services --cluster "${cluster}" --services "${service}" \
    --query 'services[0].deployments[?status==`PRIMARY`].[id,taskDefinition,rolloutState]' --output text)"
  echo "PRIMARY: ${state}"
  read -r id _def rollout <<<"${state}" || true
  if [ "${id}" != "${want_id}" ] || [ "${rollout}" = FAILED ]; then
    echo "::error::이번 배포(${want_id})가 성공하지 못했다(롤백·다른 배포일 수 있다): ${state}"; exit 1
  fi
  [ "${rollout}" = COMPLETED ] && exit 0
  [ "${SECONDS}" -lt "${deadline}" ] || { echo "::error::rollout ${timeout}초 안에 끝나지 않았다: ${state}"; exit 1; }
  sleep "${poll}"
done
