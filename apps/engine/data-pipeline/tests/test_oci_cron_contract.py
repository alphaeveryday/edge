"""OCI 배치 cron(infra/oci)이 Airflow DAG 에서 물려받은 계약 — run_id 규칙, 스텝 순서와 관문, 빠진 슬롯 통보.

레인 스크립트는 실제 bash 로 돌리고 docker·flock 만 가짜로 바꾼다(PATH 앞에 둔 대역). python3 대역은 진짜
edge_cron 을 부르되 시계를 고정하고 메일을 기록만 한다. DAG 는 Airflow 없이 텍스트로 읽는다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from data_pipeline import db, run as pipeline_run
from data_pipeline.ops import planner

REPO = Path(__file__).resolve().parents[4]
OCI = REPO / "infra" / "oci"
DAGS = REPO / "apps" / "engine" / "airflow" / "dags"
KST = timezone(timedelta(hours=9))
IMAGE = "registry.example/edge/data-pipeline:test"

sys.path.insert(0, str(OCI / "lib"))
import edge_cron  # noqa: E402

_spec = importlib.util.spec_from_file_location("oci_watchdog", OCI / "vm1" / "watchdog.py")
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)

# 레인 → (스크립트, DAG 파일, 슬롯, 모든 스텝이 성공할 때의 실행 순서). {rid} 는 그 슬롯의 run_id.
LANES = {
    "premarket": ("vm1/premarket.sh", "edge_premarket.py", "07:00", """
        ingest-raw-etf --source krx --run-id {rid} --deadline-sec 660
        normalize-etf --run-id {rid} --input-run-id {rid}
        build-minute-universe --run-id {rid} --universe s3://lake/config/minute/universe.json"""),
    "investor-intraday": ("vm1/investor_intraday.sh", "edge_investor_intraday.py", "09:35", """
        ingest-raw-investor-estimate --max-failed-symbols 1 --run-id {rid}
        normalize-investor-estimate --run-id {rid} --input-run-id {rid}
        load-investor-intraday --run-id {rid} --input-run-id {rid}"""),
    "etf-daily": ("vm1/etf_daily.sh", "edge_etf_daily.py", "16:30", """
        ingest-price-raw --source kis --max-failed-symbols 1 --run-id {rid}
        ingest-raw-investor --max-failed-symbols 1 --run-id {rid}
        ingest-raw-etf --source krx --run-id {rid} --deadline-sec 660
        ingest-raw-etf-profile --run-id {rid}
        ingest-raw-nav --run-id {rid}
        ingest-raw-sector-index-daily --run-id {rid}
        ingest-raw-financial --source dart --run-id {rid}
        normalize-price --run-id {rid} --input-run-id {rid}
        normalize-investor --run-id {rid} --input-run-id {rid}
        normalize-etf --run-id {rid} --input-run-id {rid}
        normalize-etf-profile --run-id {rid} --input-run-id {rid}
        normalize-etf-nav --run-id {rid} --input-run-id {rid}
        normalize-sector-index-daily --run-id {rid} --input-run-id {rid}
        load-instruments --run-id {rid} --latest-good
        enrich-corp-code --run-id {rid}
        load-etf-nav --run-id {rid} --input-run-id {rid}
        load-price-daily --run-id {rid} --input-run-id {rid}
        load-etf-holdings --run-id {rid} --input-run-id {rid}
        load-etf-flow --run-id {rid} --input-run-id {rid}
        load-market-series-daily --run-id {rid} --input-run-id {rid}
        load-price-triggers --run-id {rid} --input-run-id {rid}"""),
    "disclosure": ("vm1/disclosure.sh", "edge_disclosure.py", "19:30", """
        ingest-raw-disclosure --run-id {rid}
        normalize-disclosure --run-id {rid} --input-run-id {rid}
        normalize-disclosure-segment --run-id {rid} --input-run-id {rid}
        load-disclosure --run-id {rid} --input-run-id {rid}"""),
    "dividend-daily": ("vm1/dividend_daily.sh", "edge_dividend_daily.py", "03:00", """
        ingest-raw-dividend --run-id {rid} --deadline-sec 600
        normalize-dividend --run-id {rid} --input-run-id {rid}
        load-dividend --run-id {rid} --input-run-id {rid}"""),
    "source-daily": ("vm1/source_daily.sh", "edge_source_daily.py", "05:20", """
        ingest-raw-macro --run-id {rid} --from '' --to ''
        normalize-macro --run-id {rid} --input-run-id {rid}
        load-macro --run-id {rid} --input-run-id {rid}
        ingest-raw-sector --run-id {rid}
        normalize-sector --run-id {rid} --input-run-id {rid}
        load-sector --run-id {rid} --input-run-id {rid}
        ingest-raw-financial-metric --run-id {rid} --from '' --to ''
        normalize-financial-metric --run-id {rid} --input-run-id {rid}
        load-financial-metric --run-id {rid} --input-run-id {rid}"""),
    "news": ("vm3/news.sh", "edge_news.py", "00:10", """
        ingest-raw --source bigkinds --run-id {rid}
        normalize-news --run-id {rid} --input-run-id {rid}
        load-documents --run-id {rid} --input-run-id {rid}
        tag-news --run-id {rid} --input-run-id {rid} --limit 10000
        load-assertions --run-id {rid} --input-run-id {rid}
        assemble-events --run-id {rid} --window-days 1"""),
    # DAG 가 없는 운영 작업 — AWS 에서는 EventBridge 스케줄이 db-maintenance 태스크로 돌렸다.
    "snapshot-retention": ("vm1/snapshot_retention.sh", None, "04:30", "-m data_pipeline.ops.snapshot_retention"),
}
DAG_LANES = [lane for lane, spec in LANES.items() if spec[1]]
DB_DISK = "/var/lib/postgresql"
ENV_LINES = [f"EDGE_PIPELINE_IMAGE={IMAGE}", "EDGE_KRX_ETF_DEADLINE_SEC=660",
             "EDGE_MINUTE_UNIVERSE_URI=s3://lake/config/minute/universe.json", "EDGE_TAG_NEWS_LIMIT=10000",
             "EDGE_ASSEMBLE_WINDOW_DAYS=1", f"DB_DISK_PATH={DB_DISK}"]

DOCKER_STUB = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({{"docker": args}}) + "\\n")
if "-c" in args:      # edge_sql.sh(python -c) — 성공 행 기록
    sys.exit(int(os.environ.get("STUB_SQL_EXIT", "0")))
step = args[args.index("{image}") + 1]
exits = dict(pair.split("=") for pair in os.environ.get("STUB_EXITS", "").split(",") if pair)
sys.exit(int(exits.get(step, 0)))
"""
# 진짜 edge_cron 을 부른다. 바꾸는 것은 시계(STUB_NOW)와 SMTP(기록만)뿐이다.
PYTHON_STUB = """#!{python}
import json, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import edge_cron
def record(subject, body, conf):
    with open(os.environ["STUB_LOG"], "a") as log:
        log.write(json.dumps({{"mail": subject, "body": body}}) + "\\n")
edge_cron.send_mail = record
sys.exit(edge_cron.main(sys.argv[2:], now=datetime.fromisoformat(os.environ["STUB_NOW"])))
"""
FLOCK_STUB = "#!/bin/sh\nexit ${STUB_FLOCK_EXIT:-0}\n"


def _slot(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 30, hour, minute, tzinfo=KST)     # 월요일


def run_lane(tmp_path, lane, *, exits="", late_minutes=1, env_lines=ENV_LINES, mount=(), **stub_env):
    """레인 스크립트를 돌려 (exit, 실행된 스텝 명령들, 메일 제목들, 성공 행 SQL 인자들)을 돌려준다."""
    script, _dag, hhmm, _steps = LANES[lane]
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    for name, body in (("docker", DOCKER_STUB), ("python3", PYTHON_STUB), ("flock", FLOCK_STUB)):
        path = stubs / name
        path.write_text(body.format(python=sys.executable, image=IMAGE) if name != "flock" else body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    env_file, log = tmp_path / "batch.env", tmp_path / "stub.log"
    env_file.write_text("\n".join(env_lines) + "\n")
    log.write_text("")
    result = subprocess.run(
        ["bash", str(OCI / script), hhmm], capture_output=True, text=True, timeout=60,
        env={"PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "EDGE_ENV_FILE": str(env_file),
             "EDGE_LOCK_FILE": str(tmp_path / "lock"), "STUB_LOG": str(log), "STUB_EXITS": exits,
             "STUB_NOW": (_slot(hhmm) + timedelta(minutes=late_minutes)).isoformat(), **stub_env})
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    docker = [call["docker"] for call in calls if "docker" in call]
    steps = [args for args in docker if "-c" not in args]
    # 스텝은 정해진 꼴 그대로 뜬다 — 이미지 ENTRYPOINT 가 `python -m data_pipeline.run` 이라 명령은 스텝 이름부터다.
    prefix = ["run", "--rm", "--env-file", str(env_file), "--memory", "2g", *mount, IMAGE]
    assert all(args[:len(prefix)] == prefix for args in steps), steps
    success_rows = [args[args.index("-c") + 3:] for args in docker if "-c" in args]
    return (result.returncode, [args[len(prefix):] for args in steps],
            [call["mail"] for call in calls if "mail" in call], success_rows)


def _expected(lane: str) -> list[list[str]]:
    hhmm, steps = LANES[lane][2], LANES[lane][3]
    rid = edge_cron.run_id(lane, _slot(hhmm))
    return [shlex.split(line.format(rid=rid)) for line in steps.strip().splitlines()]


# ── run_id ──

def test_run_id_is_what_the_dags_built_for_the_same_slot():
    # WHY: 수집 → 정제 → 적재가 run_id 로 이어지고, raw·canonical 파티션 자리도 run_id 다. 규칙이 갈리면
    # 옮긴 뒤의 실행이 옛 실행과 다른 자리에 쓰고, 사람이 `--input-run-id` 로 다시 정제할 값도 못 맞춘다.
    slot = datetime(2026, 9, 22, 9, 35, tzinfo=KST)
    assert edge_cron.run_id("investor-intraday", slot) == "run_c52a16e3d7f98774b0fe384f5d"   # dev 실제 실행 값
    for _vm, lane, hhmm, _days, _grace in edge_cron.SLOTS:
        if lane != "premarket":
            assert edge_cron.run_id(lane, _slot(hhmm)) == db.stable_domain_id(
                "run", planner.slot_run_key(_slot(hhmm), lane)), lane
    # 장전 레인만 슬롯의 UTC 시각이다(edge_premarket.utc_run_id — dev 실제 실행 2026-10-20 07:00 KST).
    assert edge_cron.run_id("premarket", datetime(2026, 10, 20, 7, 0, tzinfo=KST)) == "20261019T220000Z"
    assert edge_cron.run_id("premarket", _slot("07:00")) == pipeline_run.make_run_id(
        _slot("07:00").astimezone(timezone.utc))
    assert f'_PIPELINE_ID = "{edge_cron.PIPELINE_ID}"' in (DAGS / "edge_batch.py").read_text(encoding="utf-8")


def test_slot_is_refused_when_the_host_clock_is_not_kst(tmp_path):
    # WHY: 날짜 인자가 없는 수집은 "지금" 값을 슬롯 날짜로 라벨한다. cron 이 UTC 로 돌면 07:00 줄이 16:00 KST 에
    # 떠서 엉뚱한 시각의 값을 07:00 슬롯으로 저장한다 — 조용히 틀리느니 시작하지 않고 알린다.
    code, steps, mails, rows = run_lane(tmp_path, "premarket", late_minutes=9 * 60)
    assert (code, steps, rows) == (1, [], []) and len(mails) == 1


# ── 스텝 순서와 관문 ──

@pytest.mark.parametrize("lane", DAG_LANES)
def test_lane_runs_the_dag_steps_in_dependency_order(tmp_path, lane):
    # WHY: DAG 가 나란히 돌리던 스텝을 한 줄로 세웠다. 순서가 바뀌면 정제가 수집보다 먼저 돌거나(빈 입력),
    # 가격변동 트리거가 가격·holdings 적재보다 먼저 떠 옛 DB 를 읽는다(ALPHA-1039).
    code, steps, mails, rows = run_lane(tmp_path, lane)
    assert steps == _expected(lane)
    # 모두 성공하면 성공 행 하나(레인, 슬롯 시각)만 쓰고 메일은 없다.
    assert (code, mails, rows) == (0, [], [[lane, _slot(LANES[lane][2]).isoformat()]])
    # DAG 에 스텝이 늘거나 빠지면 여기서 걸린다(원장 스텝 plan-run·reconcile 은 옮기지 않았다).
    dag_source = (DAGS / LANES[lane][1]).read_text(encoding="utf-8")
    dag_steps = set(re.findall(r'"((?:ingest|normalize|load|tag|assemble|enrich|build)-[a-z-]+)"', dag_source))
    assert {step[0] for step in steps} == dag_steps


@pytest.mark.parametrize("lane, exits, not_run", [
    # 수집이 실패해도 정제·적재는 돈다(ASL NotifyRawPartial → Normalize).
    ("investor-intraday", "ingest-raw-investor-estimate=1", set()),
    # 정제 부분 실패(2)는 적재로 넘어가고, 그 밖의 정제 실패는 적재를 막는다.
    ("investor-intraday", "normalize-investor-estimate=2", set()),
    ("investor-intraday", "normalize-investor-estimate=1", {"load-investor-intraday"}),
    ("disclosure", "normalize-disclosure-segment=1", {"load-disclosure"}),
    # 장전 레인은 전량 성공 게이트다 — 반쪽 입력으로 유니버스를 갈아끼우지 않는다.
    ("premarket", "ingest-raw-etf=2", {"normalize-etf", "build-minute-universe"}),
    # 뉴스 정제는 exit 0 만 통과한다.
    ("news", "normalize-news=2", {"load-documents", "tag-news", "load-assertions", "assemble-events"}),
    ("news", "tag-news=1", {"load-assertions", "assemble-events"}),
    # 한 계열의 실패가 다른 계열을 막지 않는다.
    ("source-daily", "normalize-macro=1", {"load-macro"}),
    # 정제 하나가 깨지면 적재 전부를 돌리지 않는다. holdings 적재만 exit 0 이어야 트리거가 돈다.
    ("etf-daily", "normalize-etf=1", {"load-instruments", "enrich-corp-code", "load-etf-nav", "load-price-daily",
                                     "load-etf-holdings", "load-etf-flow", "load-market-series-daily",
                                     "load-price-triggers"}),
    ("etf-daily", "load-etf-holdings=2", {"load-price-triggers"}),
    ("etf-daily", "load-etf-nav=2", set()),
    # 신호로 죽은 스텝(137) 뒤로는 아무것도 돌리지 않는다 — 어디까지 썼는지 모른다.
    ("disclosure", "ingest-raw-disclosure=137", {"normalize-disclosure", "normalize-disclosure-segment",
                                                 "load-disclosure"}),
])
def test_step_failure_gates_downstream_and_never_reports_success(tmp_path, lane, exits, not_run):
    # WHY: 관문은 DAG 의 trigger rule·partial_exit_codes 를 옮긴 것이다. 그리고 exit 0 이 아닌 스텝이 하나라도
    # 있으면 성공 행을 쓰지 않고 통보한다 — 부분 실패를 성공으로 접으면 감시가 그 슬롯을 영영 못 본다.
    code, steps, mails, rows = run_lane(tmp_path, lane, exits=exits)
    assert {step[0] for step in _expected(lane)} - {step[0] for step in steps} == not_run
    assert (code, rows) == (1, []) and len(mails) == 1 and mails[0].startswith(f"[{lane}] FAILED")


def test_lock_timeout_runs_nothing_and_alerts(tmp_path):
    # WHY: 락을 못 잡았다고 조용히 건너뛰면 그 슬롯이 빠진 것을 아무도 모른다.
    code, steps, mails, rows = run_lane(tmp_path, "investor-intraday", STUB_FLOCK_EXIT="1")
    assert (code, steps, rows) == (1, [], []) and len(mails) == 1


def test_missing_lane_argument_runs_nothing_and_alerts(tmp_path):
    # WHY: DAG 의 lane_arg 와 같다 — 빈 인자나 옛 상수로 업무를 돌리지 않는다.
    code, steps, mails, rows = run_lane(tmp_path, "premarket", env_lines=ENV_LINES[:1])
    assert (code, steps, rows) == (1, [], []) and len(mails) == 1


def test_kis_token_cache_dir_is_mounted_at_the_same_path(tmp_path):
    # WHY: 스텝마다 컨테이너가 새로 뜬다. 캐시 디렉터리를 같은 경로로 붙이지 않으면 스텝마다 KIS 토큰을 새로 받고,
    # KIS 는 잦은 재발급을 막는다. 없는 디렉터리는 docker 가 root 소유로 만들어 버리므로 시작 전에 거부한다.
    cache = tmp_path / "kis-token"
    env_lines = [*ENV_LINES, f"KIS_TOKEN_CACHE_DIR={cache}"]
    code, steps, mails, _rows = run_lane(tmp_path, "investor-intraday", env_lines=env_lines)
    assert (code, steps) == (1, []) and len(mails) == 1
    cache.mkdir()
    code, steps, mails, _rows = run_lane(tmp_path, "investor-intraday", env_lines=env_lines,
                                         mount=("-v", f"{cache}:{cache}"))
    assert (code, steps, mails) == (0, _expected("investor-intraday"), [])


def test_snapshot_retention_runs_with_the_db_disk_attached(tmp_path):
    # WHY: 정리는 삭제 중 DB 디스크 여유를 보고 멈춘다(11-16 에 디스크가 가득 찼다). 컨테이너가 그 디스크를 보지 못하면
    # 보호 없이 지우게 되므로, 경로를 모르면 레인이 시작하지 않는다. AWS 태스크와 같은 진입점·MODE=run 으로 돈다.
    attached = ("-e", "MODE=run", "-v", f"{DB_DISK}:{DB_DISK}:ro", "--entrypoint", "python")
    code, steps, mails, rows = run_lane(tmp_path, "snapshot-retention", mount=attached)
    assert (code, steps, mails) == (0, _expected("snapshot-retention"), []) and len(rows) == 1
    # exit 3 = 디스크 여유가 모자라 중간에 멈춤 — 실패로 알린다.
    code, _steps, mails, rows = run_lane(tmp_path, "snapshot-retention", mount=attached, exits="-m=3")
    assert (code, rows) == (1, []) and len(mails) == 1
    code, steps, mails, rows = run_lane(tmp_path, "snapshot-retention", env_lines=ENV_LINES[:1])
    assert (code, steps, rows) == (1, [], []) and len(mails) == 1


def test_snapshot_retention_refuses_to_delete_without_a_disk_guard(tmp_path, monkeypatch):
    # WHY: RDS 지표가 없는 VM 에서 예전처럼 보호를 조용히 끄면, 삭제가 만드는 WAL 로 디스크가 차도 멈추지 못한다.
    import psycopg
    from data_pipeline.ops import snapshot_retention

    def no_connection(*args, **kwargs):
        raise AssertionError("디스크 여유를 읽을 곳이 없는데 DB 에 접속했다")

    monkeypatch.setattr(psycopg, "connect", no_connection)
    monkeypatch.delenv("RDS_INSTANCE_ID", raising=False)
    monkeypatch.delenv("DB_DISK_PATH", raising=False)
    monkeypatch.setenv("MODE", "run")
    assert snapshot_retention.main() == 2
    # VM: 경로가 가리키는 파일시스템의 여유를 읽는다.
    free = snapshot_retention.free_source({"DB_DISK_PATH": str(tmp_path)})()
    assert 0 < free <= shutil.disk_usage(tmp_path).total
    # AWS: RDS 식별자가 있으면 종전대로 CloudWatch 를 읽는다(둘 다 있어도).
    monkeypatch.setattr(snapshot_retention, "rds_free_bytes", lambda client, instance: f"rds:{instance}")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    assert snapshot_retention.free_source({"RDS_INSTANCE_ID": "edge-dev", "DB_DISK_PATH": str(tmp_path)})() \
        == "rds:edge-dev"


def test_unwritten_success_row_is_a_failure(tmp_path):
    code, steps, mails, _rows = run_lane(tmp_path, "dividend-daily", STUB_SQL_EXIT="1")
    assert code == 1 and steps == _expected("dividend-daily") and len(mails) == 1


# ── 슬롯 표와 감시 ──

def test_crontab_fires_exactly_the_slots_the_watchdog_expects():
    # WHY: crontab 에만 있는 슬롯은 감시가 안 보고, 표에만 있는 슬롯은 매일 헛통보한다. 줄의 HH:MM 인자가
    # cron 시각과 다르면 run_id 가 다른 슬롯의 것이 된다.
    line = re.compile(r"^(\d+) (\d+) \* \* (\S+) \$EDGE/vm(\d)/(\w+)\.sh (\d\d:\d\d) ", re.M)
    fired = set()
    for vm in (1, 3):
        for minute, hour, days, script_vm, script, hhmm in line.findall((OCI / f"vm{vm}" / "crontab").read_text()):
            assert (int(script_vm), f"{int(hour):02d}:{int(minute):02d}") == (vm, hhmm)
            lane = re.search(r"^begin_lane (\S+) ", (OCI / f"vm{vm}" / f"{script}.sh").read_text(), re.M).group(1)
            fired.add((vm, lane, hhmm, days))
    assert fired == {entry[:4] for entry in edge_cron.SLOTS}


def test_watchdog_mails_a_slot_without_a_success_row_once(tmp_path, monkeypatch):
    # WHY: 원장 Planner·Reconciler 를 뗀 뒤 "레인이 아예 안 돌았다"를 잡는 것은 이 감시뿐이다.
    now = datetime(2026, 11, 30, 9, 0, tzinfo=KST)       # 월요일 09:00 — 08:10 뉴스는 아직 기한(09:40) 전이다
    due = watchdog.due_slots(now)
    assert due == [("news", "2026-11-30T00:10"), ("dividend-daily", "2026-11-30T03:00"),
                   ("snapshot-retention", "2026-11-30T04:30"), ("source-daily", "2026-11-30T05:20"),
                   ("premarket", "2026-11-30T07:00")]
    # 주말에는 평일 레인을 기대하지 않는다(헛통보 방지).
    assert {lane for lane, _slot_time in watchdog.due_slots(datetime(2026, 11, 28, 23, 0, tzinfo=KST))} \
        == {"news", "dividend-daily", "snapshot-retention", "source-daily"}
    sent = []
    monkeypatch.setattr(watchdog, "STATE_FILE", tmp_path / "alerted")
    monkeypatch.setattr(watchdog, "done_slots", lambda: set(due) - {("premarket", "2026-11-30T07:00")})
    monkeypatch.setattr(edge_cron, "send_mail", lambda subject, body, conf: sent.append((subject, body)))
    watchdog.check_slots(now, {"OPS_KR_HOLIDAYS": ""})
    assert len(sent) == 1 and "premarket 2026-11-30T07:00" in sent[0][1] and "source-daily" not in sent[0][1]
    assert "20261129T220000Z" in sent[0][1]              # 다시 돌릴 때 쓸 run_id 를 본문에 싣는다
    watchdog.check_slots(now, {"OPS_KR_HOLIDAYS": ""})   # 같은 슬롯을 15분마다 다시 알리지 않는다
    assert len(sent) == 1


def test_vms_are_not_started_on_a_krx_holiday(monkeypatch):
    # WHY: 휴장일에 VM-2·VM-3 을 켜면 장중 컨테이너가 재시작을 되풀이한다. 판정은 수집 스텝과 같은 달력 코드와
    # 같은 휴장일 값(OPS_KR_HOLIDAYS)이어야 한다 — 갈리면 한쪽은 쉬고 한쪽은 도는 날이 생긴다.
    from data_pipeline.ops import trading_calendar
    assert watchdog.TRADING_CALENDAR == Path(trading_calendar.__file__)
    conf = {"EDGE_VM2_OCID": "ocid.vm2", "EDGE_VM3_OCID": "ocid.vm3", "OPS_KR_HOLIDAYS": "2026-12-25"}
    calls = []
    monkeypatch.setattr(watchdog, "oci", lambda *args: calls.append(args[-1])
                        or subprocess.CompletedProcess(args, 0, "RUNNING\n", ""))
    holiday, eve = datetime(2026, 12, 25, 7, 50, tzinfo=KST), datetime(2026, 12, 24, 7, 50, tzinfo=KST)
    watchdog.run(["vm", "start", "VM2", "VM3", "--trading-day"], conf, now=holiday)     # 금요일이지만 성탄절
    watchdog.run(["vm", "check", "VM2", "--trading-day"], conf, now=holiday)
    assert calls == []
    watchdog.run(["vm", "start", "VM2", "VM3", "--trading-day"], conf, now=eve)
    assert calls == ["START", "START"]
    # 00:00 뉴스 배치용 켜기는 휴장일에도 한다.
    watchdog.run(["vm", "start", "VM3"], conf, now=holiday)
    assert calls == ["START", "START", "START"]
    # 휴장일엔 08:10 뉴스 슬롯을 기대하지 않는다(VM-3 이 꺼져 있다). 평일 레인은 VM-1 에서 그대로 돈다.
    due = watchdog.due_slots(holiday.replace(hour=12), lambda day: watchdog.is_trading_day(day, conf))
    assert ("news", "2026-12-25T08:10") not in due and ("premarket", "2026-12-25T07:00") in due
    assert ("news", "2026-12-24T08:10") in watchdog.due_slots(
        eve.replace(hour=12), lambda day: watchdog.is_trading_day(day, conf))
    # 휴장일 값이 없으면 거래일로 치지 않고 멈춘다.
    with pytest.raises(RuntimeError):
        watchdog.run(["vm", "start", "VM2", "--trading-day"], {"EDGE_VM2_OCID": "ocid.vm2"}, now=holiday)


def test_stopping_an_already_stopped_vm_is_not_a_failure(monkeypatch):
    # WHY: 16:30 끄기는 휴장일에도 돈다. 이미 꺼진 VM 에 대한 거절을 실패로 알리면 휴장일마다 헛통보가 간다.
    answers = {"action": subprocess.CompletedProcess((), 1, "", "409 Conflict"),
               "get": subprocess.CompletedProcess((), 0, "STOPPED\n", "")}
    monkeypatch.setattr(watchdog, "oci", lambda *args: answers[args[2]])
    watchdog.vm("stop", "VM2", {"EDGE_VM2_OCID": "ocid.vm2"})
    with pytest.raises(RuntimeError):       # 켜기가 거절됐고 꺼져 있다 = 진짜 실패
        watchdog.vm("start", "VM2", {"EDGE_VM2_OCID": "ocid.vm2"})


def test_watchdog_mails_when_the_lake_grows_more_than_5_gib_since_the_last_check(tmp_path, monkeypatch):
    # WHY: AWS 의 레이크 하루 증가 알람(5GiB)을 대신한다 — 11월에 분석 실행 기록 117GiB 가 쌓인 것을 사람이 재 보고서야 알았다.
    sizes, sent, gib = iter([100, 104, 110]), [], 1024**3
    monkeypatch.setattr(watchdog, "LAKE_STATE_FILE", tmp_path / "lake-size")
    monkeypatch.setattr(watchdog, "lake_bytes", lambda path: next(sizes) * gib)
    monkeypatch.setattr(edge_cron, "send_mail", lambda subject, body, conf: sent.append(subject))
    for day in (28, 29, 30):        # 첫 측정은 기준값, +4GiB 는 조용히, +6GiB 는 알린다
        watchdog.check_lake(datetime(2026, 11, day, 6, 0, tzinfo=KST), {"EDGE_LAKE_DATA_DIR": "/data/minio"})
    assert sent == ["[watchdog] 레이크가 6.0GiB 늘었다"]


def test_watchdog_mails_when_it_cannot_do_its_job(monkeypatch):
    # WHY: 조회 실패·OCI 호출 실패를 삼키면 "빠진 슬롯 없음"·"VM 켜짐"과 구별되지 않는다.
    sent = []
    monkeypatch.setattr(edge_cron, "read_conf", lambda: {"EDGE_VM2_OCID": "ocid1.instance.test"})
    monkeypatch.setattr(edge_cron, "send_mail", lambda subject, body, conf: sent.append(subject))
    monkeypatch.setattr(watchdog, "oci", lambda *args: subprocess.CompletedProcess(args, 0, "STOPPED\n", ""))
    assert watchdog.main(["vm", "check", "VM2"]) == 1 and sent == ["[watchdog] FAILED — vm check VM2"]
    monkeypatch.setattr(watchdog, "oci", lambda *args: subprocess.CompletedProcess(args, 0, "RUNNING\n", ""))
    assert watchdog.main(["vm", "check", "VM2"]) == 0 and len(sent) == 1
