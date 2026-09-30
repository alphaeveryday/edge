"""종료 장치 짧은 시험(ALPHA-1119) — 본 실험 전에 한 번. Terraform 이 만든 종료 스케줄과 **같은 대상·역할·요청**으로
몇 분 뒤 시각의 일회성 스케줄을 만들고(grace 60초), 대기 태스크(verify-sleep)를 하나 띄운 뒤 결과를 조회로 확인한다.

    python verify/shutdown_test.py <exp>

통과 조건: 서비스 desired 0 · 대기 태스크 STOPPED(stoppedReason 이 verify-shutdown) · ASG 0 · 보고서(shutdown/) 존재.
통과해도 호스트·서비스는 내려간 상태로 끝난다 — 본 실험 전에 ASG 를 Terraform 값(1/2/1)으로, 서비스를 배포로 다시 올린다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

import run as r


def main(exp: str) -> int:
    sch = r._c("scheduler")
    base = sch.get_schedule(Name=f"{r.PREFIX}-verify-shutdown")
    target = base["Target"]
    req = json.loads(target["Input"])
    req["Overrides"]["ContainerOverrides"][0]["Command"] = ["verify-shutdown", "60"]
    at = datetime.now(r.KST) + timedelta(minutes=4)
    name = f"{r.PREFIX}-verify-shutdown-test"
    dummy = r.ecs.run_task(cluster=r.CLUSTER, taskDefinition=f"{r.PREFIX}-verify-ops", launchType="FARGATE",
                           networkConfiguration=r._network(f"{r.PREFIX}-verify"), startedBy="verify-shutdown-test",
                           overrides={"containerOverrides": [{"name": "data-pipeline", "command": ["verify-sleep", "1200"]}]}
                           )["tasks"][0]["taskArn"]
    sch.create_schedule(Name=name, ScheduleExpression=f"at({at:%Y-%m-%dT%H:%M:%S})",
                        ScheduleExpressionTimezone="Asia/Seoul", FlexibleTimeWindow={"Mode": "OFF"},
                        ActionAfterCompletion="DELETE",
                        Target={"Arn": target["Arn"], "RoleArn": target["RoleArn"], "Input": json.dumps(req)})
    r.mark(exp, "shutdown_test_begin", at=at.isoformat(), dummy=dummy.rsplit("/", 1)[1])
    bucket = r._bucket()
    before = {o["Key"] for o in r.s3.list_objects_v2(Bucket=bucket, Prefix="shutdown/").get("Contents", [])}
    deadline = time.time() + 15 * 60
    res = {}
    while time.time() < deadline:
        time.sleep(20)
        svc = r.ecs.describe_services(cluster=r.CLUSTER, services=[r.PREFIX])["services"][0]["desiredCount"]
        d = r.ecs.describe_tasks(cluster=r.CLUSTER, tasks=[dummy])["tasks"][0]
        g = r.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[f"{r.PREFIX}-host"])["AutoScalingGroups"][0]
        reports = [o["Key"] for o in r.s3.list_objects_v2(Bucket=bucket, Prefix="shutdown/").get("Contents", [])
                   if o["Key"] not in before]
        res = {"service_desired": svc, "dummy": d["lastStatus"], "dummy_reason": d.get("stoppedReason"),
               "asg": [g["MinSize"], g["MaxSize"], g["DesiredCapacity"]], "reports": reports}
        if svc == 0 and d["lastStatus"] == "STOPPED" and res["asg"] == [0, 0, 0] and reports:
            break
    if res.get("reports"):
        res["report"] = json.loads(r.s3.get_object(Bucket=bucket, Key=res["reports"][0])["Body"].read())
    ok = (res.get("service_desired") == 0 and res.get("dummy") == "STOPPED"
          and "verify-shutdown" in (res.get("dummy_reason") or "") and res.get("asg") == [0, 0, 0]
          and bool(res.get("reports")) and not res.get("report", {}).get("errors"))
    r.mark(exp, "shutdown_test_end", ok=ok, **res)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
