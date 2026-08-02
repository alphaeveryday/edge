"""1분 파이프라인 EOD 세션 QC (ALPHA-693, 계획 §13 / v0.7 13절).

하루가 끝나면 누군가 **"이 세션은 무엇을 못 했는가"를 확정**해야 한다. Worker 는 drain 에서
claim 을 반납만 하고 떠나므로(PR 4), 처리 못 한 window 는 `DUE` 로 남는다 — 그대로 두면
"아직 안 한 것"과 "끝내 못 한 것"이 원장에서 구분되지 않는다. 그 확정이 여기다.

```text
DRAINED
→ QC_RUNNING CAS
→ DUE 잔존 → MISSING 확정
→ window 결과 집계 + 불변식 검사(CLAIMED 잔존·계획 개수)
→ orphan artifact 나열(지우지 않는다)
→ FINALIZED(+final_checksum) 또는 FAILED
```

⚠️ **이 모듈은 판정만 한다.** 누락을 메우는 복구(토스 retention 안의 재수집·BigKinds
full-day reconciliation)는 벤더 실호출 경로가 필요해 별건이고, 여기서 섞으면 "판정이
실패했는지 복구가 실패했는지"가 한 exit code 로 뭉개진다.

⚠️ **되돌릴 수 없는 것을 만들지 않는다.** QC 는 쓰기가 전부 멱등이고(같은 입력 → 같은
확정), `FINALIZED` 만이 단방향이다. 그래서 `QC_RUNNING`·`FAILED` 에서 **재진입을 연다** —
QC 에는 lease 가 없어서, 중간에 죽은 실행을 막아 두면 그 세션은 누구도 끝낼 수 없다.

⚠️ **orphan 목록은 스냅샷이지 보증이 아니다.** S3 PUT 과 DB commit 은 한 트랜잭션이 아니라
(v0.7 9절), lease 를 잃은 구 Worker 가 QC **뒤에** 늦은 PUT 을 할 수 있다(그 PUT 은 fence
검사보다 먼저 실행되고 DB commit 만 거부된다). 그때 생긴 잔재는 이 판정에 안 실린다 —
"orphan 0건"은 "그 시점에 안 보였다"는 뜻이다.

**QC 는 worker fence 를 잡지 않는다** — 잡을 필요가 없기 때문이다. 세션이 `DRAINED` 에
이르면 Worker 쪽 경로가 이미 phase 로 전부 닫힌다: fence 획득은 `PLANNED|ACTIVE|DRAINING`,
window claim·결과 기록은 `ACTIVE|DRAINING` 에서만 된다(`repository`). 즉 QC 가 보는
window 집합은 더 이상 움직이지 않는다. ⚠️ 이 전제가 깨지는 순간(누가 저 phase 집합을
넓히는 순간) QC 는 **움직이는 원장을 스냅샷으로 착각한다** — 그 목록을 바꾸려면 여기부터 본다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..lake.storage import Storage
from .commit import find_orphan_artifacts
from .models import content_checksum
from .repository import MinuteLedger
from .states import (
    WINDOW_CLAIMED,
    WINDOW_DUE,
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_MISSING,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)

logger = logging.getLogger(__name__)

# orphan 스캔이 성립하는 dataset — `find_orphan_artifacts` 의 prefix 가 이 값에 묶여 있다.
_PRICE_DATASET = "price_minute"
_WINDOW_STEP = timedelta(minutes=1)

# 하루가 정상적으로 끝났다고 말할 수 있는 window 상태 — 나머지는 사람이 봐야 한다.
_COMPLETE_STATUSES = frozenset({WINDOW_VALID, WINDOW_VALID_EMPTY})
# 결과 요약에 항상 실리는 축 — 0 건이어도 키가 사라지지 않게 미리 고정한다(조용한 0 금지).
_REPORTED_STATUSES = (
    WINDOW_VALID, WINDOW_VALID_EMPTY, WINDOW_INCOMPLETE, WINDOW_MISSING,
    WINDOW_INVALID, WINDOW_DUE, WINDOW_CLAIMED,
)


class SessionQcRejected(RuntimeError):
    """QC 자격이 없다 — 이미 FINALIZED 이거나 아직 DRAINED 에 도달하지 않았다."""


@dataclass
class SessionQc:
    """세션 하나의 EOD 판정. `storage`·`source`·`market` 은 orphan 나열에만 쓴다."""

    ledger: MinuteLedger
    storage: Storage = field(repr=False)
    source: str
    market: str

    def run(self, *, session_id: str, now: datetime) -> dict:
        """판정 결과 dict. 불변식 위반이면 phase 를 FAILED 로 두고 `ok=False` 로 돌려준다.

        예외로 올리지 않는 이유: 위반 자체가 **이 QC 의 산출물**이다. 예외로 던지면 호출자가
        "판정을 못 했다"와 "판정 결과가 나쁘다"를 구분할 수 없고, 그 둘의 처방은 다르다.
        """
        session = self.ledger.begin_qc(session_id=session_id, now=now)
        if session is None:
            # 이미 확정된 세션이면 **그 판정을 다시 돌려준다**(재실행 no-op). 거부하면
            # "확정 커밋 직후 출력 전에 죽은 실행"의 재시도가 영원히 실패로 보이고,
            # 첫 실행의 요약을 복원할 경로도 없다. 확정을 다시 열지 않는 것과 이미
            # 확정된 사실을 보고하는 것은 다르다.
            return self._already_finalized(session_id)

        missing_confirmed = self.ledger.confirm_missing_windows(
            session_id=session_id, now=now
        )
        rows = self.ledger.session_window_rows(session_id=session_id)
        counts = {status: 0 for status in _REPORTED_STATUSES}
        for _, status, _, _ in rows:
            # 미지 상태를 조용히 버리면 그 window 가 어느 칸에도 안 세어져 합이 안 맞는다
            if status not in counts:
                raise ValueError(f"window 원장에 미지 data_status 가 있다: {status!r}")
            counts[status] += 1

        orphans, orphan_scanned = self._scan_orphans(session_id, session)

        violations = self._violations(session, rows, counts)
        summary = {
            "session_id": session_id,
            "session_date": session["session_date"].isoformat(),
            "expected_window_count": session["expected_window_count"],
            "window_count": len(rows),
            "missing_confirmed": missing_confirmed,
            "counts": counts,
            "complete_count": sum(counts[s] for s in _COMPLETE_STATUSES),
            "orphan_artifacts": orphans,
            "orphan_scanned": orphan_scanned,
            "violations": violations,
            "final_checksum": _final_checksum(rows),
            "final_generation": max((generation for _, _, generation, _ in rows), default=None),
        }

        if violations:
            # 위반이 있는데 FINALIZED 로 봉인하면, 그 하루는 "확정됨"으로 보이면서 안이
            # 비어 있다. 되돌릴 수 있는 상태(FAILED)로 두고 사람이 보게 한다.
            logger.error("세션 QC 불변식 위반 session=%s: %s", session_id, violations)
            if not self.ledger.fail_session_qc(session_id=session_id, now=now):
                # 내 QC_RUNNING 이 아니다 — 다른 실행이 그새 phase 를 바꿨다. 내 스냅샷은
                # 낡았으므로 FAILED 라고 보고하면 원장과 정반대인 결과를 내놓게 된다.
                raise SessionQcRejected(
                    f"위반 기록 실패(다른 실행이 phase 를 바꿨다): {session_id}"
                )
            return {**summary, "ok": False, "phase": "FAILED"}

        if not self.ledger.finalize_session(
            session_id=session_id, final_checksum=summary["final_checksum"],
            final_generation=summary["final_generation"], now=now,
        ):
            # 내 QC_RUNNING 이 아니다 — 다른 실행이 그새 확정했거나 phase 가 바뀌었다.
            # 그쪽 판정을 덮지 않는다.
            raise SessionQcRejected(f"확정 실패(다른 실행이 phase 를 바꿨다): {session_id}")

        if orphans:
            # ⚠️ **확정을 막지 않는다.** orphan 은 커밋되지 않은 잔재라 원장 판정과 무관하고,
            # 막으면 crash 잔재 하나가 그날 확정을 무기한 세운다 — 지금 이 잔재를 치울 도구가
            # 없어서(격리 이동·삭제 미구현) 고칠 방법 없는 정지가 된다. 대신 키를 전부
            # 결과에 싣고 로그로 올린다. 격리 정책·도구는 ALPHA-694.
            # ⚠️ 남는 위험: 커밋 세대보다 높은 세대 키가 남아 있으면, 나중에 그 세대로
            # 정정할 때 put_immutable 이 ArtifactImmutabilityError 로 막는다.
            logger.warning(
                "세션 %s 에 orphan artifact %d 건이 남아 있다(지우지 않는다 — ALPHA-694): %s",
                session_id, len(orphans), orphans[:5],
            )
        logger.info(
            "세션 QC 확정 session=%s windows=%d complete=%d missing=%d checksum=%s",
            session_id, len(rows), summary["complete_count"],
            counts[WINDOW_MISSING], summary["final_checksum"][:12],
        )
        return {**summary, "ok": True, "phase": "FINALIZED"}

    def _already_finalized(self, session_id: str) -> dict:
        """확정된 세션의 판정을 원장에서 되읽어 그대로 돌려준다(재실행 no-op).

        window 를 다시 세지 않는 이유: 확정 뒤에 원장이 바뀌었다면 그건 이 세션의 판정이
        아니라 **다른 사건**이고, 여기서 다시 계산해 내놓으면 확정된 값과 조용히 갈린다.
        확정 시점의 값이 그 세션의 답이다.
        """
        recorded = self.ledger.session_final_result(session_id=session_id)
        if recorded is None:
            raise SessionQcRejected(f"세션이 없다: {session_id}")
        final_checksum, final_generation = recorded
        if final_checksum is None:
            # FINALIZED 도 아니고 QC 자격도 없다 = 아직 DRAINED 에 못 갔다(ACTIVE·DRAINING).
            raise SessionQcRejected(
                f"QC 자격이 없다: {session_id} — DRAINED/QC_RUNNING/FAILED 가 아니다"
            )
        logger.info("이미 확정된 세션 — 기록된 판정을 그대로 보고한다: %s", session_id)
        return {
            "session_id": session_id, "ok": True, "phase": "FINALIZED",
            "final_checksum": final_checksum, "final_generation": final_generation,
            "reused": True,
        }

    def _scan_orphans(self, session_id: str, session: dict) -> tuple[list[str], bool]:
        """orphan 나열 — (키 목록, 실제로 훑었는가).

        ⚠️ `find_orphan_artifacts` 는 **가격 raw 경로 전용**이다(prefix 에 `dataset=
        price_minute` 가 박혀 있다). 뉴스 세션에 그대로 부르면 같은 날짜의 무관한 가격
        객체를 훑거나 아무것도 못 훑고 **"orphan 0건"으로 확정**된다 — 안 본 것을 없는
        것으로 보고하는 게 여기서 가장 나쁜 실패다. 그래서 훑었는지를 함께 돌려준다.

        source 는 **원장의 `source_group`** 을 쓴다. CLI 인자를 쓰면 오타 하나로 없는
        prefix 를 훑고 빈 목록을 확정한다(그 역시 조용한 거짓 clean 이다).
        """
        if session["dataset"] != _PRICE_DATASET:
            logger.info(
                "orphan 스캔 생략 — dataset=%s 는 가격 raw 경로 규약이 아니다(session=%s)",
                session["dataset"], session_id,
            )
            return [], False
        return find_orphan_artifacts(
            db=self.ledger.db, connect_fn=self.ledger.connect_fn, storage=self.storage,
            session_id=session_id, source=session["source_group"], market=self.market,
            session_date=session["session_date"].isoformat(),
        ), True

    @staticmethod
    def _violations(session: dict, rows: list, counts: dict) -> list[str]:
        """확정을 막아야 하는 것만 — 데이터 결손 자체(MISSING·INCOMPLETE)는 위반이 아니다.

        누락은 **판정 결과**이고 여기서 드러나는 게 정상이다. 위반은 원장이 스스로와 모순되는
        경우, 즉 판정을 신뢰할 수 없는 경우다.
        """
        violations = []
        if counts[WINDOW_CLAIMED]:
            # ack_drain 이 CLAIMED 잔존을 거부하므로 DRAINED 세션엔 있을 수 없다. 있다면
            # drain 을 우회한 경로가 있다는 뜻이라, MISSING 으로 접으면 그 경로가 숨는다.
            violations.append(f"CLAIMED 잔존 {counts[WINDOW_CLAIMED]}건 — drain 이 우회됐다")
        if counts[WINDOW_DUE]:
            # 방금 확정했는데 남아 있다 = phase 가 QC_RUNNING 이 아니었거나 동시 쓰기다.
            violations.append(f"DUE 잔존 {counts[WINDOW_DUE]}건 — MISSING 확정이 적용되지 않았다")
        # ⚠️ `expected_window_count` 와 대조하지 **않는다** — 그 값은 계획 시점에
        # `COUNT(*)` 로 덮어써지므로(`plan_session`) 항상 실제 행 수와 같다. 그걸 게이트로
        # 쓰면 planner 가 390분 중 389분만 만들어도 389==389 로 통과해, 빠진 분은 MISSING
        # 행조차 없이 확정된다. 대신 **행들이 스스로 말하는 것**을 본다: 1분 간격으로
        # 빈틈없이 이어지는가(계획은 연속된 분을 만든다 — plan_session_windows).
        violations.extend(_gap_violations(rows))
        return violations


def qc_session_cli(settings, *, session_id: str | None, source: str, market: str) -> int:
    """`python -m data_pipeline.run qc-minute-session --session-id <id>` (EOD SFN 이 부른다).

    exit code 가 세 갈래인 이유는 처방이 다르기 때문이다 — 0=확정, 1=판정은 됐는데 원장이
    스스로와 모순(사람이 봐야 한다), 2=판정 자체를 못 함(자격 없음·설정 없음). 하나로
    뭉치면 SFN 이 "다시 돌려도 되는 실패"와 "고쳐야 하는 실패"를 구분하지 못한다.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from ..lake import make_storage

    # 설정·인자 결손도 "판정을 못 했다"(2)다 — SystemExit(문자열)로 나가면 프로세스는 1 을
    # 내고, SFN 은 그걸 "판정 결과가 나쁘다"(1)와 구분하지 못한다.
    if settings.db is None:
        logger.error("db 설정 없음 — qc-minute-session 은 세션 원장 필수(DATA_PIPELINE_DB__* 주입)")
        return 2
    if not session_id:
        logger.error("--session-id 가 없다 — QC 는 세션 하나를 지목해서 돈다")
        return 2

    qc = SessionQc(
        ledger=MinuteLedger(db=settings.db),
        storage=make_storage(settings.storage), source=source, market=market,
    )
    try:
        result = qc.run(session_id=session_id, now=datetime.now(timezone.utc))
    except SessionQcRejected as error:
        logger.error("세션 QC 미실행: %s", error)
        return 2
    except Exception:
        # DB·S3 장애, 직렬화 오류 — 전부 "판정 자체를 못 함"이다. 1 로 나가면 원장이
        # 모순이라는 뜻이 돼, 재시도하면 될 실패를 사람이 조사하게 만든다.
        logger.exception("세션 QC 실행 실패: %s", session_id)
        return 2
    # 결과를 stdout 으로 낸다 — SFN·운영자가 판정 내용을 그대로 읽는다(원장에 사유 컬럼이 없다)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def _gap_violations(rows: list) -> list[str]:
    """window 목록이 1분 간격으로 이어지는가 — 계획 결손을 행 자체에서 잡는다.

    빠진 분은 원장에 **행이 없어서** 어떤 상태 집계에도 안 잡힌다("없는 것"과 "안 만든 것"이
    같아 보인다). 간격을 보면 그 구멍이 드러난다.
    """
    violations = []
    for previous, current in zip(rows, rows[1:]):
        gap = current[0] - previous[0]
        if gap != _WINDOW_STEP:
            violations.append(
                f"window 간격이 1분이 아니다: {previous[0].isoformat()} → "
                f"{current[0].isoformat()} ({gap})"
            )
    # 한 세션에 구멍이 여럿이면 앞의 몇 개만 봐도 원인은 같다 — 출력이 세션 하나로 폭발하면
    # 정작 다른 위반이 안 보인다
    return violations[:5]


def _final_checksum(rows: list) -> str:
    """세션 결과의 결정적 요약 — 같은 결과면 같은 값이어야 재실행이 no-op 으로 판정된다.

    집계가 아니라 **window 목록**에서 유도한다: 개수만 해시하면 두 window 의 상태가 서로
    뒤바뀐 세션이 같은 checksum 을 갖는다.

    시각 정규화(UTC `Z`)와 naive 거부는 `canonical_json` 이 이미 한다 — 여기서 다시
    다루면 규약이 두 곳이 되고, 그중 한쪽이 naive 를 로컬 시각으로 읽는 순간 같은 세션이
    배포 환경마다 다른 checksum 을 낸다.
    """
    return content_checksum([
        [window_start, status, generation, checksum]
        for window_start, status, generation, checksum in rows
    ])
