#!/usr/bin/env python3
"""OCI 배치 cron 공통 — 슬롯 표, run_id, 설정 읽기, 통보 메일. 표준 라이브러리만 쓴다.

레인 스크립트(lib/batch.sh)와 감시(vm1/watchdog.py)가 같이 쓴다. 설정 파일은 EDGE_CONF_DIR(기본 /etc/edge) 아래에 있고
읽는 쪽이 갈린다 — batch.env(호스트 스크립트), pipeline.env(스텝 컨테이너), mail.env(통보 메일, 이 파일만 읽는다).

    edge_cron.py run-id <lane> <HH:MM>   # "<run_id> <슬롯 ISO 시각>" 을 출력한다
    edge_cron.py seconds-until <HH:MM>   # 오늘(KST) 그 시각까지 남은 초(지났으면 0)
    edge_cron.py mail <제목>             # 본문은 표준 입력
"""

from __future__ import annotations

import hashlib
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

KST = timezone(timedelta(hours=9))
# data_pipeline.db.PIPELINE_ID — run_id 해시의 재료다(apps/engine/airflow/dags/edge_batch.py 와 같은 값).
PIPELINE_ID = "alphamale-etf-daily-v1"
# cron 이 슬롯 시각보다 이만큼 넘게 늦으면 그 슬롯으로 돌리지 않는다(호스트 시간대가 KST 가 아닌 경우를 잡는다).
MAX_START_DELAY = timedelta(minutes=10)

# (VM, 레인, 슬롯 KST, cron 요일 칸, 성공 행 기한(분)). crontab 과 감시가 이 표를 같이 본다(테스트가 VM-1·VM-3 crontab 과 대조).
# 기한 = 락 대기 최대 60분 + 레인 실행 시간. etf-daily 는 21스텝을 한 줄로 돌려 더 길게 잡았다.
# ponytail: 기한은 추정값이다. 첫 주 실행 시간을 보고 줄인다.
SLOTS = (
    (1, "dividend-daily", "03:00", "*", 90),
    # 정리는 06:50 에 끊긴다(vm1/snapshot_retention.sh) — 그 뒤 성공 행을 쓸 시간까지 준다.
    (1, "snapshot-retention", "04:30", "*", 150),
    (1, "source-daily", "05:20", "*", 90),
    (1, "premarket", "07:00", "1-5", 90),
    (1, "investor-intraday", "09:35", "1-5", 90),
    (1, "investor-intraday", "10:05", "1-5", 90),
    (1, "investor-intraday", "11:25", "1-5", 90),
    (1, "investor-intraday", "13:25", "1-5", 90),
    (1, "investor-intraday", "14:35", "1-5", 90),
    (1, "etf-daily", "16:30", "1-5", 150),
    (1, "disclosure", "19:30", "1-5", 90),
    (3, "news", "00:10", "*", 90),
    # VM-3 은 거래일 아침에만 켜져 08:10 은 거래일만 돈다(Airflow 는 매일이었다 — README "Airflow 와 달라진 것").
    (3, "news", "08:10", "1-5", 90),
    # VM-2 의 분 세션 종료가 쓰는 행이다(이 디렉터리의 레인이 아니다 — vm2/crontab 이 16:10 에 돌린다).
    # 레인 이름과 시각은 data_pipeline/minute/session_ops.py 의 STOP_SUCCESS_LANE · STOP_SUCCESS_SLOT_KST 와 같아야 한다.
    # 큐와 밀린 분석이 다 빠진 뒤에 쓰므로 기한을 19:00 으로 잡았다(감시가 VM-2 끄기를 포기하는 시각과 같다).
    (2, "minute_session_stop", "16:10", "1-5", 170),
)
# 평일이어도 휴장일에는 돌지 않는 슬롯 — 그날 감시가 VM-2·VM-3 을 켜지 않는다. 감시는 휴장일에 이 슬롯을 기대하지 않는다.
TRADING_DAY_ONLY = {("news", "08:10"), ("minute_session_stop", "16:10")}


def read_conf(name: str = "batch.env") -> dict[str, str]:
    """설정 파일 하나를 docker --env-file 과 같은 규칙으로 읽는다(KEY=VALUE 그대로, 따옴표 해석 없음)."""
    conf = {}
    with open(os.path.join(os.environ.get("EDGE_CONF_DIR", "/etc/edge"), name), encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                conf[key] = value
    return conf


def slot_at(hhmm: str, now: datetime) -> datetime:
    """오늘(KST)의 HH:MM 슬롯. 날짜는 늘 오늘이다 — 지난 날 슬롯으로 수집하면 오늘 값이 옛 run_id 로 저장된다."""
    parsed = datetime.strptime(hhmm, "%H:%M")
    return now.astimezone(KST).replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)


def run_id(lane: str, slot: datetime) -> str:
    """Airflow DAG 가 같은 슬롯에 만들던 run_id.

    premarket 은 슬롯의 UTC 시각(edge_premarket.utc_run_id), 나머지는 `<lane>:<KST 분>` 해시(edge_batch.pipeline_run_id).
    """
    if lane == "premarket":
        return slot.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_key = f"{lane}:{slot.astimezone(KST).strftime('%Y-%m-%dT%H:%M')}"
    return "run_" + hashlib.sha256(f"{PIPELINE_ID}\x01{run_key}".encode()).hexdigest()[:26]


def checked_slot(lane: str, hhmm: str, now: datetime, manual: bool) -> datetime:
    """표에 있는 슬롯인지, 지금 돌려도 되는 시각인지 확인한다. 아니면 ValueError."""
    if not any(lane == entry[1] and hhmm == entry[2] for entry in SLOTS):
        raise ValueError(f"슬롯 표에 없다: {lane} {hhmm}")
    slot = slot_at(hhmm, now)
    if now < slot:
        raise ValueError(f"아직 슬롯 시각 전이다: {slot.isoformat()}")
    if not manual and now - slot > MAX_START_DELAY:
        raise ValueError(f"슬롯 {slot.isoformat()} 보다 {now - slot} 늦게 시작했다 — 호스트 시간대를 확인한다. "
                         "오늘 슬롯을 손으로 다시 돌리는 것이면 EDGE_MANUAL=1 을 준다")
    return slot


def send_mail(subject: str, body: str) -> None:
    """앱 메일 계정(mail.env)으로 통보를 보낸다(app-api application.yaml 과 같은 587 포트·STARTTLS)."""
    conf = read_conf("mail.env")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = conf["EDGE_MAIL_USER"]
    message["To"] = conf["EDGE_ALERT_TO"]
    message.set_content(body)
    with smtplib.SMTP(conf["EDGE_MAIL_HOST"], 587, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(conf["EDGE_MAIL_USER"], conf["EDGE_DEV_APP_API_MAIL_PASSWORD"])
        smtp.send_message(message)


def main(argv: list[str], now: datetime | None = None) -> int:
    if len(argv) == 3 and argv[0] == "run-id":
        try:
            slot = checked_slot(argv[1], argv[2], now or datetime.now(KST), os.environ.get("EDGE_MANUAL") == "1")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(run_id(argv[1], slot), slot.isoformat())
        return 0
    if len(argv) == 2 and argv[0] == "seconds-until":
        now = now or datetime.now(KST)
        print(max(0, int((slot_at(argv[1], now) - now).total_seconds())))
        return 0
    if len(argv) == 2 and argv[0] == "mail":
        send_mail(argv[1], sys.stdin.read())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
