"""OCI 배치 cron 의 성공 행을 실 PostgreSQL(Flyway 로 만든 batch_lane_success)에서 쓰고 읽어 본다.

레인이 쓰는 INSERT(infra/oci/lib/batch.sh), 감시가 읽는 조회(infra/oci/vm1/watchdog.py DONE_SQL), 둘이 지나는
edge_sql.sh 를 실제로 실행한다. docker 만 대역이다 — 이미지 안에서 돌 파이썬 본문을 이 테스트의 파이썬으로 실행한다.
감시는 조회 결과의 (레인, KST 분) 문자열로 슬롯을 맞추므로, 시간대 변환이 틀리면 모든 슬롯을 날마다 누락으로 알린다.
"""

import importlib.util
import os
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

OCI = Path(__file__).resolve().parents[5] / "infra" / "oci"
KST = timezone(timedelta(hours=9))
# `docker run --rm --env-file F --entrypoint python IMAGE -c BODY 값…` 을 받아, F 의 env 로 `python -c BODY 값…` 을 실행한다.
DOCKER_AS_PYTHON = """#!{python}
import os, sys
args = sys.argv[1:]
with open(args[args.index("--env-file") + 1]) as handle:
    for line in handle:
        key, _, value = line.rstrip("\\n").partition("=")
        if key:
            os.environ[key] = value
os.execv(sys.executable, [sys.executable, *args[args.index("-c"):]])
"""


def test_success_row_written_by_a_lane_is_the_slot_the_watchdog_reads(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("oci_watchdog_e2e", OCI / "vm1" / "watchdog.py")
    watchdog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watchdog)

    conf, stubs = tmp_path / "conf", tmp_path / "bin"
    conf.mkdir()
    stubs.mkdir()
    (conf / "batch.env").write_text("EDGE_PIPELINE_IMAGE=unused\n")
    (conf / "pipeline.env").write_text(
        f"DATA_PIPELINE_DB__HOST={os.environ['E2E_PGHOST']}\nDATA_PIPELINE_DB__PORT={os.environ['E2E_PGPORT']}\n"
        "DATA_PIPELINE_DB__NAME=edge\nDATA_PIPELINE_DB__USER=edge\nDATA_PIPELINE_DB__PASSWORD=edge\n"
        "DATA_PIPELINE_DB__SSLMODE=disable\n")
    docker = stubs / "docker"
    docker.write_text(DOCKER_AS_PYTHON.format(python=sys.executable))
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{stubs}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("EDGE_CONF_DIR", str(conf))

    edge_sql = str(OCI / "lib" / "edge_sql.sh")
    insert = re.search(r'"(INSERT INTO batch_lane_success [^"]+)"', (OCI / "lib" / "batch.sh").read_text()).group(1)
    lane = "e2e-" + uuid.uuid4().hex[:8]
    # 00:10 KST 슬롯 — UTC 로는 전날 15:10 이라 시간대 변환이 빠지면 날짜까지 달라진다.
    slot = datetime.now(KST).replace(hour=0, minute=10, second=0, microsecond=0)
    try:
        for _ in range(2):      # 같은 슬롯을 다시 돌려도 한 행이다(finished_at 만 갱신)
            subprocess.run([edge_sql, insert, lane, slot.isoformat()], check=True, timeout=120)
        count = subprocess.run([edge_sql, "SELECT count(*) FROM batch_lane_success WHERE lane = %s", lane],
                               check=True, capture_output=True, text=True, timeout=120).stdout.strip()
        assert count == "1"
        assert (lane, slot.strftime("%Y-%m-%dT%H:%M")) in watchdog.done_slots()
    finally:
        subprocess.run([edge_sql, "DELETE FROM batch_lane_success WHERE lane = %s", lane], check=True, timeout=120)
