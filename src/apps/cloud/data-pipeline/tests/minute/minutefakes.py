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


def _expired(lease_expires_at, now) -> bool:
    """NULL lease 는 **만료로 본다** — 실제 SQL(`IS NULL OR < now`)과 같은 의미다.

    fake 가 `None < now` 로 계산하면 TypeError 로 죽어, 이 고착 복구 가드를 아예
    테스트할 수 없다(그러면 SQL 에서 빠져도 아무도 모른다).
    """
    return lease_expires_at is None or lease_expires_at < now


def _decimal_of(value):
    from decimal import Decimal

    return Decimal(str(value))


def _numeric6(value):
    """NUMERIC(24,6) 저장을 흉내 낸다 — 스케일 절단이 fake 에 없으면, 8자리 전일 종가를
    앵커에 넣고 다시 같다고 비교하는 회귀(회수 사건 반복 발행)를 못 잡는다."""
    from decimal import Decimal

    return _decimal_of(value).quantize(Decimal("0.000001"))


def _job_kind(sql: str) -> str:
    """SQL 이 어느 job 테이블을 보는지 — 두 테이블이 lifecycle 을 공유해서 필요하다."""
    return "price" if "price_window_job" in sql else "news"


class FakeMinuteDB:
    def __init__(self):
        self.sessions: dict[str, dict] = {}   # session_id -> row
        self.windows: dict[tuple, dict] = {}  # (session_id, window_start) -> row
        self.jobs: dict[tuple, dict] = {}     # (kind, job_id) -> row
        self.outbox: dict[str, dict] = {}     # event_id -> row
        self.source_items: dict[tuple, dict] = {}  # (source_code, source_item_id) -> row
        self.anchors: dict[tuple, dict] = {}   # (session_id, source_code) -> row
        self.documents: dict[tuple, dict] = {}      # (source_code, article_id) -> row
        self.news_documents: dict[str, dict] = {}   # document_id -> row
        self.session_opens: dict[tuple, dict] = {}  # (session_id, entity_id) -> row
        self.triggers: dict[str, dict] = {}         # trigger_id -> row
        self.trigger_anchors: dict[tuple, dict] = {}  # (session_id, entity_id) -> row
        # price_daily×instrument 조인의 결과만 모델링한다: {ticker: 전일 종가}.
        # 비우면 전일 종가 결손 = 세션 시가 폴백 경로다(ALPHA-745).
        self.prev_closes: dict[str, object] = {}
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
        elif s.startswith("SELECT universe_version, universe_hash FROM minute_ingestion_session"):
            row = self.db.sessions.get(params[0])
            if row is not None:
                self._rows = [(row["universe_version"], row["universe_hash"])]
        elif s.startswith("INSERT INTO minute_ingestion_window"):
            self._insert_window(params)
        elif "SET expected_window_count = ( SELECT COUNT(*)" in s:
            self._refresh_window_count(params)
        elif "SET phase = 'QC_RUNNING'" in s:
            # EOD QC 진입(ALPHA-693). 재진입 phase 집합이 SQL 에서 빠지면 중간에 죽은 QC 가
            # 그 세션을 영영 못 끝내게 만든다 — fake 가 합성하기 전에 문면을 못 박는다.
            assert "phase IN ('DRAINED', 'QC_RUNNING', 'FAILED')" in s, \
                "begin_qc SQL 에 재진입 phase 집합이 없다"
            # 토큰을 안 올리면 phase-only CAS 라 ABA 가 통과한다(낡은 실행이 새 판정을 덮는다)
            assert "worker_fencing_token = worker_fencing_token + 1" in s, \
                "begin_qc SQL 이 QC 소유권 토큰을 올리지 않는다"
            self._begin_qc(params)
        elif "worker_fencing_token = worker_fencing_token + 1" in s:
            self._acquire_fence(params)
        elif s.startswith("UPDATE minute_ingestion_session SET lease_expires_at = NULL"):
            self._release_fence(params)
        elif s.startswith("UPDATE minute_ingestion_session SET lease_expires_at"):
            self._heartbeat(params)
        elif "FOR UPDATE OF c SKIP LOCKED" in s:
            self._claim_window(params, newest_first="ORDER BY c.window_start DESC" in s)
        elif s.startswith("UPDATE minute_ingestion_window w SET data_status = %s, expected_unit_count"):
            self._record_outcome(params)
        elif s.startswith("SELECT claimed_by, claim_token FROM minute_ingestion_window"):
            window = self.db.windows.get((params[0], params[1]))
            if window is not None:
                self._rows = [(window["claimed_by"], window["claim_token"])]
        elif s.startswith("SELECT window_start, generation FROM minute_ingestion_window"):
            self._rows = [
                (w["window_start"], w["generation"]) for w in self.db.windows.values()
                if w["session_id"] == params[0] and w.get("checksum") is not None
            ]
        elif "SET data_status = 'DUE', claimed_by = NULL" in s:
            self._release_claim(params)
        elif s.startswith("SELECT phase FROM minute_ingestion_session"):
            row = self.db.sessions.get(params[0])
            if row is not None:
                self._rows = [(row["phase"],)]
        elif s.startswith("SELECT 1 FROM minute_ingestion_session"):
            if params[0] in self.db.sessions:  # watermark 선행 잠금 — 없으면 no-row
                self._rows = [(1,)]
        elif s.startswith("UPDATE minute_ingestion_session SET processed_through"):
            self._watermark_advance(params)
        elif s.startswith("SELECT phase, worker_fencing_token"):
            # QC 소유권 검사는 **행을 잠그고** 해야 한다 — 비잠금이면 READ COMMITTED
            # 스냅샷이 옛 토큰을 본 채 되돌릴 수 없는 MISSING 을 찍는다(재발 패턴 #1)
            assert "FOR UPDATE" in s, "QC 소유권 검사가 세션 행을 잠그지 않는다"
            row = self.db.sessions.get(params[0])
            if row is not None:
                self._rows = [(row["phase"], row.get("worker_fencing_token", 0))]
        elif s.startswith("UPDATE minute_ingestion_window") and "'MISSING'" in s:
            # 시각 가드가 빠지면 조기 drain 만으로 **아직 오지도 않은 분**까지 MISSING 으로
            # 확정하고 하루가 봉인된다(장중 오작동의 유일한 잠금장치다)
            assert "scheduled_at <= %s" in s, "confirm_missing_windows SQL 에 시각 가드가 없다"
            self._confirm_missing(params)
        elif s.startswith("SELECT window_start, window_end, data_status"):
            assert "ORDER BY window_start" in s, "QC 입력은 결정적으로 정렬돼야 한다"
            self._rows = sorted(
                ((w["window_start"], w["window_end"], w["data_status"], w["generation"],
                  w.get("checksum"))
                 for w in self.db.windows.values() if w["session_id"] == params[0]),
                key=lambda row: row[0],
            )
        elif s.startswith("SELECT dataset, source_group, session_date"):
            row = self.db.sessions.get(params[0])
            if row is not None:
                self._rows = [(
                    row["dataset"], row["source_group"], row["session_date"],
                    row["expected_window_count"], row["universe_version"], row["phase"],
                    row.get("final_checksum"), row.get("final_generation"),
                )]
        elif "SET phase = 'FINALIZED'" in s:
            # CAS 가 SQL 에서 빠지면 다른 실행이 이미 바꾼 phase 를 덮어쓴다 — fake 가
            # 파이썬으로 재검사해 주면 그 회귀가 계속 초록으로 보인다(Rule 9)
            assert "AND phase = 'QC_RUNNING'" in s, "finalize_session SQL 에 phase CAS 가 없다"
            assert "worker_fencing_token = %s" in s, "finalize_session SQL 에 토큰 대조가 없다"
            self._finish_qc(params[2], "FINALIZED", fence_token=params[3],
                            final_checksum=params[0], final_generation=params[1])
        elif "SET phase = 'FAILED'" in s:
            assert "AND phase = 'QC_RUNNING'" in s, "fail_session_qc SQL 에 phase CAS 가 없다"
            assert "worker_fencing_token = %s" in s, "fail_session_qc SQL 에 토큰 대조가 없다"
            self._finish_qc(params[0], "FAILED", fence_token=params[1])
        elif "SET phase = 'DRAINING'" in s:
            self._request_drain(params)
        elif "SET phase = 'DRAINED'" in s:
            self._ack_drain(params)
        elif s.startswith("SELECT d.available_at, n.lead_text"):
            # 신선도 판정은 두 테이블이 **공유**한다 — 따로 걸면 낡은 관측이 한쪽만
            # 되돌려 제목/리드가 섞인 행이 남는다. 그래서 잠그고 한 번만 본다.
            assert "FOR UPDATE" in s, "canonical 신선도 검사가 문서 행을 잠그지 않는다"
            row = self.db.documents.get((params[0], params[1]))
            if row is not None:
                child = self.db.news_documents.get(row["document_id"])
                self._rows = [(
                    row["available_at"],
                    None if child is None else child["lead_text"],
                )]
        elif s.startswith("UPDATE document SET available_at"):
            # available_at 을 쓰는 세 경로(부모·자식·이 보정)의 규칙이 같아야 한다
            assert "GREATEST(available_at, %s)" in s, "리드 보정 UPDATE 의 시각이 단조가 아니다"
            row = self.db.documents.get((params[1], params[2]))
            if row is not None:
                row["available_at"] = max(row["available_at"], params[0])
                self.rowcount = 1
        elif s.startswith("INSERT INTO document ("):
            # 1분 뉴스 canonical writer(ALPHA-691). 정정 반영이 이 문장의 존재 이유라
            # DO UPDATE 와 변경분 판정이 SQL 에 있어야 한다 — fake 가 합성하면 배치와 같은
            # DO NOTHING 회귀가 초록으로 통과한다.
            assert "ON CONFLICT (source_code, source_document_id) DO UPDATE" in s, \
                "document upsert 가 정정을 반영하지 않는다(DO NOTHING 회귀)"
            set_clause = s.split("DO UPDATE", 1)[1].split("WHERE", 1)[0]
            where_clause = s.split("DO UPDATE", 1)[1].split("WHERE", 1)[1]
            # fake 는 아래에서 이 컬럼들을 **자기 규칙으로** 갱신한다 — SQL 에서 한 컬럼이
            # 빠져도 그대로 통과하므로, 갱신 대상과 비교 대상을 문면에서 못 박는다(Rule 9)
            for column in ("title", "published_at", "source_uri", "language_code"):
                assert f"{column} = EXCLUDED.{column}" in set_clause, \
                    f"document upsert 가 {column} 을 갱신하지 않는다(정정이 안 반영된다)"
                assert f"document.{column} IS DISTINCT FROM EXCLUDED.{column}" in where_clause, \
                    f"document upsert 가 {column} 변경을 판정하지 않는다(멱등이 거짓이 된다)"
            # 정정과 함께 시각도 움직여야 PIT 축이 내용과 어긋나지 않는다(ALPHA-691 2라운드)
            # ⚠️ FOR UPDATE 는 **없는 키를 못 잠근다** — 동시 최초 INSERT 가 끼면 잠금
            # 판정만으로는 낡은 관측이 최신값을 되돌린다. 가드는 여기에도 있어야 한다.
            # 시각은 단조 증가만 — 뒤로 밀면 과거 as-of 구간에서 문서가 사라진다
            assert "GREATEST(document.available_at, EXCLUDED.available_at)" in set_clause, \
                "document upsert 의 도착 시각이 단조가 아니다"
            # ⚠️ **내용 쓰기를 시각으로 막지 않는다**(ALPHA-691 모델). 이 fake 는 WHERE 를
            # 해석하지 않으므로, 가드가 다시 들어와도 여기서 막지 않으면 "정정이 건너뛰어진다"
            # 는 실제 PG 회귀를 테스트가 통과시킨다 — 배치가 미래 published_at 을 실은 행에서
            # 정확히 그 P1 이 재현된다.
            assert "available_at <=" not in where_clause, \
                "내용 쓰기가 시각으로 막혀 있다 — 미래 timestamp 행에서 정정이 유실된다"
            self._upsert_document(params)
        elif s.startswith("INSERT INTO news_document"):
            assert "ON CONFLICT (document_id) DO UPDATE" in s, \
                "news_document upsert 가 리드 정정을 반영하지 않는다"

            assert "SET lead_text = EXCLUDED.lead_text" in s, \
                "news_document upsert 가 리드를 갱신하지 않는다"
            assert "news_document.lead_text IS DISTINCT FROM EXCLUDED.lead_text" in s, \
                "같은 리드에도 UPDATE 하면 멱등 집계가 거짓이 된다"
            self._upsert_news_document(params)
        elif s.startswith("INSERT INTO news_source_item"):
            self._insert_source_item(params)
        elif s.startswith(
            "SELECT content_checksum, generation, canonical_article_id, last_seen_at "
            "FROM news_source_item"
        ):
            assert "FOR UPDATE" in s, "source item 비교는 행을 잠가야 한다"
            row = self.db.source_items.get((params[0], params[1]))
            if row is not None:
                self._rows = [(
                    row["content_checksum"], row["generation"],
                    row["canonical_article_id"], row["last_seen_at"],
                )]
        elif s.startswith("UPDATE news_source_item"):
            self._update_source_item(params)
        elif s.startswith("INSERT INTO news_poll_anchor"):
            self._upsert_anchor(params)
        elif s.startswith("SELECT success_anchor_ids, head_anchor_ids"):
            row = self.db.anchors.get((params[0], params[1]))
            if row is not None:
                self._rows = [(
                    row["success_anchor_ids"], row["head_anchor_ids"],
                    row["success_poll_at"], row["head_poll_at"],
                )]
        elif s.startswith("INSERT INTO news_extraction_job"):
            self._insert_job("news", params)
        elif s.startswith("INSERT INTO price_window_job"):
            self._insert_job("price", params)
        elif s.startswith("SELECT source_code, article_id, input_fingerprint"):
            # job 선언 정체성 조회(ALPHA-689). 컬럼 **순서**가 handler 의 zip 매핑을
            # 정하므로 fake 가 의미를 합성하기 전에 문면을 못 박는다 — 순서가 바뀌면
            # article_id 자리에 지문이 들어가 정상 job 이 전부 불일치로 막힌다.
            for clause in ("tagger_version, ontology_version",
                           "FROM news_extraction_job WHERE job_id = %s"):
                assert clause in s, f"news_job_identity SQL 에 {clause} 가 없다"
            row = self.db.jobs.get(("news", params[0]))
            if row is not None:
                self._rows = [(row["source_code"], row["article_id"],
                               row["input_fingerprint"], row["tagger_version"],
                               row["ontology_version"])]
        elif s.startswith("SELECT session_id, window_start, generation, trigger_schema_version"):
            # price job 선언 정체성 조회(ALPHA-708) — news 와 같은 이유로 문면을 못 박는다
            assert "FROM price_window_job WHERE job_id = %s" in s
            row = self.db.jobs.get(("price", params[0]))
            if row is not None:
                self._rows = [(row["session_id"], row["window_start"],
                               row["generation"], row["trigger_schema_version"])]
        elif s.startswith("SELECT window_start, generation, data_status, checksum"):
            # 정규장 첫 window(시가 근거) 조회 — ORDER BY·하한(>=)이 빠지면 시간외
            # 세션에서 08:00 window 가 정본이 되는 조용한 회귀라 문면을 못 박는다
            assert "ORDER BY window_start ASC LIMIT 1" in s
            assert "window_start >= %s" in s
            rows = sorted(
                (r for (sid, _), r in self.db.windows.items()
                 if sid == params[0] and r["window_start"] >= params[1]),
                key=lambda r: r["window_start"],
            )
            if rows:
                first = rows[0]
                self._rows = [(first["window_start"], first["generation"],
                               first["data_status"], first["checksum"])]
        elif s.startswith("SELECT attempt_count, redrive_generation FROM price_window_job"):
            # 트리거 persist 의 attempt fence(ALPHA-708) — CLAIMED 조건·FOR UPDATE 가
            # 빠지면 낡은 attempt 의 쓰기가 실DB 에서 통과한다
            assert "status = 'CLAIMED'" in s and "FOR UPDATE" in s
            row = self.db.jobs.get(("price", params[0]))
            if row is not None and row["status"] == "CLAIMED":
                self._rows = [(row["attempt_count"], row["redrive_generation"])]
        elif s.startswith("SELECT generation, checksum FROM minute_ingestion_window"):
            row = self.db.windows.get((params[0], params[1]))
            if row is not None:
                self._rows = [(row["generation"], row["checksum"])]
        elif s.startswith("SELECT generation, data_status FROM minute_ingestion_window"):
            # persist·시가 확정의 stale 대조(ALPHA-708) — FOR UPDATE 가 빠지면 실DB 에선
            # 대조와 삽입 사이에 정정이 끼어드는 TOCTOU 라 문면을 못 박는다
            assert "FOR UPDATE" in s
            row = self.db.windows.get((params[0], params[1]))
            if row is not None:
                self._rows = [(row["generation"], row["data_status"])]
        elif s.startswith("SELECT entity_id, status, open_price FROM minute_session_open"):
            self._rows = [
                (entity, row["status"], row["open_price"])
                for (sid, entity), row in self.db.session_opens.items()
                if sid == params[0]
            ]
        elif s.startswith("INSERT INTO minute_session_open"):
            assert "ON CONFLICT (session_id, entity_id) DO NOTHING" in s
            key = (params[0], params[1])
            if key not in self.db.session_opens:  # 확정 후 불변
                self.db.session_opens[key] = {
                    "status": params[2], "open_price": params[3],
                    "reason": params[4], "source_window": params[5],
                }
        elif s.startswith("SELECT i.ticker, p.close_price"):
            # 기준선(전일 종가) 조회(ALPHA-745). 기준일 하한(trade_date < 세션일)·시장
            # 필터가 빠지면 당일 종가나 동명 해외 티커가 기준선이 되므로 문면을 못 박는다
            assert "p.trade_date = ( SELECT max(trade_date) FROM price_daily" in s
            assert "WHERE trade_date < %s )" in s
            assert "i.market_code = ANY(%s)" in s
            self._rows = [(ticker, close) for ticker, close
                          in sorted(self.db.prev_closes.items())
                          if ticker in params[0]]
        elif s.startswith("SELECT entity_id, anchor_price FROM minute_trigger_anchor"):
            self._rows = [(entity, row["anchor_price"])
                          for (sid, entity), row in self.db.trigger_anchors.items()
                          if sid == params[0]]
        elif s.startswith("UPDATE minute_trigger_anchor"):
            # 회수는 **조건부 UPDATE 의 RETURNING** 이 중복 차단이다(ALPHA-745) —
            # `anchor_price <> %s` 가 빠지면 복귀 구간 매 window 마다 회수 사건이 나간다.
            # `anchor_window < %s` 가 빠지면 낡은 window 의 재배달이 최신 발화를 회수로
            # 덮으면서 사건은 event_id 충돌로 못 내보낸다(하류가 노출 상태로 고착).
            assert "anchor_price <> %s" in s and "RETURNING entity_id" in s
            assert "anchor_window < %s" in s
            price, window, session_id, entity, guard_price, guard_window = params
            row = self.db.trigger_anchors.get((session_id, entity))
            # 비교 대상은 **저장된 6자리 값 vs 파라미터 원본**이다 — Postgres 는 넘어온
            # 파라미터를 컬럼 스케일로 반올림하지 않는다. 여기서 양자화하면 정밀도
            # 회귀가 fake 안에서 자기 자신에 의해 가려진다
            if (row is not None
                    and row["anchor_price"] != _decimal_of(guard_price)
                    and row["anchor_window"] < guard_window):
                row.update(anchor_price=_numeric6(price), anchor_window=window)
                self.rowcount = 1
                self._rows = [(entity,)]
        elif s.startswith("INSERT INTO minute_trigger_anchor"):
            # 발화 앵커 이동 — upsert 가 아니면 두 번째 발화가 예외로 죽고, window
            # 전진 조건이 빠지면 낡은 발화가 최신 앵커를 되돌린다
            assert "ON CONFLICT (session_id, entity_id) DO UPDATE" in s
            assert "WHERE minute_trigger_anchor.anchor_window < EXCLUDED.anchor_window" in s
            session_id, entity, price, window = params
            row = self.db.trigger_anchors.get((session_id, entity))
            if row is None:
                self.db.trigger_anchors[(session_id, entity)] = {
                    "anchor_price": _numeric6(price), "anchor_window": window}
            elif row["anchor_window"] < window:
                row.update(anchor_price=_numeric6(price), anchor_window=window)
        elif s.startswith("INSERT INTO minute_price_trigger"):
            # v2 멱등 축은 UNIQUE(entity_id, session_id, window_start)(ALPHA-745) —
            # DO NOTHING 이 빠지면 실DB 에선 같은 window 재판정이 예외로 죽는다.
            # 쿨다운 축으로 되돌아가면 앵커 재발화가 통째로 막히므로 문면을 못 박는다.
            assert "ON CONFLICT (entity_id, session_id, window_start) DO NOTHING" in s
            assert "cooldown_bucket" not in s
            entity, session_id, window_start = params[1], params[2], params[3]
            if not any((r["entity_id"], r["session_id"], r["window_start"])
                       == (entity, session_id, window_start)
                       for r in self.db.triggers.values()):
                self.db.triggers[params[0]] = {
                    "trigger_id": params[0], "entity_id": entity,
                    "session_id": session_id, "window_start": window_start,
                    "generation": params[4], "detection_policy_version": params[5],
                    "open_price": params[6], "close_price": params[7],
                    "change_rate": params[8], "threshold": params[9],
                    "anchor_price": params[10], "seq": self.db.next_seq(),
                }
                self._rows = [(params[0],)]
        elif s.startswith("SELECT status, next_attempt_at, redrive_generation"):
            self._fetch_job(_job_kind(s), params)
        elif "SET status = 'CLAIMED'" in s and "redrive_generation = %s" in s:
            # 메시지 기반 claim(ALPHA-672) — 일반 폴링 claim 보다 **먼저** 걸러야 한다
            # (접두가 겹친다). 세대 가드가 SQL 에서 빠지면 redrive 가 무효가 되므로
            # fake 가 합성하기 전에 절의 존재를 못 박는다(Rule 9)
            for clause in (
                "WHERE job_id = %s AND redrive_generation = %s",
                "status = 'PENDING'",
                "status = 'RETRY_WAIT' AND next_attempt_at <= %s",
                "status = 'CLAIMED' AND (lease_expires_at IS NULL",
            ):
                assert clause in s, f"claim_job SQL 에 {clause} 가 없다"
            self._claim_job_by_id(_job_kind(s), params)
        elif "SET status = 'CLAIMED'" in s:
            self._claim_job("price" if "price_window_job" in s else "news", params)
        elif "SET lease_expires_at = %s, updated_at" in s:
            # heartbeat — attempt fence 가 빠지면 옛 attempt 가 새 claim 의 lease 를
            # 연장해 두 실행이 동시에 살아 있게 된다
            for clause in ("claimed_by = %s", "status = 'CLAIMED'", "attempt_count = %s"):
                assert clause in s, f"heartbeat_job SQL 에 {clause} 가 없다"
            self._heartbeat_job(_job_kind(s), params)
        elif "SET status = 'DEAD', error_code = %s" in s and "redrive_generation = %s" in s:
            for clause in (
                "status IN ('PENDING', 'RETRY_WAIT', 'CLAIMED')",
                "status <> 'CLAIMED' OR lease_expires_at IS NULL OR lease_expires_at < %s",
            ):
                assert clause in s, f"dead_on_dlq SQL 에 {clause} 가 없다"
            self._dead_on_dlq(_job_kind(s), params)
        elif s.startswith("SELECT status, redrive_generation, lease_expires_at, error_code FROM"):
            assert "FOR UPDATE" in s, "redrive 는 job 행을 잠그고 상태를 봐야 한다"
            row = self.db.jobs.get((_job_kind(s), params[0]))
            if row is not None:
                self._rows = [(row["status"], row["redrive_generation"],
                               row["lease_expires_at"], row["error_code"])]
        elif s.startswith("SELECT destination, generation, payload"):
            row = self.db.outbox.get(params[0])
            if row is not None:
                self._rows = [(row["destination"], row["generation"], row["payload"],
                               row["status"])]
        elif "SET status = 'RETRY_WAIT', redrive_generation = redrive_generation + 1" in s:
            # 관측한 상태·세대를 CAS 에 그대로 되건다 — 빠지면 그새 바뀐 job 을
            # 덮어써 진행 중인 실행이나 방금 끝난 결과를 잃는다
            for clause in ("status = %s", "redrive_generation = %s"):
                assert clause in s, f"redrive SQL 에 {clause} 가 없다"
            self._redrive_job(_job_kind(s), params)
        elif s.startswith("SELECT j.generation, w.generation"):
            # 직전 TOCTOU 수정의 핵심 — 잠금 구문이 사라지는 회귀를 fake 가 잡는다
            assert "FOR UPDATE OF w" in s, "stale 검사는 window 행을 잠가야 한다"
            self._job_window_generation(params)
        elif "SET status = 'DEAD', error_code = 'STALE'" in s:
            self._stale_dead(params)
        elif "WHERE job_id = %s AND claimed_by = %s AND status = 'CLAIMED'" in s:
            # 세대 fence 가 빠지면 옛 실행의 늦은 보고가 redrive 로 만든 새 세대를
            # 마감한다 — fake 가 합성하기 전에 절의 존재를 못 박는다(Rule 9)
            assert "redrive_generation = %s" in s, "전이 SQL 에 세대 fence 가 없다"
            self._job_transition("price" if "price_window_job" in s else "news", params)
        elif s.startswith("UPDATE dataset_commit_outbox SET last_error = concat_ws"):
            # 덧붙이기다 — 덮어쓰면 Relay 가 왜 포기했는지(redrive 판단의 근거)가 사라진다
            row = self.db.outbox.get(params[1])
            if row is not None:
                row["last_error"] = " | ".join(
                    part for part in (row["last_error"], params[0]) if part
                )
                self.rowcount = 1
        elif s.startswith("INSERT INTO dataset_commit_outbox"):
            self._insert_outbox(params)
        elif s.startswith("SELECT o.status, COUNT(*) FROM dataset_commit_outbox"):
            # DEAD 도 미발행이다 — SQL 이 NEW 만 세도록 바뀌면 배출 게이트가 격리분을
            # 남긴 채 성공으로 끝난다(fake 가 하드코딩하면 그 회귀가 숨는다)
            assert "o.status = 'DEAD'" in s, "미발행 집계는 NEW·DEAD 를 함께 세야 한다"
            # redrive 가 만든 더 새 delivery 가 있으면 그 DEAD 는 대체된 것이다
            assert "NOT EXISTS" in s, "대체된 DEAD 제외 절이 없다"
            counts: dict[str, int] = {}
            for row in self.db.outbox.values():
                if row["status"] == "DEAD" and any(
                    other["aggregate_id"] == row["aggregate_id"]
                    and other["seq"] > row["seq"]
                    for other in self.db.outbox.values()
                ):
                    continue   # 더 새 delivery 가 대신한다
                if row["status"] in ("NEW", "DEAD"):
                    counts[row["status"]] = counts.get(row["status"], 0) + 1
            self._rows = sorted(counts.items())
        elif s.startswith("UPDATE dataset_commit_outbox o SET claimed_by"):
            self._claim_outbox(params, s)
        elif "SET status = 'PUBLISHED'" in s:
            # CAS 가드를 fake 가 Python 으로 재현하므로, SQL 에서 빠지면 여기서 못 잡는다
            # — 절의 존재를 못 박는다(옛 Relay 가 새 claim 을 마감하는 회귀, Rule 9)
            for clause in ("claimed_by = %s", "status = 'NEW'", "claim_expires_at = %s"):
                assert clause in s, f"mark_published SQL 에 {clause} 가 없다"
            self._mark_published(params)
        elif s.startswith("UPDATE dataset_commit_outbox SET status = %s, attempt_count"):
            for clause in ("claimed_by = %s", "status = 'NEW'", "claim_expires_at = %s"):
                assert clause in s, f"record_publish_failure SQL 에 {clause} 가 없다"
            self._publish_failure(params)
        else:
            raise AssertionError(f"FakeMinuteDB 가 모르는 SQL: {s[:120]}")

    # ── session ──
    def _upsert_document(self, p):
        (document_id, source_code, article_id, title, language_code, published_at,
         available_at, source_uri) = p
        existing = self.db.documents.get((source_code, article_id))
        if existing is None:
            self.db.documents[(source_code, article_id)] = {
                "document_id": document_id, "source_code": source_code,
                "source_document_id": article_id, "title": title,
                "language_code": language_code, "published_at": published_at,
                # ⚠️ 최초 1회만 — 실제 SQL 도 DO UPDATE 목록에서 뺐다
                "available_at": available_at, "source_uri": source_uri,
            }
            self.rowcount = 1
            return
        changed = {
            "title": title, "language_code": language_code,
            "published_at": published_at, "source_uri": source_uri,
        }
        if all(existing[k] == v for k, v in changed.items()):
            self.rowcount = 0   # IS DISTINCT FROM — 값이 같으면 UPDATE 하지 않는다
            return
        existing.update(changed)
        # 정정과 함께 움직이되 **앞으로만**(GREATEST)
        existing["available_at"] = max(existing["available_at"], available_at)
        self.rowcount = 1

    def _upsert_news_document(self, p):
        lead_text, source_code, article_id = p
        document = self.db.documents.get((source_code, article_id))
        if document is None:
            self.rowcount = 0   # INSERT … SELECT 가 0행 — 부모가 없으면 아무것도 안 쓴다
            return
        key = document["document_id"]
        existing = self.db.news_documents.get(key)
        if existing is not None and existing["lead_text"] == lead_text:
            self.rowcount = 0
            return
        self.db.news_documents[key] = {"document_id": key, "lead_text": lead_text}
        self.rowcount = 1

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

    def _begin_qc(self, p):
        row = self.db.sessions.get(p[0])
        if row is None or row["phase"] not in ("DRAINED", "QC_RUNNING", "FAILED"):
            return
        row["phase"] = "QC_RUNNING"
        row["worker_fencing_token"] = row.get("worker_fencing_token", 0) + 1
        self._rows = [(row["dataset"], row["source_group"], row["session_date"],
                       row["expected_window_count"], row["universe_version"],
                       row["worker_fencing_token"])]

    def _confirm_missing(self, p):
        # 소유권 검사는 앞선 잠금 SELECT 가 한다(실제 코드와 같은 순서) — 여기서 다시
        # 합성하면 그 SELECT 가 빠져도 테스트가 통과한다
        session_id, now = p
        changed = 0
        for window in self.db.windows.values():
            if (window["session_id"] == session_id and window["data_status"] == "DUE"
                    and window["scheduled_at"] <= now):
                window["data_status"] = "MISSING"
                changed += 1
        self.rowcount = changed

    def _finish_qc(self, session_id, phase, *, fence_token, final_checksum=None,
                   final_generation=None):
        row = self.db.sessions.get(session_id)
        if (row is None or row["phase"] != "QC_RUNNING"
                or row.get("worker_fencing_token") != fence_token):
            self.rowcount = 0
            return
        row["phase"] = phase
        if phase == "FINALIZED":
            row["final_checksum"] = final_checksum
            row["final_generation"] = final_generation
        self.rowcount = 1

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
            "checksum": None,
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
            (window["window_start"], window["window_end"], window["generation"],
             window.get("checksum"), window.get("manifest_checksum"),
             window["attempt_count"], window["claim_token"])
        ]

    def _record_outcome(self, p):
        (data_status, expected, succeeded, failed, records, checksum_case,
         manifest_checksum_case, checksum, manifest_uri, manifest_checksum,
         missing_units, stage_timestamps,
         session_id, window_start, worker_id, claim_token) = p
        window = self.db.windows.get((session_id, window_start))
        # fence 검사는 repository 가 _fence_holds 로 먼저 한다 — 여기선 claim 만 검사
        if (
            window is None
            or window["claimed_by"] != worker_id
            or window["claim_token"] != claim_token
        ):
            return
        # records checksum 과 manifest_checksum 둘 다 불변일 때만 generation 유지
        generation = (
            window["generation"]
            if (window.get("checksum") == checksum_case
                and window.get("manifest_checksum") == manifest_checksum_case)
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
        self._rows = [(window["generation"],)]

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
                 or (r["status"] == "CLAIMED" and _expired(r["lease_expires_at"], now)))
        ]
        if not candidates:
            return
        row = min(candidates, key=lambda r: r["seq"])
        row.update(status="CLAIMED", claimed_by=worker_id, lease_expires_at=lease_until,
                   attempt_count=row["attempt_count"] + 1)
        self._rows = [(row["job_id"], row["attempt_count"], row["redrive_generation"])]

    # ── 메시지 기반 job 경로 (ALPHA-672) ──
    def _fetch_job(self, kind, p):
        row = self.db.jobs.get((kind, p[0]))
        if row is not None:
            self._rows = [(row["status"], row["next_attempt_at"],
                           row["redrive_generation"], row["attempt_count"])]

    def _claim_job_by_id(self, kind, p):
        worker_id, lease_until, job_id, generation, now, now2 = p
        row = self.db.jobs.get((kind, job_id))
        if row is None or row["redrive_generation"] != generation:
            return
        eligible = (
            row["status"] == "PENDING"
            or (row["status"] == "RETRY_WAIT" and row["next_attempt_at"] is not None
                and row["next_attempt_at"] <= now)
            or (row["status"] == "CLAIMED" and _expired(row["lease_expires_at"], now2))
        )
        if not eligible:
            return
        row.update(status="CLAIMED", claimed_by=worker_id, lease_expires_at=lease_until,
                   attempt_count=row["attempt_count"] + 1)
        self.rowcount = 1
        self._rows = [(row["attempt_count"],)]

    def _heartbeat_job(self, kind, p):
        lease_until, job_id, worker_id, attempt, generation = p
        row = self.db.jobs.get((kind, job_id))
        if (row is None or row["claimed_by"] != worker_id
                or row["status"] != "CLAIMED" or row["attempt_count"] != attempt
                or row["redrive_generation"] != generation):
            return
        row["lease_expires_at"] = lease_until
        self.rowcount = 1

    def _dead_on_dlq(self, kind, p):
        error_code, now, job_id, generation, now2 = p
        row = self.db.jobs.get((kind, job_id))
        if row is None or row["redrive_generation"] != generation:
            return
        if row["status"] not in ("PENDING", "RETRY_WAIT", "CLAIMED"):
            return
        if row["status"] == "CLAIMED" and not _expired(row["lease_expires_at"], now2):
            return  # 살아 있는 lease — 실행 중이다
        row.update(status="DEAD", error_code=error_code, completed_at=now,
                   claimed_by=None, lease_expires_at=None)
        self.rowcount = 1

    def _redrive_job(self, kind, p):
        now, job_id, status, generation = p
        row = self.db.jobs.get((kind, job_id))
        if row is None or row["status"] != status or row["redrive_generation"] != generation:
            return
        row.update(status="RETRY_WAIT", redrive_generation=generation + 1,
                   next_attempt_at=now, attempt_count=0, completed_at=None,
                   claimed_by=None, lease_expires_at=None)
        self.rowcount = 1

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
         job_id, worker_id, attempt, generation) = p
        row = self.db.jobs.get((kind, job_id))
        if (row is None or row["claimed_by"] != worker_id
                or row["status"] != "CLAIMED" or row["attempt_count"] != attempt
                or row["redrive_generation"] != generation):
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

    def _claim_outbox(self, p, sql=""):
        # 실제 RETURNING 에 attempt_count 가 없으면 Relay 의 backoff 가 IndexError 로
        # 죽는다 — fake 가 합성해 주면 그 회귀가 여기서 숨는다(Rule 9)
        assert "o.attempt_count" in sql, "outbox claim 은 attempt_count 를 반환해야 한다"
        # 아래 의미는 fake 가 **합성**한다 — SQL 에서 가드가 빠져도 fake 만 보면 통과하므로
        # (실DB 에선 조기 재claim·claim 탈취·중복 발행) 절의 존재를 여기서 못 박는다(Rule 9)
        for clause in (
            "c.status = 'NEW'",
            "c.next_attempt_at IS NULL OR c.next_attempt_at <= %s",
            "c.claimed_by IS NULL OR c.claim_expires_at < %s",
            "FOR UPDATE SKIP LOCKED",
            "ORDER BY c.created_at",
        ):
            assert clause in sql, f"outbox claim SQL 에 {clause} 가 없다"
        # destination 필터가 있는 변형은 파라미터가 6개다(SQL 과 파라미터 수를 함께 본다)
        excluded = ()
        destination = None
        if "c.destination = %s" in sql:
            relay_id, claim_until, now, now2, destination, limit = p
        elif "NOT (c.destination = ANY(%s))" in sql:
            relay_id, claim_until, now, now2, excluded, limit = p
        else:
            relay_id, claim_until, now, now2, limit = p
        eligible = sorted(
            (r for r in self.db.outbox.values()
             if r["status"] == "NEW"
             and (destination is None or r["destination"] == destination)
             and r["destination"] not in set(excluded)
             and (r["next_attempt_at"] is None or r["next_attempt_at"] <= now)
             and (r["claimed_by"] is None or r["claim_expires_at"] < now)),
            key=lambda r: r["seq"],
        )[:limit]
        for row in eligible:
            row.update(claimed_by=relay_id, claim_expires_at=claim_until)
        self._rows = [
            (r["event_id"], r["event_type"], r["destination"], r["payload"],
             r["claim_expires_at"], r["attempt_count"]) for r in eligible
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

    def _release_claim(self, p):
        session_id, window_start, worker_id, claim_token = p
        window = self.db.windows.get((session_id, window_start))
        if (window is None or window["claimed_by"] != worker_id
                or window["claim_token"] != claim_token
                or window["data_status"] != "CLAIMED"):
            return
        window.update(data_status="DUE", claimed_by=None, claim_token=None,
                      lease_expires_at=None)
        self.rowcount = 1

    def _release_fence(self, p):
        session_id, fence_token = p
        row = self.db.sessions.get(session_id)
        if row is None or row["worker_fencing_token"] != fence_token:
            return
        row["lease_expires_at"] = None
        self.rowcount = 1

    # ── news_source_item (ALPHA-668) ──
    def _insert_source_item(self, p):
        source_code, source_item_id, canonical_article_id, first_seen, last_seen, checksum = p
        key = (source_code, source_item_id)
        if key in self.db.source_items:
            return  # ON CONFLICT DO NOTHING
        self.db.source_items[key] = {
            "source_code": source_code, "source_item_id": source_item_id,
            "canonical_article_id": canonical_article_id,
            "first_seen_at": first_seen, "last_seen_at": last_seen,
            "content_checksum": checksum, "generation": 1,
        }
        self._rows = [(1,)]

    # ── news_poll_anchor (ALPHA-669) ──
    def _upsert_anchor(self, p):
        (session_id, source_code, success_json, head_json, success_at, head_at,
         advance, advance2) = p
        assert advance is advance2, "success anchor 전진 조건이 두 컬럼에서 갈렸다"
        key = (session_id, source_code)
        row = self.db.anchors.get(key)
        if row is None:
            self.db.anchors[key] = {
                "session_id": session_id, "source_code": source_code,
                # 실제 PG 는 ::jsonb 로 파싱해 저장한다 — 문자열로 두면 자료형이 갈린다
                "success_anchor_ids": json.loads(success_json),
                "head_anchor_ids": json.loads(head_json),
                "success_poll_at": success_at, "head_poll_at": head_at,
            }
            self.rowcount = 1
            return
        row.update(head_anchor_ids=json.loads(head_json), head_poll_at=head_at)
        if advance:
            row.update(success_anchor_ids=json.loads(success_json),
                       success_poll_at=success_at)
        self.rowcount = 1

    def _update_source_item(self, p):
        last_seen, checksum, generation, canonical_article_id, source_code, source_item_id = p
        row = self.db.source_items.get((source_code, source_item_id))
        if row is None:
            return
        row.update(
            last_seen_at=last_seen, content_checksum=checksum, generation=generation,
            canonical_article_id=canonical_article_id,
        )
        self.rowcount = 1
