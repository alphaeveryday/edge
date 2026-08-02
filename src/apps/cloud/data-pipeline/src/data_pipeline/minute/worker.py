"""1분 Worker tick 루프 골격 + Price Worker (ALPHA-667, 계획 §9 — 토스 adapter 는 별도).

`MinuteWorkerLoop` 는 가격·뉴스가 공유하는 tick 골격(fence 유지·phase 관측·2-lane
claim·drain 수렴)이고, window 하나를 어떻게 처리하는지(`_process`)만 구현체가 채운다.
뉴스 Worker(ALPHA-669, news_worker.py)가 같은 골격을 쓴다 — 이 fencing/drain 논리를
복제하면 한쪽만 고쳐지는 divergence 가 생긴다.

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
  4. window 처리 — 기대 유니버스 결정(`Universe.units_at` — 그 window 의 시각에 캔들이
     있어야 하는 종목만) → collect → 세대 예측 → artifact/manifest PUT → fenced commit.
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
    ArtifactImmutabilityError,
    build_window_manifest,
    put_immutable,
    serialize_manifest,
    serialize_records,
    sha256_bytes,
)
from .commit import (
    CanonicalWriter,
    CommitRejectedError,
    GenerationMismatchError,
    MinuteCommitter,
)
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


class MinuteWorkerLoop:
    """tick 골격 — 구현체(가격/뉴스)는 `_process(claim, now) -> bool` 만 채운다.

    필드는 dataclass 인 구현체가 선언한다(session_id·ledger·config·fence_token·
    stopping·_last_heartbeat). config 는 worker_id 와 lease/heartbeat/recovery budget
    을 제공하는 어떤 설정이든 된다.
    """

    def _process(self, claim: dict, now: datetime) -> bool:
        raise NotImplementedError

    def request_stop(self) -> None:
        """SIGTERM 핸들러가 부른다 — 진행 중 tick 을 끊지 않고 다음 tick 에 멈춘다."""
        self.stopping = True

    # ── tick ─────────────────────────────────────────────────
    def tick(self, now: datetime) -> str:
        """한 사이클. 반환은 관측용 상태 문자열:
        STOPPED / DRAINING / DRAINED / IDLE / PROCESSED / WINDOW_FAILED
        """
        if self.stopping:
            if self.fence_token is not None:
                # lease 즉시 반납 — 교체 Worker 가 만료(수 분)를 기다리지 않게.
                # token 은 유지되므로 이 프로세스의 잔여 쓰기는 계속 거부된다.
                self.ledger.release_worker_fence(
                    session_id=self.session_id, fence_token=self.fence_token
                )
                self.fence_token = None
            return "STOPPED"
        if not self._ensure_fence(now):
            return "STOPPED"

        phase = self._session_phase()
        if phase == "DRAINING":
            # 신규(DUE) claim 은 원장이 금지하지만 **만료된 고아 CLAIMED 회수**는
            # DRAINING 에서도 허용된다(2B-2) — 실패로 남은 window 를 여기서 회수해
            # 처리하지 않으면 ack 가 CLAIMED 잔존으로 영구 거부돼 drain 이 안 끝난다.
            for _ in range(self.config.recovery_budget_per_tick):
                claim = self.ledger.claim_due_window(
                    session_id=self.session_id, worker_id=self.config.worker_id,
                    fence_token=self.fence_token, now=now,
                    lease_seconds=self.config.lease_seconds, lane="recovery",
                )
                if claim is None:
                    break
                if not self._process(claim, now):
                    # 반복 실패 window 를 CLAIMED 로 두면 ack 가 영구 거부된다 —
                    # DUE 로 반납하고 잔여 판정(MISSING 등)은 EOD QC 에 넘긴다
                    self.ledger.release_window_claim(
                        session_id=self.session_id, window_start=claim["window_start"],
                        worker_id=self.config.worker_id, claim_token=claim["claim_token"],
                    )
                else:
                    self.ledger.advance_watermarks(session_id=self.session_id)
            self.ledger.ack_drain(
                session_id=self.session_id, fence_token=self.fence_token, now=now
            )
            return "DRAINING"
        if phase not in ("ACTIVE",):
            return "DRAINED" if phase == "DRAINED" else "STOPPED"

        # realtime(최신) 1건 + recovery(최고령) budget 을 **항상** 이어서 소진한다 —
        # realtime 이 비었을 때만 recovery 를 보면 두 lane 의 due 조건이 같아 recovery
        # 가 영영 안 돌고, backlog 복구가 최신 분 처리에 밀려 지연된다
        processed = 0
        failed = False
        claim = self.ledger.claim_due_window(
            session_id=self.session_id, worker_id=self.config.worker_id,
            fence_token=self.fence_token, now=now,
            lease_seconds=self.config.lease_seconds, lane="realtime",
        )
        if claim is not None:
            processed += 1
            failed |= not self._process(claim, now)
        for _ in range(self.config.recovery_budget_per_tick):
            claim = self.ledger.claim_due_window(
                session_id=self.session_id, worker_id=self.config.worker_id,
                fence_token=self.fence_token, now=now,
                lease_seconds=self.config.lease_seconds, lane="recovery",
            )
            if claim is None:
                break
            processed += 1
            failed |= not self._process(claim, now)
        if processed == 0:
            return "IDLE"
        # 커밋된 진행을 session watermark 에 반영한다 — 안 하면 downstream(관측·drain
        # 판단·EOD)이 진행을 보지 못한다. 부분 실패여도 커밋된 만큼은 전진한다.
        self.ledger.advance_watermarks(session_id=self.session_id)
        return "WINDOW_FAILED" if failed else "PROCESSED"

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


@dataclass
class PriceWorker(MinuteWorkerLoop):
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

    def _predict_generation(self, claim: dict, artifact_checksum: str,
                            units: dict[str, list[str]],
                            expected_unit_ids: tuple[str, ...]) -> tuple[int, str, bytes, str]:
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
                expected_unit_ids=list(expected_unit_ids), units=units,
                artifact_key=artifact_key, artifact_checksum=artifact_checksum,
            )
            data = serialize_manifest(manifest)
            return artifact_key, data, sha256_bytes(data)

        current = claim["generation"]
        if current == 0 or artifact_checksum != claim["checksum"]:
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
            # 기대 유니버스는 **그 window 의 시각**이 정한다 — 전 종목을 매 window 에
            # 넘기면 15:30 이 마지막인 종목이 시간외 window 마다 missing 으로 잡혀
            # INCOMPLETE 가 영원히 재시도된다(2026-08-02 dev 실증)
            expected_unit_ids = cfg.universe.units_at(claim["window_start"])
            request = CollectionRequest(
                dataset=cfg.dataset, window_start=claim["window_start"],
                window_end=claim["window_end"], run_id=cfg.run_id,
                session_id=self.session_id, execution_mode="resident",
                universe_version=cfg.universe.universe_version,
                unit_ids=expected_unit_ids, failure_injection=None,
            )
            result, records, manifest_units = self.collector.collect(request, now)
            # 4분류 전체를 통과시킨다 — invalid 를 버리면 완전분할 검증이 터져
            # 정당한 INVALID 결과가 일시 실패로 위장돼 영구 재시도된다
            units = {
                cls: list(manifest_units.get(cls, []))
                for cls in ("received", "no_trade", "missing", "invalid")
            }
            # window/manifest 의 checksum 은 **저장되는 artifact 바이트**의 sha256 이다 —
            # 소비자는 bars.ndjson 을 재해시해 검증하므로 result_checksum(의미 해시)을
            # 쓰면 모든 정상 window 가 불일치로 판정된다. serialize_records 가 결정적이라
            # 이 값도 데이터 identity 로 동등하다.
            artifact_bytes = serialize_records(list(records))
            artifact_checksum = sha256_bytes(artifact_bytes)
            generation, artifact_key, manifest_bytes, manifest_checksum = (
                self._predict_generation(claim, artifact_checksum, units, expected_unit_ids)
            )
            put_immutable(self.storage, artifact_key, artifact_bytes)
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
                checksum=artifact_checksum, manifest_uri=manifest_key,
                manifest_checksum=manifest_checksum,
                missing_units=units["missing"] or None,
                stage_timestamps=result.stage_timestamps,
                records=records, canonical_writer=self.canonical_writer,
                dataset=cfg.dataset,
                trigger_schema_version=cfg.trigger_schema_version,
                destination=cfg.destination, artifact_generation=generation,
            )
            return True
        except (GenerationMismatchError, ArtifactImmutabilityError):
            # 결정적 예측/불변 artifact 의 불변식 위반 — 재시도해도 같은 충돌이 반복될
            # 뿐이다(회복 불가). 크게 죽어서 수퍼바이저/운영자가 보게 한다.
            raise
        except CommitRejectedError:
            # fence/claim 상실 — 이 window 는 새 소유자의 것이다. fence 까지 잃었다면
            # 다음 heartbeat 주기 tick 이 STOPPED 로 정지시킨다.
            logger.warning("window %s commit 거부 — claim/fence 상실", claim["window_start"])
            return False
        except Exception:
            # 한 window 의 실패를 다음 window 로 전파하지 않는다 — claim 은 lease 만료로
            # 재청구되고, 실패 자체는 크게 기록한다(조용한 폐기 금지, Rule 12)
            logger.exception(
                "window %s 처리 실패 — lease 만료 후 재시도된다", claim["window_start"]
            )
            return False
