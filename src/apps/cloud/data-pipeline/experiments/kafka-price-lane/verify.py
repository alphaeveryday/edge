"""결과 파일만으로 성공 조건(protocol.json S1~S7)을 다시 판정한다 — `python3 verify.py r0 [r1 …]`.

기대값은 lab·앱 코드를 쓰지 않고 가격 표에서 직접 계산한다(판정 규칙 v2를 손으로 옮긴 것).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / "results" / "protocol-v1.json").read_text())   # 가격 표는 v1·v2 동일
PRICES = [Decimal(p) for p in PROTOCOL["prices"]]
START = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)   # 09:00 KST
SWITCH_AFTER, SNAPSHOT_SEQ = 11, 1


def expected(until):
    """(발화 [(seq, anchor, close)], 회수 [seq], 최종 (앵커, 앵커 seq))."""
    base, anchor, anchor_seq = Decimal(100), Decimal(100), None
    fires, reverts = [], []
    for seq, close in enumerate(PRICES[:until]):
        if abs(close / base - 1) <= Decimal("0.01"):
            if anchor != base:
                reverts.append(seq)
                anchor, anchor_seq = base, seq
            continue
        if abs(close / anchor - 1) >= Decimal("0.03"):
            fires.append((seq, anchor, close))
            anchor, anchor_seq = close, seq
    return fires, reverts, (anchor, anchor_seq)


def seq_of(text):
    moment = datetime.fromisoformat(text.replace(" ", "T"))
    return int((moment - START).total_seconds() // 60)


def triggers(db):
    return [(seq_of(t[0]), Decimal(t[4]), Decimal(t[3])) for t in db["triggers"]]


def events(db):
    return sorted((seq_of(e[2]["window_start"]), e[1]) for e in db["events"])


def expected_events(fires, reverts, skip=()):
    return sorted([(s, "PriceTriggerFired") for s, *_ in fires if s not in skip]
                  + [(s, "ExposureReverted") for s in reverts])


def verify(run):
    folder = HERE / "results" / run
    state = json.loads((folder / "state.json").read_text())
    live_log = [json.loads(x) for x in (folder / "live.jsonl").read_text().splitlines()]
    repair_log = [json.loads(x) for x in (folder / "repair.jsonl").read_text().splitlines()]
    timeline = {e["action"]: e for e in map(json.loads, (folder / "timeline.jsonl").read_text().splitlines())}
    live, repair = state["edge"], state["edge_repair"]
    fires, reverts, final = expected(len(PRICES))
    r_fires, r_reverts, _ = expected(SWITCH_AFTER + 1)
    checks = {}

    # S1 — 운영 경로 전 창 성공, 배선 오류 신호 0, offset 이 창 수와 같다(중복 발행 없음)
    counts = [e["counts"] for e in live_log if e["event"] == "tick"]
    checks["S1"] = (all(j[1] == "SUCCEEDED" for j in live["jobs"]) and len(live["jobs"]) == len(PRICES)
                    and not any(c.get(k) for c in counts for k in ("poison", "misrouted", "ahead", "orphan"))
                    and sum(v or 0 for v in state["offsets"]["price-live"].values()) == len(PRICES))

    # S2 — 재수신은 실행 없이 terminal, attempt 1, 같은 handle 이 한 번만 commit
    f1 = timeline["F1_live_exit_before_offset_commit"]
    commits = [e["handle"] for e in live_log if e["event"] == "committed"]
    checks["S2"] = (f1["exit"] == 73 and timeline["F1_redelivered"]["first_tick"] == {"received": 1, "terminal": 1}
                    and live["jobs"][3][2] == 1 and commits.count(f1["handle"]) == 1)

    # S3 — 재시도 동안 뒤 창 미수신·PENDING, commit 순서가 창 순서와 같다
    received = [seq_of(e["window_start"]) for e in live_log if e["event"] == "received"]
    committed = [seq_of(next(r["window_start"] for r in live_log if r["event"] == "received"
                             and r["handle"] == h)) for h in commits]
    checks["S3"] = (timeline["F2_next_window_while_retrying"]["job"][:2] == ["PENDING", 0]
                    and timeline["F2_next_window_while_retrying"]["received"] is None
                    and received.index(6) > max(i for i, s in enumerate(received) if s == 5)
                    and committed == sorted(committed) == list(range(len(PRICES))))

    # S4 — 브로커 재시작 뒤 창이 수동 개입 없이 처리됐다
    restart_at = timeline["F3_broker_restarted"]["at"]
    checks["S4"] = any(seq_of(e["window_start"]) == 10 and e["at"] > restart_at
                       for e in live_log if e["event"] == "received") and live["jobs"][10][1] == "SUCCEEDED"

    # S5 — 복구: 스냅샷 이전 terminal, 재적용 1회, orphan 대기, 전 구간 기대값 일치
    r_ticks = [e["counts"] for e in repair_log if e["event"] == "tick"]
    r_exit = timeline["F4_repair_exit_before_offset_commit"]
    checks["S5"] = (r_ticks[:2] == [{"received": 1, "terminal": 1}] * 2
                    and r_exit["exit"] == 73 and r_exit["job"][:2] == ["SUCCEEDED", 1]
                    and sum(c.get("terminal", 0) for c in r_ticks) == 3   # seq0·1 + 종료 뒤 seq4
                    # seq2~11 재판정 10건 중 seq4 는 tick 이 끝나기 전에 종료돼 tick 로그가 없다
                    # (그 성공은 r_exit 의 job 상태가 증명한다)
                    and sum(c.get("succeeded", 0) for c in r_ticks) == SWITCH_AFTER - SNAPSHOT_SEQ - 1
                    and any(c.get("orphan") for c in r_ticks)
                    and triggers(repair) == r_fires
                    and events(repair) == expected_events(r_fires, r_reverts)
                    and all(j[1] == "SUCCEEDED" for j in repair["jobs"][:SWITCH_AFTER + 1]))

    # S6 — 전환 뒤 운영 판정 상태 = 전 구간 기대값. 운영 outbox 는 손상 구간의 발화 사건이 빠져 있다(옮기지 않음)
    anchor = live["anchors"][0]
    checks["S6"] = (triggers(live) == fires
                    and (Decimal(anchor[1]), seq_of(anchor[2])) == final
                    and events(live) == expected_events(fires, reverts, skip={2}))

    # S7 — 같은 이미지·명령, env 차이는 DB 이름·group·lab 이름뿐
    a, b = state["containers"]["live"], state["containers"]["repair"]
    diff = {x.split("=", 1)[0] for x in set(a["env"]) ^ set(b["env"])}
    checks["S7"] = a["image"] == b["image"] and a["cmd"] == b["cmd"] and diff <= {
        "DATA_PIPELINE_DB__NAME", "DATA_PIPELINE_MINUTE_PRICE_CONSUMER__QUEUE_URL", "LAB_NAME",
        "LAB_EXIT_BEFORE_COMMIT_WINDOW"}

    # S8 — 경계: 재생 전 복구 DB 의 SUCCEEDED job = 스냅샷 트랜잭션의 SUCCEEDED 집합(v2 부터)
    before = folder / "repair-jobs-before-replay.json"
    if before.exists():
        snap = {j for j, _ in json.loads((folder / "snapshot-position.json").read_text())["succeeded_jobs"]}
        done = {j for j, _, status in json.loads(before.read_text()) if status == "SUCCEEDED"}
        checks["S8"] = bool(snap) and done == snap
    else:
        checks["S8"] = None   # v1 실행 — 경계를 상수 창 번호로 정했고 대조 기록이 없다
    return checks


if __name__ == "__main__":
    failed = False
    for run in sys.argv[1:]:
        checks = verify(run)
        failed |= any(v is False for v in checks.values())
        print(run, " ".join(f"{k}={'n/a(v1)' if v is None else 'ok' if v else 'FAIL'}"
                            for k, v in checks.items()))
    sys.exit(1 if failed else 0)
