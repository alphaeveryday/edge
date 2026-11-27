#!/usr/bin/env python3
"""VM-1 감시 — 성공 행이 없는 슬롯을 알리고, VM-2·VM-3 을 정해진 시각에 켜고 끈다. 표준 라이브러리만 쓴다.

    watchdog.py slots                         # 기한이 지났는데 성공 행이 없는 슬롯을 메일로 알린다
    watchdog.py lake                          # 레이크가 지난 측정보다 5GiB 넘게 늘었으면 메일로 알린다(하루 한 번)
    watchdog.py holidays                      # OPS_KR_HOLIDAYS 에 올해 날짜가 없으면, 12월에는 내년 날짜가 없어도
                                              # 메일로 알린다(매달 1일)
    watchdog.py vm start|stop|check VM2 [VM3] [--trading-day]
                                              # OCI CLI(인스턴스 주체 인증)로 켜기·끄기·RUNNING 확인.
                                              # --trading-day 를 주면 KR 휴장일에는 아무것도 하지 않는다
    watchdog.py vm stop-when-done VM2 <레인> <HH:MM> <포기 HH:MM>
                                              # 오늘 그 슬롯의 성공 행이 있을 때만 끈다. 없으면 다음 실행을 기다리고,
                                              # 포기 시각에도 없으면 메일을 보내고 켜 둔다

끄기는 모두 SOFTSTOP 이다(OS 종료를 기다린다). 무엇이든 실패하면 메일을 보내고 0 이 아닌 값으로 끝난다.
실행 시각은 vm1/crontab 에 있다. 설정은 batch.env, 휴장일은 pipeline.env 의 OPS_KR_HOLIDAYS 를 읽는다.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import edge_cron  # noqa: E402
from edge_cron import KST, SLOTS, TRADING_DAY_ONLY  # noqa: E402

# 수집 스텝이 쓰는 거래일 판정 코드. 호스트에는 data_pipeline 패키지가 없어 파일로 읽는다(표준 라이브러리만 쓰는 모듈이다).
TRADING_CALENDAR = (Path(__file__).resolve().parents[3]
                    / "apps/engine/data-pipeline/src/data_pipeline/ops/trading_calendar.py")
# 기한이 지난 지 이 시간 안의 슬롯만 본다. VM-1 이 하루 안쪽으로 꺼졌다 켜져도 그 사이 슬롯을 알린다.
LOOKBACK = timedelta(hours=24)
STATE_FILE = Path(os.environ.get("EDGE_WATCHDOG_STATE", "/var/lib/edge/watchdog-alerted"))
LAKE_STATE_FILE = Path(os.environ.get("EDGE_LAKE_STATE", "/var/lib/edge/lake-size"))
# AWS 의 레이크 하루 증가 알람과 같은 기준(infra/terraform/envs/dev/storage-growth.tf).
LAKE_GROWTH_LIMIT = 5 * 1024**3
DONE_SQL = ("SELECT lane, to_char(slot_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD\"T\"HH24:MI') "
            "FROM batch_lane_success WHERE slot_at > now() - interval '3 days'")
OCI_ACTION = {"start": "START", "stop": "SOFTSTOP"}
# 켜기·끄기 요청이 거절돼도 이미 그 상태(또는 그리로 가는 중)면 된 것으로 본다.
SETTLED = {"start": ("RUNNING", "STARTING"), "stop": ("STOPPED", "STOPPING")}


def holidays(conf: dict) -> frozenset[str]:
    days = frozenset(value.strip() for value in conf.get("OPS_KR_HOLIDAYS", "").split(",") if value.strip())
    if not days:    # 키가 없든 값이 비었든 같다 — 예시 파일의 빈 줄을 그대로 둔 경우를 거래일 판정으로 넘기지 않는다
        raise RuntimeError("pipeline.env 의 OPS_KR_HOLIDAYS 가 없거나 비었다 — 휴장일을 거래일로 볼 수 없어 멈춘다")
    return days


def is_trading_day(day: datetime, conf: dict) -> bool:
    """KR 거래일인가 — 수집 스텝과 같은 코드(trading_calendar.is_trading_day)와 같은 휴장일 값(OPS_KR_HOLIDAYS)으로 판정한다."""
    spec = importlib.util.spec_from_file_location("edge_trading_calendar", TRADING_CALENDAR)
    calendar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calendar)
    return calendar.is_trading_day(day.astimezone(KST).date(), holidays(conf))


def check_holidays(now: datetime, conf: dict) -> None:
    """휴장일 목록에 올해 날짜가 없으면 알린다. 12월에는 내년 날짜가 없어도 알린다.

    목록에 없는 해는 평일 휴장일을 전부 거래일로 본다 — VM-2·VM-3 이 켜지고 수집도 거래일로 판정한다.
    """
    today, days = now.astimezone(KST), holidays(conf)
    years = [today.year] + ([today.year + 1] if today.month == 12 else [])
    missing = [str(year) for year in years if not any(day.startswith(str(year)) for day in days)]
    if missing:
        edge_cron.send_mail(
            f"[watchdog] OPS_KR_HOLIDAYS 에 {'·'.join(missing)}년 휴장일이 없다",
            "휴장일이 없는 해는 평일 휴장일에 VM-2·VM-3 이 켜지고 수집이 거래일로 판정한다.\n"
            "모든 VM 의 pipeline.env 에서 OPS_KR_HOLIDAYS 를 갱신한다.")


def due_slots(now: datetime, trading_day=lambda day: True) -> list[tuple[str, str]]:
    """성공 행 기한이 지난 슬롯 [(레인, 'YYYY-MM-DDTHH:MM' KST)] — 기한이 지난 지 LOOKBACK 안의 것만."""
    due = []
    for _vm, lane, hhmm, days, grace in SLOTS:
        for back in range(3):
            day = now.astimezone(KST) - timedelta(days=back)
            if days == "1-5" and day.weekday() >= 5:
                continue
            slot = edge_cron.slot_at(hhmm, day)
            if not now - LOOKBACK < slot + timedelta(minutes=grace) <= now:
                continue
            if (lane, hhmm) in TRADING_DAY_ONLY and not trading_day(slot):
                continue
            due.append((lane, slot.strftime("%Y-%m-%dT%H:%M")))
    return sorted(due, key=lambda item: (item[1], item[0]))


def done_slots() -> set[tuple[str, str]]:
    result = subprocess.run([str(LIB / "edge_sql.sh"), DONE_SQL], capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"성공 행 조회 실패(exit {result.returncode}): {result.stderr[-2000:]}")
    return {tuple(line.split("\t")) for line in result.stdout.splitlines() if line}


def check_slots(now: datetime, conf: dict) -> None:
    """빠진 슬롯을 한 통으로 알리고, 알린 슬롯을 적어 둔다(같은 슬롯을 다시 알리지 않게).

    메일이 실패하면 적지 않으므로 다음 실행이 다시 알린다. ponytail: DB 가 죽어 있으면 실행마다 조회 실패 메일이 간다.
    """
    alerted = {tuple(line.split()) for line in STATE_FILE.read_text().splitlines()} if STATE_FILE.exists() else set()
    done = done_slots()
    missing = [slot for slot in due_slots(now, lambda day: is_trading_day(day, conf))
               if slot not in done and slot not in alerted]
    if not missing:
        return
    lines = [f"- {lane} {slot} (run_id {edge_cron.run_id(lane, datetime.fromisoformat(slot).replace(tzinfo=KST))})"
             for lane, slot in missing]
    edge_cron.send_mail(
        f"[watchdog] 성공 행이 없는 슬롯 {len(missing)}건",
        "기한이 지났는데 batch_lane_success 에 행이 없다. 레인이 실패했거나, 돌지 않았거나, 아직 도는 중이다.\n"
        "레인이 스스로 보낸 실패 메일과 호스트의 /var/log/edge 로그를 확인한다.\n\n" + "\n".join(lines))
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("a") as handle:
        handle.writelines(f"{lane} {slot}\n" for lane, slot in missing)


def lake_bytes(path: str) -> int:
    result = subprocess.run(["du", "-sb", path], capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"레이크 크기를 재지 못했다(du exit {result.returncode}): {result.stderr[-2000:]}")
    return int(result.stdout.split()[0])


def check_lake(now: datetime, conf: dict) -> None:
    """레이크(MinIO 데이터 디렉터리) 크기를 재서 지난 측정보다 LAKE_GROWTH_LIMIT 넘게 늘었으면 알린다."""
    path = conf.get("EDGE_LAKE_DATA_DIR")
    if not path:
        raise RuntimeError("batch.env 에 EDGE_LAKE_DATA_DIR 가 없다")
    size = lake_bytes(path)
    previous = LAKE_STATE_FILE.read_text().split() if LAKE_STATE_FILE.exists() else None
    if previous and size - int(previous[1]) > LAKE_GROWTH_LIMIT:
        edge_cron.send_mail(
            f"[watchdog] 레이크가 {(size - int(previous[1])) / 1024**3:.1f}GiB 늘었다",
            f"{path}: {previous[0]} 에 {int(previous[1]) / 1024**3:.1f}GiB, 지금 {size / 1024**3:.1f}GiB.\n"
            "접두사별 크기를 비교해 늘어난 곳을 찾고, 수명 규칙이 필요한지 본다(infra/oci/README.md \"MinIO 수명 규칙\").")
    LAKE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAKE_STATE_FILE.write_text(f"{now.isoformat()} {size}\n")


def oci(conf: dict, *args: str) -> subprocess.CompletedProcess:
    """OCI CLI 를 batch.env 의 절대 경로(OCI_CLI)로 부른다 — cron 의 PATH 에 기대지 않는다."""
    cli = conf.get("OCI_CLI", "")
    if not os.access(cli, os.X_OK):
        raise RuntimeError(f"batch.env 의 OCI_CLI 를 실행할 수 없다: {cli or '(값 없음)'}")
    return subprocess.run([cli, *args, "--auth", "instance_principal"], capture_output=True, text=True, timeout=120)


def _ocid(name: str, conf: dict) -> str:
    ocid = conf.get(f"EDGE_{name}_OCID")
    if not ocid:
        raise RuntimeError(f"batch.env 에 EDGE_{name}_OCID 가 없다")
    return ocid


def vm_state(name: str, conf: dict) -> subprocess.CompletedProcess:
    """인스턴스 상태 조회 결과(stdout 이 RUNNING·STOPPED 등)."""
    return oci(conf, "compute", "instance", "get", "--instance-id", _ocid(name, conf),
               "--query", 'data."lifecycle-state"', "--raw-output")


def vm(action: str, name: str, conf: dict) -> None:
    """VM 하나를 켜거나 끄거나, RUNNING 인지 확인한다. 안 되면 RuntimeError."""
    if action == "check":
        state = vm_state(name, conf)
        if state.returncode != 0 or state.stdout.strip() != "RUNNING":
            raise RuntimeError(f"{name} 이 RUNNING 이 아니다: {state.stdout.strip() or state.stderr[-2000:]}")
        return
    result = oci(conf, "compute", "instance", "action", "--instance-id", _ocid(name, conf),
                 "--action", OCI_ACTION[action])
    if result.returncode != 0 and vm_state(name, conf).stdout.strip() not in SETTLED[action]:
        raise RuntimeError(f"{name} {OCI_ACTION[action]} 실패: {result.stderr[-2000:]}")


def stop_when_done(name: str, lane: str, hhmm: str, give_up: str, now: datetime, conf: dict) -> None:
    """오늘 <레인> <HH:MM> 슬롯의 성공 행이 있을 때만 VM 을 끈다 — 일이 도는 중에 끄지 않는다.

    행이 없으면 아무것도 하지 않고 다음 실행(cron 10분 간격)을 기다린다. 포기 시각에도 없으면 RuntimeError(메일)로
    알리고 VM 은 켜 둔다. 이미 꺼져 있으면(휴장일 등) 아무것도 하지 않는다.
    """
    if vm_state(name, conf).stdout.strip() in SETTLED["stop"]:
        return
    slot = edge_cron.slot_at(hhmm, now).strftime("%Y-%m-%dT%H:%M")
    if (lane, slot) in done_slots():
        vm("stop", name, conf)
    elif now >= edge_cron.slot_at(give_up, now):
        raise RuntimeError(f"{lane} {slot} 의 성공 행이 {give_up} 까지 없어 {name} 을 끄지 않았다. "
                           "일이 끝났는지 확인하고 손으로 끈다")
    else:
        print(f"{now.isoformat()} {name}: {lane} {slot} 성공 행을 기다린다")


def run(argv: list[str], conf: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(KST)
    if argv == ["slots"]:
        check_slots(now, conf)
    elif argv == ["lake"]:
        check_lake(now, conf)
    elif argv == ["holidays"]:
        check_holidays(now, conf)
    elif len(argv) == 6 and argv[:2] == ["vm", "stop-when-done"]:
        stop_when_done(*argv[2:], now=now, conf=conf)
    elif len(argv) >= 3 and argv[0] == "vm" and argv[1] in ("start", "stop", "check"):
        names = [arg for arg in argv[2:] if arg != "--trading-day"]
        if "--trading-day" in argv and not is_trading_day(now, conf):
            print(f"{now.date()} 휴장일 — vm {argv[1]} {' '.join(names)} 건너뜀")
            return
        errors = []
        for name in names:   # 하나가 실패해도 나머지는 한다
            try:
                vm(argv[1], name, conf)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("\n".join(errors))
    else:
        raise RuntimeError(f"모르는 명령이다: {argv} — crontab 줄을 확인한다")


def main(argv: list[str]) -> int:
    try:
        # 휴장일은 스텝 컨테이너가 읽는 값을 그대로 쓴다(pipeline.env). 나머지 설정은 batch.env.
        conf = edge_cron.read_conf()
        pipeline = edge_cron.read_conf("pipeline.env")
        if "OPS_KR_HOLIDAYS" in pipeline:
            conf["OPS_KR_HOLIDAYS"] = pipeline["OPS_KR_HOLIDAYS"]
        run(argv, conf)
    except Exception as exc:
        print(f"FAILED {' '.join(argv)}: {exc}", file=sys.stderr)
        edge_cron.send_mail(f"[watchdog] FAILED — {' '.join(argv)}", f"{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
