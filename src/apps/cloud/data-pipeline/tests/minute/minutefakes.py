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

    def session_by_identity(self, dataset, source_group, session_date):
        for row in self.sessions.values():
            if (row["dataset"], row["source_group"], row["session_date"]) == (
                dataset, source_group, session_date
            ):
                return row
        return None

    @contextmanager
    def connect(self, _db):
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
            self._claim_window(params)
        elif s.startswith("UPDATE minute_ingestion_window w SET data_status = %s, expected_unit_count"):
            self._record_outcome(params)
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
            self._rows = [(row["worker_fencing_token"],)]

    # ── fence ──
    def _acquire_fence(self, p):
        lease_until, now, session_id, now2 = p
        row = self.db.sessions.get(session_id)
        if row is None:
            return
        if row["lease_expires_at"] is not None and row["lease_expires_at"] >= now:
            return  # 살아 있는 lease — CAS 실패
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
        row["lease_expires_at"] = lease_until
        row["heartbeat_at"] = now
        self.rowcount = 1

    # ── window claim / outcome ──
    def _claim_window(self, p):
        (claimed_status, worker_id, lease_until,
         session_id, now, due_status, claimed_filter, now2) = p
        # fence 검사는 repository 가 _fence_holds(SELECT FOR UPDATE)로 먼저 한다
        candidates = [
            w for w in self.db.windows.values()
            if w["session_id"] == session_id and w["scheduled_at"] <= now
            and (w["data_status"] == due_status
                 or (w["data_status"] == claimed_filter and w["lease_expires_at"] < now))
        ]
        if not candidates:
            return
        window = min(candidates, key=lambda w: w["window_start"])
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
