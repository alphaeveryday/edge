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
    canonical_etf_inav_minute_artifact_key,
    canonical_price_minute_artifact_key,
    minute_window_manifest_key,
)
from .artifacts import (
    UNIT_CLASSES,
    ArtifactImmutabilityError,
    build_window_manifest,
    put_immutable,
    serialize_manifest,
    serialize_records,
    sha256_bytes,
)
from .commit import (
    CommitRejectedError,
    GenerationMismatchError,
    MinuteCommitter,
)
from .models import KST, CollectionRequest, Universe
from .repository import MinuteLedger
from .rollup import maybe_rollup

logger = logging.getLogger(__name__)


def universe_matches(ledger: MinuteLedger, session_id: str, universe: Universe) -> bool:
    """원장이 고정한 universe 와 내 설정이 같은가 — window 를 처리할 자격.

    session 의 universe 는 생성 시 고정되는데(v0.7 10.1) 기대 집합은 **내 설정**으로
    계산한다. 둘이 갈리면 ① 원장이 고정한 종목이 조용히 누락되거나 ② 거래시간 밖이 된
    window 에서 `units_at` 이 터진다.

    window 계획 범위가 그 universe 와 맞는지는 보지 않는다 — 계획과 hash 를 같은
    universe 에서 뽑는 것은 planner 의 불변식이다(그 진입점에서 강제한다).
    """
    ledger_plan = ledger.session_universe(session_id=session_id)
    mine = (universe.universe_version, universe.universe_hash)
    if ledger_plan == mine:
        return True
    logger.error(
        "session %s 의 계획 %s 와 내 설정 %s 가 다르다 — 정지",
        session_id, ledger_plan, mine,
    )
    return False


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
    # 과거일 세션인가 — 참이면 commit 이 실시간 판정 outbox 를 내지 않는다(ALPHA-863).
    # ⚠️ **기본값을 두지 않는다**: 기본 False 면 새 구성 지점이 이 축을 빠뜨렸을 때
    # 조용히 실시간으로 판정돼 과거 봉이 트리거·설명(LLM)을 돌린다 — 08-08 에 실제로
    # 났고 outbox 390건을 손으로 DEAD 격리해 막았다. 빠뜨리면 TypeError 로 죽어야 한다.
    is_backfill: bool
    # ⚠️ window claim lease 는 **tick 상한보다 길어야** 한다 — 토스 실측 tick 은 73초+
    # (363종 ÷ 초당 5회)라, 60 으로 두면 자기 claim 이 in-flight 중 만료돼 recovery
    # lane 이 같은 window 를 재청구하고 원래 attempt 의 commit 이 거부된다(ALPHA-706).
    # 짧은 lease 로 만료를 검증하는 테스트는 이 값을 명시적으로 넘긴다.
    lease_seconds: int = 300
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

    # ── window 처리 공유부 (가격·iNAV) ────────────────────────
    # 뉴스는 이 조각을 쓰지 않는다(기사 N건 축이라 window 단위 artifact 가 없다).
    # 갈리는 곳은 넷뿐이라 아래 훅으로 열고 나머지는 한 벌로 둔다 — 복제하면 이 파일
    # 상단이 경고하는 divergence 가 정확히 그 형태로 생긴다(한쪽만 고쳐진다).

    def _expected_units(self, window_start: datetime) -> tuple[str, ...]:
        """그 window 에 **값이 있어야 하는** unit — 완전성 판정의 기대 집합."""
        raise NotImplementedError

    def _artifact_key(self, window_hhmm: str, generation: int) -> str:
        """이 dataset 의 canonical artifact 키(세대 포함)."""
        raise NotImplementedError

    def _commit(self, claim: dict, *, result, records: tuple, units: dict,
                generation: int, artifact_checksum: str, manifest_key: str,
                manifest_checksum: str) -> None:
        """확정 트랜잭션. dataset 마다 job/outbox 축이 달라 공유하지 않는다."""
        raise NotImplementedError

    def _after_commit(self, claim: dict) -> None:
        """커밋 뒤 파생(가격의 5분 롤업). 기본은 없음."""

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
            artifact_key = self._artifact_key(window_hhmm, generation)
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

    def _process_window(self, claim: dict, now: datetime) -> bool:
        """collect → 분할 검증 → artifact/manifest PUT → 확정. 예외 정책까지 공유부다."""
        cfg = self.config
        try:
            expected_unit_ids = self._expected_units(claim["window_start"])
            request = CollectionRequest(
                dataset=cfg.dataset, window_start=claim["window_start"],
                window_end=claim["window_end"], run_id=cfg.run_id,
                session_id=self.session_id, execution_mode="resident",
                universe_version=cfg.universe.universe_version,
                unit_ids=expected_unit_ids, failure_injection=None,
            )
            result, records, manifest_units = self.collector.collect(request, now)
            # 4분류 전체를 통과시킨다 — invalid 를 버리면 완전분할 검증이 터져
            # 정당한 INVALID 결과가 일시 실패로 위장돼 영구 재시도된다. 미지 분류는
            # 여기서 터뜨린다: 걸러서 넘기면 manifest 검증(미지 키)이 실행되지 않아
            # 우리가 이해 못 한 관측이 증거에서 사라진 채 window 가 성공 커밋된다
            if unknown := set(manifest_units) - UNIT_CLASSES:
                raise ValueError(f"collector 가 미지 unit 분류를 냈다: {sorted(unknown)}")
            units = {cls: list(manifest_units.get(cls, [])) for cls in sorted(UNIT_CLASSES)}
            # 원장에 실리는 수량(result)과 증거로 남는 분할(manifest)이 같은 관측을
            # 말하는지 대조한다 — 각자의 validator 는 상대를 모르므로, 어긋나면
            # "missing_units 는 있는데 VALID·failed=0" 같은 성공 위장이 커밋된다
            failed = len(units["missing"]) + len(units["invalid"])
            succeeded = len(units["received"]) + len(units["no_trade"])
            if (succeeded, failed) != (result.succeeded_count, result.failed_count):
                raise ValueError(
                    f"collector 의 result 수량({result.succeeded_count}/{result.failed_count})과 "
                    f"manifest 분할({succeeded}/{failed})이 다르다"
                )
            # window/manifest 의 checksum 은 **저장되는 artifact 바이트**의 sha256 이다 —
            # 소비자는 저장된 파일을 재해시해 검증하므로 result_checksum(의미 해시)을
            # 쓰면 모든 정상 window 가 불일치로 판정된다. serialize_records 가 결정적이라
            # 이 값도 데이터 identity 로 동등하다.
            # 벤더 축은 canonical 키에서 빠졌다(ALPHA-705) — 소비자가 벤더를 알 수
            # 있도록 레코드 컬럼으로 싣는다. checksum 은 이 최종 형상에서 나온다.
            records = tuple(dict(record, source=cfg.source) for record in records)
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
            self._commit(
                claim, result=result, records=records, units=units,
                generation=generation, artifact_checksum=artifact_checksum,
                manifest_key=manifest_key, manifest_checksum=manifest_checksum,
            )
            self._after_commit(claim)
            return True
        except (GenerationMismatchError, ArtifactImmutabilityError):
            # 결정적 예측/불변 artifact 의 불변식 위반 — 재시도해도 같은 충돌이 반복될
            # 뿐이다(회복 불가). 크게 죽어서 수퍼바이저/운영자가 보게 한다.
            # ⚠️ collector 계약 위반(미지 분류·수량 불일치)은 여기 넣지 않는다: 전파하면
            # drain 이 release/ack 을 못 거쳐 세션이 DRAINING 에 고착되고, 교체 Worker 가
            # 같은 window 로 크래시 루프를 돈다. 그건 window 실패로 격리하고 잔여 판정은
            # drain 반납 → EOD QC 가 한다(지정된 판정 장치에 위임).
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

    def _session_ready(self) -> bool:
        """원장에 고정된 session 속성과 내 설정이 맞는가 — window 를 처리할 자격.

        구현체가 좁힌다(기본은 무조건 통과). 거짓이면 ACTIVE 에서는 claim 없이 tick 을
        끝내고(STOPPED) DRAINING 에서는 claim 을 집었다 반납한 뒤 ack 까지 간다 —
        drain 을 막으면 EOD 가 영원히 시작되지 못한다. 어느 쪽이든 **fence 는 쥔 채이고
        루프는 산다**: 설정 불일치는 재시도로 낫지 않지만, 정지를 영구화하면 뒤따르는
        drain 을 관측하지 못하고 fence 를 반납·재획득하면 token thrash 가 된다.
        해소는 설정을 고친 배포이고, 그 SIGTERM 이 lease 를 반납해 교체가 이뤄진다.
        """
        return True

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
        # 설정이 원장과 갈렸으면 window 를 **처리할** 자격이 없다(기대 집합을 내 설정으로
        # 계산하므로). 단 drain 은 막지 않는다 — ack_drain 을 부를 수 있는 건 Worker 뿐이라,
        # 여기서 먼저 멈추면 그 세션은 DRAINING 에 영구 고착되고 EOD 가 시작되지 못한다.
        ready = self._session_ready()
        if phase == "DRAINING":
            # 신규(DUE) claim 은 원장이 금지하지만 **만료된 고아 CLAIMED 회수**는
            # DRAINING 에서도 허용된다(2B-2) — 실패로 남은 window 를 여기서 회수해
            # 처리하지 않으면 ack 가 CLAIMED 잔존으로 영구 거부돼 drain 이 안 끝난다.
            if not ready:
                # 자격 없음이 반환값(DRAINING)에 안 실린다 — bounded 확인 게이트가
                # "자격도 없었는데 성공"으로 판정하지 않게 카운터로 남긴다
                self.drain_blocked = getattr(self, "drain_blocked", 0) + 1
            # ⚠️ **drain 회수는 backlog recovery 와 다른 일인데 노브를 공유하고 있었다**
            # (ALPHA-851 리뷰). `recovery_budget_per_tick = 0` 인 dataset(iNAV)에서 이
            # 루프가 0회 돌면 CLAIMED 가 남고, `ack_drain` 은 CLAIMED 잔존 시 거부하므로
            # 세션이 **DRAINING 에 영구 고착**된다 — EOD 가 그 dataset 에서 영영 시작되지
            # 않고 상주 진입점은 sleep 루프를 무한히 돈다. 위 도크스트링이 요구하는 그
            # 회수다. backlog 를 안 쫓는 dataset 도 **drain 수렴은 선택이 아니다.**
            reclaim_budget = max(1, self.config.recovery_budget_per_tick)
            for _ in range(reclaim_budget):
                claim = self.ledger.claim_due_window(
                    session_id=self.session_id, worker_id=self.config.worker_id,
                    fence_token=self.fence_token, now=now,
                    lease_seconds=self.config.lease_seconds, lane="recovery",
                )
                if claim is None:
                    break
                if not ready or not self._process(claim, now):
                    # 반복 실패 window 를 CLAIMED 로 두면 ack 가 영구 거부된다 —
                    # DUE 로 반납하고 잔여 판정(MISSING 등)은 EOD QC 에 넘긴다.
                    # 자격이 없을 때도 **집었다가 반납**한다: 안 집으면 죽은 Worker 가
                    # 남긴 CLAIMED 가 영원히 남아 ack 가 계속 거부된다
                    if ready:
                        # 반환값은 DRAINING 하나라 처리 실패가 여기 아니면 안 보인다 —
                        # bounded 확인 게이트(price_worker_cli)가 이 카운터를 합산한다
                        self.drain_window_failures = (
                            getattr(self, "drain_window_failures", 0) + 1
                        )
                    self.ledger.release_window_claim(
                        session_id=self.session_id, window_start=claim["window_start"],
                        worker_id=self.config.worker_id, claim_token=claim["claim_token"],
                    )
                else:
                    self.ledger.advance_watermarks(session_id=self.session_id)
            if self.ledger.ack_drain(
                session_id=self.session_id, fence_token=self.fence_token, now=now
            ):
                # ack 성공 = 세션이 방금 DRAINED 가 됐다. 다음 tick 관측에 맡기면
                # 그 사이 heartbeat 주기가 도래했을 때 DRAINED 세션이 heartbeat 를
                # 거부해 STOPPED 로 빠지고, 상주 진입점이 재획득 불가 재시도에 영구히
                # 갇힌다(EOD 정상 종료 불가) — 여기서 바로 알린다.
                return "DRAINED"
            return "DRAINING"
        if phase not in ("ACTIVE",):
            return "DRAINED" if phase == "DRAINED" else "STOPPED"
        if not ready:
            # 계속 돌면 남의 기대 집합을 내 기준으로 VALID 확정한다(조용한 누락).
            # claim 하지 않고 이 tick 을 끝낸다 — fence 상실 처리와 같은 모양이다.
            # ⚠️ **stopping 을 세우지 않는다**: 세우면 그 뒤 EOD 가 drain 을 걸어도 tick 이
            # 최상단에서 STOPPED 로 빠져 ack_drain 에 도달하지 못하고, 그걸 부를 수 있는
            # 주체가 Worker 뿐이라 세션이 DRAINING 에 영구 고착된다. fence 도 쥔 채 둔다 —
            # 반납만 하고 멈추면 다음 tick 이 재획득해 token 을 매번 올리는 thrash 가 되고,
            # 설정을 고친 배포는 어차피 SIGTERM 경로로 lease 를 반납해 교체가 즉시 된다.
            return "STOPPED"

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
                # 죽은 token 은 버린다 — 쥔 채 두면 상주 진입점(price_worker_cli)의
                # 재시도 tick 이 같은 stale token 으로 heartbeat 만 영원히 반복한다.
                # 비운 뒤에는 다음 tick 이 재획득을 시도한다(경쟁자가 살아 있으면
                # 실패해 대기, 죽었으면 lease 만료 후 인계).
                self.fence_token = None
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
    config: WorkerConfig
    fence_token: int | None = None
    stopping: bool = False  # SIGTERM — 새 claim 중단, 다음 tick 에서 STOPPED
    _last_heartbeat: datetime | None = field(default=None, repr=False)

    def _session_ready(self) -> bool:
        return universe_matches(self.ledger, self.session_id, self.config.universe)

    def _process(self, claim: dict, now: datetime) -> bool:
        return self._process_window(claim, now)

    def _expected_units(self, window_start: datetime) -> tuple[str, ...]:
        # 기대 유니버스는 **그 window 의 시각**이 정한다 — 전 종목을 매 window 에
        # 넘기면 15:30 이 마지막인 종목이 시간외 window 마다 missing 으로 잡혀
        # INCOMPLETE 가 영원히 재시도된다(2026-08-02 dev 실증)
        return self.config.universe.units_at(window_start)

    def _artifact_key(self, window_hhmm: str, generation: int) -> str:
        return canonical_price_minute_artifact_key(
            self.config.market, self.config.session_date, window_hhmm, generation
        )

    def _commit(self, claim: dict, *, result, records: tuple, units: dict,
                generation: int, artifact_checksum: str, manifest_key: str,
                manifest_checksum: str) -> None:
        cfg = self.config
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
            trigger_schema_version=cfg.trigger_schema_version,
            destination=cfg.destination, artifact_generation=generation,
            emit_outbox=not cfg.is_backfill,
        )

    def _after_commit(self, claim: dict) -> None:
        # 5분봉 롤업 파생(ALPHA-750) — 1분 커밋(정본)은 이미 끝났다. 롤업 실패를
        # window 실패로 접으면 lease 만료 후 정상 수집이 재시도되므로, 여기서
        # 격리하고 크게 기록만 한다(조용한 skip 금지 — Rule 12).
        cfg = self.config
        try:
            maybe_rollup(
                self.storage, self.ledger, session_id=self.session_id,
                market=cfg.market, session_date=cfg.session_date,
                universe=cfg.universe, window_start=claim["window_start"],
            )
        except Exception:
            logger.exception(
                "5분 롤업 실패 — window %s (1분 레인은 계속, 재유도는 그 버킷의 "
                "다음 커밋 또는 재실행 소관)", claim["window_start"],
            )


@dataclass
class InavWorkerConfig:
    """iNAV Worker 설정. `WorkerConfig` 와 **일부러 다르다** — 가격의 발행 축
    (`trigger_schema_version`·`destination`·`is_backfill`)이 이 dataset 엔 없다.
    없는 축에 더미를 채우면 나중에 그 값이 의미 있는 것처럼 읽힌다.
    """

    worker_id: str
    dataset: str
    source: str
    market: str
    session_date: str  # YYYY-MM-DD — artifact key 축
    universe: Universe
    run_id: str
    lease_seconds: int = 300
    session_lease_seconds: int = 300
    heartbeat_every_seconds: int = 60
    # 🔴 **0 이다 — 이 dataset 은 복구를 하지 않는다**(2026-08-08 결정). iNAV 는 추정
    # NAV 라 분 단위 완전성 요구가 낮다: 놓친 분은 놓친 채로 두고, 결손은 원장이 드러낸다
    # (`overdue_no_evidence`). **이건 미착수가 아니라 채택된 방침이다** — "복구가 빠졌네"
    # 로 읽고 채우지 마라.
    #
    # 그래도 두 사실은 남겨 둔다. 나중에 요구가 바뀌면 여기서부터 다시 판단하면 된다.
    # ⚠️ 흔한 오해: "과거 window 재청구는 벤더가 지금 값을 줘서 틀린 값을 커밋한다".
    # **그건 아니다** — 수집기가 `bsop_hour` 라벨로 행을 고르므로(`select_window_row`),
    # 창(30분) 안이면 그 분의 **올바른 값**이 응답에 실제로 들어 있고 창 밖이면 매칭
    # 실패로 missing 이 된다. 틀린 값이 실릴 경로는 없다.
    # ⚠️ 막는 이유는 정확성이 아니라 **지평**이다: recovery 는 최고령 due 부터 집는데
    # (`claim_due_window` ORDER BY ASC) 창 밖 window 는 못 채우면서 계속 최고령이라
    # 매 tick 같은 것을 집어 앱키 전역 쿼터만 태우고 최신 분을 민다. 켜려면 원장에
    # "창 폭 안의 due 만" 이라는 지평 필터가 먼저 필요하다.
    recovery_budget_per_tick: int = 0


@dataclass
class InavWorker(MinuteWorkerLoop):
    """장중 iNAV Worker (ALPHA-851). 가격과 같은 골격, 다른 훅 넷."""

    session_id: str
    ledger: MinuteLedger
    committer: MinuteCommitter
    storage: Storage
    collector: object
    config: InavWorkerConfig
    fence_token: int | None = None
    stopping: bool = False
    _last_heartbeat: datetime | None = field(default=None, repr=False)

    def _session_ready(self) -> bool:
        return universe_matches(self.ledger, self.session_id, self.config.universe)

    def _process(self, claim: dict, now: datetime) -> bool:
        return self._process_window(claim, now)

    def _expected_units(self, window_start: datetime) -> tuple[str, ...]:
        """NAV 가 존재하는 unit 만 — **구성종목에는 NAV 가 없다**.

        `units_at` 을 그대로 쓰면 410종을 기대해 매 window 가 INCOMPLETE 다(구성종목이
        영원히 missing). 시각 게이트는 그대로 태우고(거래시간 밖 window 를 조용히 빈
        집합으로 만들지 않는다 — `units_at` 이 거기서 raise 한다) 그 결과에서 ETF 계열만
        남긴다. 참조 계열(`sector_etf_ids`)도 ETF 라 NAV 가 있다.
        """
        universe = self.config.universe
        nav_units = set(universe.etf_ids) | set(universe.sector_etf_ids)
        return tuple(u for u in universe.units_at(window_start) if u in nav_units)

    def _artifact_key(self, window_hhmm: str, generation: int) -> str:
        return canonical_etf_inav_minute_artifact_key(
            self.config.market, self.config.session_date, window_hhmm, generation
        )

    def _commit(self, claim: dict, *, result, records: tuple, units: dict,
                generation: int, artifact_checksum: str, manifest_key: str,
                manifest_checksum: str) -> None:
        # job·outbox 없음 — iNAV 는 하위 소비자가 없어 window 확정에서 멈춘다.
        self.committer.commit_inav_window(
            session_id=self.session_id, window_start=claim["window_start"],
            worker_id=self.config.worker_id, fence_token=self.fence_token,
            claim_token=claim["claim_token"], data_status=result.status,
            expected_unit_count=result.expected_count,
            succeeded_unit_count=result.succeeded_count,
            failed_unit_count=result.failed_count, record_count=len(records),
            checksum=artifact_checksum, manifest_uri=manifest_key,
            manifest_checksum=manifest_checksum,
            missing_units=units["missing"] or None,
            stage_timestamps=result.stage_timestamps,
            artifact_generation=generation,
        )


def _require_credentials(pair: tuple[str | None, str | None], env_names: str) -> None:
    """자격증명 결손을 **기동에서** 거부한다.

    공백-only 도 결손이다 — 통과시키면 기동은 되고 모든 벤더 인증이 실패해 window 실패만
    쌓인다(fail-loud 기동 검증이 무력해진다).
    """
    if not all((value or "").strip() for value in pair):
        raise SystemExit(
            f"벤더 자격증명 없음 — {env_names} 를 env 로 주입한다(커밋되는 TOML 금지)"
        )


def make_price_collector(options, *, session_date) -> tuple[object, bool]:
    """설정 `source` + 세션 날짜 → `(collector, is_backfill)`. **미지 소스는 기동 거부**(ALPHA-735).

    조용한 폴백을 두지 않는다: 오타 source 로 토스가 끼워지면 원장 source_group 과 갈린
    세션에 다른 벤더의 봉이 실린다. 세션 identity 는 source 로 유도되므로 여기서 막는
    게 유일하게 싼 지점이다.

    ⚠️ **지난 거래일이면 KIS 는 다른 TR 이다**(ALPHA-846). 당일 TR 에는 날짜 축이 없어
    과거 세션에 물리면 오늘 봉이 오늘 라벨로 돌아와 전 window 가 missing 이 된다 —
    설정 노브가 아니라 벤더 사실이라 날짜에서 유도한다(끌 수 있게 두면 꺼진 채로 도는
    백필이 조용히 빈 하루를 만든다).

    ⚠️ `is_backfill` 을 **같이 돌려주는** 이유(ALPHA-863): 이 판정은 벤더 선택뿐 아니라
    시간외 기동 게이트와 outbox 발행 여부까지 가르는데, 호출부가 collector 를 뜯어
    되물으면(`isinstance(getattr(collector, "client", None), …)`) 배선이 바뀔 때 **아무
    신호 없이** False 로 접힌다. 벽시계는 여기서 **한 번만** 읽는다 — 두 번 읽으면 자정
    경계에서 collector 는 당일 TR 인데 게이트는 소급으로 판정한다.

    벤더 무관이다: 소급 TR 이 없는 토스도 과거일 세션이면 실시간 판정 대상이 아니다.
    """
    from ..sources.http import PoliteClient
    from .states import DATASET_PRICE_MINUTE, SOURCE_GROUPS_BY_DATASET

    is_backfill = session_date < datetime.now(KST).date()
    # ⚠️ **벤더를 여기에 더하는 사람에게**: 그 어댑터의 과거일 경로가 정규장만 준다면
    # `price_worker_cli` 의 시간외 universe 거부 게이트에 그 source 도 넣어라. 안 넣으면
    # 시간외 window 가 구조적으로 안 나오는데 기동은 통과해, 그 window 들이 매 tick
    # 재청구·재실패하며 세션이 영영 안 마른다(그 게이트가 존재하는 이유다).
    if options.source == "kis":
        from ..sources.kis_minute import KisHistoricalMinuteClient, KisMinuteClient
        from .kis_collector import KisPriceCollector

        _require_credentials(
            (options.app_key, options.app_secret),
            "DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY/__APP_SECRET",
        )
        # 간격이 곧 유량 상한이다 — 앱키 전역 한도를 15:40 배치와 나눠 쓴다.
        http = PoliteClient(min_interval=options.min_interval_sec)
        if is_backfill:
            return KisPriceCollector(client=KisHistoricalMinuteClient(
                options.app_key, options.app_secret, http, session_date=session_date,
            )), is_backfill
        return KisPriceCollector(
            client=KisMinuteClient(options.app_key, options.app_secret, http)
        ), is_backfill
    if options.source == "toss":
        from ..sources.toss import TossOpenApiClient
        from .toss_collector import TossPriceCollector

        _require_credentials(
            (options.client_id, options.client_secret),
            "DATA_PIPELINE_MINUTE_PRICE_WORKER__CLIENT_ID/__CLIENT_SECRET",
        )
        return TossPriceCollector(
            client=TossOpenApiClient(
                client_id=options.client_id, client_secret=options.client_secret
            ),
            lookback=options.lookback,
        ), is_backfill
    raise SystemExit(
        f"알 수 없는 source {options.source!r} — 이 벤더의 어댑터가 없다"
        f"(가능: {sorted(SOURCE_GROUPS_BY_DATASET[DATASET_PRICE_MINUTE])})"
    )


def price_worker_cli(settings, *, session_date: str | None, universe: str | None,
                     max_ticks: int | None = None) -> int:
    """상주 Price Worker 진입점 — `python -m data_pipeline.run price-worker` (ECS Service).

    relay_cli 와 같은 계약이다: SIGTERM/SIGINT 는 진행 중 tick 을 끊지 않고 tick 경계에서
    멈추며(fence lease 즉시 반납 — 교체 Worker 무대기 인계), DB 오류는 여기서 잡지 않는다 —
    삼키면 수집이 멈춘 걸 아무도 모른 채 프로세스만 살아 있다. 전파시켜 task 를 죽이면
    ECS 가 재기동하고, 잡힌 claim 은 lease 만료로 회수된다.

    session identity 는 조회가 아니라 **결정적 유도**다(원장의 stable_domain_id 규칙과
    동일) — 설정 source 가 원장의 source_group 과 갈리면 유도된 session_id 의 행이 없어
    기동이 거부된다(시작 시점 오배선 차단. in-flight 대조는 ALPHA-700).

    `--max-ticks` 는 로컬 확인용 상한 — WINDOW_FAILED 가 하나라도 있으면 1 로 끝난다.
    """
    import os
    import signal
    import socket
    import time
    from datetime import timezone

    from ..db import stable_domain_id
    from ..lake.storage import make_storage
    from .models import load_universe_uri
    from .states import DATASET_PRICE_MINUTE

    if settings.db is None:
        raise SystemExit("db 설정 없음 — price-worker 는 1분 원장 필수(DATA_PIPELINE_DB__* 주입)")
    options = settings.minute_price_worker
    if options is None:
        raise SystemExit(
            "minute_price_worker 설정 없음 — 벤더 자격증명 필수"
            "(DATA_PIPELINE_MINUTE_PRICE_WORKER__APP_KEY/__APP_SECRET 주입)"
        )
    if not universe:
        raise SystemExit(
            "--universe 필요 — planner(plan-minute-session)와 **같은 파일**이어야 원장의 "
            "universe 와 일치한다(갈리면 Worker 가 처리를 거부한다 — _session_ready)"
        )
    from .jobs import DESTINATION_JOB_KINDS
    price_queues = sorted(d for d, k in DESTINATION_JOB_KINDS.items() if k == "price")
    if options.destination not in price_queues:
        # 오타 destination 은 커밋까지 통과하고 Relay 가 전건 DEAD 로 격리한다 —
        # event_id 가 결정적이라 설정을 고쳐 재실행해도 그 행은 안 바뀌고 건별
        # redrive 만 남는다. 기동에서 어휘로 거부한다.
        raise SystemExit(
            f"destination {options.destination!r} 는 가격 큐 어휘가 아니다"
            f"(가능: {price_queues})"
        )
    day = session_date or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        # strptime 고정 — date.fromisoformat 은 3.11+ 에서 주 날짜(2026-W01-1)를 다른
        # 연도로 읽는다(session_cli 와 같은 이유)
        parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--session-date 형식 오류(YYYY-MM-DD): {day!r}") from None
    # ⚠️ universe 파일 읽기보다 **먼저** 만든다 — 자격증명 결손은 기동에서 죽어야 하고
    # (`_require_credentials`), 뒤로 미루면 파일 오류가 그 판정을 가린다.
    # 벤더 TR 선택이 세션 날짜에 걸려 있어(ALPHA-846) 날짜를 읽은 뒤여야 한다.
    # ⚠️ "지난 거래일인가"를 여기서 **다시 묻지 않는다** — `datetime.now` 를 두 번 읽으면
    # 자정 경계에서 collector 는 당일 TR 인데 게이트는 소급으로 판정해 정상 재기동이
    # 그 순간에만 거부된다. 무엇을 골랐는지는 collector 를 뜯어 되묻지 않고 같이 받는다
    # (ALPHA-863 — 되물으면 배선 변경이 판정을 조용히 뒤집는다).
    collector, is_backfill = make_price_collector(options, session_date=parsed_day)
    universe_model = load_universe_uri(universe)
    if is_backfill and options.source == "kis" and universe_model.extended_hours_ids:
        # ⚠️ 이 게이트만은 **벤더 축이 남아 있다**(ALPHA-863). 백필 판정 자체는 날짜
        # 하나지만, 거부 사유는 소급 TR 의 사실이라 다른 벤더에 옮겨 붙이면 거짓이
        # 된다 — 토스는 window 끝 시각으로 임의 과거 구간을 받으므로 시간외도 구조적
        # 결손이 아니다. `source` 는 바로 위에서 collector 를 고른 그 값이다.
        #
        # 소급 TR 은 정규장(09:00–15:30)만 페이징한다. 시간외 종목이 있으면 세션은
        # 720 window 로 계획되는데, 그 330개는 **구조적으로** 봉이 안 나온다 —
        # 런타임 거부는 `_process` 의 catch-all 이 window 실패로 접어(소스 전역 실패가
        # 전파되지 않는 레인이다) 매 tick 재청구·재실패로 세션이 영영 안 마른다.
        # 그건 종목 하나가 아니라 이 백필 전체가 불가하다는 뜻이라 기동에서 거부한다.
        raise SystemExit(
            f"소급 백필은 시간외 universe 를 지원하지 않는다 — "
            f"extended_hours_ids {len(universe_model.extended_hours_ids)}종 "
            f"(세션 계획이 08:00–20:00 인데 소급 TR 은 09:00–15:30 만 준다)"
        )
    session_id = stable_domain_id(
        "msn", DATASET_PRICE_MINUTE, options.source, parsed_day.isoformat()
    )
    ledger = MinuteLedger(db=settings.db)
    if ledger.session_snapshot(session_id=session_id) is None:
        # 세션이 없으면 fence 획득이 조용히 실패해 빈 폴링만 돈다 — 기동을 거부해
        # ECS 재기동(backoff)이 planner 이후를 재시도하게 한다(fail loud).
        raise SystemExit(
            f"세션 없음: {DATASET_PRICE_MINUTE}/{options.source}/{parsed_day} — "
            "plan-minute-session 이 먼저 돌아야 한다"
        )
    worker_id = f"pw-{socket.gethostname()}-{os.getpid()}"
    worker = PriceWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=settings.db),
        storage=make_storage(settings.storage),
        collector=collector,
        config=WorkerConfig(
            worker_id=worker_id,
            dataset=DATASET_PRICE_MINUTE,
            source=options.source,
            # 1분 트랙은 KR 전용이다 — 인자로 열면 오타가 다른 prefix 에 쓴다(eod._MARKET
            # 과 같은 이유). 다른 시장이 생기면 원장 컬럼에서 유도한다.
            market="KR",
            session_date=parsed_day.isoformat(),
            universe=universe_model,
            run_id=worker_id,
            trigger_schema_version=options.trigger_schema_version,
            destination=options.destination,
            is_backfill=is_backfill,
            lease_seconds=options.lease_seconds,
            session_lease_seconds=options.session_lease_seconds,
            heartbeat_every_seconds=options.heartbeat_every_seconds,
            recovery_budget_per_tick=options.recovery_budget_per_tick,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        # 진행 중 window 를 끊지 않는다 — 다음 tick 경계에서 fence lease 를 반납하고 정지
        signal.signal(received, lambda *_: worker.request_stop())
    logger.info("price-worker 시작: session=%s worker=%s", session_id, worker_id)
    ticks = 0
    failed = 0
    processed = 0
    blocked = 0  # fence 미획득·설정 불일치로 아무것도 못 한 tick
    while max_ticks is None or ticks < max_ticks:
        state = worker.tick(datetime.now(timezone.utc))
        ticks += 1
        failed += state == "WINDOW_FAILED"
        processed += state in ("PROCESSED", "WINDOW_FAILED")
        if state == "STOPPED":
            if worker.stopping:
                logger.info(
                    "price-worker 종료(SIGTERM) — %d tick, WINDOW_FAILED %d", ticks, failed
                )
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 모드는 확인 게이트라
                # 끝까지 돌지 못한 것이므로 성공으로 보고하지 않는다(relay_cli 동형).
                return 0 if max_ticks is None else 1
            # fence 미획득(경쟁 lease 잔존) 또는 설정 불일치(_session_ready) — 죽지 않고
            # 다음 tick 을 기다린다: crash 잔존 lease 는 session_lease 만료로 풀리고,
            # 불일치 중에도 drain 관측을 유지해야 세션이 DRAINING 에 고착되지 않는다.
            blocked += 1
            time.sleep(options.tick_seconds)
            continue
        if state == "DRAINED":
            # 세션이 끝났다 — EOD 로 넘어간 정상 종료. 재기동해도 할 일이 없다.
            # 단 확인 게이트 판정은 우회하지 않는다 — 그전 tick 의 실패·차단이
            # DRAINED 반환으로 지워지면 실패한 확인 실행이 성공으로 보고된다.
            # DRAINING 중의 처리 실패·자격 없음은 반환값(DRAINING)에 안 실리므로 합산.
            failed += getattr(worker, "drain_window_failures", 0)
            blocked += getattr(worker, "drain_blocked", 0)
            logger.info(
                "price-worker 종료(DRAINED) — %d tick, 처리 %d, WINDOW_FAILED %d",
                ticks, processed, failed,
            )
            return 1 if failed or (blocked and not processed) else 0
        if state in ("IDLE", "DRAINING"):
            time.sleep(options.tick_seconds)
    failed += getattr(worker, "drain_window_failures", 0)
    blocked += getattr(worker, "drain_blocked", 0)
    logger.info(
        "price-worker 종료(max-ticks %d) — 처리 %d, WINDOW_FAILED %d, 차단 %d",
        ticks, processed, failed, blocked,
    )
    # 확인 게이트다 — 실패가 있었거나, **한 window 도 못 본 채 차단만 됐으면**(경쟁
    # fence·universe 불일치) 성공으로 보고하지 않는다. 전부 IDLE(이미 다 처리된 세션)은
    # 정상이다.
    return 1 if failed or (blocked and not processed) else 0


def inav_run_blocked(parsed_day, today, skip_reason: str | None) -> str | None:
    """이 실행을 막아야 하는 사유, 돌려도 되면 None (ALPHA-851 리뷰).

    두 축 다 **같은 결함으로 이어진다**: 벤더 응답 행에는 날짜가 없고(`bsop_hour` 는
    HHMMSS 뿐) 소급 질의 경로도 없다(`_query_params` 가 날짜를 안 싣는다). 그래서 언제
    돌리든 응답은 "지금 기준 최근 30행"이고, 라벨은 **어느 날짜의 window 와도 1:1로
    맞는다** — 틀린 날짜로 돌리면 오늘 값이 그 날짜의 **불변** canonical artifact 로
    굳고, 이 소스는 재수집이 불가라 되돌릴 방법이 없다.

    * **과거·미래 날짜** — 운영자 실수다. 이 벤더에 소급이 아예 없으므로 오늘이 아닌
      날짜로는 옳은 값을 만들 수 없다.
    * **휴장일·개장 전** — KIS 는 빈 응답이 아니라 **직전 거래일 행을 그대로** 준다
      (2026-07-25 토요일 실행이 7/24 데이터 930행을 적재한 실측). raw 스텝은 이미
      `skip_reason` 으로 막는데(`ingest_raw_etf`), canonical 경로는 그 가드를 안 지났다
      — 가드가 소비자 한쪽에만 있던 형태다.
    """
    if parsed_day != today:
        return (
            f"--session-date {parsed_day} 가 오늘({today})이 아니다 — 이 벤더는 소급 질의가 "
            "불가하고 응답 행에 날짜가 없어, 지금 값이 그 날짜의 불변 artifact 로 굳는다"
        )
    return skip_reason


def inav_worker_cli(settings, *, session_date: str | None, universe: str | None,
                    max_ticks: int | None = None, tick_seconds: int = 20) -> int:
    """장중 iNAV Worker 진입점 — `python -m data_pipeline.run inav-worker` (ALPHA-851).

    `price_worker_cli` 와 같은 계약이다: SIGTERM/SIGINT 는 tick 경계에서 멈추고(fence
    lease 즉시 반납), DB 오류는 삼키지 않는다 — 삼키면 수집이 멈춘 걸 아무도 모른 채
    프로세스만 살아 있다.

    세션·유니버스 축도 같다. 다만 **수집 유니버스는 두 곳에서 온다**: 세션 identity 와
    기대 집합은 `--universe`(planner 와 같은 파일)가, KIS 질의 심볼은
    `krx_etf.source.etf_map` 이 준다. 둘이 갈리면 `etf_map` 에 없는 unit 이 매 window
    invalid 로 드러난다(`inav_collect._rows_for` — 조용히 missing 으로 접지 않는다).
    """
    import os
    import signal
    import socket
    import time
    from datetime import timezone

    from ..db import stable_domain_id
    from ..lake.storage import make_storage
    from ..sources.http import PoliteClient
    from ..sources.kis_inav import DEFAULT_INTERVAL_SEC, KisInavSource
    from .inav_collect import KisInavCollector
    from .models import load_universe_uri
    from .states import DATASET_ETF_INAV_MINUTE

    if settings.db is None:
        raise SystemExit("db 설정 없음 — inav-worker 는 1분 원장 필수(DATA_PIPELINE_DB__* 주입)")
    if settings.kis_nav is None:
        raise SystemExit("kis_nav.source 설정 없음 — iNAV 는 일별 NAV 와 같은 KIS 자격증명을 쓴다")
    if settings.krx_etf is None:
        raise SystemExit("krx_etf.source 설정 없음 — iNAV 질의 심볼(etf_map)의 출처다")
    _require_credentials(
        (settings.kis_nav.source.app_key, settings.kis_nav.source.app_secret),
        "DATA_PIPELINE_KIS_NAV__SOURCE__APP_KEY/__APP_SECRET",
    )
    if not universe:
        raise SystemExit(
            "--universe 필요 — planner(plan-minute-session)와 **같은 파일**이어야 원장의 "
            "universe 와 일치한다(갈리면 Worker 가 처리를 거부한다 — _session_ready)"
        )
    day = session_date or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        # strptime 고정 — date.fromisoformat 은 3.11+ 에서 주 날짜를 다른 연도로 읽는다
        parsed_day = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"--session-date 형식 오류(YYYY-MM-DD): {day!r}") from None
    universe_model = load_universe_uri(universe)
    source = KisInavSource(
        settings.kis_nav.source,
        settings.krx_etf.source.etf_map,
        # 간격이 곧 유량 상한이다 — 앱키 전역 한도를 가격 레인·15:40 배치와 나눠 쓴다.
        PoliteClient(min_interval=0.5),
        interval_sec=DEFAULT_INTERVAL_SEC,
    )
    # ⚠️ **수집 전에 막는다.** 틀린 날짜·휴장일에 돌면 지금 값이 그 날짜의 **불변**
    # artifact 로 굳고, 이 소스는 재수집이 불가라 되돌릴 길이 없다(`inav_run_blocked`).
    blocked = inav_run_blocked(parsed_day, datetime.now(KST).date(), source.skip_reason)
    if blocked is not None:
        # 휴장일·개장 전은 **실패가 아니라 skip 이다**(raw 스텝과 같은 규약, ALPHA-557) —
        # 스케줄러가 붙으면 휴장일마다 정상적으로 지나간다. 조용히는 아니다(Rule 12).
        logger.warning("inav-worker 실행 안 함 — %s", blocked)
        return 0
    session_id = stable_domain_id(
        "msn", DATASET_ETF_INAV_MINUTE, "kis", parsed_day.isoformat()
    )
    ledger = MinuteLedger(db=settings.db)
    if ledger.session_snapshot(session_id=session_id) is None:
        raise SystemExit(
            f"세션 없음: {DATASET_ETF_INAV_MINUTE}/kis/{parsed_day} — "
            "plan-minute-session --dataset etf_inav_minute 이 먼저 돌아야 한다"
        )
    worker_id = f"iw-{socket.gethostname()}-{os.getpid()}"
    collector = KisInavCollector(source, clock=lambda: datetime.now(timezone.utc))
    worker = InavWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=settings.db),
        storage=make_storage(settings.storage),
        collector=collector,
        config=InavWorkerConfig(
            worker_id=worker_id,
            dataset=DATASET_ETF_INAV_MINUTE,
            source="kis",
            market="KR",  # 1분 트랙은 KR 전용(price 와 같은 이유 — 오타가 다른 prefix 에 쓴다)
            session_date=parsed_day.isoformat(),
            universe=universe_model,
            run_id=worker_id,
        ),
    )
    for received in (signal.SIGTERM, signal.SIGINT):
        signal.signal(received, lambda *_: worker.request_stop())
    logger.info("inav-worker 시작: session=%s worker=%s", session_id, worker_id)
    ticks = failed = processed = blocked = 0
    while max_ticks is None or ticks < max_ticks:
        state = worker.tick(datetime.now(timezone.utc))
        ticks += 1
        failed += state == "WINDOW_FAILED"
        processed += state in ("PROCESSED", "WINDOW_FAILED")
        if state == "STOPPED":
            if worker.stopping:
                logger.info("inav-worker 종료(SIGTERM) — %d tick, WINDOW_FAILED %d", ticks, failed)
                # 상주 모드의 SIGTERM 은 정상 종료다. bounded 모드는 확인 게이트라
                # 끝까지 돌지 못한 것이므로 성공으로 보고하지 않는다(price 와 동형).
                return 0 if max_ticks is None else 1
            blocked += 1
            time.sleep(tick_seconds)
            continue
        if state == "DRAINED":
            failed += getattr(worker, "drain_window_failures", 0)
            blocked += getattr(worker, "drain_blocked", 0)
            logger.info("inav-worker 종료(DRAINED) — %d tick, 처리 %d, WINDOW_FAILED %d",
                        ticks, processed, failed)
            return 1 if failed or (blocked and not processed) else 0
        if state in ("IDLE", "DRAINING"):
            time.sleep(tick_seconds)
    failed += getattr(worker, "drain_window_failures", 0)
    blocked += getattr(worker, "drain_blocked", 0)
    logger.info("inav-worker 종료(max-ticks %d) — 처리 %d, WINDOW_FAILED %d, 차단 %d",
                ticks, processed, failed, blocked)
    # 확인 게이트다 — 실패가 있었거나, **한 window 도 못 본 채 차단만 됐으면** 성공이 아니다.
    return 1 if failed or (blocked and not processed) else 0
