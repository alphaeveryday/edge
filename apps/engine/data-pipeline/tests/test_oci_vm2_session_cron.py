"""VM-2 세션 cron(infra/oci/vm2) — 장중 수집을 올리고 내리는 유일한 주체다.

의도: 이 스크립트의 결함은 **그날 하루**를 잃는다. 휴장일에 올리면 워커가 하루 종일 재시작을 돌고,
거래일 판정이 깨졌는데 올리면 같은 일이 조용히 난다. stop 이 실패했는데 컨테이너를 내리면 처리 중이던
일감이 잘린다. 가짜 docker 를 PATH 앞에 두고 스크립트를 실제로 돌려 본다.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

VM2 = Path(__file__).resolve().parents[4] / "infra/oci/vm2"

FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *"--entrypoint python session-start"*) exit "${FAKE_TRADING_EXIT:-0}" ;;
  *"run --rm session-stop"*) exit "${FAKE_STOP_EXIT:-0}" ;;
esac
exit 0
"""


@pytest.fixture
def run(tmp_path):
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    calls = tmp_path / "calls.log"

    def invoke(script, **env):
        calls.write_text("")
        result = subprocess.run(
            ["bash", str(VM2 / script), "08:05"], capture_output=True, text=True,
            env={"PATH": f"{fake.parent}:{os.environ['PATH']}", "FAKE_DOCKER_LOG": str(calls), **env},
        )
        return result.returncode, calls.read_text().splitlines(), result.stdout
    return invoke


def test_crontab_argument_matches_its_cron_time_and_scripts_exist():
    """HH:MM 인자가 cron 시각과 다르면 로그와 성공 행 슬롯이 실제 실행 시각과 갈린다."""
    line = re.compile(r"^(\d+) (\d+) \* \* 1-5 \$EDGE/vm2/(\w+)\.sh (\d\d:\d\d) ", re.M)
    found = line.findall((VM2 / "crontab").read_text())
    assert {script: hhmm for _, _, script, hhmm in found} == {
        "session_start": "08:05", "session_stop": "16:10"}
    for minute, hour, script, hhmm in found:
        assert f"{int(hour):02d}:{int(minute):02d}" == hhmm
        assert os.access(VM2 / f"{script}.sh", os.X_OK), "cron 이 직접 실행한다"


def test_stop_slot_is_the_success_row_slot():
    """감시는 (minute_session_stop, 16:10) 행을 기다린다 — cron 시각만 옮기면 행의 슬롯과 갈린다."""
    from data_pipeline.minute import session_ops

    hour, minute = session_ops.STOP_SUCCESS_SLOT_KST
    assert f"{minute} {hour} * * 1-5 $EDGE/vm2/session_stop.sh" in (VM2 / "crontab").read_text()


def test_start_brings_services_up_on_a_trading_day(run):
    code, calls, _ = run("session_start.sh")
    assert code == 0
    assert calls[-1] == "compose up -d"


def test_start_does_nothing_on_a_non_trading_day(run):
    code, calls, out = run("session_start.sh", FAKE_TRADING_EXIT="3")
    assert code == 0, "휴장일은 실패가 아니다"
    assert not any("up -d" in call for call in calls)
    assert "비거래일" in out


def test_start_refuses_when_the_calendar_check_itself_fails(run):
    """판정을 못 했는데(이미지·.env 문제) 올리면, 휴장일 가드가 조용히 사라진다."""
    code, calls, _ = run("session_start.sh", FAKE_TRADING_EXIT="125")
    assert code == 125
    assert not any("up -d" in call for call in calls)


def test_stop_removes_containers_only_after_a_successful_stop(run):
    code, calls, _ = run("session_stop.sh")
    assert (code, calls) == (0, ["compose run --rm session-stop", "compose down"])


@pytest.mark.parametrize("stop_exit", ["1", "2"])
def test_failed_stop_leaves_containers_running_and_reports_its_exit(run, stop_exit):
    """exit 1 = 게이트·분석 대기 상한 초과. 그때 내리면 처리 중이던 일감이 잘린다."""
    code, calls, _ = run("session_stop.sh", FAKE_STOP_EXIT=stop_exit)
    assert code == int(stop_exit)
    assert calls == ["compose run --rm session-stop"]
