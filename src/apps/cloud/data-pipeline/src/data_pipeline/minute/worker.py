"""1분 Price Worker loop (ALPHA-667, 계획 §9 Worker loop — 토스 adapter 는 별도).

tick 단위로 도는 상주 루프다. 벽시계를 직접 읽지 않는다 — clock 은 주입되고 tick 이
호출될 때마다 now 를 받는다(가상 시계 테스트 원칙). collector 는 CollectionRequest
계약(PR 1)으로 주입된다 — 토스 실측 형상이 확보되기 전까지는 FakePriceCollector 가
유일한 구현이고, adapter 는 형상 추측 없이 실측 후 붙인다(계획 §19).

한 tick 의 일:
  1. fence 유지 — 없으면 획득 시도, heartbeat 주기 도달 시 연장. 실패(상실)는 즉시
     STOPPED — 구 Worker 가 계속 돌면 어차피 모든 쓰기가 거부되지만, 헛돌 이유가 없다.
  2. phase 관측 — DRAINING 이면 신규 claim 을 멈추고 ack(원장이 CLAIMED 잔존 시 거부
     하므로 in-flight 가 끝날 때까지 ack 는 실패로 남는다), DRAINED/terminal 이면 정지.
  3. claim — realtime lane 우선 1건, 비면 recovery lane 을 tick 당 budget 만큼.
  4. window 처리 — collect → 세대 예측 → artifact/manifest PUT → fenced commit.
     한 window 의 예외는 그 window 에 격리된다(lease 만료로 재청구됨) — 다음 window
     진행을 막지 않는다(fail loud 로 기록하되 루프는 산다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..lake.storage import (
    Storage,
    minute_window_manifest_key,
    raw_price_minute_artifact_key,
)
from .artifacts import (
    build_window_manifest,
    put_immutable,
    serialize_manifest,
    serialize_records,
    sha256_bytes,
)
from .commit import CanonicalWriter, MinuteCommitter
from .models import KST, CollectionRequest, Universe
from .repository import MinuteLedger

logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    worker_id: str
    dataset: str
    source: str
    market: str
    session_date: str  # YYYY-MM-DD — artifact key 축
    universe: Universe
    run_id: str
    trigger_schema_version: str
    destination: str
    lease_seconds: int = 60
    session_lease_seconds: int = 300
    heartbeat_every_seconds: int = 60
    recovery_budget_per_tick: int = 2


@dataclass
class PriceWorker:
    """tick 을 외부(엔트리포인트/테스트)가 돌리는 수동 루프 — sleep 은 호출자 소관."""

    session_id: str
    ledger: MinuteLedger
    committer: MinuteCommitter
    storage: Storage
    collector: object  # CollectionRequest 계약 — collect(request, now) -> (result, records, manifest)
    canonical_writer: CanonicalWriter
    config: WorkerConfig
    fence_token: int | None = None
    stopping: bool = False  # SIGTERM — 새 claim 중단, 다음 tick 에서 STOPPED
    _last_heartbeat: datetime | None = field(default=None, repr=False)

    def request_stop(self) -> None:
        """SIGTERM 핸들러가 부른다 — 진행 중 tick 을 끊지 않고 다음 tick 에 멈춘다."""
        self.stopping = True

    # ── tick ─────────────────────────────────────────────────
    def tick(self, now: datetime) -> str:
        """한 사이클. 반환은 관측용 상태 문자열:
        STOPPED / DRAINING / DRAINED / IDLE / PROCESSED / WINDOW_FAILED
        """
        if self.stopping:
            return "STOPPED"
        if not self._ensure_fence(now):
            return "STOPPED"

        phase = self._session_phase()
        if phase == "DRAINING":
            # 신규 claim 금지 상태 — in-flight 는 이미 없다(tick 은 window 단위 완결).
            # ack 가 False 면 만료 안 된 CLAIMED 잔존(다른 원인) — 다음 tick 재시도.
            self.ledger.ack_drain(
                session_id=self.session_id, fence_token=self.fence_token, now=now
            )
            return "DRAINING"
        if phase not in ("ACTIVE",):
            return "DRAINED" if phase == "DRAINED" else "STOPPED"

        claim = self.ledger.claim_due_window(
            session_id=self.session_id, worker_id=self.config.worker_id,
            fence_token=self.fence_token, now=now,
            lease_seconds=self.config.lease_seconds, lane="realtime",
        )
        if claim is None:
            for _ in range(self.config.recovery_budget_per_tick):
                claim = self.ledger.claim_due_window(
                    session_id=self.session_id, worker_id=self.config.worker_id,
                    fence_token=self.fence_token, now=now,
                    lease_seconds=self.config.lease_seconds, lane="recovery",
                )
                if claim is None:
                    return "IDLE"
                if not self._process(claim, now):
                    return "WINDOW_FAILED"
            return "PROCESSED"
        return "PROCESSED" if self._process(claim, now) else "WINDOW_FAILED"

    # ── 내부 ─────────────────────────────────────────────────
    def _ensure_fence(self, now: datetime) -> bool:
        if self.fence_token is None:
            self.fence_token = self.ledger.acquire_worker_fence(
                session_id=self.session_id, worker_id=self.config.worker_id,
                now=now, lease_seconds=self.config.session_lease_seconds,
            )
            if self.fence_token is None:
                logger.warning("fence 획득 실패 — 다른 Worker 의 lease 가 살아 있다")
                return False
            self._last_heartbeat = now
            return True
        if (
            self._last_heartbeat is None
            or (now - self._last_heartbeat).total_seconds()
            >= self.config.heartbeat_every_seconds
        ):
            if not self.ledger.heartbeat(
                session_id=self.session_id, fence_token=self.fence_token,
                now=now, lease_seconds=self.config.session_lease_seconds,
            ):
                logger.error("heartbeat 거부 — fence 상실, 즉시 정지")
                return False
            self._last_heartbeat = now
        return True

    def _session_phase(self) -> str:
        # 가벼운 관측 read — fence 잠금 경로(_fenced_phase)는 쓰기 직전에만 쓴다
        with self.ledger.connect_fn(self.ledger.db) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT phase FROM minute_ingestion_session WHERE session_id = %s",
                (self.session_id,),
            )
            row = cur.fetchone()
            return "" if row is None else row[0]

    def _predict_generation(self, claim: dict, result_checksum: str,
                            units: dict[str, list[str]]) -> tuple[int, str, bytes, str]:
        """결정적 세대 예측 → (generation, artifact_key, manifest_bytes, manifest_checksum).

        세대 identity 는 records+manifest 두 checksum 이다(ALPHA-666) — manifest 는
        artifact_key(세대 포함)를 담으므로 세대 후보를 바꾸면 재산출해야 한다.
        """
        cfg = self.config
        window_hhmm = claim["window_start"].astimezone(KST).strftime("%H%M")

        def manifest_for(generation: int) -> tuple[str, bytes, str]:
            artifact_key = raw_price_minute_artifact_key(
                cfg.source, cfg.market, cfg.session_date, window_hhmm, generation
            )
            manifest = build_window_manifest(
                dataset=cfg.dataset, session_id=self.session_id,
                window_start=claim["window_start"], window_end=claim["window_end"],
                generation=generation,
                expected_unit_ids=list(cfg.universe.unit_ids), units=units,
                artifact_key=artifact_key, artifact_checksum=result_checksum,
            )
            data = serialize_manifest(manifest)
            return artifact_key, data, sha256_bytes(data)

        current = claim["generation"]
        if current == 0 or result_checksum != claim["checksum"]:
            generation = current + 1 if current else 1
            return (generation, *manifest_for(generation))
        # records 동일 — manifest 까지 같아야 세대 유지
        artifact_key, data, manifest_checksum = manifest_for(current)
        if manifest_checksum == claim["manifest_checksum"]:
            return current, artifact_key, data, manifest_checksum
        generation = current + 1
        return (generation, *manifest_for(generation))

    def _process(self, claim: dict, now: datetime) -> bool:
        cfg = self.config
        try:
            request = CollectionRequest(
                dataset=cfg.dataset, window_start=claim["window_start"],
                window_end=claim["window_end"], run_id=cfg.run_id,
                session_id=self.session_id, execution_mode="resident",
                universe_version=cfg.universe.universe_version,
                unit_ids=cfg.universe.unit_ids, failure_injection=None,
            )
            result, records, manifest_units = self.collector.collect(request, now)
            units = {
                "received": manifest_units["received"],
                "no_trade": manifest_units["no_trade"],
                "missing": manifest_units["missing"],
            }
            generation, artifact_key, manifest_bytes, manifest_checksum = (
                self._predict_generation(claim, result.result_checksum, units)
            )
            put_immutable(self.storage, artifact_key, serialize_records(list(records)))
            manifest_key = minute_window_manifest_key(
                cfg.dataset, cfg.source, cfg.market, cfg.session_date,
                claim["window_start"].astimezone(KST).strftime("%H%M"), generation,
            )
            put_immutable(self.storage, manifest_key, manifest_bytes)
            self.committer.commit_price_window(
                session_id=self.session_id, window_start=claim["window_start"],
                worker_id=cfg.worker_id, fence_token=self.fence_token,
                claim_token=claim["claim_token"], data_status=result.status,
                expected_unit_count=result.expected_count,
                succeeded_unit_count=result.succeeded_count,
                failed_unit_count=result.failed_count, record_count=len(records),
                checksum=result.result_checksum, manifest_uri=manifest_key,
                manifest_checksum=manifest_checksum,
                missing_units=units["missing"] or None,
                stage_timestamps=result.stage_timestamps,
                records=records, canonical_writer=self.canonical_writer,
                dataset=cfg.dataset,
                trigger_schema_version=cfg.trigger_schema_version,
                destination=cfg.destination, artifact_generation=generation,
            )
            return True
        except Exception:
            # 한 window 의 실패를 다음 window 로 전파하지 않는다 — claim 은 lease 만료로
            # 재청구되고, 실패 자체는 크게 기록한다(조용한 폐기 금지, Rule 12)
            logger.exception(
                "window %s 처리 실패 — lease 만료 후 재시도된다", claim["window_start"]
            )
            return False
