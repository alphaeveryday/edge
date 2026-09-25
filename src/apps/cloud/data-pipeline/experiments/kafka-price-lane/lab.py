# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.1"]
# ///
"""Kafka 가격 레인 장애·복구 실험 오케스트레이터 — `uv run lab.py <run-name>` (README.md).

실제 Worker commit → Relay → Kafka → kernel → 가격 handler 를 CLI 프로세스로 돌리고,
운영 소비자와 복구 소비자는 **같은 이미지·같은 명령**으로 DB·group 만 바꿔 띄운다.
대체하는 것은 수집기(driver.py 의 스크립트 가격)와 저장소(LocalStorage)뿐이다.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PROJECT = "kafka-price-lab"
TOPIC = "price-analysis-realtime"
PRICES = json.loads((HERE / "prices.json").read_text())
START = datetime(2026, 9, 25, 9, 0, tzinfo=timezone(timedelta(hours=9)))
# handler 가 쓰는 판정 파생 상태 — 복구가 되돌리고 전환이 옮기는 대상
DERIVED = ("minute_session_open", "minute_trigger_anchor", "minute_price_trigger")
DERIVED_EVENTS = ("PriceTriggerFired", "ExposureReverted")
SNAPSHOT_SEQ, LIVE_CRASH_SEQ, RETRY_SEQ, DETECT_SEQ = 1, 3, 5, 7
REPAIR_CRASH_SEQ, BROKER_RESTART_AFTER, SWITCH_AFTER = 4, 9, 11

PROTOCOL = {
    # v2: 복구 job 초기화 경계를 스냅샷의 SUCCEEDED 집합으로(v1 은 상수 창 번호 — r0·r1)
    "version": 2,
    "question": "실시간 판정과 과거 입력 복구가 같은 소비 코드·offset 관리로 동작하는가(실제 EDGE 경로)",
    "prices": PRICES, "prev_close": 100, "abs_threshold": "0.03", "revert_threshold": "0.01",
    "real": "Worker commit·outbox·Relay·Kafka·MinuteConsumer kernel·PriceTriggerHandler·PostgreSQL 16",
    "substituted": "수집기(스크립트 가격), 저장소(LocalStorage 파일)",
    "faults": {
        "F1": f"운영 소비자: seq{LIVE_CRASH_SEQ} DB 성공 뒤 offset commit 전 exit 73",
        "F2": f"seq{RETRY_SEQ} artifact 를 숨겨 재시도 — 그동안 seq{RETRY_SEQ + 1} 발행",
        "F3": f"seq{BROKER_RESTART_AFTER} 처리 뒤 브로커 재시작",
        "F4": f"seq{SNAPSHOT_SEQ} 스냅샷 뒤 앵커 100→104 손상, seq{DETECT_SEQ} 뒤 발견. "
              f"복구 소비자 offset 0 부터 재생, seq{REPAIR_CRASH_SEQ} DB 성공 뒤 offset commit 전 exit 73",
    },
    "success": {
        "S1": "운영 job 전부 SUCCEEDED, poison·misrouted·ahead 0",
        "S2": "F1 재수신이 실행 없이 terminal 로 commit, job attempt 1",
        "S3": f"F2 동안 seq{RETRY_SEQ + 1} 는 수신되지 않고 PENDING, 복구 뒤 순서대로 처리",
        "S4": "F3 뒤 수동 개입 없이 이어서 처리",
        "S5": "복구 DB: 스냅샷 이전 job 은 terminal 로 건너뜀, 손상 구간 재판정, 입력 원장 동기화 전 orphan 대기,"
              " 동기화 뒤 따라잡음, 트리거·앵커·파생 outbox 가 기대값과 전부 일치",
        "S6": "전환 뒤 운영 DB 의 트리거·앵커가 전 구간 기대값과 일치",
        "S7": "운영·복구 컨테이너의 이미지·명령 동일, env 차이는 DB 이름·group·장애 변수뿐",
        "S8": "복구 DB 재생 전 SUCCEEDED job 집합 = 스냅샷과 같은 트랜잭션에서 읽은 SUCCEEDED 집합",
    },
    "not_claimed": "처리량·지연 순위, 복제·HA, 실제 AWS·S3, 운영 전환, 외부 부수효과 exactly-once, 다중 세션·다중 생산자",
}


def window(seq):
    return START + timedelta(minutes=seq)


class Lab:
    def __init__(self, run):
        self.dir = RESULTS / run
        self.dir.mkdir(parents=True)   # 이미 있으면 실패 — 기존 증거를 덮지 않는다
        self.env = {**os.environ, "LAB_RUN_DIR": str(self.dir)}
        self.snapshot = {}

    # ── 도구 ──────────────────────────────────────────────
    def log(self, action, **row):
        with (self.dir / "timeline.jsonl").open("a") as f:
            f.write(json.dumps({"at": time.time(), "action": action, **row}, default=str) + "\n")
        print(action, json.dumps(row, default=str)[:200], flush=True)

    def compose(self, *args, env=None, capture=False, check=True):
        return subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=HERE,
                              env={**self.env, **(env or {})}, check=check,
                              capture_output=capture, text=True)

    def kafka(self, *args):
        return self.compose("exec", "-T", "kafka", *args, capture=True).stdout

    def db(self, name="edge", autocommit=True):
        return psycopg.connect(f"postgresql://edge:edge@127.0.0.1:55442/{name}", autocommit=autocommit)

    def wait(self, what, predicate, timeout=120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if value := predicate():
                return value
            time.sleep(0.2)
        raise TimeoutError(what)

    def job(self, seq, name="edge"):
        with self.db(name) as c:
            return c.execute("SELECT status, attempt_count, error_code FROM price_window_job"
                             " WHERE window_start=%s", (window(seq),)).fetchone()

    def succeeded(self, seq, name="edge", timeout=120):
        return self.wait(f"{name} seq{seq} SUCCEEDED",
                         lambda: (j := self.job(seq, name)) and j[0] == "SUCCEEDED" and j, timeout)

    def offsets(self, group):
        rows = {}
        for line in self.kafka("/opt/kafka/bin/kafka-consumer-groups.sh", "--bootstrap-server",
                               "localhost:9092", "--describe", "--group", group).splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1] == TOPIC:
                rows[int(parts[2])] = None if parts[3] == "-" else int(parts[3])
        return rows

    def events(self, name):
        path = self.dir / f"{name}.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def handle_of(self, name, seq):
        for e in self.events(name):
            if e["event"] == "received" and datetime.fromisoformat(e["window_start"]) == window(seq):
                return e["handle"]

    def exit_code(self, service):
        return int(subprocess.run(["docker", "wait", f"{PROJECT}-{service}-1"], capture_output=True,
                                  text=True, check=True, timeout=120).stdout.strip())

    def commit(self, seq):
        out = self.compose("run", "--rm", "--no-deps", "-T", "driver", "python", "/lab/driver.py", "commit",
                           str(seq), capture=True).stdout
        self.log("committed_window", seq=seq, close=PRICES[seq], worker=out.strip().splitlines()[-1])

    # ── 장애·복구 단계 ────────────────────────────────────
    def live_crash(self, seq):
        code = self.exit_code("live")
        assert code == 73, code
        job = self.job(seq)
        handle = self.handle_of("live", seq)
        partition, offset = int(handle.split(":")[1]), int(handle.split(":")[2])
        committed = self.offsets("price-live").get(partition)
        self.log("F1_live_exit_before_offset_commit", seq=seq, exit=code, job=job, handle=handle,
                 committed_offset=committed)
        assert job[:2] == ("SUCCEEDED", 1) and committed == offset, (job, committed, offset)
        self.compose("up", "-d", "--no-deps", "live")   # 장애 변수 없이 재생성
        self.wait("F1 재수신 commit", lambda: any(
            e["event"] == "committed" and e["handle"] == handle and self._after_restart("live", e)
            for e in self.events("live")))
        ticks = [e for e in self.events("live") if e["event"] == "tick" and self._after_restart("live", e)]
        self.log("F1_redelivered", handle=handle, first_tick=ticks[0]["counts"], job=self.job(seq))
        assert ticks[0]["counts"].get("terminal") == 1 and self.job(seq)[1] == 1

    def _after_restart(self, name, event):
        starts = [e["at"] for e in self.events(name) if e["event"] == "start"]
        return len(starts) > 1 and event["at"] > starts[-1]

    def retry_hold(self, seq):
        self.compose("stop", "relay")
        self.commit(seq)
        with self.db() as c:
            uri = c.execute("SELECT artifact_uri FROM minute_window_artifact_commit WHERE window_start=%s",
                            (window(seq),)).fetchone()[0]
        artifact, hidden = self.dir / "lake" / uri, self.dir / "hidden-artifact"
        artifact.rename(hidden)
        self.log("F2_artifact_hidden", seq=seq, uri=uri)
        self.compose("start", "relay")
        failing = self.wait("F2 재시도 2회", lambda: (j := self.job(seq)) and j[1] >= 2 and j)
        self.log("F2_retrying", seq=seq, job=failing)
        self.commit(seq + 1)
        with self.db() as c:
            self.wait("다음 창 발행", lambda: c.execute(
                "SELECT status FROM dataset_commit_outbox WHERE event_type='PriceWindowCommitted'"
                " AND (payload->>'window_start')::timestamptz=%s", (window(seq + 1),)).fetchone()
                == ("PUBLISHED",))
        time.sleep(3)
        waiting = self.job(seq + 1)
        received_next = self.handle_of("live", seq + 1)
        self.log("F2_next_window_while_retrying", seq=seq + 1, job=waiting, received=received_next,
                 failing=self.job(seq))
        assert waiting[:2] == ("PENDING", 0) and received_next is None
        hidden.rename(artifact)
        restored = time.time()
        self.log("F2_artifact_restored", seq=seq)
        done = self.succeeded(seq)
        self.succeeded(seq + 1)
        self.log("F2_recovered", seq=seq, job=done, seconds_after_restore=round(time.time() - restored, 2),
                 next_job=self.job(seq + 1))

    def take_snapshot(self):
        """판정 파생 상태와 **그 시점의 SUCCEEDED job 집합**을 한 스냅샷으로 뜬다.

        복구의 job 초기화 경계는 이 집합이다(상수 창 번호가 아니다). handler 는 파생 상태를
        커밋한 **뒤** 별도 트랜잭션에서 job 을 SUCCEEDED 로 기록하므로, 한 스냅샷 안에서
        "SUCCEEDED 인데 파생 상태 없음"은 나올 수 없다. 반대(파생 있음·job 미완)는 재처리가
        멱등이라 안전하다. 경계를 따로 정하면 늦게 잡힌 쪽 창이 terminal 로 건너뛰어져
        결과가 조용히 틀린다.
        """
        with self.db(autocommit=False) as c, c.cursor() as cur:
            c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            for table in DERIVED:
                self.snapshot[table] = self._copy_out(cur, f"SELECT * FROM {table}")
            self.snapshot["dataset_commit_outbox"] = self._copy_out(
                cur, "SELECT * FROM dataset_commit_outbox WHERE event_type = ANY(ARRAY"
                     f"{list(DERIVED_EVENTS)})")
            anchor = cur.execute("SELECT anchor_price FROM minute_trigger_anchor").fetchall()
            self.snapshot_jobs = cur.execute(
                "SELECT job_id, window_start FROM price_window_job WHERE status='SUCCEEDED'"
                " ORDER BY window_start").fetchall()
            c.rollback()   # 읽기 전용 스냅샷
        position = {"last_seq": SNAPSHOT_SEQ, "window": window(SNAPSHOT_SEQ),
                    "live_committed_offsets": self.offsets("price-live"), "anchor": anchor,
                    "succeeded_jobs": self.snapshot_jobs}
        (self.dir / "snapshot-position.json").write_text(json.dumps(position, default=str, indent=2))
        for table, data in self.snapshot.items():
            (self.dir / f"snapshot-{table}.copy").write_bytes(data)
        with self.db() as c:
            changed = c.execute("UPDATE minute_trigger_anchor SET anchor_price=104"
                                " WHERE entity_id='500000' AND anchor_price=100").rowcount
        self.log("F4_snapshot_then_corrupt_anchor", position=position, corrupted_rows=changed)
        assert changed == 1

    @staticmethod
    def _clear_derived(cur):
        # TRUNCATE 는 설명 레인(etf_contribution_observation)의 FK 에 막힌다. CASCADE 대신
        # DELETE — 참조 행이 있으면 실패해야 한다(설명까지 지우는 건 이 절차의 권한 밖)
        for table in reversed(DERIVED):
            cur.execute(f"DELETE FROM {table}")

    @staticmethod
    def _copy_out(cur, query):
        data = bytearray()
        with cur.copy(f"COPY ({query}) TO STDOUT") as copy:
            for chunk in copy:
                data += chunk
        return bytes(data)

    def prepare_repair(self):
        self.compose("exec", "-T", "postgres", "psql", "-q", "-U", "edge", "-c", "CREATE DATABASE edge_repair")
        self.compose("exec", "-T", "postgres", "sh", "-c", "pg_dump -U edge edge | psql -q -U edge edge_repair",
                     capture=True)
        with self.db("edge_repair", autocommit=False) as c, c.cursor() as cur:
            self._clear_derived(cur)
            cur.execute("DELETE FROM dataset_commit_outbox WHERE event_type = ANY(%s)", (list(DERIVED_EVENTS),))
            for table, data in self.snapshot.items():
                with cur.copy(f"COPY {table} FROM STDIN") as copy:
                    copy.write(data)
            done = [job_id for job_id, _ in self.snapshot_jobs]
            reset = cur.execute(
                "UPDATE price_window_job SET status='PENDING', attempt_count=0, claimed_by=NULL,"
                " lease_expires_at=NULL, next_attempt_at=NULL, result_checksum=NULL, error_code=NULL,"
                " completed_at=NULL, updated_at=now() WHERE NOT (job_id = ANY(%s))", (done,)).rowcount
            # 경계 대조 — 복구 DB 에서 SUCCEEDED 로 남는 job 은 스냅샷이 결과를 담은 job 뿐이어야
            # 한다. 하나라도 더 있으면 그 창은 재판정 없이 건너뛰어진다(조용한 누락)
            before = cur.execute("SELECT job_id, window_start, status FROM price_window_job"
                                 " ORDER BY window_start").fetchall()
            # 양방향이다 — 스냅샷 안인데 SUCCEEDED 가 아닌 job 은 결과가 복원된 창을 다시
            # 판정하거나(행이 없으면) 뒤늦게 PENDING 으로 끼어든다
            now_done = {j for j, _, st in before if st == "SUCCEEDED"}
            if now_done != set(done):
                raise AssertionError(
                    f"경계 불일치 — 스냅샷 밖 SUCCEEDED: {sorted(now_done - set(done))}, "
                    f"스냅샷 안 비SUCCEEDED: {sorted(set(done) - now_done)}")
        (self.dir / "repair-jobs-before-replay.json").write_text(json.dumps(before, default=str, indent=2))
        synced = self.sync_inputs()
        out = self.kafka("/opt/kafka/bin/kafka-consumer-groups.sh", "--bootstrap-server", "localhost:9092",
                         "--group", "price-repair", "--topic", TOPIC, "--reset-offsets", "--to-earliest",
                         "--execute")
        self.log("F4_repair_db_prepared", reset_jobs=reset, first_sync=synced, group_reset=out.strip())
        assert reset == DETECT_SEQ - SNAPSHOT_SEQ

    def sync_inputs(self):
        """입력 원장(window·artifact 이력·job identity)만 운영 DB → 복구 DB. 판정 상태는 안 건드린다.

        메시지가 job 참조라서, 복구 DB 생성 뒤 커밋된 창은 이 동기화 없이는 복구 소비자가
        job 행을 못 찾는다(orphan). 멱등이다 — 복구 시작과 전환 직전에 같은 함수를 부른다.
        """
        counts = {}
        with self.db() as live, live.cursor() as src, \
                self.db("edge_repair", autocommit=False) as repair, repair.cursor() as dst:
            for table in ("minute_ingestion_window", "minute_window_artifact_commit", "price_window_job"):
                data = self._copy_out(src, f"SELECT * FROM {table}")
                dst.execute(f"CREATE TEMP TABLE s_{table} (LIKE {table}) ON COMMIT DROP")
                with dst.copy(f"COPY s_{table} FROM STDIN") as copy:
                    copy.write(data)
                columns = [r[0] for r in dst.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name=%s"
                    " AND table_schema='public' AND is_generated='NEVER' ORDER BY ordinal_position",
                    (table,)).fetchall()]
                if table == "minute_ingestion_window":
                    other = [col for col in columns if col not in ("session_id", "window_start")]
                    counts[table] = dst.execute(
                        f"UPDATE {table} r SET ({', '.join(other)}) = "
                        f"(SELECT {', '.join('s.' + col for col in other)}) FROM s_{table} s"
                        " WHERE r.session_id=s.session_id AND r.window_start=s.window_start"
                        " AND r.generation < s.generation").rowcount
                else:
                    reset = {"status": "'PENDING'", "attempt_count": "0"} if table == "price_window_job" else {}
                    nulls = ("claimed_by", "lease_expires_at", "next_attempt_at", "result_checksum",
                             "error_code", "completed_at") if table == "price_window_job" else ()
                    select = [reset.get(col, "NULL" if col in nulls else col) for col in columns]
                    counts[table] = dst.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(select)}"
                        f" FROM s_{table} ON CONFLICT DO NOTHING").rowcount
            repair.commit()
        return counts

    def start_repair_with_crash(self):
        self.compose("up", "-d", "--no-deps", "repair", env={"REPAIR_CRASH_WINDOW": window(REPAIR_CRASH_SEQ).isoformat()})
        code = self.exit_code("repair")
        handle = self.handle_of("repair", REPAIR_CRASH_SEQ)
        job = self.job(REPAIR_CRASH_SEQ, "edge_repair")
        committed = self.offsets("price-repair").get(int(handle.split(":")[1]))
        self.log("F4_repair_exit_before_offset_commit", exit=code, handle=handle, job=job,
                 committed_offset=committed)
        assert code == 73 and job[:2] == ("SUCCEEDED", 1) and committed == int(handle.split(":")[2])
        self.compose("up", "-d", "--no-deps", "repair")
        self.succeeded(DETECT_SEQ, "edge_repair")
        ticks = [e["counts"] for e in self.events("repair") if e["event"] == "tick"]
        self.log("F4_repair_caught_up_to_detection", ticks=ticks)

    def restart_broker(self):
        t0 = time.time()
        self.compose("restart", "kafka")
        self.compose("up", "-d", "--wait", "kafka")
        self.log("F3_broker_restarted", seconds=round(time.time() - t0, 2))

    def switch(self):
        self.compose("stop", "live")
        live_exit = self.exit_code("live")
        orphan_ticks = [e["counts"] for e in self.events("repair") if e["event"] == "tick"
                        and e["counts"].get("orphan")]
        waiting = self.job(SWITCH_AFTER, "edge_repair")
        synced = self.sync_inputs()
        self.log("switch_sync_inputs", live_exit=live_exit, repair_orphan_ticks_before=len(orphan_ticks),
                 repair_job_before_sync=waiting, synced=synced)
        assert orphan_ticks and waiting is None
        for seq in range(SWITCH_AFTER + 1):
            self.succeeded(seq, "edge_repair", timeout=90)
        self.compose("stop", "repair")
        with self.db("edge_repair") as repair, repair.cursor() as src, \
                self.db(autocommit=False) as live, live.cursor() as dst:
            self._clear_derived(dst)
            for table in DERIVED:
                with dst.copy(f"COPY {table} FROM STDIN") as copy:
                    copy.write(self._copy_out(src, f"SELECT * FROM {table}"))
            live.commit()
        self.log("switch_derived_state_copied_to_live", tables=DERIVED,
                 note="파생 outbox 는 옮기지 않았다 — 늦은 발화의 발행 여부는 업무 결정")
        self.compose("up", "-d", "--no-deps", "live")

    def collect(self):
        state = {}
        for name in ("edge", "edge_repair"):
            with self.db(name) as c:
                state[name] = {
                    "triggers": c.execute("SELECT window_start, entity_id, open_price, close_price, anchor_price,"
                                          " change_rate, generation, trigger_id FROM minute_price_trigger"
                                          " ORDER BY window_start").fetchall(),
                    "anchors": c.execute("SELECT entity_id, anchor_price, anchor_window FROM minute_trigger_anchor"
                                         ).fetchall(),
                    "events": c.execute("SELECT event_id, event_type, payload FROM dataset_commit_outbox"
                                        " WHERE event_type = ANY(%s) ORDER BY event_id",
                                        (list(DERIVED_EVENTS),)).fetchall(),
                    "jobs": c.execute("SELECT window_start, status, attempt_count, error_code FROM price_window_job"
                                      " ORDER BY window_start").fetchall(),
                }
        state["offsets"] = {g: self.offsets(g) for g in ("price-live", "price-repair")}
        state["containers"] = {}
        for service in ("live", "repair"):
            (info,) = json.loads(subprocess.run(["docker", "inspect", f"{PROJECT}-{service}-1"],
                                                capture_output=True, text=True, check=True).stdout)
            state["containers"][service] = {"image": info["Image"], "cmd": info["Config"]["Cmd"],
                                            "env": sorted(info["Config"]["Env"])}
        (self.dir / "state.json").write_text(json.dumps(state, default=str, indent=2))
        (self.dir / "containers.log").write_text(self.compose("logs", "--no-color", capture=True).stdout)

    # ── 시나리오 ──────────────────────────────────────────
    def run(self):
        self.compose("down", "-v", "--remove-orphans", capture=True)
        self.compose("up", "-d", "--wait", "postgres", "kafka")
        self.compose("run", "--rm", "-T", "flyway", capture=True)
        for topic, partitions in ((TOPIC, 3), ("price-explanation-realtime", 1),
                                  ("news-extraction-realtime", 1), ("news-extraction-backfill", 1)):
            self.kafka("/opt/kafka/bin/kafka-topics.sh", "--bootstrap-server", "localhost:9092",
                       "--create", "--topic", topic, "--partitions", str(partitions))
        session = self.compose("run", "--rm", "--no-deps", "-T", "driver", "python", "/lab/driver.py", "plan",
                               capture=True).stdout.strip().splitlines()[-1]
        self.log("planned", session=session, windows=len(PRICES))
        self.compose("up", "-d", "--no-deps", "relay")
        self.compose("up", "-d", "--no-deps", "live", env={"LIVE_CRASH_WINDOW": window(LIVE_CRASH_SEQ).isoformat()})
        seq = 0
        while seq < len(PRICES):
            if seq == RETRY_SEQ:
                self.retry_hold(seq)
                seq += 2
                continue
            self.commit(seq)
            if seq == LIVE_CRASH_SEQ:
                self.live_crash(seq)
            self.log("live_succeeded", seq=seq, job=self.succeeded(seq))
            if seq == SNAPSHOT_SEQ:
                self.take_snapshot()
            if seq == DETECT_SEQ:
                self.prepare_repair()
                self.start_repair_with_crash()
            if seq == BROKER_RESTART_AFTER:
                self.restart_broker()
            if seq == SWITCH_AFTER:
                self.switch()
            seq += 1
        self.collect()
        self.log("finished")


def main():
    RESULTS.mkdir(exist_ok=True)
    protocol = RESULTS / f"protocol-v{PROTOCOL['version']}.json"
    if protocol.exists():
        assert json.loads(protocol.read_text()) == json.loads(json.dumps(PROTOCOL, default=str)), \
            "protocol 이 바뀌었다 — 기존 결과와 섞지 않는다"
    else:
        protocol.write_text(json.dumps(PROTOCOL, default=str, indent=2, ensure_ascii=False))
    lab = Lab(sys.argv[1])
    try:
        lab.run()
    finally:
        if not (lab.dir / "state.json").exists():
            (lab.dir / "containers.log").write_text(lab.compose("logs", "--no-color", capture=True,
                                                                check=False).stdout)
        lab.compose("down", "-v", "--remove-orphans", capture=True, check=False)


if __name__ == "__main__":
    main()
