"""로컬 AWS 대역 — ECS·SNS·Step Functions 의 이 레인이 쓰는 호출만. 운영 AWS 를 부르지 않는다.

- ECS(JSON 1.1): RunTask·ListTasks·DescribeTasks·StopTask. RunTask 는 clientToken 이 같으면 새로 만들지 않고
  첫 요청의 태스크를 돌려준다(ECS 멱등 토큰 — 실제 AWS 의 보존 기간·세부 동작은 대역으로 입증하지 않는다).
  ECS API 장애 주입(faults.json 의 "api" 규칙): run_task refuse(배치 거부 확정)·lose_response(생성 뒤 5xx —
  같은 토큰의 재전송도 5xx)·lose_response_invisible(+ ListTasks 에 안 보임), list_tasks error(started_by 지정),
  describe_tasks error(그 스텝 태스크 조회만). RunTask 는 컨테이너 대신 같은 이미지의
  업무 코드(`step_shim.py` → `data_pipeline.run.main`)를 서브프로세스로 띄운다. `OPS_ECS_TASK_ARN` 으로
  가짜 ARN 을 넘겨 wrapper 가 운영처럼 attempt 를 남긴다.
- SNS Publish: 기록만 한다.
- SFN(JSON 1.0): StartExecution·DescribeExecution. 배포된 ASL(`inputs/deployed-asl.json`)을 아래 작은
  해석기로 실행한다(기존 경로 재현). ecs:runTask.sync 의 비0 종료는 실제 AWS 처럼 States.TaskFailed 다
  (dev 실행 이력 `investor-intraday-2026-09-07T11-25` 에서 확인한 동작).
  GetExecutionHistory 는 빈 이력을 준다 — 기존 경로의 Reconciler 증거는 재현하지 않는다.

관리 엔드포인트: GET /_lab/state, POST /_lab/reset, POST /_lab/stop-all.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PY = "/opt/dp/bin/python"
SHIM = "/lab/step_shim.py"
LOGS = Path("/lab-data/ecs-logs")
ASL = json.loads(Path("/inputs/deployed-asl.json").read_text())
ACCOUNT = "000000000000"

LOCK = threading.Lock()
TASKS: dict[str, dict] = {}
TOKENS: dict[str, str] = {}          # clientToken -> taskArn
RUN_REQUESTS: list[dict] = []        # RunTask 요청(boto 재전송 포함) — 제출 횟수 ≠ 생성 태스크 수를 보인다
PROCS: dict[str, subprocess.Popen] = {}
SNS: list[dict] = []
EXECUTIONS: dict[str, dict] = {}


class AwsError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _take_api_fault(api: str, **match) -> dict | None:
    """faults.json 의 {"api": ..., 조건..., "times"} 규칙 하나를 소모해 돌려준다. token 규칙은 같은 토큰에 계속 적용."""
    path = Path("/lab-data/state/faults.json")
    if not path.exists():
        return None
    with LOCK:
        rules = json.loads(path.read_text())
        for rule in rules:
            if rule.get("api") != api or rule.get("times", 1) <= 0:
                continue
            if any(k in rule and rule[k] != v for k, v in match.items() if k != "token"):
                continue
            if "token" in match and rule.get("token") not in (None, match["token"]):
                continue
            if "token" in match and rule.get("sticky"):
                rule["token"] = match["token"]          # 같은 요청의 boto 재전송에도 계속 적용
            else:
                rule["times"] = rule.get("times", 1) - 1
            path.write_text(json.dumps(rules))
            return rule
    return None


# ── ECS ──────────────────────────────────────────────────────────────
def _public(task: dict) -> dict:
    # 실제 DescribeTasks 처럼 overrides(명령)를 돌려준다 — Reconciler 주기 점검이 명령의 --run-id 로 레인·런을 찾는다.
    return {**{k: v for k, v in task.items() if not k.startswith("_")},
            "overrides": {"containerOverrides": [{"name": "data-pipeline", "command": task.get("_command") or []}]}}


def _take_start_fault(step: str) -> bool:
    """faults.json 의 {"step", "action": "fail_to_start"} 규칙을 한 번 소모한다(step_shim 과 같은 파일)."""
    path = Path("/lab-data/state/faults.json")
    if not path.exists():
        return False
    with LOCK:
        rules = json.loads(path.read_text())
        for rule in rules:
            if (rule.get("step") == step and rule.get("action") == "fail_to_start" and "api" not in rule
                    and rule.get("times", 1) > 0):
                rule["times"] = rule.get("times", 1) - 1
                path.write_text(json.dumps(rules))
                return True
    return False


def run_task(req: dict) -> dict:
    override = req["overrides"]["containerOverrides"][0]
    command = override.get("command") or []
    step = command[0] if command else None
    token = req.get("clientToken")
    with LOCK:
        RUN_REQUESTS.append({"step": step, "token": token, "startedBy": req.get("startedBy"), "at": time.time()})
        known = TOKENS.get(token) if token else None
    if known is not None:
        lost = _take_api_fault("run_task", step=step, token=token)
        if lost:
            raise AwsError("ServerException", "lab: 주입한 응답 유실(같은 토큰 재전송)", 500)
        return {"tasks": [_public(TASKS[known])], "failures": []}
    fault = _take_api_fault("run_task", step=step, token=token)
    if fault and fault["action"] == "refuse":
        return {"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY", "detail": "lab: 주입한 배치 거부"}]}
    response = _create_task(req, command)
    arn = response["tasks"][0]["taskArn"]
    with LOCK:
        if token:
            TOKENS[token] = arn
        if fault and fault["action"] == "lose_response_invisible":
            TASKS[arn]["_hidden"] = True
    if fault and fault["action"] in ("lose_response", "lose_response_invisible"):
        raise AwsError("ServerException", "lab: 주입한 응답 유실(태스크는 생성됨)", 500)
    return response


def _create_task(req: dict, command: list[str]) -> dict:
    override = req["overrides"]["containerOverrides"][0]
    env = {e["name"]: e["value"] for e in override.get("environment", [])}
    arn = f"arn:aws:ecs:ap-northeast-2:{ACCOUNT}:task/edge-lab/{uuid.uuid4().hex}"
    if command and _take_start_fault(command[0]):
        # Fargate 용량 부족·이미지 pull 실패처럼 컨테이너가 안 뜬 경우 — exit code 가 없다.
        task = {"taskArn": arn, "taskDefinitionArn": req["taskDefinition"], "startedBy": req.get("startedBy"),
                "lastStatus": "STOPPED", "desiredStatus": "STOPPED", "createdAt": time.time(),
                "stopCode": "TaskFailedToStart", "stoppedReason": "lab: 주입한 기동 실패",
                "containers": [{"name": "data-pipeline", "lastStatus": "STOPPED", "taskArn": arn}],
                "_command": command, "_env": env}
        with LOCK:
            TASKS[arn] = task
        return {"tasks": [_public(task)], "failures": []}
    task = {
        "taskArn": arn, "taskDefinitionArn": req["taskDefinition"], "clusterArn": req.get("cluster"),
        "startedBy": req.get("startedBy"), "lastStatus": "RUNNING", "desiredStatus": "RUNNING",
        "createdAt": time.time(), "group": req.get("group"),
        "containers": [{"name": "data-pipeline", "lastStatus": "RUNNING", "taskArn": arn}],
        "_command": command, "_env": env,
    }
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{arn.rsplit('/', 1)[1]}.log", "wb")
    proc = subprocess.Popen(
        [PY, SHIM, *command], stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, **env, "OPS_ECS_TASK_ARN": arn, "LAB_TASKDEF": req["taskDefinition"]},
    )
    with LOCK:
        TASKS[arn] = task
        PROCS[arn] = proc

    def wait():
        code = proc.wait()
        with LOCK:
            task["lastStatus"] = task["desiredStatus"] = "STOPPED"
            task["stoppedAt"] = time.time()
            task.setdefault("stopCode", "EssentialContainerExited")
            task["stoppedReason"] = task.get("stoppedReason", "Essential container in task exited")
            task["containers"][0].update(lastStatus="STOPPED", exitCode=code if code >= 0 else 137)
        log.close()

    threading.Thread(target=wait, daemon=True).start()
    return {"tasks": [_public(task)], "failures": []}


def list_tasks(req: dict) -> dict:
    if _take_api_fault("list_tasks", started_by=req.get("startedBy")):
        raise AwsError("ClientException", "lab: 주입한 ListTasks 실패")
    with LOCK:
        arns = [a for a, t in TASKS.items()
                if not t.get("_hidden")
                and (req.get("desiredStatus") in (None, t["desiredStatus"]))
                and (req.get("startedBy") in (None, t["startedBy"]))]
    return {"taskArns": arns}


def describe_tasks(req: dict) -> dict:
    with LOCK:
        # 끝난 태스크 조회만 실패시킨다 — 대기(waiter) 중 폴링이 규칙을 먼저 소모하지 않게.
        steps = {(TASKS[a]["_command"] or [None])[0] for a in req["tasks"]
                 if a in TASKS and TASKS[a]["lastStatus"] == "STOPPED"}
    if any(_take_api_fault("describe_tasks", step=step) for step in steps):
        raise AwsError("ClientException", "lab: 주입한 DescribeTasks 실패")
    with LOCK:
        found = [_public(TASKS[a]) for a in req["tasks"] if a in TASKS]
        missing = [{"arn": a, "reason": "MISSING"} for a in req["tasks"] if a not in TASKS]
    return {"tasks": found, "failures": missing}


def stop_task(req: dict) -> dict:
    arn = req["task"]
    with LOCK:
        task, proc = TASKS[arn], PROCS.get(arn)
        if task["lastStatus"] != "STOPPED":        # 이미 멈춘 태스크의 종료 경위는 바뀌지 않는다(ECS 와 같다)
            task["desiredStatus"] = "STOPPED"
            task["stopCode"], task["stoppedReason"] = "UserInitiated", req.get("reason", "stopped")
    if proc and proc.poll() is None:
        proc.terminate()
    return {"task": _public(task)}


def wait_stopped(arn: str) -> dict:
    while True:
        with LOCK:
            if TASKS[arn]["lastStatus"] == "STOPPED":
                return _public(TASKS[arn])
        time.sleep(0.5)


# ── SFN: 배포된 ASL 의 부분 해석기 ─────────────────────────────────────
class StatesError(Exception):
    def __init__(self, error: str, cause: str):
        super().__init__(error)
        self.error, self.cause = error, cause


_MISSING = object()


def jpath(path: str, data, ctx):
    """`$`, `$.a.b[0].C`, `$$.Execution.Id` 만 지원한다."""
    root, rest = (ctx, path[2:]) if path.startswith("$$") else (data, path[1:])
    for name, index in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", rest):
        try:
            root = root[name] if name else root[int(index)]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return root


def intrinsic(expr: str, data, ctx):
    fn, args_src = re.fullmatch(r"(States\.\w+)\((.*)\)", expr).groups()
    args = []
    for token in re.findall(r"'((?:[^'\\]|\\.)*)'|(\$\$?[^,\s)]*)", args_src):
        literal, path = token
        args.append(jpath(path, data, ctx) if path else literal)
    if fn == "States.Array":
        return args
    if fn == "States.Format":
        template, *values = args
        for value in values:
            template = template.replace("{}", str(value), 1)
        return template
    if fn == "States.JsonToString":
        return json.dumps(args[0], ensure_ascii=False)
    raise ValueError(fn)


def params(template, data, ctx):
    if isinstance(template, dict):
        out = {}
        for key, value in template.items():
            if key.endswith(".$"):
                out[key[:-2]] = (intrinsic(value, data, ctx) if value.startswith("States.")
                                 else jpath(value, data, ctx))
            else:
                out[key] = params(value, data, ctx)
        return out
    if isinstance(template, list):
        return [params(v, data, ctx) for v in template]
    return template


def with_result(data, result, result_path):
    if result_path is None:
        return data
    if result_path == "$" or result_path is _MISSING:
        return result
    out = dict(data)
    out[result_path[2:]] = result          # 이 ASL 은 한 단계 키만 쓴다($.ecs, $.error …)
    return out


def choice_ok(rule, data, ctx) -> bool:
    if "And" in rule:
        return all(choice_ok(r, data, ctx) for r in rule["And"])
    if "Or" in rule:
        return any(choice_ok(r, data, ctx) for r in rule["Or"])
    value = jpath(rule["Variable"], data, ctx)
    if "IsPresent" in rule:
        return (value is not _MISSING) == rule["IsPresent"]
    if "StringEquals" in rule:
        return value == rule["StringEquals"]
    if "NumericEquals" in rule:
        return isinstance(value, (int, float)) and value == rule["NumericEquals"]
    raise ValueError(rule)


def run_task_state(state, data, ctx):
    p = params(state.get("Parameters", {}), data, ctx)
    if state["Resource"].endswith(":sns:publish"):
        with LOCK:
            SNS.append({"via": "sfn", "subject": p.get("Subject"), "at": time.time()})
        return {"MessageId": uuid.uuid4().hex}
    if state["Resource"].endswith(":ecs:runTask.sync"):
        o = p["Overrides"]["ContainerOverrides"][0]
        arn = run_task({"taskDefinition": p["TaskDefinition"], "cluster": p.get("Cluster"),
                        "startedBy": f"sfn:{ctx['Execution']['Name']}",
                        "overrides": {"containerOverrides": [{
                            "command": o["Command"],
                            "environment": [{"name": e["Name"], "value": e["Value"]}
                                            for e in o.get("Environment", [])]}]}})["tasks"][0]["taskArn"]
        task = wait_stopped(arn)
        container = task["containers"][0]
        described = {"TaskArn": arn, "StopCode": task.get("stopCode"),
                     "Containers": [{"Name": container["name"], "ExitCode": container.get("exitCode")}]}
        if container.get("exitCode") != 0:
            raise StatesError("States.TaskFailed", json.dumps(described))
        return described
    raise ValueError(state["Resource"])


def run_machine(machine, data, ctx):
    name = machine["StartAt"]
    while True:
        state = machine["States"][name]
        kind = state["Type"]
        try:
            if kind == "Task":
                data = with_result(data, run_task_state(state, data, ctx), state.get("ResultPath", _MISSING))
            elif kind == "Parallel":
                results = [run_machine(branch, data, ctx) for branch in state["Branches"]]
                data = with_result(data, results, state.get("ResultPath", _MISSING))
            elif kind == "Pass":
                if "Parameters" in state:
                    data = with_result(data, params(state["Parameters"], data, ctx),
                                       state.get("ResultPath", _MISSING))
            elif kind == "Choice":
                name = next((c["Next"] for c in state["Choices"] if choice_ok(c, data, ctx)),
                            state.get("Default"))
                continue
            elif kind == "Succeed":
                return data
            elif kind == "Fail":
                raise StatesError("States.Fail", state.get("Cause", ""))
        except StatesError as exc:
            catch = next((c for c in state.get("Catch", [])
                          if "States.ALL" in c["ErrorEquals"] or exc.error in c["ErrorEquals"]), None)
            if catch is None or kind == "Fail":
                raise
            data = with_result(data, {"Error": exc.error, "Cause": exc.cause}, catch.get("ResultPath", _MISSING))
            name = catch["Next"]
            continue
        if state.get("End"):
            return data
        name = state["Next"]


def start_execution(req: dict) -> dict:
    arn = req["stateMachineArn"].replace(":stateMachine:", ":execution:") + ":" + req["name"]
    with LOCK:
        existing = EXECUTIONS.get(arn)
        if existing is not None:
            if existing["input"] == req["input"] and existing["status"] == "RUNNING":
                return {"executionArn": arn, "startDate": existing["startDate"]}
            raise AwsError("ExecutionAlreadyExists", f"Execution Already Exists: '{arn}'")
        execution = EXECUTIONS[arn] = {"executionArn": arn, "input": req["input"], "status": "RUNNING",
                                       "startDate": time.time(), "name": req["name"]}

    def go():
        ctx = {"Execution": {"Id": arn, "Name": req["name"]}}
        try:
            run_machine(ASL, json.loads(req["input"]), ctx)
            status = "SUCCEEDED"
        except StatesError:
            status = "FAILED"
        with LOCK:
            execution.update(status=status, stopDate=time.time())

    threading.Thread(target=go, daemon=True).start()
    return {"executionArn": arn, "startDate": execution["startDate"]}


def describe_execution(req: dict) -> dict:
    with LOCK:
        execution = EXECUTIONS.get(req["executionArn"])
    if execution is None:
        raise AwsError("ExecutionDoesNotExist", req["executionArn"])
    return {**execution, "stateMachineArn": req["executionArn"].rsplit(":", 1)[0]}


HANDLERS = {
    "AmazonEC2ContainerServiceV20141113.RunTask": run_task,
    "AmazonEC2ContainerServiceV20141113.ListTasks": list_tasks,
    "AmazonEC2ContainerServiceV20141113.DescribeTasks": describe_tasks,
    "AmazonEC2ContainerServiceV20141113.StopTask": stop_task,
    "AWSStepFunctions.StartExecution": start_execution,
    "AWSStepFunctions.DescribeExecution": describe_execution,
    "AWSStepFunctions.GetExecutionHistory": lambda req: {"events": []},
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype="application/x-amz-json-1.1"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/_lab/state":
            with LOCK:
                state = {"tasks": [{**_public(t), "command": t["_command"], "env": t["_env"]}
                                   for t in TASKS.values()],
                         "sns": SNS, "executions": list(EXECUTIONS.values()),
                         "run_requests": RUN_REQUESTS}
            self._send(200, json.dumps(state, default=str).encode(), "application/json")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/_lab/reset":
            with LOCK:
                TASKS.clear(); PROCS.clear(); SNS.clear(); EXECUTIONS.clear(); TOKENS.clear()
                RUN_REQUESTS.clear()
            return self._send(200, b"{}", "application/json")
        target = self.headers.get("X-Amz-Target")
        if target is None:                               # SNS query protocol
            form = urllib.parse.parse_qs(body.decode())
            with LOCK:
                SNS.append({"via": "airflow", "subject": form.get("Subject", [None])[0],
                            "at": time.time()})
            xml = ("<PublishResponse xmlns=\"http://sns.amazonaws.com/doc/2010-03-31/\"><PublishResult>"
                   f"<MessageId>{uuid.uuid4()}</MessageId></PublishResult><ResponseMetadata>"
                   "<RequestId>lab</RequestId></ResponseMetadata></PublishResponse>")
            return self._send(200, xml.encode(), "text/xml")
        if target.startswith("AmazonSNS."):              # SNS JSON protocol(신규 botocore)
            with LOCK:
                SNS.append({"via": "airflow", "subject": json.loads(body).get("Subject"),
                            "at": time.time()})
            return self._send(200, json.dumps({"MessageId": uuid.uuid4().hex}).encode(),
                              "application/x-amz-json-1.0")
        try:
            result = HANDLERS[target](json.loads(body or b"{}"))
            self._send(200, json.dumps(result, default=str).encode())
        except AwsError as exc:
            self._send(exc.status, json.dumps({"__type": exc.code, "message": str(exc)}).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 4566), Handler).serve_forever()
