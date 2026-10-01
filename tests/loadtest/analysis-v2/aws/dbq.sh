#!/bin/bash
# 읽기전용 dev DB 질의: dbq.sh <이름> <sql파일>  → raw/dbq-<이름>.log
set -euo pipefail
export AWS_PROFILE=edge AWS_REGION=ap-northeast-2
S="$(cd "$(dirname "$0")" && pwd)"; name=$1; sqlfile=$2
python3 - "$sqlfile" > "$S/raw/dbq-$name.overrides.json" <<'PY'
import json,sys
print(json.dumps({"containerOverrides":[{"name":"db-query","command":["--sql",open(sys.argv[1]).read()]}]}))
PY
arn=$(aws ecs run-task --cluster edge-dev-worker --launch-type FARGATE --task-definition edge-dev-db-query \
  --network-configuration 'awsvpcConfiguration={subnets=[subnet-082d6209b3c9a5da4],securityGroups=[sg-0dbd25178cf8b104f],assignPublicIp=DISABLED}' \
  --overrides "file://$S/raw/dbq-$name.overrides.json" --query 'tasks[0].taskArn' --output text)
id=${arn##*/}; echo "task $id" >&2
aws ecs wait tasks-stopped --cluster edge-dev-worker --tasks "$id"
aws ecs describe-tasks --cluster edge-dev-worker --tasks "$id" --query 'tasks[0].[stopCode,containers[0].exitCode,stoppedReason]' --output text >&2
aws logs get-log-events --log-group-name /ecs/edge-dev-db-query --log-stream-name "query/db-query/$id" --start-from-head --query 'events[].message' --output text > "$S/raw/dbq-$name.log"
wc -c "$S/raw/dbq-$name.log" >&2
