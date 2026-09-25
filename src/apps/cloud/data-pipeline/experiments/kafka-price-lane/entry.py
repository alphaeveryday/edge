"""lab 전용 진입점 — 실제 CLI(`data_pipeline.run.main`)를 그대로 부르고 관측·장애 주입만 얹는다.

- 관측: 수신·commit·재대기 handle 과 tick 판정 어휘를 /run-data/<LAB_NAME>.jsonl 에 남긴다
- 장애: `LAB_EXIT_BEFORE_COMMIT_WINDOW=<ISO 시각>` 이면 그 창 메시지의 DB 성공 기록 **뒤**,
  offset commit **전**에 exit 73 (kernel 은 succeed_job 커밋 → delete 순서다). offset 이 아니라
  창으로 지목한다 — Relay 재발행이 있으면 offset 이 밀린다

앱 코드는 바꾸지 않는다 — 여기 패치는 전부 이 프로세스 안에서만 산다.
"""
import json
import os
import sys
import time
from datetime import datetime

from data_pipeline.minute import consumer as kernel
from data_pipeline.minute import kafka_transport
from data_pipeline.run import main

NAME = os.environ["LAB_NAME"]
CRASH = os.environ.get("LAB_EXIT_BEFORE_COMMIT_WINDOW") or None
WINDOWS = {}   # handle → window_start


def log(**row):
    with open(f"/run-data/{NAME}.jsonl", "a") as f:
        f.write(json.dumps({"at": time.time(), "pid": os.getpid(), **row}) + "\n")


original_tick = kernel.MinuteConsumer.tick
original_receive = kafka_transport.KafkaQueue.receive
original_delete = kafka_transport.KafkaQueue.delete
original_visibility = kafka_transport.KafkaQueue.change_visibility


def tick(self, now):
    counts = original_tick(self, now)
    if set(counts) - {"idle"}:
        log(event="tick", counts=dict(counts))
    return counts


def receive(self, **kw):
    messages = original_receive(self, **kw)
    for m in messages:
        payload = json.loads(m.body)["payload"] if m.body else {}
        WINDOWS[m.receipt_handle] = payload.get("window_start")
        log(event="received", handle=m.receipt_handle, window_start=payload.get("window_start"))
    return messages


def delete(self, *, queue_url, receipt_handle):
    window = WINDOWS.get(receipt_handle)
    if CRASH and window and datetime.fromisoformat(window) == datetime.fromisoformat(CRASH):
        log(event="exit_before_offset_commit", handle=receipt_handle)
        os._exit(73)
    original_delete(self, queue_url=queue_url, receipt_handle=receipt_handle)
    log(event="committed", handle=receipt_handle)


def change_visibility(self, *, queue_url, receipt_handle, seconds):
    original_visibility(self, queue_url=queue_url, receipt_handle=receipt_handle, seconds=seconds)
    log(event="visibility", handle=receipt_handle, seconds=seconds)


kernel.MinuteConsumer.tick = tick
kafka_transport.KafkaQueue.receive = receive
kafka_transport.KafkaQueue.delete = delete
kafka_transport.KafkaQueue.change_visibility = change_visibility
log(event="start", argv=sys.argv[1:], crash_offset=CRASH)
sys.exit(main(sys.argv[1:]))
