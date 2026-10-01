"""실험 중 감시(shim verify-watchdog)·심장박동 게이트 — data-pipeline 환경에서 실행한다(shim 이 data_pipeline 을 import).

    cd src && VERIFY_CRITERIA=criteria_aws_1408_a4a5.json VERIFY_BUCKET=x AWS_DEFAULT_REGION=ap-northeast-2 \
        PYTHONPATH=apps/cloud/airflow/verify uv run --package data-pipeline \\
        pytest apps/cloud/airflow/verify/test_watchdog.py -q
"""
import json
import time

import pytest

import shim

STOPS = json.load(open(__file__.rsplit("/", 1)[0] + "/criteria_aws_1408_a4a5.json"))["rds_stop"]


def probe(**kw):
    return {"memavail_min_kb": 400 * 1024, "sample_age": 5, "kmsg_oom": 0, "docker_oom": 0, "cg_oom_kill": 0, **kw}


def test_host_memory_and_oom_trip_and_stale_observer_is_a_loss():
    # WHY: 노트북 없이도 호스트 메모리 기준(64MiB)·OOM 흔적에서 멈춰야 한다. 관측기가 멈췄으면 '정상'이 아니라 관측 실패다.
    assert shim._host_trip(probe(), 0) is None
    assert "MemAvailable" in shim._host_trip(probe(memavail_min_kb=63 * 1024), 0)
    assert "OOM" in shim._host_trip(probe(kmsg_oom=3), 2)            # 기동 뒤 새 OOM 줄
    assert shim._host_trip(probe(kmsg_oom=2), 2) is None              # 기동 전부터 있던 줄은 기준선
    assert "OOM" in shim._host_trip(probe(cg_oom_kill=1), 0)
    with pytest.raises(RuntimeError):
        shim._host_trip(probe(sample_age=120), 0)
    with pytest.raises(RuntimeError):
        shim._host_trip(probe(memavail_min_kb=-1), 0)


def test_rds_trip_uses_the_fixed_criteria_with_sustain():
    # WHY: 기준 파일의 rds_stop 그대로(지속 분 포함) — 한 번 튄 값은 멈추지 않고, 지속되면 멈춘다.
    ok = {"FreeableMemory": [580] * 8, "SwapUsage": [0] * 8, "CPUUtilization": [5] * 8,
          "DatabaseConnections": [22] * 8, "WriteLatency": [2] * 8, "ReadLatency": [1] * 8}
    assert shim._rds_trip(ok, STOPS) is None
    assert shim._rds_trip({**ok, "FreeableMemory": [580, 480, 480, 580, 580, 580, 580, 580]}, STOPS) is None
    assert "freeable" in shim._rds_trip({**ok, "FreeableMemory": [580, 480, 480, 480, 580, 580, 580, 580]}, STOPS)
    assert "connections" in shim._rds_trip({**ok, "DatabaseConnections": [43, 43, 22, 22, 22, 22, 22, 22]}, STOPS)


class FakeS3:
    def __init__(self, beat=None, required=True):
        self.objects = {} if beat is None else {shim.WATCH_KEY: json.dumps(beat).encode()}
        if required:
            self.objects[shim.WATCH_REQUIRED_KEY] = b"{}"
        self.puts = []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": type("B", (), {"read": lambda _s, d=self.objects[Key]: d})()}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body
        self.puts.append(Key)


@pytest.mark.parametrize("beat,refused", [
    (None, True),                                              # 감시를 띄우지 않았다
    ({"t": time.time() - 200, "trip": None}, True),             # 감시가 죽었다(심장박동 묵음)
    ({"t": time.time(), "trip": "RDS 중단 기준 ['cpu']"}, True),  # 감시가 중단을 선언했다
    ({"t": time.time(), "trip": None}, False),
])
def test_business_steps_do_not_start_without_a_live_watchdog(monkeypatch, beat, refused):
    # WHY: 감시가 없거나 죽으면 검증 부하를 걸지 않는다 — 업무 스텝은 시작 전에 거부(exit 75 = 미실행)한다.
    monkeypatch.setattr(shim, "_s3", FakeS3(beat))
    monkeypatch.setattr(shim, "_record", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(shim, "_instrument", lambda *a, **k: called.append(1))
    monkeypatch.setattr(shim.dp_run, "main", lambda argv: called.append(2) or 0)
    code = shim.main(["ingest-raw-investor-estimate", "--run-id", "r1"])
    assert (code, called) == ((75, []) if refused else (0, [1, 2]))


def test_watchdog_trip_calls_the_shutdown_procedure_and_loss_counts_as_a_trip(monkeypatch):
    # WHY: 기준 초과·감시 상실 모두 같은 종료 절차로 간다 — 감시가 3회 연속 실패하면 감시 없이 계속하지 않는다.
    monkeypatch.setenv("OPS_CLUSTER_ARN", "c")
    monkeypatch.setenv("VERIFY_SERVICE", "s")
    monkeypatch.setenv("VERIFY_ASG", "g")
    monkeypatch.setattr(shim.sys, "argv", ["shim", "verify-watchdog", "23:59", json.dumps(STOPS)])
    fake = FakeS3()
    monkeypatch.setattr(shim, "_s3", fake)
    monkeypatch.setattr(shim, "_record", lambda *a, **k: None)
    monkeypatch.setattr(shim, "_task_arn", lambda: "arn:task/w")
    monkeypatch.setattr(shim.time, "sleep", lambda s: None)

    class Broken:                      # 모든 AWS 조회가 실패 = 감시 상실
        def __getattr__(self, name):
            def fail(*a, **k):
                raise RuntimeError("down")
            return fail
    monkeypatch.setattr(shim.boto3, "client", lambda name: Broken())
    calls = []
    monkeypatch.setattr(shim, "shutdown", lambda grace=None, reason="": calls.append((grace, reason)) or 0)
    assert shim.watchdog() == 0
    assert len(calls) == 1 and calls[0][0] == 60 and "감시 상실" in calls[0][1]
    beat = json.loads(fake.objects[shim.WATCH_KEY])
    assert beat["trip"] and max(beat["losses"].values()) == shim.WATCH_LOSS_LIMIT


def test_the_heartbeat_survives_the_per_batch_reset():
    # WHY(봇 P1): 실행기는 배치마다 verify-reset 으로 상태 접두를 지운다. 심장박동이 그 안에 있으면 감시를 먼저 띄워도
    # 첫 업무 스텝이 심장박동을 못 읽고 거부된다.
    assert not any(shim.WATCH_KEY.startswith(p) for p in shim.RESET_PREFIXES)


def test_runs_without_a_watchdog_requirement_keep_the_old_behaviour(monkeypatch):
    # WHY(봇 P1): 감시를 쓰지 않는 이전 기준(V·B1~B3)의 업무 스텝까지 막으면 그 절차가 통째로 돌지 않는다.
    # 표지가 없으면 게이트를 걸지 않는다(표지가 있으면 위 테스트대로 심장박동이 있어야 한다).
    monkeypatch.setattr(shim, "_s3", FakeS3(None, required=False))
    assert shim._watch_gate() is None


class FakeRunS3:
    def __init__(self, objects):
        self.objects, self.deleted = dict(objects), []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": type("B", (), {"read": lambda _s, d=self.objects[Key]: d})()}

    def delete_objects(self, Bucket, Delete):
        self.deleted += [o["Key"] for o in Delete["Objects"]]


@pytest.fixture
def run_module(monkeypatch):
    import run
    monkeypatch.setattr(run, "_bucket", lambda: "b")
    monkeypatch.setattr(run, "mark", lambda *a, **k: None)
    return run


def test_a_watchdog_criteria_refuses_to_start_a_batch_without_a_live_watchdog(run_module, monkeypatch):
    # WHY(봇 P1): 감시를 요구하는 기준인데 수동 선행 단계를 빠뜨리면 표지가 없어 게이트가 꺼진 채 부하가 걸린다.
    monkeypatch.setattr(run_module, "CRIT", {"watchdog": {"until_kst": "22:25"}})
    for objects in ({}, {"watchdog/required.json": b"{}"},
                    {"watchdog/required.json": b"{}", "watchdog/heartbeat.json": json.dumps({"t": time.time() - 300}).encode()},
                    {"watchdog/required.json": b"{}", "watchdog/heartbeat.json": json.dumps({"t": time.time(), "trip": "x"}).encode()}):
        monkeypatch.setattr(run_module, "s3", FakeRunS3(objects))
        with pytest.raises(SystemExit):
            run_module._watch_precondition("e")
    monkeypatch.setattr(run_module, "s3", FakeRunS3({"watchdog/required.json": b"{}",
                                                     "watchdog/heartbeat.json": json.dumps({"t": time.time()}).encode()}))
    run_module._watch_precondition("e")


def test_a_criteria_without_watchdog_clears_a_stale_requirement(run_module, monkeypatch):
    # WHY(봇 P1): 앞 실험이 남긴 표지가 감시를 쓰지 않는 다음 배치(V·B)의 업무를 영영 막지 않게 한다.
    monkeypatch.setattr(run_module, "CRIT", {})
    fake = FakeRunS3({"watchdog/required.json": b"{}"})
    monkeypatch.setattr(run_module, "s3", fake)
    run_module._watch_precondition("e")
    assert set(fake.deleted) == {"watchdog/required.json", "watchdog/heartbeat.json"}
