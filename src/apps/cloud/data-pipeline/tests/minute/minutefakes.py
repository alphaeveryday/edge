"""minute 원장 테스트 더블 (ALPHA-662) — opsfakes.FakeOpsDB 관례.

session/window 2테이블을 인메모리로 모델링하는 가짜 커넥션. **실제 MinuteLedger** 를 이
위에서 돌려 SQL 경로(멱등·claim 경합·fence CAS)를 그대로 검증한다 — 가짜 repository 를
따로 두면 실제와 갈린다. ON CONFLICT/RETURNING/rowcount 의미를 상태로 흉내 낸다.

천장: FOR UPDATE SKIP LOCKED 의 실제 락 경합은 단일 스레드 fake 로는 못 재현한다 —
여기선 "이미 CLAIMED 면 후보에서 빠진다"는 논리 결과만 검증하고, 물리 경합은 CI/스테이징
ephemeral DB 실측(계획 §16) 소관이다.
"""

from __future__ import annotations

import json
from contextlib import contextmanager


class FakeMinuteDB:
    def __init__(self):
        self.sessions: dict[str, dict] = {}   # session_id -> row
        self.windows: dict[tuple, dict] = {}  # (session_id, window_start) -> row
        self.jobs: dict[tuple, dict] = {}     # (kind, job_id) -> row
        self.outbox: dict[str, dict] = {}     # event_id -> row
        self._seq = 0                          # created_at 순서 흉내
        self.connect_calls = 0                 # 트랜잭션(=connect) 횟수 — 원자성 단언용

    def next_seq(self):
        self._seq += 1
        return self._seq

    def session_by_identity(self, dataset, source_group, session_date):
        for row in self.sessions.values():
            if (row["dataset"], row["source_group"], row["session_date"]) == (
                dataset, source_group, session_date
            ):
                return row
        return None

    @contextmanager
    def connect(self, _db):
        self.connect_calls += 1
        yield _Conn(self)


class _Conn:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def cursor(self):
        yield _Cursor(self.db)


class _Cursor:
    def __init__(self, db: FakeMinuteDB):
        self.db = db
        self._rows: list[tuple] = []
        self.rowcount = 0

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def executemany(self, sql, rows):
        for params in rows:
            self.execute(sql, params)

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self._rows = []
        self.rowcount = 0
        if s.startswith("INSERT INTO minute_ingestion_session"):
            self._insert_session(params)
        elif s.startswith("SELECT worker_fencing_token"):
            self._fence_select(params)
        elif s.startswith("SELECT session_id, universe_version"):
            self._select_session(params)
        elif s.startswith("INSERT INTO minute_ingestion_window"):
            self._insert_window(params)
        elif "SET expected_window_count = ( SELECT COUNT(*)" in s:
            self._refresh_window_count(params)
        elif "worker_fencing_token = worker_fencing_token + 1" in s:
            self._acquire_fence(params)
        elif s.startswith("UPDATE minute_ingestion_session SET lease_expires_at"):
            self._heartbeat(params)
        elif "FOR UPDATE OF c SKIP LOCKED" in s:
            self._claim_window(params, newest_first="ORDER BY c.window_start DESC" in s)
        elif s.startswith("UPDATE minute_ingestion_window w SET data_status = %s, expected_unit_count"):
            self._record_outcome(params)
        elif s.startswith("SELECT 1 FROM minute_ingestion_session"):
            if params[0] in self.db.sessions:  # watermark 선행 잠금 — 없으면 no-row
                self._rows = [(1,)]
        elif s.startswith("UPDATE minute_ingestion_session SET processed_through"):
            self._watermark_advance(params)
        elif "SET phase = 'DRAINING'" in s:
            self._request_drain(params)
        elif "SET phase = 'DRAINED'" in s:
            self._ack_drain(params)
        elif s.startswith("INSERT INTO news_extraction_job"):
            self._insert_job("news", params)
        elif s.startswith("INSERT INTO price_window_job"):
            self._insert_job("price", params)
        elif "SET status = 'CLAIMED'" in s:
            self._claim_job("price" if "price_window_job" in s else "news", params)
        elif s.startswith("SELECT j.generation, w.generation"):
            # 직전 TOCTOU 수정의 핵심 — 잠금 구문이 사라지는 회귀를 fake 가 잡는다
            assert "FOR UPDATE OF w" in s, "stale 검사는 window 행을 잠가야 한다"
            self._job_window_generation(params)
        elif "SET status = 'DEAD', error_code = 'STALE'" in s:
            self._stale_dead(params)
        elif "WHERE job_id = %s AND claimed_by = %s AND status = 'CLAIMED'" in s:
            self._job_transition("price" if "price_window_job" in s else "news", params)
        elif s.startswith("INSERT INTO dataset_commit_outbox"):
            self._insert_outbox(params)
        elif s.startswith("UPDATE dataset_commit_outbox o SET claimed_by"):
            self._claim_outbox(params)
        elif "SET status = 'PUBLISHED'" in s:
            self._mark_published(params)
        elif s.startswith("UPDATE dataset_commit_outbox SET status = %s, attempt_count"):
            self._publish_failure(params)
        else:
            raise AssertionError(f"FakeMinuteDB 가 모르는 SQL: {s[:120]}")

    # ── session ──
    def _insert_session(self, p):
        session_id, dataset, source_group, session_date, version, uhash, count = p
        if self.db.session_by_identity(dataset, source_group, session_date):
            return  # ON CONFLICT DO NOTHING — RETURNING 없음
        self.db.sessions[session_id] = {
            "session_id": session_id, "dataset": dataset, "source_group": source_group,
            "session_date": session_date, "universe_version": version,
            "universe_hash": uhash, "phase": "PLANNED", "expected_window_count": count,
            "worker_fencing_token": 0, "lease_expires_at": None, "heartbeat_at": None,
        }
        self._rows = [(session_id,)]

    def _select_session(self, p):
        row = self.db.session_by_identity(*p)
        assert row is not None
        self._rows = [
            (row["session_id"], row["universe_version"], row["universe_hash"], row["phase"])
        ]

    def _insert_window(self, p):
        session_id, window_start, window_end, scheduled_at = p
        key = (session_id, window_start)
        if key in self.db.windows:
            return
        self.db.windows[key] = {
            "session_id": session_id, "window_start": window_start,
            "window_end": window_end, "scheduled_at": scheduled_at,
            "data_status": "DUE", "generation": 0, "attempt_count": 0,
            "claimed_by": None, "claim_token": None, "lease_expires_at": None,
        }

    def _refresh_window_count(self, p):
        count_session_id, session_id = p
        row = self.db.sessions.get(session_id)
        if row is None:
            return
        row["expected_window_count"] = sum(
            1 for w in self.db.windows.values() if w["session_id"] == count_session_id
        )
        self.rowcount = 1

    def _fence_select(self, p):
        # SELECT ... FOR UPDATE — 단일 스레드 fake 라 락 자체는 no-op, 값만 준다
        row = self.db.sessions.get(p[0])
        if row is not None:
            self._rows = [(row["worker_fencing_token"], row["phase"])]

    # ── fence ──
    def _acquire_fence(self, p):
        lease_until, now, session_id, now2 = p
        row = self.db.sessions.get(session_id)
        if row is None:
            return
        if row["lease_expires_at"] is not None and row["lease_expires_at"] >= now:
            return  # 살아 있는 lease — CAS 실패
        if row["phase"] not in ("PLANNED", "ACTIVE", "DRAINING"):
            return  # DRAINED 이후엔 fence 재획득 금지 — EOD snapshot 경계
        row["worker_fencing_token"] += 1
        row["lease_expires_at"] = lease_until
        row["heartbeat_at"] = now
        if row["phase"] == "PLANNED":
            row["phase"] = "ACTIVE"
        self._rows = [(row["worker_fencing_token"],)]

    def _heartbeat(self, p):
        lease_until, now, session_id, token = p
        row = self.db.sessions.get(session_id)
        if row is None or row["worker_fencing_token"] != token:
            return
        if row["phase"] not in ("ACTIVE", "DRAINING"):
            return  # terminal — Worker 정지 신호
        row["lease_expires_at"] = lease_until
        row["heartbeat_at"] = now
        self.rowcount = 1

    # ── window claim / outcome ──
    def _claim_window(self, p, *, newest_first):
        # fence·phase 검사는 repository 가 _fenced_phase 로 먼저 한다.
        # ACTIVE 변형은 파라미터 8개(DUE+만료claim), DRAINING 변형은 7개(만료claim만)
        if len(p) == 8:
            (claimed_status, worker_id, lease_until,
             session_id, now, due_status, claimed_filter, now2) = p
            def eligible(w):
                return (w["data_status"] == due_status
                        or (w["data_status"] == claimed_filter and w["lease_expires_at"] < now))
        else:
            (claimed_status, worker_id, lease_until,
             session_id, now, claimed_filter, now2) = p
            def eligible(w):
                return w["data_status"] == claimed_filter and w["lease_expires_at"] < now
        candidates = [
            w for w in self.db.windows.values()
            if w["session_id"] == session_id and w["scheduled_at"] <= now and eligible(w)
        ]
        if not candidates:
            return
        pick = max if newest_first else min
        window = pick(candidates, key=lambda w: w["window_start"])
        attempt = window["attempt_count"] + 1
        window.update(
            data_status=claimed_status, claimed_by=worker_id, claim_token=attempt,
            lease_expires_at=lease_until, attempt_count=attempt,
        )
        self._rows = [
            (window["window_start"], window["window_end"],
             window["generation"], window["attempt_count"], window["claim_token"])
        ]

    def _record_outcome(self, p):
        (data_status, expected, succeeded, failed, records, checksum_case, checksum,
         manifest_uri, manifest_checksum, missing_units, stage_timestamps,
         session_id, window_start, worker_id, claim_token) = p
        window = self.db.windows.get((session_id, window_start))
        # fence 검사는 repository 가 _fence_holds 로 먼저 한다 — 여기선 claim 만 검사
        if (
            window is None
            or window["claimed_by"] != worker_id
            or window["claim_token"] != claim_token
        ):
            return
        # CASE WHEN w.checksum IS NOT DISTINCT FROM %s — 같은 checksum 은 generation 불변
        generation = (
            window["generation"]
            if window.get("checksum") == checksum_case
            else window["generation"] + 1
        )
        window.update(
            data_status=data_status, expected_unit_count=expected,
            succeeded_unit_count=succeeded, failed_unit_count=failed,
            record_count=records, generation=generation,
            checksum=checksum, manifest_uri=manifest_uri,
            manifest_checksum=manifest_checksum,
            # 실제 PG 는 ::jsonb 로 파싱해 저장한다 — 문자열 그대로 두면 자료형이 갈린다
            missing_units=None if missing_units is None else json.loads(missing_units),
            stage_timestamps=json.loads(stage_timestamps),
            claimed_by=None, claim_token=None, lease_expires_at=None,
        )
        self.rowcount = 1

    # ── watermark · drain (ALPHA-663) ──
    def _watermark_advance(self, p):
        (sid1, result_statuses, sid2, ok_statuses, sid3, ok_statuses2, sid4) = p
        # 파라미터 배선 자체를 검증한다 — 서브쿼리 하나에 다른 session 을 넘기는 회귀가
        # fake 재구현 뒤에 숨지 않게 (Rule 9)
        assert sid1 == sid2 == sid3 == sid4, "watermark 서브쿼리 session 파라미터 불일치"
        assert list(ok_statuses) == list(ok_statuses2), "contiguous 상태 목록 불일치"
        session = self.db.sessions.get(sid4)
        if session is None:
            return
        rows = [w for w in self.db.windows.values() if w["session_id"] == sid1]
        done = [w for w in rows if w["data_status"] in set(result_statuses)]
        processed = max((w["window_end"] for w in done), default=None)
        ok = set(ok_statuses)
        holes = [w["window_start"] for w in rows if w["data_status"] not in ok]
        first_hole = min(holes, default=None)
        contiguous = max(
            (w["window_end"] for w in rows
             if w["data_status"] in ok
             and (first_hole is None or w["window_start"] < first_hole)),
            default=None,
        )
        session["processed_through"] = processed
        session["contiguous_complete_through"] = contiguous
        self.rowcount = 1
        self._rows = [(processed, contiguous)]

    def _request_drain(self, p):
        now, session_id = p
        row = self.db.sessions.get(session_id)
        if row is None or row["phase"] not in ("PLANNED", "ACTIVE"):
            return
        row["phase"] = "DRAINING"
        row["drain_requested_at"] = now
        self.rowcount = 1

    def _ack_drain(self, p):
        now, session_id, session_id2 = p
        row = self.db.sessions.get(session_id)
        if row is None or row["phase"] != "DRAINING":
            return
        if any(w["session_id"] == session_id2 and w["data_status"] == "CLAIMED"
               for w in self.db.windows.values()):
            return  # 미완료 in-flight 봉인 금지
        row["phase"] = "DRAINED"
        row["drain_ack_at"] = now
        self.rowcount = 1

    # ── job / outbox (ALPHA-664) ──
    def _insert_job(self, kind, p):
        job_id = p[0]
        if (kind, job_id) in self.db.jobs:
            return  # ON CONFLICT DO NOTHING
        row = {"job_id": job_id, "status": "PENDING", "claimed_by": None,
               "lease_expires_at": None, "attempt_count": 0, "next_attempt_at": None,
               "redrive_generation": 0, "result_checksum": None, "error_code": None,
               "completed_at": None, "seq": self.db.next_seq()}
        if kind == "news":
            row.update(source_code=p[1], article_id=p[2], input_fingerprint=p[3],
                       tagger_version=p[4], ontology_version=p[5])
        else:
            row.update(session_id=p[1], window_start=p[2], generation=p[3],
                       trigger_schema_version=p[4])
        self.db.jobs[(kind, job_id)] = row
        self._rows = [(job_id,)]

    def _claim_job(self, kind, p):
        worker_id, lease_until, now, now2 = p
        candidates = [
            r for (k, _), r in self.db.jobs.items() if k == kind
            and (r["status"] == "PENDING"
                 or (r["status"] == "RETRY_WAIT" and r["next_attempt_at"] <= now)
                 or (r["status"] == "CLAIMED" and r["lease_expires_at"] < now))
        ]
        if not candidates:
            return
        row = min(candidates, key=lambda r: r["seq"])
        row.update(status="CLAIMED", claimed_by=worker_id, lease_expires_at=lease_until,
                   attempt_count=row["attempt_count"] + 1)
        self._rows = [(row["job_id"], row["attempt_count"])]

    def _job_window_generation(self, p):
        row = self.db.jobs[("price", p[0])]
        window = self.db.windows[(row["session_id"], row["window_start"])]
        self._rows = [(row["generation"], window["generation"])]

    def _stale_dead(self, p):
        now, job_id, worker_id = p
        row = self.db.jobs.get(("price", job_id))
        if row is None or row["claimed_by"] != worker_id:
            return
        row.update(status="DEAD", error_code="STALE", completed_at=now,
                   claimed_by=None, lease_expires_at=None)
        self.rowcount = 1

    def _job_transition(self, kind, p):
        (to_status, next_attempt_at, result_checksum, error_code, completed,
         job_id, worker_id, attempt) = p
        row = self.db.jobs.get((kind, job_id))
        if (row is None or row["claimed_by"] != worker_id
                or row["status"] != "CLAIMED" or row["attempt_count"] != attempt):
            return
        row.update(status=to_status, next_attempt_at=next_attempt_at,
                   result_checksum=result_checksum, error_code=error_code,
                   completed_at=completed, claimed_by=None, lease_expires_at=None)
        self.rowcount = 1

    def _insert_outbox(self, p):
        event_id, event_type, destination, aggregate_id, generation, payload = p
        if event_id in self.db.outbox:
            return
        self.db.outbox[event_id] = {
            "event_id": event_id, "event_type": event_type, "destination": destination,
            "aggregate_id": aggregate_id, "generation": generation, "payload": json.loads(payload),
            "status": "NEW", "attempt_count": 0, "next_attempt_at": None,
            "claimed_by": None, "claim_expires_at": None, "published_at": None,
            "last_error": None, "seq": self.db.next_seq(),
        }
        self._rows = [(event_id,)]

    def _claim_outbox(self, p):
        relay_id, claim_until, now, now2, limit = p
        eligible = sorted(
            (r for r in self.db.outbox.values()
             if r["status"] == "NEW"
             and (r["next_attempt_at"] is None or r["next_attempt_at"] <= now)
             and (r["claimed_by"] is None or r["claim_expires_at"] < now)),
            key=lambda r: r["seq"],
        )[:limit]
        for row in eligible:
            row.update(claimed_by=relay_id, claim_expires_at=claim_until)
        self._rows = [
            (r["event_id"], r["event_type"], r["destination"], r["payload"],
             r["claim_expires_at"]) for r in eligible
        ]

    def _mark_published(self, p):
        now, event_id, relay_id, claim_token = p
        row = self.db.outbox.get(event_id)
        if (row is None or row["claimed_by"] != relay_id or row["status"] != "NEW"
                or row["claim_expires_at"] != claim_token):
            return
        row.update(status="PUBLISHED", published_at=now, claimed_by=None, claim_expires_at=None)
        self.rowcount = 1

    def _publish_failure(self, p):
        status, next_attempt_at, error, event_id, relay_id, claim_token = p
        row = self.db.outbox.get(event_id)
        if (row is None or row["claimed_by"] != relay_id or row["status"] != "NEW"
                or row["claim_expires_at"] != claim_token):
            return
        row.update(status=status, attempt_count=row["attempt_count"] + 1,
                   next_attempt_at=next_attempt_at, last_error=error,
                   claimed_by=None, claim_expires_at=None)
        self.rowcount = 1
