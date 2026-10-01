#!/bin/bash
# small·1408 A4·A5 표적 재검증 L 스위트 — criteria_aws_1408_a4a5.json 순서. 실패면 멈춘다. 제출 마감·날짜는 실행기가 거부한다.
W=/Users/jingi723/orca/workspaces/edge/edge-1119-a4a5
cd $W/src/apps/cloud/airflow || exit 1
export AWS_REGION=ap-northeast-2 VERIFY_CRITERIA=criteria_aws_1408_a4a5.json
PY=/Users/jingi723/orca/workspaces/edge/airflow-ecs/src/.venv/bin/python
E=${1:?exp}; START=$(date +%s)
step() { echo "== $(TZ=Asia/Seoul date +%H:%M:%S) $*"; "$PY" verify/run.py "$@" || { echo "FAILED: $*"; exit 1; }; }
echo "code: $(git -C $W rev-parse HEAD) shim $(shasum -a 256 verify/shim.py | cut -c1-16) run $(shasum -a 256 verify/run.py | cut -c1-16) crit $(shasum -a 256 verify/criteria_aws_1408_a4a5.json | cut -c1-16)"
step deployinfo $E
step watchdog $E
step idle $E S1 600
step rds $E --since $(( $(date +%s) - 660 ))
step batch $E L
step deployinfo $E
step idle $E after_L 600
step rds $E --since $START
step obs $E
echo "SUITE_DONE $(TZ=Asia/Seoul date +%H:%M:%S)"
